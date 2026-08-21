# AGENTS.md — Shared Agent Memory & Work Tracker

> **This file is the single source of truth for any AI coding agent working on
> this project** — Claude, ChatGPT/Codex, Gemini, or any other model.
> Read it FIRST on every session, work from the "Current State / Next Steps"
> section, and UPDATE it when you finish (see "Protocol" below).

`CLAUDE.md` and `GEMINI.md` are thin pointers to this file so every tool loads it.

---

## 🧱 Tech stack — READ BEFORE YOU ADD A DEPENDENCY

> **The whole stack, in one place.** Verified against the repo on 2026-08-19, not
> from memory. The file map further down says *which file does what*; this says
> *what the thing is built out of*. ⚠ **The headline is what is NOT here** — see
> "Deliberately absent" at the end before you reach for a library.

| Layer | What it is | Where |
|-------|------------|-------|
| Frontend | **React 18.3 + Vite 5**, plain JSX, hand-written CSS | `client/` |
| Preview renderer | **Raw WebGL** (no engine) + **Web Audio API** | `client/src/animatic/gl/`, `client/src/animatic/audio_engine.js` |
| Backend | **Python 3.14 + FastAPI + uvicorn**, Pydantic models | `server/` |
| Job execution | `ThreadPoolExecutor` — two pools (general + video) | `server/worker.py` |
| Export renderer | **Pillow + NumPy**, encoded by **ffmpeg** (`imageio-ffmpeg`) | `animatic.py`, `animatic_effects.py`, `video_assemble.py` |
| Auth | **PyJWT + bcrypt**, users in MongoDB, token in `localStorage` | `server/auth.py`, `server/security.py`, `client/src/api.js` |
| Data | **MongoDB** (default) · Firestore · in-memory JSON fallback | `server/jobs.py`, `server/mongo.py` |
| Files | **Google Cloud Storage** + local disk (`uploads/`, `output/`) | `storage.py` |
| AI | **Google Gemini / Vertex AI** via `google-genai` — images, text, **Veo** video, TTS | `gemini_client.py`, `script_breakdown.py`, `video_client.py`, `tts.py` |
| 3D | **Meshy.ai** (tested) · **Tripo.ai** (unverified) | `meshy.py`, `tripo.py` |
| Tests | Standalone Python check scripts + **Playwright** for the one browser suite | `tests/` |

---

### Frontend — `client/` (React 18.3 + Vite 5)

⚠ **`client/package.json` has exactly TWO runtime dependencies: `react` and
`react-dom`.** Dev deps are `vite` and `@vitejs/plugin-react`, nothing else. That
is a decision, not an oversight — every widget in the editor (timeline, monitor,
inspector, panes, drag/drop, resize handles) is written here by hand.

- **JavaScript, not TypeScript.** `.jsx` / `.js` only; there is no `tsconfig.json`
  and no build-time type check. What a module's shapes are lives in its docstring.
- **State is custom hooks**, not Redux / Zustand / Context-as-store:
  `useAnimaticProject.js` (the document), `useTimelineTransport.js` (the clock),
  `useUndoStack.js` (ONE undo stack for the whole document). Those three are the
  editor's entire state layer — read them before adding a fourth.
- **Routing is nav state in `App.jsx`.** No react-router; a "page" is a branch.
- **Styling is hand-written CSS with custom properties** in `client/src/styles/`
  (`theme.css` owns the tokens for both themes, `base.css` the shared chrome,
  `animatic*.css` the editor). No Tailwind, no CSS-in-JS, no component library.
  ⚠ `theme.css` sets a global `select { width: 100% }` — that one line has caused
  more than one "why is this control full width" bug in the Work Log.
- **The monitor is a hand-rolled WebGL canvas**: `client/src/animatic/gl/` —
  `compositor.js` (context, programs, textures), `lut.js` / `cube.js` (3D LUT
  upload), `shaders/` (the grade). No three.js, no pixi, no regl.
- **Audio is a Web Audio graph** — `audio_engine.js` (playback, EQ, ducking),
  `audio_mix.js` (the levels/fades maths), `useAudioAnalysis.js` (waveforms, beats).
- **Fonts are bundled files** in `client/public/fonts/*.ttf`, served to the browser
  AND opened off disk by the exporter — one copy for both sides, on purpose.
- Dev server on **:5173** (`client/vite.config.js`); API base from `VITE_API_BASE`,
  defaulting to `http://127.0.0.1:8000` in `src/api.js`. The backend sends
  permissive CORS headers, so **there is no dev proxy**.
- ⚠ **`frontend/` at the repo root is EMPTY** — a dead directory from an earlier
  phase. The app is `client/`. Never put anything in `frontend/`.

### Backend — `server/` + the root modules (Python 3.14 + FastAPI)

- **FastAPI** app in `server/main.py`, run with `uvicorn server.main:app --reload`;
  Swagger at `/docs`. Routers split by workflow — `animatics.py`, `videos.py`,
  `plans.py`, `drafts.py`, `auth.py` — with shared route helpers in `common.py` so
  that two routers never import each other.
- **Pydantic** for every request/response shape (`server/schemas.py`).
- **Job-based async, not a queue broker.** HTTP returns a `job_id` immediately and
  a `ThreadPoolExecutor` in `server/worker.py` does the work. ⚠ **Two pools** — a
  general one and a separate `_video_executor` — so a slow Veo render cannot starve
  the board. No Celery, no Redis, no RQ.
- **Auth**: bcrypt hashing + JWT in `server/security.py`, users in MongoDB
  (`server/users.py`), `get_current_user` as an ordinary FastAPI dependency.
- **The job store is pluggable** (`API_JOB_STORE`): ⚠ **`mongo` is the DEFAULT**
  (`server/config.py:35`) and `MongoJobStore` is the system of record.
  `FirestoreJobStore` and `MemoryJobStore` (mirrored to `.local_jobs.json`) remain
  as fallbacks. ⚠ Older prose further down still says "Firestore (default)" —
  **Mongo is correct**; believe this section.
- **Media processing is Pillow + NumPy**, and **ffmpeg ships with the install** via
  `imageio-ffmpeg` (override with `FFMPEG_BINARY`). ⚠ **There is no `ffprobe`** on
  an `imageio-ffmpeg` install — durations must be passed in by the caller, never
  probed (see `video_assemble.py`).
- **Document exports**: `openpyxl` (.xlsx) and `python-docx` (.docx), for Plan & Script.
- **Config is env-driven only** (`server/config.py` + `.env`, documented in
  `.env.example`). `prompts.yaml` is prompt templates, not configuration.

### AI providers — Google only, switchable per capability

Every capability has its **own independent** `vertex` | `gemini` switch, so images
can run on Vertex while video runs on the Gemini API. One client library
throughout: `google-genai`.

| Capability | Env switch | Default model | Module |
|------------|-----------|---------------|--------|
| Images (references, panels) | `IMAGE_PROVIDER` | `gemini-3.1-flash-image` | `gemini_client.py` |
| Text (script → shot list, beat plans) | `TEXT_PROVIDER` | `gemini-2.5-flash` ⚠ rolling alias | `script_breakdown.py`, `plan_agent.py`, `panel_sequence.py` |
| Video (Animatic → Final Video) | `VIDEO_PROVIDER` | `veo-3.1-*` (standard / fast / lite) | `video_client.py` |
| Speech (voiceover) | — | `gemini-2.5-flash-preview-tts` | `tts.py` |
| Transcription (captions) | — | Gemini text model | `captions.py` |

⚠ **Veo is the only thing billed per second of output** — roughly $0.24 (lite/720p)
to $3+ (standard/1080p) per 8s clip, and a 20-shot project is 20 clips.
`video_client.estimate_cost_usd()` runs before anything spends, and
`server/videos.py` is the only router that can spend money.
⚠ **There is no Google Flow API.** Flow is a Labs web app on a separate credit
ledger, and an AI Pro/Ultra subscription grants no API access at all. Read
`video_client.py`'s docstring before "adding Flow support".
⚠ **Vertex needs a real region for Veo** (`us-central1` is the safe default);
`global`, which the image models require, does not serve it.
Retry/backoff for every Google call is one shared policy: `retry_policy.py`.

### The twins rule — why this stack has two of some things

⚠ **The preview renders in JavaScript and the export renders in Python, so several
modules exist TWICE and must stay in step.** This is the single most important
structural fact about the codebase:

| Python (export) | JavaScript (preview) | Pinned by |
|---|---|---|
| `animatic_render.py` | `animatic/scene.js` (+ `transitions.js`) | `tests/render_parity.py` |
| `animatic_effects.py` | `animatic/gl/shaders/` | `tests/effects_parity_check.py` ⚠ tolerance, not exact — WebGL ≠ Pillow |
| `animatic_fonts.py` | `animatic/fonts.js` | `tests/captions_check.py` |
| `export_presets.py` | `animatic/export_presets.js` | `tests/export_perf_check.py` |
| `animatic.py` (the mix) | `animatic/audio_mix.js` | `tests/audio_mix_check.py` |
| `animatic.py` (fade curves) | `animatic/audio_mix.js` | `tests/audio_crossfade_check.py` |
| `captions.py` | `animatic/captions.js` | `tests/captions_check.py` |

**Change one side, change the other in the same turn, and run its parity test.**

### Tests & tooling

- ⚠ **There is no pytest suite.** Tests are **standalone scripts** — `tests/*_check.py`,
  run as `python tests/<name>_check.py`; they print and exit non-zero on failure.
  Add a new script per behaviour rather than growing a runner.
- **Playwright** (`requirements-dev.txt`) drives the only browser test,
  `tests/e2e_animatic.py`. ⚠ Standing user preference: **do not run the browser
  suite unless asked** — see "Browser tests (Playwright)" below.
- ⚠ **SEVERAL tests run a BROWSER on purpose, and none of them is the e2e suite.**
  The three below were the first, and the editor ones that came after them
  (`editor_picture_tracks_check.py`, `editor_lane_move_check.py`,
  `editor_board_import_check.py`, `editor_veo_attach_check.py`,
  `editor_media_bin_check.py`) all borrow the same harness — start Vite, answer
  every call from Playwright's router, mount the real `<AnimaticEditor>`. ⚠ If you
  need a new one, COPY THE NEAREST EXISTING ROUTER rather than writing a third
  harness; `editor_board_import_check.py`'s is the one to copy when what you are
  checking involves a picture the server refuses to serve yet.
  `tests/monitor_effects_check.py` mounts the MONITOR (the maths tests never
  unmount anything, which is how a `dispose()` crash shipped),
  `tests/editor_effects_drop_check.py` mounts the whole EDITOR and performs a
  drag (the maths tests never render a pane, which is how a `TypeError` in
  Properties shipped), and `tests/editor_razor_check.py` drives the TOOLS
  (`splitFrameAt` and `splitClip` were both correct while the razor cut the wrong
  layer from the wrong row — no arithmetic test can see that). All three start
  Vite themselves, answer every API call with Playwright's router, and run on
  **SwiftShader** — so they need no backend, no GPU and no native module. ⚠ **They
  are also where the SHADERS actually execute**, since `headless-gl` will not
  build on this machine.
- ⚠ **A UI TEST THAT HAS NEVER BEEN WATCHED TO FAIL IS NOT A TEST.** Both of the
  editor ones were written against a live bug and run against the un-fixed code
  first; the razor one passed against its own bug on the first attempt, because
  the press it made would have been refused anyway (the geometry note at the top
  of that file). Put the bug back, watch red, take it out again.
- ⚠ **A drag CAN be tested.** Playwright's mouse does not start an HTML5 drag in
  a headless Chromium, which was read for a while as "a drag is the one thing no
  test can drive". Dispatching `dragstart`/`dragenter`/`dragover`/`drop` over one
  shared `DataTransfer` drives it exactly — see `DRAG` in
  `tests/editor_effects_drop_check.py`.
- No linter, formatter, pre-commit hook or CI workflow is configured. Match the
  surrounding file's style by reading it first.

### Deliberately absent — do NOT add these without asking

TypeScript · react-router · Redux/Zustand/MobX · Tailwind/styled-components/MUI ·
three.js/pixi · d3 or any chart library · Celery/Redis/RabbitMQ · SQLAlchemy or any
SQL database · Docker/compose · pytest · ESLint/Prettier · a Node backend · OpenAI /
Anthropic / any non-Google model provider · `ffprobe` · Google Flow.

If a task looks like it needs one of these, say so and ask first — several were
considered and rejected, and the reasons are in the Work Log.

---

## ⚙️ Protocol for agents (read this every time)

1. **On start** — read this whole file. The **Work Log** tells you what's already
   done; **Current State / Next Steps** tells you what to do next. When the user
   says "start next", pick the top unchecked item under **Next Steps**.
2. **While working** — keep changes consistent with the **Conventions** section.
3. **When done (REQUIRED)** — before ending your turn, update this file:
   - Add a dated entry to the **Work Log** describing what you changed (files + why).
   - Tick/adjust items under **Next Steps**; add any new follow-ups you discovered.
   - Update **Current State** if the "where things stand" summary changed.
   - Bump **Last updated** below.
4. **Keep it honest** — only record what was actually done and verified. If a step
   was skipped or a test failed, say so.

**Last updated:** 2026-08-21 — **A RIPPLE GRIP PER CUT ADDED UP TO A GOLD BAR,
AND THE TIMELINE PANE IS BLUE ALL THE WAY THROUGH** (user-reported, with three
screenshots). CSS only — `animatic-tools.css`, `animatic-editor.css`, `theme.css`.
⚠ **THE TRIM GRIP IS 8px SO IT CAN BE HIT, NOT SO IT CAN BE SEEN.** With the
ripple (or rolling) tool armed it was filled solid `--primary` edge to edge, so on
a picture row of butted-up shots every cut carried 16px of gold and the lane read
as bars with clips between them. It is a **2px inset shadow** now (3px on hover),
hard against the outer edge of the SAME 8px box — narrowing `width` would have
made the grip harder to grab, which is the worse bug. ⚠ **THE PANE'S BLUE IS A
TOKEN REMAP, NOT A `background`**: `.an-pane-timeline` re-points `--panel` /
`--panel-2` / `--border` at new `--tl-*` tokens, so the head, ruler, lane heads,
tracks and toolbar all turn together instead of staying grey on top of blue. The
navy is a step DARKER than the swatch that was sent, as asked, which also keeps
the `--clip-*-tint` alphas readable on the track; light theme gets a blue-tinted
paper rather than the navy. ⚠ **AND THE MARK IS ONE MARK NOW** (second report,
same day): the fat grip was `.tl-handle:hover` in `animatic.css`, not the tool
rule, so thinning only the tool-armed state left Selection hovering a gold block.
`--tl-grip-mark` is declared once on `.tl-handle` and widened by the base hover —
the tool-scoped hover rule is deleted and must not come back. Verified: `npm run
build` passes. ⚠ **NOT LOOKED AT IN A REAL BROWSER** — this is a colour and a
stripe width, so it is worth a glance.

**Previously:** 2026-08-21 — **OPENING A PROJECT WAS SLOW, AND THE SPINNER
STILL SAID "ANIMATIC"** (user-reported, with a screenshot). The spinner blocks on
ONE request, so the cost was everything that request and the hundred media
requests behind it paid over and over: **`get_current_user` did a remote Atlas
lookup PER REQUEST** (now cached 30 s on the bearer token — successes only, and
dropped immediately when an account is deleted), `_asset_url` re-fetched a board
per library card, `get_frame_image` parsed every frame to serve one, and
`content_hash` read a whole video off disk even on a cache HIT (now memoised on
the stat triple — the key still means "sha1 of these bytes"). Added
**`GZipJSONOnlyMiddleware`** — ⚠ *not* a bare `GZipMiddleware`, which would gzip
100 MB MP4s — and `Cache-Control` on media, but ⚠ **only where the URL cannot
change meaning**: an immutable upload id, or a picture route that was actually
sent its `?v=` stamp. Client-side, video/audio/overlay blobs no longer download
one at a time (**`runPooled`**, a sliding window at 2/3/5). Then finished the
rename: `Opening your project…` and ~70 more user-visible strings across client
and server. ⚠ **`LEGACY_UNTITLED = ["Untitled animatic"]` IS A MIGRATION
SENTINEL, NOT DISPLAY TEXT** — left alone. Verified: client builds, 35/35
non-browser checks pass (the 2 failures are pre-existing and unrelated), plus
ad-hoc checks for the auth cache, the gzip routing, the hash memo and `runPooled`.
⚠ **NOT DRIVEN IN A REAL BROWSER** — the open-timing numbers are still unmeasured.

**Previously:** 2026-08-21 — **A CAPTION COVERS ITS SHOT, THE WAY THE PICTURE
ALREADY DOES** (user-reported, with a screenshot). The captions move now; what
they did not do is GROW, so a take turning a 4-second hold into 8 seconds of
footage left the subtitle stopping a quarter of the way through the shot.
⚠ **THIS REVERSES A RULE `ripple.js` WROTE DOWN DELIBERATELY** ("a caption of two
words, not a rubber band") — that reasoning holds for a subtitle under a picture
that merely got longer, and not here, where the ask across four reports has been
*the shot and everything over it agree about how long the shot is*.
**`grownSpans(before, after)`** reports the new span of every panel whose HOLD
grew (a panel that only MOVED is not in it), and **`coverGrownShots`** extends a
generated caption's END to the end of the shot it sits in. ⚠ **THE END MOVES,
NEVER THE START** — the words are still spoken when they were spoken, so scaling a
caption into the shot's new span would slide every subtitle off the line it
transcribes; holding the start keeps it on the voice and leaves it up for the rest
of the shot, which is exactly the trade being asked for. ⚠ **NEVER PAST THE NEXT
CAPTION**, or a shot with two lines has the first stretched over the second — two
subtitles at once, the thing `tidy_lines` exists to prevent. ⚠ **GENERATED
CAPTIONS ONLY (`cap…`) AND THE PREDICATE IS HARD-CODED**: text the user typed and
placed is theirs, and a caller passing a different predicate would have this pass
silently resizing their titles. ⚠ **IT ONLY EVER GROWS**, and it runs AFTER the
carry at both sites (`coverGrownShots(rippleClips(list, shifts), grown)`) because a
caption is matched to its shot by where it now STARTS. Covered by
`tests/timeline_ripple_check.py` — 42 checks, node + source, no browser.
⚠ **STILL NOT DRIVEN IN A REAL BROWSER.**

**Previously:** 2026-08-21 — **THE RIPPLE READ THE DOCUMENT OUT OF A REF, AND A
REF IS EMPTY AT LOAD AND STALE IN A POLL** (user-reported, third pass on the same
edit). The layout, the shift map and `rippleClips` were all right — what was wrong
is where the five lists came from. `attachVeoClip` read the captions, shapes,
overlays and audio out of a `docRef` filled by a `useEffect`: **EMPTY** straight
out of the load promise (`onLoadedRef` runs before React has rendered anything) and
**STALE** inside the Veo poll (deliberately keyed on `animating` alone).
⚠ **RIPPLING AN EMPTY LIST IS A SILENT NO-OP THAT LOOKS EXACTLY LIKE "NOTHING
NEEDED TO MOVE"** — no error, and the identity check says "unchanged" perfectly
truthfully, which is why this took three passes to pin down. ⚠ **THE FIX IS TO
STOP READING THE DOCUMENT AT ALL**: every ripple now goes through React's own
functional setters (`setTexts((list) => rippleClips(list, shifts))`), which are
handed the LIVE list at commit time. It works at load precisely BECAUSE it is an
updater — the loader has already queued `setTexts(p.texts)` and the updater is
handed that pending list. ⚠ **`docRef` IS GONE AND MUST NOT COME BACK** —
`tests/timeline_ripple_check.py` asserts `"docRef" not in editor`. `rippleDocument`
is replaced by **`RIPPLED_LISTS`**, the five names in one place, and the test
counts the calls at each of the three sites so a forgotten list fails loudly.
Verified by modelling React's setter semantics in node over the real modules: a 6s
take over a 2s shot grows the panel, moves the next one, moves the caption, and
cuts the voiceover — **caption and audio landing on the same millisecond**.
⚠ **STILL NOT DRIVEN IN A REAL BROWSER.**

**Before that:** 2026-08-21 — **…AND IT ONLY RAN ON THE ATTACH, SO EVERY TAKE
ALREADY ON A TIMELINE STAYED WRONG** (user-reported, with two screenshots). The
rule below was right; **when** it ran was not, in two places. ⚠ **THE LAYOUT ONLY
EVER RAN FROM `attachVeoClip`**, and `reconcileVeoClips` skips a clip already on
the timeline — so a render that landed before the stretch existed kept a 2-second
still under 4 seconds of footage for ever, with no gesture that re-runs it. **The
load now runs it once** (`onLoadedRef`), after the Veo recovery, and ⚠ **COSTS
NOTHING ON A BOARD THAT IS ALREADY RIGHT** — both passes are idempotent and hand
back the SAME arrays, so a correct project is an identity test and no edit, which
is what stops this dirtying every animatic on open. It runs before `resetHistory`
and sets `changed`, so the autosave persists it and the first Ctrl+Z cannot undo
into a half-healed row, and there is a NOTICE, because clips moving by themselves
the instant a project opens is the most alarming thing this editor can do
silently. ⚠ **AND `docRef` WAS EMPTY FOR A CLIP RECOVERED AT LOAD** — it is filled
by an effect, and `onLoadedRef` is called straight out of the load promise before
React has rendered anything, so a paid clip recovered there rippled an EMPTY
document: pictures moved, sound left behind, on every reload. Seeded from `p` in
the same breath as `framesRef`, which was already seeded there for that reason.
Covered by `tests/timeline_ripple_check.py` — 30 checks, node + source, no browser.
⚠ **A NOTE FOR THE NEXT REPORT:** if a symptom matches the code as it was BEFORE a
change, check the bundle under test contains the change before hunting a second
bug — the screenshots here were of a board whose takes landed under the old rule.

**And before that:** 2026-08-21 — **A SHOT GROWS TO ITS TAKE, AND THE WHOLE FILM
MOVES WITH IT** (user-reported, with three screenshots). Two halves of one edit.
⚠ **THE PANEL TAKES THE TAKE'S LENGTH** (`spreadPanelsForRenders`, `scene.js`): a
2-second still under 4 seconds of footage is a shot whose two halves disagree
about how long it is, and one that collapses back to 2s the moment the take is
deleted. It keeps its START and grows to the take's end, only ever GROWS (a
shorter take leaves it alone — shrinking a hold somebody set by hand is
discarding an edit), and is clamped at `MAX_FRAME_MS`. ⚠ **AND EVERYTHING ELSE ON
THE TIMELINE TRAVELS WITH THE PICTURES** — the captions, the voiceover, typed
text, shapes, overlays, the Video row — which is the new **`ripple.js`**. There is
no single number to move things by (shot 7 grows by 2s and shot 24 by 9s), so
**`renderShifts`** turns what the layout pass did into a STEP FUNCTION over OLD
time — two points per panel, its start AND its end, matched by frame id — and
**`rippleDocument`** moves all five lists in one call. ⚠ **A CLIP IS LOOKED UP BY
ITS OWN START AND IS NOT STRETCHED**: a caption inside the grown shot stays under
it, one a millisecond past its old end owes the whole debt. ⚠ **THE VOICEOVER IS
CUT AT THE EDIT, NOT DRAGGED WHOLE** — it is one clip laid from 0:00, so its start
owes nothing (the bug) and shifting it by a later shot's debt would drag the lines
before that shot too (a different bug); `rippleAudio` razors it with the razor's
own `splitClip` and moves only the tail. ⚠ **EVERY CLIP THAT CAME OFF A BOARD IS
SKIPPED BY `rippleFrames`** — panels AND takes: the layout pass already placed
both and the map is in OLD time, so looking a moved take up at its NEW start adds
its debt twice and slides it off its own shot. Run by **`attachVeoClip`** and by
**the voiceover poll** (against `speechFramesRef`, with `keep` for the captions
and voiceover the server just re-timed). Covered by
`tests/timeline_ripple_check.py` — 25 checks, node + source, no browser.
⚠ **NOT DRIVEN IN A REAL BROWSER** — in particular the razored voiceover has not
been listened to across the cut; browser tests are run on request in this project.

**Earlier:** 2026-08-21 — **A SHOT HOLDS ITS OWN LINE, AND THE DIALOGUE IS A
SCRIPT YOU CAN EDIT BEFORE IT IS READ** (user-reported, with four screenshots). A
line was laid at the start of its shot and nothing moved the picture, so a
ten-second line over a two-second hold — and the caption built from it — ran
straight over the four shots after it. ⚠ **THERE WERE TWO CLOCKS**: `tts`
advanced its own by `line + gap` while the picture row was never touched, and two
clocks agree right up until a shot holds LONGER than its line. `synthesise_timed`
is gone; `tts` now speaks, measures and lays blobs where it is told
(`speak_lines` / `assemble`) and the ONE clock lives with the frames in
**`_lay_out_speech`** (`server/animatics.py`). ⚠ **THE ROOM COMES FROM THE ROW
ITSELF**, exactly as it does for a Veo take: the shot that owns the line is
stretched to cover it (line + `GAP_MS`) and the shots after it are pushed clear —
forward only, never past where a clip already is, a Veo take travelling with its
panel by the panel's delta. A second run over its own output moves nothing.
⚠ **THE EDITOR MUST RE-READ THE FRAMES** when the run ends (`setFrames`), or its
next autosave writes the old layout back over the one the server just worked out.
⚠ **AND THE DIALOG SHOWS THE SCRIPT NOW**: free `GET /animatics/{id}/dialogue`
returns every line with its shot, its speaker and a **persona** guessed from the
board's cast sheet (`tts.persona_from` — keyword-only, free, and it declines to
guess a sex the board never gave). ⚠ **THE PERSONA IS THE ONLY THING THAT CARRIES
AN AGE AND A SEX TO THE MODEL** — a voice name is a timbre, so the persona writes
the stage direction the line is read with AND casts the voice; the direction never
reaches the captions. The edited sheet wins entirely and is sent on BOTH calls, so
the estimate prices the words on screen. Covered by
`tests/voiceover_fit_check.py` — 49 checks, no model call, no browser.
⚠ **NOT OPENED IN A REAL BROWSER** — the sheet's layout at forty lines is
unverified by eye; browser tests are run on request in this project.

**Earlier still:** 2026-08-21 — **AN UPLOADED PICTURE GOES TO THE **Images**
LAYER, THE **Stills** ROW IS GONE, AND A BLANK LANE IS NO LONGER AN UPLOAD
BUTTON** (user-reported, with two screenshots of the gutter). A Stills row was
made FOR you the first time you uploaded a photo — and picture rows stack
highest-draws-first, so it landed ABOVE the storyboard rows and one photo blanked
out the opening seconds of the board. ⚠ **`stills` IS OUT OF `ROW_KINDS`**
(`scene.js`), so the three rows left in the cut are the board's two and **Video**,
and every door into "add a picture" — the Media pane's ＋, its drop card, a
library card's ＋, a double-click on one — routes through ONE new rule,
`belongsOnImageLane`, onto the default **Images** lane as an overlay.
⚠ **A PICTURE CAN STILL BE PUT IN THE CUT ON PURPOSE**: the Video row takes
footage and full-frame stills alike (`ROW_TAKES.video`), which is what the ＋ Add
layer menu has always claimed — what went is the row that appeared unasked.
⚠ **AND NOTHING SAVED CHANGES**: `rowKindOrLegacy` reads a stored
`kind: "stills"` record as the plain video row its clips already play on, and
`clipRowKind` answers `"video"` for a plain picture, so old projects keep their
photos, their timing and their export byte-for-byte — only the gutter label
changes. ⚠ **THE SECOND ASK IS AN ABSENCE**: the empty band of a lane used to BE
an add button (a full-width invisible control that opened a file dialog);
`emptyBand` renders nothing now, so the blank part of a row scrubs and marquees
like the rest of it and the only two ways in are the Media pane and the row's ＋
in the gutter — "only keep media and layer ＋ icon". Covered by
`tests/image_lane_routing_check.py` — 26 checks, node + source, no browser.
⚠ **`tests/editor_picture_tracks_check.py` WAS UPDATED AND NOT RUN** — its
"a clip only moves to a row of its own kind" pair asserted the Stills/Video split
that no longer exists and now asserts the one-kind rule; the Playwright suites are
run on request in this project, so that change is unverified.

**Older:** 2026-08-21 — **A TAKE MAKES ROOM FOR ITSELF: ANIMATING A SHOT
PUSHES THE PANELS AFTER IT ALONG** (user-reported, with three screenshots). A Veo
render is as long as Veo was ASKED for — 4s of footage over a 2s hold is the
ordinary case — so the second render, which starts where ITS panel starts, used to
land inside the first one's tail and the two bars overlapped on the Storyboard
video row. ⚠ **THE ROOM COMES FROM THE ROW UNDERNEATH**: the animated panel stays
put and the panels AFTER it are pushed clear of the take's end
(`spreadPanelsForRenders` in `scene.js`, run by `attachVeoClip`) — sliding the
renders instead would only move the collision. ⚠ **FORWARD ONLY, AND NEVER PAST
WHERE A CLIP ALREADY IS** — this is not a re-lay of the row: a gap the user opened
by hand survives, deleting a take leaves the spread it made, and a second pass over
its own output is a no-op (which is also how the editor knows whether to say a
panel moved). ⚠ **A RENDER MOVES BY ITS PANEL'S DELTA, NOT ONTO ITS PANEL'S
START**, so a nudge the user gave it survives. ⚠ **PAIRED BY THE BOARD REFERENCE
`attachVeoClip` COPIES OVER** (`storyboard_id` + `index` + `frame`) — not by
`assetKey`, which keys a render by its upload. Covered by
`tests/veo_ripple_check.py` — 20 checks, node + source, no browser.

**Older still:** 2026-08-21 — **A VEO RENDER CAN BE SAVED TO DISK, FROM TWO
PLACES** (user-specified). A ⬇ on its Media card and **Download** in a
right-click menu beside its bar on the timeline. ⚠ **THE ⬇ IS FIRST IN THE
CARD'S TOOL ROW** (⬇ ＋ ✕) so ＋ and ✕ stay in the same columns on every card —
a layout rule, not a preference. ⚠ **ON A PAID RENDER AND ON
NOTHING ELSE**, which was the ask — every other source is already on the user's
machine or still on the board; a render exists only here and costs money to make
again, so "delete the project" had to stop being the thing that destroys it.
⚠ **`isVeoRender` IS `cardRowKind(…) === "board_video"`, THE SAME DERIVATION
THAT PAINTS THESE BARS PURPLE** — no new field, no migration, and no second
opinion that can disagree with the colour on screen. ⚠ **RIGHT-CLICK ON ANY
OTHER CLIP KEEPS THE BROWSER'S OWN MENU** — the guard returns before
`preventDefault`. ⚠ **AND THE FETCH CARRIES THE BEARER TOKEN** (`<a href>` sends
no headers, so a plain link is a 401). Covered by `tests/veo_download_check.py`
— 20 checks, node + source, no browser.

**Before that:** 2026-08-21 — **THE PROGRAM MONITOR GOES FULL SCREEN**
(user-specified, with a screenshot of a video player's control). A **Full screen**
button sits at the empty right-hand end of the Program pane head, beside the
aspect-ratio menu and the size read-out. ⚠ **IT IS THE PANE BODY THAT GOES FULL
SCREEN** (`.an-program-body`) — picture *and* transport, not the picture alone:
a preview you cannot pause or scrub is a screensaver. ⚠ **THE STATE IS DRIVEN BY
`fullscreenchange`, NEVER BY THE CLICK** — Escape and F11 leave without telling
us, so a flag flipped in the handler would leave the button drawing "exit" over a
window that had already come back. ⚠ **THE ICON IS FOUR CORNER BRACKETS, NOT THE
PLAYER'S DIAGONAL ARROWS** (`fullscreen` / `fullscreen-exit` in `Icon.jsx`) —
asked for explicitly: a common glyph drawn in this app's own set, one object in
two states like `eye` / `eye-off`. ⚠ **AND ALMOST NO SIZING CODE WAS NEEDED** —
`.an-screen-fit` is already a size container and `.an-nle .an-screen` measures it
in `cqw`/`cqh`, so a body that fills the display makes the monitor fill it at the
project's exact aspect ratio. ⚠⚠ **AND THE HOOKS SIT AT THE TOP OF THE
COMPONENT, NOT NEXT TO THE JSX** — `AnimaticEditor` returns early for `loading`
and for a load error, so hooks declared below that ran on the second render and
not the first, which is *"Rendered more hooks than during the previous render"*
and a black page on opening any project. Shipped broken, reported, fixed the same
session. `npx vite build` passes and does **not** catch it.

**Previously:** 2026-08-21 — **✨ ANIMATE OPENS ON THE BOARD'S OWN PROMPT, AND
OFFERS THE SHOT'S DIALOGUE** (user-specified). ⚠ **NO NEW ROUTE** — the redraw
pane's free `GET /animatics/{id}/frames/{frame_id}/panel` already answered "what
does the board say about this clip"; all that was added is `dialogue` on it.
⚠ **THE DRAFT IS THE DESCRIPTION ONLY**, matching `_starting_prompt` in
`server/videos.py`, and it is written **only over the frame label** — a late
response must never overwrite what the user has typed. ⚠ **TICKING "Have Veo speak
these lines" EDITS THE PROMPT BOX AND TURNS SOUND ON** — what goes to Veo is what
is on screen, and Veo cannot speak with `generate_audio` off. ⚠ **AND THE SHOT'S
NAME SITS ABOVE THE BOX** (`.an-animate-shot`) — the box carried it before the
draft replaced it, and it is the only thing naming what is about to be paid for.
Covered by `tests/animate_prompt_draft_check.py`.

**Previously:** 2026-08-21 — **"New Project", "Untitled Project", AND A DEFAULT
"Images" ROW** (user-specified). ⚠ **THE PLACEHOLDER TITLE IS A SENTINEL** — the
editor compares against it to decide whether Save must ask for a name and whether
an untouched project is discarded, and every project made before today carries
the old wording, so ask **`isUntitled(title)`** (`AnimaticLibrary.jsx`), never
`=== UNTITLED`. ⚠ **THE DEFAULT "Images" ROW IS `layerId: ""`, `removable: false`**
— the same shape as the default Text and Shapes rows, which is why every drop,
move and clear path already handled it; `addLayer` had already been numbering
image layers from 2 for a row that did not exist. The library HEADINGS ("Your
Animatics" and friends) are unchanged.

**Previously:** 2026-08-21 — **＋ Add layer LOSES "Stills track", "Video track"
BECOMES "Video", AND THE WORKFLOW IS "Video Editor"** (user-specified). ⚠ **THE
TWO MENU ENTRIES BUILT THE SAME ROW** — a picture row takes footage and stills
alike, so `stills` was a second name for `video`; only the MENU entry went, the
`stills` row kind still exists because an unrouted image import creates one.
⚠ **THE ROUTE ID `storyboard-to-animatics` AND THE "Your Animatics" LIBRARY TITLE
ARE UNCHANGED** — the id is branched on in three places in `App.jsx`, and the
library heading names the projects rather than the workflow. Nothing was run:
label edits only.

**Previously:** 2026-08-20 — **THE NAV RAIL COLLAPSES TO ICONS, LIKE
ChatGPT's** (user-specified, with the ChatGPT panel and its icon rail as the
reference). ⚠ **THE COLLAPSED RAIL IS THE SAME DOM WITH THE LABELS HIDDEN IN
CSS** — that is what keeps every row's `title`, which at 68px is the only place a
workflow can say its name. ⚠ **AND THE STATE LIVES IN `App.jsx`, NOT
`Sidebar.jsx`**: `.shell` is a two-column grid, so the rail and the page must
change width in the same render — App stamps `.shell.nav-collapsed` (68px track,
transitioned) and passes `collapsed` + `onToggleCollapse` down. Persisted in
`localStorage` (`cas_nav_collapsed`), toggled by the brand-row button or
**Ctrl/Cmd+B** (which bails while a field has focus, so it can't fight a text
control's bold). ⚠ **THE RAIL IS 280px NOW, UP FROM 264px** — the brand row grew
a fourth control and "Character Studio" was ellipsising; the width plus a
tightened row was measured in a browser to fit, so anything added to that row
again clips the app's name first. **Rendered headless in all four states**
(dark/light × open/collapsed) — ⚠ but **NOT opened in the real app**: no dev
server and no Playwright run this pass.

**Previously:** **WHITE LABELS ON COLOURED STROKES, SHAPES GET A
VIOLET, AND THE SIX TOOL LETTERS ARE ICONS** (user-specified). Three asks in one
pass. ⚠ **THE COLOUR IS THE STROKE AND THE WORDS ARE `var(--text)`, NEVER `#fff`**
— ＋ Add layer, the four green tools, and every layer head's number and name; a
literal white would vanish on the light theme's white panel. That also retired
`--lane-ink`. ⚠ **SHAPES ARE VIOLET ON BOTH SIDES OF THE ROW, WHICH OVERRULES A
RULE WRITTEN DOWN IN TWO FILES** (a shape clip stayed neutral because its swatch
carries the shape's own colour) — both comments now say what happened, because the
swatch says WHICH SHAPE and the bar says WHICH ROW, and a neutral bar answered the
second question with nothing. ⚠ **VIOLET AND THE VEO PURPLE ARE THE CLOSEST PAIR
ON THIS TIMELINE** (~28° of hue, and they can sit one row apart) — check them
together if either is retuned. ⚠ **AND THE TOOL LETTERS' SHORTCUTS NOW LIVE ONLY
IN THE TOOLTIP**: the six buttons draw icons (`select` / `razor` / `ripple` /
`rolling` / `hand` / `zoom` in `Icon.jsx`, plus `aria-label`, since the SVG is
`aria-hidden`), so the "(V)" in every title is load-bearing. Then, in the other
direction, **the CLIP labels went pastel grey** (`--muted` on `.tl-bar-label` /
`.tl-text-label` / `.tl-shape-label`) — ⚠ **THE TWO COLUMNS ARE READ DIFFERENTLY**:
a layer name is read once to find the row, a clip label is repeated forty times
across a row of shots, and white at that count is what you see instead of the
bars. Rolling was redrawn
after looking at it — its first two-headed arrow merged into a diamond at the 18px
the button actually is. **This CSS pass was looked at**: the icons and a head-bar +
gutter mock built from the compiled `dist` stylesheet were rendered headless in
both themes and read back. ⚠ **NOT THE REAL EDITOR THOUGH** — no Playwright suite
run, so none of it met real project data.

**Previously:** **A LAYER HEAD IS STROKED IN ITS OWN ROW'S COLOUR,
AND THE FOUR MAKE-SOMETHING BUTTONS ARE GREEN** (user-specified). The gutter and
the tracks now say the same thing in the same hue — Video orange, Stills and
Story..Image pink, Story..Video purple, Text yellow, Captions mint, Audio gold —
with the row's NUMBER CHIP filled in that hue, which is what turns a 1px stroke
into a highlight you can find down a column of eight. ⚠ **ONE MAPPING, `laneHue`
IN `Timeline.jsx`, AND IT MUST KEEP AGREEING WITH `clipRowKind`** — a picture
row's hue comes from its STRICT KIND, and a head stroked orange above purple
renders is worse than no stroke. ⚠ **THE STROKES ARE THEIR OWN `--lane-edge-*`
TOKENS, NOT `--clip-*-edge` REUSED**: those pastels are translucent fills and wash
out to grey at 1px, and a head must not read as another clip. Shapes and audio are
stroked without claiming a content hue (audio's own gold; a lifted grey for
shapes, which needs a separate `--lane-ink` because a stroke has to be seen and a
numeral has to be read). The four tools in `.tl-add-tools` are green as a set —
⚠ **`--tool-*` IS DELIBERATELY NOT `--clip-caption-*`** (a control colour beside
a content colour, both on screen at once) and ⚠ **their hover restates everything
with `:not(:disabled)`**, or `.btn:hover:not(:disabled)` in base.css turns them
GOLD, which is the timeline's selection colour. Verified by `npx vite build` plus
a read-back of the emitted CSS; ⚠ **NOT OPENED IN A BROWSER, SO THE CONTRAST
JUDGEMENTS ARE REASONED AND NOT MEASURED** — look at Captions and Audio in dark
and Shapes in both.

**Previously:** **✨ ANIMATE WITH VEO IS IN THE TIMELINE'S ADD ROW,
AND THE GUTTER NUMBERS ITS ROWS** (user-specified). The head row now reads
`＋ Add layer · ✨ Animate with Veo · T Text · ▣ Colour card · 🎙 Voiceover`, in
that order. ⚠ **THE NEW BUTTON WIDENS THE WAY IN, NOT WHAT MAY BE RENDERED** — it
calls the same `openAnimate`, which only opens the priced dialog, and every spend
guard stays on the server (`_animate_targets`). Because a toolbar has no selection
to lean on, it aims at `selectedFrame || currentFrame` — the selected shot, else
the shot under the playhead, which is the rule `＋ Text` already follows. And
`LANE_ICON` is gone: each gutter row opens with its POSITION IN THE STACK, 1 at the
top. ⚠ **THE NUMBER IS THE MAP INDEX, NEVER A STORED FIELD** — that is what makes
a sixth layer say 6 by itself and renumbers the stack after a delete; a stored one
prints "Layer 4" second in a stack of five. `.tl-layer-ico` → `.tl-layer-num`
(`min-width`, `tabular-nums` — row 10 is two digits). Verified by
`npx vite build`; ⚠ **NOT OPENED IN A BROWSER AND NO NEW REGRESSION CHECK** — both
changes are placement, and "the button spends nothing on its own" is already
asserted in `tests/animate_guard_check.py`.

**Previously:** **THE ROW ✕ ASKS BESIDE ITS ROW, TAKES THE CLIPS
WITH IT, AND A VEO RENDER IS PURPLE** (user-reported, three screenshots). ⚠ **THE
CONFIRM'S BUG WAS NOT WHERE IT OPENED — IT WAS WHAT OPENING IT DID.** It hung
BELOW its row inside `.tl-gutter-clip`, which is `overflow: hidden`, and the labels
are held level with the tracks by a TRANSFORM — so the browser scrolled that hidden
box to reveal the autofocused Delete button and every NAME slid up while every
TRACK stood still. It is one popover in `.tl-cols` now (the only box spanning both
columns that clips nothing), placed beside its row with a `top` MEASURED off the
row every render, its Delete focuses with `preventScroll`, and `readView` holds
that box's `scrollTop` at 0. Also: **deleting a picture row now deletes its
clips** — the confirm always said "the row and the 1 clip on it" while the code
dropped them to track 0, and it is the Media library that makes deleting them safe
(the SOURCE outlives the clip) — and **a Veo render is pastel purple**
(`--clip-veo-*`, `.tl-bar.is-veo`, chosen by `clipRowKind`), where it used to be
drawn the same pink as the panel it came from. Regression checks are in
`tests/editor_media_row_routing_check.py` — ⚠ **STILL NOT RUN.**

**Previously:** **A MEDIA CARD GOES BACK ON THE ROW IT CAME FROM,
AND THE GUTTER SAYS WHICH ROW THAT IS** (user-reported). A Veo render dragged out
of Media could not be dropped on the Storyboard video row its clip had just been
deleted from; it landed on plain Video instead. ⚠ **`ROW_TAKES` IS ABOUT FILES AND
IT WAS BEING ASKED ABOUT CARDS** — both board rows take no file *on purpose*
(the import and ✨ Animate fill them), so the one drag with every right to land
there was refused, while Video accepted it because a render is genuinely video.
`cardRowKind(kind, fromBoard)` in `scene.js` is the rule now, `clipRowKind`
delegates to it, and the drag learns "did this come from a board?" from a new
`application/x-anim-board` marker — only the TYPE LIST is readable during
`dragover`. The drag, the drop and ＋ all ask the one function. Also: **the two
storyboard rows are "Story..Image" / "Story..Video"** (`ROW_KIND[*].short`),
`--tl-gutter-w` 11rem → 13rem so both fit uncut, and **a board row is never named
after the board** — the import used to, so the gutter read "TTBB E…" for the row
whose kind matters most. Regression test:
`tests/editor_media_row_routing_check.py` — ⚠ **WRITTEN, NOT YET RUN** (the
Playwright suite runs only when the user asks for it).

**Previously:** **THE MEDIA PANE IS A LIBRARY, NOT A VIEW OF THE
TIMELINE** (user-specified). An ASSET is a source, a clip is a placement of one:
deleting a clip leaves the card, dragging the card out makes a new clip, and the
card goes only when its own ✕ is pressed (direct, no confirm — as asked).
`client/src/animatic/assets.js` + `AnimaticAsset` + `MediaBin.jsx`. ⚠ **`assets`
IS `| None`**: `None` = predates the library (derive one), `[]` = emptied on
purpose — flatten them and the ✕ looks broken. ⚠ **A CARD IS SERVABLE WITH NO SAVE
AND NO CLIP**, via the new content-addressed `/animatics/{id}/panel/{board}/{i}`.
Also: **🔒 ON EVERY ROW** (`settings.locked_lanes` — editing only, never the
export; enforced in `Timeline.jsx` where the gestures are), and **THE ROW ✕ NOW
ASKS FIRST** in a popover anchored to that row, counting what goes with it. ⚠ The
new test caught `api.saveAnimatic` silently dropping `assets` — `frameForSave`'s
whitelist trap for the third time. Regression tests:
`tests/asset_fields_check.py`, `tests/editor_media_bin_check.py`.

**Previously:** **A PAID VEO RENDER HAD NO `url`, SO IT WAS A
SPINNER IN MEDIA AND A BLACK HOLE IN THE MONITOR** (user-reported).
`attachVeoClip` wrote its clip out as a literal instead of using `newVideoClip`
and left `url` off — which kills the thumbnail fetch AND the monitor, whose
fallback while a video blob downloads IS that thumbnail. ⚠ **IF YOU WRITE A CLIP
LITERAL WITH A FILE BEHIND IT, YOU HAVE WRITTEN THIS BUG** — it is the second
time, `newVideoClip`'s own note is about the first. Regression test:
`tests/editor_veo_attach_check.py`, watched fail first, and its monitor assertion
is a COLOUR because "the monitor drew something" passes against the bug (it drew
the panel underneath). See the Work Log.

**Previously:** **AN IMPORTED STORYBOARD ARRIVED WITH NO
PICTURES, BECAUSE `flush()` CANNOT SAVE WHAT YOU HAVE JUST PUT IN STATE**
(user-reported). It reads the document and the dirty flag out of refs that
EFFECTS fill, so `setFrames(...)` then `await flush()` — the shape
`doBoardImport` used — ran a render too early, saw a clean project and wrote
nothing; the panel urls then 404'd for ever against a server that had never heard
of those frames. ⚠ **`flush` TAKES AN OVERRIDE NOW** (`flush({ frames, layers })`)
and `doBoardImport` COMMITS NOTHING UNTIL THE WRITE LANDS, sending the frames,
their urls and their row in ONE PUT. ⚠ **`signatureOf` IS THE ONE SIGNATURE
BUILDER**, shared by the save and the dirty-check. Regression test:
`tests/editor_board_import_check.py`, watched fail first. ⚠ **THE VEO REROUTE IS
STILL UNEXERCISED IN A BROWSER.** See the Work Log.

**Previously:** **THE STORYBOARD HAS ITS OWN TWO ROWS, AND A
PICTURE ROW IS ONE OF FOUR STRICT KINDS** (user-specified). `board_image` /
`board_video` / `stills` / `video`, declared in `scene.js`; which row a clip
belongs on is DERIVED from the clip (`clipRowKind`), never stored. ⚠ **A CLIP ONLY
LANDS ON A ROW OF ITS OWN KIND**, enforced in the drag (`laneMoveTarget`), the drop
(`ROW_TAKES`) and the import (`addAssets`). ⚠ **NEITHER BOARD ROW TAKES FILES** —
one is filled by `POST /animatics/{id}/import-storyboard` (the picker), the other
by ✨ Animate. ⚠ **A VEO RENDER NO LONGER REPLACES ITS PANEL**: it lands on the
Storyboard video row ABOVE it, at the same start, with the still left underneath,
so 👁 on that row shows the board again. ⚠ **A ROW NO RECORD NAMES IS CALLED AFTER
WHAT IS ON IT** (`dominantRowKind`) — that is the whole migration, and it is why a
board-built animatic opens reading "Storyboard images" with nothing moved.
⚠ **THE IMPORT PICKER AND THE VEO REROUTING HAVE NOT BEEN OPENED IN A BROWSER.**
See the Work Log.

**Previously:** **A CLIP'S ROW, POSITION AND LOOK WERE NEVER SAVED, AND AN EMPTY
ROW COULD NOT EXIST** (user-reported). `frameForSave` had fallen five fields behind
`AnimaticFrame` — `track`, `start_ms`, `effects`, `mask`, `blend` — and the same
function builds the dirty-check signature, so moving a clip between rows never even
marked the document dirty. It moved to its own pure module
(`client/src/animatic/frame_save.js`) with `tests/frame_save_fields_check.py`
comparing it against the schema. A picture track became an `AnimaticLayer`
(`kind` + `track`), so an empty row survives and its ✕ removes it.
See the Work Log.

**Previously:** **THE LAYER ROW’S THREE CONTROLS ARE FINALLY
IN THEIR CLUSTER, AND THE LAYER NAME HAS ROOM AGAIN** (user-reported, with a
screenshot of the gutter reading "Capti…" / "Eleve…"). ⚠ **THE 2026-08-19 FIX
FOR THIS WAS WRITTEN IN CSS AND NEVER IN THE MARKUP** — `.tl-layer-acts` and
`.tl-layer-btn` had been in `animatic-editor.css` since `b47c9e9` and
`Timeline.jsx` rendered neither, so the rules were dead, the three buttons were
bare `<button>`s at the browser’s own size, and the name — the one item on a
fixed-width row that shrinks — paid for the difference. The cluster now exists in
the JSX, all three controls are always rendered with `disabled` standing for
"nothing to do", and `--tl-gutter-w` went 9rem → 11rem because **the controls and
the name are ONE budget and the controls are the fixed half**. See the Work Log.

**Previously:** **THE PICTURE IS A STACK OF INDEPENDENT TRACKS
NOW, NOT ONE SEQUENCE DRAWN TWICE** (user-reported, and the request was "do best
for me, i make production level editor"). *"when i do video trim so i see my image
layer conetnt move like snip … i want user move independaly each asstes/conetnt in
layer"* — accurate, and true BY CONSTRUCTION: `frames` was one list laid end to
end, so a clip's place was the SUM of the clips before it and changing any length
moved every clip after it. The two picture rows made that look like a bug because
they were that same sequence FILTERED BY ORIGIN (`lane.only` / `frameOrigin`) —
they looked like two layers and shared one clock. ⚠ **A PICTURE CARRIES `track`
AND `start_ms`** (`AnimaticFrame`): `frameSpans` places each clip on its own track,
a higher track draws OVER a lower one, and **a gap is legal** — it shows whatever
is underneath, or the letterbox colour. ⚠ **`sceneAt` RETURNS `pictures`, A STACK,
BOTTOM TRACK FIRST**; `frame`/`frame_b`/`mix`/`transition` remain as the TOPMOST
entry, DERIVED, because "which clip is at the playhead" is a different question
from "what is on screen" and every existing caller wanted the first one. ⚠ **A
MISSING `start_ms` MEANS "AFTER THE LAST CLIP ON MY TRACK"** — the compatibility
hinge: every animatic written before this lays out exactly as the old running total
did, and the editor fills the nulls in once on load. ⚠ **A TRANSITION IS
TRACK-LOCAL AND NEEDS A REAL BUTT-CUT** — there is no edit point in a gap, so one
across a hole is inert rather than wrong. ⚠ **NEITHER PLANNER MAY SKIP A MOMENT
WITH NO PICTURE** any more (both used to `continue`): skipping one now makes the
encoded video SHORTER than the timeline and pulls the audio out of sync from the
first gap on. `render_frame` composites a stack onto the bar colour, `_draw_track`
is one layer of it, and the still-cache key names every track. On the bar: **a
plain trim (V) moves one clip and leaves a gap**, B ripples what follows on that
track, N rolls the cut — and **the picture rows joined the cross-track drag**, so a
shot moves between tracks like any other clip. **▶⇧ in a picture row's gutter puts
the footage on a track of its own** without re-timing anything, which is the
one-press way back to the old two-row view. New `tests/picture_tracks_check.py`
(27) and `tests/editor_picture_tracks_check.py` (22, Chromium); `render_parity.py`
gained a whole multi-track fixture (16 more checks — gaps, overlaps, a transition
per track). **All 14 non-browser suites and all 5 browser suites pass.**

⚠ **AND ONE FILE WAS LOST AND REBUILT DURING THIS WORK. READ THIS BEFORE YOU PATCH
BY SEARCH TEXT.** A patch script anchored on `if (lane.kind === "frames") {` —
which occurs THREE times in Timeline.jsx (`laneTakes`, `selectLane`, `renderLane`)
— matched the first one and deleted ~900 lines. The repair attempt was
`git checkout HEAD -- client/src/components/Timeline.jsx`, and the working tree
held ~635 lines of UNCOMMITTED work beyond HEAD, none of it staged: git had
nothing to give back (no dangling blobs, index blob == HEAD blob), and OneDrive
version history did not have the file either. It was rebuilt from HEAD plus the
minified bundle in `client/dist/` (built mid-session, so it carried the complete
logic), with `editor_razor_check.py` / `editor_effects_drop_check.py` /
`editor_lane_move_check.py` as the executable spec — all three pass, and the
bundle came back within 1KB of its pre-loss size. **Two rules out of it: anchor a
text patch on something that occurs ONCE and assert the count, and `git add -A`
before touching a file you cannot re-derive.**

**Previously:** **A CLIP'S ROW WAS DECIDED ONCE AND FOR ALL, AND
THE EMPTY-ROW PROMPT FELL OFF THE BOTTOM OF ITS ROW** (both user-reported). A move
drag on the timeline was purely HORIZONTAL, so the only way to change which layer
a clip sat on was to drag it out of the Media pane again — which existed for
shapes and for audio and for nothing else, and which for audio was refused
outright whenever the destination was one of the rows grouped by FILE ("mai big
thing i not move some audio part in other audio layer"). ⚠ **A MOVE HAS A VERTICAL
HALF NOW**: let go over another row of the same kind and the clip goes there, at
the time you dragged it to — captions, text, shapes, overlay pictures and audio,
with the original dimmed and an outline drawn where it will land. ⚠ **THE ROW
UNDER THE POINTER IS FOUND BY ASKING THE DOM** (`data-lane`, `laneAtPoint`), for
the same reason the marquee does it: the browser has already laid the rows out and
a second copy of their vertical geometry would be wrong for the whole of a
vertical zoom. ⚠ **THE TIMELINE REPORTS THE ROW, NOT A LAYER ID** (`onMoveToLane`)
— because an audio row grouped by upload has no id to write, and turning that into
a real destination means **PROMOTING THAT ROW TO A LAYER**, taking its own clips
with it, which is the document's business and not the timeline's. That promotion
is one undo: `setLayers` + `setAudioTracks` in one handler. ⚠ **THE PICTURE ROWS
ARE DELIBERATELY NOT A DESTINATION** — `frames` is ONE sequence drawn as two rows
filtered by origin, so which row a picture is on is READ OFF the clip; see Next
Steps for the user's related report that trimming footage shifts the stills, which
is that same fact and is NOT fixed here. Separately, `.tl-track-empty` was padded
down from the top of a row whose height the vertical zoom writes, so at the short
end the prompt landed on the row's bottom edge and `overflow: hidden` sliced it
("i see it gos in down") — it is centred by `line-height` now, which cannot clip
at any height, and `line-height` rather than flex because flex would make the text
an anonymous item and stop `text-overflow: ellipsis` applying to it. New
`tests/editor_lane_move_check.py` (19 checks, Chromium) drives every drag with the
mouse and measures the prompt at both ends of the zoom; **both bugs were put back
and watched to fail on it**, and `editor_razor_check.py` plus the five audio /
selection / hidden-lane checks still pass. Not otherwise opened in a browser.

**Previously:** **THE FIRST PICTURE HAD NO HEAD GRIP, AND THAT WAS
WRONG RATHER THAN PRINCIPLED** (user-reported, follow-up to the entry below). The
head grip on every other picture drags the CUT in front of it, and the first
picture has none — so it was skipped. But an edit does exist there: **start later
INTO the clip**, the ripple trim-in every NLE does. ⚠ **AND ON A VIDEO CLIP THAT
MEANS MOVING `in_ms`, NOT JUST SHORTENING IT** — `sourceAt` reads
`in_ms + t * speed`, so skipping `head` ms of TIMELINE must skip `head * speed` of
FILE or the picture at 0:00 never changes and all you did was throw the end of the
shot away. `out_ms` is absolute in the source and stays. The travel is bounded up
front in timeline ms (the clip's own floor, the last moment of source there is to
show, and however much footage sits before `in_ms` — nothing, for a still), so the
edge stops at the tighter wall instead of quietly passing it. ⚠ **KEYFRAMES ARE
RE-TIMED HERE TOO**: `trimKeyframesHead` came out of `trimTimedClipStart` and is
now shared, because a Ken Burns push is stored relative to the frame's own start
exactly as a caption's opacity is. The rule both halves of the grip obey is **"it
edits whatever is at this clip's head"** — a cut where there is one, the start of
the film where there isn't. New `onFrameChange` prop, since `onResize` only ever
carried a length. Verified against `sourceAt` in both directions at speed 1 and 2,
with the walls exercised; **not opened in a browser**.

**Previously:** **EVERY CLIP HAS A GRIP AT BOTH ENDS NOW; SOUND
WAS THE ONLY THING YOU COULD TRIM FROM THE HEAD.** ⚠ **A HEAD TRIM IS A THIRD
MODE, NOT A RESIZE** (`"trim-start"` in `startClipDrag`): the START moves and
**THE END STAYS PUT**, so it writes both numbers — and on a caption / shape /
overlay it must also re-time the clip's KEYFRAMES, which are stored relative to
the clip's own start. `trimTimedClipStart` in `animatic/razor.js` is new and does
that by reusing `splitKeyframes` — a trim-in is the TAIL HALF of a split at the
new head, planting a key there with the value and ease that were running, or the
animation silently slides by however far you trimmed. ⚠ **ON A PICTURE THE HEAD
GRIP IS THE CUT BEFORE IT**, i.e. `startResize` on the previous frame: a frame has
no start of its own, so shortening the clip itself would move its FAR edge (which
is what the tail grip already does) — moving the cut is the only edit that puts
the edge you grabbed under the pointer, and it inherits ripple / rolling (B / N)
for free. No grip on the first picture, and none on any clip under 24px, or two
8px strips would leave nothing in the middle to press. `.tl-handle-l` moved out of
`.tl-audio-clip` scope, which is what had made sound the exception. Also removed:
the **"audio 2:40 — video ends early"** badge in the timeline head, at the user's
request — the ruler and the transport clock already show it. Verified by unit-
testing the keyframe re-timing in both directions (no drift at any absolute time,
clamps at 0:00 and at the 100ms floor) and by bundling; **not opened in a
browser**.

**Previously:** **SCRUBBING THE RULER SELECTED THE WHOLE TIMELINE,
AND THE RULER NOW READS IN TIMECODE.** `startSeek` was the one press handler on
this bar that never called `preventDefault`, so a drag on the ruler or the
playhead grip started a native TEXT SELECTION and left the track names, clip
labels and empty-lane prompts highlighted blue behind the playhead
(user-reported). ⚠ **`.tl-wrap` IS NOW `user-select: none` WHOLESALE** — every
drag on the timeline means something and there are no inputs on it, so the
belt-and-braces is the right shape here; `cursor: text` on `.tl-ruler` became
`ew-resize`, because the bar was *announcing* itself as a selection surface. The
ruler's labels went from `0:05` to **HH:MM:SS:FF**, with a taller labelled tick
and bare minor ticks between: ⚠ **the sub-second steps are the DIVISORS of fps**,
or a run of labels stops rolling over to `:00` at the next second, and ⚠ **the
ticks are CULLED to the visible window** (1,681 → 49 nodes at full zoom on a 70s
cut, and the sticky ruler re-renders on every scrub). `--tl-ruler-h` is 1.5rem and
`.tl-ruler` reads it instead of repeating 1.15rem — the gutter spacer is sized
from the same variable. **Verified by bundling both files with esbuild and by
checking the step ladder at 2/10/40/120/300/600 px-per-sec × 12/24/25/30 fps; not
opened in a browser.**

**Previously:** **THE RAZOR CUT WHATEVER IT LIKED.** `toolPress`
answered the razor for the time ruler and for the empty part of every lane, and
what it called was the PICTURE razor — so a press in the seconds row cut an image
clip (user-reported), and there was no way to say "cut this layer" because the
callback got a time, not a target. ⚠ **ONE `onRazor(kind, id, ms)` now replaces
`onSplitAt` and `onSplitAudioAt`**: two callbacks was the shape of the bug. Every
lane names its own clip at the press, the ruler scrubs instead of cutting, and
captions / shapes / overlays gained a razor at all (`animatic/razor.js` — ⚠ it
plants a keyframe at the blade, or the animation jumps at the edit while the
document still validates). The cut cursor is on the clips and nowhere else, with
the grips inside them taken out of the pointer's way, which is what makes it ONE
icon instead of a different one per row. Also: the Effects library's descriptions
moved behind an ⓘ per entry — the exported `InfoDot`, not a second circle.
**Both regressions were put back and the new `tests/editor_razor_check.py` (21
checks, Chromium) was watched to fail on each**; `tests/razor_check.py` (15) pins
the keyframe surgery.

**Previously:** **STEP 3: THE TREATMENT ROW IS GROUPED BY FAMILY,
AND SIX POINT-WISE GRADES LANDED.** Twelve chips became five families (Fade ·
Wipe · Shape · Slide · Dip) from a `family` field on the `TRANSITIONS`
descriptor — ⚠ **PRESENTATION, SO NOT TWINNED IN PYTHON**, and ⚠ **deliberately
a DIFFERENT grouping from `fx_library.js`**, which answers "what can I add"
rather than "what is this cut doing". Effects gained Exposure, Gamma,
Temperature & tint, Hue rotate, Sepia and Posterize. ⚠ **`EFFECT_PARAMS` IS
APPEND-ONLY** — an effect reaches the shader as its INDEX, so inserting one
re-numbers every kind after it. ⚠ **`uFxArgs` IS NOW PACKED POSITIONALLY off the
descriptor**, which is why six effects needed no change in `compositor.js`. ⚠
**HUE GOES THROUGH YIQ, NOT THE 709 SVG MATRIX**, so a rotation cannot change
luma. ⚠ **POSTERIZE USES `floor(x+0.5)`, NEVER `round()`** — numpy and GLSL round
halves in opposite directions. Blur/sharpen/grain stay out until the
source-resolution question is settled. ~~No shader in this or the previous step
has ever executed, and nothing has been opened in a browser~~ — **both were
closed on 2026-08-19, see the entry above: the shaders run under Chromium on
SwiftShader and the editor has been driven in a browser.**

**Previously:** **EIGHT NEW TRANSITIONS, AND A REVEAL IS A MASK
RATHER THAN A COMPOSITING STAGE.** Diagonal, Split, Iris, Diamond, Box, Clock,
Blinds, Checker, plus a soft edge on all of them and on the wipe. ⚠ **NO NEW
SHADER PROGRAM AND NO EXTRA FRAMEBUFFERS** — a wipe at 50% and a mask are the
same operation ("show this picture where ‹condition›"), so a transition matte is
a SECOND MASK on the arriving picture, multiplied into its alpha one line
further out than the clip's own mask. That is what keeps composite-over, blend
modes, chroma keys and per-clip masks all working through a transition; a
gl-transitions `mix(from, to)` stage would have thrown all four away. ⚠
**`_setMatte` RUNS ON EVERY LAYER** — uniforms live on the program, so setting
it only when a matte is passed cuts holes in the shapes and overlays drawn after
it. ⚠ **A DISSOLVE IS THE CONSTANT MATTE AND IS DELIBERATELY NOT ONE**:
`apply_matte` rounds, `_faded_layer` truncates. ⚠ **THE WIPE'S EDGE MOVED BY UP
TO ONE COLUMN**, from an integer crop box to a pixel-centre threshold — which is
what makes the two renderers agree by construction. Parity, transition and
effects checks pass; **the shader has never run on a GPU here and nothing has
been opened in a browser** — top of Next Steps.

**Previously:** **EFFECTS ARE A LIBRARY YOU DRAG FROM, IN THE
MEDIA PANE.** A third tab beside Media and Shapes, as a folder tree
(`▸ Video Effects` / `▸ Video Transitions`), dragged onto the timeline.
⚠ **THE MEDIA PANE IS THE SHELF AND PROPERTIES IS STILL THE WORKBENCH** — "what
can I add" and "what is on this clip" are two questions and they get two panes;
the `+ Add an effect…` dropdown stays, because it is the only path without a
mouse. ⚠ **`fx_library.js` FILES KINDS, IT DOES NOT DEFINE THEM**: every entry is
looked up in `EFFECT_PARAMS` / `TRANSITIONS`, and a kind nobody filed lands in
"Uncategorised" rather than being unreachable. ⚠ **ONE DRAG MARKER SERVES BOTH
PAYLOADS**, because `getData` is blank during `dragover` — so the picture rows
accept both and `dropAsset` is where a transition on an overlay row is refused.
⚠ **AN EFFECT LANDS ON A CLIP, SO THE BAR LIGHTS UP** rather than the drop line,
which would not say which of two pictures was about to be graded. ⚠ **A GRADED
CLIP USED TO LOOK IDENTICAL TO AN UNGRADED ONE** — clips carry a ƒx badge now,
and clicking it selects the clip AND opens the Effects section. **Nothing here
has been dragged by hand** — top of Next Steps.

Before that: **A TRANSITION TAKES PARAMETERS: WHICH WAY IT
TRAVELS, WHICH COLOUR IT DIPS THROUGH.** Ten combinations where there were four,
and no new architecture. ⚠ **A REVEAL IS A REGION CUT OUT OF THE ARRIVING
PICTURE, NOT A COMPOSITING STAGE.** A gl-transitions-style
`mix(getFromColor, getToColor)` pass would have needed a new GL program, extra
framebuffers, and would have thrown away the rule `_transition_canvas` documents
— the incoming picture is composited OVER the outgoing one, so a keyed clip
reveals the shot it is arriving over rather than black. A wipe at 50% is "show
the incoming picture where uv.x < 0.5", which both sides already do, so the
direction is a reveal rect and a slide is still pure geometry. ⚠ **`direction`
means the direction of TRAVEL on both kinds, but the DEFAULTS differ** — `right`
for a wipe, `left` for a slide — because those are the behaviours that already
shipped; reproducing them exactly is what let this land without changing a single
existing animatic. ⚠ **A DIP IS A VEIL NOW, NOT A FADE OF THE PICTURE'S OPACITY**,
because only a veil also covers the LETTERBOX BARS — without it a dip to any
colour but the bar colour flashes the bars at both edges of the window, the two
moments a transition has to be invisible at. ⚠ **THREE PLACES HAD TO CHANGE IN
BOTH LANGUAGES OR THE MP4 SILENTLY DIVERGES:** `scene_signature` (two wipes at
one `mix` differ only in the parameter, so without it a re-export reuses the old
stills), the segment in `plan_animated_segments`, and the worker's task args in
`build_animatic`. Proved by sabotaging the last one and watching five checks
fail. **Not opened in the real editor by hand** — see Next Steps.

Before that: **PICKING A COLOUR LOOK BLACKED THE MONITOR OUT.**
User-reported, and it was not the grading maths — `Compositor.dispose()` handed
`deleteTexture` a `{ texture, size }` LUT ENTRY instead of the texture inside it.
That THROWS, out of a React effect's CLEANUP, so React unmounted `<ProgramCanvas>`
and the editor showed a black rectangle. ⚠ **It could only fire once a LUT had
been uploaded**, which is why the symptom was "the screen goes black when I choose
Identity" and every other effect looked fine. ⚠ **The reason `dispose()` ran at all
is the second bug: the context effect listed `onUnavailable` in its dependencies**
and the editor passes an inline arrow, so the whole WebGL context was torn down and
rebuilt — two programs recompiled, every texture dropped — ON EVERY RENDER. The
callback is held in a ref now and the effect's deps are `[]`. **`tests/monitor_effects_check.py`
is new and is the only test that MOUNTS the monitor** — the maths tests never
unmount anything, so both of them passed throughout. Verified by reverting each fix
and watching it fail.

Before that: **TRACK HEADS LINE UP, THE ADD BUTTONS ARE IN
ONE PLACE, AND ASSETS DRAG FROM MEDIA ONTO A TIMELINE ROW.** ⚠ **EVERY LAYER ROW CARRIES THE SAME THREE
CONTROLS — hide · add · remove — in one `.tl-layer-acts` grid of three fixed
columns**, and `Timeline.jsx` ALWAYS RENDERS ALL THREE: a control with nothing to
do is drawn disabled (`opacity: 0.25`), because leaving one out is what let the
next one slide into its place and made the icons zig-zag down the gutter. ⚠
**`.tl-add-layer` now takes its box from the rows it makes** — same radius,
border and type as `.tl-gutter-row`, `height: var(--tl-track-h)`, highlighted
with the clips' own `--tl-clip-bg` gold. ⚠ **All four pane heads share ONE soft blue**
(`--pane-ink/tint/edge` in theme.css, both themes) on the head fill, its
hairline and the pane border only. **A pastel per pane — blue / lilac / mint /
apricot — was built first and rejected on sight: four hues plus gold is five
accents on one screen, so don't re-derive it.** The dot is on `.an-pane-head`,
not `.an-pane-title`, because the Media pane has no title element. ⚠ **The
monitor's transport is smaller and has lost its "Frame 7 of 34" readout** —
sized as `.an-transport .an-tbtn`, NOT on `.an-tbtn`, which is also the timeline
header's zoom pair. ⚠ **Text / Colour card / Voiceover moved to the timeline's
own head row beside ＋ Add layer** (`.tl-headbar`, with `.tl-head` and ＋ Add
layer itself UNMOVED) — handed in as `<Timeline addTools>`, still the editor's
buttons. ⚠ **ASSETS NOW DRAG FROM THE MEDIA PANE ONTO A LANE** and land at the
snapped time under the pointer: `Timeline` decides where (`dropProps`,
`laneTakes`), the editor decides what it means (`dropAsset`). **The dragged
kind is read from `dataTransfer.types` via an empty marker type, because
`getData` is blank during `dragover` in every browser** — that is why there are
two entries on the clipboard. On the picture rows a drop time is the nearest
CUT (the sequence has no gaps); on audio it is `start_ms`. Built clean; not
driven in a browser.

Before that: **THE MEDIA PANE HAS STICKY HEADINGS, AND ⓘ MOVED
ONTO THE ROW.** Follow-up to the chrome pass below, same three complaints again.
⚠ **A SECTION HEADING NOW PINS UNDER THE ＋ CARD** (Storyboard Frames / Video /
Images / Audio) so the pane always says which list you are scrolling — which
needed `overflow: hidden` DROPPED from `.an-grp` **in the media pane only**, since
an `overflow: hidden` ancestor is a scrollport of its own and a sticky child of
one does nothing; the heading takes over the section's top corners. ⚠ **The ＋
card is a FIXED `--an-drop-h` (7rem)** because the card's sticky `top` and every
heading's sticky `top` must be the same number or frames scroll through the band
between them. ⚠ **The sliver above the card was `.an-pane-body`'s own 0.6rem of
top padding** — the pane that has the card now sets `padding-top: 0`, so the card
is flush with the pane head and the padding-box/content-box question never
arises; the belt-and-braces cover over that strip is a **box-shadow, not a
`::before`**, because the card's `overflow: hidden` clipped the pseudo-element
away and the cover never painted. ⚠ **ⓘ IS NOW A PROP, NOT A BLOCK**:
`info` on `PropRow` / `PropSlider` / `PropGroup` puts it in the row's right-hand
cluster (⏱ , ⓘ , ↺) instead of on a line of its own; `PropNote` is warnings ONLY
now. It opens on hovering the ICON (`:has()`), never the row, and a click pins it.
**Built clean; not driven in a browser.**

Before that: **THE EDITOR'S CHROME GAVE ITS HEIGHT BACK TO THE
PICTURE.** Six user-reported layout faults, all the same complaint: furniture was
taking room the monitor wanted. A **back button is an arrow now** (`.btn.back-btn`
in `base.css`) with the destination in its tooltip — in the editor's top bar and
in every workflow that had a "← Your Storyboards"-sized slab. The **project title
is drawn as a field** (it was transparent until hovered, so it merged into the
page). The **status strip moved to the FOOT of the editor** and got shorter —
⚠ it is LAST IN THE DOM now, which is what puts it at the bottom of the Long
workspace's flex column, and the Reel workspace's `grid-template-areas` moved
`stat` to its last row to match. The **＋ Add assets card is `position: sticky`**
at the head of the Media pane, because a 31-frame board scrolled the pane's only
drop target off screen. ⚠ **The Program head's `<select>` was full width because
`theme.css` sets `select { width: 100% }` for the app's forms** — `width: auto`
on `.an-ar-select` is what puts the title, the shape and "1920×1080 · 24 fps" on
ONE line instead of three. And **`PropNote` is an ⓘ** — hover or click to open
— ⚠ for the "" tone only: a `tone="warn"` note is conditional and stays in plain
sight. **Built clean; not driven in a browser.**

Before that: **THE PICTURE TRACK IS TWO ROWS, AND EVERY ROW HAS
AN EYE AND AN ✕.** Video dropped into a board now lands on its own timeline row
and in its own Media-pane section (Storyboard Frames / Video / Images). ⚠ **The
split is by ORIGIN (`frameOrigin`), never by kind** — animating a board shot makes
it a video clip, and it must not leave the board's row; `attachVeoClip` therefore
preserves `src.storyboard_id`. ⚠ **It is still ONE sequence**: the rows filter what
they draw and the clock runs over every clip. The eye (`hidden_lanes`, a project
SETTING) reaches the encoder, and **a hidden picture row is BLANKED, never
dropped** — dropping would move every later cut. ✕ on a default row empties it and
keeps the row. Also fixed: a just-uploaded video sat on its thumbnail spinner —
`newVideoClip` set no `url`; `/media/{upload}?poster=1` is the still. Pinned by
`tests/hidden_lane_check.py`. **Not driven in a browser.**

Before that: **THE MEDIA PANE IS A STACK OF SECTIONS YOU CAN
CLOSE**, and they are the Properties pane's sections: `PropGroup` now wraps
Frames, Audio, and the Shapes tab's library and placed list. ⚠ **Never write a
second collapsible for a second pane** — same twist, same count pill, two panes
side by side. `FrameStrip` gained `heading={false}` so the section header isn't
saying "Frames (31)" twice; the add-assets card stays outside the sections
because a drop target you can fold away is one you cannot drop on.

Before that: **THE REEL WORKSPACE GIVES THE MONITOR THE WHOLE
HEIGHT.** A 9:16 picture is bounded by HEIGHT, so while Program was a pane in the
top row the monitor stayed a stamp however wide its column was dragged. The reel
workspace is now a **grid** (`an-ws-reel` in `styles/animatic-editor.css`):
Program is a full-height left column, Media and Properties sit beside it, and the
timeline stacks under Media on the right. ⚠ **No markup changed** — `.an-panes`
goes `display: contents` and named areas (not rows-by-position, because the
status strip is conditional) place everything; every rule is `:not(.an-has-max)`
so ~ still works. Reel defaults in `pane_layout.js` now derive the monitor's
width from the height it can reach. Also: **a workspace icon is a MAP of the
workspace** — `layout-long` / `layout-reel` draw the real seams with the Program
pane filled in, and the top bar's gear is replaced by the layout you are in.
**Built clean; not driven in a browser.**

Before that: **CHANGING THE FRAME SHAPE NOW REDRAWS AND
CARRIES.** Two faults behind "the video stretch and the shapes don't resize":
⚠ **the monitor never redrew when only its BOX changed** (the canvas kept its
old pixels and the browser scaled them — the Scale-110 workaround was just
forcing a redraw; pane drags and window resizes had it too), fixed with an
aspect dependency *and* a ResizeObserver in `ProgramCanvas.jsx`; and ⚠ **a
shape's `w`/`h` are fractions of the FRAME**, so `refitBox()` in
`animatic/aspects.js` carries shapes and overlays across a change of shape,
preserving proportion and apparent size and round-tripping exactly. Pictures are
deliberately not carried — `placePicture` re-fits them itself.
⚠ **`reshapeFrame()` is the ONE way in**; a bare `setSettings({aspect_ratio})` is
now a bug (the export presets went through it too). `tests/aspect_refit_check.py`
pins the arithmetic against the server's own `resolve_size` — and caught a real
parity bug on the way (unlisted ratios were derived off the short edge here and
the long edge there). **The redraw fix has not been driven in a browser.**

Before that: **THE ASPECT RATIO IS A MENU IN THE PROGRAM
PANE'S HEAD**, next to the title, in every workflow and both workspaces. It was
only ever in Video properties, which is the pane you are *not* looking at
whenever a clip is selected — hence "I switched to Reel / Shorts and my video is
still 16:9 and I can't change it". One list of shapes now
(`client/src/animatic/aspects.js`) feeds the menu, the Shape chips and the export
dialog's size table. ⚠ **A WORKSPACE STILL WRITES NOTHING** — in the Reel
workspace a landscape film gets a `Make it 9:16` **button**, which is the user
changing it, not the layout doing it behind their back. Built clean; **not driven
in a browser.**

Before that: **THE MEDIA PANE LISTS ASSETS TWO WAYS, AND
＋ ADD LAYER IS AT THE TOP OF THE GUTTER.** Icon view (thumbnails in a fluid
grid) or list view (a compact row each: small thumb, name, hold, tools), chosen
in the Media pane's head and remembered per browser
(`cas_animatic_media_view`, `client/src/animatic/media_view.js`). ⚠ **THE VIEW IS
CSS ONLY** — the same cards in the same order — so a drag reorders identically in
either, and the Reel workspace no longer forces a grid of its own (it would
overrule the switch). ＋ Add layer moved from under the last lane to a head above
the gutter; ⚠ **it must stay OUTSIDE `.tl-cols`**, or the labels shift down while
the tracks don't. Built clean; **not driven in a browser.**

Before that: **THE EDITOR HAS WORKSPACES, AND THE PANES ARE
DRAGGABLE.** ⚙ in the animatic editor's top bar switches between **Long Video
Workspace** (the arrangement that was always there) and **Reel / Shorts Video
Workspace** (Program moves to the left and goes tall; Media takes the width the
wide monitor had and lays its frames out in a grid). ⚠ **A WORKSPACE IS LAYOUT
ONLY** — it writes no project field, so a 16:9 animatic is still 16:9 while you
cut it on a vertical-feeling screen, and the monitor keeps the project's real
shape in both layouts because that is what will be exported. The choice lives in
`localStorage` (`cas_animatic_workspace`, `client/src/animatic/workspace.js`),
not on the animatic — it's how you like to work, like the theme.

**The three seams between the four panes are handles now** (`PaneSplitter.jsx`
in the 0.55rem the grid used to spend on `gap`): drag to size Program / Media /
Properties / Timeline, double-click to reset that one, arrow keys once focused.
⚠ **THE STYLESHEET NO LONGER DECIDES HOW BIG A PANE IS** — three px numbers per
workspace do (`animatic/pane_layout.js`), defaulted as a fraction of the window,
clamped against the window on the way out, and saved only once you have actually
dragged something. A breakpoint may no longer rewrite a width you chose.
⚠ **HOOKS IN `AnimaticEditor` GO ABOVE ~line 430** — below that they are past
`if (loading) return …`, which is a hook-count mismatch and a **black page**, the
way this shipped for an hour. Driven in a real browser: drags, clamps, reset, ~,
stacking, and the sizes surviving a reopen.

Before that: **PHASE 8 IS IN: THE RENDER GOES WIDE, AND AN
EXPORT IS NOT ALWAYS AN MP4.** The still loop runs across processes (29.0s →
11.8s on 8 workers over 216 stills, **byte-identical output**), the editor
scrubs on half-res proxies, and the export dialog opens on a preset —
YouTube / TikTok / Reels / GIF / Still. Four things to know before touching any
of it. **(1) The stills are PLANNED, then DRAWN** — names are assigned in one
pass, so they cannot depend on which worker finishes first, and that alone is
why parallel and serial encode to the same bytes.
**(2) `_detached_main` closes the Windows-spawn trap for every caller**, so no
script needs an `if __name__ == "__main__"` guard to render in parallel; proved
by neutering it and watching an unguarded probe re-run itself once per worker.
Cancellation stays in the parent, because a worker cannot see the job store.
**(3) A preset states ONLY what it means** — GIF and Still deliberately do not
state an aspect ratio, so exporting a thumbnail cannot reshape the film, while
TikTok does because that is the entire point of choosing it; `match()` is the
exact inverse of `apply()`, and a PNG never reaches ffmpeg at all.
**(4) A proxy saves PIXELS; bytes are the usual case, not the guarantee** (a
downscaled PNG of line art can be *larger*) — and **the export never touches
`proxies.py`**, so no proxy can reach the encoder. 57 offline checks, the key
one hashing two MP4s; **nothing has been opened in a browser, and the monitor
now draws from proxies.**

Before that: **PHASE 7 — THE BOARD REACHES INTO THE
EDITOR.** Redraw a shot from the Properties pane, run a shot longer, cut to the
beat, re-frame a whole board for a new screen shape. Four things to know before
touching any of it. **(1) A FRAME'S URL CARRIES `?v=` NOW, and that is the whole
feature** — a frame has always been a REFERENCE to a board panel, so a redraw
has always updated the animatic, and it has never been visible because every
picture here is an authed blob cached BY URL and the url was built from two ids
a redraw does not touch. The server stamps the panel's mtime in
(`_frame_version`) AND the editor remembers which url each blob came from
(`urlSrcRef`); either half alone changes nothing. **(2) The board's two actions
have ONE implementation each**, in `server/common.py` — a second copy of the
continuity bible and the resume arithmetic is two things to keep in step and one
of them would silently fall behind. **(3) "2s longer" EXTENDS the pose plan
rather than re-planning it**: the lines the drawings on disk were made from are
kept word for word and only the tail is bought, because a re-plan leaves drawing
17 continuing a motion drawings 1–16 never made and nothing but playing it shows
that. **(4) The reframe asks the model for the SUBJECT, never for the crop** —
code is exact at aspect arithmetic and a model is approximate at it — and it
writes ordinary keyframable `scale`/`x`/`y`, so an auto-reframed shot is one
somebody could have panned by hand. 147 offline checks, the key one measuring
autoframe's output through the REAL `place_picture`; **no real AI call has been
made and nothing has been opened in a browser.**

Before that: **CAPTION BOXES FILL THE WAVE BLOCKS**
(user-reported, twice: *"the caption plays after the voiceover"*, then *"there is
blank space — the box starts before the wave"*). Four things to know before
touching any of it. **(1) `transcribe()`'s WORDS are excellent and its TIMES are
a guess** — it is a language model listening, not a forced aligner, and every
caption-timing bug that survived `clip_lines` and `tidy_lines` was those two
faithfully placing numbers that were already wrong. **(2) The times are MEASURED:**
`captions.peak_envelope` has ffmpeg decode the track and keeps one PEAK per 20ms
window — ⚠ the same quantity `beats.js::peaksOf` draws the timeline waveform
from, so a measured run of sound is a visible block — and the threshold is
derived from the track's own noise floor and peak, **capped** (`MAX_THRESHOLD_SHARE`)
because on continuous narration the "quietest tenth" is speech, not silence.
**(3) `align_lines` DEALS the lines into the runs and FILLS each run exactly**,
first line starting where the sound starts and last ending where it stops. Two
invariants hold by construction, and both are asserted: no caption ever starts in
a silence, and every run of sound is covered end to end. It runs BEFORE
`clip_lines`, in FILE time, so the razor is unaware of it, and it **declines to
guess** — an unreliable measurement returns the model's own times. **(4)
`tidy_lines` rule 2 was backwards:** the gap between two captions comes off the
EARLIER one's end, because a start is when the word is *said* and an end is only
how long the line has been left up. 41 offline checks; **no real captions run has
been made and nothing has been opened in a browser** — including the new progress
row in the properties pane.

Before that: **A SELECTION IS A LIST NOW** (user-reported: *"I
have to select and delete one by one"*). Three things to know before touching any
of it. **(1) TWO KINDS OF "SELECTED":** the six `selected*Id` states are the
PRIMARY — the one clip the Properties pane describes — and `selection` is the
whole list a rubber band, a shift-click or a group produces. **`selectOnly` is
the only writer of both**, and where a group expands; a third way to select
something goes through there. **(2) The marquee hit-tests `data-sel` NODES** —
every selectable thing carries `data-sel="kind:id"` and the band intersects their
rects, because each lane already knows where it puts its clips and a second copy
of that arithmetic would drift. **(3) `group_id` is a shared string on the
members, never a container** — and the razor's new piece and a duplicate both
LEAVE the group, or you could not take a pause out of a grouped clip. New model in
`client/src/animatic/selection.js`, 34 new checks in `tests/selection_check.py`,
`npm run build` clean; **not opened in a browser, and this adds a mouse gesture
that shares a press with scrubbing.**

Before that: **CAPTIONS FOLLOW THE CUTS AND HAVE A LANE OF
THEIR OWN, and the picture lane stopped drifting away from the ruler.** All
user-reported, one screenshot. Three things to know before touching any of it.
**(1) A transcript is of the FILE; the timeline holds CLIPS cut out of it** —
`captions.clip_lines` is what walks one onto the other, per clip, dropping the
words whose audio was cut out and splitting a sentence that was cut through.
Shifting the whole transcript by one clip's `start − offset` (what it did) is
correct only on an uncut track. **(2) Generated captions live on a RESERVED lane**
— `CAPTION_LAYER_ID`, a twin of `client/src/animatic/captions.js` — written by the
server together with its clips in one update, drawn ABOVE the picture row, and
rendered from the clips alone if the layer record ever goes missing. **(3) The
picture lane is placed by TIME, not by flow:** `.tl-bar` was a flex item whose
padding put a ~13px floor under every bar, so short frames shoved the rest of the
sequence off the end of the timeline — which is what "the scrollbar won't show
everything" was. 20 new offline checks, `npm run build` clean; **nothing has been
looked at in a browser and no real captions run has been made.**

Before that: **THE RAZOR CUTS AUDIO IN THE MIDDLE NOW, and
every property row has a ↺.** Both user-reported. An audio track used to be
pinned to 0:00 with only its two ends trimmable, so a pause in the middle of a
take was uncuttable by construction; a clip now carries **`start_ms` (where it
sits on the timeline) as well as `offset_ms` (how far into the file it reads)**
and the razor sets BOTH on the second half — set one without the other and the
audio jumps at the cut. Four things to know before touching any of it: **an
audio entry is a CLIP and its identity is `id`, not `upload_id`** (several clips
share one upload after a cut; `_audio_tracks_of` backfills `id` from the upload
for every project saved before this); **a lane holds a LIST of clips**
(`lane.tracks`); **`adelay` goes AFTER the fades** in `audio_graph`, or the ramps
stay at the head of the video; and **playback is now scheduled per frame**
(`syncTracks`) while the first playing clip is still the master clock and is
never drift-corrected. The razor's arithmetic is editor-only, in
`client/src/animatic/audio_clips.js`, checked by `tests/audio_razor_check.py`.
The ↺ is `ResetButton` in `PropGroup.jsx`, **always rendered and disabled at the
default** — a lit column of them is the list of what you have changed — and on an
animatable row it clears that property's keyframe track too. 87 offline checks
pass and `npm run build` is clean; **nothing has been looked at in a browser, and
this changed both timeline interaction and every pane's layout.**

Before that: **Phase 6 is in and complete: AUDIO DEPTH.** A
track now has a fade at each end, a three-band EQ, a duck under the voice, and
its beats on the timeline (drawn AND snapped to). **Preview playback goes through
a WebAudio graph now** (`client/src/animatic/audio_engine.js`) — an `<audio>`
element caps volume at 1 and cannot be EQ'd — but ⚠ **the element is still the
master clock**, and every failure in that file falls back to `el.volume`. Four
things to know before touching any of it: `fade_window`/`EQ_BANDS` in
`animatic.py` and `fadeWindow`/`EQ_BANDS` in `client/src/animatic/audio_mix.js`
are **a twin pair**, checked by running the JS through node; **which track is the
voice is stated (`role`), never guessed**; `amix=…:normalize=0` must survive
every edit to `audio_graph`; and the shelves must keep stating `t=s:w=1`, or
ffmpeg and WebAudio build different filters. The duck is a compressor, so **the
preview is close rather than exact — the one remaining gap in this editor's
preview**, and the pane says so. `tests/audio_mix_check.py` encodes, decodes and
measures the result (49 checks); **nothing has been looked at in a browser, and
this time the whole preview signal path changed.**

Before that: **An open `<select>` was unreadable** (white list,
near-white text, user-reported): a select drawn `background: transparent` makes
Chromium paint its popup on the browser's white default, so `option`/`optgroup`
now state their colours explicitly in `theme.css`. **Read that rule's comment
before styling any select** — `color-scheme: dark` does not cover this case.

Before that: **The TIMELINE SCROLLS LIKE PREMIERE'S now
(user-requested, with a screenshot).** A bar along the bottom and one down the
right-hand side, each with a round grip at both ends that ZOOMS instead of
scrolling — `client/src/components/ZoomScrollbar.jsx` +
`client/src/styles/animatic-scrollbar.css`. The native scrollbars are hidden but
still driven (wheel, trackpad, the Hand tool), the ruler is pinned to the top of
the scroller, the gutter is moved by hand to stay beside its tracks, and the zoom
became CONTINUOUS pixels-per-second instead of six fixed steps. `npm run build`
is clean and the component was **driven in a headless Chromium** (grip-zoom,
pan, track-height zoom, scrolled alignment — all measured), but **the real
editor has still not been opened — see Next Steps.**

Before that: **The PROPERTIES PANE was rebuilt as one pane
(user-reported: "confusing").** It is now Premiere's Effect Controls grammar:
named collapsible sections, one property per row on a fixed two-column grid, one
size for every control, and colour used to rank things rather than to decorate
them. Everything a pane is made of lives in
`client/src/components/properties/PropGroup.jsx` + `client/src/styles/properties.css`
— **read that file's header before editing any pane**, because the alignment
depends on two rules stated there (one property per row; rows in `ANIMATABLE`
order, to match the timeline's diamond rows). `npm run build` is clean and every
selector the e2e suite reaches for was kept, but **the editor has not been opened
in a browser since — this is a layout change and needs eyes.**

Before that: **Phase 5 is in: the TEXT ENGINE. The font is now
a FILE THAT SHIPS WITH THE PROJECT, and that is the point of the whole phase.**
`_text_font` used to grope for `arial.ttf` while the monitor asked CSS for the
nearest system sans; on any machine where those differ, the caption you
positioned was not the caption that landed in the MP4. Six OFL fonts now live in
`client/public/fonts/` and `animatic_fonts.py` ⇄ `client/src/animatic/fonts.js`
are a TWIN PAIR checked by `tests/captions_check.py`, exactly like the scene
model. A caption gained real type (stroke, shadow, letter spacing — every unit
chosen so both sides use the same number), free placement (`place: flow | free`
with keyframable `x`/`y`), and in/out presets that are **keyframe macros and
nothing else**: the exporter needed no changes at all. Auto-captions come from an
audio track and a voiceover from the board's dialogue, both timed from data we
already hold. **⚠ NO REAL GEMINI CALL HAS BEEN MADE — both AI paths are proven
against stubs only.** Read the top Work Log entry before touching any of it.

Before that: **Phase 4: the LOOK (colour, LUT, masks,
chroma key, blend modes), and the Program monitor is a WebGL canvas.** A frame
and an overlay each carry `effects` / `mask` / `blend`, and every numeric
parameter is keyframable through the ordinary ⏱ using a FLAT track name
(`fx:<effect id>:<param>`, `mask:<field>`) — so `keyframes` stays the shape it
has always been, and the timeline, the undo stack and the inspector all work on a
graded clip unchanged. The pixel maths is written twice, `animatic_effects.py`
and `client/src/animatic/gl/shaders/`, and the two are compared with a TOLERANCE
rather than exactly, because WebGL and Pillow never will be byte-identical.
**Read the top Work Log entry before touching any of it.**

**Three pre-existing bugs fell out of this, all of one kind: the preview was
lying and nothing was checking.** The export payload in `server/animatics.py` was
a hand-written dict that had fallen behind the model, so **every transition was
inert in the MP4** and **a Ken Burns push exported as a still**; and the fast
planner dropped a STATIC transform. That payload is a `model_dump` now and must
stay one.

**⚠ THE SHADERS HAVE NEVER BEEN COMPILED.** The pixel half of
`tests/effects_parity_check.py` needs `headless-gl`, which would not build on
this machine, and the editor has not been opened in a browser. Its static half
passes and the whole Python side is pinned to golden values, but nothing has yet
proved the monitor draws. That is the first thing to do next.

**`tests/e2e_animatic.py` passes all 14 sections again.** It had been crashing at
section 5 since the LANES work, so sections 6-14 — the Media pane, the Properties
panes, keyframes, all five viewports — had not run for several sessions. Three of
the four failures were a stale test, one was the test misreading the app, and
none was a bug in the app; **when one of its assertions fails, work out which
side is wrong before changing either.**

Before that: **the timeline holds VIDEO CLIPS**, alongside
stills and colour cards (Phase 3). A "frame" is a clip: `kind` says what it is,
and a video adds `in_ms`/`out_ms`/`speed`. **`duration_ms` is still the length on
the TIMELINE for every kind — speed widens the SOURCE window, it does not re-time
the clip** — which is why nothing else on the timeline moves when you change it.
Pillow can't decode video, so `video_frames.py` extracts stills with ffmpeg and
caches them by content; **there is still no ffprobe**. A clip gets there two
ways — **dragged in from the desktop, or generated by Veo with ✨ Animate** — and
a generated clip lands as an ordinary video upload so the two are the same object
from that moment on. **The ✨ button SPENDS MONEY**: read the top Work Log entry
and the 2026-08-07 one before touching it, and note that render records live in
the job's `result` because the autosave would otherwise erase a clip that was
paid for.

Before that, and still true: **the editor has KEYFRAMES and TRANSITIONS** (⏱ per
property, draggable diamonds, easing, per-gesture undo; dissolve / dip / wipe /
slide on any cut), and the scene model underneath them is written twice on
purpose. `client/src/animatic/scene.js` + `client/src/animatic/transitions.js`
and `animatic_render.py` are the same evaluator in two languages; the preview and
the exporter both go through it, and `tests/render_parity.py` fails the moment
they disagree. Read the top Work Log entry before touching any of those files, or
before adding any property that varies over time.

**The one thing to know about transitions before you change them:** they are
**boundary-local** — the blend straddles the cut and the timeline does NOT get
shorter. Everything else about the design follows from that, and the reasoning
is at the top of `transitions.js`.

This is Phases 0–2 of the CapCut/VN-style editor plan — see **Video editor
roadmap** near the bottom.

Before that: 2026-08-11 — public landing page rewritten to cover all six
workflows (the workflow list there is the third copy of `Sidebar.jsx`'s
`WORKFLOWS` and must be kept in step with it).

Before that: 2026-08-09 — three passes, all from user reports on the same
board. Read the top three Work Log entries, and the CONTINUITY one below them,
before touching any panel prompt, key-pose prompt, or panel-image cache.

- **(a) KEY-POSE SCOPE.** Pose 1 is now the panel COPIED, not generated, and a
  shot may no longer animate its way into the next one — the pose planner gets
  the neighbouring shots plus a written `hold` invariant.
- **(b) REGENERATE NOW REDRAWS, and looks like it.** It sent `resume=true` and
  so did nothing at all on a finished shot; a redrawn pose kept its URL so the
  new picture could never show; and no loading state ever appeared over a
  picture that already existed.
- **(c) REFRESH ONLY WHAT CHANGED.** `reloadBoard()` was wired to the panel
  version arrows, so switching one shot's version blanked and re-downloaded
  every tile. `refreshPanelImage` is the single-panel tool; `reloadBoard` is for
  insert/delete only.
- **(d) NO DRAWN FRAME.** Asking for a "panel" got us the BOX as well as the
  picture, on 138 of 371 real panels. The prompt now asks for a full-bleed
  IMAGE, and `strip_drawn_border()` crops any frame that still gets drawn.

---

## 📦 Project overview

**Text-to-Animation AI workflow — Character Asset Generation Pipeline.**

From a single reference photo of a person/character, generate clean turnaround
asset sheets (front / left / three-quarter / back) for each body part and
garment, then optionally turn them into 3D models via Meshy.

Pipeline stages (see `pipeline.py`):
0. **Reference image (Step 0, optional)** — user provides a text prompt → Gemini
   generates a T-pose character on white background (`gemini_client.py:
   generate_character_reference`). Alternative: user uploads their own image.
1. **Fullbody sheet** — Gemini image model turns the reference photo into a 2×2
   turnaround grid.
2. **Per-part sheets** — each part (hair, face, upper/lower garment, shoe, …) is
   generated using the fullbody sheet as reference.
3. **Split** — cut each 2×2 sheet into 4 view images (`splitter.py`).
4. **Post-process** — clean white background + auto-crop + normalize to
   1080×1080 (`postprocess.py`).
5. **Store** — save locally + upload to GCS + zip (`storage.py`).
6. **3D (optional)** — submit 4 views per part to Meshy (`meshy.py`).

---

## 🗂️ Architecture / file map

### Core pipeline (Phase 1 — CLI)
| File | Responsibility |
|------|----------------|
| `run_character.py` | CLI entry point (argparse → `run_pipeline`). |
| `pipeline.py` | Orchestrates all stages. `run_pipeline(...)`. |
| `gemini_client.py` | Image generation. **Switchable backend: Vertex AI or Gemini API.** Also owns the storyboard panel prompt and its **three continuity channels** — `build_cast_context` / `build_set_context` (the written bible), the scene look-anchor, and `build_flow_context` (what runs either side of this shot). `resolve_name()` is the shared alias-tolerant name matcher; a tie deliberately returns None. |
| `script_breakdown.py` | Script→Storyboard Stage A: script → shot list (LLM). **Switchable text backend (`TEXT_PROVIDER`): Vertex AI or Gemini API.** |
| `splitter.py` | Split 2×2 sheet → 4 views. |
| `postprocess.py` | Clean white bg + crop + normalize. |
| `storage.py` | Local save, GCS upload, zip. |
| `meshy.py` | Meshy multi-image-to-3D submit + poll. |
| `tripo.py` | Tripo.ai multiview-to-3D submit + poll. **UNVERIFIED** (no live test). |
| `prompts.yaml` | Prompt templates + per-template `parts_order`. Subject types: `default` (human, gender inferred), `human_male`, `human_female`, `robot`, `animal`, `bird`, `monster`, `ghost`. Global `parts_order` fallback. |
| `postprocess.py` | Clean white bg + auto-crop + **group-normalize** (4 views share one scale). |
| `splitter.py` | Split 2×2 sheet → 4 views at natural aspect (NO square resize). |
| `animatic.py` | **Storyboard → Animatic.** Timed image sequence + text layer + audio → MP4. Owns the ffmpeg integration: `ffmpeg_exe()` and `run_ffmpeg()` are public so `video_assemble.py` reuses them. `plan_segments()` cuts the timeline wherever a text clip starts/ends; `draw_texts()` burns captions in with Pillow. Also owns **the MIX** — `audio_graph()` builds the levels, the `afade` ramps and the sidechain duck, and returns None when nothing needs a filter at all; `track_play_ms`/`fade_window` are a **⚠ TWIN of `client/src/animatic/audio_mix.js`**, and `amix=…:normalize=0` must survive every edit to it. Since Phase 8 the stills are **planned in one pass and drawn in another, across processes** — names cannot depend on which worker finishes first, which is why parallel and serial encode to the same bytes — and `container` picks what file comes out (`mp4` / `gif` / `png`; a PNG never reaches ffmpeg). See `_detached_main` before assuming a caller needs an `if __name__ == "__main__"` guard: it doesn't. Spends no AI quota. |
| `export_presets.py` | **"Make me a file for X", as a named set of export settings** — YouTube / TikTok / Reels / GIF / Still. **⚠ TWIN of `client/src/animatic/export_presets.js`**, compared field for field through node by `tests/export_perf_check.py`, because the dialog promises a size and a frame rate before anything is encoded. Two rules: a preset **states only what it means** (GIF and Still deliberately carry no aspect ratio, so a thumbnail cannot reshape the film), and `match()` is the exact **inverse** of `apply()`, so editing a field by hand simply reads "Custom". Owns `container` — mp4 / gif / png — which `animatic.py` honours. |
| `proxies.py` | **Half-res copies of the pictures, for the editor to scrub on.** One lossless PNG per (source, mtime, size, rung), cached beside the animatic; the frame route serves one when asked with `?w=`. **⚠ THE EXPORT NEVER TOUCHES THIS** — `build_animatic` opens sources, so no proxy can reach the encoder, and what the preview trades is sharpness at high zoom and nothing else. What it saves is guaranteed in PIXELS (a quarter of the decoded bitmap for a 1920px panel) and usually, but **not always**, in bytes. Keyed by a `stat`, never a decode — same rule as `_frame_version`, and for the same reason. Every failure path returns the source path. |
| `animatic_render.py` | **The scene model: what the frame looks like at time t.** Which clips are on screen, what every animated property has interpolated to, and — mid-cut — which SECOND picture is blending in and how far. **⚠ TWIN of `client/src/animatic/scene.js` (and, for the `transition_*` half, of `client/src/animatic/transitions.js`)** — the same evaluator in two languages, so the preview and the export agree; `tests/render_parity.py` fails the moment they don't. Also owns `place_picture()`, a frame's own pan/zoom, which has to happen while the picture is fitted rather than after. Knows nothing about ffmpeg. |
| `video_client.py` | **Animatics → Final Video.** Veo image→video. The ONLY module that knows Veo exists. **Switchable backend (`VIDEO_PROVIDER`): Vertex AI or Gemini API** — same shape as `gemini_client.py`. **BILLED PER SECOND OF OUTPUT.** `estimate_cost_usd()` lives here. There is no Google Flow API — read the module docstring. |
| `animatic_effects.py` | **The LOOK, in pixels** — brightness / contrast / saturation, a 3D LUT from a `.cube`, a chroma key, feathered masks and the blend modes. **⚠ TWIN of `client/src/animatic/gl/shaders/`**, and the ONE twin in this project that cannot be compared exactly: WebGL and Pillow will never be byte-identical, so `tests/effects_parity_check.py` compares them with a tolerance while `tests/effects_check.py` pins this side to golden values. Deliberately NOT `ImageEnhance` — its contrast pivots on the image's own mean, which a fragment shader cannot know. Numpy; no ffmpeg, no quota. |
| `animatic_fonts.py` | **The caption fonts, server side** — the bundled list and the path to each `.ttf` in `client/public/fonts/`. **⚠ TWIN of `client/src/animatic/fonts.js`**, element for element, checked by `tests/captions_check.py`. Exists because a font resolved by NAME resolves differently on a laptop and a server, so the caption in the monitor is not the caption in the MP4. Never asks the machine it is running on. |
| `client/public/fonts/*.ttf` | The six OFL faces themselves, served to the browser at `/fonts/` and opened off disk by the exporter — ONE file for both sides, which is the whole design. Licences in that folder's `OFL.txt` and `README.md`. |
| `captions.py` | **Audio → timed caption clips.** ⚠ SPENDS QUOTA, in one call. Three parts on purpose, and only the first costs anything: `transcribe()` is the model call; **`clip_lines()` walks the transcript THROUGH THE RAZOR** — the model heard the FILE, the timeline holds CLIPS cut out of it, so this moves each line onto the timeline where it is actually heard, splits a sentence a cut went through by character count, and drops what was cut out; `tidy_lines()` is the drawing rules (order, never overlap, long enough to read, inside the video). The free two are where every "the subtitles are on top of each other" / "the captions don't match the audio" bug lives, and a failure there must not mean paying to listen again. A generated caption is marked ONLY by its `cap…` id prefix and lives on the reserved `CAPTION_LAYER_ID` lane — **⚠ TWIN of `client/src/animatic/captions.js`**. |
| `tts.py` | **Dialogue → a spoken voiceover, and the CASTING for it.** ⚠ SPENDS QUOTA, one call PER LINE. Holds `CAST` (the voices) and `PERSONAS` — a persona writes the stage direction a line is read with, which is the only way an age and a sex reach the model, AND casts the voice; `persona_from` guesses one off the board's cast sheet for free. ⚠ **WHERE a line goes is NOT decided here** — `speak_lines` reads one shot's lines and `assemble` lays finished blobs where it is told; the one clock over the pictures and the sound is `_lay_out_speech` in `server/animatics.py`. **The one place here that knows a sound's length without being told**: raw PCM at a known rate, so the byte count IS the duration and no ffprobe is needed. |
| `luts/*.cube` | The built-in colour looks, as FILES — read by `Color3DLUT` for the export and fetched by the browser for the monitor, so there is one copy of the numbers. Regenerate with `python luts/generate_luts.py`. |
| `video_assemble.py` | Joins rendered clips into the final cut (`cut` = stream copy, `crossfade` = re-encode). Free and repeatable — spends nothing. Reuses `animatic.py`'s ffmpeg helpers. Take `durations_ms` from the caller: **there is no ffprobe** on an `imageio-ffmpeg` install. |
| `panel_sequence.py` | **Image to Animatic Image.** One drawn panel → its KEY POSES for a shot of 2/4/6/8/10s. Reasons in real frames (4s×24fps=96) but returns the ~4-per-second drawings that carry the motion. TEXT model plans the poses, IMAGE model draws them — **each anchored on the source panel, never on the previous frame** (see the docstring; chaining drifts). **Pose 1 is the panel COPIED, not drawn** — it is already approved and generating it produced a different first picture every time. **The camera never moves inside a sequence** — a cut is a new shot. **Nor does the STORY move**: `plan_beats` is given the neighbouring shots (`story_context`) and returns a `hold` invariant that fences every drawing, or a shot with no written action invents the next shot's. `frames_on_disk()` is the one honest answer to "which poses exist": holes are holes, not the end of the sequence. |
| `retry_policy.py` | When to retry a Google AI call and how long to wait. Shared by `gemini_client.py` and `video_client.py` so one tuned policy governs both. Pure functions over an exception. |

### Server (Phase 2 — FastAPI backend, in `server/`)
| File | Responsibility |
|------|----------------|
| `server/main.py` | FastAPI app, most endpoints, provider validation. |
| `server/animatics.py` | `/animatics` router: animatic CRUD, media upload, frame/audio serving, export, stop. |
| `server/videos.py` | `/final-videos` router: Animatics → Final Video. Project CRUD, art tray, per-shot Veo render, assembly, serving, stop. **The only router that can spend money** — every such path estimates first and caps the batch. Also exports `render_one_shot` / `update_shot` for the worker. |
| `server/common.py` | Helpers shared by `main.py` and `animatics.py` (`get_owned_job`, `board_dir`, `variants_of`, `panel_path`). They live here so the two route modules don't import each other. |
| `server/config.py` | Env-driven settings (paths, job store, auth, Mongo). |
| `server/schemas.py` | Pydantic models (`Job`, responses, `MeshyRequest`). |
| `server/jobs.py` | Job store: **MongoDB (default, `API_JOB_STORE=mongo`)** + Firestore and in-memory/JSON fallbacks. |
| `server/worker.py` | ThreadPoolExecutor running the pipeline off-request. |
| `server/security.py` | bcrypt password hashing + JWT create/verify. |
| `server/users.py` | MongoDB-backed user store (`users` collection). |
| `server/auth.py` | `/auth/register`, `/auth/login`, `/auth/me`, `get_current_user`. |

### Client (Phase 3 — React + Vite, in `client/`)
| File | Responsibility |
|------|----------------|
| `client/src/api.js` | Fetch client, JWT in localStorage, auth'd blob download, cache-busted zip, friendly network errors. |
| `client/src/animatic/keyframes.js` | **Editing keyframes** — the operations behind the ⏱ button (add / remove / move a key, set a curve, start and stop animating a property). `moveKeysAt()` is the one a timeline diamond drags: a diamond is an INSTANT, so every property keyed there moves together. Pure, returns PATCHES rather than mutating, so a keyframe edit is an ordinary document edit and Ctrl+Z works on it. No Python twin: the server renders animations, it never edits them. |
| `client/src/components/KeyframeControls.jsx` | The `⏱ ‹ ◆ › curve` row that sits at the end of an animatable property in the Properties pane. Renders and reports intent only — every operation on the data is in `animatic/keyframes.js`. |
| `client/src/animatic/scene.js` | **The scene model, client side** — `sceneAt(project, t)`, keyframe interpolation, easing, `isAnimated`, `sceneSignature`. Pure: no React, no DOM, no urls. **⚠ TWIN of `animatic_render.py`.** Read its docstring before adding any property that varies over time. |
| `client/src/animatic/fonts.js` | **The caption fonts, client side** — the same list, plus the generated `@font-face` rules. **⚠ TWIN of `animatic_fonts.py`.** The rules are GENERATED rather than written in a .css file: a third hand-maintained copy of the list is exactly the `_SHAPE_POINTS`/`POINTS` failure. Families are namespaced so a user's own copy of Inter can never win. |
| `client/src/animatic/text_presets.js` | **In/out text animations, as KEYFRAME MACROS.** Fade / Rise / Drop / Slide write keys on `opacity`/`x`/`y` and get out of the way — nothing is stored on the clip and neither renderer has heard of a "preset", which is why the exporter needed no changes. A moving preset switches the clip to free placement, or it would animate nothing. |
| `client/src/App.jsx` | Landing → Login → sidebar shell. Nav state, upgrade + account (logout) popups. |
| `client/src/components/Landing.jsx` | Public marketing landing page (full-bleed). |
| `client/src/components/Login.jsx` | Login / register + "Continue with Google" (UI only, not wired) + back-to-home. |
| `client/src/components/Sidebar.jsx` | Nav rail: Home + Workflows (Text to Image live; others "Soon") + profile chip + gold Upgrade. |
| `client/src/components/Home.jsx` | Profile, plan/credits, recent work + downloads, saved 3D API keys, delete account. |
| `client/src/components/WorkflowSoon.jsx` | Placeholder for roadmap workflows. |
| `client/src/components/GenerateForm.jsx` | Describe/Upload tabs (drag-and-drop), subject-type dropdown, parts multi-select chips + custom asset. |
| `client/src/components/JobList.jsx` | Owner's jobs; auto-polls while active. |
| `client/src/components/JobDetail.jsx` | Live progress bar + per-section skeletons, incremental gallery, per-view/section regenerate, failed-part retry, per-section download + 3D popup. |
| `client/src/animatic/gl/compositor.js` | **The Program monitor's compositor.** WebGL: the pictures, the transition, the SHAPE FILLS and the overlays, each with its own effects, mask and blend. Blend modes work by ping-ponging two framebuffers so a layer can sample what is under it. Holds `placePicture` (twin of `place_picture`) and `overlayRect` (twin of the sizing in `draw_overlays`). ⚠ The shape polygons here are the THIRD copy of `POINTS` / `_SHAPE_POINTS` — a clip-path, a Pillow polygon and a vertex buffer genuinely cannot share one representation. |
| `client/src/animatic/gl/shaders/` | `effects.js` (one GLSL function per effect, plus the mask and the blend) and `layer.js` (the one program everything is drawn with). Exported as STRINGS, not `.glsl`, so the parity harness can import the exact source the browser compiles under plain node. The effect numbering is generated from `EFFECT_KINDS`, never typed twice. |
| `client/src/animatic/gl/cube.js` | Parsing a `.cube` (twin of `parse_cube`) and laying the table out as the 2D strip the shader samples. ⚠ Imports NOTHING — one import of `api.js` would make the whole compositor unloadable outside a bundler and break the parity test. `lut.js` is the browser half that fetches and caches. |
| `client/src/components/ProgramCanvas.jsx` | The monitor. Draws the scene through the compositor and keeps the media elements it uploads as textures HIDDEN but in the document — a `<video>` will not decode otherwise, and `useMonitorVideo` still drives them through `videoElsRef`. The captions, the label and the drag handles stay in the DOM over the top. |
| `client/src/components/EffectsPanel.jsx` | The Look rows slotted into `FrameProperties` and (for an overlay) `ShapeProperties`. Every numeric parameter wears the ordinary ⏱; the track is just named `fx:<effect id>:<param>`. Structure is read from the STORED clip and values from the RESOLVED one — `resolveLook` drops an effect kind this build doesn't know, so editing the resolved list would delete the wrong row. |
| `client/src/components/StoryboardToAnimatics.jsx` | Animatics workflow shell: library ⇄ one open animatic. |
| `client/src/components/AnimaticLibrary.jsx` | "Your Animatics": New / From a Storyboard tiles + Recent / All sections. **Mirrors `StoryboardLibrary.jsx` and shares its `.lib-*` styles** — change a card in one, change it in both. |
| `client/src/components/AnimaticEditor.jsx` | The editor, as an **NLE workspace**: top bar + status strip + Media / Program / Properties panes over a full-width timeline, fixed to the viewport height. What is left here is the workspace itself — the panes, the media it fetches, the edits it makes, and the two server jobs it can start. The document, the clock and the undo stack are the three hooks below; the Properties panes are in `components/properties/`. |
| `client/src/animatic/useAnimaticProject.js` | **The document**: loading it, holding it, autosaving it. "Is this saved?" is decided by comparing content against a BASELINE signature, never by "did an effect fire" — read the header before touching it. Owns `frameForSave`. `veo_clips` arrives on the project and is **never sent back**, so a save cannot erase a paid render. |
| `client/src/animatic/useTimelineTransport.js` | **The playhead** — the rAF clock, shuttle (J/K/L), marks (I/O), seek and stepping. **Audio is the master clock**; a `<video>` in the monitor is a slave, which is `useMonitorVideo`, exported separately from the same file. ⚠ `scene` is derived FROM this clock, so it can never be an argument to the transport — that is why the video slaving is a second call. |
| `client/src/animatic/useUndoStack.js` | Ctrl+Z / Ctrl+Shift+Z over the WHOLE document (one stack, not one per layer), the per-gesture bracket (`gestureProps`), and `reset()` for when a project finishes loading. |
| `client/src/animatic/util.js` | `clamp`. Nothing else belongs here — scene-model arithmetic goes in `scene.js`. |
| `client/src/components/properties/` | The Properties pane, one component per selection state: `TransitionProperties`, `TextProperties`, `ShapeProperties` (serves overlays too), `AudioProperties`, `FrameProperties`, `VideoProperties`, re-exported from `index.js`. `VideoClipProperties.jsx` sits beside them but is not a pane — it is the extra rows a video clip or colour card adds to `FrameProperties`. All presentational: no state, they write through the handlers they are given. |
| `client/src/components/FrameStrip.jsx` | Frame thumbnails: typed hold time, drag-reorder, duplicate, delete, add images. |
| `client/src/components/Shapes.jsx` | The shape layer's vocabulary: the unit-square polygons (**mirrored in `animatic.py`**), the CSS for them, and the picker gallery. |
| `client/src/components/Timeline.jsx` | **As many lanes as the project has** — the editor passes ONE `lanes` list and both the gutter labels and the tracks render from it. Kinds: 🖼 sequence · 🖼 image overlay · T text · ◆ shapes · ♪ audio. Fixed label gutter, ruler pinned to the top of the scroller, playhead, and the two `ZoomScrollbar`s. Drag a frame's right edge to change its hold; drag a text clip to move it, its edge to stretch it. ⚠ The gutter is OUTSIDE the scroller, so `readView` translates it by hand — that is the only thing keeping a label beside its own track when the lanes are scrolled down. ⚠ **MORE THAN ONE THING CAN BE SELECTED**: drag a lane's empty space for a rubber band (a press that does NOT travel still scrubs), shift-click to toggle one, double-click a lane's label for the whole row — the timeline only REPORTS these, the editor owns the list. Every selectable node carries `data-sel="kind:id"`, which is what the band hit-tests. ⚠ The picture bars are placed at an absolute `left` from the running total, NOT by flow — a bar drawn wider than its time used to shove the rest of the sequence off the end. Exports `formatTime`. |
| `client/src/components/ZoomScrollbar.jsx` | The timeline's scroll bars — one component, both axes. **The ends ZOOM**: the thumb's length is the zoom and its position is the scroll, so a grip drag frames a stretch of the edit in one gesture (Premiere's bars, not the browser's). Reports a WINDOW as fractions of the whole timeline; `Timeline.jsx` turns that into pixels-per-second (horizontal) or track height (vertical). |
| `client/src/components/Waveform.jsx` | Draws the peaks on a canvas. The DECODE moved to `animatic/beats.js` and is cached there by url — the waveform, the beat markers and the duck preview all want the same samples, and three decodes of one MP3 was three chances to disagree about how long it is. |
| `client/src/animatic/audio_mix.js` | **What a track sounds like at a moment** — its tone, its fader, its fades, the duck it sits under. **⚠ TWIN of the mix half of `animatic.py`** (`trackPlayMs`/`fadeWindow`/`EQ_BANDS` ⇄ `track_play_ms`/`fade_window`/`EQ_BANDS`), compared case by case and band by band in `tests/audio_mix_check.py` by running this file under node. A fade is placed against what the track PLAYS — its trim, or the end of the video — never against the file. The EQ is three FIXED bands because each is one cookbook biquad, i.e. one `BiquadFilterNode` here and one ffmpeg filter there. Also owns **the three crossfade curves** (`FADE_CURVES` / `curveGain` ⇄ `FADE_FF_CURVE` / `curve_gain`): a fade carries a curve per END, ⚠ the formulae are ffmpeg's `fade_gain()` transcribed rather than invented, and `linear` is the default because it is `afade`'s own (`curve=tri`). Pinned by `tests/audio_crossfade_check.py`. |
| `client/src/animatic/audio_engine.js` | **The preview's mixer.** Each `<audio>` is routed `→ 3 biquads → gain → destination`, because an element gives you `volume` (capped at 1) and `muted` and nothing else — a track at 150% used to preview at 100%, and an EQ was impossible. ⚠ **The element is still the master clock**: a `MediaElementSource` plays its element, it does not replace it. The context starts suspended (resumed from the gesture that starts playback) and `createMediaElementSource` may be called once per element — **every failure falls back to `el.volume`**, the behaviour that predates this file. |
| `autoframe.py` | **Where the subject is, and therefore where to put the camera.** ⚠ THE MODEL IS ASKED FOR THE SUBJECT, NEVER FOR THE CROP: a model asked for "a 9:16 crop" returns roughly 9:16, and roughly is a reframe subtly wrong on every shot. `crop_box` then builds a box of EXACTLY the target aspect around it — provably containing it, because the clamp can only move the crop toward the subject's own side — and `frame_transform` is the INVERSE of `animatic_render.place_picture`, so change one and this moves with it. ⚠ Writes ordinary `scale`/`x`/`y`; there is no crop concept in this codebase and this is not the place to add one. `apply_to_frame` carries an existing Ken Burns push through the reframe. |
| `client/src/animatic/audio_clips.js` | **An audio track is a CLIP, and this is what you can do to one** — find the one under a click, cut it in two, trim its head, group a lane. ⚠ **EDITOR-SIDE ONLY, no Python twin and none needed**: the server renders a mix, it never edits one (the same split as `keyframes.js`). ⚠ **`clipId(track)` is the identity, NOT `upload_id`** — after a cut several clips share one upload, so the upload answers "which sound" and never "which clip"; a clip saved before the razor has no `id` and `_audio_tracks_of` backfills it with the upload. `splitClip` sets **`start_ms` AND `offset_ms`** on the second half by the same amount — one without the other and the audio jumps at the cut. Checked by `tests/audio_razor_check.py` under node. Also owns **the crossfade** (`crossfadePatch` / `crossfadeTarget`), which unlike a picture transition really does overlap its two clips — it eats the media handles either side of the cut, **spending the outgoing clip's TAIL before the incoming clip's HEAD** so that laying one moves nothing. ⚠ Everything there works in FILE time and takes no `totalMs`: `trim_ms` is written from a play length, so the video's clamp would get baked into any clip hanging past the last frame. Checked by `tests/audio_crossfade_check.py`. |
| `client/src/animatic/selection.js` | **What "the selection" is, now that more than one thing can be in it** — a LIST of `{kind, id}`, the shift-click toggle, group expansion, and the rubber band's box maths. ⚠ **EDITOR-SIDE ONLY** (a selection is not part of the project) except `group_id`, which is saved on the clips. ⚠ **A group is a shared string on its members, not a container**: nothing has to be kept in step, so it cannot go stale — delete a member and the group is what is left. `MOVABLE`/`GROUPABLE` exclude `frame` because the picture sequence is a flow, not free-floating clips. Checked by `tests/selection_check.py` under node. |
| `client/src/animatic/captions.js` | **Which clips this app WROTE, and which lane they live on** — three strings and two predicates. **⚠ TWIN of `captions.py`** (`CAPTION_LAYER_ID` / `CAPTION_LAYER_NAME` / `CAPTION_ID_PREFIX`), compared by running this file under node in `tests/captions_check.py`. The SERVER writes generated captions, so this is the whole contract that lets the browser find the lane, name it, and keep it at the top of the timeline. Get it wrong and the captions are invisible on the timeline while still burning into the export. |
| `client/src/animatic/ripple.js` | **When the board's pictures move, the REST OF THE FILM moves with them.** ⚠ EDITOR-SIDE ONLY, no Python twin (the same split as `razor.js`). `spreadPanelsForRenders` moves PICTURES; this carries the captions, the voiceover, typed text, shapes, overlays and the Video row along behind them. ⚠ **THERE IS NO SINGLE NUMBER TO MOVE THINGS BY** — shot 7 grows by 2s and shot 24 by 9s — so `renderShifts(before, after)` builds a STEP FUNCTION over OLD time (two points per panel, its start AND its end, matched by frame id) and every other clip is moved by looking its own start up in it. ⚠ **A CLIP IS NOT STRETCHED BY THE CARRY** — a caption is moved by its own start, never scaled. Growing one is a SEPARATE pass, `coverGrownShots`: a generated caption's END is extended to the end of the shot it sits in (never past the next caption, never a clip the user typed, only ever longer), because the words are still spoken when they were spoken. ⚠ **THE VOICEOVER IS CUT AT THE EDIT** — one clip laid from 0:00 owes nothing at its start (the bug) and cannot be shifted whole (a different bug), so `rippleAudio` razors it with `splitClip` and moves the tail. ⚠ **EVERY BOARD CLIP IS SKIPPED BY `rippleFrames`**, panels and takes alike: the layout pass placed them already and the map is in OLD time, so a moved take looked up at its NEW start is moved twice. Run by `attachVeoClip`, by the LOAD (so a take attached before the rule existed is put right) and by the voiceover poll. ⚠ **NO CALLER READS THE DOCUMENT** — every list is rippled through React's own functional setter, because a ref is empty at load and stale in a poll and rippling an empty list is a SILENT no-op; `RIPPLED_LISTS` names the five so none is forgotten. Checked by `tests/timeline_ripple_check.py` under node. |
| `client/src/animatic/beat_cut.js` | **Pulling every cut onto the nearest beat.** ⚠ EDITOR-SIDE ONLY, no Python twin (the same split as `selection.js`). Three rules, each a check in `tests/autoframe_check.py` under node: **a cut is not a thing you can move** — the sequence is a FLOW, so moving one rewrites the durations either side; **beats cluster and cuts must not** — the nearest beat to two consecutive cuts is often the same one, and without the running floor that is a zero-length clip, a picture that never appears; and a cut further than `REACH_MS` from a beat is LEFT ALONE, or this tightens nothing and rewrites the edit. The last cut is never moved — it is the end of the video, not an edit point. |
| `client/src/components/RegeneratePanelInline.jsx` | **Redraw the shot you are looking at, and run it longer** — the two Properties groups that reach back to the BOARD (`RelengthShotInline` is the second, exported from here). Renders NOTHING unless the clip's picture is a board panel, so an animatic of uploaded stills is unchanged. Follows the 2026-08-09 three rules: it really redraws (no resume flag lives here), the server's answer carries a new `?v=` that `onRedrawn` hands to the editor, and `.is-redrawing` + `.redraw-veil` blur the OLD picture so you can see which one is being replaced. ⚠ Says out loud that the panel is SHARED — the redraw changes every animatic built from that board. |
| `client/src/animatic/beats.js` | **Where the beats are, and the one decode everything reads from.** Energy-envelope onset detection: no library, no FFT, no server round-trip. The pure half (`energyEnvelope`, `onsetsFromEnvelope`) takes plain arrays and touches no browser API, which is what lets the test run it under node against a click track at a known BPM. Beat times are in FILE time, like `offset_ms`. |
| `client/src/animatic/useAudioAnalysis.js` | The React end of that cache: upload_id → the decoded analysis, for the timeline's markers and the transport's duck. Deliberately NOT part of `useTimelineTransport` — the transport owns the clock and an analysis arriving late must not be able to restart it. |
| `client/src/components/PanelSequenceStrip.jsx` | One shot's KEY POSES under its panel: the duration dialog, the thumbnail strip, per-pose ↻ redraw, the lightbox, Stop/resume/clear. **Regenerate sends `resume=false` (redraw); "Draw the remaining N" sends `resume=true`** — they cost different amounts, don't merge them. Knows a redraw has landed by the frame URL's `?v=<mtime>` changing, and blurs the poses being replaced under `.redraw-veil` until then. |
| `client/src/components/PanelVersions.jsx` | The "‹ 2 / 3 ›" pill on a panel: every redraw is archived, so you can step back to the version you preferred. Renders nothing until a shot has been redrawn once. |
| `client/src/components/DialogueBox.jsx` | A shot's spoken lines, read-only (board tiles). Renders **nothing** when the shot is silent. |
| `client/src/components/DialogueEditor.jsx` | The same lines, editable, on the review step. A silent shot shows only a "＋ Add dialogue" link. |
| `client/src/styles/` | Dark + champagne-gold theme, in 24 files pulled in by `styles/index.css` (which `main.jsx` imports). **⚠ The import ORDER in `index.css` is the cascade** — it is the order the one big `styles.css` was written in, and moving an import can change the page without changing a declaration. `theme.css` must stay first; add new files at the end or beside their block. |

### API endpoints
- `POST /auth/register` · `POST /auth/login` · `GET /auth/me` · `DELETE /auth/me` (delete account)
- `GET/PUT /auth/me/api-keys` · `DELETE /auth/me/api-keys/{provider}` — saved 3D keys (plaintext)
- `POST /storyboards/breakdown` — Script→Storyboard Stage A: script → shot list (auth'd, sync; `TEXT_PROVIDER` backend)
- `POST /storyboards` — Stage D: generate panels from reviewed shots (async job; poll `GET /jobs/{id}`) · `GET /storyboards/{id}/panel/{index}` — serve a panel PNG · `GET /storyboards/{id}/pdf` — Stage F: export the board as PDF
- **Board editing:** `POST /storyboards/{id}/panels/insert` (`{at, description}`) — add a blank panel, shifting the rest down · `DELETE /storyboards/{id}/panels/{index}` — remove a panel, shifting up. Both renumber files+indices+urls across ALL style variants so `index == position` stays true; the new panel is drawn with the normal `regenerate-panel` call.
- **Key poses (Image to Animatic Image):** `POST /storyboards/{id}/panels/{index}/sequence` — block one shot's motion out as key drawings (body: `duration_seconds` 2/4/6/8/10, `resume`). Async on the BOARD job; stop with `/storyboards/{id}/stop`, and calling it again RESUMES from the frames on disk. `GET` the same path for what exists, `DELETE` it to start over, `GET …/frames/{n}` serves one PNG, `GET …/frames.zip` serves the lot as `pose_001.png…` in play order. Count is derived from the duration (4 per second), never sent by the client
- **Board copies:** `POST /storyboards/{id}/copy?workflow=…` — deep-copy a board (new record + its own panel FILES, urls re-pointed, share token dropped). This is how Image to Animatic Image takes a board in, so editing it can never change the original. Refused while the source is RUNNING or a DRAFT.
- **Library (Stage G):** `GET /storyboards?workflow=` — the caller's saved boards (lean summaries: title, genre, aspect, cover). `workflow` scopes them: unset = Script to Storyboard's originals, `animatic-image` = the copies, `*` = all (what the animatic/video pickers use) · `GET /storyboards/{id}/project` — saved shots+settings for Duplicate · `PATCH /storyboards/{id}` — rename · `DELETE /storyboards/{id}` — delete record + panel files
- **Share links:** `POST/DELETE /storyboards/{id}/share` — mint / revoke a public token · `GET /public/storyboards/{token}` · `GET /public/storyboards/{token}/panel/{index}` — **the only unauthenticated routes**; token-gated, serve drawn panels only
- `POST /characters/reference` — generate T-pose reference from text (surfaces the REAL error via `ReferenceGenerationError`)
- `GET /characters/reference/{id}/image` — serve generated reference for preview
- `POST /characters` — upload image (or `reference_id`) + options → `job_id` (async)
- `GET /jobs?kind=generate,meshy` · `GET /jobs/{id}` — list / poll (owner-scoped). `kind` is a comma-separated `JobKind` filter that keeps the Text-to-Image list free of storyboards; omit it for all kinds. Job carries live `progress`.
- `GET /jobs/{id}/assets` — PNG URLs per part/view. Also serves PARTIAL assets while `running`.
- `GET /jobs/{id}/image/{part}/{view}` — serve a single asset PNG (enables local-run previews)
- `GET /jobs/{id}/download` — full zip · `GET /jobs/{id}/download/{part}` — per-section zip
- `POST /jobs/{id}/stop` — stop a running job (any kind): finishes the work in flight, skips the rest, keeps what was generated. `POST /storyboards/{id}/stop` is the same helper for a board.
- `POST /jobs/{id}/regenerate-part` — redo one part · `POST /jobs/{id}/regenerate-view` — redo one view
- `POST /jobs/{id}/meshy` — submit part(s) for 3D; body accepts `provider` (`meshy`|`tripo`) + optional `api_key` (falls back to saved key)
- **Storyboard → Animatic (`server/animatics.py`, kind `animatic`):**
  `POST /animatics` — new project; with `source_storyboard_id` and no frames it fills the sequence from that board's DRAWN panels (the board's "🎬 Make animatic") · `GET /animatics` — library · `GET/PUT /animatics/{id}` — read / save the project: `frames`, `texts`, `shapes`, `layers` (the lanes), `overlays` (pictures composited over the video), `audio`, `settings` (PUT is the editor's autosave, every field optional; 409 while exporting) · `DELETE /animatics/{id}`
  `POST /animatics/{id}/images` (multi-file) · `POST /animatics/{id}/audio` — uploads; images are stored but NOT sequenced (the client picks the order) · `GET /animatics/{id}/frame/{frame_id}` — ONE url shape for both source kinds · `GET /animatics/{id}/media/{upload_id}` — a just-uploaded image, before it's saved · `GET /animatics/{id}/audio`
  `POST /animatics/{id}/videos` (multi-file) — upload video clips; each item comes back with a `duration_ms` **measured server-side by ffmpeg** (there is no ffprobe), because the exporter must work from the same number
  `POST /animatics/{id}/animate/estimate` — **free**; what animating these frames would cost. Takes the SAME body as the render below, so the price quoted is the price of what the button does · `POST /animatics/{id}/animate` — **SPENDS MONEY.** 202, renders off-request on the video pool (poll `GET /jobs/{id}`). Refuses promptless frames, skips already-rendered ones unless `force`, caps at `API_MAX_VIDEO_BATCH`. The finished clip lands as an ordinary video upload, so it is indistinguishable from a dropped file thereafter. **Render records live in the job's `result` (`veo_clips`), never `params`** — the autosave rewrites `params` wholesale and would otherwise erase a clip that was paid for
  **Back to the board (Phase 7):** `GET/POST /animatics/{id}/frames/{frame_id}/panel` — the wording behind one clip / re-draw it. Synchronous (one image) and it answers with the **FRAME**, whose `url` carries a fresh `?v=<mtime>` — that is what the client re-fetches against · `GET/POST /animatics/{id}/frames/{frame_id}/sequence` — that shot's key poses / re-block it at a new length ("make this shot 2s longer"). ⚠ **The job returned is the STORYBOARD's**, because the drawings belong to the board — which is also why this animatic stays fully editable while it runs. It RESUMES, so 4s → 6s buys eight drawings, not twenty-four
  **Captions & voiceover:** `POST /animatics/{id}/captions/estimate` — **free** · `POST /animatics/{id}/captions` — **SPENDS QUOTA.** 202, transcribes ONE audio track into caption clips on a lane of their own · `GET /animatics/{id}/dialogue` — **free, and it calls no model**: the dialogue sheet the 🎙 dialog opens on — every spoken line, the shot it belongs to, its speaker, a **persona** guessed from the board's cast, and both pickers (the voice list lives in `tts.CAST`, never in the JSX) · `POST /animatics/{id}/voiceover/estimate` — **free**, and priced from the EDITED sheet in the body, so the quote is the price of the words on screen · `POST /animatics/{id}/voiceover` — **SPENDS QUOTA.** 202, one call per line. ⚠ **IT MOVES PICTURES**: with `fit_shots` (the default) the shot that owns a line is stretched to cover it and the shots after it are pushed clear, so the client must re-read `frames` as well as `texts` and `audio_tracks` when it finishes
  `POST /animatics/{id}/reframe/estimate` — **free** · `POST /animatics/{id}/reframe` — **SPENDS QUOTA.** 202, one vision call per shot on the video pool. Writes `scale`/`x`/`y` onto the frames server-side, so the client re-reads the project when it finishes. Back to QUEUED never FAILED, like the other two AI passes
  `POST /animatics/{id}/export` — 202, encodes off-request (poll `GET /jobs/{id}`) · `POST /animatics/{id}/stop` · `GET /animatics/{id}/video`
- **Animatics → Final Video (`server/videos.py`, kind `final_video`):**
  `POST /final-videos` — new project; with `source_animatic_id` and no shots it fills the shot list from that animatic's frames (`FinalVideoLibrary`'s "new from animatic"; the animatic editor's own shortcut to this was removed 2026-08-20); `source_storyboard_id` does the same from drawn panels · `GET /final-videos` — library · `GET/PUT /final-videos/{id}` — read / save (`shots`, `art`, `settings`, `title`; PUT is the workspace autosave, 409 while busy) · `DELETE /final-videos/{id}`
  `GET /final-videos/backend` — is Veo reachable? Checked before the first paid call so a missing key is a banner, not a two-minute wait for a failure
  `POST /final-videos/{id}/art` (multi-file) — upload stills into the art tray · `GET /final-videos/{id}/art/{ref_id}` — serve a reference (upload, board panel, or one view of a Text-to-Image character run) · `GET /final-videos/{id}/media/{upload_id}` — a just-uploaded still, before the project is saved
  `POST /final-videos/{id}/estimate` — **free**; what a render request would cost. The client calls this to fill the confirm dialog, so the price is on screen before the button that spends it
  `POST /final-videos/{id}/render` — **SPENDS MONEY.** 202, renders off-request (poll `GET /jobs/{id}`). Empty `shot_ids` = every included shot without a clip. Refuses prompt-less shots, skips already-rendered ones unless `force`, caps at `API_MAX_VIDEO_BATCH`
  `GET /final-videos/{id}/shot/{shot_id}/image` — the source still · `GET /final-videos/{id}/shot/{shot_id}/clip` — that shot's rendered MP4
  `POST /final-videos/{id}/assemble` — **free**; 202, joins the clips (poll `GET /jobs/{id}`) · `POST /final-videos/{id}/stop` — stops a render batch or an assembly, keeping every clip already paid for · `GET /final-videos/{id}/video` — the final cut
- `GET /templates` · `GET /health` (also reports `ffmpeg`)

---

## ▶️ How to run

### CLI
```powershell
# Full run with uploaded image (female subject template), skip some parts:
python run_character.py --name kamla --image ./kamla.jpg --template human_female --skip goggles,headphone

# Generate reference from text prompt (Step 0) then run pipeline:
python run_character.py --name kamla --prompt "Indian woman in red saree, age 30" --template human_female

# Cheap test — one part, no GCS:
python run_character.py --name kamla --image ./kamla.jpg --parts hair --local-only

# Choose image backend explicitly:
python run_character.py --name kamla --image ./kamla.jpg --parts hair --local-only --provider gemini

# Verify backends work (keys + model names) before a full run:
python smoke_test_providers.py                 # both backends
python smoke_test_providers.py --provider gemini --save
```

### Server
```powershell
python -m uvicorn server.main:app --reload
# Swagger UI: http://127.0.0.1:8000/docs
```
Auth flow: `POST /auth/register` → copy `access_token` → click **Authorize** in
Swagger → all protected endpoints work.

Bootstrap the first user from the CLI (needs MongoDB running):
```powershell
python seed_admin.py --email admin@example.com          # prompts for password
python seed_admin.py --email admin@example.com --update-password   # reset password
```
Check dependencies: `GET /health` reports MongoDB connectivity (status flips to
`degraded` if Mongo is down; `?check_db=false` skips the ping).

Requirements: `pip install -r requirements.txt`

### Client (Phase 3)
```powershell
cd client
npm install        # first time only
npm run dev        # dev server at http://localhost:5173
npm run build      # production build to client/dist/
```
Point it at a non-default API host by copying `.env.example` → `.env.local` and
setting `VITE_API_BASE`. The backend must be running (see API section). Gallery
previews require a cloud run (not `local_only`).

---

## 🔐 Environment variables (see `.env.example`)

| Var | Purpose |
|-----|---------|
| `IMAGE_PROVIDER` | `vertex` (default) or `gemini`. Which image backend. |
| `TEXT_PROVIDER` | `vertex` (default) or `gemini`. Which text/LLM backend (script breakdown). Independent of `IMAGE_PROVIDER`. |
| `VERTEX_TEXT_MODEL` / `GEMINI_TEXT_MODEL` | Optional text-model overrides (default `gemini-2.5-flash`). |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | Vertex AI (location MUST be `global`). |
| `GEMINI_API_KEY` | Required when `IMAGE_PROVIDER=gemini`. |
| `VERTEX_IMAGE_MODEL` / `GEMINI_IMAGE_MODEL` | Optional model overrides (default `gemini-3.1-flash-image`). |
| `VIDEO_PROVIDER` | `vertex` (default) or `gemini`. Which **Veo** backend (Animatics → Final Video). Independent of the image/text switches. **Renders are billed per second of output.** |
| `GOOGLE_CLOUD_VIDEO_LOCATION` | Vertex region for Veo (default `us-central1`). NOT `global` — that serves the image models, not Veo. |
| `VERTEX_VIDEO_MODEL` / `GEMINI_VIDEO_MODEL` | Pin an exact Veo model. Unset picks by the project's quality tier (lite/fast/standard). |
| `VIDEO_MAX_CONCURRENCY` / `VIDEO_POLL_TIMEOUT` / `VIDEO_POLL_INTERVAL` | In-flight renders (default 2 — Veo's quota is tight), give-up seconds (900), poll gap (10). |
| `API_MAX_VIDEO_SHOTS` / `API_MAX_VIDEO_BATCH` / `API_MAX_VIDEO_WORKERS` | Spend guards: shots per project (60), shots one "Render all" may submit (12), parallel render jobs on their own pool (2). These bound **money**, not just work. |
| `MESHY_API_KEY` | Meshy 3D generation. |
| `JWT_SECRET` | **Required in prod.** JWT signing key. Dev fallback warns loudly. |
| `JWT_ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT config (defaults HS256 / 1440). |
| `MONGODB_URI` / `MONGODB_DB` | User account store (default `mongodb://localhost:27017`). |
| `API_JOB_STORE` | `firestore` (default) or `memory` (local dev). |
| `API_MAX_WORKERS` | Concurrent pipeline jobs (default 2). |
| `FFMPEG_BINARY` | Optional path to your own ffmpeg. Unset → PATH → the `imageio-ffmpeg` bundled binary. |
| `API_MAX_AUDIO_BYTES` | Animatic audio upload cap (default 50 MB). |
| `API_MAX_ANIMATIC_FRAMES` | Frames per animatic (default 500). |
| `API_MAX_ANIMATIC_TEXTS` | Text clips per animatic (default 400). Each clip boundary splits the timeline into another rendered still, so this also caps export work. |
| `API_MAX_ANIMATIC_SHAPES` | Shapes per animatic (default 400). Same reasoning as the text cap — every shape boundary is another cut and another still. Also caps overlay pictures. |
| `API_MAX_ANIMATIC_LAYERS` | Lanes on the timeline (default 24). This is a rough cut, not a compositing suite. |
| `API_MAX_ANIMATIC_AUDIO_TRACKS` | Audio tracks per animatic (default 4). Each is another ffmpeg input to decode and mix. |
| `ANIMATIC_EXPORT_WORKERS` | How many processes draw the export's stills. Unset = decided per export (`cpu_count-1`, capped at 8, and **serial below 48 distinct stills** — a pool started for a dozen is slower than the loop). `1` forces the old serial loop; the parity half of `tests/export_perf_check.py` sets it. |
| `ANIMATIC_PROXY_EDGE` | Largest preview proxy this install will make, as a long edge (default 1440, ladder 480/960/1440). `0` serves every picture at full size — the whole feature off, as one variable rather than a code path. Never affects the export. |
| — | Export resolution / quality / include-audio / **container** are per-project **settings**, not env vars: `AnimaticSettings.resolution` (short edge), `.quality` (CRF), `.include_audio`, `.container` (mp4/gif/png) + `.preset` + `.still_ms`. |

---

## 🖼️ Image backend switching (Vertex AI ⇄ Gemini API)

Resolution order: **explicit arg → `IMAGE_PROVIDER` env → `vertex`**.
A separate client is cached per provider, so both can be used in one process.

- **Global default:** set `IMAGE_PROVIDER` in `.env`.
- **Per CLI run:** `--provider vertex|gemini`.
- **Per API job:** `provider` form field on `POST /characters` (validated; stored on job).

If the Gemini Developer API rejects the default model name, set `GEMINI_IMAGE_MODEL`
in `.env` — no code change needed.

---

## 🧪 Browser tests (Playwright)

There are TWO, and they answer different questions. `tests/e2e_animatic.py` is the
whole editor against a live API and needs three terminals; **`tests/monitor_effects_check.py`
is the Program monitor only, starts Vite itself, needs no backend, and takes about
a minute** — so it is the one to run after touching anything under
`client/src/animatic/gl/` or `ProgramCanvas.jsx`. It exists because a black
monitor is a CRASH before it is a rendering bug: it mounts `<ProgramCanvas>`,
turns each effect on the way the Effects pane does, and asserts the canvas is
still in the document, that nothing reached `window.onerror`, and that **the GL
context was built once rather than rebuilt per render**. The maths tests
(`effects_check.py`, `effects_parity_check.py`) never unmount anything and passed
right through the bug it was written for — see the top Work Log entry.

`tests/e2e_animatic.py` drives a real Chromium against a live API + Vite on
isolated ports. It has caught bugs a clean `npm run build` happily shipped
(mis-aligned timeline labels, a waveform that never drew, a preview that wasn't
the exported frame shape, dead space under the workspace).

**Status: ALL 14 SECTIONS PASS** (2026-08-16). It had been dying at section 5
for several sessions — see the Work Log — so if you are comparing against an
older note, the numbers moved.

**⚠ WHEN AN ASSERTION HERE FAILS, FIND OUT WHICH SIDE IS WRONG BEFORE TOUCHING
EITHER.** Of the four that were failing, three were stale test and one was a
misreading of the app; none was a bug in the app. **Never edit an assertion just
to make it green** — write down which it was and why, as the comments in the
file now do.

**Two rules this test has to keep obeying, both learned the hard way:**
1. **Never name timeline tracks by hand.** The timeline has as many lanes as the
   project has. Walk `.tl-lane` / `.tl-gutter-row` in DOM order; a hardcoded
   list of three is what made it `IndexError` and take sections 6-14 with it.
2. **Anything in the Properties pane needs the thing SELECTED first.** The pane
   follows the selection (`selectOnly`), so asserting a control is present
   without clicking is asserting that the pane shows a control for something
   nobody picked — the exact thing that design prevents.

**Run it when the user ASKS for a browser test — not after every change.** It
takes minutes (two servers, five viewports) and the user has said plainly they
don't want it on every tweak. Do keep it *up to date* when behaviour it asserts
changes, so it still passes when it is run. Setup and the three commands are in
the test's docstring; `pip install -r requirements-dev.txt` first.

Two Windows gotchas that will waste your time:
- `pkill -f uvicorn` does **not** kill a Windows python process — the stale
  server keeps the port and you end up testing old code. Use
  `Get-NetTCPConnection -LocalPort 8124 | Stop-Process -Id $_.OwningProcess`.
- Add `sys.stdout.reconfigure(encoding='utf-8')` to anything that prints arrows
  or emoji; the console is cp1252 and will crash the run mid-way.

## 📱 Responsive rules (apply to EVERY page)

Standing conventions, not a one-off fix. Check these before adding a screen.

1. **Never use bare `100vh` for a full-height surface.** Write the `vh` line and
   a `dvh` line under it:
   ```css
   min-height: 100vh;   /* fallback */
   min-height: 100dvh;  /* follows the real viewport as mobile chrome slides away */
   ```
   Every full-height rule in `styles.css` is paired this way (`body`,
   `.auth-wrap`, `.landing`, `.hero`, `.sb-form`, `.public-wrap`, `.shell`,
   `.sidebar`, the editor).
2. **No fixed `width`/`min-width` ≥ 320px on anything that must fit a phone.**
   Use `clamp()`, `minmax(0, 1fr)` or `min(100%, …)`. Audited: the stylesheet
   currently has none.
3. **`min-width: 0` / `min-height: 0` on every grid and flex child** that holds
   text or a scroller — without it a child refuses to shrink below its content
   and pushes the page sideways.
4. **Multi-column layouts get a middle stage**, not just "3 columns → 1". The
   editor narrows its side panes at 1400px before stacking at 1180px.
5. **Consider the SHORT screen too**, not only the narrow one. A viewport-height
   layout needs a `@media (max-height: …)` escape or it becomes unusable slivers
   with devtools open (the editor releases at 620px tall).
6. **Wide content scrolls inside its own container** (`overflow-x: auto`), never
   the page body.

## 🗄️ Storage rule — READ BEFORE ADDING A WORKFLOW

**MongoDB is the system of record for everything the app produces, except the
binary files themselves.**

| What | Where | How |
|------|-------|-----|
| Accounts | Mongo `users` | `server/users.py` |
| Plan & Script sessions (chat + calendar) | Mongo `jobs` (`JobKind.PLAN`) | `server/plans.py` |
| The script being typed | Mongo `script_drafts` | `server/drafts.py` |
| **Every job: character runs, storyboards, animatics, and anything added later** | **Mongo `jobs`** | **`server/jobs.py` → `get_store()`** |
| Image / video BYTES | disk (`output/`, `uploads/`) — GCS when enabled | `storage.py` |
| **URLs of those files** | **Mongo, inside the job's `result`** | written by the pipeline |

**Adding a workflow to the sidebar?** You do not write storage code. Add a value
to `JobKind`, call `get_store().create(..., kind=JobKind.YOURS)`, and update it
through the same store. It is then persisted, owner-scoped, listable and
share-linkable for free. **Never** invent a per-workflow storage path, and never
write app state to a JSON file next to the code.

When GCS is switched on, `storage.save_character_assets` returns public GCS URLs
and the pipeline writes them into `result` — so the URLs land in Mongo with no
extra plumbing. Verified by a test in `tests/mongo_job_store_check.py`.

All three stores share ONE MongoClient via `server/mongo.py` — do not open your
own.

---

## 🎨 UI rule — REUSE THE EXISTING LAYOUT FOR A NEW WORKFLOW

**Two traps that have already been hit, both reported by the user:**

0. **`align-items: start` on a card grid makes ragged rows.** It has caused a
   reported bug THREE times (storyboard library, Home dashboard, plan calendar).
   Use `stretch`, give the card `height: 100%` (or a fixed height), let the body
   take the slack with `flex: 1`, and push the footer down with
   `margin-top: auto`. If a card's content varies a lot, fix the height and
   scroll the body rather than letting one long card stretch its whole row.
1. **`.btn.primary` carries a global `margin-top: 1.1rem`** (it is normally the
   last control in a form). Put one in a ROW and the gold button sits lower than
   its neighbours and reads as a different size. EVERY button row in the app
   resets it — `.top-actions`, `.review-actions-right`, `.board-toolbar`,
   `.account-modal`, and now the Plan & Script rows. **A new row of buttons must
   reset it too**, and should set `display:inline-flex; align-items:center;
   min-height` so a ghost / icon-bearing button matches a solid one.
2. **`min-height` is not enough to make buttons match.** A gold `.btn.primary`
   carries its own glow shadow and heavier weight, so beside a ghost button it
   still reads as bigger. Give every button in a row an IDENTICAL box:
   `height` (not min-height), `padding-top/bottom: 0`, `box-sizing: border-box`.
3. **A scrolling element must not also be width-capped.** `max-width` on the
   scroller parks its scrollbar at that width — i.e. in the MIDDLE of a wider
   panel. The scroller goes full width; cap the line length on a page element
   INSIDE it (`.export-doc` / `.export-doc-page`).
4. **Never nest two scrolling containers.** A scrollable panel holding a
   scrollable table means the inner one grows to the full content height, so its
   horizontal scrollbar sits at the bottom of the CONTENT — off-screen until you
   scroll all the way down. Exactly one element in the chain gets
   `overflow:auto`; its ancestor gets `overflow:hidden` + `display:flex` +
   `min-height:0`, and the scroller gets `flex:1; min-height:0`. Then the bar is
   pinned to the visible bottom and stays put.


**User's instruction, after a workflow shipped with its own bespoke gallery:**
*"when I create new workflow so you keep in mind first you use my UI layout, so
simple for user understanding."*

Every workflow looks the same because it uses the same parts. Before writing any
new screen, copy the structure from `StoryboardLibrary.jsx` — do not invent one.

**A workflow library is always:**

1. `workflow-head-wrap sb-library` → `workflow-header` (icon + `wf-title` + `muted` subtitle)
2. `lib-grid lib-new-row` → one `card lib-new` tile: `lib-new-plus` (+),
   `lib-new-title` ("New X"), and a `tiny muted` count line
3. **`Recent X`** section — the newest `RECENT_COUNT` (1)
4. **`All X`** section — everything, with a `N in total` hint

**Each section** (`renderSection`) is `lib-section` → `lib-section-head`
(`lib-section-title` + `tiny muted` hint) → one of: shimmering `lib-ghost` cards
while loading, a single `lib-ghost-empty` card with `lib-empty-ico` +
`lib-empty-text` when empty, or `lib-grid` of cards.

**Each card** is `card lib-card` → `lib-cover` (thumbnail or `lib-cover-empty`
emoji, optional `lib-badge`) → `lib-body` (`lib-title`, `lib-meta` with `chip`s,
`lib-foot` with a date and `lib-icon` action buttons using `Icon`).

`renderSection` must be a render FUNCTION, not a nested component — a component
declared inside the parent gets a new identity every render and remounts the
section on each keystroke, which makes an inline rename field lose focus.

If a new class name starting with `lib-` is needed, the layout is probably being
reinvented. Plan & Script reuses **27** of these and invents **0**.

---

## 📐 Conventions

- **Python 3.14**, standard library logging (`logging.getLogger(__name__)`).
- Modules start with a docstring explaining their role. Match the existing
  comment density and style.
- Pipeline functions are **synchronous** and I/O-bound; the API runs them in a
  thread pool — do not block the FastAPI event loop.
- Secrets come from `.env` only (never commit it; `.env.example` documents keys).
- Jobs are **owner-scoped**: a user only ever sees their own jobs (others → 404).
- Prefer env-driven config over hardcoding; add new settings to `api/config.py`
  and document them in `.env.example` and this file.
- Temporary/generated dirs (`output/`, `uploads/`) are git-ignored.

---

## ✅ Work Log (newest first)

### 2026-08-21 (latest) — A RIPPLE GRIP PER CUT ADDED UP TO A GOLD BAR, AND THE TIMELINE PANE IS BLUE ALL THE WAY THROUGH (user-reported, with three screenshots)

> "i want ripple icon size in clip view in timline i want you keep little thin
>  and change color blue of my timeline panel see color image 3 for ref i want
>  same color but ltiile dark"

Two visual asks, CSS only. No JS touched, no behaviour changed.

#### The trim grip was as wide as its hit area

`.tl-handle` is 8px because that is what can be grabbed without precision aiming,
and `.tl-inner.tool-ripple .tl-handle` filled the whole of it with `--primary` to
say "this is what the pointer is for". ⚠ **ON THE PICTURE ROWS THAT ADDS UP.**
Every clip carries a grip at each end, so butted-up shots put 16px of solid gold
on every cut — the screenshot is a Story…Image lane where the gold is the thing
you see and the clip labels and content tints are what you have to hunt for.

- **A 2px inset shadow instead of a fill** (`--tl-grip-mark`, 3px on hover),
  drawn hard against the grip's outer edge so it sits ON the cut it trims.
- ⚠ **THE 8px BOX IS UNTOUCHED.** An inset shadow paints inside it without
  resizing it; narrowing `width` would have made the grip harder to grab, which
  is a worse bug than a fat marker.
- The mirrored rule for `.tl-handle-l` must stay AFTER the general one — a head
  grip carries both classes, so the two selectors weigh the same and source
  order is what decides which shadow lands.

#### The timeline pane is blue, and that means every surface in it

The four pane heads share one soft blue (`--pane-*`); the ask was for the
TIMELINE pane itself to carry the colour, a shade darker than the swatch sent.

- ⚠ **NOT `background` ON THE PANE.** Everything inside the timeline paints
  itself out of `--panel` / `--panel-2` / `--border` — head strip, ruler, lane
  heads, tracks, audio clips, toolbar buttons. Colouring the pane alone leaves
  all of those grey ON the blue, which is the one result that looks broken
  rather than restyled. `.an-pane-timeline` **re-points those three tokens** at
  `--tl-panel` / `--tl-panel-2` / `--tl-border`, so the pane hands the new
  values down to every rule that already asked for them and nothing outside it
  sees them.
- ⚠ **DARKER, WHICH IS ALSO WHY IT WORKS.** The clip colours are ALPHAS
  (`--clip-*-tint`); a lighter navy washes them out. `--tl-panel` is what a lane
  sits on and `--tl-panel-2` is the lane — keep the two steps apart or the
  timeline is one flat blue slab with no rows in it.
- ⚠ **LIGHT THEME IS NOT THE NAVY.** A near-black timeline under three white
  panes is a hole in the page, so light mode gets the same hue as a blue-tinted
  paper, one step down from `--panel`.

#### Follow-up the same day: it was thin under B and fat under V

Reported with two screenshots: the ripple mark was thin with the tool armed, and
"older size" when hovering a grip in Selection. ⚠ **THE FAT GRIP WAS NEVER THE
TOOL RULE — IT WAS `.tl-handle:hover` IN `animatic.css`**, `background:
var(--primary)` across all 8px, under every tool. The first pass only thinned the
tool-armed rest state and then added its OWN hover rule beside it, which is
precisely how the two drifted apart. Now: **`--tl-grip-mark` is declared once on
`.tl-handle`** (2px) and the base hover re-declares it (3px) and paints the same
inset stripe, so ONE mark serves every tool. ⚠ **THE TOOL-SCOPED HOVER RULE IS
GONE ON PURPOSE** — do not add it back; a second hover rule for two tools is the
bug. The blue was left exactly as it is ("not need to chnage now blue okay").

Verified: `npm run build` passes (163 kB CSS, no warnings from these files).
⚠ **NOT LOOKED AT IN A REAL BROWSER** — a colour and a stripe width both want a
glance before this is called finished.

### 2026-08-21 — OPENING A PROJECT WAS SLOW, AND THE SPINNER STILL SAID "ANIMATIC" (user-reported, with a screenshot)

> "see when open project so take time so you chcek and how i fix it give me idea
>  so user open quickly … and see in image Opening your Animatics show but i
>  chnage name animatics to Project"

Two things in one report. The screenshot is the full-screen loading card reading
**"Opening your animatic…"** — a string the rename had missed — and the
complaint is that the card is up too long.

#### Where the time actually went

The spinner blocks on ONE request (`GET /animatics/{id}`), so the fix was never
"make the editor render sooner". It was that that request, and every one of the
hundred-odd media requests behind it, was paying for the same things over again.

- ⚠ **`get_current_user` did a remote Atlas `find_one` PER REQUEST.** This was by
  far the biggest cost. A sixty-panel project is one project fetch, sixty
  thumbnails and a blob per clip and track — every one of them a round trip to
  Mongo Atlas to re-learn the same unchanged fact. **`server/auth.py` now caches
  the resolved user for 30 s, keyed on the raw bearer token.** SUCCESSES ONLY, so
  a bad or expired token is re-validated every time and can never come out of the
  cache; `forget_cached_email` is called from `DELETE /auth/me` so a deleted
  account's tokens stop working immediately rather than at the end of the TTL.
  The TTL is the staleness budget for exactly one fact, `disabled`.
- **`_asset_url` was a live N+1.** It asked `_frame_version` for a stamp with
  `boards=None`, so a panel-backed library card re-fetched the board record the
  frame loop had already fetched — once per card. `_project_of` now passes its
  own `boards` dict through.
- **`get_frame_image` parsed every frame to serve one.** Sixty thumbnail requests
  meant sixty full Pydantic parses of sixty frames. **`_frame_by_id`** scans the
  raw rows and validates only the match, falling back to `_frames_of` when the
  scan finds nothing.
- ⚠ **A CACHE HIT STILL READ THE WHOLE FILE.** `video_frames.content_hash` is the
  extraction cache's KEY and it is a sha1 of the bytes, so it ran on the way in
  even when the stills were already on disk — three 80 MB clips read half a
  gigabyte to answer questions already answered. **Memoised on
  `(abspath, mtime_ns, size)`** — the same stat triple `proxies.cache_key` uses.
  The KEY STILL MEANS WHAT IT MEANT: same bytes, same digest, so the dedupe of
  one clip across two projects is untouched, and a file rewritten in place
  changes mtime and re-hashes.
- **No compression anywhere.** `server/main.py` had CORS and nothing else, and the
  project JSON — every field of every frame, defaults included — went out raw.
  ⚠ **NOT A BARE `GZipMiddleware`**: that also compresses every `FileResponse`,
  i.e. PNG panels and whole MP4s, spending real CPU to save nothing and delaying
  the first byte of a file the browser wants to play. **`GZipJSONOnlyMiddleware`**
  hands `/frame/`, `/panel/`, `/media/`, `/video`, `/download` and `/image`
  straight through and gzips the rest.
- **Nothing survived a reload.** `FileResponse` sent no `Cache-Control`.
  ⚠ **ONLY WHERE THE URL CANNOT CHANGE MEANING** — `/media/{upload_id}` (every
  upload id is a fresh uuid, so the bytes are written once) and the picture
  routes WHEN `?v=` was actually sent. `v` is now a declared param on both for
  that reason. Without a stamp there is nothing to invalidate, and a redrawn
  panel served from a week-old cache is the exact bug `_frame_version` exists to
  prevent, so the header is withheld in that case. `/audio` stays uncacheable.
- **Video, audio and overlay blobs downloaded ONE AT A TIME.** Three 80 MB clips
  went back to back before the last could be scrubbed. **`runPooled`** — a
  sliding window, not `Promise.all` over fixed batches, so one slow file does not
  stall the rest — at 2 for video, 3 for audio, 5 for overlays. Two, not five,
  for video: the old comment's point about five parallel 100 MB fetches being a
  worse first impression still stands.

**Deliberately not done** (agreed with the user, quick wins only): Range
streaming for `<video>` (it needs a non-header auth path), `React.lazy` on the
editor bundle, and reworking the 5-at-a-time thumbnail batching into a window.

#### The rename

`Opening your animatic…` → `Opening your project…`, plus every other
user-visible string: ~22 more in `AnimaticEditor.jsx` (back-button tooltips,
`Save project as…`, the export placeholder and default filename, the delete
confirm, the row/track limit toasts), 8 in `AnimaticLibrary.jsx` (`Your
Projects`, `Recent Projects`, `All Projects`), and ~40 server `detail=` strings
and job titles in `server/animatics.py`, `server/videos.py`, `animatic.py` and
`export_presets.py`. Article changes were done by hand (`an animatic` → `a
project`).

- ⚠ **`LEGACY_UNTITLED = ["Untitled animatic"]` IN `AnimaticLibrary.jsx:30` IS NOT
  DISPLAY TEXT** and was left alone. It is compared against titles already in the
  database; renaming it breaks `isUntitled()` for every pre-rename project and
  permanently suppresses the "Save as…" prompt.
- Identifiers, routes, JSON keys, CSS classes, filenames, log lines and comments
  were NOT touched. `Image to Animatic Image` is a separate feature name and was
  left alone, confirmed with the user.
- ⚠ The server title fallbacks (`character_name or "Project"`) only affect NEW
  projects. Rows already in the database keep the titles they were given.

#### Verified

`client` builds. All 35 non-browser `tests/*_check.py` pass. Two failures are not
this work: `effects_parity_check.py` needs a native GL module that is not
installed, and `profile_check.py` asserts the shared users collection holds
exactly one account when it holds ~40 — it fails identically with these changes
stashed. Ad-hoc checks written and run for the three risky pieces: the auth cache
(one lookup for three requests; bad tokens and disabled accounts never cached;
TTL expiry re-validates; `forget_cached_email` is per-account; the cap holds),
the gzip routing (JSON compressed, `/frame/` `/media/` `/panel/` untouched and
byte-intact), the `content_hash` memo (read once, re-read after a rewrite, digest
still equals a plain sha1 of the bytes) and `runPooled` (completeness, the
concurrency ceiling, a slow item not blocking others, empty input).
⚠ **NOT DRIVEN IN A REAL BROWSER** — the timings in the plan's verification
section are still to be measured against a real project.

### 2026-08-21 — A CAPTION COVERS ITS SHOT, THE WAY THE PICTURE ALREADY DOES (user-reported, with a screenshot)

> "see when i generate veo video and video come in layer and caption and text
>  move but see caption length only 4sec but my video is 8 sec so i want caption
>  goes 8 sec so match video length. like you already do in image"

The captions move now. What they do not do is GROW: a take that turns a 4-second
hold into 8 seconds of footage leaves the subtitle written for that shot stopping
a quarter of the way through it.

⚠ **THIS REVERSES A RULE WRITTEN DOWN DELIBERATELY.** `ripple.js` said "a clip is
looked up by its own start and is NOT stretched — it is a caption of two words,
not a rubber band". That reasoning holds for a subtitle under a picture that
merely got longer; it does not hold here, where the user's whole ask across four
reports has been **the shot and everything over it agree about how long the shot
is**. Asked for explicitly, so it is the ask that wins.

#### The rule

**`grownSpans(before, after)`** — the new span of every board panel whose HOLD
grew. A different question from `renderShifts`: that one says how far each moment
slid, this one says which stretches of film are now longer than what is written
over them. A panel that only MOVED is not in it.

**`coverGrownShots(clips, spans)`** — a generated caption's END is extended to the
end of the shot it sits in.

- ⚠ **THE END MOVES, NEVER THE START.** The words are still spoken when they were
  spoken — a voiceover is a recording and does not stretch — so scaling a caption
  into the shot's new span would slide every subtitle off the line it transcribes.
  Holding the start keeps it on the voice and simply leaves it up for the rest of
  the shot, which IS the trade being asked for: the subtitle now stays on screen
  after the line has finished.
- ⚠ **NEVER PAST THE NEXT CAPTION.** A shot with two spoken lines in it would
  otherwise have the first stretched over the second — two subtitles on screen at
  once, the one thing `captions.tidy_lines` exists to prevent.
- ⚠ **GENERATED CAPTIONS ONLY (`cap…`), AND THE PREDICATE IS HARD-CODED.** That
  prefix marks a clip this app wrote to match a spoken line, so making it agree
  with its shot is finishing our own work. Text the user typed and placed is
  theirs; a caller passing a different predicate would have this pass silently
  resizing their titles.
- ⚠ **IT ONLY EVER GROWS**, like the panel stretch it mirrors.
- ⚠ **MOVED FIRST, THEN STRETCHED** at both call sites
  (`coverGrownShots(rippleClips(list, shifts), grown)`) — a caption is matched to
  its shot by where it now STARTS, so clips that have not been carried yet match
  against the wrong shot.

#### Files

`client/src/animatic/ripple.js` (`grownSpans`, `coverGrownShots`),
`client/src/components/AnimaticEditor.jsx` (the attach and the load heal),
`tests/timeline_ripple_check.py`, `tests/veo_ripple_check.py`.

#### Verified

- `python tests/timeline_ripple_check.py` — **42 checks, all pass**, including the
  two-lines-in-one-shot case, "text the user typed is never resized", "a shot that
  only moved does not stretch anything" and "a caption longer than its shot is
  left alone".
- Driven under node over the real modules with React's setter semantics modelled:
  a 4s shot with one line and a 4s shot with two, each given an 8s take. The
  single caption goes 3.8s → 8s and ends exactly on the shot; the two-line shot's
  first caption stops on the second's start and the second runs to the shot's end;
  a typed title inside the same shot is untouched.
- `veo_ripple_check` (23), `voiceover_fit_check` (51), `captions_check`,
  `veo_download_check`, `image_lane_routing_check` — pass.
- `npx vite build` in `client/` — clean.
- ⚠ **NOT RUN:** the Playwright suites, and still not driven in a real browser.

### 2026-08-21 — THE RIPPLE READ THE DOCUMENT OUT OF A REF, AND A REF IS EMPTY AT LOAD AND STALE IN A POLL (user-reported)

> "now all good audio and image but Caption not move while come veo video
>  see and fix it"

Third report on the same edit. The layout was right, the shift map was right, and
`rippleClips` moves captions correctly when driven directly — all of that was
checked under node against the real modules. What was wrong is **where the five
lists came from**.

#### The shape of the fault

`attachVeoClip` read the captions, shapes, overlays and audio out of a `docRef`
filled by a `useEffect`. That ref is:

- **EMPTY** straight out of the load promise — `onLoadedRef` is called before
  React has rendered anything, so no effect has run; and
- **STALE** inside the Veo poll, which is deliberately keyed on `animating` alone
  so it cannot cancel its own in-flight fetch.

⚠ **AND RIPPLING AN EMPTY LIST IS A SILENT NO-OP THAT LOOKS EXACTLY LIKE "NOTHING
NEEDED TO MOVE".** There is no error, no warning, and the identity check
(`carried.texts !== docRef.current.texts`) says "unchanged" perfectly truthfully.
That is why this took three passes to pin down: every fix made the pictures and
the sound move, and the captions kept failing in a way that produced no evidence.

#### The fix is to stop reading the document at all

Every ripple now goes through **React's own functional setters**, which are handed
the LIVE list at commit time:

    setTexts((list) => rippleClips(list, shifts));
    setShapes((list) => rippleClips(list, shifts));
    setOverlays((list) => rippleClips(list, shifts));
    setAudioTracks((list) => rippleAudio(list, shifts, newId));

- ⚠ **`docRef` IS GONE AND MUST NOT COME BACK** — `tests/timeline_ripple_check.py`
  asserts `"docRef" not in editor`. The whole class of fault was one copy of the
  document too many.
- ⚠ **IT WORKS AT LOAD TIME PRECISELY BECAUSE IT IS AN UPDATER.** The loader has
  already queued `setTexts(p.texts)`; an updater is handed that pending list, so
  the heal ripples the project that is arriving without ever holding a copy of it.
  The hand-written seed added in the entry below is deleted — it was patching one
  symptom of the design fault.
- **`rippleDocument` is gone**, replaced by **`RIPPLED_LISTS`** — the five names in
  one place. What is left to get wrong is forgetting one, and the test counts the
  calls at each of the three sites (attach, load-heal, voiceover poll) so a
  forgotten list fails loudly instead of silently.
- The **voiceover poll** still ripples the server's own texts and audio as VALUES,
  because the server rewrote those two — with `keep`, so the captions and the
  voiceover it just re-timed are not moved twice.

#### Files

`client/src/animatic/ripple.js` (`rippleDocument` → `RIPPLED_LISTS`),
`client/src/components/AnimaticEditor.jsx` (all three sites; `docRef` and its
effect removed), `tests/timeline_ripple_check.py`, `tests/veo_ripple_check.py`,
`tests/voiceover_fit_check.py`.

#### Verified

- `python tests/timeline_ripple_check.py` — **33 checks, all pass**, including
  "NOTHING READS THE DOCUMENT OUT OF A REF" and a per-list count at every site.
- **React's setter semantics were modelled in node over the real modules** — a
  toy state cell whose setter takes a value or an updater, driving `attachVeoClip`
  verbatim. A 6s take over a 2s shot: the panel grows to 6s, the next panel moves
  to 10s, the caption at 9s moves to 13s, and the voiceover is cut at 6s with its
  tail laid at 10s reading from 6s — **so the caption and the audio under it land
  on the same millisecond**, which is the thing that was actually broken.
- `veo_ripple_check` (23), `voiceover_fit_check` (51), `captions_check` — pass.
- `npx vite build` in `client/` — clean.
- ⚠ **NOT RUN:** the Playwright suites, and still not driven in a real browser.

### 2026-08-21 — …AND IT ONLY RAN ON THE ATTACH, SO EVERY TAKE ALREADY ON A TIMELINE STAYED WRONG (user-reported, with two screenshots)

> "see i check when i generate shot 18 so image not capture video lenth only
>  video come but image still not extend/ripple video 4 sec time lenght
>
>  and see you when Veo video come in layer so image move but caption and audio
>  not move same place so not solved i think please fext it"

The rule shipped in the entry below was correct — driven through the editor's own
sequence under node, a 4s take grows its panel to 4s, the shots after it move, the
captions move and the voiceover is cut at the edit. What was wrong was **WHEN it
runs**, in two places, and the symptom of both is exactly what a board that never
got the fix looks like.

#### 1. The layout only ever ran on the ATTACH

`spreadPanelsForRenders` is called from `attachVeoClip` and nowhere else, and
`reconcileVeoClips` skips a clip already on the timeline (`already`). So a render
that landed **before** the stretch existed keeps a 2-second still under 4 seconds
of footage **for ever** — there is no gesture that re-runs it, and paying to render
the shot again just to straighten the row is not a fix.

- **The load runs it once** (`onLoadedRef`), after the Veo recovery: the same
  `spreadPanelsForRenders` → `renderShifts` → `rippleDocument` the attach runs.
- ⚠ **IT COSTS NOTHING ON A BOARD THAT IS ALREADY RIGHT.** Both passes are
  idempotent and hand back the SAME arrays when they change nothing, so a correct
  project is an identity test and no edit — which is what stops this dirtying
  every animatic on open. Checked, because a load-time pass that is not an
  identity test writes to the server on every single load.
- ⚠ **AND IT SAYS SO.** Clips moving by themselves the instant a project opens is
  the most alarming thing this editor can do silently, so there is a notice.
- It runs before `resetHistory`, like the `start_ms` normalisation beside it, so
  the first Ctrl+Z cannot undo into a half-healed row; and it sets `changed`, so
  the load is not adopted as the saved baseline and the autosave persists it.

#### 2. `docRef` was EMPTY for a clip recovered at load

`docRef` (the captions, shapes, overlays and audio the ripple carries) is filled
by an effect. `onLoadedRef` is called **straight out of the load promise**, before
React has rendered anything, so no effect has run — a paid clip recovered there
rippled an empty document. The pictures moved and the sound stayed exactly where
it was, on every reload. ⚠ **Seeded from `p` in the same breath as `framesRef`**,
which was already being seeded there for precisely this reason.

#### Files

`client/src/components/AnimaticEditor.jsx` (`onLoadedRef`),
`tests/timeline_ripple_check.py`.

#### Verified

- `python tests/timeline_ripple_check.py` — **30 checks, all pass**. The new logic
  case is a board saved BEFORE the stretch existed — take already attached, panel
  still short — and it asserts both that it is put right and that a second pass
  over the result is an identity test.
- The editor's own attach sequence was driven under node against the real modules
  (25 shots, animate 7 then 18, with captions and a one-clip voiceover): panels
  grow to 4s, later shots move, captions shift, the voiceover is razored twice and
  each piece reads on from where the last stopped.
- `veo_ripple_check`, `voiceover_fit_check`, `captions_check`, `veo_download_check`,
  `image_lane_routing_check`, `editor_board_import_check` — pass unchanged.
- `npx vite build` in `client/` — clean.
- ⚠ **NOT RUN:** the Playwright suites, and none of this has been driven in a real
  browser. ⚠ **AND A NOTE FOR WHOEVER READS THE NEXT REPORT:** if a symptom
  matches the behaviour of the code as it was BEFORE a change, check that the
  bundle being tested contains the change before looking for a second bug — the
  screenshots here were of a board whose takes had landed under the old rule, and
  the two defects above are what stopped it healing.

### 2026-08-21 — A SHOT GROWS TO ITS TAKE, AND THE WHOLE FILM MOVES WITH IT (user-reported, with three screenshots)

> "see image 3 when i generate Veo video from image shot 7 so first fixt it my
>  video lengh is 4 sec but image sitll in 2 sec so image also extend 4 second
>  when com veo video that time like when i generte voice over so image capture
>  time frame of voiceover and caption
>
>  and second when i generte veo video so time timeline all layer clip go move but
>  my audio not move so see problem my caption and voiver over not move so both
>  still . so get this type of problem. So i want when i generate veo video an dit
>  come in Story..video layer so iamge clip move already but move also caption,
>  voicerover audio, if image, video ,text layer clip so those also move that time
>  so user not get this type of problem"

Two halves of one edit: **how long the shot becomes**, and **what else moves when
it does**.

#### 1. The panel takes the take's length

`spreadPanelsForRenders` kept a panel at its own hold and pushed the panels AFTER
it clear of the take. So a 2-second still sat under 4 seconds of footage — a shot
whose two halves disagree about how long it is, and one that collapses back to 2s
the moment the take is deleted.

- **The panel keeps its START and grows to the take's end** (`scene.js`). Same
  rule the voiceover already follows (`_lay_out_speech`): a shot is as long as the
  thing laid over it.
- ⚠ **IT ONLY EVER GROWS.** A take shorter than its panel leaves the panel alone —
  shrinking a hold somebody set by hand is not making room, it is discarding an
  edit. Clamped at `MAX_FRAME_MS` (`AnimaticFrame.duration_ms`'s own `le=600_000`),
  because a length computed here that the wire rejects is a lost project.
- Everything else about the pass is unchanged: forward only, never past where a
  clip already is, a take travelling with its panel by the panel's delta, a second
  run over its own output a no-op.

#### 2. `ripple.js` — the rest of the film travels with the pictures

The pass moves PICTURES, because that is the collision it was written for.
Captions, voiceover, typed text, shapes, overlays and the Video row all stayed
put, so one grown shot put the whole soundtrack out for the rest of the film.

- ⚠ **THERE IS NO SINGLE NUMBER TO MOVE THINGS BY.** Shot 7 grows by 2s and shot
  24 by 9s. **`renderShifts(before, after)`** turns what the layout pass did into a
  STEP FUNCTION over OLD time — two points per panel, its start AND its end,
  because a shot that grew *without moving* owes nothing at its head and the whole
  of its growth at its tail. Matched by frame ID, never by index.
- **`rippleDocument`** carries `frames` / `texts` / `shapes` / `overlays` /
  `audioTracks` in one call, so a caller cannot move four lists and forget the
  fifth. Every list is handed back by IDENTITY when nothing moved.
- ⚠ **A CLIP IS LOOKED UP BY ITS OWN START AND IS NOT STRETCHED.** A caption that
  sat inside the grown shot stays inside it; one a millisecond past its old end
  owes the whole debt. That boundary is what `tests/timeline_ripple_check.py` is
  really guarding — off by one clip and the subtitles are a shot out for the
  entire second half of a board.
- ⚠ **THE VOICEOVER IS CUT AT THE EDIT, NOT DRAGGED WHOLE.** It is ONE clip laid
  from 0:00 across the film: its start is 0 so the map owes it nothing (the bug),
  and shifting it by a later shot's debt would drag the lines BEFORE that shot
  along too (a different bug). `rippleAudio` razors it at the step with the
  razor's own **`splitClip`** and moves only the tail — two ordinary clips reading
  two windows of one file, which nothing downstream has to learn about.
- ⚠ **EVERY CLIP THAT CAME OFF A BOARD IS SKIPPED** by `rippleFrames` — panels AND
  takes. The layout pass has already placed both, and the map is in OLD time, so
  looking a moved take up at its NEW start adds its debt twice and slides it off
  the shot it is a take of. Shipped correct only because the test that catches it
  was written first; it is checked by identity (`v3 === p3`).

#### 3. Both passes run it

- **`attachVeoClip`** runs `renderShifts` + `rippleDocument` in the same write
  that makes room, with the editor's own `newId` as the id minter (ids are the
  editor's to hand out, not a pure module's). ⚠ **The carried lists go back into
  `docRef`**, because a batch of four renders attaches in one tick and the second
  must not ripple a document the first already rippled.
- **The voiceover poll** does the same against the row the server re-laid —
  `speechFramesRef` is the picture row as it stood when the run was submitted, and
  is the only record of where those shots were. ⚠ **`keep` is not optional**: the
  generated captions and the new voiceover track are already timed against the NEW
  layout, and shifting them by the same map would be this bug committed by its own
  fix.

#### Files

`client/src/animatic/ripple.js` (new), `client/src/animatic/scene.js`
(`MAX_FRAME_MS`, the panel stretch), `client/src/components/AnimaticEditor.jsx`
(`docRef`, `speechFramesRef`, `speechAudioRef`, both call sites, the notice),
`tests/timeline_ripple_check.py` (new), `tests/veo_ripple_check.py`,
`tests/voiceover_fit_check.py`.

#### Verified

- `python tests/timeline_ripple_check.py` — **25 checks, all pass** (node +
  source, no browser). The double-move guard was confirmed to FAIL against the
  previous rule before the fix was kept.
- `python tests/veo_ripple_check.py` — **23 checks, all pass**; its two expected
  layouts were rewritten for the stretch and a "a take SHORTER than its panel does
  not shrink it" case added.
- `python tests/voiceover_fit_check.py` — **51 checks, all pass**.
- `captions_check`, `veo_download_check`, `image_lane_routing_check`,
  `audio_razor_check`, `audio_mix_check`, `picture_tracks_check`, `razor_check` —
  pass unchanged.
- `npx vite build` in `client/` — clean.
- ⚠ **NOT RUN:** the Playwright suites, and **none of this has been driven in a
  real browser** — in particular the razored voiceover has not been LISTENED to
  across the cut. Browser tests are run on request in this project.

### 2026-08-21 — A SHOT HOLDS ITS OWN LINE, AND THE DIALOGUE IS A SCRIPT YOU CAN EDIT BEFORE IT IS READ (user-reported, with four screenshots)

> "when i generate voiceover of my Story..image layer in timline Voicerover
>  buttun so Geneate perfectly voiceover and caption and placement Starting is
>  good ony but caption and voicerover goes overlap other image shots see image 1
>  but i want like this look image 4 … so my shot 9 image cover voiceover lenght
>  and set like image 4 and other voicer and capyion arrange like this
>
>  Second i want i see my Storyborad Dialouge in here (read the dialogue aloude)
>  in pop-up so user see what dialouge generte so user look if user want chnage so
>  user change/edit Dialouge … like animate with veo pop up view shot with prompt
>  … and if posible so add character name like so user understand what charater
>  voicerover and with gender men/women, boy/girl, child and grand father"

Two asks about one dialog: **where the sound lands**, and **what you can see and
change before paying for it**.

#### 1. The overlap was built in — there were TWO clocks

A line is laid at the start of the shot it belongs to. The shot holds for two
seconds and the line takes ten, and nothing moved the picture — so the line and
the caption built from it ran straight over the four shots after it.

    image   [S9][S10][S11][S12][S13]                          <- before
    audio   |========= S9's line =========|

    image   [ S9 ..................... ][S10][S11][S12][S13]  <- after
    audio   |========= S9's line =========|

- **The room comes from the row itself**, exactly as it does for a Veo take.
  `_lay_out_speech` (`server/animatics.py`) stretches the shot that owns a line to
  cover it (line + `GAP_MS`, so the next picture starts on the breath and not on
  the last syllable) and pushes the shots after it clear — the same forward-only
  ripple as `spreadPanelsForRenders`, which it deliberately mirrors: **a Veo take
  travels with its panel by the panel's delta**, and the next panel clears the
  TAKE's end, not just the panel's.
- ⚠ **`tts.synthesise_timed` IS GONE, AND THAT IS THE FIX.** It advanced its own
  clock by `line + gap` while the picture row was never touched at all. Two clocks
  agree right up until a shot holds LONGER than its line — from there the audio
  runs ahead of the pictures and every line after it is early. `tts` now speaks,
  measures and lays blobs where it is told (`speak_lines` / `assemble`); the one
  clock lives with the frames.
- ⚠ **FORWARD ONLY, AND NEVER PAST WHERE A CLIP ALREADY IS.** A second run over
  its own output moves nothing, a gap the user opened by hand survives, and
  `_write_frames` is skipped entirely when nothing had to move.
- ⚠ **THE EDITOR MUST RE-READ THE FRAMES.** This pass now moves pictures, so the
  speech poll in `AnimaticEditor.jsx` calls `setFrames(project.frames)` alongside
  the texts and audio. Without it the browser holds the old layout and its next
  autosave writes that back over the one the server just worked out.
- **`fit_shots: false`** is the escape hatch: not one picture moves, not even to
  clear a take, and a long line pushes the next LINE later instead — the
  behaviour this pass had before it could stretch anything.

#### 2. The dialog shows the script now, and says who is speaking

The dialog offered a voice and a price. What would actually be said was whatever
the board happened to hold, unseen and uneditable — the same gap ✨ Animate closed
when it started showing its prompt.

- **`GET /animatics/{id}/dialogue`** — FREE, calls no model. Every spoken line,
  the shot it belongs to (`Shot 9`), its speaker, and both pickers. ⚠ **The voice
  list comes from the SERVER** (`tts.CAST`): six names were typed into the JSX,
  which is a second source of truth for something the model call has to agree with.
- **A line carries a PERSONA** — `boy`, `girl`, `child`, `young man/woman`,
  `man`, `woman`, `grandfather`, `grandmother`, `narrator`. ⚠ **THE PERSONA IS THE
  ONLY THING THAT CARRIES AN AGE AND A SEX TO THE MODEL**: a voice name is a
  timbre, so the persona writes a stage direction (`Read this line as an elderly
  man, gravelly and unhurried:\n"…"`) AND casts the default voice. The direction is
  shown in the dialog and never reaches the captions.
- **`tts.persona_from`** guesses it from the board's own cast sheet — free,
  keyword-only, and always overridable. ⚠ **It declines to guess a sex the board
  never gave** ("" reads plainly); an age in years beats an adjective.
- **The edited sheet WINS ENTIRELY** and is sent on BOTH calls, so the estimate
  prices the words on screen. `AnimaticVoiceoverRequest.lines`; a `frame_id` that
  is not on the timeline is dropped rather than placed at zero. ⚠ **A persona is
  part of the price** — `tts.estimate` counts `prompt_for`, not the bare line.
- Voice resolution is one order in both halves (`tts.voice_for` / `voiceForLine`):
  the line's own pick, then its persona's casting, then the dialog's default.

#### Files

`tts.py` (cast table, personas, `persona_from`, `prompt_for`, `speak_lines`,
`assemble`; `synthesise_timed` removed), `server/animatics.py`
(`_dialogue_sheet`, `_requested_lines`, `_lay_out_speech`, `GET /dialogue`),
`server/schemas.py` (`VoiceoverLine`, `VoiceOption`, `PersonaOption`,
`AnimaticDialogueLine`, `AnimaticDialogueSheet`, two new request fields),
`client/src/api.js`, `client/src/components/AnimaticEditor.jsx`,
`client/src/styles/animatic-text.css`, `tests/voiceover_fit_check.py` (new),
`tests/captions_check.py`.

#### Verified

- `python tests/voiceover_fit_check.py` — **49 checks, all pass**. No model call:
  `tts.speak` is stubbed with silence of a known length, which is what makes the
  layout arithmetic checkable. Covers the ripple, the take pairing, the second
  run being a no-op, `fit_shots: false`, the edited sheet, and the browser wiring.
- `python tests/captions_check.py` — pass (its voiceover section was rewritten for
  the new `tts` API and gained the casting checks).
- `python tests/animate_prompt_draft_check.py`, `tests/editor_veo_attach_check.py`,
  `tests/frame_save_fields_check.py`, `tests/hidden_lane_check.py` — pass unchanged.
- `npx vite build` in `client/` — clean.
- ⚠ **NOT RUN:** the rest of the Playwright suites, and **the dialog has not been
  opened in a real browser** — the sheet's layout at forty lines is unverified by
  eye. Browser tests are run on request in this project.

### 2026-08-21 — AN UPLOADED PICTURE GOES TO THE **Images** LAYER, THE **Stills** ROW IS GONE, AND A BLANK LANE IS NO LONGER AN UPLOAD BUTTON (user-reported, with two screenshots of the gutter)

> "see when upload image in media so in timline image show Still layer and when i
>  upload through image layer so i see this good but i wnat same in same like
>  image layer only not need still layer remove still layer when user uplaod
>  media or layer so image shoul come in image layer not sitll layer
>
>  and i wnat second things i lony upload media and layer + icon not in
>  Background panel of clip remove this in time blank box layer only keep media
>  and layer + icon"

Two asks about the same gutter: **where an upload lands**, and **which controls
may start one**.

#### 1. The Stills row was not just a name — it was in the wrong place

A `stills` picture row was created FOR the user the first time they uploaded a
photo. Picture rows stack **highest draws first**, and that new row went in above
the storyboard rows — so one dropped photo covered the opening seconds of the
board. Uploading the same picture through the **Images** layer composites it OVER
the cut at a third of the frame instead, which is the behaviour the report calls
"good".

- **`stills` is out of `ROW_KINDS`** (`client/src/animatic/scene.js`). The three
  rows left in the cut are `board_image`, `board_video`, `video`. `ROW_KIND` in
  `AnimaticEditor.jsx` lost its entry to match, and `LANE_HUE` in `Timeline.jsx`
  lost its colour.
- **One routing rule, `belongsOnImageLane(kind, fromBoard)`**, new in `scene.js`
  beside the row kinds. Every door into "add a picture" asks it: the Media pane's
  ＋ and the drop card beside it (`addAssets` with no row named), a library card's
  ＋ and a double-click on one (`placeAsset`). A board panel and a **colour card**
  are deliberately NOT overlays — the panel belongs to the storyboard rows, and a
  colour card is full-frame and takes up time in the cut.
- **A picture can still be put in the cut on purpose.** `ROW_TAKES.video` is
  `["video", "image"]`, so aiming a file at the **Video** row — its ＋, or a drag
  onto it — still makes a full-frame still there. That is what the ＋ Add layer
  menu has always claimed the Video row is for; what went is the row that
  appeared unasked. `addFiles` therefore stays alive and unchanged.
- **The Media drop card's note now says where each kind goes** ("Video for the
  video track · images for the Images layer · an MP3 for the audio") — the one
  sentence on screen that could contradict where the clip appears.

#### 2. Nothing saved changes — the migration is a READ, not a rewrite

- **`rowKindOrLegacy`** (`scene.js`) reads a stored `kind: "stills"` layer record
  as the plain video row its clips already sit on; `videoTracks` in the editor
  goes through it and blanks the stored name ("Stills" names a kind that no
  longer exists), so the row renumbers as "Video"/"Video 2".
- **`cardRowKind` answers `"video"` for a plain picture.** A clip whose row kind
  no longer existed would be unnameable by `dominantRowKind` and unmovable by
  `laneMoveTarget`. The export reads a clip's `track` NUMBER and nothing else, so
  every photo of every project made before this keeps its timing and its pixels.
- `server/schemas.py` documents `stills` as a RETIRED fourth picture kind that is
  still accepted, and says where it is read.
- ⚠ **Consequence, recorded honestly:** the two picture rows a `▶⇧` split leaves
  behind are now the SAME kind, so a still and a piece of footage may be dragged
  between them. "Image moves only in image layers" is now carried by the Images
  LANE (overlays cannot reach a picture row at all — `laneMoveTarget` refuses a
  lane of another `kind`) and by the two board rows.

#### 3. A blank lane is no longer a button

The empty band of every row WAS its add control: a full-width, invisible button
that opened a file dialog when clicked, and that swallowed `pointerdown` so an
empty row could not be scrubbed or marqueed. `emptyBand` in `Timeline.jsx` renders
`null` now. The two ways in are the Media pane and the row's own ＋ in the gutter
— "only keep media and layer ＋ icon" — and the blank part of a row behaves like
the rest of it. `.tl-track-add` in `animatic-text.css` is kept, unreferenced, with
a note saying so.

#### Files

`client/src/animatic/scene.js`, `client/src/components/AnimaticEditor.jsx`,
`client/src/components/Timeline.jsx`, `client/src/styles/animatic-text.css`,
`server/schemas.py`, `tests/image_lane_routing_check.py` (new),
`tests/editor_picture_tracks_check.py`.

#### Verified

- `python tests/image_lane_routing_check.py` — **26 checks, all pass** (node +
  source, no browser).
- `python tests/veo_download_check.py` and `python tests/veo_ripple_check.py` —
  pass unchanged (both lean on `cardRowKind`).
- `npx vite build` in `client/` — clean.
- ⚠ **NOT RUN:** the Playwright suites. `tests/editor_picture_tracks_check.py`
  was EDITED here — its "a clip only moves to a row of its own kind" pair
  asserted the Stills/Video split that no longer exists and now asserts the
  one-kind rule — and that edit is unverified. Browser tests are run on request
  in this project.

### 2026-08-21 — A TAKE MAKES ROOM FOR ITSELF: ANIMATING A SHOT PUSHES THE PANELS AFTER IT ALONG (user-reported, with three screenshots)

> "when i generete image to veo video in timeline so shot 1 image so i get shot
>  Veo video of Story..video layer in same place this is good but i again generate
>  shot 2 image to veo video so see my second shot 2 video overlap on shot1 video
>  so this fuction not good for user … Automatic my storyborad image clip move
>  like this after shot 1 image like 2 shot image with all image move in time
>  line … so my Video and image clear view so user not confuse waht happen in
>  timeline"

**A RENDER IS AS LONG AS VEO WAS ASKED FOR, AND THE HOLD IT CAME FROM IS NOT.**
4 seconds of footage over a 2-second panel is the ordinary case, not an edge one.
`attachVeoClip` starts a take where its panel starts — which is right, and was
never the bug — so the SECOND take, one hold along, began inside the first one's
tail and the two bars sat on top of each other on the Storyboard video row. The
first animate looked perfect; the second made the row unreadable.

    video   [ Shot 1 ····· ]                     <- before
    video       [ Shot 2 ····· ]                    (Shot 2 buried in Shot 1)
    image   [S1][S2][S3][S4]

    video   [ Shot 1 ····· ][ Shot 2 ····· ]     <- after
    image   [S1]            [S2]            [S3][S4]

⚠ **THE ROOM COMES FROM THE ROW UNDERNEATH — THE PANEL MOVES, NOT THE RENDER.**
A take's place IS its panel's place; making room by sliding takes along would only
move the collision somewhere the user cannot explain, and it would break the one
thing the Storyboard video row is for (a render sitting over the shot it was made
from, so 👁 on that row shows the board again underneath). So the animated panel
stays exactly where it is and the panels AFTER it are pushed clear of the take's
end. That is `spreadPanelsForRenders` in `client/src/animatic/scene.js`, run by
`attachVeoClip` in the same write that adds the clip — the same "everything after
it moves too" ripple `insertPictures` performs, measured against the VIDEO row's
lengths instead of the panels' own.

⚠⚠ **FORWARD ONLY, AND NEVER PAST WHERE A CLIP ALREADY IS. DO NOT "TIDY" THIS
INTO A RE-LAY OF THE ROW.** Every other clip on a picture track obeys one rule —
it moves when you move it and at no other time (`frameSpans`) — and this ripple is
the single deliberate exception, in ONE direction. A pass that also closed gaps
would eat a gap the user opened by hand, and would yank every panel back the
moment a take was deleted. So: `Math.max(currentStart, clock)`, and nothing else.
The function hands back the SAME LIST when it moved nothing, which is both how a
second pass is proved to be a no-op and how the editor knows whether to say a
panel moved.

⚠ **A RENDER MOVES BY ITS PANEL'S DELTA, NOT ONTO ITS PANEL'S START.** Snapping
would silently undo a nudge the user gave a take; carrying the delta keeps the two
aligned however they were placed. Whatever its offset, the take's END is what the
next panel clears — animate shot 3 first and then shot 1, and take 3 travels along
with panel 3 keeping its offset to the millisecond.

⚠ **PAIRED BY THE BOARD REFERENCE `attachVeoClip` COPIES OVER**, i.e.
`storyboard_id` + `index` + `frame` (`shotKey`). NOT by `assetKey` — that keys a
render by its UPLOAD, because by then `src.kind` is "video", and the whole problem
here is matching a take back to the still it was made from. `frame` is in the key
because a key pose and its panel share a `storyboard_id` AND an `index`; without
it a take of pose 7 is credited to the panel underneath it and shoves the pose
itself out of the way. A render is also consumed ONCE, so a duplicated panel does
not claim the original's take and drag it off the shot it belongs to.

⚠ **PER BOARD TRACK.** An animatic may hold a second "Storyboard images" row; the
clock is kept per track, exactly as `frameSpans` keeps its own, so one row's
ripple never moves another row's panels.

⚠ **AND THE EDITOR SAYS IT OUT LOUD** — "Clip ready — it's on the timeline, and
the panels after it moved along to make room." A clip that moves on its own with
nothing said about it reads as the editor losing the user's cut. The wording is
used only when something actually moved (the identity test above), so it cannot
become a line that is always there and therefore never read.

**Files:** `client/src/animatic/scene.js` (`shotKey`, `spreadPanelsForRenders`),
`client/src/components/AnimaticEditor.jsx` (`attachVeoClip` runs it and returns
whether anything shifted; `reconcileVeoClips` aggregates that into the notice),
`tests/veo_ripple_check.py` (new).

**Verified:** `python tests/veo_ripple_check.py` — 20/20, and the two layouts it
asserts are the user's second and third screenshots in milliseconds (0 / 4000 /
6000 / 8000 … after one take; 0 / 4000 / 8000 / 10000 … with two). The pairing key
and the consume-once rule were each mutation-tested on a COPY of `scene.js`: drop
`frame` from the key and the pose case lands at 12000 instead of 6000; stop
consuming a render and the take slides off its panel. `veo_download_check`,
`picture_tracks_check`, `video_clip_check`, `asset_fields_check` and
`selection_check` all still pass, and `npx vite build` is clean. **Not opened in a
browser** — see Next Steps.

### 2026-08-21 — A VEO RENDER CAN BE SAVED TO DISK, FROM TWO PLACES (user-specified, with a screenshot of the Media card and the timeline)

> "i want you add download icon in media panel of only Veo video so user
>  download video in local. because if user want delete project so user first
>  download veo gneereted video in midea panel or when user click right mouse on
>  clip in timeline so user get side of clip dropdown Download text buttun so
>  user download both place … only add fuction when user generte Veo video"

**A VEO RENDER IS THE ONE ASSET IN A PROJECT THAT CANNOT BE GOT BACK.** An upload
came off the user's machine and a panel is still on the storyboard; re-rendering
this costs money. Until now the only way out of the editor was the whole animatic
as one encoded MP4 — so deleting a project destroyed every render in it, and the
reason given for the request says exactly that.

⚠ **"ONLY VEO" IS THE REQUIREMENT, AND IT IS THE HALF THAT ROTS SILENTLY.** A
⬇ that showed up on a panel or on a dropped file is not a crash and not a visible
mistake — it is a Download that fetches a poster PNG, or one offered on a file
the user already has. `tests/veo_download_check.py` pins the question against
every other kind of source in the library, including the two near-misses: a board
panel (same storyboard, not footage) and an uploaded video (same footage, no
storyboard).

⚠⚠ **`isVeoRender` IS `cardRowKind(kind, fromBoard) === "board_video"` — NOT A
NEW FIELD, AND DO NOT ADD ONE.** A paid render was already identified in this
codebase exactly once, by two facts on the clip itself: it came out of a
storyboard (`src.storyboard_id`, kept underneath the video source — see
`attachVeoClip`) and it is footage now. That pair is what draws these bars pastel
purple (`.tl-bar.is-veo`) and pins them to the Storyboard video row. A `from_veo`
flag, or a lookup into the server's `veo_clips`, would be a **second opinion that
can disagree with the colour on screen**, and it would need a migration for every
project rendered before the day it was added. This needs none — and the test
asserts the two answers stay identical, so a bar cannot become purple without a
⬇ or the other way round. (It also cannot catch an upload by mistake:
`ROW_TAKES` gives both board rows an empty list, so no dropped file ever acquires
a `storyboard_id`.)

⚠ **THE ⬇ GOES FIRST IN THE CARD'S TOOL ROW — ⬇ ＋ ✕, NOT ＋ ⬇ ✕.** It
shipped in the middle and was reported the same session: "keep download icon
first because not match uper clip in icon see". `.fs-tools` is the right-hand
child of a `space-between` foot, so it grows LEFTWARD off the card's edge — an
extra button in the MIDDLE pushes ＋ one slot left on the cards that have one,
and a library is a COLUMN of cards, so ＋ then sits in two different places down
the same list. At the front it costs nothing: ＋ and ✕ stay in the same two
columns on every card, and the only thing that varies is whether a third icon
hangs off to the left of them. **A layout rule, not a preference** — anything
added to this row later goes in front of ＋ for the same reason.

⚠ **ONE HANDLER, TWO ENTRY POINTS.** `downloadVeoClip` in `AnimaticEditor.jsx`
takes **a clip *or* a library card** — both carry `src.upload_id`, because a card
is built from the clip by `assetFromFrame` — so the ⬇ and the menu cannot come to
save different files. It re-asks `isVeoRender` itself rather than trusting its
caller: it is the last gate before the bytes.

⚠ **THE MENU OPENS BESIDE THE BAR, NOT UNDER THE POINTER**, which is what was
asked for ("side of clip dropdown"). A menu at the pointer sits **on** the clip
and hides the thing it is about. `.tl-clip-menu` is a child of `.tl-cols` with
`top`/`left` measured in a layout effect — the same arrangement, and the same
reasons, as the ✕'s confirm: the bar itself lives inside `.tl-scroll`, which
scrolls on both axes and clips, so a menu rendered in there would be cut off at
the edge of the pane. It flips to the left of the bar when a clip near the end of
the pane leaves no room on the right, and `data-side` moves the caret to match.
It borrows `.tl-layer-menu`'s surface and its `.tl-layer-menu-opt` rows, because
＋ Add layer, the ✕'s confirm and this are now three popovers on one bar.

⚠ **RIGHT-CLICKING ANY OTHER CLIP LEAVES THE BROWSER'S OWN MENU ALONE.** The
guard returns **before** `preventDefault` — asserted by the test, because getting
that order wrong means every bar on the timeline swallows the native menu and
offers nothing in its place, which is strictly worse than the behaviour this
replaced. A menu holding one greyed-out line would be worse than no menu: it
promises a place where clip commands live and then has none. When there is
something to put there for every clip, that is where it goes.

⚠ **THE MENU OPENS, IT DOES NOT TOGGLE.** The outside-press listener fires on
the same gesture's `pointerdown` and shuts whatever was open, so a functional
toggle in `onContextMenu` would read the pending `null` and re-open on every
second right-click of the same bar — which looks like the menu ignoring you.
(Right-click was inert on this timeline before today: both `startClipDrag` and
`startLanePress` already returned on `e.button !== 0`.)

⚠ **THE FETCH GOES THROUGH `fetch`, NOT AN `<a href>`.**
`/animatics/{id}/media/{upload_id}` is owner-checked and needs a bearer token; a
plain link sends no headers and would land on a 401 page. `downloadAnimaticMedia`
follows every other download in `api.js`: authed blob → temporary `<a download>`
→ `revokeObjectURL` (a render is tens of megabytes — leaking one per press is
real memory). **The name is the caller's**: this route serves stills, footage and
audio alike and sets no Content-Disposition, so `serverFilename` has nothing to
read; the editor knows the clip's label and sanitises it for Windows.

⚠ **AND IT SAYS SOMETHING BEFORE IT STARTS.** There is no browser download bar
until the whole blob has landed, so without the first notice a press on a large
render looks like it did nothing for several seconds.

**No server change.** The route already serves the MP4.

**Files:** `client/src/animatic/scene.js` (`isVeoRender`), `client/src/api.js`
(`downloadAnimaticMedia`), `client/src/components/AnimaticEditor.jsx`
(`downloadVeoClip`, both props), `client/src/components/MediaBin.jsx` (the ⬇),
`client/src/components/Timeline.jsx` (`onDownloadClip`, the menu, its two
effects), `client/src/styles/animatic-editor.css` (`.tl-clip-menu`),
`tests/veo_download_check.py` (new).

**Verified:** `python tests/veo_download_check.py` — 20/20. `npx vite build` —
clean. `tests/asset_fields_check.py`, `picture_tracks_check.py` and
`selection_check.py` still pass.
⚠ **PRE-EXISTING FAILURE, NOT CAUSED HERE:**
`tests/editor_media_row_routing_check.py` fails one assertion — "…level with the
row it is about", the ✕ confirm's vertical centring. Confirmed by reverting
`Timeline.jsx` and `animatic-editor.css` to HEAD and re-running: it fails
identically on the untouched files. Everything else in that suite passes.
Not opened in a browser, per the standing "browser tests on request only" rule.

### 2026-08-21 — THE PROGRAM MONITOR GOES FULL SCREEN (user-specified, with a screenshot of a player's "Full Screen video" control)

> "i want you add in program panel ike this fuction Full Screen video you keep
>  name Full screen and not copy same icon you add similar common icon set in
>  Progran balnk cornaer og progrm text line"

**The monitor is the smallest pane on the screen and it is the one you are
actually watching.** A player has had a way out of that box since 2010; this
editor did not, so checking a cut meant dragging two pane seams and dragging
them back.

⚠ **WHAT GOES FULL SCREEN IS `.an-program-body` — THE PICTURE *AND* THE
TRANSPORT.** Not the picture alone (a preview you cannot pause, step or scrub is
a screensaver, and the clock is how you know where you are), and not the whole
`<section>` either — a pane head with an aspect-ratio menu on it is furniture at
two metres, and the button that got you here would then be sitting in the middle
of the film.

⚠ **THE `fullscreen` FLAG IS WRITTEN BY THE EVENT, NEVER BY THE CLICK.** Escape,
F11 and the browser's own chrome all leave full screen without telling the app,
so a boolean flipped inside `toggleFullscreen` would have left the button drawing
"exit" over a window that had already come back — and pressing it would then have
done nothing, because there is nothing to exit. A `fullscreenchange` /
`webkitfullscreenchange` listener is the only thing that knows, so it is the only
thing that writes, and it compares against `programBodyRef.current` so another
element's full screen is not mistaken for ours.

⚠ **`requestFullscreen` IS `.catch`ed.** It rejects — an iframe without
`allowfullscreen`, a call the browser doesn't consider a user gesture — and an
unhandled rejection in a click handler is a console error plus a button that looks
broken with nothing said about why.

⚠ **THE ICON IS FOUR CORNER BRACKETS, WHICH WAS THE ASK: "not copy same icon,
add similar common icon".** `fullscreen` and `fullscreen-exit` are new entries in
`Icon.jsx`, stroked in `currentColor` like the rest of the set, and they are one
object in two states (the corners fold inward to leave) exactly the way
`eye` / `eye-off` is. The player's two diagonal arrows are a picture of a
*gesture*; brackets are a picture of the *frame*, which is what this set is made
of — a panel, an eye, a bin. Sized in `rem`, not `em`: `.an-tool`'s font-size is
0.72rem and an `em` icon comes out ~12px, which is mud (the same trap the timeline
tool buttons already carry a comment about).

⚠ **ALMOST NO SIZING CODE WAS NEEDED, AND THAT IS THE POINT.**
`.an-screen-fit` is already `container-type: size` and `.an-nle .an-screen` is
already `min(100cqw, calc(100cqh * var(--ar-num)))`, so a body that fills the
display makes the monitor fill it at the project's exact aspect ratio with no
second sizing rule to keep in step. What the `:fullscreen` block actually sets is
a **background** — the UA paints the backdrop black but leaves the element's own
background alone, and `.an-pane-body` has none, so the clock and the labels would
have gone dark-on-black — plus a bigger transport for reading across a room.

⚠ **`:fullscreen` AND `:-webkit-full-screen` ARE TWO SEPARATE BLOCKS AND MAY NOT
BE COMMA-JOINED.** One unknown selector invalidates a whole selector list, so the
joined form drops *both* rules in every engine.

⚠ **THE BUTTON IS PUSHED RIGHT WITH `margin-left: auto`, NOT AN `.an-spacer`.**
`.an-pane-head` is `flex-wrap: wrap`; a growing spacer sits on whichever line it
lands on and would shove the button to the far edge of a row it no longer shares
with the title. It carries `aria-label` and `aria-pressed`, because the SVG is
`aria-hidden` and the button would otherwise announce as "button".

No keyboard shortcut was added — Escape already leaves, and there is no global
key handler in this editor to hang an `F` on without auditing the timeline's
V/B/N/H/Z bindings first. Say the word if you want one.

**Files:** `client/src/components/Icon.jsx` (two new glyphs),
`client/src/components/AnimaticEditor.jsx` (the ref, the flag, the listener, the
toggle, the button), `client/src/styles/animatic-editor.css` (`.an-fs-btn` and
the `:fullscreen` blocks).
⚠⚠ **THE THREE HOOKS LIVE AT THE TOP OF THE COMPONENT, WITH THE MONITOR'S
OTHER STATE, AND MOVING THEM DOWN NEXT TO THE JSX IS A BLACK PAGE.** They were
written beside the Program pane at first — which is where they read best — and
that is **below** `AnimaticEditor`'s two early returns (`if (loading)` and
`if (error && !frames.length && !title)`). So on the first render, while the
project was still opening, those `useRef` / `useState` / `useEffect` /
`useCallback` calls never ran; on the render after it loaded, they did. React
counts hooks: *"Rendered more hooks than during the previous render"*, thrown
during render, which takes the whole editor down. **Reported as "not open new
project and recent too in Video editor workflow" — the Video Editor workflow
went to a blank black page on opening any project, new or recent.** Fixed by
moving the block up beside `glFailed` / `onGlUnavailable`; the comment there now
says why it may not move back. `npx vite build` is **not** a check for this — it
compiled clean both before and after.

**Verified:** `npx vite build` — clean, 129 modules; and no hook call now appears
after line ~6151 (`if (loading)`) in `AnimaticEditor.jsx`, which is the actual
guard against the fault above. **Not** opened in a browser by me, per the standing
"browser tests on request only" rule — the user saw the crash and reported it.

### 2026-08-21 — ✨ ANIMATE OPENS ON THE BOARD'S OWN PROMPT, AND OFFERS THE SHOT'S DIALOGUE (user-specified, with a screenshot of the dialog)

> "when i generte animate with Veo so user get this pop up So i want this time
>  user see prompt too so user control prompt if user want add some prompt and
>  dialouge like generted in last Storyboard panel show this type"

**THE BOX WAS PREFILLED WITH THE FRAME'S LABEL — "Shot 1" — WHICH IS A NAME AND
NOT A PROMPT.** The panel that clip was drawn from already carried a sentence
describing the shot and a list of who says what in it, and the user was retyping
both. It now opens on the panel's **description**, and the shot's spoken lines
are offered underneath.

⚠ **NO NEW ROUTE. `GET /animatics/{id}/frames/{frame_id}/panel` ALREADY ANSWERED
THIS QUESTION** — it is the free, owner-checked read the redraw pane
(`RegeneratePanelInline`) uses, and it already returned `description` / `camera` /
`location` / `title`. The whole server change is **`dialogue` on the same
response** (`AnimaticPanelSource`), filtered the way `_dialogue_lines` already
filters it for the voiceover: a line with no words is dropped here, not in the
UI, so one rule lives in one place. Adding a second "what does the board say
about this clip" endpoint would have been two owner checks to keep in step.

⚠ **THE DRAFT IS THE DESCRIPTION ONLY, WHICH IS WHAT `_starting_prompt` BUILDS
IN `server/videos.py`.** Two workflows animating the same panel must open on the
same wording. Camera and location are deliberately left out: they describe how
the STILL is framed, and handing them to Veo invites it to re-frame a shot it was
asked to move.

⚠ **THE PANEL READ IS ASYNCHRONOUS AND THE BOX IS FOCUSED THE WHOLE TIME.** Two
guards, and both are the difference between a help and a hazard: the response is
dropped unless `animatePanelReq` still names the frame it was started for (open a
second ✨ Animate while the first is in flight and it would otherwise fill the box
with the wrong shot), and the draft is written **only over the label we put
there** — anything the user has already typed outranks a draft that arrives late.
A shot with no board, or a board that has gone, simply keeps the label; this read
improves the prompt and is never a precondition.

**THE DIALOGUE IS A DECISION, NOT A DEFAULT — AND TICKING IT EDITS THE PROMPT
BOX.** The lines are drawn with `DialogueBox`, the same component the storyboard
uses, so a line looks the same in both places. ⚠ **"Have Veo speak these lines"
WRITES THEM INTO THE TEXT, it does not set a flag that gets bolted on at submit
time** — the dialog exists so what goes to Veo is what is on screen, and anything
appended invisibly would be the exact opposite of the ask. Unticking takes back
the block it added *and only that*: `animateSpokenRef` holds the exact text, and
an edited block no longer matches, so the user's words stay.
⚠ **AND TICKING IT TURNS SOUND ON.** Veo cannot say a line with `generate_audio`
off — it renders mouths moving in silence and bills for it. If sound is switched
back off by hand the block says so in `--warn`, because this is the one dialog in
this editor where money moves.
⚠ **THE IMAGE SIDE STILL KEEPS DIALOGUE OUT OF ITS PROMPTS** (see `Shot.dialogue`
— a drawing model renders the words as speech bubbles). Veo is the exception, and
the comment on the new schema field says so, so nobody "fixes" the panel prompt to
match.

- ⚠ **THE SHOT'S NAME MOVED ABOVE THE BOX, AND HAD TO** — same session,
  reported the moment the draft landed ("see you remove image name like Shot1").
  The box used to open on the frame's LABEL, so "Shot 1" was the one thing on
  screen naming what you were about to pay to animate; filling it with the
  board's description took that away and nothing replaced it. `.an-animate-shot`
  now carries the name in `--primary` with the board quiet beside it, above the
  box, in the same label-over-control rhythm as Quality / Size / Length. It reads
  `frames.find(...)` on every render rather than a value captured in
  `openAnimate`, so renaming a clip with the dialog open cannot leave it naming
  the shot by a name it no longer has. The "Drafted from…" line under the box
  dropped the board's name with it: **the board is named once**, and what is left
  there is the part that is not obvious — this is a draft, and typing over it does
  not touch the storyboard.
- **`maxLength` 1000 → 2000** on the prompt box: dialogue now lives in it, and a
  silent truncation of a paid prompt is the worst way to find that out.
- **`.an-name-modal` scrolls itself now** (`max-height: calc(100vh - 2rem)`).
  ⚠ `.modal-overlay` is a centred grid that does NOT scroll, so a card taller
  than the viewport loses its own footer off both ends — and the footer is where
  Cancel and the priced button are. This dialog is the tallest of them.

**Verified:** new `tests/animate_prompt_draft_check.py` — 27 checks, all passing,
covering the description draft, the dropped empty line, a silent shot, a shot
with lines but no description, a non-panel frame, and ⚠ **both cross-account
paths** (someone else's animatic 404s; a CRAFTED frame pointing at a board you do
not own returns empty wording, not the board's). `tests/animate_guard_check.py`
still passes — every spend guard holds. `npx vite build` clean. ⚠ **NOT opened in
a browser**, per the standing instruction.

### 2026-08-21 (later) — "New Project", "Untitled Project", AND A DEFAULT **Images** ROW (user-specified, with three screenshots)

> "first you chnage name New Animatics to New Project and when when i open new
>  Project sso show untitled Project
>
>  and add Image layer in default like other see image 1"
>
> — image 1 the timeline gutter (Text / Shapes / Video / Audio, no Images row),
> image 2 the editor topbar reading "Untitled animatic", image 3 the library's
> NEW ANIMATIC tile.

**THE PLACEHOLDER TITLE IS A SENTINEL, NOT A LABEL — DO NOT COMPARE AGAINST IT
WITH `===`.** `UNTITLED` is now `"Untitled Project"`, and the editor reads it
twice: `needsName` (Save on an unnamed project opens the save-as prompt instead
of writing) and `isEmpty` (an untouched project is DISCARDED on the way out, so
the library doesn't fill with blank rows — `tests/e2e_animatic.py` checks exactly
that). ⚠ **EVERY PROJECT MADE BEFORE TODAY CARRIES `"Untitled animatic"` IN THE
DATABASE**, so flipping the constant alone would have promoted all of them to
"named": Save would silently write the old placeholder to the library forever and
the prompt would never appear again. So the comparison moved into an exported
**`isUntitled(title)`** (`AnimaticLibrary.jsx`) that accepts the new string, the
`LEGACY_UNTITLED` list, and blank — and all three editor call sites now ask it.
Add to that list, never swap it. `tests/editor_board_import_check.py` still
builds its fixture with the OLD title on purpose: that is the legacy path's
regression guard.

- **The server's fallback matches** — `character_name=title or "Untitled Project"`
  in `server/animatics.py`. Only reachable from a direct API call (the New tile
  sends the title itself), but a mismatch there is a project the editor would
  treat as already named.
- **The tile is "New Project"**, its counter says "N projects created", and the
  empty-state line that pointed at **New Animatic** by name follows it — a
  sentence naming a button that no longer exists is worse than either wording.
- ⚠ **"Your Animatics" / "Recent Animatics" / "All Animatics" ARE UNCHANGED**, as
  is the editor's ← back button and the e2e title assertion. Flagged to the user
  rather than renamed: those headings name the LIBRARY, and the ask named the
  tile and the placeholder.

**AND THE DEFAULT "Images" ROW IS ALWAYS ON THE TIMELINE NOW**, beside the Text,
Shapes, Video and Audio rows that always were (`lanes` in `AnimaticEditor.jsx`).
It used to appear only once an image layer RECORD existed, so a new project
opened with four rows and nowhere obvious to drop a logo or a cut-in. ⚠ **THE
CODE ALREADY ASSUMED THIS ROW EXISTED**: `addLayer` numbers a new image layer
from **2** ("Images 2") precisely because the default row is supposed to be
holding the name "Images" — it was naming a row after a row that wasn't there.
It is `layerId: ""` and `removable: false`, the same shape as the default Text
and Shapes rows, which means every path already handles it: an overlay with no
`layer_id` lands on it (`clipLane`, `laneCount`), its ✕ EMPTIES rather than
deletes (`clearLane`), and `addToLane` / `dropAsset` / `moveClipToLane` all read
`lane.layerId || ""`. ⚠ **IT IS STILL A DIFFERENT THING FROM THE VIDEO ROW** — it
composites OVER the cut; Video is part of it. The gutter now reads Text · Shapes
· Images · Video · Audio.

**Verified:** `vite build` clean (129 modules). ⚠ **NOT opened in a browser** —
no dev server and no Playwright this pass, per the standing instruction that the
suite runs when asked.

### 2026-08-21 — "Stills track" LEAVES ＋ Add layer, "Video track" BECOMES "Video", AND THE WORKFLOW IS CALLED "Video Editor" (user-specified, with a screenshot of the open menu)

> "remove still tracker layer in add layer buttun not need same work image layer
>  and chnage name video tracker to Video
>
>  and change name my workflow Storyborad to Animatics to Video Editor"

**THE MENU HAD TWO DOORS INTO ONE ROOM.** ＋ Add layer listed a *Video track* and
a *Stills track* as separate kinds, but a picture row takes footage AND stills
alike — `ROW_TAKES.video`, the row's own ＋ and `dropAsset` all say so, and the
gutter has named every one of those rows "Video" since the row-naming pass. So
the two entries built rows that differ only in the word `kind` and in what the
first import happens to be. **`stills` is now off the menu**; the Video entry's
note says out loud that full-frame photos belong on it, and a ⚠ comment beside it
records why there is no second entry so it does not get re-added.

- **`kind: "stills"` IS STILL A LIVE ROW KIND — only the menu entry went.**
  `ROW_KIND.stills`, `rowOfKind("stills")` and `addPictureTrack("stills")` are
  untouched, because an image import with no row named still routes through them
  (`addAssets`, ~line 4551) and existing projects have rows carrying that kind.
  ⚠ **The consequence is asymmetric and deliberate**: a plain image drop can
  still MAKE a Stills row that you can no longer ask for by name. Folding that
  route onto the video row is a behaviour change, not a rename, so it was left
  alone — see Next Steps.
- **"Video track" → "Video"**, which is what the gutter, the Media pane's group
  and every other NLE already call it. The three picture-row entries and the
  `Images` entry below them are unchanged; **`Images` is still the OVERLAY** —
  it composites over the cut rather than being part of it, and that is the
  distinction the removed entry was blurring.

**AND THE WORKFLOW IS "Video Editor" EVERYWHERE A HUMAN READS IT** —
`Sidebar.jsx` (`WORKFLOWS`), `Home.jsx` (the recent-work group), `Landing.jsx`
(the workflow card), plus `README.md` and the Playwright suite's click target in
`tests/e2e_animatic.py`. ⚠ **THE ROUTE ID `storyboard-to-animatics` IS
UNCHANGED** — `App.jsx` branches on it in three places and `Home.jsx` keys its
group by it; renaming a nav id is a separate change with no user-visible payoff.
⚠ **AND THE LIBRARY IS STILL "Your Animatics"** (`AnimaticLibrary.jsx`, the
editor's back links, the e2e title assertion): that heading names the PROJECTS,
not the workflow, and the user renamed the workflow.

**Not run this pass:** no dev server, no Playwright, no build — the changes are
label edits plus one deleted array entry, and per the standing instruction the
browser suite runs when asked.

### 2026-08-20 — THE NAV RAIL COLLAPSES TO ICONS, LIKE ChatGPT's (user-specified, with four screenshots)

> "i want add sidebar like chatgpt when user close side so user get image 2 type
>  view like only icon
>
>  add fuction like this in my workflow screen in ui"
>
> — with ChatGPT's open panel (image 1) and its collapsed icon rail (image 2) as
> the reference, plus two shots of the toggle's tooltip ("Close sidebar",
> "Collapse sidebar Ctrl+B").

**THE COLLAPSED RAIL IS THE SAME DOM, NARROWED — NOTHING IS UNMOUNTED.** The
labels, the Soon badge, the live dots, the theme switch's track and the account
chip's two lines are hidden in CSS (`.sidebar.collapsed`), which is what keeps
the `title` on every row alive: at 68px the tooltip is the ONLY place a workflow
can say its name, so a conditional render that dropped the row's attributes
would have taken the collapsed rail's only labelling with it. Every row also
keeps its full height and its outline, so the thing you are aiming at does not
move when the rail narrows — only its text goes.

⚠ **THE STATE LIVES IN `App.jsx`, NOT IN `Sidebar.jsx`.** `.shell` is a
two-column grid (`280px 1fr`), so the rail and the page have to change width in
the SAME render — a `collapsed` flag owned by the sidebar would narrow the aside
while the grid track stayed 280px and left a dead column beside it. App holds it,
stamps `.shell.nav-collapsed` (`grid-template-columns: 68px 1fr`), and passes
`collapsed` + `onToggleCollapse` down. The TRACK is what transitions (0.18s), not
the aside's width: animating the aside alone makes the page edge jump a frame
ahead of the rail.

- **Remembered per browser** — `localStorage` key `cas_nav_collapsed`, read at
  boot the way `theme.js` reads its own. Both the read and the write are wrapped
  in `try` so a storage-disabled browser starts expanded instead of failing to
  boot.
- **Ctrl/Cmd+B** toggles it from anywhere, and the toggle's tooltip is where that
  is written down. ⚠ The handler **bails while an INPUT / TEXTAREA / SELECT /
  contenteditable has focus**, so it can never fight a text control's own bold
  binding — the editor is full of name fields.
- **The toggle sits in the brand row and stays in the same corner in both
  states**, so the button that closed the rail is the button that reopens it. It
  draws `Icon.jsx`'s new `sidebar` glyph — a panel with its left rail — the same
  in both directions, because a flipping chevron moves the target under the
  cursor you just clicked with.
- **Two things had to become spans to be hideable**: Home's bare `Home` text node
  and Upgrade's bare `Upgrade` — CSS cannot hide a text node, so they are
  `.sb-item-label` / `.sb-upgrade-label` now.
- **The "WORKFLOWS" heading is swapped for a hairline** (`.sb-divider`) when
  collapsed, rather than squeezed: the group still reads as a group, and this is
  the one label that IS conditionally rendered, because a heading nobody can read
  is not a heading.
- **The brand avatar is hidden in the collapsed rail** — the footer chip already
  carries one, and two faces in a 68px column read as a bug.
- **Under 820px the collapse button is `display: none`** and the collapsed track
  is overridden back to `1fr`: at that width the rail is already a full-width
  block above the page, so narrowing it would hide the labels for nothing.

⚠ **THE RAIL IS 280px WIDE NOW, UP FROM 264px, AND THAT WAS FORCED.** The brand
row carries the logo, the name, the account avatar AND the new toggle; at 264
"Character Studio" ellipsised to "Character St…", and the app's own name is the
one label that must not be clipped. The 16px plus a tightened brand row (gap
0.55→0.35rem, side padding 0.6→0.3rem, logo 1.4→1.3rem, a 26px toggle) is what
buys it back — **measured in a browser, not guessed**: `scrollWidth` 125 vs 125.1
available, i.e. it now fits at its natural width with the slack in the row rather
than in the label. Anything added to that row again will clip the name first.

**Files:** `client/src/components/Sidebar.jsx` (collapsed prop + toggle + the two
new label spans + the divider), `client/src/components/Icon.jsx` (`sidebar`
glyph), `client/src/App.jsx` (state, `localStorage`, Ctrl+B, `.shell` class),
`client/src/styles/shell.css` (280px track, `.nav-collapsed`, `.sb-collapse`,
`.sb-divider`, the `.sidebar.collapsed` block).

**Verified:** `npx vite build` clean (only the pre-existing >500kB chunk
warning), and the rail was RENDERED HEADLESS in all four states — dark/light ×
open/collapsed — from the compiled `dist` stylesheet and read back, which is how
the clipped brand name was caught and confirmed fixed. ⚠ **NOT opened in the real
app**: no dev server, no login, no Playwright suite this pass, so the Ctrl+B
handler, the `localStorage` round-trip and the grid transition have been read and
built but not exercised in a live browser.

### 2026-08-20 — WHITE LABELS ON COLOURED STROKES, SHAPES GET A VIOLET, AND THE SIX TOOL LETTERS ARE ICONS (user-specified, with three screenshots)

> "i want change + add Layer buttun, Animate with veo, text, colour card and
>  Voiceover buttun text color keep sholid white not buttun color
>
>  and you not add shapes storck color violet color and change clip too with same
>  layer color petran
>
>  look image 2. i want you add icon replace V, C, B, N, H, Z leter. see iamge 3 i
>  giive you ref but you not copy you add your own style and keep comman icon."
>
> — and mid-turn, with a fourth screenshot: "change text color this number and
>  text color is sholid white"

**1. THE COLOUR IS THE STROKE; THE WORDS ARE WHITE.** ＋ Add layer, the four green
tools, and — after the follow-up — every layer head's NUMBER and NAME. Green ink
on a green wash was a low-contrast button that also read as disabled, and the same
was true one column left: colouring the row's name made the one thing you actually
read the hardest thing to read, and a muted name on a stroked row looked switched
OFF next to the `.off` state that means exactly that.
- ⚠ **`var(--text)`, NEVER `#fff`.** It is white in the dark theme, which is what
  was asked for, and near-black in the light one — a literal white would be
  invisible on a white panel. Checked in both.
- The icons inside Text and Colour card follow for free: `Icon.jsx` draws in
  `currentColor`.
- This also retired `--lane-ink`, which existed for one revision only to keep the
  Shapes numeral readable. With every numeral on `--text` there is nothing left
  for it to solve.

**2. SHAPES ARE VIOLET, ON BOTH SIDES OF THE ROW.** ⚠ **THIS OVERRULES A RULE THAT
WAS WRITTEN DOWN TWICE**, in `theme.css` and in `animatic-lanes.css`: a shape clip
stayed neutral because `.tl-shape-swatch` already carries the shape's OWN colour,
and two unrelated colours in one box was the thing to avoid. Both comments now say
what happened instead of still claiming the old rule — the argument was thinner
than it looked, because **the swatch says WHICH SHAPE and the bar says WHICH ROW**,
and a neutral bar answered the second question with nothing. The swatch stays; it
is a dot and the tint is the whole bar, so they do not compete.
- `--clip-shape-tint` / `--clip-shape-edge` are the sixth content hue, and
  `.tl-shape` now reads `--clip-tint` / `--clip-edge` with its old neutral pair as
  the fallback — the same arrangement `.tl-bar` has always had, so `.sel` and
  `.over-end` keep their specificity.
- ⚠ **VIOLET AND THE VEO PURPLE ARE THE CLOSEST PAIR ON THIS TIMELINE** (~28° of
  hue in dark, ~30° in light, and they can sit one row apart). The violet is
  pushed toward magenta and run at a higher saturation; the purple is bluer and
  softer. Rendered side by side before committing — if either is ever retuned,
  check them together again.

**3. V / C / B / N / H / Z ARE SIX DRAWN ICONS.** Pointer, scissors, ripple,
rolling, hand, magnifier-with-a-plus — in `Icon.jsx`'s own 24-box with `STROKE`,
not traced from the reference that was handed over ("you not copy you add your own
style and keep comman icon").
- ⚠ **THE SHORTCUT NOW LIVES ONLY IN THE TOOLTIP.** The letter on the button was
  what taught the key; every `title` is still "<label> (<key>) — <hint>" and that
  is now load-bearing. Each button also gained an `aria-label`, because the SVG is
  `aria-hidden` and the button would otherwise announce as "button".
- ⚠ **TWO OF THE SIX ARE NOT COMMON ICONS, BECAUSE THE TOOLS ARE NOT.** Ripple
  and rolling have no everyday glyph, so they are drawn as what they DO and as a
  PAIR: ripple is a fixed edge, an arrow and a clip sliding back onto it (the track
  gets shorter); rolling is one bar whose outer edges never move with a seam in the
  middle (the track stays the same length). Redraw one and you redraw both.
- ⚠ **ROLLING WAS REDRAWN AFTER LOOKING AT IT.** The first version put a
  two-headed arrow across the bar; its shaft ran straight over the seam and the
  whole thing merged into one diamond at 18px — which is the size the button is,
  so it is the size that decides. Four variants were rendered and compared; the
  shipped one drops the shaft and keeps two shaftless chevrons, and it is the only
  one where the SEAM survives.
- ⚠ **`.an-tool-ico` EXISTS BECAUSE `.an-tool` IS SIZED FOR A CAPITAL LETTER**
  (`min-width` + side padding). An 18px icon is wider than that content box, so the
  six came out oblong beside the square ↶ / ↷ / 🧲 / 🥁 next to them. Fixed
  width, no padding. The icon is also sized in `rem` not `em`: the button's
  font-size is 0.72rem, so an `em` icon rendered at ~12px.

**4. AND THE CLIP LABELS WENT THE OTHER WAY — PASTEL GREY.** Straight after the
white landed: "keep clip text name keep pastel grey not white". ⚠ **THE TWO
COLUMNS ARE READ DIFFERENTLY, AND THAT IS THE WHOLE ANSWER.** A layer NAME is read
once, to find the row, so it is `--text`. A clip label is repeated forty times
across a row of shots, and at that count white labels are what you see INSTEAD of
the bars — grey lets the bar's content colour come forward, which is the only
reason it has one. `--muted` on `.tl-bar-label`, `.tl-text-label` and
`.tl-shape-label` (which the overlay clip borrows), the same token
`.tl-bar-secs` already used, so a bar's name and its duration now match — they do
not need a colour between them, one is long and left-aligned and the other is
short and right-aligned. ⚠ **THE `.sel` INK RULES ABOVE THEM STILL WIN AND MUST**:
a selected bar is gold and its label has to go `--gold-ink` or it disappears.

**Files:** `client/src/components/Icon.jsx` (six new paths),
`client/src/components/AnimaticEditor.jsx` (the tool buttons render `<Icon>`;
`TOOLS`' contract note), `client/src/styles/theme.css` (`--clip-shape-*`, violet
`--lane-edge-shape`, both themes; the palette note rewritten),
`client/src/styles/animatic-lanes.css` (`.tl-shape`'s colour rule; the shape-head
note), `client/src/styles/animatic-text.css` (`.tl-shape` reads the vars;
`.tl-layer-num` ink), `client/src/styles/animatic-editor.css` (white labels on
＋ Add layer, the four tools, and `.tl-layer-name`),
`client/src/styles/animatic-tools.css` (`.an-tool-ico`),
`client/src/styles/animatic.css` (`.tl-bar-label` grey).

**Verified:** `npx vite build` clean, and — unusually for a CSS pass — **this one
was actually looked at.** Two throwaway pages were rendered headless and read
back: the six icons at 72px and at their real 18px, and a mock of the head bar
plus seven gutter rows built from **the compiled `dist` stylesheet** with the real
class names, shot in BOTH themes, with `getComputedStyle` read back to confirm the
light theme was really applying (the first downscaled shot looked like it was not,
and that was a misread). That is what caught the rolling icon and what confirms
Shapes-violet against Storyboard-video-purple. The grey labels were shot the same
way — nine bars, one of them selected, both themes — to confirm the gold clip's
dark ink survived the change. ⚠ **STILL NOT THE REAL EDITOR** —
the Playwright suite runs only when asked for, so nothing here was exercised
against real project data.

### 2026-08-20 — A LAYER HEAD IS STROKED IN ITS OWN ROW'S COLOUR, AND THE FOUR MAKE-SOMETHING BUTTONS ARE GREEN (user-specified, with a screenshot)

> "i want we add color Strock in Layer buttun and Animate with veo, text, colour
>  card and Voiceover buttun too i want layer and buttun look like some highlight
>
>  and you keep layer buttun Strock color same of layer clip color like video layer
>  clip color is now pestal Orange so you keep video layar strock little Dark
>
>  And Animate with veo, text, colour card and Voiceover buttun strock same of tree
>  like green"

**1. THE GUTTER AND THE TRACKS NOW SAY THE SAME THING IN THE SAME COLOUR.** Every
layer head takes the hue of the clips on its row — Video orange, Story..Image and
Stills pink, Story..Video purple, Text yellow, Captions mint, Audio gold — so you
can find the orange row without reading five names.
- ⚠ **ONE MAPPING, IN `laneHue` (`Timeline.jsx`), AND IT MUST KEEP AGREEING WITH
  `clipRowKind`.** A picture row's hue comes from its STRICT KIND (`rowKind`), not
  from `kind`: all four picture rows draw `frames`, and the strict kind is exactly
  the distinction the colours carry. A head stroked orange above a row of purple
  renders would be worse than no stroke at all, which is why the answer is not
  computed twice.
- ⚠ **THE STROKES ARE THEIR OWN TOKENS (`--lane-edge-*`), NOT `--clip-*-edge`
  REUSED.** Two reasons and both matter: those pastels are translucent FILLS and
  wash out to grey when borrowed for a 1px border, and the head must not read as
  another clip — it is the thing that NAMES the row. Deeper and opaque, which is
  also what "keep video layar strock little Dark" asked for. Both themes, in
  `theme.css`, and one step deeper again in light where they sit on white.
- ⚠ **THE NUMBER CHIP CARRIES THE HUE TOO, and that is what makes it a
  HIGHLIGHT.** A 1px border on its own is a hairline; one filled chip per row is
  what the eye catches down a column of eight. It fills with the row's `-tint-alt`
  and inks with the stroke.
- ⚠ **SHAPES AND AUDIO ARE STROKED WITHOUT CLAIMING A CONTENT HUE.** The clip
  palette leaves both alone on purpose (a shape carries the shape's OWN colour in
  its swatch, audio carries a waveform), so audio takes the gold its clips are
  already bordered with and Shapes takes a lifted grey — the colour a shape clip
  is actually drawn in. Every head is stroked, as asked, and no row claims a hue
  it has no right to.
- ⚠ **SHAPES NEEDED A SEPARATE `--lane-ink`.** A stroke only has to be VISIBLE
  against the panel; a numeral has to be READABLE inside the chip, and that grey
  fails it in both themes (near-black on near-black, then near-white on white).
  It is the one hue that overrides the ink.
- Every rule assigns custom properties and nothing else — the same discipline as
  the `.tl-bar.is-*` block it sits under, and for the same reason: `.off`,
  `.locked` and `.sel` keep exactly the specificity they had. Fallbacks are the
  old plain values, so a lane `laneHue` cannot name is plain, not broken.

**2. ✨ ANIMATE WITH VEO · T TEXT · ▣ COLOUR CARD · 🎙 VOICEOVER ARE ONE GREEN
SET.** Green stroke, faint green wash, green ink (the icons are `currentColor`, so
they follow), against ＋ Add layer's gold beside them — the row now reads as "the
things that MAKE something" next to "the thing that makes a ROW".
- ⚠ **THE SELECTOR IS THE CONTAINER (`.tl-add-tools .btn`), NOT FOUR CLASSES.**
  Whatever the editor hands in as `addTools` is in this set by definition; a fifth
  tool should not have to remember to opt in, and three of the four have no class
  of their own to hang it on.
- ⚠ **`--tool-*` IS NOT `--clip-caption-*`, AND MUST NOT BE COLLAPSED INTO IT.**
  The captions row's mint is a CONTENT colour, this is a CONTROL colour, and both
  are on screen at once — the captions head sits a row under this bar. The tool
  green is deeper (a tree, as asked) and that difference is the only thing keeping
  them from reading as one signal.
- ⚠ **THE HOVER RULE RESTATES EVERYTHING AND CARRIES `:not(:disabled)`**, because
  `.btn:hover:not(:disabled)` in `base.css` outweighs a plain
  `.tl-add-tools .btn:hover` — without it these four would go GOLD on hover, which
  is the timeline's selection colour. The hover is a ring in their own green
  instead, since the border cannot move. And `:disabled` goes neutral as well as
  faint: two of the four are disabled a lot of the time (no shot to animate, no
  board dialogue to read) and a green that stays legible looks pressable.

**Files:** `client/src/styles/theme.css` (`--lane-edge-*`, `--tool-*`, both
themes), `client/src/components/Timeline.jsx` (`LANE_HUE` + `laneHue`, the
`tl-hue-*` class on the gutter row), `client/src/styles/animatic-lanes.css` (the
new ROW COLOUR block beside the clip one), `client/src/styles/animatic-text.css`
(`.tl-gutter-row`'s border and `.tl-layer-num` read the variables),
`client/src/styles/animatic-editor.css` (the four green tools).

**Verified:** `npx vite build` — clean, and the emitted CSS was read back to
confirm all seven `.tl-hue-*` rules and the three `.tl-add-tools .btn` rules
survive minification with their `var()` chains intact. ⚠ **NOT OPENED IN A
BROWSER** (the Playwright suite runs only when asked for), so **the contrast
judgements above are reasoned, not measured** — the four to look at first are
Captions mint and Audio gold in DARK (the two dimmest strokes) and Shapes in BOTH
(the only row with a separate ink).

### 2026-08-20 — ✨ ANIMATE WITH VEO JOINS THE TIMELINE'S ADD ROW, AND THE GUTTER NUMBERS ITS ROWS INSTEAD OF DRAWING A GLYPH (user-specified, with three screenshots)

> "Animate with Veo Buttun i want one more place for user confort you arange like
>  this in timeline : + add layer, Animate with Veo and text, colour card and
>  Voiceover
>
>  i want remove layer icon and i want add Number like 1, 2, 3, 4, 5 and if user
>  add layer then automatic show number 6. 7. like increase"

**1. A SECOND WAY TO ✨ ANIMATE WITH VEO, IN `addTools`.** The only one before this
sat in the Properties pane's Footage group, which meant selecting the shot, finding
the group and scrolling to it — while the shot you were animating was under the
playhead. It is now first in the timeline's head row, in the order asked for:
`＋ Add layer · ✨ Animate with Veo · T Text · ▣ Colour card · 🎙 Voiceover`.
- ⚠ **IT WIDENS THE WAY IN, NOT WHAT MAY BE RENDERED.** Both buttons call the one
  `openAnimate`, which opens the priced dialog and renders nothing — the rule every
  paid path here follows. What is refused and what it costs stays in
  `_animate_targets` / `_estimate_animate` (`server/animatics.py`); no guard was
  copied, loosened or duplicated on the client.
- ⚠ **IT NEEDED A TARGET, BECAUSE A TOOLBAR HAS NO SELECTION TO LEAN ON.**
  `veoTarget = selectedFrame || currentFrame` — the selected shot if there is one,
  else the shot under the playhead, which is the same rule `＋ Text` follows, so
  every button in that row means "this shot" and means it the same way. Disabled
  (with a tooltip saying why) when there is no shot at all or the server is busy.
- Its label still changes to **"✨ Render again with Veo"** once that shot has a
  ready render (`veoTargetClip`), for the reason `FrameProperties` gives: paying a
  second time has to be a deliberate act and must never look like a retry.
- Plain `btn small`, deliberately NOT the `.an-add-text` / `.an-add-card` weight —
  those two are the pair that makes a clip out of nothing and costs nothing. This
  one spends, like 🎙 Voiceover beside it.

**2. THE GUTTER OPENS EVERY ROW WITH ITS NUMBER.** `LANE_ICON` is gone — the
per-kind glyphs (🎞 / 🖼 / T / ◆ / ♪) are replaced by the row's position in
the stack, `1` at the top (the row drawn over everything else, which is the order
this gutter has always been in).
- ⚠ **IT IS THE MAP INDEX, NOT A FIELD ON THE LANE.** That is the whole of "if user
  add layer then automatic show number 6, 7": a sixth row is 6 the moment it
  exists, and deleting row 2 makes what was 3 into 2. A stored number would go
  stale on the first delete and print "Layer 4" second in a stack of five.
- ⚠ **IT IS NOT AN ID.** Every handler on the row still goes by `lane.key`.
- `lane.icon` is read nowhere now, so the captions lane's `icon: "❝"` went with
  it rather than being left as a field nothing looks at. What a row IS was already
  said twice over by its NAME beside the number and by the colour of the clips on
  it; its POSITION had nothing saying it at all, and a number is what a track head
  is called by in every NLE ("put that on 3").
- `.tl-layer-ico` → `.tl-layer-num`, same box so nothing in the column moved, plus
  `min-width` instead of `width` (row 10 is two digits and a fixed box clipped it)
  and `tabular-nums` so 1 and 7 are the same width down a long stack.

**Files:** `client/src/components/AnimaticEditor.jsx` (`veoTarget`,
`veoTargetClip`, the button at the head of `addTools`, the captions lane's dead
`icon`), `client/src/components/Timeline.jsx` (`LANE_ICON` removed, the gutter's
`laneIndex`, `.tl-layer-num`), `client/src/styles/animatic-text.css`
(`.tl-layer-num`), `client/src/styles/animatic-editor.css` (the dimmed-row rule
follows the rename).

**Verified:** `npx vite build` — clean, 129 modules. ⚠ **NOT OPENED IN A BROWSER**
(the Playwright suite runs only when asked for) and **no regression check was
written for either change**: both are placement, and the one thing worth asserting
— that pressing the new button spends nothing on its own — is already covered by
`tests/animate_guard_check.py` against the server it would have to go through.

### 2026-08-20 — THE ROW ✕ ASKS BESIDE ITS ROW, TAKES THE CLIPS WITH IT, AND A VEO RENDER IS PURPLE (user-reported, with three screenshots)

> "when i delete Story..Video 2 layer that time dropdown msg appair in below so my
>  layer buttun goes up and my time clip layer still so this look not good so i
>  want you show dropdown like layer of side like in clip layer side not below.
>
>  second you look image 3 when i delete layer. so only delete layer not clip and i
>  want delete clip too
>
>  third keep add Video veo video clip color like pestal prupel"

**1. THE CONFIRM OPENS BESIDE THE ROW, OVER THE TRACKS.** ⚠ **AND THE BUG WAS NOT
WHERE IT OPENED — IT WAS WHAT OPENING IT DID.** The popover hung below its row
INSIDE `.tl-gutter-clip`, which is `overflow: hidden`, and the labels are kept
level with the tracks by a TRANSFORM on `.tl-gutter-rows` (`readView`) rather than
by scrolling. So when the Delete button took focus, the browser scrolled that
hidden box to bring it into view — and every NAME slid up while every TRACK stood
still. That is the "layer buttun goes up and my time clip layer still" in the
report, and it is invisible to any check that only looks at what the popover says.
Three parts, and all three are needed:
- ⚠ **ONE POPOVER, IN `.tl-cols`, NOT ONE PER ROW IN THE GUTTER.** That box is the
  only ancestor spanning both columns that clips nothing, so a popover in it can
  sit over the track lane beside its row. `.tl-cols` gained `position: relative`
  for it; `.tl-layer-confirm` is `left: calc(var(--tl-gutter-w) + 1rem)` with a
  caret pointing back at the row.
- ⚠ **ITS `top` IS MEASURED OFF THE ROW, EVERY RENDER** — a layout effect with no
  dependency list, reading `[data-lane-row="<lane key>"]`. Deriving it from the
  lane's index and `--tl-track-h` would be a second copy of the timeline's
  vertical geometry and wrong for the whole of a vertical zoom (the same rule
  `laneAtPoint` follows). Scrolling re-renders, so re-measuring is what keeps it
  on its row; it is clamped to the pane so the top row's confirm cannot open half
  off the top.
- ⚠ **`focus({ preventScroll: true })`, NEVER `autoFocus`**, and `readView` now
  holds `.tl-gutter-clip.scrollTop` at 0 — a guard for every other control in
  there, since focusing any of them can scroll a hidden box.
- `laneDelete(lane)` is new, because the popover no longer sits inside the row
  whose variables it read: the button, its tooltip and the confirm now agree from
  a distance through one function.
- ⚠ **AND `clearLane`'S `window.confirm` IS GONE** — a second question about the
  same press, in the browser's own styling, which is exactly what "same place
  dropdown" was asked to replace. The popover is its only caller, so nothing lost
  a guard; while there, it picked up the `pruneTransitions` + `selectOnly({})` that
  every other delete path does and it had been skipping.

**2. DELETING A PICTURE ROW DELETES ITS CLIPS.** ⚠ **THE CONFIRM HAS ALWAYS SAID
"The row and the 1 clip on it"** and `removeLayer` dropped them to track 0 instead
— so the clip did not go, it MOVED, and reappeared on a row it had never been put
on (image 3). ⚠ **AND IT IS THE MEDIA LIBRARY THAT MAKES DELETING THEM SAFE.** The
old behaviour was argued from cost: a board panel, an upload, a paid Veo render are
the most expensive things on this timeline to lose. Since `assets.js` the SOURCE
outlives the clip, so what is deleted here is the placement — the notice says so
("the sources are still in Media, so you can drop them back in"), and dragging the
card back now lands it on a row of its own kind. It also does the two chores every
other frame delete does and this one skipped: `pruneTransitions` and
`selectOnly({})`.

**3. A VEO RENDER IS PASTEL PURPLE.** ⚠ **IT WAS DRAWN PINK — THE SAME PINK AS THE
PANEL IT WAS MADE FROM.** The bar's colour came from `frameOrigin`, which answers
"board" for a render (it keeps the panel's `src`), and pink is the not-video case;
so the one clip on this timeline that COST MONEY wore the colour of the one that
did not. `--clip-veo-tint` / `-alt` / `-edge` in `theme.css` (both themes, pastel
in dark and deeper in light like the other four), `.tl-bar.is-veo` in
`animatic-lanes.css`, and the class comes from `clipRowKind(f) === "board_video"` —
the strict-rows question, already answered in one place — rather than from a fifth
copy of "is this an animated panel".

**Files:** `client/src/components/Timeline.jsx` (`laneDelete`, the popover moved out
of the gutter, the positioning effect, `gutterClipRef`, `is-veo`),
`client/src/components/AnimaticEditor.jsx` (`removeLayer`),
`client/src/styles/animatic-editor.css` (the popover, side-anchored, with a caret),
`client/src/styles/animatic-text.css` (`.tl-cols` positioning context),
`client/src/styles/theme.css` (the Veo purple, both themes),
`client/src/styles/animatic-lanes.css` (`.tl-bar.is-veo`).

**Tests:** `tests/editor_media_row_routing_check.py` grew a section for all three —
⚠ and the assertion for the first one is **`probe.drift()`, one number per row**
(gutter row top minus its lane's top), because "the popover is over there now" is
not the thing that was broken: every label being beside its own track after the
confirm opens is. It also checks the confirm is `beside` and not `below`, that it
stays inside the pane, that Delete takes the clip AND leaves the other rows alone,
and that the render's bar carries `is-veo`. `tests/editor_media_bin_check.py`'s
`rowByName` probe now finds the confirm by `data-confirm` rather than inside the
row. ⚠ **NEITHER FILE HAS BEEN RUN** — the browser suite runs only when the user
asks — so all of it is unverified beyond `npx vite build`.

### 2026-08-20 — A MEDIA CARD GOES BACK ON THE ROW IT CAME FROM, AND THE GUTTER SAYS WHICH ROW THAT IS (user-reported, with a screenshot)

> "see when i generate storyborad a image to video so video come in Storyborad
>  video layer but then i delete veo video clip in timeline so then next i do
>  media panel and then i select Veo video clip and drang and drop on same
>  storyboard video layer but i can't drop in Storyboad layer but i drop in Video
>  layer this is happng now … i want one thing when one time come clip in media
>  penal so i drop and drag in particuler layer like veo vidio go in Storyboerd in
>  layer any time … you change name Storyborad video to Story..Video and
>  Storyborad Image to Story..Image … i see my storyborad namke come and show in
>  layer but this not happen i want you keep Story..Image … and if anme show not
>  proper so you increase layer width like all fit ans show look good"

Two bugs and one rename, all about the same row.

**1. `ROW_TAKES` IS ABOUT FILES, AND IT WAS BEING ASKED ABOUT LIBRARY CARDS.**
⚠ **BOTH BOARD ROWS TAKE NOTHING BY DESIGN** — `board_image` is filled by the
storyboard import and `board_video` by ✨ Animate, and refusing uploads on them is
the whole point of the strict rows. Asking that same table about a card out of the
Media pane refused the one drag that had every right to land there (a Veo render
being put back after its clip was deleted) and plain **Video accepted it instead,
because a render genuinely IS video**. A kind can never answer "which row": two
cards in the library are `video` and they belong on different rows.
- `cardRowKind(kind, fromBoard)` — new in `client/src/animatic/scene.js`, and
  **`clipRowKind` now delegates to it**, so the row a CARD lands on and the row the
  CLIP made from it belongs on are one derivation. Two copies is how a drop lands
  somewhere the next drag refuses to move it away from — which is exactly what had
  happened between `placeAsset` (＋ / double-click, right) and `dropAsset` (the
  drag, wrong).
- ⚠ **A NEW EMPTY MARKER, `application/x-anim-board`**, stamped by `MediaBin.jsx`
  beside the kind marker. `getData` is blank until the drop in every browser and
  only the TYPE LIST is readable during `dragover` — the same trick the kind
  markers already use — so "did this come out of a storyboard?" has to be a type,
  not a field in the payload. `dragFromBoard` reads it; `laneTakes` then asks
  `cardRowKind` instead of `ROW_TAKES`, so **the right row lights up and the wrong
  one shows the no-entry cursor mid-drag**, which is the half the user sees first.
- ⚠ **ONLY THE LIBRARY STAMPS IT**, so nothing else changed meaning: files, shapes,
  effects and a clip being re-timed all read `false` and go on being judged exactly
  as before.

**2. THE TWO STORYBOARD ROWS ARE "Story..Image" AND "Story..Video".** `ROW_KIND`
gained a `short` name for the gutter; `name` stays the full phrase for prose (a
notice saying which row something belongs on has room for it). ⚠ **AND A BOARD ROW
IS NEVER NAMED AFTER THE BOARD**: `doBoardImport` used to pass the storyboard's
title to `pictureLane`, so the gutter read "TTBB E…" for the one row whose KIND
matters most, and `videoTracks` now blanks a stored name on either board row so
every project already saved reads the canonical one too. There is no rename in the
UI, so a stored name on those two rows is never something the user typed — it is
that board title, or an older build's long label.

**3. `--tl-gutter-w` 11rem → 13rem** (9.5rem at the 720px breakpoint). Even at 12
characters the two labels did not fit, and truncated they both read "Story.." —
the one thing a row label must not do. The name and the four row controls are ONE
budget and the controls are the fixed half, so all of the extra width goes to the
name.

**Files:** `client/src/animatic/scene.js` (`cardRowKind`, `isBoardRow`,
`clipRowKind` delegating), `client/src/components/MediaBin.jsx` (the board
marker), `client/src/components/Timeline.jsx` (`dragFromBoard`, `laneTakes`),
`client/src/components/AnimaticEditor.jsx` (`ROW_KIND[*].short`, `rowKindName`,
`videoTracks`, `placeAsset`, `dropAsset`, `doBoardImport`),
`client/src/styles/animatic-text.css` (the gutter width).

**Tests:** `tests/editor_media_row_routing_check.py` — new, and every refusal is
read twice (the timeline is unchanged AND the row said `.drop-no` under the
pointer), because a refused drop looks exactly like a drag that missed. Its drag
is dispatched in THREE steps rather than one: `dropAt` is React state set from a
continuous-priority drag event, so the class is not on the lane by the time
`dispatchEvent` returns. ⚠ **IT HAS NOT BEEN RUN** — the browser suite is only run
when the user asks — so it is unverified.
`tests/editor_board_import_check.py` had its "the gutter calls it after the board"
check INVERTED (that assertion WAS the bug), and `tests/editor_media_bin_check.py`
now addresses the row as "Story..Image".

### 2026-08-20 — THE MEDIA PANE IS A LIBRARY, A ROW CAN BE LOCKED, AND ITS ✕ ASKS FIRST (user-specified, with a screenshot)

> "i see when i upload/generate Veo video and then i delete in time so i see in
>  media panel also delete … i want when user delete video, storboard image, veo
>  video, audio and shapes in timeline after upload in media so only clip delete
>  in timeline not delete in media panel i want stay in media panel so user need
>  deleetd cipl again so user go media panle and drang and drop in perticular
>  layer … i wnat you add lock icon in layer … x cross icon when user click x
>  buttun so user get same place dropdron with deleted layer masg then user click
>  delete and cancel"

Three features, one report. All three are answers to "what happens when I press a
delete", so each one's fix is the other two's regression risk — which is why they
share a test.

**1. THE LIBRARY AND THE TIMELINE ARE TWO LISTS NOW.** ⚠ **THE MEDIA PANE USED TO
*BE* THE TIMELINE** — it listed `frames` grouped by `frameOrigin` — so deleting a
clip deleted the only record that its source had ever been added, and the only way
back was to upload it again. A **Veo render was unrecoverable**: re-making it costs
money. So an ASSET is a source and a clip is a placement of one:
- `client/src/animatic/assets.js` — new, pure, no React. `assetKey` (identity, by
  SOURCE, so importing a board twice is one card), `assetOrigin` (which section),
  `assetUrl`, `clipFromAsset`, `libraryFromProject`, `mergeAssets`, `assetForSave`.
- `AnimaticAsset` in `server/schemas.py`; `assets` on the project and on the save
  request; `MAX_ANIMATIC_ASSETS` (1000 — deliberately above the frame cap, because
  the library outlives the timeline).
- `client/src/components/MediaBin.jsx` — new. Reuses every `.fs-*` class so it is
  the same object to the eye; what differs is semantic (no reorder, no typed hold,
  and a drag COPIES).
- Deleting a clip now touches only the clip. A card's own ✕ removes the source AND
  its clips, **with no confirm** — asked for directly ("no dropdwon delete and
  cancel option not need here"); the count is on the tooltip before the press.

⚠ **`None` AND `[]` ARE DIFFERENT ANSWERS FOR `assets`, AND THE FIELD IS `| None`
FOR THAT REASON.** `None` = saved before the library existed → the editor derives
one from the frames and audio on first open. `[]` = emptied on purpose. With
`default_factory=list` both arrive as `[]`, the backfill cannot tell them apart,
and emptying the library puts every card back on reload — the ✕ looks broken. The
derivation lives in `assets.js` only; a Python twin would be a second thing to
keep in step.

⚠ **A LIBRARY CARD IS SERVABLE WITH NO SAVE AND NO CLIP.** New route
`GET /animatics/{id}/panel/{board}/{index}` — content-addressed, where
`get_frame_image` is id-addressed and resolves through the SAVED frame list. That
is the same trap `doBoardImport` hit two entries ago, avoided by construction this
time: an upload is served by upload id and a panel by (board, index), so the
client builds every url itself (`assetUrl`, twin of `_asset_url`).

**2. 🔒 EVERY ROW HAS A PADLOCK, beside the eye.** ⚠ **LOCK AND HIDE ARE DIFFERENT
IDEAS**: the eye takes a row out of the VIDEO (monitor and export); the lock
changes nothing about the film and takes the row out of REACH — nothing on it can
be moved, trimmed, razored, dropped onto, selected or deleted. So
`settings.locked_lanes` is read by the EDITOR and deliberately ignored by
`animatic_render.py` and the exporter. Same token vocabulary as `hidden_lanes`
(`laneToken`), so an audio row can be neither — it has no stable token.
Enforcement is in `Timeline.jsx`, because a lock stops GESTURES and the gestures
live there (`laneLocked`, one gate); the editor guards only what it owns —
`deleteFrame`, `deleteMany`, the keyboard razor, `placeAsset`.

**3. THE ROW ✕ ASKS FIRST, WHERE IT WAS PRESSED.** A popover anchored to the row
(`.tl-layer-confirm`), not a modal — on a gutter of ten rows the connection between
the question and the row is the only thing that makes it answerable. It names the
row and COUNTS what goes with it ("The row and the 42 clips on it"), Cancel then
Delete, Escape and an outside press both close it.

⚠ **THE CLUSTER IS FOUR SLOTS NOW, NOT THREE** — `hide · lock · add · remove`,
`repeat(4, var(--tl-act-w))`. The count lives in that one `repeat()`.

**Files:** `client/src/animatic/assets.js` (new),
`client/src/components/MediaBin.jsx` (new), `AnimaticEditor.jsx`, `Timeline.jsx`,
`Icon.jsx` (`lock` / `unlock`), `useAnimaticProject.js`, `api.js`,
`styles/animatic-editor.css`, `server/schemas.py`, `server/animatics.py`,
`server/config.py`, `tests/asset_fields_check.py` (new),
`tests/editor_media_bin_check.py` (new), `tests/editor_board_import_check.py`.

**Verified:** `npm run build` clean; `python -c "import server.main"` clean.
`tests/asset_fields_check.py` (new, 21 checks — node parity against
`AnimaticAsset`) and `tests/editor_media_bin_check.py` (new, 30 checks — the real
editor) both pass, as do `frame_save_fields_check`, `render_parity`,
`picture_tracks_check`, `hidden_lane_check`, `selection_check`, `video_clip_check`,
`editor_board_import_check`, `editor_veo_attach_check`,
`editor_picture_tracks_check`, `editor_razor_check`, `editor_effects_drop_check`.
`editor_lane_move_check` still fails its 3 stale `promptFit` checks — pre-existing,
still under Next Steps.

⚠ **THE NEW BROWSER TEST FOUND TWO REAL BUGS IN THIS WORK, and both are the same
shape as bugs this file already documents:**
- **`api.saveAnimatic` SILENTLY DROPPED `assets`.** The field was added to the
  schema, the save request, `flush` and the signature — and not to that function's
  destructured parameter list, which is a whitelist. Nothing errors; an unnamed key
  simply is not in `body`, so every save sent the project without its library and
  the server never heard of it. **This is `frameForSave`'s trap in a third place.**
  Caught because the test asserts on the PUT BODY, not on the screen.
- **A LOCKED CLIP LEFT A STALE SELECTION.** Clicking one correctly did not select
  it, and incorrectly did not clear what was already selected — so Delete removed a
  clip on another row while the locked one sat there looking protected. Caught only
  because the assertion compared the WHOLE timeline; the version that checked just
  the locked clip passed against the bug. `startClipDrag` now clears the selection
  on a locked lane.

⚠ **WHAT WAS DELIBERATELY *NOT* BUILT: a library for shapes or text.** The report
says "and shapes", and the answer is that the Shapes tab already IS a library a
deletion cannot empty, and a caption is typed rather than sourced. Neither has a
source to keep, so giving them cards would be inventing state to list.

⚠ **THE MEDIA PANE NO LONGER LISTS CLIPS AT ALL**, and one thing moved with it: a
hold used to be TYPED on a Media card. It is typed in Properties (Duration) and
dragged on the timeline, so nothing was lost — but if a card ever needs to show a
clip's hold again, that is why it does not.

---

### 2026-08-20 — A PAID VEO RENDER HAD NO `url`, SO IT WAS A SPINNER IN MEDIA AND A BLACK HOLE IN THE MONITOR (user-reported, with a screenshot)

> "same erroe when i upload video see image not view in program panel and in
>  media now i see uploading type view"

The clip landed on its Storyboard video row, at the right moment, at the right
length — and showed nothing. Read as "the upload didn't finish", which is why the
report calls it an upload; it is a **render**.

**⚠ ONE MISSING FIELD, AND IT BREAKS TWO DIFFERENT THINGS.** `attachVeoClip` wrote
its clip out as an object literal instead of going through `newVideoClip`, and left
out the one field a hand-written copy always leaves out: `url`.
- The thumbnail effect only fetches frames that **have** a url, so no poster was
  ever requested and the Media card sat on `.fs-thumb-wait` for ever.
- `ProgramCanvas` falls back to a video clip's THUMBNAIL while the video blob is
  still downloading (blobs come one at a time — they are the biggest files in the
  project). No thumbnail means no fallback, so the monitor drew **nothing** for the
  render and the panel underneath showed through.

⚠ **THIS IS THE SAME BUG `newVideoClip`'s OWN NOTE DESCRIBES**, happening a second
time in the one place that did not use that factory — "I upload a video file here
but it doesn't show in the media panel", fixed there, re-introduced here. A reload
hid it, because the server fills a url in on read (`_with_urls`). **If you write a
clip literal with a file behind it, you have written this bug.** Use the factory.

**The fix:** `attachVeoClip` spreads `newVideoClip(...)` and overrides only what is
genuinely different — the panel's `src` kept underneath the video one (so the
render stays in Storyboard Frames, not Video), `track`, `start_ms`, and `out_ms`
(which must be the RENDER's length, not the factory's inference from a duration
that falls back to the panel's hold). `animaticId` joined its dep list; the effect
holding this callback already restarts on it, so nothing about its lifetime moved.

**Evidence it was this and not the upload path.** The user's own 54.4s upload had
its poster **cached on disk** (`_stills/…`), i.e. requested and served fine; the
4.01s render's poster had **never been extracted**, i.e. never asked for. Server
side was blameless: `probe_duration` and `extract_frames` both ran clean on the two
real files in `output/_animatics/d81a7ac7…/media/`.

**Files:** `client/src/components/AnimaticEditor.jsx` (`attachVeoClip`),
`tests/editor_veo_attach_check.py` (new).

**Verified:** `tests/editor_veo_attach_check.py` — new, and **watched fail first**.
Against the bug it reports the Media card as
`{"label": "Shot 1", "badge": "▶ 4.0s", "drawn": false, "waiting": true}` and the
monitor as `rgb(30, 80, 220)` — the panel's own blue showing through — which is the
user's screenshot in numbers. Against the fix all 13 checks pass.
`editor_board_import_check` and `editor_picture_tracks_check` still pass.

⚠ **THE MONITOR ASSERTION IS A COLOUR, AND THAT IS THE ONLY HONEST WAY TO WRITE
IT.** The panel is blue and the render's poster is red, stacked at the same moment
with the render on the row above, and the raw video route is **aborted on purpose**
so the thumbnail fallback is the only path red can take to the screen. "The monitor
drew something" would have passed against the bug — it drew the panel.

⚠ **IT NEEDS NO ANIMATE DIALOG AND SPENDS NOTHING.** `reconcileVeoClips` is
self-healing and runs on every LOAD, so a `veo_clips` record in the fixture drives
the same attach. Copy that trick for anything else on the Veo path.

---

### 2026-08-20 — AN IMPORTED STORYBOARD CAME IN WITH NO PICTURES, BECAUSE THE SAVE IT WAITED FOR NEVER HAPPENED (user-reported, with a screenshot)

> "see when i open new animatics and clicl add layer then i click Storyboard image
>  then select my storyborad project and import so take see image panel not show
>  and in media in not upload properly"

Exactly the thing the previous entry said to look at first, and it went wrong in
the place that entry named. Forty-two panels imported, the row appeared, the clips
were on the timeline — and every thumbnail was a spinner, the Program monitor was
black, and Media listed 42 cards with nothing in them.

**⚠ THE BUG IS NOT IN THE IMPORT. IT IS THAT `flush()` CANNOT SAVE WHAT YOU HAVE
JUST PUT IN STATE.** A board panel is REFERENCED, never uploaded, so its picture
comes from `/animatics/{id}/frame/{frameId}` — a route that resolves by looking
the frame up in the **saved** project. `doBoardImport` knew that and did the right
things in the right order: place the frames, `await flush()`, then write the urls.

But `flush` answers both of its questions from refs that **effects** fill —
`dirtyRef` ("is there anything to save?") and `docRef` ("what is the document?").
One microtask after `setFrames`, which is where that `await` lands, React has not
re-rendered and neither ref has moved. So the flush saw a clean project, returned
at its first line, and wrote **nothing**. The urls then went out against a server
that had never heard of those frames, every one 404'd, and the fetch effect
caches nothing on failure and does not retry — one permanent miss per panel. The
autosave did save them 900ms later, but `frames` never changed again, so the
effect never re-ran and the tiles stayed blank until the next unrelated edit.

⚠ **IT COULD ONLY HAPPEN ON A FRESH ANIMATIC**, which is why the report opens with
"when i open new animatics". On a document with an unsaved edit already in it the
flush had something to write and the frames went up by accident.

**The fix, in two parts:**
- **`flush` takes an override**: `flush({ frames, layers })` merges the values it
  is handed over `docRef` and writes unconditionally, signing the baseline from
  the document it actually sent. That is what makes "set it, then save it"
  expressible at all. The signature builder moved out to a module-level
  `signatureOf` so the save and the dirty-check cannot drift — same rule as
  `frameForSave`. ⚠ A patched flush also RE-THROWS on failure, because a caller
  that saves on purpose and then acts on the result has to know; the autosave has
  nobody to tell.
- **`doBoardImport` commits nothing until the write lands.** The frames, their
  urls and their row are built as plain values, sent in ONE `flush`, and only then
  put in state. So the pictures are fetchable the first time anything asks for
  them. The import spins for one PUT and then everything appears at once.
- ⚠ **THE ROW GOES UP IN THE SAME WRITE AS ITS FRAMES.** `pictureLane` was split
  out of `addPictureTrack` (which is now a thin wrapper over it and behaves
  identically) so the lane can be *built* without reaching state first. Two
  writes racing the 900ms autosave is how a row loses the name it was given.
- The import also opens the Media pane on **Storyboard Frames**, the section its
  panels land in — every other add path in this file already did this
  (`addAssets`, `addColourCard`) and this one did not.

**Files:** `client/src/animatic/useAnimaticProject.js` (`signatureOf`, `flush`
override), `client/src/components/AnimaticEditor.jsx` (`pictureLane`,
`doBoardImport`), `tests/editor_board_import_check.py` (new).

**Verified:** `tests/editor_board_import_check.py` — new, and **watched fail
first**, per the rule in "Tests & tooling". Against the old ordering it reports
`{'cards': 3, 'drawn': 0, 'waiting': 3}` and three 404s, which is the user's
screenshot reproduced; against the fix all 19 checks pass. It drives the real
gesture (＋ Add layer → Storyboard images → pick the board → Import) and its fake
server enforces the REAL rule — `GET /frame/{id}` is a 404 until a PUT has
carried that id — so the assertion is the ORDER of two requests as well as the
pixels. `editor_picture_tracks_check`, `hidden_lane_check` and
`frame_save_fields_check` still pass. `editor_lane_move_check` still fails its 3
stale `promptFit` checks — pre-existing, unchanged, and confirmed against `HEAD`
(the empty band has been a childless `<button>` since `10fd0bd`); still under
Next Steps.

⚠ **THE VEO REROUTE IS STILL UNEXERCISED.** Only the import half of the previous
entry's warning has been closed.

---

### 2026-08-20 — THE STORYBOARD GETS ITS OWN TWO ROWS, AND A PICTURE ROW IS ONE OF FOUR STRICT KINDS (user-specified in detail)

> "i want you add Storybord Layer seprately like when i import storyborad so that
>  time create automatic new layer of Storyborad image Layer … then user click
>  Storyboad image then user get a pop up like all generated storyborad project
>  with name so user click any project then click Import … user want genearte
>  shortyborad image to video footage from VEO 3 model in editor then video
>  genarte and come in Storyboad video layer Sepratlty just up of image layer"

**A PICTURE ROW HAS A KIND NOW, AND THERE ARE FOUR OF THEM** —
`board_image` / `board_video` / `stills` / `video`, declared in `scene.js` as
`ROW_KINDS` beside `clipRowKind`. The kind of row a clip BELONGS on is DERIVED,
never stored: a board reference (`src.storyboard_id`) says it came from a
storyboard and `clipKind` says whether it is footage yet, so the four rows fall
out of two questions already on the clip and there is no fifth field to disagree
with them. That is also why the Veo change below needed no migration — an
animated panel keeps its `storyboard_id`, so it reads as `board_video` the moment
it becomes video.

⚠ **THE ROWS ARE STRICT: a clip only ever lands on a row of its own kind.** The
user's rule, in their words: "i only move each same layer clip like image move in
only image layer and video move video any layer". Enforced in three places, all
reading the same two tables:
- **the drag** — `laneMoveTarget` in `Timeline.jsx` took only the LANE kind, and
  all four picture rows are `kind: "frames"`, so any picture clip could land on
  any picture row. It now also compares `clipRowKind(clip)` against the row's
  `rowKind`, and the drag is handed the clip so it has something to ask.
- **the drop** — `laneTakes` and `dropAsset` both read `ROW_TAKES`. A `files`
  drag says yes wherever either kind would, because a desktop drag does not
  reveal what it carries until the drop; `dropAsset` then refuses it BY NAME.
- **the import** — `addAssets` takes a `rowKind`. Given one, a file the row does
  not take is refused and counted in the notice; given none (the Media pane's own
  button, where no row was pressed), images go to a Stills row and footage to a
  Video row, creating that row if the project has none.

⚠ **`ROW_TAKES` LIVES IN `scene.js`, NOT IN THE EDITOR.** The timeline reads it to
decide whether to light a row up as a drop target and the editor reads it to
decide what the file dialog offers; two copies would drift into a row that
accepts your file and then refuses it. `ROW_KIND` in the editor holds only the
presentation half (name, hint, ＋ text) and takes its `takes` from that table.

⚠ **NEITHER BOARD ROW ACCEPTS FILES AT ALL** (`takes: []`), and that is the point
of them being separate rather than a naming convention. A storyboard row is
filled by the import and a Veo row by ✨ Animate; an upload on either is exactly
the mixing the strict rows exist to stop. Their ＋ opens the thing that DOES fill
them — the picker, or a notice pointing at ✨ Animate — instead of a file dialog.

⚠ **A ROW NO RECORD NAMES IS CALLED AFTER WHAT IS ON IT** (`dominantRowKind`), and
that is the entire migration for every animatic saved before these kinds existed.
An animatic built from a board opens with its panels on track 0, so that row reads
**"Storyboard images"** rather than "Video" with nothing moved and nothing
rewritten — the clips already say what they are. The load-time adoption uses the
same answer, so the record it writes carries the right kind rather than a guessed
"video" (which would have mislabelled the board row and then allowed a panel to be
dragged onto a footage row, defeating the point). An empty row has no clips to ask
and falls back to a plain video row, which is what a new animatic opens with:
**Video, Text, Shapes, Audio**, as asked.

**IMPORTING A STORYBOARD INTO AN ANIMATIC THAT ALREADY EXISTS.**
`POST /animatics/{id}/import-storyboard` (`AnimaticBoardImportRequest` /
`Response`), which reuses `_frames_from_board` — the same builder the board's
"Make animatic" uses, so a shot's key poses come across at their real rate and
only shots without a sequence fall back to a held panel.
- ⚠ **IT RETURNS THE FRAMES AND SAVES NOTHING.** Same contract as the image and
  video uploads: the server produces the material, the client decides where on
  the timeline it lands. `source_storyboard_id` on create is the other job — that
  one fills a brand-new project.
- ⚠ **BOTH JOBS ARE OWNERSHIP-CHECKED.** The animatic because we are handing back
  its own future content, and the BOARD separately because a board id is a
  user-supplied string — without the second check this would read any storyboard
  on the instance by id.
- ⚠ **THE FRAME CAP IS COUNTED AGAINST WHAT IS ALREADY ON THE TIMELINE**, which is
  the difference from create (where the project is empty). Over the cap it falls
  back to one frame per shot and SAYS SO (`panels_only`) rather than silently
  importing something other than what was asked for.
- The picker itself is `.an-board-modal`, listing `api.listStoryboards()` with each
  board's drawn-panel count, one to select and Import. ⚠ **IT SPENDS NOTHING** —
  the panels are already drawn and paid for, this only references them, so there is
  no priced confirmation step. Adding one would teach the user to click through a
  dialog that never has a price on it.
- ⚠ **THE ROW IS MADE BY THE IMPORT AND ITS TRACK READ FROM THE RETURN VALUE.**
  `addPictureTrack` hands the number back precisely because `videoTracks` is
  derived from state the current render has not produced yet.
- ⚠ **THE URLS ARE WRITTEN AFTER THE SAVE**, and this is the subtle part. A frame's
  picture is served from `/animatics/{id}/frame/{frameId}`, a route that resolves
  by looking the frame up in the SAVED project — so a url handed out before the
  save can only 404, and the fetch effect caches nothing on failure and never
  retries. So: place the frames, `flush()`, then patch the urls in. `url` is
  excluded from the saved shape, so that second write does not re-dirty the
  document; it exists purely to make the fetch effect run now that the pictures
  are fetchable.

**A VEO RENDER GOES ON ITS OWN ROW, ABOVE THE PANEL IT CAME FROM.** `attachVeoClip`
used to REPLACE the still in place — same clip, `kind` flipped to "video" — and the
panel was gone. It now makes a NEW clip on the Storyboard video row at the panel's
own start, and **the panel stays underneath**.
- ⚠ **WHY "ABOVE" IS THE WHOLE POINT**: a higher track draws over a lower one, so
  the render is what plays, and 👁 on that row instantly shows the board again.
  That makes animating non-destructive and comparable, which replacing the still
  could never be. Its LENGTH is what Veo was asked for and may differ from the
  panel's hold — left visible rather than trimmed to match, because which of the
  two is right is the user's call.
- ⚠ **IDEMPOTENCY MOVED TO THE UPLOAD ID.** The old test was "is the source frame
  video yet", which worked only because the render replaced the panel. The panel
  is a still for ever now, so that test would answer "no" on every load and
  attach a second copy each time. `reconcileVeoClips` asks whether any frame
  already carries this clip's `upload_id`.
- ⚠ **THE ROW IS RESOLVED ONCE PER PASS, FROM REFS.** A batch finishes as several
  ready clips at once, and each one asking "is there a row yet?" would get "no"
  from the same pre-render state — four renders, four rows, all claiming the same
  track. `boardVideoTrack()` is called lazily and once per reconcile, writes the
  new record into `layersRef` as well as state so a second call in the same tick
  finds it, and reads `layersRef`/`framesRef` rather than the closure because the
  Veo poll deliberately holds a callback several renders old (see `animating`).
  `attachVeoClip` appends to `framesRef` too, so clips attaching in one pass see
  the ones before them.
- Out of room it says **the render is safe** — it is paid for and on the server as
  an ordinary upload; what failed is finding it a row.

**Files:** `client/src/animatic/scene.js` (`ROW_KINDS`, `ROW_TAKES`, `isCutRow`,
`clipRowKind`, `dominantRowKind`), `client/src/components/AnimaticEditor.jsx`,
`client/src/components/Timeline.jsx`, `client/src/styles/animatic-editor.css`,
`client/src/api.js`, `server/schemas.py`, `server/animatics.py`,
`tests/editor_picture_tracks_check.py`.

⚠ **ONE TEST'S EXPECTATION WAS DELIBERATELY REVERSED.**
`editor_picture_tracks_check.py` asserted "a still dragged up onto the footage
track goes there", which was true while any picture row took any picture clip.
It now asserts the refusal, IN BOTH DIRECTIONS (a still onto footage, and footage
onto stills), and that a refused drag leaves the timeline exactly as it was. A
rule that only holds one way round is not the rule.

**Verified:** `npm run build` clean; `python -c "import server.main"` clean.
`frame_save_fields_check`, `picture_tracks_check`, `selection_check`,
`video_clip_check`, `hidden_lane_check`, `transition_check`, `razor_check`,
`editor_picture_tracks_check`, `editor_razor_check`, `editor_effects_drop_check`
all pass. `editor_lane_move_check` still fails its 3 stale `promptFit` checks —
unchanged and pre-existing, see Next Steps. **The import picker and the Veo
rerouting have NOT been exercised in a browser** — no storyboard fixture drives
either path in the suite, so they are built-and-built-only. That is the top thing
to look at.

---

### 2026-08-20 — RECOVERING `AnimaticEditor.jsx` AFTER A PATCH SCRIPT TRUNCATED IT

Not a feature; recorded because the failure mode is cheap to repeat and the
recovery had one non-obvious step.

**WHAT HAPPENED.** A patch script did `io.open(path,'wb').write(s.encode('utf-8'))`.
`open(...,'wb')` truncates the file the moment it is called and the argument is
only evaluated afterwards, so a `UnicodeEncodeError` in the encode left the file
at **0 bytes** — 7138 lines gone. Nothing could recover it: the work was
uncommitted (no stash, no index blob, no dangling object), VSCode local history
had no entry, and the build emits no sourcemaps.

⚠ **THE ENCODING ERROR CAME FROM A HEREDOC.** `\\uXXXX` escapes written inside a
`<<'PY'` heredoc reached Python as real lone surrogates, which cannot be encoded
as UTF-8. Put literal characters in a written-out `.py` file instead of escapes in
a heredoc.

**THE FIX FOR THE PROCESS** is `scratchpad/safepatch.py`: it refuses to patch an
empty file, asserts every substitution's occurrence count BEFORE writing anything,
encodes to bytes before opening the target, writes a sibling temp file and
`os.replace`s it over the original. Every patch in this session's later work went
through it, and two of them failed their assertions and wrote nothing — which is
the point.

**THE NON-OBVIOUS PART OF THE RECOVERY.** The copy the user restored turned out to
be plain git HEAD run through a formatter: `git diff` said 6543/6547 lines changed,
`git diff -w` said **4 added / 8 removed**, and all four hunks were a formatter
joining multi-line template literals. So it held nothing unique, and HEAD was the
better base (2-space, matching every other file, no 6500-line whitespace diff).
⚠ **BUT HEAD WAS MISSING WORK THAT SPANNED THREE FILES.** The ＋ Add layer
DROPDOWN was uncommitted, and only `AnimaticEditor.jsx` had died —
`animatic-editor.css` still had its `.tl-layer-menu` rules with `.an-layer-opt`
already folded away, and `Timeline.jsx` still had the `addLayerMenu` prop. So
reverting one file left the surviving halves pointing at markup and CSS classes
that no longer existed, and HEAD's modal would have rendered completely unstyled.
The editor's half was rebuilt from this file's own work-log entry for it.
**Lesson: when uncommitted work spans files, reverting one file is not a local
decision.** `git diff -w --numstat` is what separates real changes from formatting
churn before deciding.

### 2026-08-20 — A CLIP'S ROW, POSITION AND LOOK WERE NEVER SAVED, AND AN EMPTY ROW COULD NOT EXIST (user-reported: "i look same previous arrangement of ecah layer of clip")

> "what i see problem now wheni click video picker and all video go in video
>  layer but then i go click back butun in animatic editor so when i see again my
>  video picker layer not show and again i show same video picker icon only first
>  video layer so this is big prolem … and when i bo back then i come so i look
>  same previous arrangement of ecah layer of clip"

**`frameForSave` HAD FALLEN FIVE FIELDS BEHIND `AnimaticFrame`, AND ONE OF THEM
WAS THE WHOLE MULTI-TRACK TIMELINE.** It is a WHITELIST of what a picture clip
sends to the server, and a field the schema gains and that function does not
mention is computed by the editor, drawn in the monitor, and then thrown away on
the way out — with no error anywhere, because dropping a key is not a failure.
Missing: `track`, `start_ms`, `effects`, `mask`, `blend`. So every clip came back
on **track 0 with no start**, and since `start_ms: null` means "after the last
clip on my track", the rows collapsed into one and the clips re-laid themselves
end to end. Every colour grade, mask and blend mode went with them.

⚠ **AND IT WAS WORSE THAN LOSING THEM ON RELOAD, because the same function builds
the dirty-check SIGNATURE.** A field that is not in the saved shape is not in the
signature either, so moving a clip to another row **did not make the document look
changed**: the autosave never fired, and Save believed there was nothing to write.
The edit was not lost in transit — it was never sent. That is why the multi-track
work of earlier the same day looked like it worked and then wasn't there.

⚠ **THIS IS THE SECOND TIME THIS EXACT LIST HAS DRIFTED** — the function's own
docstring records the first (`scale`/`x`/`y`/`opacity`/`keyframes`, so Phase 1's
motion never survived a reload). So the fix is not only the five fields:

- **`frameForSave` moved to its own pure module**, `client/src/animatic/frame_save.js`.
  It used to sit in `useAnimaticProject.js`, which imports React and therefore
  cannot be loaded outside a browser — so the one thing about it worth checking
  automatically could not be. Same rule as `selection.js` / `scene.js`: logic with
  a right answer lives where a test can reach it.
- **`tests/frame_save_fields_check.py`** compares the keys the client actually
  sends (under node) against `AnimaticFrame.model_fields`, and fails on the next
  field that goes missing. It also pins the three things a careless "just add the
  field" fix gets wrong: `start_ms` stays **null** (null is not 0 — it means
  "after the last clip on my track", and 0 would nail every pre-tracks clip to the
  head of its row), `mask` is **omitted** rather than sent as null (`AnimaticMask`
  is not optional, so null fails validation on the majority of clips), and what
  comes out is fed through `AnimaticFrame` itself.

**AN EMPTY VIDEO ROW IS A RECORD NOW, SO IT CAN EXIST.** The other half of the
report. A picture track was a NUMBER on a clip and the rows were derived from the
numbers in use, so a row's EXISTENCE and a row's EMPTINESS were the same state —
`extraPictureTracks` was view-only, explicitly "not saved", and an added row you
had not filled yet vanished on the way to the library and back.

- **`AnimaticLayer` gains `track: int | None`** and `kind` gains `'video'`. A
  picture track is a layer record like Text 3 and Shapes are. ⚠ **IT IS STILL THE
  ODD ONE OUT**: its clips do NOT point at it by `layer_id` — a picture clip
  carries the track NUMBER, because that number is also the compositing order and
  the export reads it directly. The record exists to say the one thing the number
  cannot: *this row exists, and it is called this*.
- **`videoTracks`** unions the tracks CLIPS are on with the tracks RECORDS claim,
  highest first. Both halves are necessary: records alone would hide every row of
  every animatic saved before them, and clips alone is the bug.
- **Rows are ADOPTED on load** — every track above the base with no record gets
  one, once, in `onLoadedRef`. So the ✕ has a record to remove and emptying a row
  no longer makes it disappear underneath you. ⚠ **TRACK 0 IS DELIBERATELY NOT
  ADOPTED**: the base row exists whether or not anything is on it, so a record for
  it would say nothing — and writing one into a brand-new animatic would stop it
  being discarded on the way out (`isEmpty`).
- ⚠ **`onLoadedRef` NOW RETURNS `attached === 0 && !changed`.** It was `attached`
  alone, which stopped being right the moment `track`/`start_ms` joined the saved
  shape: the load-time `start_ms` normalisation rewrites both, and folding that
  into "what the server already has" means recomputing it on every load instead of
  writing it down once.
- **✕ on a video row removes the row; its clips drop to track 0 keeping the moment
  they play at** (user's choice). ⚠ **THEIR CLIPS ARE NOT DELETED, unlike every
  other layer's** — those point at their lane by `layer_id` and have nowhere else
  to live, whereas a picture clip's track number always has a base to fall back
  to, and a shot is the most expensive thing on this timeline to lose (a board
  panel, an upload, a Veo render that was paid for). The landing positions are
  captured from `pictureSpans` BEFORE the record goes, because a clip with no
  `start_ms` of its own begins where its neighbour on *that* row ended.
- Track 0 is not removable (its ✕ empties it, like the default Text and Shapes
  rows), and neither is a row no record proves exists — `onRemoveLayer` takes a
  layer id, so a null one would be a ✕ that does nothing.
- `addPictureTrack` and `splitFootageOntoTrack` both write a record, and both
  refuse past `MAX_PICTURE_TRACK` (15, the schema's cap on `AnimaticFrame.track`)
  with a notice rather than a failed save.

**Files:** `client/src/animatic/frame_save.js` (new),
`client/src/animatic/useAnimaticProject.js`, `client/src/components/AnimaticEditor.jsx`,
`server/schemas.py` (`AnimaticLayer`), `tests/frame_save_fields_check.py` (new).

**Verified:** `npm run build` clean. `tests/frame_save_fields_check.py` 14/14.
`picture_tracks_check.py`, `selection_check.py`, `video_clip_check.py`,
`hidden_lane_check.py`, `editor_picture_tracks_check.py`, `editor_razor_check.py`,
`editor_effects_drop_check.py` all pass. `editor_lane_move_check.py` still fails
its 3 stale `promptFit` checks — unchanged, pre-existing, see Next Steps.
**Not opened in a browser by hand.**

⚠ **AGREED AND NOT YET BUILT — the storyboard layers.** The user's requirement in
full, decided this session and deliberately left for the next pass so the
persistence fix could be tested on its own:

1. A new animatic shows only **Video, Text, Shapes, Audio** (already true).
2. **`+ Add layer → Storyboard images…`** opens a popup listing the user's
   storyboard projects by name → pick one → **Import** → the panels land on their
   own "Storyboard images" row. `api.listStoryboards()` and the server's
   `_frames_from_board` already exist; the endpoint that imports into an EXISTING
   animatic does not. Importing a board should CREATE that row automatically.
3. **`+ Add layer → Storyboard video`**, and a Veo render lands on it — directly
   ABOVE the storyboard images row, at the same time, **with the still left
   underneath** (user's choice). A higher track draws over a lower one, so the
   animation plays and 👁 on that row instantly shows the board again. ⚠ This is a
   change to `attachVeoClip`, which currently REPLACES the still in place (keeping
   `src.storyboard_id`, so an animated panel is already a board-origin video clip
   — which is exactly the distinction the two rows need).
4. **Row kinds are STRICT** (user's choice): a board panel may only sit on a
   Storyboard images row, a Veo render only on a Storyboard video row, an upload
   only on its own kind of row. Photos and footage never share a row. ⚠ `frames`
   vs `image` is ALREADY enforced by `laneMoveTarget` (`from.kind !== to.kind`);
   what is missing is the finer rule WITHIN the cut, which `frameOrigin` +
   `clipKind` can answer without a new field on the clip.
   ⚠ **STRICTNESS AND THE VEO CHANGE ARE ONE UNIT** — shipping strict rows while
   Veo still replaces a still in place would leave the render sitting on a row its
   own kind is not allowed on.

### 2026-08-20 — THE PICTURE ROWS ARE CALLED **VIDEO** NOW, THEIR ＋ TAKES FOOTAGE, AN IMPORT LANDS ON THE ROW YOU AIMED AT, AND A SELECTION OF CLIPS ACTUALLY MOVES (all four user-reported, with two screenshots)

> "at the place of picture we have video which can also have both import a video
>  or a photo don't restrict it and the name is confusing we are not able to move
>  here and there properly in the picture tracker and why is audio import not
>  available through the dropdown and why is the video tracker not importing
>  another video differently if there is one there in the timline before it
>  imports that only even if we add it on different layer"

**FOUR SEPARATE FAULTS BEHIND ONE REPORT.** Three of them were the same shape:
code that had been updated when the picture track became a stack of real tracks
(2026-08-20, earlier the same day) sitting beside code that still believed the
old model, with no error to say so.

**1 — "Pictures" WAS THE WRONG NAME, AND THE ROW'S ＋ ENFORCED IT.** The rows are
`Video`, `Video 2`, … (`lanes` in `AnimaticEditor.jsx`), the dropdown item is
**Video track**, and the lane hint / ＋ tooltip say "footage and stills". The name
was the visible half; the ENFORCEMENT was the real bug: the row's ＋ clicked an
input with `accept="image/*"` whose `onChange` called `addFiles` — the
**image-only** upload path — so the file dialog HID the very MP4 the same row
accepted by drag and drop, and forcing one through ("All files") uploaded it as a
still. One picker for both kinds now (`pictureInputRef`, `accept="image/*,video/*"`,
straight into `addAssets`, which has routed by file type all along).
⚠ **THE DEAD VIDEO-ONLY INPUT IS GONE** — `videoInputRef` existed, nothing ever
clicked it, and its `accept="video/*"` was the opposite half of the same mistake.
⚠ **`LANE_ICON.frames` IS 🎞, NOT 🖼** — the video row and the overlay ("Images")
row drew the SAME icon, which is the other half of why the two were confusable.
The overlay row keeps 🖼 and its name: it composites a picture OVER the cut, which
is a different thing.

**2 — AN IMPORT IGNORED THE ROW YOU DROPPED IT ON.** Which track new media landed
on was read from a ref, `pendingPictureTrack`, that **only the lane ＋ ever set and
nothing ever reset**. Every other way in — a file dropped on a row, the Media
pane's own button, the drop target beside it — silently used whatever row was
last touched, or track 0. That is the whole of "it imports that only even if we
add it on different layer": your second video went in beside the first one.
`dropAsset` compounded it by calling `frameIndexAt(at)` with **no track**, so the
insert INDEX came from the nearest cut across all rows while the insert itself
happened on a different one.
- **The track is a parameter now** — `addAssets(files, insertAt, track, atMs)`
  → `addFiles` / `addVideoClips` → `insertPictures`. The ref survives only to
  carry the row across the OS file dialog, which is asynchronous and has no other
  way to remember what it was opened for, and it is **read once and cleared**
  (`takePendingTrack`).
- ⚠ **AND THE DROP TIME IS HONOURED ON AN END-OF-TRACK INSERT** (`atMs`). "The
  end of an empty track" is zero, so a clip dropped at 0:45 on a row you just
  made jumped to 0:00 — harmless while the target row was always 0, and the first
  thing you would hit now that it is not. A deliberate GAP in front of the clip
  is a thing that exists in this model (a gap shows the row underneath).
  Inserting BETWEEN two clips still snaps to the nearest cut and ripples.
- Two things fixed in passing, both in `insertPictures`: a mixed drop put the
  VIDEO in front of the STILL (both were handed the same `insertAt`, and the
  second insert used an index the first had already shifted), and the ripple pass
  used `list.indexOf(f)` to find each clip's span — n² on a thirty-panel board,
  and the wrong answer the moment two entries are the same object reference.
  `spans` is parallel to `list`; it is indexed now.

**3 — A SELECTION OF PICTURES COULD BE DRAGGED BUT NOT MOVED.** `frame` has been
in `MOVABLE` since clips got their own `start_ms`, so the timeline started the
drag and drew the ghost — and then `moveSelection` in `AnimaticEditor.jsx`, which
still carried the comment "Pictures are not moved", wrote every OTHER kind and
dropped them. The clip snapped back, with no error and nothing in the undo stack.
- `moveSelection` has a `frame` branch (one `setFrames`, one Ctrl+Z), and
  `selectionFloorMs` counts pictures, so a mixed selection whose leftmost clip is
  a picture measures its 0:00 wall from the right clip.
- ⚠ **A PICTURE'S START IS NOT NECESSARILY ITS `start_ms`.** A clip saved before
  tracks has none and begins where the one before it on its row ended, so the
  move is written from a new `frameStartById` (the EVALUATED start, out of
  `frameSpans`) — `+ delta` on the raw field would move such a clip from 0.
- ⚠ **AND THE SAME BUG HAD A SECOND MOUTH**: `SelectionProperties.jsx` computed
  `movable` from `GROUPABLE` instead of `MOVABLE`. The two lists differ by exactly
  that one kind, so the **Nudge** buttons were hidden for a selection of nothing
  but video clips — the selection this pane is most often opened on. Its
  breakdown row now says "video clip(s)" with 🎞, and its ⓘ no longer claims
  pictures stay where they are.

**4 — THE DROPDOWN'S AUDIO ITEM COUNTED CLIPS, NOT FILES.** It disabled on
`audioTracks.length >= MAX_AUDIO_TRACKS`, which is the number of CLIPS; every
other audio limit in the file uses `audioFileCount()` (distinct uploads). Razor
one voiceover into four pieces and the menu greyed itself out at "4/4" on a
project holding **one** file — exactly the reporter's case (3 pieces of an
ElevenLabs take + one music bed = 4 clips, 2 files). Counted in files now, and
the disabled note says cutting one up does not count against it. ⚠ **The item
still adds an EMPTY ROW and no file** — confirmed with the user as the intended
behaviour, consistent with every other kind in that menu: you add the row, then
you put things on it with that row's own ＋.

**Files:** `client/src/components/AnimaticEditor.jsx` (lane names + hints,
`frameStartById`, `selectionFloorMs`, `moveSelection`, `insertPictures`,
`addToLane`, `dropAsset`, `addFiles` / `addVideoClips` / `addAssets`, the hidden
inputs, the ＋ Add layer menu, four notices),
`client/src/components/properties/SelectionProperties.jsx`,
`client/src/components/Timeline.jsx` (`LANE_ICON`, `LANE_HINT`, `LANE_ADD`).
No server change — the upload endpoint was already correct: every
`POST /animatics/{id}/videos` mints a fresh `uuid` and stores its own file, so
the second video really was uploaded; it was only put in the wrong place.

**Verified:** `npm run build` in `client/` clean. `tests/editor_picture_tracks_check.py`,
`tests/picture_tracks_check.py`, `tests/editor_razor_check.py`,
`tests/editor_effects_drop_check.py`, `tests/selection_check.py`,
`tests/video_clip_check.py` all pass. ⚠ **`tests/editor_lane_move_check.py` FAILS
3 of its checks, and it was ALREADY FAILING before this work** — its `promptFit`
probe looks for a TEXT NODE inside `.tl-track-empty` and measures 0 prompts,
because the empty-row prose was deliberately REMOVED on 2026-08-20 ("remove
information text look in blanck layer"); the band is now a childless `<button>`
carrying its sentence on `title`. Stale test, not a regression — see Next Steps.
**Not opened in a browser by hand.**

### 2026-08-20 — ＋ ADD LAYER IS A DROPDOWN UNDER THE BUTTON NOW, NOT A DIALOG, AND EACH KIND IS ONE LINE (user-reported, with screenshots of both)

> "i want when i click +add layer so not open popup i want open dropdown in same
>  place and and only keep main name and remove information text pf each layer"

**A FIVE-ITEM CHOICE IS A MENU, NOT A MODAL.** "Add a layer" dimmed the whole
editor, put a heading, a paragraph and a ✕ round the list, and threw your eye to
the middle of the screen and back for one word. It hangs off the ＋ instead:
`.tl-layer-menu`, absolutely positioned under `.tl-head`, starting at the layer
column's width and growing only if a label needs it, so the list opens where the
press landed and reads as that column's own.

- ⚠ **THE TIMELINE HOLDS IT, THE EDITOR FILLS IT** — a new `addLayerMenu` node
  prop on `<Timeline>`, written to the same rule as `addTools` right beside it:
  *what* layers exist and what one costs is `AnimaticEditor`'s business, and this
  file only knows where the ＋ that opens them stands. **Rendering the node IS
  "open"** (`addLayerMenu={layerMenu && (…)}`), so `layerMenu` stays the single
  answer to "is the menu up?" and there is no second copy in `Timeline.jsx` to
  disagree with it. `aria-haspopup` / `aria-expanded` on the button.
- ⚠ **`.tl-head` IS NOW POSITIONED AND AT `z-index: 12`.** It is the menu's
  containing block, and 12 clears everything drawn on a lane — the pinned ruler
  (8) and the playhead grip (9) included — or the picker would open BEHIND the
  timeline it adds a row to. `.tl-cols` is not positioned, so raising this one
  box is enough. ⚠ **IT IS STILL CLIPPED BY `.an-timeline-body`'s
  `overflow: hidden`**: five tight rows are ~9rem, which the pane clears at any
  usable height, but that is the ceiling on how long this list can get before it
  needs a fixed-position rect instead.
- ⚠ **ESCAPE AND AN OUTSIDE PRESS HAVE TO BE WRITTEN NOW** — the modal overlay
  did both for free. One effect while the menu is open. **The ＋ is exempt from
  the outside-press close**, because it TOGGLES: closing on its `pointerdown`
  would let the `click` that follows reopen what it just shut, which looks
  exactly like a dead button.

**ONE LINE PER KIND: icon, name, nothing else.** The note under each label
("Another row for stills and footage — drawn OVER the tracks below it", ×5) is
gone — a menu is read by SCANNING it, and five sentences is not a scan. Each
note moved to the item's `title`, which is where this editor already puts the
long answer (the empty lanes and every row ＋ did the same on 2026-08-20). The
Audio item still explains itself when it is disabled, on the same `title`.

⚠ **THE WORKSPACE DIALOG OWNS ITS OWN ROWS NOW.** It was wearing
`.an-layer-opt an-ws-opt` — borrowing the add-layer picker's modal, list, row
and icon-chip rules and overriding four things. Add-layer is a dropdown, so
those base rules had exactly one user left; they are folded into `.an-ws-modal` /
`.an-ws-list` / `.an-ws-opt` / `.an-ws-opt-ico` and the double class names in
the JSX are gone. **No visual change to that dialog** — same declarations, one
name. Verified with `npm run build` in `client/` (clean); no browser run.

### 2026-08-20 — THE ANIMATIC EDITOR'S TOP BAR IS TWO GROUPS NOW, AND "MAKE FINAL VIDEO" IS GONE FROM IT (user-reported, with a screenshot of the bar)

> "First remove make final video buttun not need in Storyboad to animatics
>  workflow and Mp4 out of date buttun keep side of export video buttun and save
>  buttun keep long video workshapes side"

**THE HAND-OFF BUTTON IS REMOVED, NOT HIDDEN.** `🎞️ Make final video` created a
`/final-videos` project from this animatic and navigated to the next workflow —
a second front door to a workflow that already has its own. Deleted end to end
rather than left behind a flag: the button, `makeFinalVideo()`, the `makingVideo`
state and the `onMakeFinalVideo` prop in `AnimaticEditor.jsx`; the prop's
pass-through in `StoryboardToAnimatics.jsx`; the handler **and** the
`pendingFinalVideoId` state in `App.jsx`. ⚠ **`AnimaticsToVideo` LOST ITS
`openId` / `onOpened` PROPS WITH IT** — that pair existed only to consume the id
this button minted, and nothing else ever set it, so leaving them would have left
a prop pair no caller could reach. The workflow now always opens on
`FinalVideoLibrary`, which is where a final video is started from and already
creates its own projects (`api.createFinalVideo`). ⚠ **THE SERVER IS UNTOUCHED**:
`POST /final-videos` still accepts `source_animatic_id`, and the library still
uses it — only the editor's shortcut to it is gone. The four editor probes in
`tests/` that passed `onMakeFinalVideo={() => {}}` drop the prop.

**THE BAR READS AS DOCUMENT-THEN-OUTPUT.** Order was
`MP4 · Save · Export · Make final video`; it is now
`workspace name · workspace · Save │ MP4 · Export video · 🗑`. Save sits beside
the workspace because both are about the DOCUMENT you are editing, and the last
export sits against the button that makes the next one because "here is the file
you have" and "make a new one" are one question. ⚠ **PURE REORDER — NO LOGIC
MOVED.** The download button still names its file from `video.container` (the
export that was made, never the dialog's current setting) and still wears
`an-stale` + "(out of date)" when the project changed after it; Export still
captures the playhead into `still_ms` on the way INTO the dialog. Delete stays
last, furthest from the button you came here to press. Verified with
`npm run build` in `client/` (clean); no browser run this session.

### 2026-08-20 — THE EMPTY ROWS STOPPED TALKING, THE PICTURE ROWS GOT A SURFACE, AND A CLIP NOW SAYS WHAT IT IS IN COLOUR (all three user-reported)

> "first remove information text look in blanck layer … and see picture both
>  layer no Backgrond fill panel like other not match other layer type bg look …
>  i want keep color of content so user understand easily color byies content
>  like video clip keep pastel orange coloue in layer so i wnat also image clip
>  color is pastel pink, text clip color is pastel yellow, and caption clip color
>  pastel green shapes till nw good not chnage in shapes"

**1. AN EMPTY ROW IS AN EMPTY ROW.** Every lane with nothing on it carried a
line of prose — "T No text yet — click to caption the shot at the playhead",
"◆ No shapes yet — click to add one", and three more — so a project with five
layers open read as a page of instructions with a few clips on it. All five are
gone. ⚠ **NOTHING WENT WITH THE TEXT**: the whole band is still the row's add
button, it still lights on hover (the tint went 0.07 → 0.1, because the hover is
now the entire affordance), and what it does is on its `title` — **the same
string the row's ＋ carries in the gutter** (`lane.add || LANE_ADD[kind]`), so
there is one sentence per lane instead of two that could drift apart. One
`emptyBand(lane, count)` in `Timeline.jsx` replaces five copies of the button.
`lane.empty` on the captions lane is deleted with them; `.tl-track-empty`'s
centring rules stay, because the audio clip's "Loading …" placeholder is still
text in a lane of variable height.

**2. ⚠ THE PICTURE ROWS KEEP THE LANE'S OWN PANEL NOW.** `.tl-lane.tl-bars`
stripped the background, the border and the radius, and the note said a band
round butted-up bars reads as a box they sit in. That was true while the picture
WAS one unbroken sequence. **It stopped being true the moment a picture track
became a stack of independently placed clips** (2026-08-20, the entry below): a
gap is legal now, and a gap in a row with no panel is a hole straight through to
the timeline — so the two picture rows were the only ones on the bar with no
surface at all. Same `--panel-2`, same 6px radius, same edge as every other lane,
and the gaps read as gaps IN something. ⚠ **AN INSET BOX-SHADOW, NOT A BORDER** —
the same rule `.drop-ok` is written against: a real border moves the content box
in by a pixel and every bar on this row is placed at an absolute `left` measured
against the ruler. `overflow` stays `visible` (`.tl-tr-add` hangs 7px left of its
cut) and the bars keep `:first-of-type` / `:last-of-type` rounding — rounding
every bar would put a pinch at each butt joint, which is most joints.

**3. ONE COLOUR PER KIND OF CONTENT.** Footage **orange**, pictures **pink**,
text **yellow**, captions **green**. Shapes and audio are deliberately untouched:
a shape clip carries the shape's own colour in its swatch and audio carries a
waveform, so both already say what they hold, and two more hues would only make
the four that mean something harder to tell apart.

- ⚠ **EVERY NEW RULE ASSIGNS CUSTOM PROPERTIES AND NOTHING ELSE.** The base rules
  (`.tl-bar`, `.tl-text`, `.tl-overlay`) read `--clip-tint` / `--clip-tint-alt` /
  `--clip-edge` / `--clip-seam` with their old gold as the fallback; the block at
  the end of `animatic-lanes.css` only sets those variables. Writing
  `.tl-bar.is-video { background }` would have been the obvious way and **would
  have broken two things**: it out-specifies `.tl-bar:hover` AND `.tl-bar.sel`,
  so a coloured clip would have lost its hover edge and its gold selection fill.
  Through a variable every state rule keeps exactly the specificity it had.
- ⚠ **THE PASTELS ARE PASTEL BECAUSE GOLD MEANS "SELECTED"** on every lane — one
  selection language across the bar, which is the rule `.tl-bar.sel` was written
  to hold. A content colour has to sit UNDER that without competing; saturated
  fills would have made "orange clip" and "selected clip" the same kind of
  signal. Palette in `theme.css`, both themes (light keeps the hue and goes
  deeper, exactly as the gold tints do); no literal colour in the lane file.
- ⚠ **`frameOrigin` IS BACK IN `Timeline.jsx` FOR THIS AND ONLY THIS.** It
  stopped deciding WHERE a picture is drawn when tracks landed — that is
  `frameTrack`, and re-using origin for placement is the exact bug that change
  existed to fix. What a clip IS is a different question and origin is the honest
  answer to it: `video` → `is-video`, `board`/`image` → `is-still` (a storyboard
  panel is a still, so it is pink like any other picture).
- The captions row is marked `tl-captions` rather than branched — it is a text
  lane, same clips and same drag code — so `.tl-captions .tl-text` beats
  `.tl-text` by coming later at higher specificity, and green wins over yellow.

**Files:** `client/src/components/Timeline.jsx`, `client/src/styles/theme.css`
(20 new tokens, both themes), `animatic.css` (`.tl-bar` reads the vars),
`animatic-text.css` (`.tl-text` reads them; the `.tl-track-empty` comments no
longer describe a prompt that exists), `animatic-lanes.css` (the picture lane's
panel, `.tl-overlay` reads the vars, and the new colour block),
`AnimaticEditor.jsx` (the dead `empty:` field).

**Not verified in a browser.** This is markup, tokens and cascade only: checked
by esbuild parse of both components, by bundling the whole stylesheet
(`esbuild index.css --bundle`) to confirm it compiles, and by reading the bundled
output to confirm the new rules land AFTER the base rules and that nothing in
`animatic-tools.css` or later re-declares a clip background. **The four colours
have not been looked at on a real timeline** — pastel alphas are the one thing a
screenshot decides better than arithmetic, so if orange and pink read too alike
at 0.17, that is the number to move. No test asserts on clip colours or on the
empty-row prompts.

### 2026-08-20 — THE LAYER ROW'S CONTROLS WERE NEVER PUT IN THEIR CLUSTER, SO THE NAME HAD NOTHING LEFT (user-reported, with a screenshot)

> "see my previous look icon and name of layer in Storynoard to Animatics
>  worklfow / fixt it please" — a screenshot of the gutter, where the layer names
>  were down to "Capti…" and "Eleve…".

⚠ **THE 2026-08-19 FIX WAS WRITTEN IN CSS AND NEVER IN THE MARKUP.** That entry
("TRACK HEADS LINE UP…", further down) says every layer row got the same three
controls in the same three places, in one `.tl-layer-acts` cluster, with each
button carrying `.tl-layer-btn` and a `disabled` state instead of being left out.
`.tl-layer-acts`, `.tl-layer-btn`, `.tl-layer-btn:hover:not(:disabled)` and
`.tl-layer-btn:disabled` have been in `animatic-editor.css` since `b47c9e9` —
**and `Timeline.jsx` has never rendered any of those classes.** `grep -r
tl-layer-acts client/src` returned CSS and nothing else. So the rules were dead,
the three buttons were bare `<button>`s taking the browser's own padding, and the
consequences were exactly what the screenshot shows:

- **The name was starved.** The row is a fixed-width flex line, and the name is
  the only item on it that shrinks (`text-overflow: ellipsis`, `min-width: 0`).
  Unsized buttons ate ~25px more than the three 1.15rem slots they were budgeted,
  and the name paid for all of it — "Captions" became "Capti…".
- **The controls could still zig-zag**, which is the fault that entry was written
  against: no `margin-left: auto` on a cluster that didn't exist, so they sat
  wherever the name ended, and only `.tl-layer-split` (`▶⇧`, picture rows with
  mixed stills and footage) had an auto margin of its own — so *that* row pushed
  its controls right and the others did not.
- **A control with nothing to do was absent, not ghosted.** The ✕ was rendered
  behind `(lane.removable || … || count > 0)`, so an empty Text or Shapes row had
  no third slot and no ghost.

**What changed** — `client/src/components/Timeline.jsx`, the gutter row only:
- The eye/speaker, the ＋ and the ✕ are wrapped in `<div className="tl-layer-acts">`
  and each carries `tl-layer-btn` alongside its own class. The CSS is now reached.
- **All three are always rendered**; `disabled` is what says "nothing to do" —
  `clips.length === 0` for audio's speaker, `!lane.vis` for the eye (dead in
  practice: `laneToken` returns a token for every non-audio lane, kept as the
  safety net the CSS assumes), and a new `deletable` const for the ✕, which is the
  old render condition moved off the JSX and into a name. Each disabled state has
  its own tooltip ("Nothing on Shapes to delete yet") rather than an inert button
  that explains nothing.
- **What the controls DO is unchanged** — `onToggleMute` / `onToggleHidden`,
  `onAddToLane`, and the same `onRemoveLayer` / `onRemoveTrack` / `onClearLane`
  three-way on the ✕, now with `count > 0` guarding the last one because the
  button is reachable when it is 0.
- The ＋ gained `stopPropagation`, which the 2026-08-19 entry says it should have
  had: a click on a control is not also a click on the row.

**And the name got a budget worth having** — `animatic-text.css`:
- `--tl-gutter-w` 9rem → **11rem** (720px breakpoint: 6.4rem → 8rem). ⚠ **THE
  CONTROLS AND THE NAME ARE ONE BUDGET AND THE CONTROLS ARE THE FIXED HALF**:
  padding + icon + gaps + 3 × `--tl-act-w` is ~101px of it, so at 9rem the name
  had 43px — five characters, which is what was reported. 11rem leaves ~10, which
  is "Captions", "Pictures" and "Shapes" whole. The comment there now records the
  arithmetic so the next person widening it knows what they are trading.
- `.tl-layer-name` carries its own `title={lane.name}`, because a fixed column
  will always truncate SOMETHING (an audio row is named by its filename) and the
  one element that must be able to say the whole name is the name. The rest of the
  row still shows the lane's hint.
- `.tl-layer-split` **lost its `margin-left: auto`** — the cluster carries the one
  auto margin now, and two of them would have split the free space and parked the
  ▶⇧ in the middle of the gutter.

**Not done / not verified:** the lane icons are still emoji (`🖼 T ◆ ♪ ❝`, plus
🔇/🔊 on audio) and 🖼 carries its own colour beside four monochrome glyphs —
which is the exact complaint `Icon.jsx`'s own header was written about. Left
alone because the screenshot the user pointed at has those icons and the ask was
to get the row back, not to redraw it; noted under Next Steps. **Nothing was
opened in a browser** — the change is markup + two CSS variables, verified by
esbuild parse and by grep (`tl-layer-acts` and `tl-layer-btn` now appear in the
JSX as well as the CSS). No test asserts on these buttons, so none needed
updating: `tests/` references only `.tl-gutter-row` (heights, in
`e2e_animatic.py`) and `.tl-layer-split`, both untouched.

### 2026-08-20 — THE PICTURE TRACK BECAME A STACK OF INDEPENDENT TRACKS (user-reported)

> "when i do video trim so i see my image layer conetnt move like snip and same
>  with image when i trim image so my video layer content move. i want user move
>  independaly each asstes/conetnt in layer"

**IT WAS TRUE BY CONSTRUCTION, WHICH IS WHY NO SMALLER FIX EXISTED.** `frames` was
ONE list laid end to end: a clip's place was the sum of the clips before it, so
changing any clip's length moved every clip after it. And the two picture rows
("Images" / "Video") were that SAME sequence filtered by ORIGIN (`lane.only`,
`frameOrigin`) — they looked like two independent layers and shared one clock, so
trimming footage moved the stills on the row above. A filtered VIEW could never
have fixed that.

**THE MODEL.** A picture carries two new fields (`AnimaticFrame`):

- `track` — 0 is the base, and a HIGHER NUMBER IS DRAWN OVER A LOWER ONE. So a gap
  on an upper track shows whatever is on the track below, and a moment with nothing
  on any track shows `settings.background`.
- `start_ms` — where the clip sits. ⚠ **`None` MEANS "AFTER THE LAST CLIP ON MY
  TRACK"**, and that is the compatibility hinge: every animatic written before this
  carries no starts at all and sits on one track, so `frameSpans` lays it out
  exactly as the old running total did, at every cut. The editor fills the nulls in
  ONCE on load (`onLoadedRef`) so the document stops being relative — a mixture of
  explicit and implicit starts is the one state that can surprise you.

⚠ **`sceneAt` RETURNS `pictures`: A STACK, BOTTOM TRACK FIRST.** Every renderer
walks it. `frame` / `frame_b` / `mix` / `transition` / `transition_params` are kept
as the TOPMOST entry, **derived, never computed a second way** — they answer "which
clip is at the playhead", which is what the Properties pane and the transport
wanted all along and a different question from "what is on screen". On a project
with one picture track the stack has exactly one entry and those ARE it, which is
why nothing about an existing animatic resolves differently.

⚠ **A GAP IS LEGAL, AND NEITHER PLANNER MAY SKIP IT.** Both used to
`continue` past a moment with no picture — unreachable while the sequence had no
holes. Skipping one now makes the encoded video SHORTER than the timeline and pulls
the audio out of sync from the first gap onward. `plan_segments` emits a segment
with an empty stack; `_ground` draws it as the bar colour.

⚠ **A TRANSITION IS TRACK-LOCAL AND NEEDS A REAL BUTT-CUT.** `after_frame_id` names
the outgoing clip; the incoming one is the next clip ON THE SAME TRACK whose start
is exactly this one's end. `spans[from + 1]` was exact while the picture was one
gapless sequence and is wrong twice over now — the next clip in the LIST may be on
another track, and two clips can be neighbours without touching. No cut, no
transition: inert rather than wrong, the same treatment one on the last clip gets.

⚠ **ONE CLIP PER TRACK AT A TIME, AND THE LATER ONE WINS** where two overlap
(`stackAt`). Free placement makes an overlap possible where a butt-jointed sequence
could not; it is MARKED (`.tl-bar.clash`) rather than prevented, because refusing
the drop would fight the pointer and silently choosing which picture plays is worse
than saying so.

**THE RENDERERS.** `render_frame` takes `pictures` — a list — and composites it onto
the bar colour, `_draw_track` being one layer of it; `_picture_canvas` and
`_transition_canvas` take the canvas UNDERNEATH them instead of making one, which
is what lets an upper clip's chroma key or faded edge reveal the track below. With
one picture on track 0 that is byte-for-byte what they always produced, which is
why `effects_check`'s goldens still hold. `ProgramCanvas` walks the same stack per
track (`drawTrack`), and `useMonitorVideo` cues every track's video — reading
`scene.frame` alone would have left a clip on a lower track frozen. The still-cache
key names EVERY track, or two moments differing only in what an upper track shows
would share one rendered still.

**THE BAR.** The picture rows are tracks (`pictureTracks`), placed by `frameSpans`
like every other clip, and they share the ONE clip drag:

- **V — a plain trim moves one clip and leaves a gap.** This is the fix, and it is
  the default because it is the only trim that never touches a clip you were not
  pointing at.
- **B — ripple:** the trim, then everything after it on that track slides by the
  same amount. The old behaviour, kept as a tool rather than as the only offer.
- **N — rolling:** the neighbour absorbs it, so the cut moves and the track's length
  does not. Falls back to a plain trim when there is no cut to roll against.
- **The head grip is a REAL head trim now** on a picture, not "the cut before it" —
  it has a start of its own — and on a video clip it moves `in_ms` with it.
- **Pictures joined the cross-track drag** (`CROSS_LANE_KINDS`), and `MOVABLE` in
  `selection.js` gained `frame`. `GROUPABLE` deliberately did NOT: `group_id` is not
  a field on `AnimaticFrame`, so tagging a picture would write something the server
  drops — that is a schema change, not a list change.
- **▶⇧ in a picture row's gutter** puts that row's footage on a track of its own
  without re-timing anything. The origin split was worth keeping; imposing it as a
  MODEL was the bug. It reports how many transitions it stranded across the new
  boundaries rather than letting them go quiet.
- Hidden picture tracks: **blanked on track 0, dropped above it.** The two are the
  SAME PICTURE where each applies (under track 0 there is only the bar colour), and
  only one is safe in each case — blanking holds the time, which the export needs;
  dropping reveals the track below, which an opaque card would hide. Twinned in
  `server/animatics.py`. The tokens are `frames:<n>` now, so a picture row hidden
  before this comes back visible once.

**Verified:** `tests/picture_tracks_check.py` is new (27 checks) — colour cards, so
it needs no files: it proves the placement, that a trim moves nothing else, that
both planners carry a stack and emit the gap, and that the composite draws the
higher track over the lower one, the lower one through a gap, and the bar colour
through a hole — then encodes it and measures the LENGTH, which is what a skipped
gap would break. `tests/editor_picture_tracks_check.py` is new (22 checks,
Chromium) and drives every gesture with the mouse, asserting on **what MOVED** — the
bug was never about the clip you were dragging. `render_parity.py` gained a
multi-track fixture (36 sampled moments + 16 rules: gaps, an overlap, a transition
per track, one across a hole). `hidden_lane_check.py` and `editor_lane_move_check.py`
were updated for the new rows. **All 14 non-browser suites and all 5 browser suites
pass, and `npm run build` is clean.**

### 2026-08-19 — A CLIP COULD NOT BE MOVED TO ANOTHER LAYER, AND THE EMPTY-ROW PROMPT FELL OUT OF ITS ROW (both user-reported)

The report, in the user's words:

> "first you bsee layer empty text not view full i see it gos in down"
> "mai big thing i not move some audio part in other audio layer on blank area i
>  want i do move audio content to other audio layer"
> "and i see same problem with other like shape not move other shapes layer sam
>  with image, text, amd caption"

**1. A MOVE DRAG HAD NO VERTICAL HALF.** A clip's row was decided when it was
made. Dragging its bar slid it along the timeline and nothing else, so changing
layer meant dragging the thing out of the Media pane again — a path that existed
for shapes (`shapeClip`) and audio and for nothing else, and which for audio was
refused outright whenever the destination was a row grouped by FILE. Captions and
overlay pictures had no path at all.

- `Timeline.jsx`: `CROSS_LANE_KINDS`, `laneAtPoint`, `laneMoveTarget`,
  `laneIsTarget`, `laneGhost`. `startClipDrag` and `startAudioDrag` take the LANE
  instead of just `lane.kind` and remember `fromKey`; both move handlers set
  `toKey` from the pointer's Y; both pointerups route through the new
  `onMoveToLane` when it is set.
- `AnimaticEditor.jsx`: `moveClipToLane` / `moveTrackToLane`, wired as
  `onMoveToLane`. `addLayer` gained `{ name, notice }` for one caller.
- `animatic-lanes.css`: `.tl-lane.drop-lane`, `.tl-ghost`, `.lifting`.

⚠ **THE ROW UNDER THE POINTER IS ASKED OF THE DOM**, not computed. Every lane
carries `data-lane` (the frames and audio rows gained it here) and `laneAtPoint`
hit-tests those boxes — the same decision `hitsIn` makes for the marquee, and for
the same reason: the browser has laid the rows out already, and a copy of their
vertical geometry here would be wrong for the whole of a vertical zoom, which
rewrites every row's height.

⚠ **THE BAR DOES NOT TRAVEL BETWEEN THE ROWS.** A clip is a CHILD of its own
lane, so re-parenting it mid-drag would move the node the pointer is captured on
and the gesture would simply end. The original dims (`lifting`), an outline is
drawn on the destination (`tl-ghost`) and the row lights up — which says the same
thing and survives the drag.

⚠ **THE TIMELINE REPORTS THE ROW; IT WRITES NO IDS.** `onMoveToLane(kind, id,
lane, patch)`. For a caption, a shape or an overlay a row IS a `layer_id` and this
file could have written one — but an audio row grouped by upload has no id, so
"put it on that row" can only be answered where the document is. Same division of
labour as `onDropAsset`.

⚠ **A FILE-GROUPED AUDIO ROW IS PROMOTED TO A REAL LAYER when something is
dropped on it**, taking its own clips with it. Those rows are grouped by upload on
purpose — that is what makes a razored take look cut rather than doubled — and
that grouping is precisely why the old `dropAsset` had to refuse the move. After
the promotion it is an ordinary layer row that happens to have started life as one
file. It is ONE undo: `setLayers` + `setAudioTracks` in the same handler, so React
batches them into a single render and the stack sees a single signature change.
A layer row holding more than one file is now named after the LAYER rather than
after whichever clip starts earliest, or the row would rename itself under you.

⚠ **ONLY A PLAIN, SINGLE-CLIP MOVE CAN CHANGE ROW.** A trim is about the clip's
own length; a GROUP move can span kinds, and "put these forty things on that one
row" is not an edit with a single meaning. Both are excluded up front.

⚠ **THE PICTURE ROWS ARE NOT A DESTINATION, AND THAT IS NOT AN OMISSION.**
`frames` is ONE sequence drawn as two rows filtered by ORIGIN (`laneShows`), so
which row a picture is on is READ OFF the clip rather than chosen — the same rule
`laneTakes` already states for a drop out of the Media pane. This is also the
answer to the third thing in the report ("when i do video trim so i see my image
layer conetnt move"): the two rows share one clock by construction, so a ripple
trim on either moves the other. **NOT FIXED, and it cannot be fixed here** — see
Next Steps.

**2. THE EMPTY-ROW PROMPT WAS SLICED IN HALF AT SHORT TRACK HEIGHTS.**
`.tl-track-empty` was `padding: 0.45rem 0.6rem` — a FIXED offset from the top of a
box whose height is a variable the vertical zoom writes (`--tl-track-h`, floor
`1.5rem`). Zoom the rows down and the text landed on the lane's bottom edge, where
the lane's own `overflow: hidden` took its descenders off. The audio row's
`padding: 0.9rem` override was worse: 14px of it in a 24px row.

⚠ **CENTRED BY `line-height`, NOT BY FLEX.** `.tl-track-add` is one line at the
lane's inner height, so it sits on the middle of the row whatever the zoom has
made that. Flex centring would have made the text an ANONYMOUS flex item and
`text-overflow: ellipsis` would have stopped applying to it — a long prompt would
then overflow instead of truncating, which is the bug the `nowrap`/`ellipsis` pair
exists to prevent.

**Verified:** `tests/editor_lane_move_check.py` is new — 19 checks, Chromium,
driving the real `<AnimaticEditor>` with the mouse. It drags a caption, a shape, an
overlay picture and an audio clip each onto another row and asserts in BOTH
directions (the destination gained it, the source lost it — which is what tells a
move from a copy); drags an audio clip onto a file-grouped row and asserts both
clips end up on ONE new row; asserts a HEAD GRIP dragged across rows trims and does
not move; asserts a shape dragged onto the picture row stays put; and measures
every empty-row prompt against its row at 1.5rem, the default and 6rem. ⚠ **BOTH
BUGS WERE PUT BACK AND WATCHED TO FAIL ON IT** — emptying `CROSS_LANE_KINDS` fails
all eleven move checks, and restoring the old padding fails the prompt check at
the short end by 5px (12px on the audio rows), which is the reported symptom to
the pixel. `editor_razor_check.py`, `selection_check.py`, `hidden_lane_check.py`,
`audio_razor_check.py`, `razor_check.py` and `audio_crossfade_check.py` all still
pass, and `npm run build` is clean. **Not otherwise opened in a browser.**

### 2026-08-19 — THE FIRST PICTURE STILL HAD NO HEAD GRIP (user-reported)

> "i see all place Ripple edit work but i see in video clip not cut in start but i
> see in last video clip i see Ripple edit fuction / why is happen / i not able in
> video layer only first video not abkle to Ripple edit"

Follow-up to the entry below, which gave every clip a grip at both ends **except**
the first picture in the sequence. That was deliberate and it was wrong. The
reasoning was sound as far as it went — a picture's head grip drags the CUT in
front of it, and the first picture has no cut in front of it — but "there is no cut
here" is not the same as "there is no edit here". The edit is **start later into
the clip**: the ripple trim-in, with everything after it moving up.

**⚠ IT IS THE ONLY PICTURE EDGE THAT TRIMS THE CLIP ITSELF.** `startHeadTrim` is
new, and the rule the two halves of the grip now share is *"it edits whatever is at
this clip's head"* — a cut where there is one, the start of the film where there
isn't. That is why the same-looking grip does two different things on the same row,
and it is not arbitrary.

**⚠ ON A VIDEO CLIP A HEAD TRIM MOVES `in_ms`, NOT JUST `duration_ms`.** `sourceAt`
reads `in_ms + t * speed`, so skipping `head` ms of TIMELINE has to skip
`head * speed` of FILE — otherwise the picture at 0:00 is unchanged and all the
trim did was throw away the end of the shot. `out_ms` is an absolute position in
the source and stays exactly where it is. Both fields go in **one** patch (hence
the new `onFrameChange` prop: `onResize` only ever carried a length), because
written apart they are two renders, two steps to undo through, and a project saved
between them has a clip that lost its head without losing the footage in it.

**⚠ THE TRAVEL IS BOUNDED UP FRONT, IN TIMELINE MS**, so the edge stops at
whichever wall comes first rather than hitting one and going on:
- trimming IN — the clip's own `MIN_MS` floor, **and** the last moment of source
  there is to show (`out_ms`, exclusive, so one ms inside it);
- trimming OUT — however much footage sits BEFORE `in_ms`. **Nothing, for a
  still**: it has no source to give back, so its head only goes one way.

Both bounds straddle 0 by construction, so "did not move" can never come back as
an edit — `clamp` returns its low bound when they cross, which is what makes a
hand-edited project already past its own `out_ms` refuse the trim instead of
inventing one.

**⚠ AND THE KEYFRAMES, AGAIN.** A Ken Burns push is stored relative to the frame's
own start, exactly as a caption's opacity is, so the same silent slide applies.
`trimKeyframesHead` was lifted out of `trimTimedClipStart` and is now shared —
⚠ **the one thing in `razor.js` that serves the picture sequence**, and the header
says so: the keyframe surgery is common because keys are stored the same way on
every kind of clip; the clip models are not, and those stay apart.

**⚠ WHAT SNAPS IS THE FAR EDGE, NOT THE ONE UNDER THE POINTER.** The head of the
first picture is pinned to 0:00 — it is the start of the film and cannot move — so
trimming into it moves the FIRST CUT, and that is the edge with something to line
up against: the **audio does not ripple**, so the cut can be pulled onto a beat.
Snapping the pinned edge would have snapped to nothing.

**Files:** `client/src/components/Timeline.jsx` (`startHeadTrim`, its branch in the
frame-drag effect, the grip's two-way handler), `client/src/animatic/razor.js`
(`trimKeyframesHead` extracted and documented as shared),
`client/src/components/AnimaticEditor.jsx` (`onFrameChange={patchFrame}`).

**Verified:** the trim was checked against the real `sourceAt` in both directions
at speed 1 and speed 2 — after trimming `head` ms the clip opens on exactly the
frame that used to be `head` in, and after extending it the old head reappears
`head` ms in. The `out_ms` wall, the `MIN_MS` floor, the `in_ms >= 0` wall and the
already-past-`out_ms` case were each exercised. Keyframe re-timing keeps the push
in step with the footage; ⚠ **an EASED segment re-normalises its curve over the
shorter span** — endpoints exact, interior differs — which was measured to be
**bit-identical to what `splitTimedClip` already does when the razor cuts an eased
track**, so it is the accepted property of `splitTrack`, not a new defect. Linear
tracks drift by zero. Both JSX files bundle clean. **Not opened in a browser and no
Playwright suite was run.**

### 2026-08-19 — ONLY AUDIO COULD BE TRIMMED FROM THE HEAD (user-reported)

> "see one thing i not able to edit start of asstes like image, video, shapes,
> text and caption. i want add fuction i able to Ripple edit in start in layer of
> asstes. only i do able in last of assets but you see in audio i Ripple edit both
> side"
>
> "and name remove this text in Timeline panel (audio 2:40 — video ends early) not
> need to view of user"

**EVERY CLIP ON THE TIMELINE HAD ONE GRIP, ON THE RIGHT. Audio had two.** The
asymmetry was in the CSS as much as the JSX: `.tl-handle-l` existed but was
scoped `.tl-audio-clip .tl-handle-l`, so sound was structurally the exception.
It is now a general rule in `animatic.css`, and every lane renders both grips.

**⚠ A HEAD TRIM IS A THIRD MODE, NOT A RESIZE.** `startClipDrag` had `"move"` and
`"resize"`; `"trim-start"` is the new one and the distinction is the point — the
tail grip moves the END and leaves the start, a move changes neither length nor
far edge, and a head trim **moves the start and nails the end down**, so it is the
only one of the three that writes both numbers. It is also the only one that has
to re-time keyframes.

**⚠ THE KEYFRAMES ARE THE WHOLE RISK, AND THE FAILURE IS SILENT — AGAIN.** Key
times are relative to a clip's own start, so moving that start and leaving the
keys alone slides the entire animation by however far you trimmed, while the clip
still validates and still plays. `trimTimedClipStart` is new in
`client/src/animatic/razor.js` and solves it by reusing what the razor already
had: **a trim-in IS the tail half of a split at the new head**, so
`splitKeyframes` plants a key there carrying the value AND the ease that were
running. Trimming OUT (making the clip longer at the head) only shifts the keys
forward and plants nothing — `valueAt` holds at the first key rather than
extrapolating, so the new head holds the value the clip used to open on, which is
what it looked like before. Returns a PATCH, or null when the drag came back to
where it started.

**⚠ ON A PICTURE THE HEAD GRIP IS THE CUT BEFORE IT, NOT A TRIM OF THAT CLIP.** A
frame has no `start_ms` — its start is the sum of every hold before it — so
"shorten this one's head" can only ripple everything left and move its FAR edge,
which is precisely what its tail grip already does; as an edit it would have been
a duplicate. Moving the cut is the only thing that puts the edge you grabbed under
the pointer, so the picture lane's left grip is `startResize(e, frames[i - 1],
i - 1)` — one call, and it inherits **ripple / rolling (B / N)** and the snapping
from the existing implementation for nothing. Two clips get no head grip: the
first picture (its start is 0:00 and there is no cut there) and anything narrower
than `BOTH_GRIPS_MIN_PX` (24px), since two 8px strips would leave no middle to
press for "select" or drag for "move" — below that a clip behaves exactly as every
clip did before, tail grip only.

**AND THE LENGTH BADGE IS GONE.** `audio 2:40 — video ends early` / `✓ matches the
audio` was a sentence of running commentary on the busiest bar in the editor, and
it was never news: the ruler already runs to the end of the audio and the
transport clock counts past the last picture, so the timeline SHOWS what the badge
described. ⇔ Fit to audio, a step to the right, is still the fix it pointed at.
`lengthMatches` had exactly one reader and went with it, as did the `.an-match`
rules.

**Files:** `client/src/animatic/razor.js` (`trimTimedClipStart`),
`client/src/components/Timeline.jsx` (the `"trim-start"` mode, both new grips,
`BOTH_GRIPS_MIN_PX`), `client/src/components/AnimaticEditor.jsx` (badge removed),
`client/src/styles/animatic.css` (`.tl-handle-l` generalised, `.an-match` deleted),
`client/src/styles/animatic-tools.css` (audio keeps only the `z-index`).

**Verified:** the keyframe re-timing was unit-tested in both directions on a
three-key `opacity` track — the interpolated value at every absolute time is
identical before and after the trim, in and out, and the clamps hold at 0:00 and
at the 100ms floor. Both JSX files bundle clean under esbuild. **Not opened in a
browser and no Playwright suite was run** (the user asks for browser runs
explicitly). ⚠ `tests/editor_razor_check.py` asserts the grips go
`pointer-events: none` under the blade — the new ones carry `.tl-handle` so they
are covered, but that suite has not been re-run.

### 2026-08-19 — A SCRUB SELECTED THE TIMELINE AS TEXT, AND THE RULER COULD NOT NAME A FRAME (both user-reported)

> "see when i slide timeline stick so text selectd appeir in timeline fix it please"
>
> "and second i wnat in timeline so time like this see image 3 / with small samll
> line with time sec like this"

**EVERY PRESS ON THIS BAR CALLED `preventDefault` EXCEPT THE ONE THAT SCRUBS.**
`startLanePress` already had it, with a comment saying exactly why ("or the
browser starts a TEXT SELECTION under the band: the lanes are full of labels").
`startSeek` did not — and it is bound to the *two* surfaces drawn ABOVE every
lane, the ruler and the playhead grip, so a scrub across the bar highlighted the
track names, the clip titles and the empty-lane prompts blue behind the playhead.
Fixed in `Timeline.jsx:startSeek`. ⚠ **`.tl-wrap` also became `user-select: none`
wholesale** (`animatic-text.css`): every drag on the timeline means something —
scrub, marquee, move, trim — and there are no inputs on it, so a handler-by-handler
guard is one missed handler away from the same bug. `.tl-ruler`'s `cursor: text`
became `ew-resize`; a scrub surface should not advertise itself as a selection one.

**AND THE RULER WAS READ IN PROSE.** `0:05` cannot name a frame, which is what a
ruler you cut against is for. `tickStep`/`TICK_STEPS` are gone, replaced by
`tickSteps` + `rulerTicks` + `formatTimecode` (exported) in `Timeline.jsx`, and a
labelled major tick with bare minors between it — `.tl-tick` is now the LINE and
`.tl-tick-label` the timecode hanging off the majors. Three things to know:

- ⚠ **SUB-SECOND STEPS ARE THE DIVISORS OF FPS**, not a fixed ladder. At 24fps a
  3-frame step reads `00, 03 … 21, 01:00`; a 5-frame one reads `20, 01:01` and
  stops meaning anything. Above a second they are whole seconds for the same
  reason. That is why the ladder is built per-fps: half a second is 12 frames at
  24 and 15 at 30.
- ⚠ **THE TICKS ARE CULLED TO THE VISIBLE WINDOW** (`viewBox.sl`/`vw`, already
  read for the scrollbars). The minor step is only bounded below by 7px, so at
  600px/s a 70s cut is 1,681 ticks; the ruler is `position: sticky` and
  re-rendered on every scrub, and 49 nodes is what a screenful actually needs.
- ⚠ **NO x-OFFSET ON A TICK.** The old `.tl-tick` carried `transform:
  translateX(2px)`, harmless under a text label and a fifth of a frame of lie
  under a line that marks a time.

`fps` is a new `<Timeline>` prop, fed from `settings.fps` so the ruler counts in
the rate the film is EXPORTED at. Only the ruler reads it — every duration on the
bar is still milliseconds, because that is what the clips are stored in.
`--tl-ruler-h` went 1.15rem → 1.5rem for the second row, and `.tl-ruler` now reads
the variable rather than repeating the number (`.tl-gutter-ruler`, which keeps the
labels level with the tracks, is sized from the same one).

**Files:** `client/src/components/Timeline.jsx`,
`client/src/components/AnimaticEditor.jsx` (the `fps` prop),
`client/src/styles/animatic.css` (ruler + ticks),
`client/src/styles/animatic-text.css` (`--tl-ruler-h`, `user-select`).

**Verified:** both JSX files bundle clean under esbuild (only the pre-existing
`import.meta`/iife warning), and the step ladder was checked at 2 / 10 / 40 / 120
/ 300 / 600 px-per-sec against 12 / 24 / 25 / 30 fps — every label lands on a
divisor of the second and rolls to `:00`, and culling drops 1,681 ticks to 49.
**Not opened in a browser and no Playwright suite was run for this** (the user
asks for browser runs explicitly).

### 2026-08-19 — THE RAZOR CUT WHATEVER IT LIKED, AND THE ⓘ RULE WAS BEING IGNORED (both user-reported)

> "when i cut so my cut icon in top of timeline in time sec show row and i click
> so i notice my image clip cut but this not happen again when i go image layer
> then i cut only image layer and same do in all layer not cut from any where"
>
> "when i cut audio so i not see cut icon like when i cut video"
>
> "i told you earlier not show too much text of information always make I icon
> buttun and show text information … i see in midea effects panel too"

**THE RAZOR CUT THE PICTURE FROM ANYWHERE ON SCREEN, and it was one function.**
`toolPress` existed because "hand and zoom and razor mean the same thing wherever
they land" — and that is true of two of the three. It ran for the RULER and for
the empty part of EVERY lane, and what it called was `onSplitAt`, the PICTURE
razor. So a press in the seconds row cut an image clip; so did a press on an
empty stretch of the shapes row. There was never a way to say "cut *this* layer",
because the callback was given a TIME and left to work out what it meant.

Fixed by making the razor name its target. ⚠ **ONE `onRazor(kind, id, ms)`
REPLACES `onSplitAt` AND `onSplitAudioAt`** — two callbacks was the shape of the
bug, since "which list does this cut belong to" was answered by which one the
call site happened to reach. Every lane now identifies its own clip at the press
(`razorPress` in `Timeline.jsx`, called from the clip bodies **and** from the drag
starters, so a press on a trim handle or a fade grip cuts instead of resizing),
and `startLanePress` reports `kind: null` — the editor then says "the razor cuts
a CLIP" rather than cutting something on another row. The ruler goes on scrubbing
while the razor is up, which is what Premiere does and what the reporter wanted.

**AND THREE LAYERS HAD NO RAZOR AT ALL.** Captions, shapes and overlay pictures
could not be cut by any gesture; "cut each particular layer" needed them to work,
so `client/src/animatic/razor.js` is new. ⚠ **THE KEYFRAMES ARE THE WHOLE RISK
THERE, and the failure is silent**: key times are relative to a clip's own start
and `valueAt` HOLDS at the first and last key rather than extrapolating, so the
obvious split ("keep the keys before the cut, shift the rest back") loses the
value AT the blade on both halves — the head freezes early, the tail starts late,
and the animation JUMPS at the edit while the document still validates. So
`splitTrack` plants a key at the cut on both sides, carrying the ease that was
running. ⚠ It is a THIRD razor rather than a generalisation of the other two, and
the file says why: a picture has no start of its own, an audio clip has a file to
seek into, and a free clip has neither and has keyframes. One function taking the
union of three clip models is how a half gets a field that meant something on a
different kind of clip.

**⚠ THE CUT CURSOR IS NOW ON THE CLIPS AND NOWHERE ELSE**, which is both halves
of the second report. It was on `.tl-inner`, `.tl-bar` and `.tl-ruler` — so the
RULER (which must not cut) wore the blade, and an AUDIO clip did not, because
`.tl-audio-clip` sets its own `cursor: grab` and beat the container's rule. Every
kind of clip is now named, `*` included so the pointer does not change as it
crosses a caption's own text, and the grips inside a clip get
`pointer-events: none` while the razor is up — which is what makes it ONE icon
per the request, rather than `ew-resize` over a fade grip and `col-resize` over a
trim handle. Ctrl+K also cuts the selected clip whatever kind it is now, instead
of only audio-or-picture.

**THE ⓘ RULE.** The Effects library printed every entry's whole description
beside its name, and it was the WIDER half of each row: the one word you were
scanning for ("Blinds up") was the part getting the ellipsis, and it
`display: none`d itself under 1100px so the answer to "what does this do"
vanished on a narrow pane. Now each entry carries an ⓘ. ⚠ **`InfoDot` IS EXPORTED
FROM `PropGroup.jsx` AND IMPORTED, not redrawn** — a second circle that is nearly
the same size stops reading as a convention and starts reading as decoration. The
row became a wrapper (`div` → draggable button + ⓘ + the note as a wrapped row),
because a button cannot hold another button and because a note that opened on
clicking the ROW would fire on every attempt to add an effect. The new crossfade
chips lost their `opt-chip-note` too: three chips carrying a sentence each made
one control taller than most of the pane, and the row's existing ⓘ can explain
all three together, which is where the difference between them actually lives.

Files: `client/src/animatic/razor.js` (**new** — `splitTimedClip`, `splitTrack`,
`splitKeyframes`, `timedClipAt`, `RAZOR_KINDS`, `MIN_SPLIT_MS`);
`Timeline.jsx` (`onRazor` replacing the two split props, `razorPress`, the razor
out of `toolPress`, guards on `startResize` / `startClipDrag` / `startAudioDrag` /
`startFadeDrag`); `AnimaticEditor.jsx` (`razorAt`, `splitTimedAt`, `onRazor`,
Ctrl+K across every kind); `EffectsLibrary.jsx` (the ⓘ per entry);
`properties/PropGroup.jsx` (`InfoDot` exported); `properties/AudioProperties.jsx`
(chip notes dropped); `animatic-tools.css` (the razor cursor block and the
`.fx-entry-wrap` / `.fx-entry-note` rules).

**Verified — and ⚠ BOTH REGRESSIONS WERE PUT BACK AND THE TEST WAS WATCHED TO
FAIL**, which is the only way to know a UI test is not vacuous:
- `tests/editor_razor_check.py` is **new, 21 checks, all green** — the real
  `<AnimaticEditor>` in Chromium with a clip on all five kinds of lane, asserting
  after every press that *this* lane gained a clip **and no other lane changed*.
  With the old `toolPress` restored it fails with `frame: 2 → 3` on the ruler
  press; with the old CSS restored it fails with `grab` on audio, text, shape and
  overlay.
  ⚠ **AND ITS GEOMETRY IS LOAD-BEARING.** The first draft aimed at the middle of
  the ruler, which on two 4s shots is exactly the cut between them — the OLD code
  refused that for being 0ms from an edit point, so the test passed against the
  bug it was written for. Every "nothing to cut" press now lands at 2.0s, which
  is 2.0s clear of both edges of the first shot, and the picture is then cut at
  the SAME x. Two more misleading failures came from `page.mouse` clicking a
  POINT while the lane was scrolled out of the pane (the zoom scrollbar ate the
  picture press, the status bar ate the audio one, both reported as "the razor
  did not cut") — `press()` scrolls into view and verifies `elementFromPoint`
  before clicking, and returns a miss as a miss.
- `tests/razor_check.py` is **new, 15 checks, all green** — the keyframe surgery
  under node, including that the value either side of the blade matches through
  `valueAt` and that a caption with no `keyframes` field does not gain one.
- `audio_crossfade_check.py` (27), `audio_razor_check.py`,
  `editor_effects_drop_check.py`, `selection_check.py`, `keyframe_ops_check.py`,
  `hidden_lane_check.py`, `video_clip_check.py` all still pass; `npm run build`
  clean. **The ⓘ and the cursor have not been looked at by a human** — see Next
  Steps.

### 2026-08-19 — AUDIO TRANSITIONS: THE THREE CROSSFADES, AND THEY ARE THREE CURVES

Asked for Premiere's **Audio Transitions → Crossfade** folder — Constant Gain,
Constant Power, Exponential Fade — in the Effects library.

**⚠ THERE IS NO NEW OBJECT, AND THAT IS THE DESIGN.** The picture has an
`AnimaticTransition` record because the picture sequence is a CHAIN: a cut there
is a position between two links, so it needs something to be anchored to. Audio
clips are placed absolutely (`start_ms`) and `audio_graph` already mixes whatever
overlaps — so **two clips that overlap, one fading out while the other fades in,
already ARE a crossfade**, at both ends of the app. What was missing was the
CURVE. So the whole feature is two new fields (`fade_in_curve`, `fade_out_curve`)
plus the gesture that sets both ends of one cut at once. No new render path, no
new wire object, no migration.

**⚠ `acrossfade` IS NOT USED AND CANNOT BE** — it concatenates two streams, which
would shorten the timeline. Same objection that made picture transitions
boundary-local.

**⚠ THE CURVES ARE FFMPEG'S, TRANSCRIBED FROM `fade_gain()` IN `af_afade.c`.**
`afade` is what actually shapes the exported audio and the editor only PREDICTS
it, so a nicer-looking curve in the browser would be a preview that lies about
the MP4:

| Library entry | curve id | `afade` | gain |
|---|---|---|---|
| Constant Gain | `linear` | `tri` | `x` |
| Constant Power | `power` | `qsin` | `sin(x·π/2)` |
| Exponential Fade | `exponential` | `exp` | `10^(−5(1−x))` |

`linear` is the default everywhere **because it is what already shipped** —
`afade`'s own default is `tri`, so every fade in every saved animatic keeps its
shape and nothing needed migrating. Both sides FOLD an unknown curve to `linear`
(the `AnimaticTransition.kind` rule), so a project from a newer client opens.
⚠ Premiere's Exponential is gentler than ffmpeg's `exp`; matching the encoder we
can measure beats matching an editor we cannot, and only one of the two ends up
in the file. **This was the user's explicit call.**

**⚠ AND HERE AUDIO DIVERGES FROM THE PICTURE ON PURPOSE — IT EATS MEDIA HANDLES.**
A picture transition refuses to overlap its shots because the timeline would get
SHORTER and every cut position would move. None of that is true for audio, so
`crossfadePatch` does the real Premiere thing: it grows the clips into the file
either side of the cut (head handle = `offset_ms`, tail handle = `clipRoomMs`).
Three rules, each of which was arrived at by getting it wrong first:
1. **The outgoing clip's TAIL is spent before the incoming clip's HEAD.** Letting
   clip A play on over clip B moves nothing; pulling clip B earlier shifts when
   its content is heard, and a voice cue landing on a picture cut does not want
   moving half a second because you dropped a preset. So "centre it on the cut",
   Premiere's default alignment, is deliberately **not** copied — there is no
   transition rectangle here whose position could look wrong, only clips that did
   or did not move.
2. **How far it grows is SETTLED, not solved.** The overlap can be no longer than
   either clip covering it, and how long each clip IS depends on how far it grew.
   A one-pass answer stretched the outgoing clip a full second against a 400ms
   neighbour and left 600ms where BOTH played at full level — a doubled mix,
   which is the one thing a crossfade must never produce.
3. **No handles anywhere → it dips through the cut and SAYS SO** (`overlapped:
   false`). Two whole files butted together is the everyday way to get here.
   Premiere refuses this as "insufficient media"; this still leaves you the fades.

**Both fades span the WHOLE overlap**, never part of it: anywhere inside the
overlap where only one is ramping, both clips are at full level.

**⚠ A SECOND DRAG MARKER WAS REQUIRED, `application/x-anim-afx`.** `getData` is
blank during `dragover`, so the marker type is the only thing a row can decide
on mid-drag — and the rows that take a crossfade (audio) are not the rows that
take an effect or a video transition (picture). One shared marker would light
every row up for every drag and refuse half of them after the drop, which is the
"no entry" cursor arriving one gesture too late.

**Also fixed on the way past:** `startFadeDrag` set `fadeDraft.id` to
`track.upload_id` while `fadeOf` looks it up by `clipId`, so on a file the razor
had cut in two, dragging either piece's fade grip drew **no live wedge at all** —
it only appeared on release, which reads as a broken handle rather than a slow
one. Identical in pre-razor projects (where a clip's id IS its upload), which is
why it went unnoticed.

Files: `client/src/animatic/audio_mix.js` (`FADE_CURVES`, `FADE_CURVE_INFO`,
`fadeCurve`, `curveGain`, and `fadeGainAt` now reads a curve per END);
`animatic.py` (the twin: `FADE_CURVES`, `FADE_FF_CURVE`, `fade_curve`,
`curve_gain`, `fade_gain_at`, and `:curve=` stated on every `afade`);
`server/schemas.py` (`AnimaticAudio.fade_in_curve` / `fade_out_curve`);
`client/src/animatic/audio_clips.js` (`DEFAULT_CROSSFADE_MS`, `fadeEndPatch`,
`crossfadePatch`, `crossfadeTarget` — ⚠ all in FILE time, taking no `totalMs`,
because `trim_ms` is written from a play length and the video's clamp would get
baked into a clip hanging past the last frame); `fx_library.js` (a third family
`audioTransition`, the `KNOWN` table, `AFX_DRAG_TYPE`, `fxMarkerType`);
`EffectsLibrary.jsx` (which marker); `Timeline.jsx` (`afx` in `DRAG_KINDS`,
`laneTakes`, the `drop-onto` ring on an audio clip, curve classes on the two
wedges, the `fadeDraft` id fix); `AnimaticEditor.jsx` (`laneSiblings`,
`addCrossfade` — **one `setAudioTracks` writing both clips, so it is ONE undo
step**, the `fxAudioTransition` branch in `dropAsset`, and the click path in
`addFxFromLibrary` resolved by the razor's own three lines);
`AudioProperties.jsx` (an "In shape" / "Out shape" chip row, drawn only where
there is a ramp to shape); `animatic-tools.css` (the wedge gradients — one
colour-interpolation hint per curve, solved from `1 − gain(x) = 0.5`, so it stays
theme-aware) and `animatic-lanes.css`.

**Verified:** `tests/audio_crossfade_check.py` is **new, 27 checks, all green** —
old projects still open, the curve reaches the graph, the JS and Python curves
agree to 1e-12 over a 41-point grid (plus the whole `fadeGainAt`/`fade_gain_at`
envelope, so the window and the curve are checked together), the browser's
advertised `afade` name matches the exporter's mapping, every crossfade case as
the patch it must produce, and — the point of the whole thing — **two exports
decoded back out of the MP4 and measured**: through two uncorrelated tones a
constant-gain crossfade scoops to **0.707×** through the middle and a
constant-power one holds at **1.000×**, a measured 3.0 dB apart. ⚠ The two tones
are the fixture, not a detail: cross one sine with a copy of itself and the
amplitudes add, constant gain holds perfectly and constant power comes out 3 dB
LOUD — the opposite result from a test that looks the same.
`audio_mix_check.py` (70), `audio_razor_check.py`, `transition_check.py` and
`editor_effects_drop_check.py` all still pass unchanged; `npm run build` clean.
`effects_parity_check.py` still exits 2 for the pre-existing reason (headless-gl
will not build here). **NOT DRAGGED BY HAND IN A BROWSER** — see Next Steps.

### 2026-08-19 — THE EDITOR WENT BLACK THE INSTANT AN EFFECT WAS DROPPED (user-reported)

> "when i drag and drop gamma and exposure effects in timline so my screen is
> black right now"

**One shadowed name.** `EffectsPanel.jsx` had a module-level helper
`shown(value, field)` — the thing that turns a stored 1.0 into the "100" a
percentage field shows — and, inside the component, a local
`const shown = new Map(...)` holding each effect's resolved parameters. The
local one shadows the module one for the whole component body, so both calls to
`shown(...)` in the parameter rows were **calling a Map**:

    Uncaught TypeError: shown2 is not a function
      at EffectsPanel (EffectsPanel.jsx)

It threw while RENDERING, and React unmounts the tree it was rendering — the
monitor, the timeline and the library all went with it. **That is the black
screen.** The Map is now `atPlayhead`; nothing else in the file changed.

⚠ **THE MATHS WAS NEVER WRONG, WHICH IS WHY EVERY TEST PASSED.** The bug was in
the pane the drop OPENS: `addEffectToClip` calls `openGroup("look:effects")`, so
dropping an effect is exactly the gesture that renders the row that threw. Every
kind was affected, not just the two in the report — gamma and exposure were
simply the two the user reached for.

**Why nothing caught it, and what does now.** Three effects tests existed and
all three were green throughout:

| | what it proves |
|---|---|
| `tests/effects_check.py` | the PYTHON numbers, pinned to golden values |
| `tests/effects_parity_check.py` | the GLSL agrees with them (needs headless-gl) |
| `tests/monitor_effects_check.py` | the MONITOR draws a chain handed to it |

None of them ever ran the EDITOR, so none could see a Properties pane crash.
**`tests/editor_effects_drop_check.py` is new and does**: it mounts the real
`<AnimaticEditor>` in Chromium with every API call answered by Playwright's
router, opens the Effects tab, and performs the drag — dispatching
`dragstart`/`dragenter`/`dragover`/`drop` over one shared `DataTransfer`,
because Playwright's mouse does not start an HTML5 drag in a headless browser.
It asserts the monitor still exists, is still drawing a picture rather than
black, reports no GL error, and that nothing reached `window.onerror` or
`console.error` — then reads the value out of each control the drop added, so a
pane that renders while every field is blank cannot pass either. It **fails
loudly on the un-fixed code** and prints the TypeError.

**Two coverage gaps closed at the same time, both of the same kind — a claim in
a comment that the list below it did not keep.**

- `tests/monitor_effects_check.py` said "⚠ EVERY KIND IS HERE" while covering
  four of eleven. Exposure, gamma, temperature, hue, sepia, posterize and the
  chroma key now have cases, **each twice**: once at a value that moves the
  picture, and once FRESHLY DROPPED with `params: {}`. Those two prove different
  things and neither is enough — a value case passes its own numbers in, so it
  says nothing about whether a DEFAULT reaches the shader, which is the state a
  dropped effect is actually in. All 96 checks pass in Chromium on SwiftShader.
- `tests/effects_parity_check.py` had **no case for any of the six** — the GLSL
  and its NumPy twin had never been compared for them. Thirteen cases plus a
  stacked chain are in now.

⚠ **`headless-gl` STILL WILL NOT BUILD ON THIS MACHINE** — `npm install
--no-save gl` fails in node-gyp with "could not find a version of Visual Studio
2017 or newer", so `effects_parity_check.py` still exits 2 rather than passing.
Its new cases are for a machine with the C++ workload. **The shaders HAVE now
executed**, though, and that is no longer an open question: `monitor_effects_check.py`
and the new editor check both run them in Chromium on **SwiftShader**, which is
a real GL driver rather than a stub, and both compare the result to the Python
exporter. The file header now says so, so nobody reads its exit 2 as "the
shaders have never run".

**Files:** `client/src/components/EffectsPanel.jsx` (the fix — one rename),
`tests/editor_effects_drop_check.py` (new), `tests/monitor_effects_check.py`,
`tests/effects_parity_check.py`.

**Verified:** the new editor check (38 checks) — red before the fix with the
TypeError printed, green after; `monitor_effects_check.py` (96); plus
`effects_check.py`, `render_parity.py`, `transition_check.py`,
`keyframe_ops_check.py`, `selection_check.py`. `effects_parity_check.py` exits 2
for the missing native module, as it is designed to.

---

### 2026-08-19 (latest) — STEP 3: FAMILIES ON THE TREATMENT ROW, AND SIX POINT-WISE GRADES

Two halves, both descriptor-driven, neither one a new widget.

**(a) The Treatment row is grouped.** Step 2 took it from 4 chips to 12, which is
a row you scan rather than read. It is now five families — **Fade · Wipe · Shape ·
Slide · Dip** — from a `family` field on the `TRANSITIONS` descriptor plus
`transitionsByFamily()`, drawn with the `PropRow full` / `opt-chip` primitives
the pane already had. ⚠ **PRESENTATION, SO DELIBERATELY NOT TWINNED IN PYTHON** —
`animatic_render.py` carries no `label` or `note` either, for the same reason:
which chips sit under which heading cannot change a pixel. ⚠ **A kind whose
family names no heading lands in "Other" rather than vanishing**, the same
catch-all rule `fx_library.js` uses. ⚠ **NOT `PropGroup` PER FAMILY**: five
collapsible sections to open before you can see twelve chips is worse than the
flat row it replaced.

⚠ **THIS IS A SECOND, DIFFERENT GROUPING OF THE SAME TWELVE KINDS** from the one
in `fx_library.js`, and it is on purpose. The library answers "what can I add",
where a dip belongs under Dissolve because *Dip to Black* is what an editor goes
looking for. The pane answers "what is this cut doing", where a dip is its own
family — the only treatment that puts NO second picture on screen. Filing them
identically would make one of the two wrong.

**(b) Six point-wise effects** — Exposure (stops), Gamma, Temperature & tint,
Hue rotate, Sepia, Posterize. Each is an `EFFECT_PARAMS` entry in both languages,
a GLSL chunk in `effects.js`, a NumPy function in `animatic_effects.py`, and a
label. **Blur, sharpen and grain stay out**: the monitor grades in ONE fragment
pass with no neighbourhood, so they need a second pass and an answer to "at which
resolution", which the preview and the export do not share.

**⚠ FOUR THINGS THAT WOULD HAVE BEEN BUGS.**

1. **`EFFECT_PARAMS` IS APPEND-ONLY.** An effect reaches the shader as its INDEX
   in that table (`fxIndex`). Insert a kind in the middle and every kind after it
   silently re-numbers — a saved project comes back graded by the wrong effect,
   on every machine, with nothing reporting it.
2. **`uFxArgs` is now packed POSITIONALLY off the descriptor** — the kind's
   numeric params in declaration order fill x/y/z. That replaced a hand-written
   `chroma ? similarity : amount` special case and reproduces it exactly (chroma
   declares similarity, smoothness, spill in that order). It is why six effects
   needed no change in `compositor.js` at all.
3. **Hue goes through YIQ, not the SVG `feColorMatrix hueRotate` matrix.** That
   matrix is built on the 709 weights; this project's luma is 601. Mixing them
   would mean a rotation of 0° did not quite agree with saturation 1 or with
   `Image.convert("L")`. YIQ's Y *is* `LUMA`, so a rotation cannot change
   brightness — asserted, not assumed (`a hue rotation leaves the luma where it
   was`).
4. **Posterize uses `floor(x + 0.5)`, never a `round()`.** numpy rounds halves to
   EVEN and GLSL rounds them away from zero, and a band edge is exactly where the
   halves land — `round` would put whole regions of a posterised frame one band
   apart between monitor and export.

Also: `EffectsPanel`'s `FIELD` gained `places`, because stops is the one
parameter whose natural unit is neither a percentage nor a whole number and
rounding it to an integer would look like it worked.

**Verified.** `tests/effects_check.py` — 21 new goldens, every effect pinned at
its no-op value first, all pass. `tests/effects_parity_check.py` source half —
34 checks, 0 failures, including a shader branch per new effect. `render_parity.py`
and `transition_check.py` still pass. `npm run build` clean. The Treatment row
groups all 12 kinds with none unreachable; the library is 38 entries with nothing
in "Uncategorised".

**⚠ NOT verified.** Same gap as Step 2 and it has now grown: **no shader in this
work has ever executed.** headless-gl is not installed, so the pixel half of
`effects_parity_check.py` — the only thing that would prove the six new GLSL
chunks match their NumPy twins — does not run. **Nothing has been opened in a
browser.** `tests/monitor_effects_check.py`'s one failure ("LUT then brightness")
is pre-existing, in the uncommitted `dispose()`/LUT work already in the tree.

**⚠ A GOTCHA THAT BIT TWICE.** A backtick inside a `/* glsl */` template literal
ends the JS string, and the parse error surfaces dozens of lines away. Do not
write `` `uFxArgs` `` in a shader comment. There is now a guard for it in the
scratch tooling; a real one belongs in `effects_parity_check.py`.

### 2026-08-19 (latest) — A REVEAL TRANSITION IS A MASK, NOT A COMPOSITING STAGE

Eight new transitions — **Diagonal, Split, Iris, Diamond, Box, Clock, Blinds,
Checker** — plus a **soft edge** on all of them and on the wipe. The interesting
part is not the list, it is that adding them needed **no new shader program and
no extra framebuffers**.

**THE CORRECTION THAT MADE THIS SMALL.** The plan of record said Phase 0 needed a
transition program with `mix(getFromColor, getToColor)`, gl-transitions style,
and a second render target to hold the outgoing picture. That would have thrown
away the rule `_transition_canvas` documents at `animatic.py`: *the incoming
picture is composited OVER the outgoing one, not blended with it*, so that "a
caption keyed out of the arriving shot reveals the shot it is arriving over, not
black". Under a two-texture mix, clip B's blend mode, chroma key and per-clip
mask have nothing left to blend against. But look at what a reveal actually is:

```
wipe at 50%  =  show the incoming picture where uv.x < 0.5
mask         =  show this picture where it is inside the region
```

Identical operation — and both renderers already had it (`maskCoverage` in
`effects.js`, `mask_coverage` in `animatic_effects.py`, each applied as one
`a *= …`). **So a transition matte is a second mask on the incoming picture,
driven by progress instead of by keyframes**, multiplied in one line further out
than the clip's own mask. Composite-over, blend modes, chroma keys and masks all
keep working for free; `MAX_EFFECTS` and the `uFxArgs` budget are untouched.

**What landed**

| | |
|---|---|
| NEW `client/src/animatic/gl/shaders/mattes.js` | One GLSL chunk per shape — linear, diagonal, split, radial, diamond, box, angular, blinds, checker. Exported strings, exactly as `effects.js` is, so the bare-`node` parity harness imports the source the browser compiles |
| NEW `animatic_transitions.py` | The NumPy twin, beside `animatic_effects.py` and reusing its `smoothstep` |
| `layer.js` | `uMatte*` uniform block + `MATTE_*`/`DIR_*` defines generated from the model; **one line**: `a *= matteCoverage(…)` right after the mask multiply |
| `compositor.js` | `_setMatte()`, called **unconditionally** on every `layer()` |
| `ProgramCanvas.jsx` | Every reveal is now one branch; `revealRegion`/`clipTo` deleted |
| `animatic.py` | `_transition_canvas` multiplies the matte into B's alpha; `_wipe_box` deleted |
| `transitions.js` + `animatic_render.py` | `MATTE_KINDS`, `TRANSITION_MATTE`, `TRANSITION_PARAM_RANGE`, the eight kinds, `softness` + `count` |
| `fx_library.js` | Filed as `Iris` and `Wipe Patterns` — the library went 13 → 32 entries |
| `TransitionProperties.jsx` | An `Edge` slider and a `Bands`/`Squares` field, built from `TRANSITION_PARAMS` like every other row |

**⚠ FOUR THINGS THAT WOULD HAVE BEEN BUGS, and are worth not re-discovering.**

1. **`_setMatte` runs on EVERY layer, never only when a matte is passed.**
   Uniforms live on the program, not the draw call. Set it conditionally and the
   transition's matte goes on to cut holes in the shapes, overlays and dip veil
   drawn after it in the same frame.
2. **A dissolve is the constant matte and is deliberately NOT implemented as
   one.** `apply_matte` rounds on the way back to 8 bits, `_faded_layer`
   truncates — routing a dissolve through the matte would move every blended
   pixel by up to one level, on the one transition that has shipped since the
   beginning. It stays on `_faded_layer`.
3. **The threshold travels FURTHER than 0–1**, by the feather either side, so
   the matte is *exactly* empty at progress 0 and *exactly* full at progress 1 at
   every softness. Ramping 0→1 instead leaves half a feather showing at both
   ends and the shot jumps on the frame either side of every soft transition.
4. **The wipe's edge moved by up to one pixel column.** It used to be an integer
   box (`int(round(width * m))`) cropped and pasted; it is now wherever a pixel
   CENTRE crosses the threshold — the same rule `mask_coverage` already uses. The
   old preview (a clipped quad, rasterised on centres) and the old export
   disagreed by up to half a pixel anyway, so this makes them agree *by
   construction*. No test pinned the old boundary; the change is deliberate.

**Verified.** `tests/render_parity.py` passes — including the cross-language run,
so JS and Python resolve the new parameters identically. `tests/transition_check.py`
passes end to end through a real ffmpeg encode, wipe in all four directions.
`tests/effects_parity_check.py`'s source-level half passes with new drift guards
for `MATTE_KINDS` order, `TRANSITION_MATTE`, direction numbering, a field per
shape, and the alpha multiply itself. `tests/effects_check.py` passes.

**⚠ NOT verified, honestly.** The GLSL has been parsed, its defines checked and
its structure asserted, but **it has never run on a GPU here** — headless-gl is
not installed (`cd client && npm install --no-save gl`), so
`effects_parity_check.py` exits before the pixel comparison. **Nothing has been
looked at in a browser.** `tests/monitor_effects_check.py` has one failure,
"LUT then brightness" — it is **pre-existing** and unrelated: it sits in the
uncommitted `dispose()`/LUT work already in the working tree, and the matte diff
to `gl/` is purely additive apart from the `layer()` signature.

### 2026-08-19 (latest) — EFFECTS ARE A LIBRARY YOU DRAG FROM, IN THE MEDIA PANE

User-reported, and the complaint was about WHERE they live: the only way to
reach an effect was a `<select>` inside the Properties pane, on a clip you had
already selected. So there was no answer to "what can this editor do" — you had
to already have the right clip picked to find out. **Effects is a third tab in
the Media pane now, beside Media and Shapes**, as a folder tree you drag onto
the timeline. The reference was Premiere's Effects panel and the shape is the
same: `▸ Video Effects` / `▸ Video Transitions`, sections inside them, entries
inside those.

**⚠ THE MEDIA PANE IS THE SHELF; PROPERTIES IS STILL WHERE A CHAIN IS MANAGED.**
Two panes, two questions — "what can I add" and "what is on this clip" — and the
split is deliberate rather than a half-move. A chain needs its parameters, its
order, its ⏱ keyframe rows and a mask editor, which is a pane's worth of room;
the library needs to be readable at a glance and to grow to thirty entries. The
`+ Add an effect…` dropdown in Properties STAYS: it is the only way to add one
without a mouse, and deleting a working path because a nicer one now exists is
how a feature ships broken for keyboard users.

**⚠ AN ENTRY IS A PRESET, NOT A KIND — `kind` IS NOT UNIQUE IN THE LIBRARY.**
There is one `wipe` in the renderer and it takes a direction, but the browser
lists FOUR, one per direction, because "Wipe up" is the thing you actually want
to drag and reaching it as "drag Wipe, then hunt for the direction chip in
another pane" is two steps for one gesture. **17 entries: 5 effects, and 12
transitions** — Dissolve, Dip to the bar colour / to black / to white, and Wipe
and Slide in each of four directions, each carrying the `params` it applies.
(The bare dip is named "Dip to the bar colour" rather than "Dip" on purpose: its
colour defaults to the letterbox, which is black in a default project, so
without the longer name it and "Dip to black" read as a duplicated row rather
than as a choice about which one follows the bars.) The directional presets are
DERIVED from `TRANSITION_DIRECTIONS`, in that list's order — the same order the
Properties pane draws its chips in, because two orderings of four arrows on one
screen is a thing to double-take at every time.

**⚠ SO THE PAYLOAD CARRIES AN ENTRY ID ("wipe:up"), NEVER A KIND**, and it
carries no parameters either: `fxEntry` reads those out of the library at DROP
time, so a tab open since before a preset was last edited still drops the
current one. A kind as the key would silently collapse the four wipes into
whichever was found first.

**⚠ `fx_library.js` FILES KINDS, IT DOES NOT DEFINE THEM.** `EFFECT_PARAMS`
(scene.js) and `TRANSITIONS` (transitions.js) remain the truth, both twinned in
Python. The folder table names ids and every entry is looked UP: a folder naming
a kind this build lacks is dropped on the way through, and — the important half
— **a kind in either table that nobody filed lands in an "Uncategorised" folder**
rather than being unreachable from the UI. Same reasoning as the family fill on
the transition badge: an entry nobody filed should be visible and ugly, never
invisible. Verified by removing `chroma` from its section and watching it appear
under Uncategorised, still draggable, still counted.

**⚠ ONE MARKER TYPE FOR BOTH PAYLOADS, and that is why `dropAsset` does the
refusing.** `getData` is blank during `dragover` in every browser, so a lane can
only read the TYPE LIST — it can tell an fx is coming but not whether it is an
effect or a transition. `laneTakes` therefore says yes to both on the rows that
carry PICTURES (`frames` and image layers — the `LOOK_KINDS` rule the scene model
already follows), and the drop is where a transition on an overlay row is turned
away. A caption or a shape row takes neither: they are drawn above the finished
composite and have no pixels to grade.

**⚠ AN EFFECT LANDS ON A CLIP, SO THE CLIP LIGHTS UP — not the drop line.** The
existing feedback is a line at the snapped moment, which is right for an asset
and a straight lie for an effect: a line between two pictures does not say which
one is about to be graded, and that is the one thing the drag has to answer
before you let go. `dropAt.fx` carries the distinction over from `dragKind`, and
`dropOnto` picks the bar.

Landing rules, all of them in `AnimaticEditor.jsx` so the timeline stays dumb:
- An effect onto a picture row grades the picture playing at that moment —
  **but only if it is on THAT row**. Images and Video are one sequence drawn
  twice, filtered by origin, so at a moment where a still plays the Video row
  shows a gap, and a drop into the gap must not quietly grade the still above it.
- An effect onto an image layer grades the overlay under the pointer; dropping
  on empty row is refused rather than guessing at the nearest one.
- A transition goes on the nearest CUT, and **replaces** what is already there
  rather than stacking — one per cut is what keeps `transitionAt` single-valued.
  It carries the preset's kind AND parameters, and the parameters are replaced
  **wholesale rather than merged**: a preset IS its parameters, so dropping
  "Wipe up" on a cut that wipes right must leave nothing of the old one behind.
  The record itself moved into `newTransition()` — one literal, two callers (the
  ＋ on a cut and a dropped preset), so a field added to a transition cannot
  arrive on the ones made one way and not the other. The notice names the preset
  ("Wipe up added on that cut"): being told "transition added" leaves you
  checking whether you got the right one.
- **Clicking** an entry does the same thing at the playhead. Not a nicety — it
  is the only path through the library without a mouse.

**⚠ A GRADED CLIP LOOKED EXACTLY LIKE AN UNGRADED ONE.** An effect chain had no
representation on the timeline at all, so the timeline now draws a small **ƒx
badge** on any clip carrying effects, and clicking it selects that clip AND
opens the Effects section (`openGroup("look:effects")`). Selecting alone is half
an answer — with the section folded shut the pane looks unchanged and the thing
you just dropped is invisible, which is the exact failure `openGroup` was written
for. It is a COUNT, not a list: at eight pixels of bar there is no room for more,
and "which ones at what values" is what the pane is for.

Files: **new** `client/src/animatic/fx_library.js` (the catalogue) and
`client/src/components/EffectsLibrary.jsx` (the tree); `AnimaticEditor.jsx` (the
third tab, `addEffectToClip` / `addTransitionAtCut` / `addFxFromLibrary` /
`manageEffects`, and the two fx branches in `dropAsset`); `Timeline.jsx`
(`fx` in `DRAG_KINDS`, `laneTakes`, `dropOnto`, `fxBadge`, `onManageEffects`);
`animatic-tools.css` (the tree) and `animatic-lanes.css` (the badge and the
`drop-onto` ring).

**Verified:** `npm run build`, and the library module driven under node — the
full folder/preset dump (17 entries, every id and its params), `fxEntry`
returning null for an id that isn't there, the payload shapes, and the
Uncategorised catch-all with two kinds unfiled at once. **NOT DRIVEN IN A
BROWSER: nothing here has been dragged by hand** — see Next Steps, where it is
the top item. Nothing on the Python side was touched this round.

### 2026-08-19 — A TRANSITION TAKES PARAMETERS: WHICH WAY IT TRAVELS, WHICH COLOUR IT DIPS THROUGH

Step 1 of the transitions plan. The four kinds are unchanged; what is new is that
each one can be told HOW to behave, through a `params` dict resolved exactly the
way an effect's is. Ten combinations where there were four: dissolve, dip through
any colour, and wipe and slide in each of four directions.

**⚠ A REVEAL IS A REGION, NOT A COMPOSITING STAGE — and that is why this is
small.** The first sketch of this called for a gl-transitions-style
`mix(getFromColor, getToColor)` pass, a new GL program and a pair of extra
framebuffers. That would have thrown away the rule `animatic.py` documents at
`_transition_canvas`: **the incoming picture is composited OVER the outgoing one,
not blended with it**, so a caption keyed out of the arriving shot reveals the
shot it is arriving over rather than black. Blending two finished textures loses
clip B's blend mode, chroma key and mask — they would have nothing left to blend
against. A wipe at 50% is *"show the incoming picture where uv.x < 0.5"*, which
is the operation both sides already have; so the direction is a REVEAL REGION cut
out of the arriving picture's quad, and a slide is still pure geometry. No new
program, no framebuffers, no change to `MAX_EFFECTS` or the `uFxArgs` budget.

**The parameters, and why the defaults are what they are.** `TRANSITION_PARAMS`
(transitions.js, twinned in `animatic_render.py`) is the whole table: dip has
`color`, wipe and slide have `direction`, dissolve has none. `direction` means
the direction of TRAVEL for both — the way a wipe's edge sweeps, the way a
slide's two pictures move — but the defaults differ (`right` for a wipe, `left`
for a slide) because those are the behaviours that already shipped, and
reproducing them exactly is what let this land without touching a single existing
animatic. `color` defaults to `""` meaning THE BAR COLOUR, the same
empty-string-is-inherit rule `lut.name` follows.

**⚠ A DIP IS A VEIL NOW, NOT A FADE.** It used to scale the picture's own
opacity so it sank into `settings.background`. Over real numbers that is the same
arithmetic as laying the backdrop colour over the top — but only the veil also
covers the LETTERBOX BARS, and without that a dip to red would snap the bars to
red at both edges of the window, which are the two moments a transition has to be
invisible at. A dip that names no colour is bit-for-bit the dip that always
shipped, save for one 8-bit rounding on keyed edges.

Files, and the two that bite:
- `client/src/animatic/transitions.js` + `animatic_render.py` — `TRANSITION_PARAMS`,
  `TRANSITION_DIRECTIONS`, `TRANSITION_PARAM_CHOICES`, `transitionKind()` /
  `transitionParams()` and their twins. Params land on the window object.
- `scene.js` / `animatic_render.py` — `sceneAt`/`scene_at` flatten
  `transition_params` onto the scene, ALWAYS a dict (empty off a transition), or
  the resolved scene is a different shape on the two sides and the parity test
  compares two things it thinks are equal for the wrong reason.
- **⚠ `sceneSignature` / `scene_signature`, in both languages, byte-identically.**
  Two wipes at the same `mix` differ ONLY in the parameter, so without it the
  exporter renders one still per `mix` and reuses it across directions — the
  transition would come back unchanged from a re-export. Only NON-DEFAULT
  parameters go in, so an untouched transition signs what it always signed.
- `ProgramCanvas.jsx` — `clipRight` generalised to `revealRegion()` (a rect with
  the non-sweeping axis left at ±Infinity, which is what makes "right"
  arithmetically identical to the code it replaced), `shiftX` gained `shiftY` via
  `slideOffsets()`, and the dip draws a colour quad over the picture.
- `animatic.py` — `_wipe_box()` and `_slide_offsets()` are the twins of those
  two; `_shifted` gained `dy`; `_faded` became `_veiled`.
- **⚠ TWO PLACES CARRY A TRANSITION TO THE RENDERER, and both had to change:**
  `plan_animated_segments` puts `transition_params` on the segment, and
  `build_animatic` puts it in the worker's task args. Miss either and the
  monitor shows the direction while the MP4 draws the default — proved by
  sabotaging the second one and watching five checks fail.
- `server/schemas.py` — `params: dict[str, float | str]` on `AnimaticTransition`,
  free like `AnimaticEffect.params`. No migration; `kind` was already
  unconstrained and `_transitions_of` already drops what it can't read.
- `TransitionProperties.jsx` — direction chips and the dip swatch, shown only for
  kinds that declare them (**the pane reads `TRANSITION_PARAMS`, it does not keep
  its own list**). `AnimaticEditor.jsx` passes the bar colour in so the swatch
  tells the truth, and `addTransition` writes `params: {}`.
- `animatic-lanes.css` — `.tl-transition` carries the family fill now, so a kind
  added before its own rule draws as a transition instead of an invisible
  outline. Badges are NOT varied by direction: sixteen patterns at eight pixels
  wide is not a legend anyone can read.

**Verified:** `tests/render_parity.py` (the fixture now carries a non-default
direction, a parameter belonging to a different kind, and a parameter on a kind
that offers none — so both languages are compared on all three) and
`tests/transition_check.py`, which grew a section that encodes real MP4s and
decodes them back: a wipe told to travel left uncovers the RIGHT half, one told
to travel down uncovers the TOP, a slide told to travel right enters from the
LEFT, one told to travel up enters from the BOTTOM, an unknown direction folds to
the default, and a dip through white turns a BLACK shot bright in the middle.
Every one of those is the opposite of the default assertion above it, so a build
that dropped `params` anywhere passes the old checks and fails these. Also run:
`effects_check`, `animatic_motion_check`, `video_clip_check`,
`keyframe_ops_check`, and `npm run build`. **Not opened in the real editor by
hand** — see Next Steps. `tests/effects_parity_check.py` still can't run here
(no headless-gl), unchanged from before.

### 2026-08-19 — THE STACK IS WRITTEN DOWN AT THE TOP OF THIS FILE, AND README.md TELLS A HUMAN HOW TO RUN IT

Docs only; no code touched. The user asked what the tech stack is, so the answer
is now the FIRST section of this file instead of something every agent re-derives
by grepping `package.json` and `requirements.txt`.

- **New `## 🧱 Tech stack — READ BEFORE YOU ADD A DEPENDENCY`, inserted directly
  after the intro and before the Protocol** (line 12). Everything in it was read
  off the repo, not recalled: `client/package.json`, `requirements.txt`,
  `requirements-dev.txt`, `client/vite.config.js`, `server/config.py`,
  `server/worker.py`, `server/jobs.py`, `.env.example`, and the model ids grepped
  out of the Python modules.
- **What it says, beyond the obvious list:** ⚠ the frontend has exactly TWO runtime
  dependencies (`react`, `react-dom`) and every editor widget is hand-written; ⚠
  `frontend/` at the root is EMPTY and the app is `client/`; ⚠ Mongo is the default
  job store, not Firestore; ⚠ there is no `ffprobe`, no pytest, no CI; ⚠ Veo is the
  only per-second cost; and the **twins table** — the six modules that exist once in
  Python and once in JavaScript, each with the parity test that fails when they
  drift. It closes with a **"Deliberately absent"** list (TypeScript, react-router,
  Redux, Tailwind, three.js, Celery, SQL, Docker, pytest, non-Google providers)
  so the next agent asks before reaching for one.
- **Also corrected one stale row in the file map**: `server/jobs.py` still read
  "Firestore (default)"; `server/config.py:35` defaults `API_JOB_STORE` to `mongo`.
  The new section and the file map now agree.

Nothing was run or tested — there is nothing here to run.

**`README.md` was one line — the repo title — and is now the local-run guide.** It is
written for a HUMAN cloning this repo, and it says so at the top that agents read
`AGENTS.md` instead, so the two don't drift into two sources of truth.

- **Six numbered steps** (venv → `pip install -r requirements.txt` → `.env` → uvicorn
  → `npm run dev` → create an account), PowerShell first because that is the dev
  platform, with the macOS/Linux difference noted once rather than duplicated.
- ⚠ **The Gemini API key path is presented as the default**, with Vertex + ADC in a
  collapsed `<details>` — one key gets someone running; ADC plus a billing-enabled
  project does not.
- ⚠ **A "Run without MongoDB (fully local)" section**: `API_USER_STORE=local` +
  `API_JOB_STORE=memory`. Both exist in `server/config.py` and neither was written
  down anywhere a newcomer would look. It also warns that the Mongo→file fallback is
  a LOUD error, not a feature, and that an empty `API_LOCAL_JOBS_PATH` is what makes
  `--reload` lose boards.
- ⚠ **A "What costs money" section** — Veo per-second pricing, the spend guards, and
  "there is no Google Flow API" — plus the cheap smoke-test commands, so the first
  thing someone runs isn't a 20-clip render.
- **Troubleshooting is the failures this project actually has**: wrong-backend model
  names 404ing (Vertex `-001` vs Gemini `-preview`), Veo on `global`, the insecure
  dev JWT warning, `ffmpeg: false` on `/health`, `pkill` not killing a Windows
  python process, cp1252 killing a script that prints arrows, and an empty gallery
  meaning `local_only` was ticked.
- Everything was verified against the repo: the six sidebar workflows and their
  current labels, `run_character.py`'s actual flags, `client/.env.example`,
  `seed_admin.py`'s 8-char rule, the `/health` body, and the store defaults in
  `server/config.py`.

⚠ **`storage.py` hardcodes the GCS bucket** (`BUCKET_NAME`, no env var), so the README
tells local users to tick **"Local only"** rather than pretending the bucket is
configurable. If GCS ever needs to be per-install, that is the line to change.

### 2026-08-19 — THE MONITOR WENT BLACK WHEN YOU PICKED A COLOUR LOOK

One user-reported fault — "choose Colour look (LUT) → Identity and the screen
turns black" — and the interesting part is where it was NOT. Identity is the LUT
that changes nothing, so "the picture went black" could not be the table, the
shader or the interpolation, and `tests/effects_check.py` and
`tests/effects_parity_check.py` both agreed: every effect computes the right
numbers. **The grading was never wrong. The monitor was being destroyed.**

- **1. ⚠ `Compositor.dispose()` DELETED THE WRONG THING, AND THE THROW ESCAPED
  INTO REACT.** `this.luts` maps a name to `{ texture, size }`, not to a texture;
  `dispose()` looped `for (const texture of this.luts.values())` and handed the
  whole entry to `gl.deleteTexture`, which raises
  `parameter 1 is not of type 'WebGLTexture'`. It ran inside the context effect's
  **cleanup**, so React tore `<ProgramCanvas>` out of the tree and the editor was
  left showing `an-screen`'s background — a black rectangle with no error visible
  on screen. **It was unreachable until a LUT existed**, because until then the
  map is empty and the bad line never executes: that is the whole reason the
  symptom named the colour look. `client/src/animatic/gl/compositor.js`.
  `_blank` is deleted there now too, which was a straightforward leak.
- **2. ⚠ THE WEBGL CONTEXT WAS REBUILT ON EVERY RENDER, and that is what kept
  firing (1).** The context effect had `}, [onUnavailable])` and
  `AnimaticEditor` passed `onUnavailable={() => setGlFailed(true)}` — a new
  function identity every render — so every playhead tick and every keystroke in
  a property field destroyed the context and built another: two programs
  recompiled, `this.textures` thrown away, every picture re-uploaded. The
  callback now lives in a ref (`unavailableRef`) with `}, [])` deps, so a caller
  **cannot** thrash the context by passing an unstable prop; the call site was
  given a `useCallback` as well. `ProgramCanvas.jsx`, `AnimaticEditor.jsx`.
- **3. A third LUT in one chain is now dropped LOUDLY, as `layer.js` always said
  it was.** `MAX_LUTS` is 2 (samplers cannot be indexed by a loop variable) but
  the Effects pane allows six effects, so a chain with three LUTs previews
  differently from what it exports — in silence, which reads as "this effect
  does nothing". `warnOnce` in `compositor.js` says it once per name rather than
  once per frame; the draw path runs on every tick and an unguarded `console.warn`
  there is a thousand identical lines a second.
- **4. `tests/monitor_effects_check.py` — NEW, and it is the only test that
  MOUNTS THE MONITOR.** Vite + Chromium (SwiftShader, so it needs no GPU), no
  backend: it renders the real `<ProgramCanvas>` over a real `sceneAt`, answers
  the LUT endpoints off `luts/` with Playwright's router, and walks 22 chains —
  every effect kind at a value that MOVES the picture, both LUT orderings, a
  missing LUT, a freshly-added effect whose `params` are `{}`, two LUTs at once
  — comparing each against `apply_effects` on the same flat colour. **The
  assertions that matter are about SURVIVAL**: is the canvas still in the
  document, did anything reach `window.onerror`, and **is the dispose count
  zero**. Reverting either fix makes it fail 16 checks, which is how both were
  confirmed rather than assumed.

**Where the numbers landed** (monitor vs `apply_effects`, on `#4a86c8`, all within
1/255 of each other): identity `(74,134,200)` unchanged, noir `(123,123,123)`,
brightness 1.4 `(104,188,255)`, saturation 0 `(124,124,124)`, and — the case that
proves the chain is not silently sorted — LUT-then-brightness `(172,172,172)`
against brightness-then-LUT `(181,181,181)`.

**⚠ THE LESSON, because it will happen again in a different place:** a WebGL
monitor that goes black is a CRASH before it is a rendering bug, and a crash in an
effect's cleanup leaves nothing on screen to say so. Look at `window.onerror` and
at how many times the context is built before touching a shader. `npm run build`
is clean; `tests/effects_check.py` still passes; the new browser check passes all
46 assertions. **Not opened in the real editor by hand** — see Next Steps.

### 2026-08-19 — TRACK HEADS LINE UP, THE ADD BUTTONS MOVED TOGETHER, AND ASSETS DRAG ONTO A LANE

Five user-reported UI faults, mostly off one screenshot of the timeline — a
wayfinding pass on the editor's four panes (which took two rounds — see 3), a
trim of the monitor's transport bar, the add-buttons gathered into one place —
and then drag-and-drop from the Media pane onto the timeline's rows.

- **1. ⚠ EVERY LAYER ROW NOW HAS THE SAME THREE CONTROLS IN THE SAME THREE
  PLACES — hide · add · remove.** They used to be rendered only when they had
  something to do (no ✕ on an empty default row, no eye on audio's speaker row),
  and each one carried its own `margin-left: auto`, so whichever happened to be
  first took the right-hand edge: a row with no eye put its ＋ in the eye's
  column and the icons zig-zagged down the gutter. The three now live in one
  `.tl-layer-acts` cluster — `display: grid`, three columns of `--tl-act-w`
  (1.15rem, 1rem under 720px) — and `Timeline.jsx` **always renders all three**.
- **⚠ A CONTROL WITH NOTHING TO DO IS DRAWN AND DISABLED, NOT LEFT OUT.** This is
  the whole reason the columns hold: an omitted button lets the ones after it
  slide left. `.tl-layer-btn:disabled` is `opacity: 0.25`, `cursor: default` —
  the ghost ✕ on an empty row also says "this row can be emptied, once there is
  something on it", which a gap said nothing about. What each control does is
  unchanged (`onRemoveLayer` / `onRemoveTrack` / `onClearLane`, `hidden_lanes`
  for the eye); only when it is *offered* changed. The ＋ and audio's speaker
  now `stopPropagation` like the other two, so a click on them no longer also
  selects the row.
- **2. `.tl-add-layer` TAKES ITS BOX FROM THE ROWS IT MAKES.** As a small dashed
  strip it read as a caption over the column, not the head of it — different
  height, type and colour from the layer rows below. Now the same radius, border
  width and 0.74rem type as `.tl-gutter-row`, `height: var(--tl-track-h)`
  (clamped 1.6–2.1rem), and highlighted with the timeline's own gold —
  `--tl-clip-bg` / `--tl-clip-bg-alt`, the tints the clips already use, so it is
  lit by an existing token and not a new colour. Dashed edge stays: it is what
  still says "nothing here yet".
- **3. ALL FOUR PANE HEADS SHARE ONE SOFT BLUE.** Four identical grey heads made
  the editor read as one slab. Three `--pane-ink/tint/edge` tokens in
  `theme.css` (both themes; light mode goes fainter in tint and several shades
  deeper in ink), read directly by `.an-pane`, `.an-pane-head` and
  `.an-pane-title`. It lands in three quiet places only: the head fill, the
  hairline under it and the pane border — gold is still the app's one accent.
- **⚠ A PASTEL PER PANE WAS BUILT FIRST AND REJECTED ON SIGHT** (Media blue,
  Program lilac, Properties mint, Timeline apricot). Four hues plus gold is five
  accents on one screen and the editor read as unrelated tools; the user asked
  for the blue on all four. **Don't re-derive it — lifting the heads off the
  grey needs a colour, not four.** The per-pane `.an-pane-{name}` token mapping
  is gone with it; there is nothing left for a pane to override.
- **⚠ THE DOT IS ON `.an-pane-head`, NOT `.an-pane-title`.** The Media pane's
  head opens with its Media / Shapes tabs and has no title element at all, so a
  marker hung on the title would have appeared on three panes out of four. The
  head tint is a `linear-gradient` layered OVER `--panel-2` rather than
  replacing it: the pastel is an alpha, and a head that lost its own surface
  would show the pane body through it.
- **4. THE MONITOR'S TRANSPORT IS SMALLER, AND "Frame 7 of 34" IS GONE.** The bar
  under the picture was taking height the picture wanted. Buttons 2.2 → 1.75rem,
  play 2.8 → 2.1rem, clock 0.95 → 0.82rem, gap 0.5 → 0.35rem; the frame readout
  and its `.an-shotnum` rule are deleted (which frame is up is already told by
  the playhead and the selected bar on the timeline). `currentIndex` stays —
  it is what `stepFrame` walks.
- **⚠ SIZED AS `.an-transport .an-tbtn`, NOT ON `.an-tbtn` ITSELF.** That class
  is also the timeline header's zoom pair, which is `.an-tbtn.small` at 1.8rem —
  shrinking the base rule would have dragged those down too AND left the
  "small" modifier LARGER than the thing it modifies.
- **5. EVERY "MAKE SOMETHING" BUTTON IS IN ONE PLACE NOW.** Text, Colour card and
  Voiceover moved out of the far right of the timeline pane head and into the
  timeline's own head row, beside ＋ Add layer — they were a bar's width away
  from the only other control that adds anything. ⚠ **＋ Add layer HAS NOT
  MOVED**, which was the explicit requirement: `.tl-head` keeps the gutter's
  width and stays a sibling of `.tl-cols`; a new `.tl-headbar` flex row wraps it
  and the buttons sit in the space beside it, over the tracks they add to. The
  row's gap is the SAME 0.5rem as `.tl-cols`, so the buttons start exactly where
  the tracks do.
- **⚠ THEY ARE STILL THE EDITOR'S BUTTONS** — passed in as `<Timeline addTools>`,
  a node, not reimplemented in `Timeline.jsx`. What they make, and that
  Voiceover SPENDS QUOTA, is the editor's business; the timeline only gives them
  a place to stand. Undo / redo / snapping / 🥁 / Fit to audio / Set all / zoom
  all stay in the pane head: they act on what is already there.
- **6. ASSETS DRAG FROM THE MEDIA PANE ONTO A LANE.** Drag a frame card or an
  audio row out of Media, or a file off the desktop, and drop it on a timeline
  row: it lands AT THE TIME UNDER THE POINTER, snapped like every other drag
  here. `Timeline` decides WHERE (`dropProps` / `laneTakes` / `onDropAsset`);
  the editor decides what that MEANS (`dropAsset`), because only it knows what
  an asset is.
- **⚠ THE KIND IS READ FROM `dataTransfer.types`, NOT `getData`.** `getData` is
  blank during `dragover` in every browser — by design — so a lane could not
  know whether to accept until after the drop. The drag sources stamp an EMPTY
  MARKER TYPE beside the JSON payload (`application/x-anim-image` / `-video` /
  `-audio`) and `dragKind` reads the marker. Don't "simplify" this back to one
  type.
- **⚠ A TIME MEANS DIFFERENT THINGS ON DIFFERENT ROWS.** The picture rows are a
  sequence with NO GAPS, so a drop time becomes the nearest CUT and the clip is
  reordered to that place (`frameIndexAt` → `reorder`) — a picture left floating
  at 0:07 with a hole in front of it is not a state the picture track has. An
  audio row is free-floating, so the time is literally `start_ms`.
- **⚠ A CLIP IS NEVER CONVERTED BY BEING DROPPED.** Video belongs on the Video
  row and stills on Images because that is what they ARE (`frameOrigin`), so
  those drops are refused — mid-drag by the browser's own no-entry cursor (an
  unaccepting lane simply never calls `preventDefault`) plus a red inset ring on
  the row that said no. A loose audio track keeps its own row too: those rows are
  grouped by FILE, so "drop it on that other file's row" is a promise the
  timeline cannot keep. Layer rows accept and re-parent.
- **⚠ AN IMAGE LAYER TAKES A STILL TOO, AND IT IS A COPY.** It refused them at
  first and that read as a broken row (user-reported, with a screenshot of the
  red ring): two rows say "image" and they are different things — the picture
  track's stills, and a layer of pictures composited OVER the video. Dropping a
  frame on the layer now makes an overlay at that time (`overlayFromFrame`)
  and LEAVES the still in the sequence; moving it would empty a cut out of the
  video to make an overlay, which is not what the gesture means.
- **⚠ A BOARD PANEL HAS NO UPLOAD OF ITS OWN.** Its picture belongs to the
  storyboard (`src.storyboard_id`) while an overlay is only ever an `upload_id`
  on this animatic's media route — so `overlayFromFrame` uploads the blob the
  editor is ALREADY holding for the thumbnail. An uploaded still reuses its
  `src.upload_id` and sends nothing. The overlay arrives as long as the still is
  held, not an arbitrary 2s.
- `addAudioTrack(file, startMs)` gained its second argument for the file-drop
  case; every existing caller still gets 0. Undo needs nothing special — a drop
  is one document change, so one Ctrl+Z.
  `addOverlayFiles(files, layerId, startMs)` gained the same optional third
  argument as `addAudioTrack`.
- **SHAPES DRAG TOO** — a tile out of the picker onto a shape row lands there
  (`addShape(kind, layerId, startMs)`), and a shape already on the timeline can
  be dragged out of "In this animatic" to re-time it or move it to another shape
  row. A dropped shape takes the length of the SHOT UNDER THE DROP, not of the
  one at the playhead — the picker's own rule, aimed at where you dropped it.
- **⚠ `dropEffect` IS READ OFF THE DRAG (`allowedEffect`), NOT PICKED PER LANE.**
  A drop whose `dropEffect` is not in the source's `effectAllowed` is filtered
  out by the browser and never fires — silently. The picker's tiles are a copy
  (the gallery keeps its shape), a clip being re-timed is a move, a file drop
  arrives as "all". Hard-coding "move" broke the picker before this landed.
- **7. THE SHAPE PICKER'S STANDING PARAGRAPH IS AN ⓘ NOW.** "A shape lands on the
  frame at the playhead…" is true forever and read once, so as prose under the
  tiles it cost three lines of a narrow pane on every visit. It is `info` on
  that section's `PropGroup` — the same ⓘ, in the same right-hand column, as
  every row in Properties. `.an-shape-hint` is deleted from both stylesheets.

Files: `client/src/components/Timeline.jsx`,
`client/src/components/AnimaticEditor.jsx`, `client/src/components/FrameStrip.jsx`,
`client/src/styles/animatic-editor.css`, `client/src/styles/animatic-lanes.css`,
`client/src/styles/animatic-text.css`, `client/src/styles/animatic.css`,
`client/src/styles/theme.css`. **`npm run build` clean; not driven in a
browser.**

### 2026-08-19 — STICKY HEADINGS IN THE MEDIA PANE, AND ⓘ MOVED ONTO THE ROW

Three follow-ups to the entry below, all reported off the same screenshot.

- **1. A SECTION HEADING STAYS PUT WHILE ITS OWN FRAMES SCROLL UNDER IT.**
  Reported as "Storyboard Frames goes under Add assets" — with thirty-odd cards
  in a section, the heading naming them is off the top for the entire time you
  are looking at them, so the pane stops saying what you are scrolling. Every
  `.an-grp-head` in `.an-media-body` is now `position: sticky` at
  `top: var(--an-drop-h)`, pushed out by the next section. Covers Storyboard
  Frames, Video, Images, Audio and the Shapes tab's sections — they are all the
  same `PropGroup`.
- **⚠ THIS NEEDED `overflow: hidden` OFF `.an-grp`, AND ONLY IN THIS PANE.** An
  `overflow: hidden` ancestor is a scrollport in its own right, and a sticky child
  of one is pinned to a box that never scrolls — i.e. it does nothing at all. The
  clipping was there for the section's rounded corners, so **`.an-grp-head` takes
  those corners over** (`8px 8px 0 0`, and all four when the section is shut).
  ⚠ The Properties pane keeps its `overflow: hidden`: nothing there is sticky, and
  the rule is scoped `.an-media-body .an-grp`.
- **2. ⚠ THE ＋ CARD IS A FIXED HEIGHT NOW — `--an-drop-h: 7rem` on
  `.an-media-body`.** Not styling: the card's sticky `top` is 0 and every
  heading's sticky `top` is the card's HEIGHT, so if the two disagree by a pixel
  a band of scrolling frames shows through between them. One custom property is
  the only way to keep them in step, and `:not(:has(> .an-asset-drop))` zeroes it
  for the Shapes tab, which has no card. `overflow: hidden` guards the fixed
  height when the note inside wraps to two lines.
- **⚠ THE SLIVER ABOVE THE CARD WAS `.an-pane-body`'S OWN `padding: 0.6rem`.**
  Reported twice — "the panel doesn't cover the media panel up, so you see my
  storyboard image a little". That band is the only thing between the pane head
  and a card pinned at `top: 0`, and frames scrolled straight up through it. The
  pane that has the card now takes `padding-top: 0`
  (`.an-media-body:has(> .an-asset-drop)`), which makes the card genuinely flush
  with the top AND retires the question of whether a sticky item pins to the
  padding box or the content box — with no padding they are the same edge. The
  card's own 0.9rem is the breathing room.
- **⚠ THE COVER OVER THAT STRIP IS A BOX-SHADOW, NOT A `::before`** — and that is
  why the first fix did nothing. A pseudo-element at `bottom: 100%` is inside the
  card, so the `overflow: hidden` guarding its fixed height clipped the cover
  away entirely. An outer box-shadow (`0 -1.2rem 0 0.8rem var(--panel)`) is
  painted outside the border box, is untouched by the element's own overflow, and
  is drawn behind the background so the card still covers it.
- **3. ⓘ IS A PROP ON THE ROW, NOT A BLOCK BETWEEN ROWS.** Yesterday's version
  gave every note its own line, which is the fault the prose had, in miniature:
  each one pushed the next property down the pane, and the column of ↺'s that
  tells you what you have changed on this clip stopped being a column.
  **`info` is now a prop on `PropRow`, `PropSlider` and `PropGroup`**, rendered in
  the row's right-hand cluster — **⏱ , ⓘ , ↺, in that order**, on the same edge
  every ↺ sits on. `.an-row-ctl > .an-note-i` takes `margin-left: auto` and the
  ↺ after it drops to 0, which is the same trick `.an-kf + .an-reset` already
  played. The prose opens in flow under the row (`grid-column: 1 / -1`, like
  `.an-row-hint`).
- **⚠ THE POINTER MUST BE ON THE ICON, NOT THE ROW** —
  `.an-row:has(> .an-row-ctl > .an-note-i:hover)`. Hovering the whole row would
  open a paragraph under it and shove the pane down while you were reaching for a
  slider. A click pins it (`.note-on`), which is the touch-screen answer and how
  you read a long note without holding the pointer still.
- **⚠ `PropNote` IS WARNINGS ONLY NOW.** Twelve explanation notes moved to `info`;
  the four `tone="warn"` ones stayed exactly where they were, in plain sight. Two
  of the twelve had no row to sit on and went on the SECTION instead (audio Tone,
  which explains all three bands at once; Selection's "What's selected", whose
  rows are a generated tally) — a group's ⓘ sits in its header and its note opens
  OUTSIDE `.an-grp-body`, so a shut section can still be asked what it is for.
  `PropNote` still renders plain prose for any call that is left, so nothing
  breaks silently.
- Files: `client/src/styles/{animatic-editor,properties}.css`,
  `components/properties/PropGroup.jsx` (new `InfoDot`, `info` on `PropRow` /
  `PropSlider` / `PropGroup`), and `Audio` / `Selection` / `Text` / `Transition` /
  `VideoClip` / `VideoProperties`.
- **Verified:** `npm run build` clean; no server code changed. ⚠ **Not driven in a
  browser** — the sticky heading offsets and the `:has()` hover in particular are
  reasoned about, not seen.

### 2026-08-18 — THE CHROME GAVE ITS HEIGHT BACK TO THE PICTURE

Six layout faults reported together off one screenshot of the animatic editor.
Every one of them is furniture taking room the monitor wanted.

- **1. A BACK BUTTON IS AN ARROW, NOT A SENTENCE.** "← Your Animatics" was the
  widest slab in the editor's top bar and the one action nobody opens the editor
  to press; the same button spelled itself out in five other workflows. One new
  class — **`.btn.back-btn`** in `base.css`, a square of the row's own height with
  `padding: 0` — and the destination moved into `title` + `aria-label`, which is
  where `.fv-top`'s back arrow (Final Video) has kept it all along; that button
  was the model. Changed in `AnimaticEditor` (top bar + error card),
  `StoryboardBoard`, `ScriptToStoryboard` (×2), `PlanAndScript`,
  `StoryboardAssets`, `StoryboardCast`, `FinalVideoWorkspace` (error card).
  ⚠ `backLabel` is now **prose only** — no arrow in it — because it is read as a
  tooltip; both callers (`CreateAnimaticImage`, `ScriptToStoryboard`) were
  updated. ⚠ Rows that state their own button padding must re-state `padding: 0`:
  `.an-topbar .back-btn` is now beside `.an-topbar .an-del-btn`, which does the
  identical thing for the bin. **Deliberately NOT changed:** the wizard-footer
  back in `PreflightModal`, `Login`'s "← Back to home", `FinalVideoArtStep`'s
  "← Other runs" and the Properties pane's "← Video" tab — none of those are
  workflow navigation, and an unlabelled arrow in a two-button modal footer is a
  riddle.
- **2. THE PROJECT TITLE IS A FIELD, SO IT IS DRAWN AS ONE.** `.an-title` was
  `background: transparent; border-color: transparent` until hover — reported as
  "I see look merge in bg". It now carries the panel fill and border every other
  input in the app has; hover lifts it to `--border-gold` and focus to
  `--primary`, so those states still mean something.
- **3. THE STATUS STRIP IS AT THE FOOT OF THE EDITOR.** It is a running
  commentary (a notice, an export percentage), and under the top bar it pushed
  the monitor and all three panes down the moment it had anything to say.
  ⚠ **IT IS LAST IN THE DOM NOW** — that is what puts it at the bottom of the Long
  workspace, which is a flex column; the Reel workspace places by NAME, so its
  `grid-template-areas` moved `stat` from the second row to the last and the rows
  became `auto minmax(0,1fr) auto auto auto`. Also shorter (0.22rem padding,
  0.72rem type) and **`flex-wrap: nowrap`**, with the message eliding instead of
  the strip growing a second line — `.an-status-export` took `flex: none` so a
  long notice can't squeeze the percentage away. Under 1180px, where the editor
  is a scrolling page again, it is `position: sticky; bottom: 0` — otherwise an
  export report sits minutes of scrolling from wherever you are.
- **4. ＋ ADD ASSETS DOES NOT SCROLL AWAY.** It is the Media pane's ONLY add
  control and its drop target, and one flick of the wheel on a 31-frame board put
  it off the top — so the answer to "where do I put this file?" was "scroll back
  up first". `position: sticky; top: 0`, which is the same reason it was kept
  OUTSIDE the collapsible sections. ⚠ The `::before` on it is not decoration:
  `.an-pane-body` has 0.6rem of padding and without something painted over that
  band the frame cards are seen sliding through the gap above the pinned card.
- **5. ⚠ THE PROGRAM HEAD'S MENU WAS FULL WIDTH BECAUSE `theme.css` SETS
  `input, select, textarea { width: 100% }`.** That rule is for the app's forms;
  `.an-ar-select` never opted out, so a menu whose longest option is "16:9 — Wide"
  ate the whole head and pushed "1920×1080 · 24 fps" onto a THIRD line — two rows
  of height off the monitor on every screen, which is what the report was really
  about. `width: auto; flex: 0 0 auto` puts title, shape and size on one line.
  The read-out elides rather than wrapping the head open.
- **6. `PropNote` IS AN ⓘ.** Teaching prose ("100% is the file as recorded…") is
  true forever and useful exactly once, and printed under every section it
  out-shouted the controls — five sections meant five grey paragraphs, and the
  properties people came to change were the shortest thing on screen. Hover the
  ⓘ to open it, click to PIN it (touch screens, and long notes you want to read
  without holding the pointer still). It opens IN FLOW, not floating, because
  `.an-grp` is `overflow: hidden` for its corners and the pane scrolls — a popover
  would be clipped by one or the other. ⚠ **ONLY THE "" TONE.** A `tone="warn"`
  note ("this clip runs past the end of the video") is conditional, is about the
  state you are in right now, and stays in plain sight — a notice you have to go
  looking for is a notice nobody reads.
- Files: `client/src/styles/{base,animatic,animatic-editor,properties}.css`,
  `client/src/components/AnimaticEditor.jsx`,
  `client/src/components/properties/PropGroup.jsx`, and the seven back-button
  components listed in (1).
- **Verified:** `npm run build` clean. No server code changed, so no Python test
  was run. ⚠ **Not driven in a browser** — in particular the sticky ＋ card, the
  strip's new home in both workspaces and the ⓘ hover have been reasoned about,
  not seen.

### 2026-08-18 — THE PICTURE TRACK GROWS A VIDEO ROW, AND EVERY ROW GETS AN EYE AND AN ✕

Six things, reported together after a video was dropped into a 31-panel board.

- **1. A JUST-UPLOADED VIDEO SAT ON ITS SPINNER — "media panel looking uploading
  type" for minutes.** The clip had uploaded; `newVideoClip` was the one clip
  factory that set no `url`, and the thumbnail effect only fetches frames that
  HAVE one, so the card waited for a picture that was never coming until a reload
  filled it in. ⚠ The raw `/media/{upload}` route can't answer this — it hands
  back an MP4 an `<img>` can only fail to draw — so the route learned
  **`?poster=1`**, backed by `_video_poster(job_id, upload_id)`: a still BY UPLOAD
  ID, because between the drop and the debounced save there is no clip to name.
  `_video_thumb` is now a two-line wrapper over it. `w=` proxies the still.
- **2. VIDEO GETS ITS OWN TIMELINE ROW.** The picture track is drawn as two rows —
  "Images" and "Video" — via `lane.only` + `laneShows` in Timeline.jsx. ⚠ **IT IS
  STILL ONE SEQUENCE**: the clock runs over every clip and the rows only filter
  what they draw, so a gap on one row is exactly where the other is playing.
  Advance the clock, THEN skip. The cuts divide between the rows by which one owns
  the outgoing picture, so no cut is drawn twice.
- **3. ⚠ THE SPLIT IS BY ORIGIN, NEVER BY KIND — `frameOrigin` in
  `animatic/scene.js`, mirrored by `_frame_origin` on the server.** Animating a
  board shot with Veo makes it a video clip, so by kind every animated shot would
  jump rows and cut a thirty-panel sequence into islands. `attachVeoClip` now
  PRESERVES `src.storyboard_id`/`index` (it used to replace `src` outright), which
  is the only record that a clip is a board shot; every server path branches on
  `src.kind` first, so the kept ids are inert.
- **4. The Media pane lists the track in three sections** — **Storyboard Frames**,
  **Video**, **Images** (stills and colour cards) — same origin rule, empty ones
  not drawn. `FrameStrip` gained **`indexOf`**: every index leaving it goes through
  that, so a card's number is its place IN THE VIDEO and a reorder or a drop inside
  a section moves the clip within the SEQUENCE. Also: `assetInputRef` now accepts
  `video/*` — the file dialog was refusing the exact thing the drop target beside
  it accepted.
- **5. Lane order is now the compositing order, top first**: captions, text,
  shapes, overlay pictures, Images, Video, audio. Read bottom-up it is the order
  asked for (audio, video, image, shapes, text, captions) and the order the frame
  is built in.
- **6. AN ✕ ON EVERY ROW, AND AN EYE.** ✕ on a lane the user added still removes
  the row; on a DEFAULT row it **empties** it and keeps the row (`clearLane`) —
  they are structural, so that is the only honest meaning of "remove" — and it
  asks first, because it can be a whole board behind one click. The eye
  (`toggleLaneHidden`) is audio's speaker for rows you SEE.
- **⚠ THE EYE REACHES THE ENCODER, which is why `hidden_lanes` is a project
  SETTING and not a browser preference.** A switch that dimmed the preview and
  exported the row anyway would lie at the one moment it matters. It names a ROW,
  not its clips (`"text:"`, `"shape:<id>"`, `"image:<id>"`, `"frames:stills"`,
  `"frames:video"`) — an encoding both sides can rebuild from a clip's own fields:
  `laneToken` on the client, `_lane_hidden` on the server. Audio stays with
  `muted`; two switches for one idea is worse than either.
- **⚠ A HIDDEN PICTURE ROW IS BLANKED, NEVER DROPPED.** `frames` is laid end to
  end: dropping a clip moves every later cut, shortens the video and pulls the
  audio out of sync — from pressing an eye. It becomes a colour card of the
  letterbox colour, holding exactly its old time. Free-floating clips (text,
  shapes, overlays) ARE dropped, and dropped from the `end_ms` calculation too — a
  hidden caption row that still set the length would leave held picture with
  nothing on it.
- Files: `server/animatics.py`, `server/schemas.py` (`hidden_lanes`),
  `client/src/animatic/scene.js`, `components/{AnimaticEditor,Timeline,FrameStrip,Icon}.jsx`,
  `styles/animatic-editor.css`, `tests/hidden_lane_check.py` (new).
- **Verified:** `python tests/hidden_lane_check.py` — 38 assertions, all pass,
  including that an animated board shot stays on the stills row and that blanking
  leaves the sequence exactly as long. `render_parity`, `video_clip_check`,
  `transition_check`, `selection_check`, `keyframe_ops_check`,
  `animate_guard_check`, `aspect_refit_check`, `effects_check`,
  `audio_razor_check`, `captions_check`, `export_perf_check` all still pass;
  `npm run build` clean. ⚠ **Not driven in a browser.**

### 2026-08-18 — THE MEDIA PANE IS A STACK OF SECTIONS YOU CAN CLOSE

- **Reported:** "in media panel I want some function like properties panel — when
  I close 'Frames 31' it opens and closes like Clip, Source etc, so I easily
  close frames, audio, shapes, video which I add after."
- **It is the PROPERTIES PANE'S SECTION, not a new one.** `PropGroup`
  (`components/properties/PropGroup.jsx`) now wraps the Media pane's lists too:
  **Frames** (count = frames), **Audio** (count = tracks), and on the Shapes tab
  **Add a shape** and **In this animatic**. ⚠ Writing a second, media-only
  collapsible would have been the same control drawn twice — same twist, same
  count pill, one of them subtly different — in two panes that sit side by side.
  One component means Frames folds exactly the way Motion does, remembers what
  you closed for the session the same way, and unmounts its body when shut.
- **Why it was needed:** a 31-panel board pushed Audio and Shapes below the fold,
  so reaching a track meant scrolling past every frame card. A closed section
  still carries its count, so folding Frames away doesn't hide what is in it.
- **`FrameStrip` grew `heading={false}`** — the section header already carries
  "Frames" and the count, so the strip's own `fs-head` was those two things
  again one line below. ⚠ The hidden file input moved OUT of that head, because
  the head is now optional and the input is what the add-card opens.
- **The add-assets card stays outside the sections**, deliberately: it is the
  control that FILLS them, and a drop target you can fold away is one you cannot
  drop on. ⚠ **WHICH IS WHY EVERY ADD NOW OPENS THE SECTION IT LANDED IN** —
  `openGroup(id)`, exported from `PropGroup.jsx`. Reported immediately: "I upload
  a video file here but it doesn't show in the media panel." It had uploaded
  (31 → 32 frames); Frames was folded shut, so an add moved a count and changed
  nothing else on screen, which is indistinguishable from an add that failed.
  `addAssets` (images + video) and `addColorCard` open `media:frames` and switch
  to the Media tab; `addAudioTrack` opens `media:audio` — placed there, not in
  its callers, because the drop card, the Audio lane's ＋ and "Add layer" all go
  through it; `addShape` opens `media:shapes`. ⚠ **`openGroup` may only OPEN.**
  Closing a section for someone is the same surprise pointing the other way. It
  writes the memory whether or not the group is mounted, so a section on the tab
  you are not looking at is already open when you get there.
- ⚠ **`.an-media-body > * { flex: 0 0 auto }` IS WHAT SCROLLS THE PANE, and it
  was missed first time round.** A flex item shrinks by default, so the Frames
  section squeezed down to the height left over — and a section is
  `overflow: hidden` (it must be, for its corners), so the frames past the fold
  were **clipped, with no scroll bar at all**: 31 frames, eight visible. Pinned
  at content height they overflow instead and `.an-pane-body`'s `overflow: auto`
  is the scroll bar again. Any new child of this pane has to be `flex: 0 0 auto`.
- CSS: `.an-media-body` is the gapped column (`styles/animatic-editor.css`), and
  the lists drop the margins they carried as loose blocks —  the old
  `.an-media-audio` rule (margin-top + border-top) and `.an-media-sub` heading
  are gone with the markup they styled.
- **Verified:** `npm run build` clean. ⚠ **Not driven in a browser.**

### 2026-08-18 — THE REEL WORKSPACE GIVES THE MONITOR THE WHOLE HEIGHT, AND A WORKSPACE ICON IS A MAP

- **Reported:** "in reel/shorts workspace my program panel look small because
  aspect ratio 9:16… set the timeline below the media panel and keep the program
  panel long" (with a Premiere screenshot: one tall monitor down the left, the
  project pane and the timeline stacked beside it), plus "change the icon of the
  Long / Shorts workspace so the user understands what the icon says".
- **WHY THE MONITOR WAS SMALL, and why a bigger width was never the fix.** A 9:16
  picture is bounded by HEIGHT (`.an-screen` fits both axes of `.an-screen-fit`).
  While Program was a pane in the top row like the other two, the tallest it
  could ever be was "the window minus the timeline" — so dragging its column
  wider only added empty gutter either side of the same small picture. The pane
  has to reach the BOTTOM of the window, which means the timeline has to move.
- **The reel workspace is now a grid, not a column** (`an-ws-reel` block in
  `client/src/styles/animatic-editor.css`): Program is a full-height left column;
  Media and Properties sit in the row beside it; the timeline and its seam span
  only the right-hand side, under Media. ⚠ **No markup changed** — `.an-panes`
  becomes `display: contents` so its panes and seams join the editor's own grid,
  and named `grid-template-areas` place them. **Areas, not rows-by-position**,
  because the status strip only exists when it has something to say — the exact
  bug that made `.an-nle` a flex column in the first place. Every rule is
  `:not(.an-has-max)`, so maximizing (~) drops back to that flex column.
- **The seams still size what they always sized.** `.an-split-left` is the
  Program/Media seam and now runs the full height; `.an-split-right` is
  Properties; the horizontal one is the timeline height and now spans only the
  right side, which is the only part of the window it divides.
- **The reel defaults follow** (`client/src/animatic/pane_layout.js`): the
  monitor's opening width is derived from the height it can now reach —
  `(h − chrome) × 9/16`, clamped — instead of a flat fraction of the width, and
  the timeline goes back to the long workspace's height because it no longer
  steals any from the picture. Dragged sizes are untouched, still per workspace.
- **A WORKSPACE ICON IS NOW A MAP OF THE WORKSPACE.** `▭` / `▯` said one was
  wider than the other and nothing about where anything goes. `layout-long` and
  `layout-reel` (`client/src/components/Icon.jsx`) draw the actual arrangement —
  the seams where the real seams are, with the Program pane filled in — so the
  Reel icon literally shows the tall monitor beside a stacked timeline. ⚠ **Move
  a pane in the CSS and the icon has to move with it.**
- **THE MAP FILLS THE BUTTON.** First cut drew the icons inset like the symbol
  icons and sized them at the app's `1.05em` — about 12px of a 2.3rem square,
  reported straight back as "icon good but see small". A symbol is recognised by
  its outline and survives that; a map is read by its internal divisions and does
  not. The window now fills the 24-box (1.6 → 22.4) and the svg is **1.75rem** —
  `rem`, not `em`, because the size has to follow the BUTTON rather than a font
  size that exists for a label this button doesn't have. Same in the picker
  (1.6rem in its 1.9rem square), where CSS owns the size for both.
- **The top bar wears the layout instead of a gear.** The button beside
  "LONG VIDEO WORKSPACE" was `settings`, which says "settings live here" and
  nothing about what pressing it changes; it now draws the workspace you are in
  and is titled `<workspace> — click to switch layout`. Same button, same modal.
- Files: `Icon.jsx`, `animatic/workspace.js` (`ico` is an icon name now, plus
  `workspaceIcon()`), `components/AnimaticEditor.jsx` (top-bar button + picker),
  `animatic/pane_layout.js`, `styles/animatic-editor.css`.
- **Verified:** `npm run build` clean, and the emitted CSS carries the grid
  areas. ⚠ **Not driven in a browser** — the new reel arrangement and both icons
  need eyes at a real window size.

### 2026-08-18 — CHANGING THE ASPECT RATIO: THE STRETCH AND THE LOZENGE

- **Reported:** "when I change Aspect ratio in reel the video stretch, but when
  I go to properties and increase scale 110 my image fit good… but shapes not
  look resize." Two faults, one trigger.
- **1. THE MONITOR WAS NOT REDRAWING — that is the whole of the "stretch".**
  `ProgramCanvas` sizes its backing store *inside* the draw effect, and no
  dependency of that effect changed when only the frame's SHAPE did. So the
  canvas kept its old pixels and the browser scaled a 16:9 composite into a 9:16
  box. Nudging Scale "fixed" it because it changed `scene`, which re-ran the
  effect — the 110 was never doing anything; the redraw was. ⚠ **The same fault
  hit every pane-seam drag, ~ and every window resize**, and would have kept
  hitting them.
  Fixed with both halves: `settings.aspect_ratio` in the dependency list, so the
  redraw belongs to the commit that reshaped the box (an observer alone reports
  a frame late — one stretched frame every time), and a **ResizeObserver** on the
  canvas for the changes React cannot see. Cheap on a drag: measuring is
  coalesced to one animation frame, and a size that rounds to the same whole
  pixels returns the same state object, so React bails out and nothing redraws.
- **2. SHAPES DISTORT BECAUSE `w`/`h` ARE FRACTIONS OF THE FRAME.** That is the
  model, here and in `draw_shapes`, so the same two numbers draw a different
  rectangle in a different frame: 16:9 → 9:16 turned a square star into a tall
  lozenge. `refitBox()` (`animatic/aspects.js`) carries the numbers over at the
  moment of the change, preserving the box's **proportion** and its **apparent
  size** — both frames measured against their short edge, exactly as
  `resolve_size()` does. It **round-trips**, so flipping between two shapes to
  compare them costs nothing, and a box too big for the new frame is scaled down
  whole rather than cropped. Applied to overlays too, same reasoning.
  **Pictures are deliberately NOT carried**: `placePicture` re-fits them from the
  source every draw, so a stored correction would be a second one.
- ⚠ **`reshapeFrame()` IS THE ONE WAY IN**, and a plain `setSettings` beside any
  aspect control is now a bug: the Program menu, the Shape chips *and their ↺*,
  the "Make it 9:16" offer and **the export presets** (TikTok reshapes the film —
  it used to stretch every shape on the way past, silently, from inside the
  export dialog) all go through it. One event, so React commits settings +
  shapes + overlays together: one document change, **one Ctrl+Z**.
- **A REAL PARITY BUG, CAUGHT BY THE NEW CHECK AND FIXED.** `frameSizeFor()`
  derived unlisted ratios off the SHORT edge; `_base_size` in animatic.py uses
  the LONG one, so 5:2 read 2700×1080 in the dialog and encoded 1920×768. (The
  code this replaced fell back to 16:9 for unlisted ratios, so it disagreed too,
  differently.) The client is now the twin of the server, table and fallback.
- **New:** `tests/aspect_refit_check.py` — the `selection_check.py` pattern
  (Python driving `node` over the pure JS). Pins proportion, apparent size, the
  round trip, the clamp, and **checks every size against the server's own
  `resolve_size` rather than a second copy of the rule**. 10/10 pass.
  Re-ran motion, transition, selection, video-clip and autoframe checks: all
  green. `npm run build` clean. **The redraw fix cannot be checked offline and
  has not been driven in a browser** — that one is a genuine gap.

### 2026-08-18 — THE SHAPE OF THE FILM IS A CONTROL IN THE PROGRAM HEAD

- **Asked for:** "give Aspect ratio change function in program panel… I switch
  reel/shorts video workflow so my Aspect ratio still 16:9".
- **What was actually wrong:** the aspect ratio *was* changeable — Video
  properties → Frame → Shape — but Video properties is the pane you are **not**
  looking at whenever a clip is selected, which in the editor is almost always.
  So switching to the Reel workspace showed a tall monitor, a 16:9 film, and no
  visible way to change it. The ⚙ modal's "your video stays 16:9" made it worse:
  it said what had *not* happened and never said where the control was.
- **Built:**
  - `client/src/animatic/aspects.js` — one list of shapes. `ASPECTS` (the five
    you can pick), `BASE_SIZES` (seven, wider on purpose: a storyboard board can
    arrive 3:4 or 21:9 and must still be measured exactly), `frameSizeFor()`,
    `aspectNumber()`, `knownAspect()`. Three places used to answer this
    separately — the Shape chips, the editor's size table, the export dialog.
  - `AnimaticEditor.jsx` — the ratio menu in the **Program pane head**, beside
    the title, where the thing that changes shape when you press it is. It shows
    the real output size (`1920×1080 · 24 fps`) instead of repeating the ratio,
    and it offers the project's own shape as an extra option when that shape
    isn't one of the five, rather than showing the nearest one it knows.
  - **A one-press offer, not an automatic change.** In the Reel workspace with a
    landscape film, the head grows a `Make it 9:16` button. ⚠ `chooseWorkspace`
    is **still forbidden from writing settings** — rearranging your screen must
    not silently reshape a finished edit — and this doesn't change that: it is
    the user pressing a button. One direction only: "Reel / Shorts" states what
    shape it is for, while Long is the *default* workspace, so a vertical film
    sitting in it means nothing and a nag there would fire on every project.
  - `VideoProperties.jsx` reads `ASPECTS` from the shared module; the ⚙ modal now
    names where the ratio lives.
- ⚠ **ONE FIELD, TWO CONTROLS.** The Program menu and the Shape chips both write
  `settings.aspect_ratio` — neither holds its own copy — so they cannot disagree,
  and both are saved and undone by the project hook like any other edit.
- **Verified:** `npm run build` clean (122 modules). **Not opened in a browser**
  (standing "browser tests on request only" rule), so the menu, the offer and the
  monitor re-shaping have not been driven live.

### 2026-08-18 — MEDIA PANE HAS TWO VIEWS, AND ＋ ADD LAYER MOVED TO THE TOP

- **Asked for:** in the Storyboard → Animatics editor, "add list View and icon
  View so user see easily assets" in the Media pane; and "add Layer buttun move
  up of caption not look beter in below".
- **Built:**
  - `client/src/animatic/media_view.js` — the preference, modelled on
    `workspace.js` exactly: `icon` | `list`, in `localStorage`
    (`cas_animatic_media_view`), **UI only**, default `icon`. It is how you like
    to work, not a property of the animatic, so it is not saved on the project.
  - `FrameStrip.jsx` — a `view` prop that puts `fs-view-icon` / `fs-view-list` on
    the wrapper and **does nothing else**. ⚠ Both views are the SAME cards in the
    SAME DOM order, so drag-to-reorder, the typed hold and the tools have one
    code path and cannot disagree between views. The only markup change: the
    label is always rendered (`f.label || "Frame N"`), because in list view the
    name *is* the row and an unnamed card would be the one row you can't read.
  - `AnimaticEditor.jsx` — the switch in the Media pane head, built from the
    existing `.an-tool` buttons the timeline's V/C/B/N/H/Z already use (no new
    control invented). Shown on the **Media** tab only — the Shapes tab is a
    fixed gallery, so a view switch over it would do nothing.
  - `Icon.jsx` — `grid` and `list`, drawn as what they arrange (four tiles /
    three rows) so the pair reads as one switch with two positions.
  - `animatic-editor.css` — the two layouts, and **the Reel workspace no longer
    lays the frame cards out**: it used to force a grid, which would now silently
    overrule the button the user just pressed. Icon view is the default, so Reel
    still opens as a grid — it can just be turned off now. Same for the ≤1180px
    stacked override.
- **＋ Add layer is now the head of the gutter, not its tail.** Below the lanes it
  sat past the last one — off the bottom on a project with a few layers, so the
  way to add a layer was reachable only by scrolling to the end of what you
  already had, and it moved every time you added something.
  ⚠ **IT IS A SIBLING OF `.tl-cols`, NOT A CHILD OF THE GUTTER, and must stay
  that way.** The labels line up with the tracks because both columns start at
  the same y; anything added inside the gutter pushes its labels down while the
  tracks stay put — the exact misalignment the LANES block in
  `animatic-lanes.css` warns about. Outside, it shifts both columns equally.
  Its width is `--tl-gutter-w` on `.tl-wrap` — **one** number now, because the
  gutter and this head are both that wide and written out twice they drifted
  apart at the 720px breakpoint.
- **Verified:** `npm run build` clean (121 modules). **Not opened in a browser
  this session** — per the standing "browser tests on request only" rule, so the
  two views and the moved button have not been driven live.

### 2026-08-18 — RESIZABLE PANES: the gap between two panes is the handle

- **Asked for:** the Reel workspace's Program pane was too small to work in
  ("not perfect" — the monitor was a postage stamp), and, more generally, the
  four panes should be sized BY HAND: "panel in between gap drag so this happen".
- **Built:**
  - `client/src/components/PaneSplitter.jsx` — one component for both axes. It
    lives IN the 0.55rem the panes grid used to spend on `gap` (`--an-seam`), so
    nothing moved to make room for it: the dead margin became the handle, which
    is where every NLE puts it. Drag, double-click to reset, arrow keys / Home
    when focused; `role="separator"` with the real min/max on it.
    ⚠ The size is read ONCE at pointer-down and every move measured from it —
    accumulating deltas drifts as soon as a drag hits a limit and comes back.
  - `client/src/animatic/pane_layout.js` — the model: `left`, `right`,
    `timeline`, in **px**, **per workspace**, in `localStorage`
    (`cas_animatic_panes`). Px because that is what a drag produces (a fraction
    would silently resize the pane you sized to fit a waveform). Per workspace
    because Reel wants a wide monitor and a short timeline and Long wants the
    opposite — which is also the fix for the too-small monitor: Reel's Program
    column now opens at ~30% of the window instead of a 12rem file-list column.
    The middle column is always `minmax(0, 1fr)`, so the numbers cannot disagree
    about how wide the window is.
  - `AnimaticEditor.jsx` — the three sizes as inline custom properties on
    `.an-nle`, the three splitters between the panes, a `resize` listener, and a
    save that waits 250ms **and only fires once a seam has been dragged**
    (saving on mount would freeze a laptop's defaults onto every later screen).
    What is APPLIED is the clamped copy, not the state, so a small window
    borrows a pane's width rather than permanently trimming it.
  - `animatic-editor.css` — `.an-panes` is five tracks now (pane, seam, `1fr`,
    seam, pane) with `gap: 0`; `.an-nle` likewise, since a flex gap would sit on
    both sides of the horizontal seam. The timeline's height is
    `var(--an-timeline-h)`: it no longer grows with the number of layers, which
    was a fine guess and the wrong answer once there is a handle. **The 1400px
    column overrides are gone** (the defaults scale with the window instead), and
    ~ hides every seam.
  - ⚠ **`.an-screen`'s 56vh cap is off inside the workspace.** The fitter already
    bounds the picture on both axes; with the width definite, a max-height that
    bites shortens the box without narrowing it and the preview stops matching
    the exported frame — which only became reachable now that a pane can be
    dragged tall. The stacked layout (≤1180px) has no size container, so it puts
    the cap back.
- **THE BLANK EDITOR, AND THE RULE IT BROKE (fixed same day).** The first cut put
  the two pane-size `useEffect`s and a `useRef` down beside the layout code they
  belong to — which is **below `if (loading) return …`**. So the loading render
  ran fewer hooks than the one after it, React threw *"Rendered more hooks than
  during the previous render"*, and the whole app went black: there is no error
  boundary, so an editor that throws takes the page with it. The hooks now sit
  with the rest at the top of the component, with a ⚠ saying why they can't move
  back. **Anything hook-shaped in `AnimaticEditor` goes above line ~430.**
- **Verified in a real browser** (Playwright against an isolated API): the editor
  opens; three seams; dragging Media 240→360, Properties 320→424 and the timeline
  270→350; the min clamp holds at 168 with the pointer running past it;
  double-click resets one pane only; `~` hides every seam and gives the timeline
  the full 820, then hands back the dragged 270; at 1000px the panes stack and
  the seams go, and widening restores the sizes; leaving the editor and reopening
  it restores them from `localStorage`. In the Reel workspace Program leads at
  480px with a 459×258 picture — the postage stamp is gone.
  `tests/e2e_animatic.py` reaches the end with 4 content failures + a 404 console
  check, none of them layout: they assert `Frame shape` (the pane says something
  else now), one `.an-vol` (the Media mixer adds one per track — 5), a Colour
  card label, and `.an-screen img` matching the box (the picture is a CANVAS
  since Phase 4). **All four are about the properties work in progress, not this
  change** — and note the suite cannot be compared against `git stash` here,
  because the CSS files it would revert carry uncommitted work of yours too.

### 2026-08-17 — WORKSPACES: ⚙ in the editor, and the panes rearrange

- **Asked for:** a settings icon in the editor's top bar holding two named
  workspaces — *Long Video Workspace* (what image 1 already showed) and
  *Reel / Shorts Video Workspace* — where switching **changes the UI only, not
  the video format**.
- **Built:**
  - `client/src/animatic/workspace.js` — the two workspaces, and the per-browser
    `localStorage` memory of which one you're in (`cas_animatic_workspace`).
    Modelled on `theme.js`: a preference about *you*, not about the animatic, so
    it is deliberately NOT a project field and never reaches the server.
  - `AnimaticEditor.jsx` — `workspace` state stamped on `.an-nle` as
    `an-ws-long` / `an-ws-reel`, the workspace name + ⚙ button in the top bar
    (start of the right-hand cluster, where Premiere puts the same thing), and
    the picker itself as a modal reusing the add-layer picker's
    `.an-layer-list` / `.an-layer-opt`. `chooseWorkspace` also drops a maximized
    pane, because "which pane fills the screen" means something else once the
    panes have moved.
  - `Icon.jsx` — a `settings` gear, drawn as a ring of spokes; a scalloped cog
    turns to mud at 1em.
  - `animatic-editor.css` — a "Workspaces" block. `an-ws-long` is the default
    and has NO rules; everything there is what reel does differently (grid
    `order`, column widths, frame cards in an auto-fill grid, a slightly shorter
    timeline so the tall monitor gets the height). ⚠ Both responsive queries
    repeat the reel overrides — `.an-nle.an-ws-reel .an-panes` outranks
    `.an-panes`, so without that the layout would keep three columns down to
    phone width.
- **The rule this feature is built on:** a workspace may not imply a frame size.
  The monitor keeps the project's aspect ratio in both layouts (`.an-screen-fit`
  is a size container, so a 16:9 project in the reel layout is simply a short
  wide picture in a tall pane) — the export is unchanged, so the preview must be
  too. The picker says so in words as well.
- **Verified:** `npm run build` in `client/` passes (118 modules). **Not opened
  in a browser** — this is a layout change, so someone has to look at it.

### 2026-08-17 — PHASE 8: PERFORMANCE & EXPORT — the render goes wide, and an export is not always an MP4

Phase 8 in full: the still-render loop runs across processes, the editor scrubs
on half-res proxies, and the export dialog opens on a preset that names a
destination rather than a codec.

- **Asked for:** the Phase 8 spec — `proxies.py`, `export_presets.py`,
  `tests/export_perf_check.py`, a `multiprocessing.Pool` over the still loop in
  `animatic.py`, and a preset dropdown in `AnimaticEditor.jsx`. Nothing is left
  unbuilt. (The "thumbnail/waveform cache" from the older one-line roadmap entry
  was NOT in the spec's file list and was not built — see Next Steps.)

**FOUR THINGS TO KNOW BEFORE TOUCHING ANY OF IT.**

**(1) THE STILLS ARE PLANNED, THEN DRAWN — and the split is the feature.** The
render loop used to decide "is this still new? then draw it and call it
`f{len(rendered)}.png`" in one pass, which makes the FILENAMES depend on the
order things finish in. It is now two passes: one that works out the distinct
stills and names them all, and one that draws them in any order at all. That is
the only reason a pool is safe here, and it is checked the only way worth
trusting — `tests/export_perf_check.py` exports the same 216-still project
serially and in parallel and **hashes both MP4s**. Measured on this machine:
**29.0s → 11.8s on 8 workers, byte-identical output.** ⚠ Below
`_POOL_MIN_STILLS` (48) it stays serial on purpose: a pool started for twelve
stills is slower than the loop it replaced. `ANIMATIC_EXPORT_WORKERS=1` forces
serial, and a pool that won't start falls back to serial with a warning rather
than failing the export.

**(2) `_detached_main` IS WHY YOU DON'T HAVE TO GUARD YOUR ENTRY POINT.** This is
the Windows-spawn trap the spec warned about, closed at the source. A worker is
a fresh interpreter, and before it runs anything of ours `multiprocessing`
reconstructs the parent's `__main__` — which for a plain script means
**re-executing that script in every worker**. Proved, not assumed: with the fix
neutered, an unguarded probe script's body ran **4 times** (once per worker) and
started four pools; with it, once. The fix swaps `sys.modules["__main__"]` for a
stub whose spec is named `__main__` for the microseconds the pool is being
created, so the child's own `_fixup_main_from_name` returns immediately and
imports nothing. Workers reach `_render_still` by importing `animatic` off the
inherited `sys.path`, which is all they ever needed. Everything crossing the
boundary is **plain data** — `_source_for` is called in the parent, so no worker
knows `video_frames` exists, and no Pillow image is ever pickled.
⚠ **Cancellation stays in the parent**, between results: a worker cannot see a
flag that lives in the server's job store. So stop is felt within one still, and
the test asserts it lands mid-batch (stills on disk, most of them not).

**(3) AN EXPORT IS NOT ALWAYS AN MP4.** `container` is `mp4` | `gif` | `png`,
chosen by a preset (`export_presets.py` ⇄ `client/src/animatic/export_presets.js`,
a twin pair compared field for field through node). Two rules in that table:
a preset **states only what it means** — GIF and Still deliberately do NOT state
an aspect ratio, so exporting a thumbnail cannot reshape the film, while YouTube
and TikTok do, because a 9:16 file is the entire point of choosing them — and
`match()` is the **exact inverse** of `apply()`, so editing a field by hand just
drops the dialog to "Custom" rather than fighting it. **A PNG never reaches
ffmpeg**: the composite Pillow just made IS the file, which is what makes a
poster frame provably the same picture the video shows, and it renders only the
one segment (and extracts only the video clips) that moment needs. A GIF gets a
real `palettegen`/`paletteuse` pass — ffmpeg's default palette bands every sky
and every dissolve.

**(4) A PROXY SAVES PIXELS; BYTES ARE THE USUAL CASE, NOT THE GUARANTEE.** The
editor now fetches each frame at `?w=960` — half the export's 1920 long edge —
so a 1920px panel is a quarter of the decoded bitmap it was, which is the memory
and decode win a sixty-panel board actually needs. The FILE is usually much
smaller too, and **for line art it can be larger**: resampling turns hard edges
into anti-aliased gradients that PNG encodes worse. That was found by the test
and is now stated in both directions rather than tuned away. ⚠ **The export
never touches `proxies.py`** — `build_animatic` opens sources — so no proxy can
reach the encoder. What the preview trades is SHARPNESS at high zoom and nothing
else: a proxy is a lossless resize, so colour, timing and geometry are
untouched. Keyed by a **stat** (path + mtime_ns + size + edge), never a decode,
for the same reason `_frame_version` is — and the test asserts that redrawing a
panel *in place* moves the proxy, which is the same bug caught one layer up.
`ANIMATIC_PROXY_EDGE=0` turns the whole thing off.

**Files.** New: `proxies.py`, `export_presets.py`,
`client/src/animatic/export_presets.js`, `tests/export_perf_check.py`. Changed:
`animatic.py` (`_render_still`, `_render_all_stills`, `_detached_main`,
`export_workers`, `_segment_at`, `_summary`, the container branches),
`server/animatics.py` (`_proxy_dir`, `_exported_file`, `?w=` on the frame route,
container/still_ms in the export payload), `server/schemas.py` (three optional
settings), `server/worker.py` (`container` in the result), `client/src/api.js`
(`fetchAnimaticMedia(path, maxEdge)`),
`client/src/components/AnimaticEditor.jsx` (the preset row, the container-aware
dialog and download button, `PREVIEW_MAX_EDGE`), `client/src/styles/animatic.css`
(`.an-select:disabled` — the export dialog is the first place a select is
disabled, and without it the row looks live and ignores you).

**Verified.** `tests/export_perf_check.py` — **57 checks**, all passing: the twin
table under node, byte-identical parallel/serial MP4s, the speed-up, a mid-batch
stop leaving nothing behind, every preset's real width/height/fps measured out of
ffmpeg's own banner (there is no ffprobe — same trick as `probe_duration`), a
still proving it is the SECOND shot at 700ms rather than the first, and the proxy
rules including the redraw case. Every existing suite still passes
(`render_parity`, `keyframe_ops_check`, `animatic_motion_check`, `video_clip`,
`transition`, `effects`, `audio_mix`, `captions`, `autoframe`, `selection`,
`audio_razor`); `npm run build` clean; `import server.main` clean; class audit
clean.

⚠ **NOTHING HAS BEEN OPENED IN A BROWSER.** The preset dropdown, the disabled
rows, the new note lines and the renamed Download button have not been looked at
— and neither has the one thing that genuinely deserves eyes: **the monitor now
draws from proxies**, so the whole preview signal path is being fed different
bytes than before.

### 2026-08-17 — PHASE 7: THE MOAT — the board reaches into the editor

Phase 7 in full: redraw a shot from the Properties pane, run a shot longer, pull
every cut onto the beat, and re-frame a whole board for a different screen shape.

- **Asked for:** the Phase 7 spec — `RegeneratePanelInline.jsx`, `autoframe.py`,
  `tests/autoframe_check.py`, plus the four editor actions and the two server
  proxies. Nothing is left unbuilt.

**FOUR THINGS TO KNOW BEFORE TOUCHING ANY OF IT.**

**(1) A FRAME'S URL CARRIES `?v=` NOW, AND THAT IS THE WHOLE FEATURE.** An
animatic frame is a REFERENCE to a board panel, so redrawing the panel updates
the animatic for free — the path is resolved from the board on every request.
That has always been true and it has never been visible, because every picture
in this app is fetched as an authed blob and **cached by URL**, and
`/animatics/{id}/frame/{frame_id}` is built from two ids a redraw does not
touch. `_frame_version` (server/animatics.py) stamps the panel file's `mtime_ns`
— and the style variant — into the url, and the editor's fetch effect now
remembers **which url each blob came from** (`urlSrcRef`) rather than only
whether it has one. Both halves are required: the server alone changes the
string nobody re-reads, the client alone re-reads a string that never changes.
This is rule 2 of the 2026-08-09 three-rule entry, arriving three phases late.
⚠ `_frame_version` is one `stat` and never a decode — it runs for every frame on
every read and every autosave, so a video clip's version comes from its source
file and in point rather than from extracting the thumbnail those two produce.
⚠ The url is NOT part of the saved document (`frameForSave` drops it, and the
signature is built from `frameForSave`), so writing a new one is not an edit: it
does not dirty the project and does not land on the undo stack.

**(2) THE BOARD'S TWO ACTIONS HAVE ONE IMPLEMENTATION EACH, IN `server/common.py`.**
`regenerate_board_panel` and `submit_sequence_run` were lifted out of the route
bodies in `main.py`; both routers now call them. This is not tidying — the
editor reaching the same two actions with a second copy of the variant handling,
the continuity bible and the resume arithmetic would have given us two of each to
keep in step, and the animatic's copy is the one nobody would have noticed had
fallen behind. `sequence_summary` came with them for the same reason.

**(3) "MAKE THIS SHOT 2s LONGER" EXTENDS THE PLAN, IT DOES NOT RE-PLAN.**
`plan_beats` takes `existing_poses`: the lines the drawings on disk were made
from are preserved **word for word**, only the tail is asked for, and only the
frame NUMBERS move (`respace`, because the same drawings now span a longer shot).
A plain call with a bigger `count` — which is what a naive lengthen does — leaves
drawing 17 continuing a motion drawings 1–16 never made, **and there is nothing
in the pictures to reveal that until you play it**. The run resumes, so 4s → 6s
costs eight drawings, not twenty-four. Shortening asks the model for nothing and
leaves the extra drawings on disk, so lengthening it again later is free. ⚠
`run_panel_sequence` now does `hold = planned_hold or hold`: without it a
lengthened shot is the one path that draws with the invariant fence down.

**(4) THE REFRAME ASKS FOR THE SUBJECT, NEVER FOR THE CROP.** `autoframe.py`'s
one model call returns the box that must stay in frame; `crop_box` then computes
a box of EXACTLY the target aspect around it, and `frame_transform` turns that
into `scale`/`x`/`y`. A model asked for "a 9:16 crop" returns roughly 9:16, and
roughly is a reframe that is subtly wrong on every shot of the board. The split
also makes the result checkable: `crop_box` provably contains the box it was
given (the clamp can only move the crop toward the subject's own side), so "did
the person survive the reframe" is a property of that file rather than of that
afternoon's weights. ⚠ **What lands on the clip is `scale`/`x`/`y` and nothing
else** — no crop field, no new render path, and an auto-reframed shot is
indistinguishable from one panned by hand. ⚠ **A clip that was already keyframed
keeps its move**: `apply_to_frame` multiplies each scale key by the ratio and
each pan offset by the same ratio, because `x` is a fraction of the CANVAS and
the same gesture across the same part of a picture drawn 3× larger is 3× the
number. Writing a static value under existing keys would have been a reframe
that did nothing at all, at every instant.

**Also: cut to beat.** `client/src/animatic/beat_cut.js`, editor-side only, no
Python twin (the same split as `selection.js` and `audio_clips.js`). Three rules
carry it and each one is a check: **a cut is not a thing you can move** — the
sequence is a flow, so moving one cut means rewriting the durations either side;
**beats cluster and cuts must not** — the nearest beat to two consecutive cuts is
often the same beat, and without a running floor that is a clip of zero length,
a picture that never appears in an edit that still claims to have it; and **a cut
nowhere near a beat is left alone** (`REACH_MS`), or this feature rewrites the
edit instead of tightening it. Free — the decode already happened in `beats.js`.

**Files.** New: `autoframe.py`, `tests/autoframe_check.py`,
`client/src/components/RegeneratePanelInline.jsx` (which also holds
`RelengthShotInline`), `client/src/animatic/beat_cut.js`. Changed:
`panel_sequence.py` (`respace`, `plan_beats(existing_poses=…)`, `_EXTEND_PROMPT`),
`server/common.py` (the three shared helpers), `server/main.py` (its two routes
now call them), `server/animatics.py` (`_frame_version`, five new endpoints,
`run_reframe`), `server/schemas.py`, `server/worker.py`
(`submit_animatic_reframe`), `client/src/api.js`,
`client/src/components/AnimaticEditor.jsx`, `FrameProperties.jsx` (a `board`
slot), `VideoProperties.jsx` (a `reframe` slot), `client/src/styles/properties.css`.

**New endpoints.** `GET/POST /animatics/{id}/frames/{frame_id}/panel` (read the
shot's wording / redraw it — synchronous, answers with the FRAME so the caller
has a url to re-fetch), `GET/POST /animatics/{id}/frames/{frame_id}/sequence`
(the shot's key poses / re-block at a new length — ⚠ **the job it returns is the
STORYBOARD's**, because the drawings belong to the board, which is also why this
animatic stays editable while it runs), and
`POST /animatics/{id}/reframe/estimate` + `/reframe` (the usual free-then-priced
pair, on the video pool, back to QUEUED never FAILED).

**Verified.** `tests/autoframe_check.py` — **147 checks**, and the one that
matters most pushes autoframe's `scale`/`x`/`y` through the REAL
`animatic_render.place_picture` and measures the subject's corners in canvas
pixels, because autoframe's arithmetic is the INVERSE of that function and the
only honest way to test an inverse is against the thing it inverts. Also: the
patch resolved through `scene_at` unchanged, a Ken Burns push surviving a
reframe, a real job record + a real file on disk proving a redraw moves the url,
and the beat arithmetic under node against a 120 BPM click track. Every existing
suite still passes (`render_parity`, `keyframe_ops_check`, `animatic_motion_check`,
`key_pose_scope/refresh`, `captions`, `selection`, `audio_razor`, `audio_mix`,
`transition`, `effects`, `video_clip`, `animate_guard`, `panel_border`,
`panel_normalise`, `storyboard_draft`, `plan`); `npm run build` is clean and the
class audit is clean. ⚠ `profile_check` fails on a stale throwaway account and
`effects_parity_check` skips for a missing native `gl` — **both fail identically
on a clean tree and neither is Phase 7's doing.**

⚠ **NOT ONE OF THE FOUR AI PATHS HAS BEEN CALLED FOR REAL, and nothing has been
opened in a browser.** `autoframe.detect_subject`'s request shape — the vision
part, the JSON schema, the subject prompt — is written from the documented API
and stubbed in the test, exactly as the Phase 5 captions call still is; the
extend-plan prompt has only ever run through its fallback. The four new pieces of
UI (the redraw group with its veil, the length chips, the 🥁 tool, the reframe
dialog) have not been looked at.


### 2026-08-17 — CAPTION BOXES FILL THE WAVE BLOCKS, and the captions run reports itself

Second pass on the same report, and **it changes the alignment written earlier
today — read this entry before that one.**

- **Asked for:** *(1)* *"When I click Write captions from this track I see no
  processing bar — captions just appear. Add one so the user understands the
  generating time."* *(2)* *"I generated captions again and it still doesn't
  match. There is blank space — the caption box starts blank. I want each wave's
  start to its end to be the caption box, not placed before the voiceover wave."*
- **Why the first attempt was not enough.** It shared the speaking time out
  GLOBALLY and then nudged each edge toward a run of sound if one happened to be
  within `SNAP_MS`. When the nudge could not reach, the box kept an edge in the
  middle of a silence — a caption opening with blank space, drawn before its own
  wave. Approximate placement plus a local tidy-up cannot produce an invariant;
  only structure can.

**What changed — two things, both in `captions.py`.**

1. **The measurement is now an AMPLITUDE ENVELOPE with an ADAPTIVE threshold,
   not ffmpeg's `silencedetect`.** `peak_envelope()` has ffmpeg decode to raw
   mono s16 on stdout and keeps one PEAK per 20ms window — ⚠ **the same quantity
   `client/src/animatic/beats.js::peaksOf` draws the timeline waveform from**, so
   a run of sound found here is a block of sound the user can *see*.
   `spans_from_envelope()` (pure) turns that into runs, and the threshold is
   derived from the track: a multiple of its own noise floor, floored by a share
   of its loudest peak, and **capped by `MAX_THRESHOLD_SHARE`**. That cap is not
   a detail — "the quietest tenth of the track" is only a *noise* floor on a
   track that has quiet in it, and without the cap continuous narration measures
   as entirely silent, which is precisely the material this feature is for. A
   test catches it.
2. **`align_lines` deals the lines into the runs and fills each run exactly.**
   Each line goes to the run holding the midpoint of its character-proportional
   share (monotone, so order is kept); each run's duration is then shared among
   just its own lines, with **the first line starting exactly where the sound
   starts and the last ending exactly where it stops**. Two invariants now hold
   by construction rather than by luck, and both are asserted on every fixture:
   **no caption ever starts in a silence**, and **every run of sound is covered
   from its first millisecond to its last**. Sound with no line of its own is
   *held* by the caption already on screen; sound before *any* line has been
   placed is reached back to by the first one (its start only — widening the run
   would put the boundaries *between* lines inside the silence).

**And the progress bar.** `run_captions` took a `progress_cb` and reports four
stages (`server/worker.py` persists them); the measurement now runs BEFORE the
model call, so an ffmpeg failure is discovered while nothing has been spent. The
bar is also drawn **inside the Captions group in the properties pane**, beside
the button that started it — the status strip at the top of the editor had always
reported this, but it is three panes from where the user just clicked, which is
why the pass read as "nothing happened, then captions appeared". Same
`spinner-inline` + `.an-status-bar` markup as the strip, wrapped for a narrow
column (`.an-prop-progress`). ⚠ **The percentage is a STAGE, not a measurement** —
transcription is one model call that cannot be asked how far through it is, and a
bar creeping during it would be an animation pretending to be progress.

- **Verified:** 34 offline checks in section 4c (was 19), all driven through a
  STUB envelope, plus three that run the real ffmpeg decode against a generated
  WAV and are *skipped with a printed warning* — not silently passed — when
  ffmpeg is absent. Against a synthetic narration-shaped track with hiss under
  it, every wave edge was found within **40ms** (two windows) and every caption
  box landed on a wave onset. Full `captions_check.py`, `audio_mix_check.py`,
  `audio_razor_check.py`, `selection_check.py`, `effects_check.py` pass;
  `npm run build` clean.
- **Not done / honest caveats:** **still no real captions run, and nothing opened
  in a browser** — the progress row and the new CSS are unverified visually, and
  a character-proportional fit *within* a run remains the thing only a real
  voiceover can judge. Two known limits, both deliberate: a caption shorter than
  `MIN_LINE_MS` is still extended past its wave's end by `tidy_lines` rule 3
  (readability wins over the box edge at the tail — the complaint was about the
  head); and a sentence the speaker reads straight through a pause is placed
  inside ONE run rather than spanning the pause, so it ends slightly early.

### 2026-08-17 — CAPTIONS ARE TIMED FROM THE WAVEFORM NOW, not from the model's guess

- **Asked for:** *"When I generate captions the text is best, but on the caption
  timeline the captions don't match the voiceover — the voiceover plays and the
  caption shows after. The voiceover wave doesn't match the caption box
  placement. I want the voiceover words to match the caption words' time frame,
  not play the voiceover first and then show the caption."* One screenshot: the
  Captions lane above a waveform, boxes sitting to the right of the sound they
  belong to, with gaps the waveform plainly does not have.
- **The diagnosis, and it is the important part.** `clip_lines` and `tidy_lines`
  were both *correct* — they were faithfully placing times that were wrong before
  they ever reached them. **`transcribe()` is a language model listening, not a
  forced aligner: its WORDS are excellent and its TIMES are a guess.** It returns
  plausible round numbers that look fine in a list and are visibly wrong the
  moment they are drawn against a waveform. Nothing downstream could have fixed
  that, because nothing downstream had ever measured the audio.

**What was built — two new pure functions plus one measurement, all in
`captions.py`, and the module docstring's part list is now five.**

1. **`speech_spans(path, total_ms)` — the waveform, as numbers.** FREE, no model,
   no quota. ffmpeg's `silencedetect` prints the silences to stderr and the sound
   is what is left over; `-f null -` throws the audio away, so **nothing is
   decoded here** and no ffprobe is needed (there isn't one — see
   `video_assemble.py`). `total_ms` comes from the CALLER as it does everywhere
   else. The parsing is split out as `spans_from_silence_log()` so it is testable
   with no ffmpeg on the box.
2. **`align_lines(lines, spans, total_ms)` — the words laid onto the sound.** The
   two signals are used for what each is actually good at: the model says *what*
   is said and in what *order*, `speech_spans` says *when there is sound*,
   exactly. Lines take a share of the SPEAKING time proportional to their
   character count — the same measure `_split_line` and `_slice_words` already
   use, because speech takes about as long as it is long — and **silence is
   stepped over rather than counted**, so a two-second pause pushes no caption
   late. Then every edge is snapped to a run of sound within `SNAP_MS`, which is
   what puts the caption BOX on the waveform BLOCK instead of a fraction off it.
   ⚠ **It declines to guess:** no spans, or less than `MIN_SOUND_SHARE` of the
   file measured as sound, and the model's own times come back untouched. **The
   fallback is the old behaviour, so a failed measurement can only ever leave
   captions no worse than they were.** In and out are both FILE time, so it slots
   in *before* `clip_lines` and the razor's arithmetic is unaware of it.
3. **`tidy_lines` rule 2 was reversed, and this was a second, separate bug.** Two
   colliding captions were separated by pushing the LATER one's start forward,
   which delayed *every* caption after the first by `GAP_MS` — permanently, and
   for nothing. **A start is when the word is SAID and is the only number here
   that is evidence; an end is merely how long the line has been left up.** So
   the gap now comes off the EARLIER line's end. The earlier line is only
   shortened as far as the new `MIN_HOLD_MS`; below that it keeps its length and
   the later one moves after all, because a subtitle that blinks is worse than
   one that is late.

`server/animatics.py::run_captions` calls the measurement and the alignment
between `transcribe` and `clip_lines`, priced from the same
`clips_of_file[0].duration_ms` (the FILE's length) the estimate uses. Gated by
`captions.ALIGN_TO_AUDIO` (`API_CAPTION_ALIGN=0` turns it off) so a support
answer never has to be "wait for a release"; `API_CAPTION_SILENCE_DB` and
`API_CAPTION_MIN_SILENCE_MS` tune the measurement. **The voiceover path is
deliberately untouched** — `tts.synthesise_timed` returns timings computed from
the PCM byte count, which are exact and need no alignment.

- **Verified:** 26 new offline checks in `tests/captions_check.py` (sections 4c
  and 4d), driven through a STUB ffmpeg log so they need neither ffmpeg nor a
  key. The alignment fixture is built so a perfect answer can be *written down*
  (three runs of sound, character counts in the same 2:3:1 ratio) rather than
  merely looking plausible. `speech_spans` was also run against a real generated
  WAV and returned spans identical to the stub log. Full
  `tests/captions_check.py`, `audio_mix_check.py`, `audio_razor_check.py` and
  `selection_check.py` all pass.
- **Not done / honest caveats:** **no real captions run has been made against a
  real voiceover** — this was verified offline only, and the accuracy of a
  character-proportional fit is the thing a real run would actually judge.
  Nothing was opened in a browser (no frontend file changed; captions are just
  clip data). The alignment assumes a roughly steady speaking rate *within* a run
  of sound, which holds for TTS and narration and is weakest for two speakers at
  very different paces on one track. If the transcript misses a stretch of speech
  entirely, proportional fitting will smear the rest across it — there is no
  guard for that, because there is no signal for it short of a real aligner.

### 2026-08-17 — A SELECTION IS A LIST NOW: rubber band, shift-click, group/ungroup

- **Asked for:** *"I want to drag and select in the timeline because I want to
  delete all the captions but I can't delete them all at once — I have to select
  and delete one by one. Add the function to all layers so I can drag and select
  any layer's content, then delete, move, group and ungroup."*
- **The fault was structural, not missing UI.** The editor held **six "the
  selected X" ids** and exactly one could be set (`selectOnly`), so *every*
  operation was one clip at a time by construction. Deleting a row of forty
  auto-captions was forty clicks and forty presses of Delete, and there was no
  shape in the code for "these clips" to be the subject of a verb.

**What was built — `client/src/animatic/selection.js` (new), and read it first.**
A selection is a LIST of `{ kind, id }`, because it spans lanes (a band catches
pictures, captions and audio together, and they live in different lists) and
because an id is only unique within its own list. `selKey` flattens the pair for
Set lookups, which is what the timeline draws from. The module is pure and
node-testable: keys, the toggle, group expansion, box intersection.

**⚠ TWO KINDS OF "SELECTED", and they are not the same thing.** The six
`selected*Id` states are now the **primary** — the one clip the Properties pane
describes — and `selection` is the whole list. **`selectOnly` is the only writer
of both**, which is what keeps them from disagreeing, and it is also where a
group expands (so every path — the media pane, the monitor's handles, the
keyboard — gets group behaviour for free). Add a third way to select something
and it goes through there.

**Three gestures, and one of them had to be squeezed in beside an old one:**
- **drag the empty part of any lane** → a rubber band; everything it *touches*
  is selected (touching, not containing — a clip whose ends are off screen has to
  be catchable), shift extends. ⚠ **A press that does NOT travel more than 4px
  still scrubs**, decided on the way rather than up front, because dragging a
  lane used to scrub and the ruler is now the only surface that does it by drag.
- **shift/ctrl-click a clip** → in if it was out, out if it was in. It
  deliberately starts no drag: a nudge while aiming would move the whole
  selection.
- **double-click a lane's label** → everything on that row, on screen or not.
  This is the shortest path to what the user actually asked for, and unlike a
  band it cannot miss the clips scrolled past the end of the pane.
- Plus `Ctrl+A` (everything), `Ctrl+G` / `Ctrl+Shift+G` (group / ungroup), and
  Delete, which now deletes the whole selection **in one pass and one undo step**
  (`deleteMany` — one `set…` per list, not one call per clip).

**⚠ THE MARQUEE HIT-TEST IS A DOM QUERY, on purpose.** Every selectable thing
carries `data-sel="kind:id"` and the band intersects those nodes' rects. Each
lane already knows how to place its own clips — a frame from a running total, a
caption from `start_ms`, an audio clip from `start_ms + offset` — and writing
that arithmetic out again to work out what a rectangle covers would be four more
places for it to drift. The browser has already laid the clips out; ask it where
they are.

**Dragging any clip in a selection moves the whole selection**, by that clip's
**snapped** delta — so the spacing between the pieces is exactly what it was.
⚠ **The clamp at 0:00 is on the DELTA, not per clip** (`selectionFloorMs`):
clamping each clip on its own looks the same and is not — drag left and the ones
that reach the front stop while the rest keep going, quietly squashing the
spacing you were preserving. The timeline is handed the same floor so the drag
you see and the write that follows agree. **Pictures are never moved** (`MOVABLE`):
a frame starts where the one before it ended.

**Groups are the one part that is SAVED.** `group_id` is a new field on
`AnimaticTextClip` / `AnimaticShape` / `AnimaticOverlay` / `AnimaticAudio` —
canonical comment on the first — and it is a **shared string on the members, not
a container holding a list of ids**: a container has to be kept in step with
every delete, split, duplicate and undo, and one missed path leaves it pointing
at a clip that is gone. Two consequences worth knowing:
- **the razor's new piece leaves the group** (`splitClip`), for the same reason
  it gets a new id — inheriting it would mean deleting the middle piece deletes
  everything grouped with it, i.e. you could no longer take a pause out of a
  grouped clip at all. The head keeps its group, as it keeps its id.
- **a duplicate leaves the group too.** A copy is a new clip; one that joined
  silently would move and delete with clips the user never pointed at.
- **the renderer must stay unaware of `group_id`** — grouping is an editing
  convenience, and if it changed one pixel of the export then "tidy up the
  timeline" would silently be "change the film". Asserted.

**`SelectionProperties` is the first pane that describes a SET rather than a
clip** (count by kind, nudge, group/ungroup, delete). No per-clip fields in it on
purpose: a selection can hold a picture, a caption and a piece of audio at once,
so most fields would be greyed out for most selections. Click one clip to get its
own pane back — and **clicking a clip that is inside a selection narrows the
selection to it**, or there would be no way back except via empty space.

**Verified:** `tests/selection_check.py` (new) — **34 checks**, the pure model
driven through node (toggle, group expansion across kinds, the band's
touch/edge/wrong-lane cases, a band dragged backwards, the click/drag slop) plus
`group_id` surviving a schema round trip on all four clip kinds and the export
being byte-identical with and without it. `audio_razor_check.py` gained the
group case and passes; `captions_check.py`, `audio_mix_check.py`,
`video_clip_check.py`, `transition_check.py`, `animatic_motion_check.py`,
`effects_check.py`, `keyframe_ops_check.py` and `render_parity.py` all still
pass. `npm run build` clean.
**⚠ NOT OPENED IN A BROWSER. This is a new mouse gesture on the surface you work
on all day and it shares a press with scrubbing** — see Next Steps.

### 2026-08-17 — CAPTIONS FOLLOW THE CUTS, GET THEIR OWN LANE, and the picture lane stopped drifting

All from ONE user report, with a screenshot of the timeline. Four complaints,
three of them the same bug seen from different sides.

- **Asked for:** *"when I generate caption it overlaps my older manual text"*;
  *"I cut audio in the mid and the last before I generate, so the captions don't
  match — Gemini should look at the timeline voiceover clip and not generate
  captions for the cut part"*; *"add caption layer separate, not disturb my text
  layer … always top in layer in timeline"*; and *"the down scrollbar doesn't
  show all the timeline content — I have to press + and −"*.

**(a) CAPTIONS NOW GO THROUGH THE RAZOR — `captions.clip_lines`.** The model
transcribes the **FILE**; the timeline holds **CLIPS** cut out of that file. The
old code took the FIRST clip reading that upload and shifted the whole transcript
by its `start_ms − offset_ms`, which is right only while the track is uncut: cut
the pause out of the middle and every word after the cut is heard EARLIER than
the transcript says, while the words inside the pause are not heard at all — and
were being written anyway. `clip_lines(lines, windows)` walks the transcript
through every clip of that file (`start_ms` / `offset_ms` / `play_ms`) and does
three things: a line inside one clip comes through moved by that clip's own
shift; a line **cut in half** comes through carrying only the words actually said
in the audible part (shared out by character count, the same weighting
`_split_line` uses for time, so no word is written twice and none is invented);
a line whose audio was cut out entirely is **dropped**. A sliver under
`MIN_PIECE_MS` left behind by a cut is dropped too — but only if it was cut, so a
genuinely short line ("No.") is never touched by that rule. `tidy_lines` still
runs on top and is unchanged; its `offset_ms` is no longer used by this path and
says so.

**(b) GENERATED CAPTIONS HAVE A LANE OF THEIR OWN, DRAWN FIRST.**
`captions.CAPTION_LAYER_ID = "captions"` is a **reserved layer id** and a **twin**
of `client/src/animatic/captions.js` (compared by running the JS through node, in
`tests/captions_check.py`, exactly like the font list). The server writes the
lane and the clips in ONE `params` update (`_write_texts(…, layers=…)`) — two
writes could be interrupted between them and leave clips on a lane that does not
exist. The editor draws that lane **above the picture row**, which is the one
exception to "lanes are ordered by kind", and it is deliberate: it is the row you
check against the audio you just cut, and it is where a subtitle track sits in
every NLE. It appears only when there is something on it.
- ⚠ **The lane is drawn whenever caption CLIPS exist, even if the layer record
  is missing** — a clip on a lane nothing renders is invisible on the timeline
  while still burning into the export, i.e. captions you cannot delete.
- ⚠ **The captions poll now re-reads `layers` as well as `texts`.** It didn't,
  so the next autosave would have written the server's new lane straight back out
  of existence.
- The layer CAP is checked BEFORE anything is spent (`/captions` 409s), because
  a lane pushed past `MAX_ANIMATIC_LAYERS` would make every later save 422 —
  losing the user's work because we added a row. `_caption_layer_id` falls back
  to the default text lane if it somehow gets there anyway.
- The voiceover's "add captions too" writes to the same lane, so there is one
  row for everything this app wrote, whichever button asked for it.

**(c) THE PICTURE LANE WAS DRAWING ITSELF WIDER THAN THE TIME IT REPRESENTS, and
that is what "the scrollbar won't show the end" actually was.** `.tl-bars` was a
flex row that added itself up, and `.tl-bar` carried `padding: 0 0.35rem` plus
its borders — **a border-box cannot be narrower than its own padding**, so every
bar shorter than ~0.4s was drawn at a floor of ~13px instead of its real 3px. Five
short frames near the head of a sequence therefore shoved every shot after them
~50px (more than a second) to the right, off the end of a timeline whose width is
computed from the TIME. Hence: the lane no longer lined up with the ruler, the
last shots sat past the right-hand edge, and zooming out with − was the only way
to see your own sequence. Fixed at the root: **each bar is now placed at an
absolute `left` from the same running total the ruler is drawn from**, and the
padding moved onto the labels. A `min-width` can now only overlap a neighbour by
a pixel; it can no longer move one. (`.tl-bar:last-child` → `:last-of-type` while
here — the transition badges are children of that lane too, so the sequence had
silently lost its rounded right-hand end.)
- `ZoomScrollbar`'s thumb is also clamped so it cannot hang past the end of its
  track when `MIN_THUMB_PX` kicks in — at full scroll it looked like there was
  still somewhere to go.

**Verified:** `python tests/captions_check.py` — **20 new checks** on
`clip_lines` (cut-out middle, trimmed head, trimmed tail, a sentence cut in two,
slivers, ordering, the identity case) plus the three twin-constant checks, and
everything that was there before still passes. `tests/audio_razor_check.py` and
`tests/audio_mix_check.py` unchanged and green. `npm run build` clean. The server
glue (`_captioned_clips` → `_clip_windows` → `clip_lines`) was driven on a
hand-built two-clip job: the cut-out line disappears and the third line moves
5.0s → 3.0s, as it should.
**⚠ NOT OPENED IN A BROWSER, and no real captions run has been made** — the
transcription half still has never been called for real. See Next Steps.

### 2026-08-17 — THE RAZOR CUTS AUDIO, and ↺ RESET ON EVERY PROPERTY

Both user-reported, with screenshots. **"I can cut the start and end of audio but
not the middle — I want to see the gap in the waveform and snip it"** and **"add
a reset icon to every property so one click puts it back"**.

**WHY THE MIDDLE COULD NOT BE CUT.** An `AnimaticAudio` had no timeline position
at all: a track began at 0:00 and the only two edits were `offset_ms` (pull the
head in) and `trim_ms` (pull the tail in). There was no way to say *this piece
plays HERE and that piece plays THERE*, so a pause in the middle of a take was
uncuttable by construction. Two fields fix it, and the pair is the whole feature:

    start_ms   where the CLIP sits on the timeline
    offset_ms  how far into the FILE it reads

A cut sets **both** on the second half, by the same amount. Set one without the
other and the audio jumps at the cut — which looks like a rendering bug rather
than like arithmetic, so it is unit-tested case by case
(`tests/audio_razor_check.py`, 17 checks through node).

**AN AUDIO TRACK IS A CLIP NOW, AND ITS IDENTITY IS `id`, NOT `upload_id`.** This
is the one thing to understand before touching any of it. Several entries can
share an upload — that is exactly what a cut leaves behind — so the upload
answers *which sound*, never *which clip*. Everything keyed per clip moved to
`clipId(track)`: the selection, its `<audio>` element, every patch, mute, the
duck's `duck_target`. Everything about the FILE stayed on `upload_id`: the blob
url, the decoded waveform and beats, the captions/transcribe call. **A clip saved
before this has no `id`, and `_audio_tracks_of` backfills it with the upload** —
unique in exactly those projects, so their keys are byte-for-byte what they were.

- **`client/src/animatic/audio_clips.js` (new)** — the razor's arithmetic, and
  editor-side ONLY (`animatic.py` has no twin and needs none: the server renders
  a mix, it never edits one — the same split as `keyframes.js`). `splitClip`,
  `trimClipStart`, `clipAt`, `clipId`, `audioEndMs`.
- **A lane holds a LIST of clips** (`lane.tracks`), grouped by upload so two
  halves of a cut land on the same row. `.tl-audio-clip` became
  `position: absolute` with a grip at **both** ends plus a body you can drag.
- **`adelay=delays=N:all=1` in `audio_graph`, AFTER the fades.** Both `afade`
  windows are measured from the start of the clip — that is what makes a fade
  travel with a trim — so delaying first would push the clip along the timeline
  and leave its ramps at the head of the video. `all=1` because without it
  adelay silences the channels it wasn't given a delay for. A clip that starts
  late can no longer take the `plain` path.
- **`track_play_ms` clamps against `total_ms − start_ms`**, not `total_ms`: the
  room a clip has is what is LEFT of the video after it starts. Twin updated in
  `audio_mix.js` and 4 `start_ms` cases added to the node comparison (11 now).
- **Playback is SCHEDULED per frame** (`syncTracks` in `useTimelineTransport`).
  It used to be `play()` on every element once, because a track ran the whole
  video; the playhead now crosses in and out of clips, so which elements should
  be running is a different answer every frame. ⚠ **The first one playing is
  still the master clock and is never drift-corrected** — the tick reads the time
  off it. Followers are nudged only past `AUDIO_DRIFT_MS`.
- ⚠ **Two different lengths, and swapping them is a real bug.** WHETHER a clip is
  audible is measured against the **span** (the timeline reaches past the
  pictures so you can scrub into the rest of a long track); WHERE its fade out
  lands is measured against the **export**. Both are commented in `gainAt`.
- **The cap on audio is a cap on FILES** (`_audio_files_of`,
  `MAX_ANIMATIC_AUDIO_TRACKS` = 4) with a separate, loose clip ceiling
  (`MAX_ANIMATIC_AUDIO_CLIPS` = 48). Counting clips against the file cap would
  make a track uncuttable after three cuts.
- **How you take a gap out:** razor (C) on the waveform either side of it, click
  the middle piece, Delete. Ctrl+K cuts the SELECTED audio clip when one is
  selected, else the picture sequence, as before.
- Captions from a cut track shift by `start_ms − offset_ms`, not just
  `−offset_ms`: a transcript's times are the FILE's and the two shifts pull in
  opposite directions.

**↺ RESET, ON EVERY PROPERTY ROW.** `ResetButton` + `reset`/`changed`/`resetTo`
on `PropRow` and `PropSlider`, wired through all seven panes and `EffectsPanel`.
⚠ **Always rendered, never conditional** — disabled and faint at the default —
for two reasons, and the second decided it: a control that appears only once you
have touched the property is one you discover twice; and because it is always
there, a lit ↺ down the pane **is** the list of what you have changed on this
clip, which is the question you are really asking when you go looking for a
reset. `changed` is passed in by the caller because only the caller knows the
default, and a table of defaults in a layout file is how the pane and the
document start disagreeing. On an **animatable** row the ↺ also clears that
property's keyframe track (`fx:<id>:<param>` / `mask:x` included) — a property
left animated is not back where it started, and leaving the keys behind makes
the reset look broken the moment the playhead moves. Two hand-written buttons
were removed because the ↺ is now the one way: "Use whole track" and "Use whole
clip".

- **Verified** — `tests/audio_mix_check.py` (now 70 checks: `start_ms` in the
  twin comparison, the graph, and **the encoded file** — a clip cut out of the
  middle is silence → sound → silence, and the same clip exported at 0 proves the
  delay is a delay and not a trim), `tests/audio_razor_check.py` (new, 17), plus
  captions / render-parity / keyframe / transition / video-clip / motion /
  effects suites all still green. `npm run build` clean.
- ⚠ **NOT opened in a browser.** This is a timeline-interaction change (three
  drag modes on a new clip shape) and a pane-wide layout change (a button on
  every row) — both need eyes. See Next Steps.

### 2026-08-17 — EQ, and PLAYBACK MOVED INTO A WEBAUDIO GRAPH

The one item of Phase 6 that was left out, and the refactor it needed. **The
refactor is the real change here** — the EQ is what forced it.

**Why an `<audio>` element wasn't enough any more.** It offers exactly two
controls, `volume` and `muted`, and `volume` is clamped to 1. That was tolerable
while a track had only a fader; it was already costing us one lie in the UI, and
an EQ cannot be applied to an element at all:

- a track set to 150% **previewed at 100% and exported at 150%**, and the
  Properties pane apologised for it in prose. That sentence is gone, because the
  limit is gone — `GainNode` has no cap.
- three bands need three filters, and `BiquadFilterNode` is the only place to
  put them.

**`client/src/animatic/audio_engine.js` (new)** wires each element as
`<audio> → low shelf → mid peak → high shelf → gain → destination` — the
exporter's chain, in the exporter's order. ⚠ **The element is still the master
clock.** A `MediaElementSource` plays its element rather than replacing it, so
`currentTime` is still read off the first playing track and nothing about the
transport's clock moved. Two traps are handled and commented: the context starts
**suspended** until a user gesture (so `playAt` resumes it — a suspended context
is silence with a moving playhead), and `createMediaElementSource` may be called
**once per element** and throws otherwise. **Every failure falls back to
`el.volume`**, which is exactly what the editor did before this file existed: a
browser that won't give us a graph should lose the EQ, not the sound.

**THREE FIXED BANDS, NOT PARAMETRIC, AND THAT IS THE DESIGN.** `EQ_BANDS` is a
twin table in `animatic.py` and `audio_mix.js` — low shelf 120 Hz, mid bell
1 kHz, high shelf 6 kHz — because each entry is one RBJ cookbook biquad, which
is one `BiquadFilterNode` **and** one ffmpeg filter. A parametric EQ would put a
frequency and a Q on the project, and every one of them is another number that
has to mean the same thing in two filter implementations. The tables are
compared band for band through node.

⚠ **`t=s:w=1` ON THE SHELVES IS LOAD-BEARING.** ffmpeg's `bass`/`treble` default
to `t=q:w=0.5`; WebAudio's shelves are cookbook shelves with a **slope** of 1.
Left at ffmpeg's defaults the two are different filters, and the editor would be
auditioning an EQ the export does not apply.

**Tone runs BEFORE the fader, and both before the fades** — in the graph and in
the preview's node order. A shelf has gain in it, so running it after the fader
would make the same boost eat a different amount of headroom at every volume,
and running it after a fade would ring on top of a ramp that is meant to reach
silence.

- **Verified** — `tests/audio_mix_check.py`, now 49 checks. The EQ half is
  measured **out of the encoded file with an FFT**, and every assertion is a
  PAIR: +9 dB of Low lifts 50 Hz by ×2.4 **and leaves 10 kHz within 1%**; −12 dB
  of High drops 10 kHz to ×0.26 **and leaves 50 Hz alone**. A plain gain, or the
  filter landing on the wrong band, fails. Two things worth knowing if you
  extend it: **a shelf gives half its dB at its own corner frequency**, so a
  6 kHz tone against the 6 kHz shelf reads as a broken filter (the tones are on
  the plateaus for that reason); and the tones are quiet, because +9 dB on a
  loud one clips the encoder and clipped audio steals energy from the other
  tone. `render_parity` / `keyframe_ops_check` / `animatic_motion_check` still
  green, `npm run build` clean, `import server.main` clean.
- **Also fixed: a flaky check I had just written.** "The duck is back afterwards"
  measured a window starting 0.6s after the voice stopped, while a 400ms release
  is still letting go — and ended on the last block of the file, where two
  exports built from different graphs disagree by a few milliseconds. It failed
  about one run in three. The duck section now uses an 8-second video and
  measures 5.2–6.5s: **0.999 → 0.463 → 1.000, three runs running.**
- **NOT browser-checked.** This is the entry with the most reason to be: the
  whole preview signal path changed. See the Next Steps item.

### 2026-08-17 — PHASE 6: AUDIO DEPTH — fades, ducking, beat markers

The sound was a level and a trim. It is now a mix: a track has a shape at each
end, a place under the voice, and the beats are on the timeline to cut against.

**Two new fields' worth of schema, and a third that had to be asked for.**
`AnimaticAudio` gains `fade_in_ms` / `fade_out_ms`, `duck_to` (a gain: 1.0 =
never, 0.3 ≈ −10 dB), and `role` + `duck_target`. Every one is optional and
defaults to today's behaviour, so an animatic saved yesterday mixes identically —
asserted, not assumed (`tests/audio_mix_check.py` §1).

⚠ **WHICH TRACK IS THE VOICE IS STATED, NEVER GUESSED.** `role: voice | music`
exists because "the other track" is wrong the first time someone lays two music
beds, and a mix that quietly ducks the wrong thing is harder to diagnose than one
that doesn't duck at all. `_duck_pairs` resolves `duck_target` first, then the
first `role: "voice"`, then gives up. A track cannot duck under itself, and a
track that is somebody's key is never itself ducked.

**A FADE IS PLACED AGAINST WHAT THE TRACK PLAYS, not against the file.**
`track_play_ms` / `fade_window` (animatic.py) ⇄ `trackPlayMs` / `fadeWindow`
(`client/src/animatic/audio_mix.js`) are **a twin pair like the scene model**,
and the parity is checked by running the JS half through **node** and comparing
window for window (§3). A fade out therefore lands on the end of the trim, or on
the end of the VIDEO when that comes first — a 4-minute bed under a 6-second cut
fades out at 6 seconds, where you can hear it. Two fades longer than the track
are scaled down together rather than crossing (the transition rule, again).

**The duck is a COMPRESSOR, and that is a deliberate trade.** `sidechaincompress`
keyed off an `asplit` of the voice. There is no ratio that means "exactly −10 dB"
for every take, so `duck_to` is aimed at a nominal speech level (`duck_ratio`)
and the compressor does the rest — further down on a shouted line, less on a
whisper, which is what a duck is for. Every chain in a ducked graph is pinned to
one `aformat`: a 44.1kHz bed keyed off a 48kHz voiceover is the ordinary case,
and sidechaincompress needs its two inputs to match.

⚠ **`amix=…:normalize=0` SURVIVED** the rewrite and must keep surviving — see the
2026-07-31 entry for what it costs when it doesn't. The whole audio graph moved
into `audio_graph(tracks, total_ms)`, which returns **None** when nothing needs a
filter at all, so one track at its recorded level still takes the plain
`-map 1:a:0` path it has always taken, byte for byte.

**The export payload's audio is a `model_dump` now.** It was a hand-written dict
of four fields — the exact shape that had already cost this project every
transition and every Ken Burns push (2026-08-17, Phase 4). `duration_ms` alone
would have been dropped, and a fade out with no idea where the track ends is a
fade that never happens.

**Beats: `client/src/animatic/beats.js`, and one decode for the whole editor.**
Energy-envelope onset detection — half-wave rectified rise, peak leading its
neighbours, enough over a local mean, 120ms minimum gap. No library, no server
round-trip, no FFT (everything a board is cut to has a transient on the beat).
The ticks are drawn under the waveform **and are snap targets**, which is the
point: "roughly on the beat" is exactly what you were trying to avoid.
The decode used to live inside `Waveform.jsx`; it is now cached by url in
`beats.js`, so a file is decoded **once** however many things look at it, and
`useAudioAnalysis` is the React end of that. Verified against a 120 BPM click
track through the real detector: 16 of 16 markers, worst error **5ms**.

**The preview mixes what the export mixes.** `useTimelineTransport` sets each
element's volume **every animation frame** (fader × fade × duck) rather than in
an effect — a fade that only moved when you edited something would be a ramp
nobody ever heard. ⚠ **The duck is the one place the preview is CLOSE rather than
exact**, and the Properties pane says so: it runs the same compressor law over
the decoded envelope, at that envelope's resolution, instead of ffmpeg's
sample-accurate sidechain. Everything else — where the ramps are, how deep, which
track ducks under which — is the same code on both sides.

**UI**: fade grips at both top corners of the audio clip (Premiere's gesture),
the ramp drawn as a wedge from `fadeWindow` so the shape on the clip is the shape
of the gain, beat ticks along the bottom, and four new rows in
`AudioProperties` — Fade in / Fade out under Timing, This track is / Duck under
voice (+ Ducks under, only when there are two voices) under Mix. All of it on the
existing `PropGroup` / `PropRow` / `NumField` / `an-select` / `an-vol` primitives;
the only new classes are `.tl-beat` and `.tl-fade*`. The grips are clamped inside
the clip because it has to clip its waveform — a grip centred on a fade of zero
would be half off one edge and entirely off the other, i.e. the handle you need
in order to make a fade would be the one you can't grab.

- **Verified** — `python tests/audio_mix_check.py`: 37 checks green. The graph is
  built, **encoded, and decoded back out of the MP4 and measured**: a fade climbs
  through its ramp, sits at half level half way up, leaves the middle of the file
  untouched (within 3% of a control export) and reaches silence at the end; the
  ducked mix runs **1.00 → 0.46 → 1.00** against the same project with the duck
  off. `render_parity` / `keyframe_ops_check` / `animatic_motion_check` /
  `captions_check` / `effects_check` still green, `npm run build` clean,
  `import server.main` clean.
- **NOT built, on purpose: EQ.** It is named in the Phase 6 line but was not in
  this phase's spec, and it is the one item here that cannot be previewed without
  routing playback through a WebAudio graph — which would put the master clock at
  risk for a filter nobody has asked for yet. Left as a Next Step.
- **NOT browser-checked** (standing instruction: Playwright on request only). The
  fade grips, the beat ticks and the two new Mix rows have not been looked at in
  a real editor; the maths behind all three is pinned by the test above.

### 2026-08-17 — AN OPEN `<select>` WAS UNREADABLE: white list, near-white text (user-reported)

- **Asked for:** two screenshots of the Look pane's "＋ Add an effect…" dropdown
  open — a white popup with the effect names all but invisible on it. Only the
  row under the blue system highlight could be read.
- **The cause is one line, and it is a general trap, not an effects bug.**
  `.an-row-ctl select.an-fx-add` is drawn `background: transparent` so it reads
  as a dashed invitation rather than a value control. **On Windows, Chromium
  paints a select's OPEN LIST using that select's own `background-color`** — so
  transparent meant the browser's white default, with the app's near-white
  option text on top of it. `color-scheme: dark` was already on `:root` and does
  not help here: it themes popups the browser draws NATIVELY, and this one is
  painted from the element's colours.
- **Fix:** an `option, optgroup` rule in `theme.css` giving both halves
  explicitly (`--panel-2` / `--text`). It is stated on the OPTIONS, not on the
  select, so the dashed opener keeps its look, and it covers every select in the
  app at once rather than this one. Both mechanisms are kept — the comment in
  `theme.css` says why neither is enough on its own.
- **Verified** in headless Chromium, both themes: options compute to
  `#1b1e2b`/`#f4f6fa` in dark and `#eceff5`/`#141824` in light, while the opener
  itself stays transparent. ⚠ The open popup cannot be screenshotted — it is an
  OS surface, not part of the page — so the computed colours are the check.

### 2026-08-17 — THE TIMELINE'S SCROLL BARS ARE PREMIERE'S: the ENDS zoom (user-requested)

- **Asked for:** a screenshot of Premiere's timeline beside ours — "i want this
  type of scroling baar ... lower and side too". Ours had the browser's own
  scrollbar along the bottom and nothing at all down the side.
- **Why it is not a cosmetic swap.** In Premiere the bar is not a scrollbar with
  a skin on it: the thumb's LENGTH is the zoom and its POSITION is the scroll, so
  dragging a grip frames a stretch of the edit in one gesture instead of picking
  a zoom level and then hunting for your place again. Copying the look without
  that behaviour would have been the wrong half of the feature.
- **What was built:**
  - `client/src/components/ZoomScrollbar.jsx` — one component for both axes
    (they differ only in which properties they read, collected in `AXIS`).
    Middle = pan, either grip = zoom with the opposite end pinned, empty track =
    page towards the click, double-click = zoom to fit.
  - `client/src/styles/animatic-scrollbar.css` — the bar, the thumb and the two
    round grips, in the panel's own colours, with its own thumb/grip tokens
    because `--btn-bg` resolves to `--panel-2` in dark, which is exactly what the
    bar's track is painted in — the thumb would have been invisible.
- **Four things in `Timeline.jsx` had to change with it, and each is load-bearing:**
  1. `.tl-scroll` scrolls on BOTH axes now, with the native scrollbars hidden
     (`scrollbar-width: none` + a zero-size `::-webkit-scrollbar`) — **hidden,
     not disabled**: the wheel, the trackpad and the Hand tool all still scroll
     that element. Turning the overflow off would take them with it.
  2. The ruler is `position: sticky; top: 0`, so the clock stays on screen while
     you work on the bottom track. It needed an opaque background for that, which
     is why `.tl-playhead` was pushed above it — its grip was behind the ruler.
  3. The gutter sits outside the scroller (so labels never slide away sideways),
     so it is now translated by hand from `readView` when the lanes scroll down.
     ⚠ **That translate is the only thing keeping a label beside its own track
     vertically** — the same alignment §5 of the e2e suite checks.
  4. The bars read `clientWidth/scrollWidth/scrollLeft` (and the same three
     vertically, less the pinned ruler) straight out of the DOM rather than
     computing them from props. Guessing how much fits on screen is how a
     scrollbar ends up claiming you can see more than you can.
- **Zoom became continuous.** `ZOOMS = [8,16,…,256]` and a `zoom` index are gone
  from `AnimaticEditor.jsx`; there is a `pxPerSec` state clamped to
  `MIN_PPS`..`MAX_PPS` instead. A grip asks for an exact scale — rounding it to
  the nearest power of two would make the gesture lie about what it is going to
  show. The ＋/− buttons and the Zoom tool (`Z`) still step, by `ZOOM_STEP`.
- **The vertical grips change TRACK HEIGHT** (`--tl-track-h`, 1.5–6rem, local to
  the timeline), because a lane's height is the only thing there is to zoom on
  that axis — the same thing Premiere's do. Both columns are measured against
  that variable, so they cannot drift apart; the waveform's canvas height is
  derived from it too, since a canvas cannot be sized in rem.
- **A grip drag reports FRACTIONS of the whole timeline, not pixels.** Fractions
  survive the zoom that is about to be applied (the timeline still spans the same
  amount of time), which is also what makes the drag absolute rather than
  incremental — so it cannot run away while the pointer is held still.
- `.an-timeline-body` no longer scrolls; it is a flex column that hands the
  timeline a bounded height. It used to scroll the ruler and the labels away.
- **Verified**, by mounting `Timeline` on its own in a throwaway Vite page and
  driving it with Playwright (the page was deleted afterwards; nothing of it is
  in the repo). Measured, not eyeballed: dragging the horizontal end grip took
  the content from 1104px to 2253px with the thumb pinned at the left edge;
  dragging the thumb panned to `scrollLeft` 487; the vertical end grip took the
  lane height from 42px to 66px; and with the lanes scrolled 60px down, all six
  gutter rows still had **exactly** the same `y` as their own lanes while the
  ruler stayed at the top of the scroller. No console errors. `npm run build`
  clean.
- **NOT opened in the real editor** — see Next Steps. The e2e suite's timeline
  selectors (`.tl-gutter-row`, `.tl-lane`, `.tl-bar`, `.tl-add-layer`) were all
  kept, but the suite has not been run this session.

### 2026-08-17 — THE PROPERTIES PANE IS NOW ONE PANE, not fifteen little forms (user-reported)

- **Asked for:** the user sent two screenshots of the Frame/video-clip Properties
  pane beside Premiere's **Effect Controls** and said the current one is
  "confusing" — wanted proper naming, proper grouping, consistent sizes and
  colours, and the same treatment when a new control is added later.
- **The actual fault, and it was structural, not cosmetic.** Every pane laid
  itself out by hand. A label was sometimes `.an-prop-label` (fixed 5.2rem) and
  sometimes the `<span>` inside `.an-tp-field` (as wide as its own text), so no
  two rows started at the same x. Number boxes were 4rem and dropdowns 9.5rem.
  Section headings were a `.an-prop-row.an-prop-head` with a rule over it, which
  cannot be collapsed, so a graded video clip was ~40 rows of unbroken form. And
  everything in it was tinted — labels, hints, values, all `--muted` or gold —
  so nothing stood out from anything else.
- **What was built:** `client/src/components/properties/PropGroup.jsx`, the four
  primitives every pane is now made of, plus `client/src/styles/properties.css`.
  - `PropGroup` — a named, **collapsible** section (Clip · Source · Speed ·
    Motion · Look · Effects · Footage). Open/closed is remembered in a
    module-level map keyed by group id, so collapsing "Look" stays collapsed
    when you click the next clip. Deliberately NOT persisted to the project: it
    is a view preference and must never reach an exported animatic.
  - `PropRow` — the two-column grid. **This is the whole fix**: column one is
    exactly `--an-label-w`, so every label in the pane starts at the same x and
    every value at the same x. Only a NAME may go in the left column.
  - `NumField` / `PropSlider` / `PropNote` — one size for every box, tabular
    figures, the ⏱ pushed to the right edge (it wraps under the value on a
    narrow pane instead of squeezing it).
- **⚠ TWO RULES ARE NOW WRITTEN DOWN IN `PropGroup.jsx`'s HEADER, and breaking
  either is the way this regresses:** one property = one `PropRow` (never two
  animatable properties on a row — each needs its own ⏱), and rows stay in
  `ANIMATABLE` order, because `Timeline.jsx`'s `renderKeys` draws one diamond row
  per animated property in that same order and the two panes are meant to read
  as the same list.
- **Naming, since that was half the complaint.** "Held for" → **Duration**;
  "Zoom" → **Scale** (the editor already has a Zoom — the timeline's, on `Z`);
  "X"/"Y" → **Position X / Position Y**; text's top/middle/bottom → **Zone**
  (it sat next to "Placement" and read as the same thing). `VideoClipProperties`'
  speed note was updated to say "Duration" with it.
- **Colour is now a rule, not a habit:** section name `--text` uppercase, property
  name `--muted`, value `--text`, `--primary` ONLY for on/active (a live ⏱, a
  chosen chip), `--warn` only for "probably not what you meant". Nothing in this
  pane is ever `--fail` — none of it can be an error.
- **Every pane was converted, not just the one in the screenshot** — Frame,
  VideoClip, Effects, Shape/Overlay, Text, Audio, Transition, Video — because a
  pane with sections beside one without is worse than either.
- Each effect in the chain is its own collapsible group with an `fx` prefix
  (Premiere's, and worth borrowing), keeping its ↑ ↓ ✕ on the header.
- **Files:** new `properties/PropGroup.jsx`, `styles/properties.css` (imported
  last in `styles/index.css` — that file's order IS the cascade); rewritten
  `properties/{Frame,VideoClip,Shape,Text,Audio,Transition,Video}Properties.jsx`
  and `components/EffectsPanel.jsx`; dead rules removed from
  `animatic-editor.css`, `animatic-effects.css`, `animatic-text.css`,
  `animatic-tools.css`, `keyframes.css` (`.an-prop-head`, `.an-prop-stack`,
  `.an-clip-short`, `.an-prop-vol`, `.an-vol-read`, `.an-fx`/`-head`/`-name`/
  `-unknown`, `.an-tp-unit`).
- **Verified:** `npm run build` clean. Every selector `tests/e2e_animatic.py`
  reaches into (`.an-tp-text`, `.an-vol`, `.an-props .an-mute`, `.an-prop-card`,
  `.an-prop-actions .danger-btn`, `.an-pane-props .an-colour`, and "Colour card"
  in the pane's text) was kept deliberately and still resolves.
- **NOT verified: the browser.** The suite was not run and the editor was not
  opened — this is a layout change, so someone has to look at it. See Next Steps.

### 2026-08-17 — PHASE 5: THE TEXT ENGINE. Bundled fonts, real type, in/out presets, auto-captions and TTS

- **Asked for:** Phase 5 of the editor roadmap — fonts, stroke/shadow, in/out
  animation presets, then auto-captions from a voiceover and dialogue → TTS,
  "both auto-timed from data we already hold". Built as specified, with one
  design decision the plan left open (`place`) and one pre-existing bug found on
  the way. Both are below.

- **⚠ THE FONT IS NOW A FILE THAT SHIPS WITH THE PROJECT, AND THAT IS THE WHOLE
  POINT OF THIS PHASE.** `_text_font` used to ask Pillow for `arial.ttf` and fall
  back to DejaVu and then to a bitmap face; the monitor asked CSS for whatever
  the OS called the nearest sans. On a Windows dev machine those look alike. On a
  Linux server they do not — so **the caption you positioned in the monitor was
  not the caption that landed in the MP4**: different width, different wrap,
  different number of lines. Same class of bug as the scene model being written
  twice, and it is fixed the same way:

  - six OFL fonts in `client/public/fonts/` (Inter, Anton, Bebas Neue, Playfair
    Display, Courier Prime, Caveat), with `OFL.txt` and a README naming each
    copyright holder;
  - **`animatic_fonts.py` ⇄ `client/src/animatic/fonts.js` are a TWIN PAIR**,
    element for element, and `tests/captions_check.py` fails if they drift;
  - the browser's `@font-face` rules are **generated from that list**, not
    written in a .css file — a third hand-maintained copy is exactly the
    `_SHAPE_POINTS`/`POINTS` failure this codebase already has one of;
  - the CSS family is namespaced (`AnimaticInter`, not `Inter`) so a user who
    has Inter installed still gets ours.

- **A caption's TYPE.** `font`, `stroke_px`, `stroke_color`, `shadow`,
  `letter_spacing`, all optional with defaults that reproduce exactly what a
  caption drew before — asserted by rendering an old-shaped payload and the same
  clip with the new defaults and comparing the two pictures byte for byte.
  **Every unit is chosen so the browser and Pillow use the same number:**
  `stroke_px` is pixels at 1080p and scales with the frame (`calc(100cqh * n /
  1080)` in CSS); `shadow` and `letter_spacing` are fractions of the font size,
  i.e. `em`, with no conversion at all. The shadow's blur is **zero on both
  sides** — a blurred one in the browser and a hard one in Pillow would be
  prettier and would be a preview that lies. Letter spacing draws glyph by glyph
  (Pillow has none), counting the trailing gap because CSS counts it too.

- **`place: "flow" | "free"` — the decision the plan left open.** The plan said
  "plus x/y in ANIMATABLE.text", but x/y alone would have broken the stacking
  that stops two subtitles landing on each other. So placement is an enum, not
  animatable (half way between two layout algorithms is not a picture):
  **"flow"** is the original behaviour and the default — dropped into its zone,
  stacking; **"free"** puts the block's centre at x/y like a shape. x/y are
  resolved either way but only USED in free, which is why `sceneSignature`
  appends them **only in free placement** — a project of ordinary subtitles signs
  byte-for-byte what it always did.

- **The presets are KEYFRAME MACROS and nothing else** (`text_presets.js`).
  Fade / Rise / Drop / Slide in write keys on `opacity`/`x`/`y` and get out of
  the way. Nothing is stored on the clip, neither renderer has heard of a
  "preset", and **the exporter needed no changes at all** — `animatic_render.py`
  already interpolates those three properties and `render_parity.py` already
  proves the two sides agree about them. The timeline shows the diamonds, every
  key can be dragged afterwards, and undo treats applying one as a single edit.
  A moving preset switches the clip to free placement, because in flow placement
  x/y are unused and the slide would animate nothing while the monitor showed it.
  The cost: a preset is write-only, so the picker cannot show a "current" one —
  which is honest, since after you drag one of its keys there isn't one.

- **AUTO-CAPTIONS (`captions.py`) — and the half that matters is free.**
  `transcribe()` is one Gemini call, audio in, timed lines out. `tidy_lines()` is
  pure: split long lines (time shared out **by character count**, because speech
  takes about as long as it is long), order them, **never overlap**, extend
  toward a readable minimum *only into the gap that is actually there*, cut
  anything past the end. They are separate functions because the timing rules are
  where every "the subtitles are on top of each other" bug lives and a failure
  there must not mean paying to listen to the track again. A generated caption is
  marked ONLY by an id prefix (`cap…`), never by a field: it has to be an
  ordinary caption in every other respect or half the inspector stops applying.

- **VOICEOVER (`tts.py`) — the trick is that nothing has to be typed or synced.**
  The board knows who says what in which shot; the timeline knows when that shot
  is on screen. So `_dialogue_lines` reads the dialogue off the source board,
  matched to the frames that reference each panel (**the first frame only**, or a
  shot with sixteen key poses would read its line sixteen times and be billed for
  each), and hands `synthesise_timed` a target time per line.
  **⚠ IT RETURNS THE TIMINGS THAT HAPPENED, NOT THE ONES ASKED FOR:** a line
  longer than its shot pushes the next one later rather than being spoken over
  the top of it. Those timings become the captions, so what is on screen matches
  what is heard even where the plan could not be honoured. Cutting the speech to
  fit would give a voiceover that is correct on the timeline and unlistenable.

- **⚠ AND IT IS THE ONE PLACE HERE THAT KNOWS HOW LONG A SOUND IS WITHOUT BEING
  TOLD.** There is still no ffprobe on this install, which is why every other
  audio duration comes from the browser. Generated speech is the exception
  because it arrives as raw PCM at a known rate: **the byte count IS the
  duration**, exactly, with no decoder. That is what makes the returned timings
  trustworthy enough to become captions.

- **Both new paths SPEND QUOTA, and follow the 2026-08-07 discipline exactly** —
  a free estimate shown before the button that spends (both endpoints of a pair
  take the SAME body, so the quote can only be the price of what the button
  does), a cap per run (`MAX_AUDIO_SECONDS`, `MAX_CHARACTERS`), and the job goes
  RUNNING so `save_animatic` 409s. **That last one matters more here than for
  Veo:** the SERVER writes the caption clips into `params`, so an autosave
  landing mid-run would put the editor's older `texts` back over work that was
  paid for. `speechRunning` is therefore part of `serverBusy`. They cost
  fractions of a cent, which is precisely why the discipline is kept — a cheap
  button is the one that gets pressed forty times.

- **What lands is ORDINARY**, the same rule the Veo path follows: a generated
  caption is an `AnimaticTextClip` and a voiceover is an ordinary audio track, so
  there is one code path downstream rather than two that can drift.

- **Verified:** `tests/captions_check.py` is new — the two font lists identical
  and every file on disk; stroke widens the glyph footprint and scales with the
  frame, shadow offsets it down-right *and not up-left*, tracking widens the
  block without making it taller, a different font is a different block; a
  pre-Phase-5 clip renders byte-identically; captions land within **±200ms** of
  where they were said and **never overlap**; voiceover lines never talk over
  each other and an overrunning one pushes the next later. `render_parity.py`
  gained a free-placed title sliding up on a curve, an unknown `place` folding
  down to flow, and the check that a keyframed caption position alone forces the
  per-frame planner. Full suite green (`render_parity`, `keyframe_ops_check`,
  `animatic_motion_check`, `captions_check`, `effects_check`, `transition_check`,
  `video_clip_check`, `animate_guard_check`); `npm run build` clean;
  `import server.main` clean; class audit clean (three new classes, all styled —
  the header Voiceover button deliberately reuses plain `btn small` rather than
  the `.an-add-text`/`.an-add-card` pair weight, because those two are free and
  it is not).

- **⚠ ONE BUG FOUND BY THE USER ON FIRST CLICK, AND IT IS WORTH GENERALISING:
  a modal's errors must be rendered INSIDE THE MODAL.** "See the price" wrote
  its failure with `setError`, which draws in the status bar at the top of the
  page — *behind* `.modal-overlay*. So the one thing that could explain the dead
  button was in the one place the user could not see it, and the button simply
  looked broken. Every dialog in this app that can fail needs its own error slot
  (`speechError` → `.an-prop-warn` inside the panel); the global banner is for
  things that happen with no dialog open. The Voiceover button is also disabled
  outright now when no clip on the timeline is a board panel, with the reason in
  its tooltip — an animatic built from uploaded stills has no dialogue to read
  and never will, so two clicks ending in "there is no dialogue" was a dead end
  that should never have been reachable.

- **⚠ NOT VERIFIED LIVE, and this is the honest gap:** **no real Gemini call has
  been made** — neither the transcription nor the TTS. Both are driven in the
  test by stubs, so what is proven is the timing arithmetic, the spend guards and
  the plumbing, NOT the request shape, the model ids
  (`gemini-2.5-flash-preview-tts`), the response parsing or the audio mime types.
  The first real run may need those adjusted. Nothing has been opened in a
  browser either, so the type in the monitor is matched to Pillow **by
  construction and by argument, not by looking** — which is the same standing gap
  Phase 1a and Phase 4 already carry.

### 2026-08-17 — PHASE 4: THE LOOK. Colour, LUT, masks, chroma, blend — and the monitor became a canvas

- **Asked for:** Phase 4 of the editor roadmap — *"where the DOM preview finally
  has to become a canvas. CSS cannot match Pillow on LUTs, feathered masks or
  chroma key."* It is built, with two deviations and three bugs found on the way;
  all five are below.

- **THE MODEL.** A clip's LOOK is three optional fields, on the two clip kinds
  that are PICTURES — a frame and an overlay:

  | Field | What it is |
  |-------|-----------|
  | `effects` | An ORDERED chain of `{id, kind, params}`. Order is the picture: a LUT after a saturation pull is not a saturation pull after a LUT. |
  | `mask` | ONE region, `{kind: none/rect/ellipse, x, y, w, h, feather, invert}`, in FRAME coordinates with `x`/`y` the centre — the same convention as a shape. |
  | `blend` | normal / multiply / screen / overlay / add / darken / lighten. |

  Effect kinds: `brightness`, `contrast`, `saturation`, `lut`, `chroma`. Defaults
  reproduce today's picture exactly, so every animatic already saved opens, plays
  and exports unchanged — asserted in `tests/effects_check.py` against an
  old-shaped payload rather than left to inspection.

- **WHERE EACH OF THE THREE APPLIES, because it is not the same place:** the
  effect chain runs on the LAYER'S OWN PIXELS before it is placed (so a
  letterboxed shot does not have its bars graded, and a chroma key sees what the
  camera recorded); the mask runs afterwards in FRAME coordinates (a vignette is
  a region of the film you are making, which is also the only reading under which
  one can be keyframed to sweep across a shot); the blend is between the finished
  layer and everything already under it.

- **⚠ AN EFFECT PARAMETER IS KEYFRAMED BY A FLAT TRACK NAME.** This is the one
  decision the rest of the phase hangs off. The track lives in the clip's own
  `keyframes` under `fx:<effect id>:<param>` (and `mask:<field>`), NOT nested
  inside the effect. `keyframes` therefore stays exactly the dict-of-lists it has
  always been, and **every keyframe operation, every timeline diamond row, the
  undo stack and the "typing a value sets a key" rule work on a graded clip with
  no changes at all.** Keyed by the effect's own `id` rather than its position,
  so re-ordering the chain carries each effect's animation with it.
  The one thing that needed care: `disableProp` returns `{[prop]: frozen}`, which
  for a look track would write a flat key the schema has no field for and Pydantic
  drops on the next save — switching the stopwatch off would look like it worked
  and quietly lose the value. `setLookValue` in `scene.js` is what puts it back
  where it lives, and every write of a look property goes through it.

- **THE MONITOR IS A CANVAS.** `ProgramCanvas.jsx` + `animatic/gl/` replace the
  `<img>` / `<video>` / colour-`<div>` stack. What moved and what didn't:

  - **canvas** — both pictures, the transition between them, the SHAPE FILLS, and
    the overlay pictures. Shapes had to move: the order is picture → shapes →
    overlays, and an overlay's blend mode needs every pixel beneath it, so a DOM
    shape would sit either in the wrong order or outside the backdrop the blend
    reads.
  - **DOM** — the captions, the shot label, and the selection outlines and resize
    handles. That was the trap the plan called out and the cheap answer it
    suggested: hit-testing and drag handles are most of what a canvas editor
    costs, and exactly the part WebGL adds nothing to.

  Blend modes are possible at all because of two framebuffers, ping-ponged: each
  layer copies the composite so far into the other buffer, then draws itself
  while SAMPLING that copy as its backdrop. Two draws per layer.

- **⚠ THE OLD "KNOWN LIMIT" ON TRANSITIONS IS GONE, and the EXPORT changed to
  close it.** `_transition_canvas` used to fit each picture onto the bar colour
  and blend the two results, while the preview composited against what was
  behind; the two agreed only while both pictures were fully opaque. It now
  composites the incoming picture OVER the outgoing one, exactly as the monitor
  does — byte-for-byte identical for two opaque pictures, and correct for the
  first time on a clip that is faded, keyed or masked mid-transition.

- **THREE BUGS FOUND WHILE WIRING IT UP — all pre-existing, all the same kind:
  the preview was lying and nothing was checking.**
  1. **EVERY TRANSITION WAS INERT IN THE MP4.** `server/animatics.py` built the
     export payload as a hand-written dict literal with no `id` on it, so
     `transition_window`'s `f.get("id") == after_id` never matched. The monitor
     blended; the video cut. The same dict had no `keyframes`, `scale`, `x`, `y`
     or `opacity` either, so **a Ken Burns push exported as a still.** Fixed by
     dumping the model (`f.model_dump(exclude={"url", "src"})`) — a dump cannot
     drift, and a field added to `AnimaticFrame` now arrives here on its own.
     **Do not rebuild that dict field by field again.**
  2. **A STATIC transform was dropped by the fast planner.** `plan_segments` —
     the one a project with no animation gets — produced no transform at all, so
     a stored `scale` of 1.5 exported at 1.0. `_static_transform` / `_look_of`
     are the fallback; the same hole would have swallowed every static grade.
  3. A redrawn panel would have kept its WebGL texture. The texture cache is
     keyed by URL, not by clip id, for exactly that reason, and is LRU-bounded
     at 12 so a sixty-panel animatic cannot hold every panel in VRAM.

- **DEVIATIONS FROM THE PLAN, both deliberate:**
  1. **NOT `ImageEnhance` for contrast.** `ImageEnhance.Contrast` blends toward
     the MEAN brightness of that particular image, which a fragment shader cannot
     know — the two sides would then differ on every picture by an amount that
     depends on the picture. All three colour operations are plain numpy with a
     fixed mid-grey pivot. (Brightness and saturation happen to agree with
     ImageEnhance anyway; saturation uses the same ITU-R 601 weights
     `convert("L")` does, which is asserted.)
  2. **The look is on FRAMES and OVERLAYS, not on "every clip".** A shape is
     vector and a caption is text; both are drawn above the finished composite
     and have no pixels of their own to grade. Giving them one means rasterising
     them into the chain, which is a different piece of work.

- **LUTs are FILES, read by both sides.** `luts/*.cube` (identity, warm, cool,
  noir, teal_orange — regenerate with `python luts/generate_luts.py`), served by
  `GET /animatics/luts` and `GET /animatics/luts/{name}`. Pillow reads them with
  `ImageFilter.Color3DLUT`; the browser fetches the same bytes into a tiled 2D
  texture. **One artefact, two readers — the opposite of the `_SHAPE_POINTS`
  mistake.** `identity.cube` is not decoration: it is the fixture that proves a
  LUT round-trips, and a red-only ramp in the test catches the classic .cube bug
  of reading the table with blue changing fastest instead of red.

- **Files.** New: `animatic_effects.py`, `luts/`, `client/src/animatic/gl/`
  (`compositor.js`, `cube.js`, `lut.js`, `shaders/effects.js`,
  `shaders/layer.js`), `client/src/components/ProgramCanvas.jsx`,
  `EffectsPanel.jsx`, `client/src/styles/animatic-effects.css`,
  `tests/effects_check.py`, `tests/effects_parity_check.py`. Changed:
  `animatic_render.py`, `client/src/animatic/scene.js`, `animatic.py`,
  `server/schemas.py`, `server/animatics.py`, `AnimaticEditor.jsx`,
  `properties/FrameProperties.jsx`, `properties/ShapeProperties.jsx`, `api.js`,
  `styles/animatic.css`, `animatic-text.css`, `animatic-lanes.css`,
  `styles/index.css`, `tests/render_parity.py`. `numpy` was already in
  requirements.txt, so that line of the plan needed nothing.

- **Verified:** `render_parity` (58 sampled times, the fixture now carrying a
  keyframed LUT, a keyframed mask, an effect with no id and an effect kind this
  build has never heard of), `keyframe_ops_check`, `animatic_motion_check`,
  `transition_check`, `effects_check`, `video_clip_check`, `animate_guard_check`
  — all pass. `cd client && npm run build` passes; `import server.main` passes.
  A real MP4 was exported end to end with a static LUT + mask on one clip, a
  keyframed brightness on the other, and a dissolve between them.
  **Class audit clean**: every new class (`.an-screen-gl`, `.an-gl-sources`,
  `.an-fx*`, `.an-tp-check`) has a rule, and the four the canvas made dead
  (`.an-screen img`, `.an-screen-b`, `.an-screen-card`, `.an-shape-fill`,
  `.an-overlay-img`) went with their JSX. 22 names remain unmatched — the same
  pre-existing set of template-literal prefixes and no-style hooks, one fewer
  than last session.

- **⚠ NOT VERIFIED: THE SHADERS HAVE NEVER BEEN COMPILED.**
  `tests/effects_parity_check.py` has two halves. The STATIC half — both sides
  know the same effect kinds, blend modes and mask kinds; every effect has a
  shader branch; the numbering is generated rather than typed twice — **passes**,
  and needs no GPU, so the most likely drift (adding an effect and forgetting its
  shader) is caught even on a machine with no GL. The PIXEL half needs
  `headless-gl`, which **would not build on this machine** (a native module;
  Windows needs the Visual Studio "Desktop development with C++" workload). It
  exits 2 and says so rather than passing. **So no GLSL written in this phase has
  ever been compiled, and the monitor has not been opened in a browser.** Run
  that test, or open the editor, before trusting the preview.

### 2026-08-16 — THE REFACTOR BREAK: two files split before Phase 4

- **Asked for:** the split this file has been asking for since Phase 2 — *"⏸
  Refactor break — do this before Phase 4. Not optional by then."*
  `AnimaticEditor.jsx` and `styles.css` had both grown past the size where a
  phase can be added to them safely, and Phase 4 (canvas/WebGL compositor)
  touches every part of both. **A pure move: no behaviour change, no new test.**

- **What moved, and nothing else did:**

  | New file | What it owns |
  |----------|--------------|
  | `client/src/animatic/useAnimaticProject.js` | Loading the document, the debounced autosave, and the BASELINE that decides "is this saved?". Also `frameForSave`. |
  | `client/src/animatic/useTimelineTransport.js` | The playhead: the rAF clock, shuttle (J/K/L), marks (I/O), seek/step/edit-points. Plus `useMonitorVideo`, a second export. |
  | `client/src/animatic/useUndoStack.js` | One stack for the whole document, the gesture bracket, and `reset()`. |
  | `client/src/animatic/util.js` | `clamp`, which four of these files were about to each declare. |
  | `client/src/components/properties/*.jsx` | The six panes, plus `VideoClipProperties` moved in beside them and an `index.js` barrel. |
  | `client/src/styles/*.css` (21 files) | `styles.css`, cut at its own banner comments. |

  `AnimaticEditor.jsx` **5151 → 3690 lines**; `styles.css` 9213 lines → 21 files,
  largest 1171.

- **⚠ TWO ORDERING CONSTRAINTS the split had to bend around, and they will bite
  anyone who rearranges these calls:**
  1. **`@import` order in `styles/index.css` IS the cascade.** Two rules of
     equal specificity are still decided by which comes last, so moving an
     import for tidiness can change the page without changing a declaration.
     Add a new file at the END or beside its block — never in the middle.
  2. **The transport owns the clock, and `scene` is derived FROM the clock**, so
     the scene cannot be an argument to `useTimelineTransport`. That is why
     slaving the monitor's `<video>` elements is a SEPARATE export
     (`useMonitorVideo`) called after the scene exists, and why `stepFrame`
     stayed in the editor — it needs `currentIndex`, which comes from the scene.
  3. `onLoaded` is **late-bound through a ref**: it runs inside the load promise
     and needs `reconcileVeoClips` and the undo stack, both declared further
     down the file. It is assigned beside `reconcileVeoClips`.

- **The one real bug the verification caught.** `useEffect(() => {
  framesRef.current = frames })` sat in the middle of the saving block and went
  out with it. Nothing would have failed loudly: the Veo poll reads
  `framesRef.current`, so a clip that finished after the load would have been
  reconciled against a stale frame list — i.e. a **paid render silently not
  attached**, which is the exact failure the 2026-08-16 entry above was written
  about. Found by comparing hook-call counts between the two bundles, not by the
  build and not by the browser.

- **How "pure move" was actually verified** (a build passing proves very little
  here — CSS never errors, and an undefined JSX identifier is a runtime fault):
  - **The emitted CSS is byte-identical.** The 21 slices rejoin into the old
    `styles.css` character for character, and the built bundle keeps the same
    content hash (`index-Rw-UMXOh.css`) before and after.
  - **Every literal in the JS bundle is unchanged** — 9026 literals, of which
    1101 prose strings (803 distinct), scanned sequentially out of the minified
    output before and after. Zero added, zero lost.
  - **Hook counts match**: `useEffect` 91→91, `useState` 313→313, `useMemo`
    14→14. `useCallback` 35→37 and `useRef` 62→65 are the new hooks' own
    (`applySnapshot`, `reset`, three late-binding refs) and are accounted for
    one by one. **This is the check that found the lost effect.**
  - **`no-undef` clean** across `client/src` (throwaway ESLint 9 flat config —
    not added to the repo). Only two hits, both pre-existing browser globals
    missing from the throwaway config (`URLSearchParams`, `getComputedStyle`).
  - **Class audit clean**: no JSX class lost a rule. The 23 unmatched names are
    the same pre-existing set (template-literal prefixes like `an-save-`, `k-`,
    `tool-`, and long-standing no-style hooks).
  - **The Python suites pass**: `render_parity`, `keyframe_ops_check`,
    `transition_check`, `video_clip_check`, `animate_guard_check`,
    `animatic_motion_check`.

- **The e2e suite gave the SAME result on the refactored tree as on a `HEAD`
  worktree** — run against both on the same API, the output diffs to **three
  lines**, all of them the count of animatics accumulated by running twice.
  Same passes, same failures, same numbers. That is the proof the move changed
  nothing a browser can see. The four failures it shared with `HEAD` were then
  fixed separately — see the entry below.

- **One thing found and deliberately NOT fixed**, because this was a pure move:
  `addColorCard()` in `AnimaticEditor.jsx` is dead — a complete, working
  function with no caller, so **there is no way to add a colour card from the
  UI at all**. Phase 3's, not the split's. Wiring it up means choosing where
  the control goes (the Media pane? the lane ＋ menu? the tools row?), which is
  a design decision and not a refactor.

### 2026-08-16 — the e2e suite was lying about being green, and one unstyled warning

Cleanup pass over what the refactor's verification turned up. **Three of the
four e2e failures were a stale TEST; one was the test misreading the app; none
was a bug in the app.** Worth stating plainly, because the tempting move here is
to "fix" the app until the assertions pass.

- **`tests/e2e_animatic.py` — the crash that hid everything else.** Section 5
  indexed a hardcoded `[".tl-bars", ".tl-texts", ".tl-audio"][i]` over
  `.tl-gutter-row`. The SHAPES lane (2026-08-02) made that four rows, so the
  run `IndexError`ed and **sections 6-14 had not run for several sessions** —
  the Media pane, the Properties panes, keyframes and all five viewports. A
  crash reads like a broken environment rather than a stale assertion, which is
  why it survived so long. Now walks `.tl-lane` / `.tl-gutter-row` in DOM order,
  which is what the lanes design says to do, and asserts one gutter row per lane.
- **"the icon-only Delete is square" — found nothing, for two releases.** It
  detected the button by `textContent.trim() === "🗑"`; Delete became an
  `<Icon name="trash"/>` (an SVG, so no text) and the check quietly matched
  nothing and passed `icon = None` into a truthiness test. Now detects
  icon-only as "has an `<svg>` and no text". **The button was square all along**
  — 37×37 — so this was never a layout bug, just a check that had stopped
  looking.
- **"one audio lane per track [2]" — the app was RIGHT.** §6 puts the WAV in
  through the Media pane's combined `image/*,audio/*` control and §9 adds it
  again through the audio picker: two uploads, two tracks, two lanes. That is
  the multi-track behaviour the 4-track cap exists for. The assertion now checks
  the RELATION (one lane and one gutter row per track) instead of a fixed 1.
- **"the track has its own volume control" — the test skipped the click.**
  `.an-vol` lives in `AudioProperties`, and the Properties pane follows the
  SELECTION. Asserting the slider was present without selecting a track was
  asserting the pane shows controls for something nobody picked — the exact
  thing `selectOnly` exists to prevent. Now clicks the lane first, then checks
  the slider AND the mute beside it.
- **A colour card could not be added AT ALL.** `addColorCard()` was a complete,
  working function with no caller, so the whole `kind: "color"` clip — built,
  exported and unit-tested by Phase 3 — was unreachable from the UI. It now has
  a **`＋ Colour card` button beside `Text` in the timeline header**, which is
  where it belongs: those two are the entire set of clips you can make WITHOUT a
  file, and the Media pane is deliberately one control for files only ("Add
  assets or drop them here"). Putting it there would have re-created the
  three-add-buttons mess that pane was cleaned up to remove. Not disabled on an
  empty animatic — a black slug is a perfectly ordinary first clip. Needed one
  new icon (`card`, a rounded rect with a solid block in it; a plain stroked
  rectangle beside the others reads as an empty frame, which is the opposite of
  what this clip is) and one CSS selector added to `.an-add-text`'s weight rule,
  since the two buttons are a pair and have to read as one.
  **Checked against the real thing, not assumed:** both buttons compute to the
  same border, background, colour, weight, shadow and height (30px). The gold
  outline in the first screenshot was `:hover` — the mouse was still parked on
  the button after the click.
- **`.an-clip-short` had no CSS rule at all.** `VideoClipProperties` renders it
  for "this clip wants 6.0s of footage but only 4.0s is selected, so its last
  2.0s will hold on one frozen frame" — the one warning that pane exists to
  give — and it was coming out as unstyled body text. Now styled as
  `.fv-banner.warn` already is: a panel with one `--warn` edge, which is how
  this app says "read this" everywhere else. Deliberately NOT red: holding the
  last frame is a legitimate freeze, not a failure.

**Verified:** `python tests/e2e_animatic.py` → **ALL PASSED**, now 15 sections
(new **§11b** covers the colour card end to end: it lands on the sequence,
Properties names it and offers its one control, and it removes cleanly). §11b
adds AND removes the card inside itself on purpose, so every duration assertion
after it still sees the same three frames. Five viewports, no console errors.
Class audit down to 22 unmatched names, all pre-existing template-literal
prefixes (`an-save-`, `k-`, `tool-`) and long-standing no-style hooks.
`npm run build` clean; the six Python suites still pass.

### 2026-08-16 — VIDEO CLIPS on the timeline, dropped OR generated (Phase 3)

- **Asked for:** Phase 3 — generalise `AnimaticFrame` into a clip that can be an
  image, a piece of video, or a colour card; then fold Veo rendering into the
  editor. The user's framing: *"I can also drag and drop video from my local and
  use Veo to generate video — I need both option."* Both are in.

**THE DECISION EVERYTHING ELSE FOLLOWS FROM: `duration_ms` is still the clip's
length ON THE TIMELINE, for every kind.** A video clip adds `in_ms` / `out_ms`
(the source window) and `speed` (how fast it is read through). Speed 2 covers
twice as much footage in the same stretch of timeline — **it does not re-time
the clip.** So `frame_spans` is untouched, and no later cut, caption or
transition moves when a speed changes. The alternative (speed re-times the clip)
is the same class of problem boundary-local transitions were designed to avoid.
Past `out_ms` a clip HOLDS its last source frame, the same rule keyframes follow
outside their first and last key — a clip stretched longer than its source shows
a freeze, never black.

**Pillow cannot decode video**, so a video clip is rendered by tearing the source
into numbered PNGs and turning "which frame is showing at t" into a lookup.

- **`video_frames.py` (new)** — `extract_frames()` via `ffmpeg -vf fps=`,
  `frame_path()`, and `probe_duration()`, which reads the length out of
  **ffmpeg's own banner** because **there is still no ffprobe** on an
  `imageio-ffmpeg` install. Extraction is **cached by CONTENT** (sha1 of the
  bytes + fps + source range), so a second export of an unchanged project decodes
  nothing. The range is in the key on purpose: two clips cut from different parts
  of one file are different extractions, and a cache that ignored that would
  serve the first one's stills for the second's timeline. A partial extraction
  (no `.done` marker) is thrown away rather than trusted. Reuses
  `animatic.ffmpeg_exe` / `run_ffmpeg` — third caller of the one ffmpeg
  integration, not a third implementation.
- **The scene model, both halves** — `clip_kind` / `clipKind`, `source_at` /
  `sourceAt`, and `_picture_at` / `pictureAt` now stamp `kind`, `color` and
  `source_ms` onto the resolved picture. `is_animated` returns **True for any
  video clip** (and any speed ≠ 1): getting that wrong in the False direction
  would export a video as ONE FROZEN FRAME held for its whole clip while the
  preview played it. `source_ms` is in `scene_signature` for exactly the reason
  `mix` is — without it every sampled moment signs the same and the render cache
  serves one still for the entire clip.
- **`animatic.py`** — a video segment's path is the extracted still for that
  instant; a colour card is painted rather than loaded.
- **`server/animatics.py`** — `POST /animatics/{id}/videos`, serving, and
  `_video_thumb`: a clip's thumbnail is the frame at its **in point**, so
  re-trimming changes the picture you see. Stills cache under the animatic's own
  folder so `delete_animatic`'s existing rmtree collects them — no separate GC to
  forget to run.
- **Client** — `VideoClipProperties.jsx` (new), a `<video>` in the monitor,
  video in the one-way-in `addAssets`, and `FrameStrip` gained a ▶ badge showing
  **how much footage runs inside the hold** (the number the duration field can't
  show, and the two differ exactly when speed ≠ 1).

**⚠ THE `<video>` IN THE MONITOR IS A SLAVE, NEVER THE CLOCK.** Audio is still
master. It is seeked on scrub and drift-corrected while playing, and it is
**muted** — the export mixes only the project's audio tracks, so an unmuted clip
would let you hear something the MP4 will not have.

**Two real bugs found on the way, both older than this phase:**
1. **Phase 1's motion never survived a reload.** The autosave and the dirty-check
   signature each wrote the saved frame shape out separately, and both carried
   only `id`/`src`/`duration_ms`/`label` — so a frame's `scale`, `x`, `y`,
   `opacity` and its whole `keyframes` track were computed, previewed, and then
   silently dropped on the way to the server. Now one `frameForSave()` feeds
   both, which is the only arrangement in which they cannot drift again.
2. **A colour card sat on its loading spinner for ever.** Every frame gets a
   `url` filled in on read, but a card has no file behind it, so that url can
   only 404. It is skipped rather than fetched, and the strip draws the colour.

**Verified:** `render_parity` (the fixture gained video-clip cases),
`keyframe_ops_check`, `animatic_motion_check`, `transition_check`,
`video_clip_check` (new — builds a real numbered MP4 with ffmpeg, exports it,
decodes the result and asserts frame N of the output is frame N of the source;
also trim, speed 2, speed 0.5, a colour card, all three kinds on one timeline,
cache hit measurably faster, and **an image-only project unchanged**),
`animate_guard_check` (new — every spend guard, via TestClient with the render
pool stubbed so it costs nothing: a promptless frame is not priced, an empty
request is refused, the cap 413s, records land in `result` and not `params`, a
save during a render 409s, **a save that wipes every frame leaves a paid clip
untouched**, a rendered frame drops out of the estimate unless forced, and
another account gets 404 on all three routes), `npm run build`,
`import server.main`. All green. **No browser test** (standing rule — ask first).

**AND THE OTHER HALF: ✨ ANIMATE WITH VEO, from inside the editor.** Select a
frame → write what MOVES → see the price → the clip lands on the same timeline.

**⚠ THE STRUCTURAL DECISION, and the reason this is worth having at all: a
generated clip lands as an ORDINARY VIDEO UPLOAD.** It is written to
`vid_{upload_id}.mp4` in the same media folder a dragged-in file goes to, and the
frame is then pointed at it. From that moment nothing downstream can tell the
two apart — trimming, speed, `in_ms`/`out_ms`, frame extraction, the export, the
monitor's `<video>` — all one code path. Building Veo output as its own kind of
clip would have meant a second one to keep in step, which is the mistake
`_SHAPE_POINTS`/`POINTS` already documents.

**⚠ RENDER STATE IS SERVER-OWNED, AND THIS IS NOT OPTIONAL.** `AnimaticVeoClip`
records live in the job's **`result`**, never in `params`. The editor autosaves
every ~900ms and rewrites `params.frames` wholesale from memory, so a render
recorded there would be rolled back by any save that started before it finished —
destroying the only record of something the user was **charged for**. This is the
`FinalVideoShot.include` vs `ShotStatus` lesson from 2026-08-07, one workflow
along. Two things enforce it, and both are asserted in the test:
- `/animate` puts the job in **RUNNING**, which `save_animatic` already 409s on,
  so for the whole life of a batch the server is the only writer to that job.
- Even after it ends, a save that empties every frame leaves `result.veo_clips`
  untouched — there is no field on `AnimaticSaveRequest` that could reach it.

The money discipline is the 2026-08-07 one, unchanged: `/animate/estimate` is
**free** and takes the **same body** as `/animate` (so the number quoted can only
be the price of what the button then does), the batch is capped at
`MAX_VIDEO_BATCH`, a promptless frame is refused rather than turned into a paid
failure, and a frame that already rendered needs `force` — surfaced as a
separately-worded "Render again", never a retry. `render_frame_clip` is the
animatic's own thin adapter over `video_client.render_shot`; `render_one_shot`
stays the final-video adapter. Two thin adapters over one renderer beats one
function that knows about two workflows.

**⭐ VEO HAS NOW BEEN CALLED FOR REAL — the first time in this project's life.**
The user rendered one frame (4s, 720p, $0.20) and it came back: a 727KB MP4,
1280×720, which `probe_duration` reads as 4010ms and `extract_frames` tears into
96 stills at 24fps with the frame mapping landing exactly where `source_at` says
it should. **The whole video-clip pipeline is confirmed against genuine Veo
output, not just synthetic `testsrc` files.** The 10ms overshoot against the
4000ms we recorded is harmless — `frame_path` clamps to the last still, which is
the same rule that protects a clip trimmed past its source.

**⚠ THE THIRD BUG, and it lost a clip that had been paid for.** The render
succeeded, the MP4 was on disk, the record said `ready` — and the editor sat on
"Animating…" for ever, because the polling effect keyed on the polled JOB
OBJECT, which the poll itself wrote. A Veo batch ends by putting the job back to
QUEUED, so: poll writes the job → the dependency changes → the effect re-runs →
its cleanup sets `alive = false` → the already-awaited fetch returns into a dead
closure → the clip is never attached, and the effect will not restart because
the status is no longer "running". **An effect must never depend on state its
own async body writes.** It now keys on a plain `animating` boolean that nothing
else touches, completion is decided by the RECORDS rather than the job status
(QUEUED is indistinguishable from idle), and the frame list is read through a
ref so attaching a clip cannot cancel the loop that is attaching it.
`reconcileVeoClips` also runs **on every load** and attaches any ready clip whose
frame isn't already video — so a render that finished while the editor was shut,
or during that bug, is recovered rather than silently paid for and lost.

**Still NOT done:** `FinalVideoWorkspace` has not been deleted. The editor can
now animate a shot, so the two overlap — but the workspace still owns the art
tray, per-shot reference images and the assemble step, none of which the editor
has. Collapsing them is a separate decision about what to keep.
Note that `tests/animate_guard_check.py` stubs the render pool on purpose: it
proves the spend guards and says nothing about Veo. What proves Veo is the real
render above, and it was a manual one.

### 2026-08-16 — TRANSITIONS, and they cost the timeline nothing (Phase 2)

- **Asked for:** Phase 2 of the editor roadmap. Delivered as **transitions
  alone** — dissolve, dip to black, wipe, slide.

- **SPEED WAS DROPPED FROM THE PHASE, deliberately.** Constant speed, reverse
  and freeze-frame are meaningless for a held still: a still at 2× is just a
  shorter hold, and the duration field already does that. They only become real
  once a clip has a source range, so they moved to Phase 3. Phase 2 is
  transitions, and is about a third of the work for it.

- **⚠ THE DESIGN DECISION, and everything else follows from it:
  BOUNDARY-LOCAL, not overlapping.** A dissolve needs two pictures at once, and
  there were two ways to pay:
  - *Overlapping (CapCut's)* — the transition eats `duration/2` from each side
    and the timeline gets SHORTER. That breaks `frameSpans`, every cut position,
    ripple and rolling trims, and every caption timed against a cut.
  - *Boundary-local (chosen)* — the blend happens over the TAIL of the outgoing
    picture and the HEAD of the incoming one, d/2 either side of the cut.
    **Nothing moves. Total length is unchanged.**
  So the roadmap's old warning ("transitions *shorten* the timeline, so
  re-verify ripple and rolling") **does not apply** — there was nothing
  downstream to re-verify, which is the whole point of choosing this. A held
  still has no spare frames to give up anyway; the pictures either side simply
  spend their last and first moments mixing. Asserted, not assumed:
  `transition_check.py` encodes the same project with and without a transition
  and fails if the MP4 lengths differ.

- **NEW: `client/src/animatic/transitions.js`, and its twin in
  `animatic_render.py`** (`transition_window` / `transition_at`). Third pair in
  the scene model, same rule as the other two: **change one, change the other,
  run `tests/render_parity.py`.** The parity fixture gained three transitions —
  a real one, one clamped from 5s down to a 900ms shot, and one naming a frame
  that doesn't exist — plus eleven new sample times across the blend.

- **⚠ DURING A TRANSITION, `scene.frame` IS THE OUTGOING PICTURE — for the whole
  window, including the half PAST the cut where `frameSpans` would say the
  incoming one is up.** This looks wrong and is the crux. It is what makes `mix`
  run 0 → 1 without doubling back, and `frame_b` mean "the picture arriving" —
  the only reading under which a **wipe or a slide has a direction**. With the
  pair flipping at the cut, a renderer would have to work out which of the two
  was incoming before it could draw an edge travelling the right way. Outside a
  window nothing changed: the half-open rule still puts a cut on one picture.

- **⚠ THE CLAMP IS LOAD-BEARING.** A transition is capped at the SHORTER of
  the two holds it joins, so each half-window is at most half of the shorter
  picture. Two transitions either side of one short frame can therefore **meet
  but never overlap**, which is what lets `transition_at` return a single
  answer instead of a list. Asserted by sweeping 10ms steps across a 400ms
  picture with a 10s transition on each side.

- **`mix` is in `scene_signature`.** Without it every video frame of a dissolve
  shares one render key, the exporter renders ONE still and reuses it for the
  whole blend, and the transition SNAPS — exactly the reuse bug the cache key
  already guards against for keyframes. Added only when there IS a second
  picture, so a project with no transitions signs byte-for-byte as before.

- **A transition costs the stills it uses, not the timeline.** `is_animated`
  returns True for any non-empty `transitions`, so the whole export moves to
  per-frame sampling — but samples outside the window resolve to an unchanging
  picture and collapse in the cache. A 600ms dissolve on a 2s project: 48
  samples → **17 stills** (15 blended + one per held picture). Put one on a cut
  of a two-minute animatic and it costs half a second of stills, not two
  minutes'. Asserted.

- **Four kinds, each defined so it is invisible at mix 0 and 1** — that is what
  lets one straddle a cut with nothing appearing to jump. `dip` is NOT a
  two-picture blend: the shot goes out through the bar colour and the next comes
  up from it, so only one picture is ever on screen, which is what makes it read
  as a beat rather than a cross-fade. `render_frame` grew a second picture;
  `_picture_layer` was split out of it so both sides get identical fit,
  background and rounding — two pictures placed by two slightly different
  calculations would shimmer along the edges of a dissolve.

- **UI, in the existing grammar.** A badge on the cut in the frames lane
  (`.tl-transition`, centred on the edit point because that is literally where
  it happens), drag its edge to change the length — ×2, since it grows from the
  centre — and a faint ＋ on every cut that hasn't got one. Sixth Properties
  state, built from `.an-prop-row` / `.an-tp-field` / `.opt-chip` like the other
  five. **⚠ `pointer-events` goes with the ＋'s opacity**: a transparent element
  still takes clicks, so without it there would be an invisible 14px button on
  every cut stealing presses aimed at the pictures either side.

- **⚠⚠ ONE DIAMOND ROW PER PROPERTY — THIS REVERSES PHASE 1's "ONE DIAMOND PER
  INSTANT" (user-reported, twice).** The two entries below still describe
  merging `scale` and `x` into a single diamond because "two diamonds a pixel
  apart would be unclickable". **That was the wrong trade and it is gone.**
  Merging hid the one thing you actually need off the timeline: *how many keys
  are here, and on what*. Two keys at one instant looked exactly like one, and a
  key near a cut vanished into the transition badge behind it. Now:
  - **A row per ANIMATED property**, in `ANIMATABLE` order — which is the order
    the Properties pane lists the same controls in, so the top row is the top
    control (Zoom, X, Y, Opacity on a frame). Only animated properties get a
    row, so an ordinary clip still draws nothing.
  - **A colour per property** (`--kf-*`), repeated as a diamond swatch on the
    matching ⏱ row (`.an-kf-swatch`). **That pairing IS the legend** — there is
    no other. Change a colour on one side only and it breaks silently, because
    neither side errors. Gold and amber are deliberately unused: the clips are
    gold, and a key the colour of its bar is invisible.
  - **A plain drag re-times ONE property** (the row you grabbed — `moveKey`);
    **shift-drag moves every property keyed at that instant** (`moveKeysAt`),
    which is how a Ken Burns push is still slid along without pulling `scale`
    and `x` apart. That capability is why `moveKeysAt` survives; `keyTimes` did
    not, because nothing asks for merged instants any more.
- **⚠ A CLIP BAR HAS THREE BANDS, AND THEY MUST NOT BE MERGED.** Keyframe rows
  own the **top** (`--tl-key-top/-row/-size`), the bar's own label sits in the
  **middle**, a transition badge owns the **bottom** (`.tl-transition`,
  `top: 66%`). Keys and badge were both at mid-height first, and the badge's
  hatching swallowed any key near the cut. Three things to preserve:
  - **The rows are in `rem`, like the track they sit in — never px.** Mix the
    two and the rows keep their size while the track shrinks with the browser's
    font size, until the bottom row lands in the transition band.
  - **A 45° square spans √2 times its width** across the corners, so a 0.3rem
    key needs 0.42rem of vertical room. This is why dragging now draws a RING
    (`box-shadow`) instead of enlarging the diamond: a bigger diamond would
    reach into the rows above and below it, and `.tl-bar` is `overflow: hidden`,
    so growing upward slices the tip off the key you are holding.
  - Two budgets bind, and the tighter is a SHAPE's six properties inside a clip
    inset 3px top and bottom — a fixed px inset against rem rows, so it is
    worst at SMALL root sizes. Checked arithmetically across a 12–24px root:
    the top row never clips, the four-row frame case clears the badge by ≥3.3px,
    and the six-row shape case fits by ≥0.5px.

- **The three ways a transition outlives its cut, all handled.** Deleting a
  frame prunes the transitions on it (and any left dangling off the new last
  frame). **Splitting a frame re-anchors it to the SECOND half** — the head
  keeps the source's id, so without that the transition would silently jump to
  the new cut in the middle of the split. Duplicating moves it to the copy, so
  it stays on the boundary with the next *different* picture. One naming a
  missing frame, or the last frame, is **inert rather than invalid**: skipped,
  kept, and working again if the sequence changes back.

- **Verified.** `tests/transition_check.py` (NEW) — 35 checks. The model half
  runs without ffmpeg; the encoded half builds real MP4s and measures decoded
  frames. Wipe and slide are asserted as **each other's opposite** half way
  through (wipe has changed the LEFT of the frame, slide the RIGHT), because a
  renderer drawing one where the other was asked for would pass any test that
  only checked "something moved". Parity (36 sample times), keyframe ops, motion
  and seven other offline suites pass; old-shaped payloads with no `transitions`
  key parse unchanged at every layer; class audit clean (9 new classes, all
  styled); `npm run build` clean.
  **NOT VIEWED IN A BROWSER** — same standing gap as Phase 1. The four CSS
  branches in `pictureStyle` are matched to `_transition_canvas` by reasoning
  about the geometry, not by looking at them. See Phase 1a, which now has a
  second thing to check.
  (`tests/profile_check.py` fails on this machine, before and after these
  changes alike — a leftover account in the local user store, unrelated.)

- **Known limit, written down rather than chased:** the monitor composites the
  two pictures against what is behind them, while the exporter fits each onto
  the bar colour and blends the results. They agree exactly unless a picture is
  ALSO being faded by its own keyframes mid-transition. Matching that properly
  needs the canvas compositor Phase 4 brings.
  **→ CLOSED 2026-08-17 by Phase 4**, and in the monitor's favour: the EXPORT
  changed to composite the incoming picture over the outgoing one too. See the
  note on `_transition_canvas`.

### 2026-08-16 — the two things Phase 1 left open

- **Asked for:** close the open issues recorded in the entry below — keyframes
  could be clicked but not dragged, and undo still coalesced on a bare timer.

- **DRAGGING A DIAMOND MOVES THE INSTANT, NOT A PROPERTY.**
  **⚠ SUPERSEDED — see the top entry.** The timeline now draws a row per
  property, so a plain drag moves that ONE property and shift-drag moves the
  instant. `moveKeysAt` still exists and is still what shift-drag calls; the
  rest of this bullet describes why it is one operation, which still holds.
  This is the whole
  design point and it needed a new operation rather than a loop at the call
  site. The timeline draws one diamond per key TIME (a Ken Burns push keys
  `scale` and `x` together, and two diamonds a pixel apart would be
  unclickable), so what is being grabbed is the instant — `moveKeysAt()` moves
  every property keyed there, and leaves properties that aren't keyed there
  alone. Moving only one of them would silently pull the push apart, and the
  picture would start zooming and panning at different moments for no visible
  reason. Asserted both ways in `keyframe_ops_check.py`.

- **Click vs drag is decided on pointerUP, not up front.** A press that doesn't
  move seeks to the key; one that does re-times it. That is what lets one
  diamond do both with no modifier, and it is why there is a 3px slop — a mouse
  always moves a pixel or two on the way to a click. `renderKeys` is ONE
  implementation shared by the frames lane and the clip lanes, same rule as
  `startClipDrag`: they are the same gesture and two copies would drift.
  Keys snap to cuts, the playhead and the marks, because snapping is done in
  timeline time and converted back — key times themselves stay relative to
  their clip.

- **Undo now groups per GESTURE, not per half second.** The old rule recorded
  one entry per 500ms burst, which is right for held keys and typing and wrong
  for a drag: pulling an opacity slider slowly for three seconds left six undo
  entries, so Ctrl+Z walked the value back in steps instead of putting it where
  it started. `setGesture(true/false)` brackets a drag and only its FIRST change
  is recorded. The timer rule still applies outside a gesture, where there is no
  pointer to bracket things.
  **⚠ The end of a gesture is caught on the WINDOW, not on the element.** A
  pointer released outside the control it started on never delivers a pointerup
  to that control, and a gesture that never closes swallows every later edit
  into one undo entry — far worse than the bug being fixed. `gestureProps`
  registers a one-shot `pointerup`/`pointercancel` on the window.
  Bracketed: the four opacity/volume sliders and the monitor's shape drag —
  i.e. everything that writes CONTINUOUSLY. Timeline drags already wrote once on
  pointerup (`dragRef.current.latest`), so they were always one entry and needed
  nothing.

- **Verified.** `keyframe_ops_check.py` is now 40 checks (8 new, all on
  `moveKeysAt`); parity, motion and the six existing offline suites pass; class
  audit clean; `npm run build` clean. **Still not browser-verified** — see
  Phase 1a, which is now the only thing outstanding on the editor.

### 2026-08-16 (later) — KEYFRAMES, end to end (Phase 1)

- **Asked for:** the keyframe UI, on top of the scene model built earlier the
  same day. The engine was already there and tested; this is the interface, plus
  the two gaps that only appear once you try to drive it.

- **Premiere's grammar, because it is the one every editor already knows.** Per
  animatable property: `⏱` to animate it, then `‹ ◆ ›` to step between keys and
  add or remove one here, then a curve picker. `KeyframeControls.jsx` renders
  it; `client/src/animatic/keyframes.js` holds every operation on the data.
  That split is deliberate — `scene.js` READS keyframes and is mirrored in
  Python, `keyframes.js` WRITES them and has no twin, because the server renders
  animations and never edits them.

- **THE RULE THAT MAKES IT AN ANIMATION TOOL: while a property is animated,
  setting a value writes a KEY at the playhead** instead of changing the value
  everywhere. Without it the stopwatch is a light that does nothing — you turn
  animation on and then have no way to say what the value should become. It
  lives in `writeAnimatable()` in `AnimaticEditor.jsx`, which every write goes
  through. Un-animated properties are written straight through exactly as
  before, so nothing changes for a clip nobody has keyframed.

- **⚠ Two traps in `writeAnimatable`, both found by using it:**
  1. **The preview's drag handles hand you the RESOLVED clip.** `sceneAt` gives
     the monitor clips whose `x` is where the shape is *right now*; the STORED
     clip is a different object and is the one that owns the keyframes. A drag
     must start from what you see and write to what is saved, so
     `startShapeDrag` now looks up both. Passing the resolved one to a `setKey`
     would bake the current on-screen position in as a new base value.
  2. **A drag captures the playhead ONCE.** Keys land where the drag began, not
     wherever the clock crept to by the time the pointer came up.
  Also: an explicit `keyframes` in a patch means the caller is managing the
  animation itself (that is how "Reset motion" works), so it bypasses the
  key-writing entirely — otherwise Reset would turn the values it is clearing
  into keys on the curves it is deleting.

- **⚠ THE PANE SHOWS THE RESOLVED VALUE, NOT THE STORED ONE.** Keyframe a zoom
  100% → 200%, park half way, and the stored `scale` is still 1 — so the field
  read "100%" while the picture plainly showed 150%. An inspector that disagrees
  with the monitor is worse than no inspector. `inspectedShown` resolves the
  selection at the playhead for display; editing still writes through the stored
  clip. `sceneAt`'s resolved clips can't be reused for this — they only exist
  while the playhead is *inside* the clip, and you can select a frame and then
  scrub elsewhere.

- **A frame gained Motion controls** (Zoom / X / Y / Opacity + "Reset motion"),
  which is where keyframes earn their keep: a Ken Burns push turns a held
  storyboard panel into a shot. Shapes and overlays can animate position, size,
  opacity and rotation.

- **TEXT GAINED `opacity`, because Phase 0 had promised it and not delivered.**
  `ANIMATABLE.text` listed `opacity`, but `AnimaticTextClip` had no such field
  and `draw_texts` couldn't render one — so the ⏱ on a caption would have
  animated nothing. Now: schema field, preview, and `draw_texts` scaling the
  alpha of the backdrop, the ink and the outline together. **It fades the PARTS,
  not the finished block** — a half-faded caption is a half-faded scrim with
  half-faded text over it. The two differ only where the text overlaps its own
  backdrop, and at the speed a caption fades, not visibly; doing it properly
  means an RGBA layer per clip, which is a lot of allocation for a difference
  nobody can see.

- **Timeline: ONE DIAMOND PER KEY INSTANT, not per property.**
  **⚠ SUPERSEDED — see the top entry.** Merging turned out to hide how many keys
  there are and on what, which is the main thing the timeline is read for; there
  is now a row and a colour per property, and `keyTimes()` has been removed.
  Everything below about seeking, hit-stealing and keys living ON the clip still
  holds. A Ken Burns push
  keyframes `scale` and `x` together, and two diamonds a pixel apart would be
  unclickable — `keyTimes()` merges them. Clicking one seeks to it, and stops
  the clip's move-drag so keys stay hittable. Keys are drawn ON the clip, which
  is also where they live: their times are relative to it, so dragging the clip
  carries them along for free.

- **Verified.** `tests/keyframe_ops_check.py` (NEW) — 32 checks through node,
  covering the ones a person notices instantly when they are wrong: the
  stopwatch ON must not change the picture, OFF must keep what is on screen
  rather than snapping back, adding a key must not move anything, re-typing a
  value must not silently reset a chosen curve, a key within tolerance is edited
  rather than duplicated, and dropping a key onto another leaves exactly one.
  `tests/animatic_motion_check.py` gained a real caption fade — decoded frames
  show the bottom strip darkening as the scrim comes up. Parity, motion and six
  existing suites all pass; class audit clean (7 new classes, all styled);
  `npm run build` clean.
  **STILL NOT VIEWED IN A BROWSER.** Everything above is reasoned and tested
  offline. The ⏱ row's layout inside `.an-prop-row`, whether the diamonds are
  big enough to hit, and whether the CSS transform on the monitor actually
  matches the exported push are all unexercised. That is the next thing to do,
  and `tests/e2e_animatic.py` is where it belongs.

### 2026-08-16 — ONE definition of "what the frame looks like at time t" (Phase 0)

- **Asked for:** an animatic editor in the class of CapCut / VN — keyframes,
  transitions, speed, filters, the lot. Researched both, audited what we have,
  and reported that **features were not the bottleneck**: the exporter and the
  preview had two separate, incompatible ideas of the picture, and both worked
  only because nothing in an animatic moved. This entry is the foundation pass.
  No new button; everything after it gets far cheaper.

- **THE PROBLEM, precisely.** `plan_segments` cuts the timeline wherever a clip
  appears or disappears and renders **one Pillow still per stretch**. That is a
  *step function*: it is cheap exactly because the picture between two cuts is
  constant. Meanwhile the preview picked visible clips out of three separate
  `useMemo`s. Every feature on the list — a keyframed zoom, a crossfade, a speed
  ramp — is *continuous*, and breaks both halves at once.

- **NEW: the scene model, and it is written TWICE.**
  `client/src/animatic/scene.js` (`sceneAt`) and `animatic_render.py`
  (`scene_at`) answer the same question — which clips are on screen at `t` and
  what has every animated property interpolated to — and return the same shape.
  The Program monitor renders one; the exporter renders the other.
  **⚠ Change one, change the other.**

- **`tests/render_parity.py` is what makes that safe, and it is not optional.**
  It evaluates a deliberately nasty fixture (keys out of order, a lone key, keys
  outside the clip, every easing curve, a shape fading up from 0, times landing
  exactly on cuts) through BOTH and fails on any difference. **It compares
  NUMBERS, not pixels** — canvas and Pillow will never produce identical bytes,
  so a pixel diff would fail forever and get switched off, whereas the resolved
  scene can be identical and is what actually drifts. It caught a real
  disagreement on its first run: JS prints `1` where Python prints `1.0`, so the
  two render-cache signatures differed for identical frames. Both sides now
  format numbers explicitly to 6 places.

- **Two planners; the project chooses.** `is_animated()` decides. Nothing
  keyframed → `plan_segments`, byte-for-byte the old cheap path, which is every
  animatic that exists today. Something keyframed → `plan_animated_segments`
  samples the scene once per video frame. That is less ruinous than it sounds:
  consecutive samples resolving to the same picture share a `signature` and
  render once, so a 3s push inside a 2min animatic costs 3s of stills, not
  2min's. `is_animated` deliberately **errs toward True** — being wrong the
  other way would silently drop every animation from the MP4 while the preview
  showed it, which is the one failure that would make the editor untrustworthy.
  Guarded by `MAX_RENDERED_STILLS` (20,000) with a message naming the number.

- **⚠ BUG FOUND AND FIXED ON THE WAY: the per-entry duration floor was `0.1s`.**
  At 24fps an animated segment is 1/24s, so every one would have been stretched
  to 100ms and a 2s animatic would have encoded as **4.8s**. The floor is now
  one video frame (`1/fps`), which is the shortest thing that can be displayed.
  It was already quietly wrong before this: `plan_segments` can emit a 40–99ms
  segment, and those were being padded to 100ms and lengthening the video.

- **A frame now has its own transform** — `scale` / `x` / `y` / `opacity`,
  defaulting to identity, keyframable. That is Ken Burns: the move that makes a
  held storyboard panel read as a shot rather than a slide. `place_picture()`
  does the fit, the zoom and the pan in **one** calculation — doing them in
  sequence rounds twice and drifts the picture a pixel per step. `x`/`y` are the
  picture's CENTRE, like every other geometry here, which is the only reading
  under which a zoom doesn't also shift the image.
  **A zoom is about the canvas centre**, so a subject in a corner is carried off
  the edge as the picture grows. That is correct, and it fooled this session's
  own test — see the fixture comment in `tests/animatic_motion_check.py`.

- **⚠ THE PREVIEW WAS NOT REWRITTEN AS A CANVAS, on purpose.** The plan said
  canvas; reading the code changed the call. Shapes and overlays in the Program
  monitor carry live drag-to-move and corner-resize handles, and a canvas means
  reimplementing hit-testing and handles for zero user-visible gain. The DOM
  preview now *consumes* `sceneAt` instead, which achieves the actual Phase 0
  goal — one definition of the frame — with none of the risk. Canvas moves to
  the colour/LUT/mask phase, where shaders are genuinely required.
- The preview gained a real fix from this: **the last picture is now HELD while
  a longer audio track plays out**, which the export has always done and the
  preview never did. It also no longer goes black when parked exactly on the
  end (a clip is alive up to but *not including* its end, so the monitor reads
  the last visible instant instead).

- **Schemas: additive, and old projects are untouched.** `AnimaticKeyframe`
  (`t`, `v`, `ease`), plus `keyframes: dict[str, list[…]]` on frames, texts,
  shapes and overlays, defaulting to `{}`. `t` is **relative to the clip's own
  start** — absolute times have to be rewritten on every drag and are wrong the
  moment one is interrupted. Outside the first and last key the value **holds**;
  extrapolating would fling a clip off screen the moment it is trimmed longer.
  `ease` is deliberately **not** constrained to the known set: both evaluators
  fall back to linear for a curve they don't recognise, so a project written by
  a newer client still opens rather than 422-ing the whole animatic over one
  word. Verified: an `AnimaticFrame` saved before any of this parses unchanged.

- **Verified.** `tests/render_parity.py` — 15 checks, both evaluators agree at
  all 28 sampled times. `tests/animatic_motion_check.py` (NEW) — encodes real
  MP4s and **decodes frames back out**: a keyframed push measurably grows the
  subject and changes continuously rather than in one jump, a fade ends darker
  than it started, and a project with no keyframes still renders one still per
  picture at exactly the old length. Six existing offline suites still pass
  (`grounding`, `panel_normalise`, `panel_border`, both `key_pose`s,
  `plan_export_columns`); `server.main` imports; `npm run build` clean.
  **NOT viewed in a browser** — the Program monitor's CSS transform is matched
  to `place_picture` by reasoning about the geometry, not by looking at it, and
  no keyframe UI exists yet to drive it. That is the first thing to check next.

### 2026-08-11 — Landing page now pitches ALL SIX workflows, not just turnarounds

Reported: *"Change landing Page of my all workflow i see it show only Text to
turnaround image / make beautifull page"* — the public page sold one workflow
(*"Turn a photo or a sentence into game-ready character assets"*), so a visitor
who hadn't signed in had no way to know Plan & Script, storyboards, key poses,
animatics or AI video existed. Same blind spot Home had before it grew
per-workflow groups.

`client/src/components/Landing.jsx` rewritten, `client/src/styles.css` LANDING
block extended:

- **Hero re-pitched to the pipeline** — *"From a sentence to a finished animated
  cut"* — plus a `.lp-flow` chip line (Plan → Characters → Storyboard → Key
  poses → Video) so the claim is concrete above the fold.
- **New "Every workflow in the studio" section** — six `.lp-wf-card`s, each with
  its icon tile, a Live dot, a real description and three capability tags. The
  cards MIRROR `Sidebar.jsx`'s `WORKFLOWS` — same labels, same icons, SAME ORDER
  — so the page matches the nav a visitor lands in. Deliberately unnumbered:
  sidebar order is the owner's choice and is not pipeline order, so the pipeline
  story is told separately in "How it works". **Renaming/adding a workflow in
  `Sidebar.jsx` means editing this list too** (the `Home.jsx` `groups` rule, now
  with a third copy).
- **"How it works" is now four beats** (Plan it / Draw it / Move it / Ship it)
  instead of the three Text-to-Image-only steps, and sits on a `.lp-section-alt`
  band so it separates the two grids. `.steps` went 3-col → 4-col with 2-col and
  1-col breakpoints.
- **Features** re-cut to what the studio actually does — cast/set continuity and
  "nothing spent by surprise" (video renders are estimated and capped) replaced
  two turnaround-only cards.
- **Hero art** is now `PipelineArt`: the existing 2×2 turnaround sheet plus a
  key-pose strip (same figure, arms stepping down) and a "Veo render · 4s" chip,
  i.e. three real outputs stacked. Reuses the old `.art-*` classes; all new
  classes are `lp-`-prefixed to avoid the `.wf-*` names Home already owns.

Verified: `npm run build` clean (79 modules). **Not** browser-tested — no
Playwright run, per the standing "only on request" rule.

### 2026-08-09 (latest+3) — NO DRAWN FRAME. The model was drawing the box, not just the picture

Reported (for the second time): *"look image draw frame not match each other, i
see many shot image frame issue … i decide remove frame in image so gemini start
draw full image … i not need frame line in storyboard panel image and key poses
image and when i regenerate"*.

**Root cause: we were asking for a "panel".** The prompt opened with *"A single
storyboard panel:"* and four style strings said "panel" too — and in comics /
storyboard training data a panel IS a bordered rectangle. So the model drew the
box as well as the picture: a sketchy frame just inside the edge with white paper
around it. Every one freehand, so no two match — different thickness, different
inset, different wobble — and a board of them reads as mismatched Polaroids.
**Measured: 138 of 371 real panels on disk carry one.**

Fixed on both sides, the way `GREYSCALE_STYLES` already handles the same class of
problem ("no amount of prompt wording fixes it reliably, so it is enforced in
code instead"):

- **Prompt.** The lead is now *"A single full-bleed storyboard IMAGE — the
  picture itself with NO border, NO frame and NO box drawn around it"*, the four
  style strings say "artwork" instead of "panel", and the closing rule is a much
  harder ABSOLUTELY NO BORDER paragraph. The old one-line version was buried
  mid-prompt and lost to the word "panel" above it.
- **`strip_drawn_border()` (storyboard_pipeline).** The reliable half. THE SIGNAL:
  a drawn frame line sits at a near-constant depth from its edge for the whole
  length of that edge, and picture content does not. Walk in from every column,
  record the depth of the first ink pixel — a border clusters those depths
  tightly, a picture scatters them. **Measured on the reported image: 7px of
  spread for the border, 319px for the picture on the same edge.** Gates against
  false positives: the line must run ≥95% of its side, sit within the outer 12%,
  have genuinely blank paper outside it (or a roofline against sky reads as a
  frame), and appear on at least THREE edges. Each edge is cut on its own.
- **Wired in ONE place** — the first step of `normalise_panel`, which panels, key
  poses and every redraw already call. That is what covers all three things the
  report asked for without three separate patches.

**Two things this shook out, both now fixed:**
- `normalise_panel`'s half-dozen early returns never honoured its own documented
  contract ("output keeps the SOURCE frame size, so a board stays uniform") —
  harmless while nothing before them changed the size, and immediately visible
  once a framed panel got 40% cropped. It now has ONE exit and one resize, so a
  cropped panel is scaled back up to the board's frame size. That is also
  literally the "use full image size" that was asked for.
- The cut was capped at the search band, which left the inner half of a thick
  frame drawn deep in that band still on the picture.

**Verified.** New `tests/panel_border_check.py`: synthetic frames at three
insets/thicknesses are removed with no fringe left on the rim; full-bleed art, a
blank page, an all-dark night panel and a dark band along ONE edge are all left
untouched; and it reports over every real board on disk (138/371 carry a frame,
worst case 44.6% of the file was margin+border, all within the safety rail).
`panel_normalise_check.py` still passes — it caught two contract breaks during
this work, which is exactly what it is for.

### 2026-08-09 (latest+2) — Refreshing ONE panel re-downloaded the WHOLE board

Reported: *"i change panel image 1/2 to 2/2 … but rest those panel image
flicker … i only regenerate this panel so why all image refresh"*.

**Root cause: `reloadBoard()` was doing the job of a single-panel refresh.** It
revokes every cached blob, clears `panelUrls` wholesale and re-reads the job —
which is *correct and necessary* for INSERT and DELETE, because those shift every
later index and a blob keyed by `/panel/2` then belongs to a different shot. It
was also wired to `PanelVersions.onSwitched`, so every ‹ › press blanked all N
tiles and re-downloaded all N pictures to change ONE.

- **`refreshPanelImage(index, url)`** — new, and now what a version switch and a
  single redraw both use. Touches exactly one cache key. Crucially it fetches the
  new bytes BEFORE changing anything on screen, so the tile goes old picture →
  new picture with no empty frame between. `fetchStoryboardPanel` already
  cache-busts its request, which is what makes this work for a version switch:
  the panel's URL is UNCHANGED there (the same `panel_NN.png`), only the pixels
  behind it differ.
- **`dropPanelImage(url)`** — forget one key, for the case where a redraw moved
  the panel to a different url. Called only after `setJob`, when nothing renders
  the old key any more.
- **`retryPanel` no longer blanks its own tile.** It used to revoke + delete the
  cache entry and let the fetch effect pick it up, so a redraw rendered at least
  once with no picture at all. It now primes the cache, then sets the job.
- **Retired blobs** (`retiredBlobs` ref) — a blob replaced by a fresher render
  can't be revoked at swap time, because the `<img>` is still showing it until
  React commits. They're parked and freed on unmount with the rest.
- **Race closed:** the first-load fetch effect now checks its slot still says
  `"loading"` before writing. Without it, a version switch during the initial
  load would be silently overwritten by the older in-flight fetch.
- `reloadBoard` keeps its two legitimate callers (insert / delete) and now says
  in a comment that it is not for refreshing one panel.

**Verified:** `npm run build` clean. Not browser-tested — the flicker is a
visual, and the user tests in the browser.

### 2026-08-09 (latest+1) — "I press Regenerate and nothing happens" — three stacked faults

Reported against the key-pose strip: *"i click generate again … i can't see any
changes"*, plus "when I regenerate, blur/hide the old image so I can see the new
one is being made", "the blank loading UI only shows the first time", and "keep
one button name — Regenerate".

Three genuinely separate bugs, each of which alone would produce that report:

1. **REGENERATE RESUMED INSTEAD OF REDRAWING — so it really did nothing.** Both
   the strip button and the dialog sent `resume=true`. On a finished 8/8 shot the
   server has nothing missing, hits `if not todo: return "already complete"`, and
   draws zero images. Measured, not guessed: `tests/key_pose_refresh_check.py`
   asserts the old path draws **0**. Regenerate now sends `resume=false`, which
   redraws the shot. **Resuming is the separate "▶ Draw the remaining N" button**
   — don't merge the two back together, they cost different amounts.
2. **A REDRAWN POSE KEPT ITS URL, so the new drawing could never be seen.**
   `PanelSequenceStrip` caches one object URL per path and never re-fetches a
   path it already holds — correct, and fatal when the path is stable across a
   redraw. The per-pose ↻ button therefore could not visibly work *at all*.
   `panel_sequence.frame_version()` (mtime_ns) is now stamped into the frame URLs
   as `?v=…`, the same trick the panel URLs use with `?v=<variant>`. A changed
   path is also what tells the client a redraw has LANDED — see below.
3. **NOTHING ON SCREEN CHANGED WHILE IT WORKED.** The shimmer only ever rendered
   for a slot with NO picture, so it showed on a first run and never on a
   regenerate. New `.is-redrawing` + `.redraw-veil`: the old picture blurs under
   a "Redrawing…" veil from the click until its replacement arrives. Blurred, not
   blanked, so the layout holds still and you can see WHICH pose is being redone.
   Poses un-blur **one at a time** as each new drawing lands, so a 16-pose run is
   watchable rather than a frozen strip followed by a jump.

**THE SAME FAULT (3) WAS IN TWO MORE PLACES, and both are fixed with the same
veil** — the user asked for it "anywhere it runs":
- `StoryboardBoard.jsx` — redrawing a panel that already had a picture showed
  that picture, unchanged, for the whole 30–60s synchronous redraw. Its spinner
  branches only ever covered FAILED and NEW panels.
- `JobDetail.jsx` — per-view and per-part regenerate in Text to Image had only a
  14px spinner inside the corner button.

**Naming:** "✨ Generate again" → **"✨ Regenerate"** (and the dialog's heading,
body and primary button follow). "Generate" is kept for a shot with no poses
yet, because that one is not a re-anything. The dialog now also states what it
costs and points at "Draw the remaining N" as the cheaper option.

**Verified.** `python tests/key_pose_refresh_check.py` — 11 checks: versions
change on the redrawn pose and only that pose; resume-on-complete draws nothing
and re-versions nothing; regenerate draws 7 of 8 (pose 1 is the copied panel) and
re-versions all 8 including pose 1. `tests/key_pose_scope_check.py` still passes;
`npm run build` clean. Not browser-tested — the user tests in the browser.

### 2026-08-09 (latest) — A SHOT MAY NOT OUTRUN ITS OWN DESCRIPTION, and pose 1 IS the panel

Traced against a real failing set the user sent: `TTBB_EP_One - shot 1 key
poses.zip`, eight poses of **shot 1**, "A wide shot establishes Kabir's cramped,
sunlit bedroom in a middle-class Indian home, showing a simple bed with a quilt"
— sitting immediately before **shot 2**, "A close-up shows Kabir lying fast
asleep under his quilt, his face peaceful."

- **Reported:** "first image totally different — always take the first image
  which shows in panel"; and "you see 8 image kabir awake up on bed, this is
  wrong because shot 2 already told kabir sleeping". Plus: make this production
  level so this class of bug stops recurring.
- **Both confirmed by looking at the zip.** `pose_001.png` is a different
  drawing from the panel on the board (figure turned away, different bedding).
  By `pose_008.png` Kabir is awake and sitting on the edge of the bed, feet on
  the floor — while the next panel still shows him asleep.

**FAULT 1 — pose 1 was GENERATED, so it was never the panel.** Every pose,
including the first, went through the image model with the panel as a
composition reference and `composition_purpose="repose"` — which says in as many
words that *"re-drawing the reference pose is a failed drawing"*. Pose 1 was
therefore **forbidden** from being the panel. No prompt fixes this: asking a
model to reproduce a picture exactly is the one thing it cannot promise.
**`run_panel_sequence` now COPIES the panel in as pose 1** (`normalise_panel`
only, no model call) and draws from pose 2 on. Exact, instant, free, and the
flipbook opens on the picture the user approved. `plan_beats` pins pose 1's line
to `OPENING_POSE` so a planner that ignores the rule can't mislabel the strip.
`PREVIEW_POSES` now counts **drawings**, so a preview still buys two real
pictures and gets the panel free in front of them.

**FAULT 2 — the pose planner was blind to the rest of the board.** `plan_beats`
got the shot's own sentence and nothing else, while the *panel* prompt has had
`build_flow_context` (what runs either side) since the CONTINUITY pass. An
establishing wide has no written action, and the planner was under a hard rule
that **every** pose must change the silhouette — so with nowhere legitimate to
put the motion it went and found some, and the only action available was the
next shot's. Three changes, all of which must stay:
1. **`story_context` reaches the planner** — `storyboard_pipeline.story_context_for`
   is now public (factored out of `_continuity_for_redraw`), the server passes
   `board_panels`, and `_flow_lines` states the next shot's description as *the
   wall this shot's action stops at*. Blunter than the image model's version of
   the same facts on purpose: the planner's failure mode is not drawing the
   neighbouring shots, it is **animating its way into them**.
2. **`hold` — the shot's INVARIANT.** The planner now also returns one sentence
   naming what is true in every drawing ("Kabir stays lying down, fast asleep
   under the quilt … he never wakes, never sits up, never leaves the bed"). It
   is stored with the pose plan, handed to **every** drawing, and appended to
   the repose prompt as the last word — *"this overrides everything above about
   movement"*. Needed because a pose line is a fragment: "his shoulder drops an
   inch" never mentions that the man is asleep. A redraw of one pose reuses the
   stored `hold`, or that one drawing is the only unfenced one in the shot.
3. **Rules that give a held shot somewhere to put the motion** —
   "A SHOT WHERE NOTHING HAPPENS IS STILL A SHOT" (breathing, the quilt sliding,
   a shoulder settling *is* the animation), "STAY INSIDE THE SHOT AS WRITTEN",
   and "MATCH THE MOVEMENT TO THE FRAMING" (a 15° head turn on a figure forty
   pixels tall is noise; a wide shot moves the whole body). The old
   head-and-shoulders demand — in both `_SYSTEM` and the `repose` branch of
   `gemini_client` — now follows **the part the pose names**, because it was
   right for a close-up and wrong for everything else.

**Verified.** `python tests/key_pose_scope_check.py` — 30 offline checks (pose 1
costs no image call and matches the panel pixel for pixel; the invariant reaches
every drawing and outranks the movement push; preview budget; a board-less
caller still works). `--live` spends one text call and re-plans the exact
reported shot; it now returns breathing, the quilt settling and the head easing
into the pillow, with `hold` = "he never wakes, never opens his eyes, never sits
up and never leaves the bed" — **no pose wakes him**. `tests/panel_normalise_check.py`
still passes. No images were generated for any of this.

**API:** `PanelSequenceInfo` gained `hold`; `generate_storyboard_panel` gained
`shot_invariant`; `plan_beats` now returns `(beats, hold)` — it had no other
callers.

### 2026-08-09 (later) — Key poses that don't move, and colour that won't stay put

Traced against a REAL failing set the user sent —
`output/_storyboards/284759b3ff034687a8bb5814b16cdcf5/seq/panel_01`, eight poses
of a reaction close-up. **Keep that folder: it is the known-bad reference set.**

- **Reported:** "there are no head movements, colours change, the image size is
  also cropped — we can't keep running our head only on this."
- **MEASURED FIRST, then fixed.** The head ink centroid across all eight poses
  sits within **3 pixels of 1365** — the head does not move at all. Only the
  eyebrows, eyes and mouth change. Mean frame brightness swings **37.9 grey
  levels** and total ink swings **3.4×** between poses.
- **ROOT CAUSE of the frozen head: two opposite instructions in one prompt, and
  the wrong one won.** `composition_reference_image` had a single fixed wording,
  written for RE-STYLING: *"Keep its composition… and the positions of
  everything in frame the same — ONLY re-render it in the new art style… Do not
  change the layout or what is happening."* The key-pose generator passes the
  source panel through that same parameter, so every pose request said "change
  the pose" and "do not change what is happening" in the same breath. The
  absolute one wins, and the model returns the same drawing re-shaded.
  **`composition_purpose` ("restyle" | "repose") now picks the wording**; the
  repose branch keeps camera/design/palette and demands the head, neck and
  shoulders sit in a visibly different position. `panel_sequence` passes
  `"repose"`; everything else defaults to `"restyle"` and is unchanged.
- **ROOT CAUSE of "colours change": the style prompt is a wish, and the model
  ignores it ~20% of the time.** On this rough-sketch board — whose style text
  says "greyscale only… absolutely no colour" — **panels 1 and 4 of 15 came back
  as full-colour illustrations**, and one pose in the eight-pose flipbook did
  too. No wording fixes this; the model either complies or it doesn't. So it is
  **enforced in code**: `GREYSCALE_STYLES` + `conform_to_style()` desaturate
  after generation (free, instant, cannot fail), and `conform_to_reference()`
  makes each key pose match its SOURCE PANEL's palette — the panel is the
  authority there, which also covers freeform "Add Your Own Style" boards.
  The anchor panel is conformed in memory before being used as a reference too,
  so a legacy coloured panel doesn't teach every pose the wrong medium.
- **The beat planner was writing expressions, not movement.** Asked to block out
  a reaction close-up it returned eight beats of "his brow furrows" / "his eyes
  narrow", which is not animation. `_SYSTEM` now requires every pose to change
  the SILHOUETTE, states that a face-only change is a failed key drawing, and
  says that in a close-up the head must move because nothing else is in frame.
- **NO automatic "did it move?" check — and this is deliberate, don't add one
  casually.** The obvious ink-threshold diff was written and thrown away: run
  against this known-bad set where the head provably does not move, it reported
  **75–100% "change" on every pair**, because re-shading pushes far more pixels
  over any fixed threshold (40% of a *static wooden crate* "changed"). A
  rank-based threshold just picks different structures instead. Separating
  re-shaded from re-posed is a vision problem, not a heuristic, and a motion
  gate that cries wolf would spend money retrying frames that were fine.
  Colour conformance IS reliably measurable, so that one is enforced. **Judge
  any future candidate metric against that folder before trusting it.**
- **The "cropped" report is NOT a file-size or aspect problem** — all eight
  poses and the source panel are 1365×768, full-bleed, content spanning the same
  region, `normalise_panel` a no-op (1365/768 is within 0.01 of 16:9), and a
  scale search against the panel returns 1.00 for every frame. It is the MODEL
  re-composing: pose 1 draws the man larger and the crates smaller than the
  others, so his torso runs out of the bottom of the frame sooner. Same cause as
  the frozen head — the panel was being re-interpreted instead of re-posed — so
  the `repose` wording ("keep the camera position and framing… the background
  and every object in it") is the fix to judge it by.
- **PREVIEW, so a failure costs 2 images instead of 40.** This was the user's
  actual ask — "we can't keep running our head only on this". `limit` on
  `run_panel_sequence` draws the first `PREVIEW_POSES` (2) and stops;
  `POST …/sequence` takes `preview: bool`; the dialog offers "👁 Preview 2
  first" alongside Generate, and the strip's continue button became "▶ Draw the
  remaining N". Continuing is the ORDINARY RESUME path, so nothing is redrawn —
  verified: preview buys 2, continue buys 14, a third run buys 0.
  **Two drawings, not one: movement can only be judged by comparison.**
- **A contact sheet is the fastest way to see what a strip is doing wrong.** The
  eight poses read as fine one at a time and the defects were obvious the moment
  they were tiled next to the panel. Worth building into the UI.
- **STOP and per-pose REDRAW on the key-pose strip** (user-asked, same session).
  While a sequence ran, every button on the strip was a disabled one — a run you
  could see going wrong at pose 2 had to be watched to the end. The strip now
  has its own **⏹ Stop** (the board's `POST /storyboards/{id}/stop`, mirroring
  `StoryboardBoard.handleStop`: same `danger-btn`, same latched "Stopping…"),
  and every drawn tile has a hover **↻** that redraws THAT pose only.
  - `run_panel_sequence(redraw=[…], beats=[…])`. `redraw` overrides
    resume/limit and draws exactly those poses; `beats` supplies the plan so
    **pose 7 is redrawn as the same pose 7** instead of whatever a fresh
    planning call invents. Verified: a one-pose redraw costs exactly 1 image,
    two poses cost 2, and the reused plan comes back identical.
  - The pose plan is now STORED (`sequences[i].poses`) and exposed on
    `PanelSequenceInfo`, which is what makes a faithful redraw possible — and
    it gives each thumbnail a tooltip saying what the drawing was meant to show,
    without which "is this one wrong?" is a hunch.
  - `worker._store_sequence` MERGES onto the existing entry rather than
    replacing it: a redraw run reports no plan of its own, and a wholesale
    write would erase the plan the sequence was built from.

### 2026-08-09 — A SHOT MUST OPEN AT THE START OF ITS OWN ACTION (user-reported)

The user's diagnosis, and it was the right one: the key poses were weak because
the **shot breakdown** was wrong, not the key-pose generator.

- **Reported:** shot 2 was "A simple Indian slipper (chappal) is seen mid-air,
  flying towards the camera." The slipper is ALREADY IN FLIGHT, so the flipbook
  built from that panel opens mid-throw and there is no throw to animate. What
  he wanted: the slipper **in the thrower's hand** first, then in the air, then
  the impact, then the reaction — "gemini divide shot like capture small small
  thing and main start pose each generate".
- **Why it matters mechanically:** every panel is later animated FORWARD from
  the moment it draws. A panel showing the middle or the end of a movement has
  nothing left to move. **This is the single highest-leverage rule in the whole
  pipeline** — no amount of key-pose prompt tuning can rescue a panel that opens
  on a follow-through.
- **`_SYSTEM_INSTRUCTION` now requires:** open on the instant BEFORE the
  movement (the wind-up, the hand still holding the object); split an action
  into preparation → action → impact → reaction, each its own shot; prefer more,
  smaller shots ("if your description needs 'then', 'as' or 'while', it is two
  shots"); a shot showing only a result with no cause and nobody reacting is a
  mistake. The thrown-slipper case is written into the prompt as the worked
  example, WRONG and RIGHT side by side.
- **Verified on the reported script.** Four sentences → **10 shots**, in exactly
  the order the user asked for: Kabir asleep → Madanlal in the doorway → his
  hand pulling off the chappal → **arm drawn back, chappal in hand, poised** →
  chappal in flight → impact on the cheek → eyes snap open → hand to the cheek →
  Madanlal pointing at Kabir → Madanlal mid-shout.
- **The second reported bug: "father told but kabir not view".** Shot 4's
  description was "…pointing an angry finger at Kabir's bed", so the artist drew
  Madanlal pointing at an EMPTY BED. The artist sees only that one sentence.
  The prompt now demands every person in frame be named AND given an action,
  explicitly including whoever is being spoken to, pointed at or reacted to, and
  states that **naming somebody's bed/chair/door does not put that person in the
  picture**. The re-run produced "Madanlal's arm is extended, his finger
  pointing directly at Kabir, who is now sitting up in bed."
- **`_add_characters_named_in_descriptions()` — the deterministic half.** The
  `characters` list drives reference images and the written bible, and the model
  still occasionally names someone in the sentence while leaving the list empty
  ("flying directly towards Kabir's face", characters: []). Such a person is
  drawn from nothing and comes back a stranger. The back-fill is deliberately
  CONSERVATIVE, because adding someone NOT in frame is the worse error: a bare
  mention counts, a possessive counts only for a BODY PART (`_BODY_PARTS`), so
  "Kabir's cheek" adds Kabir and **"Kabir's bedroom" does not**. Word-boundary
  matched, so "Ram" never matches "Rama". Unit-tested on all five cases.
- **`MAX_SHOTS` 60 → 120.** Beat-level splitting roughly doubles the count and
  `raw[:MAX_SHOTS]` truncates SILENTLY, which would have cut scripts off
  mid-story. Truncation now logs loudly that the end of the script is missing.
  Still a ceiling — a shot is an image.
- **Colour detector rebuilt: FRACTION of coloured pixels, not the mean.** The
  mean was too blunt for art whose colour is one bright accent — a shot built
  around a glowing blue object averaged ~3, indistinguishable from grey art, so
  the whole set read as "grey" and **pose 11, which came back with the accent
  missing entirely, went undetected**. By fraction the same data is unambiguous:
  greyscale 0.00–0.69%, grey-with-accent 1.5–4.4%, fully coloured 11.9–20.1%.
  Threshold 1.0%. Also added `lost_the_colour()` for the reverse failure —
  colour can be removed but not invented, so a grey pose under a coloured panel
  now gets one retry instead of being silently kept.

### 2026-08-09 — FOUR CONTINUITY FAULTS FOUND BY READING A REAL 18-PANEL BOARD

The user marked shots 1,2,3,4,5,7,9,10,12,13,14,18 good and named four faults.
Each is now a rule, and each rule states the failure it exists to prevent —
these are the exact mistakes to regression-test any prompt change against.

- **"Madanlal in the doorway panel missing before shot 3."** Even with "open at
  the start of the action", the board went Kabir asleep → wide of the room →
  SLIPPER ALREADY IN FLIGHT. The thrower never appeared. That rule is too
  abstract for the model to self-check, so it is now a testable one:
  **NOTHING MOVES ON ITS OWN** — before any shot where something is already
  moving there must be an earlier shot of the PERSON setting it moving, and the
  model is told to walk its finished list pointing at the cause of every moving
  thing. Re-run: doorway → hand pulling off the chappal → arm drawn back → in
  flight. Cause precedes motion, checked programmatically.
- **"shot 6 kabir look stand on bed but see shot 8 so he is sleeping."** Neither
  sentence said which, so the artist chose freely each time. **Posture must now
  be stated in every shot** ("lying on his back asleep under the quilt",
  "sitting up in bed") and **carries forward** — nobody is on their feet in one
  shot and asleep in the next without a shot showing them rise. Re-run: asleep →
  jolts → "now sitting up in bed" → still sitting.
- **"shot 11 background student missing not consistance same in shot 16."**
  Background people are continuity: a room with thirty students in the wide shot
  still has them in the close-up, and every shot of that scene must say so.
  Re-run: 5 of 6 classroom shots carry "other students"; the sixth is a
  close-up of a tablet screen, where none should be visible.
- **"shot 17 face and school banch marge face hide of banch."** A composition
  fault, so it belongs in the PANEL prompt, not the breakdown: nothing in the
  foreground may cross, cover or merge with a face; furniture goes below the
  chin; no cropped heads in a close-up. **A storyboard exists to show who is
  doing what** — a face swallowed by a bench communicates neither.
- **`_add_characters_named_in_descriptions` widened to look three words past a
  possessive.** It checked only the adjacent word, so "Kabir's **sleeping**
  form" and "Kabir's **left** hand" both slipped through and left the character
  unlisted. Three words covers the adjectives that occur while still, correctly,
  finding nothing in "Kabir's bedroom floor by the bed". Seven-case unit test.

### 2026-08-09 — A RESTART USED TO FREEZE A BOARD FOR EVER (user-reported)

Reported after restarting the server and reopening a board: "i cant see
regenarte buttun and i see nothing happen" — the toolbar showed "Stop
generation", the progress bar sat at 98%, and every button was inert.

- **Cause 1 (server): nothing ever closed out interrupted jobs.** Work runs in
  THIS process's thread pool, so a job still `RUNNING`/`QUEUED` at startup has
  no worker and never will. The record said "generating" for ever, and a board
  that believes it is busy hides every Regenerate button and offers only Stop —
  which does nothing, because Stop just sets a cancel flag that no worker is
  left to read. **Four animatics had been stuck QUEUED since 30 July** from the
  same cause; nobody had noticed because nothing surfaces it.
  **`_reap_orphaned_jobs()`** now runs at startup and closes them out. Marked
  SUCCEEDED, not FAILED, with `result` LEFT ALONE — the panels that were drawn
  are real and the user keeps them; `error` explains what happened and the
  normal Regenerate / "draw the remaining" buttons finish the job. Verified: it
  closed all four on the next boot, kept their 26 frames, and left the finished
  42-panel board untouched. Behind `API_REAP_ORPHANED_JOBS` (default on) —
  **turn it off if two API processes ever share a job store**, or one will reap
  the other's live work.
- **Cause 2 (client): `running = … || !status`.** "We don't know the status yet"
  was treated as "it is generating", so ANY board whose job could not be
  fetched — server restarting, dropped request, errored poll — rendered as a
  live run with Stop up and Regenerate hidden, and nothing could recover it.
  Unknown status now counts as running only while the FIRST fetch is genuinely
  in flight (`!job && !error`), which still stops the toolbar flashing on load
  but lets the buttons come back the moment a fetch fails.
- **Cause 3 (client): the poll set errors and never cleared them.** One slow
  request pinned "The server didn't respond within 120s" over a board that had
  recovered and was visibly drawing panels — and it sent this session hunting a
  server fault that had already fixed itself. A successful poll now clears it.
- **Lesson for any future long-running job:** the store is the only thing that
  outlives the process, so anything that reads `status` must assume the process
  that wrote it is gone. Terminal-state-on-boot is the cheap insurance.

### 2026-08-09 — THE IMAGE RATE LIMIT NOW FINDS ITSELF (quota storm, user-reported)

- **Reported** as "why this happen while generating storyboard images" — a red
  "server didn't respond within 120s" banner, and a panel reading "Couldn't draw
  this panel", while the board sat at 26 of 42.
- **The banner was a red herring** and partly self-inflicted: every `GET /jobs`
  in the log returned 200 and the board kept progressing throughout. It was a
  STALE error from a poll that died when the server was restarted mid-run (see
  the entry below — restarting while a board generates kills the run and hangs
  the client's in-flight request). The client sets that banner once and never
  clears it, so it stays on screen over a board that is working. **Worth fixing
  in the UI: clear the error as soon as a poll succeeds.**
- **The real fault: 90 × `429 RESOURCE_EXHAUSTED` in twenty minutes.** Each
  panel then sat through five backoff attempts (12s, 27s, 48s, 56s) before
  giving up; six panels failed outright and the board crawled.
- **Two causes, one of them ours.** (1) `IMAGE_RPM` defaulted to **60**, which
  was never a measured number and is far above what the Vertex image model
  grants. (2) The beat-splitting breakdown from earlier today roughly **tripled
  the panel count** — 42 panels where the old breakdown gave ~15 — so the same
  quota is hit three times as hard. Finer shots are the right call, but they
  changed the load profile and the limiter had to change with them.
- **Fix: the token bucket is now ADAPTIVE (AIMD, as TCP does it).** A quota
  rejection HALVES the rate (floor `MIN_RPM` 3); each success creeps it back up
  by 1 rpm toward the configured ceiling and never past it. A board therefore
  settles at whatever the project actually allows, within about a minute,
  **without anyone tuning an env var** — which matters because the right value
  differs by project, model, region and time of day, so any constant we ship is
  wrong for somebody. Defaults also lowered to `IMAGE_RPM=12`,
  `IMAGE_MAX_CONCURRENCY=2` as a safer starting point for the ramp.
- **The feedback lives in `_throttle()`, not at the call sites.** All four image
  entry points already funnel through that one context manager, so it classifies
  the exception and calls `note_quota_error()` / `note_success()` itself. Four
  copies would have meant one that got missed, and the missed one is the one
  still hammering the quota.
- Verified: 12 → 6 → 3 → 3 rpm on successive 429s (floors correctly), recovers
  to exactly 12 after 60 successes and never exceeds the ceiling; a real 429
  string is classified by `retry_policy.is_quota_error`; and `_throttle` moves
  the rate on both a raised quota error and a clean call.
- **NOT restarted to pick this up** — the user's board was still generating and
  restarting would have killed a third run. Needs a restart when the board is
  idle.

### 2026-08-09 — DEV ENV: uvicorn `--reload` wedges on Windows (cost a session)

Reported as "see not load fix it any workflow" — every workflow's library stuck
on skeletons, and the UI's own "server didn't respond within 120s" banner.

- **Not the database.** Mongo answered a direct ping in **0.6s** with all 30 jobs.
  Check that first; it is the tempting wrong answer.
- **The signature to recognise: TCP connects, HTTP never answers.**
  `Test-NetConnection 127.0.0.1 -Port 8000` succeeded while every request hung
  to the client's timeout. That combination means **a live port with a dead
  worker behind it** — never a routing, CORS or auth problem.
- **Cause:** the API was running `python -m uvicorn server.main:app --reload`.
  The reloader PARENT holds the listening socket and a CHILD serves. Editing
  source files (this session's own edits) respawned the child at 13:22; it
  imported the app fine — 140MB resident, 12 threads, idle at 6.47s CPU — but
  never took over the socket. The parent kept accepting connections into
  nothing. Generations were in flight (key poses, 12:59–13:05) while files were
  being saved, which is the collision.
- **Fix:** kill both PIDs and restart. **Do not run `--reload` while a board or
  a pose sequence is generating.** It is currently started WITHOUT `--reload`,
  so Python changes need a manual restart — `--reload` is fine to put back for
  pure UI work.
- Diagnosis order that works, next time: `curl /health` (1s answer or not) →
  `Get-NetTCPConnection` for the port → is the owning PID the reload PARENT
  (few threads, ~20MB) or a real worker? → only then look at Mongo.

### 2026-08-09 — CONTINUITY: the board is one film, not twelve pictures (user-reported)

- **Reported**, with screenshots of a rough-sketch board and its key-pose strips:
  "the storyboard is sometimes missing some frames… in animatics the character is
  getting changed… make it like a storyline how every frame should flow — think
  like a movie but made through flipbook and low cost."
- **Root cause of the changing character: the panel prompt never received the
  cast's DESCRIPTIONS, only their names.** `generate_storyboard_panel` got
  `characters: list[str]` and emitted "Characters present: Lead Thug." — which
  tells an image model nothing, so it invented a new man per panel. Character
  consistency rested entirely on reference IMAGES… and **`REFERENCE_FREE_STYLES`
  (Rough Sketch, the default) skips the cast step on purpose**, so the normal
  board had *zero* references and *zero* description. The breakdown had already
  written every character's look, for free, and threw it away.
- **The fix is words, not more image calls** (the "low cost" the user asked for).
  A written **continuity bible** — `{name: visual description}` for the cast and
  the locked props/sets — now rides in every panel prompt, scoped to the
  characters actually in that panel. `build_cast_context` / `build_set_context`
  in `gemini_client.py`. Sent for EVERY style, including the reference-free ones:
  skipping the cast step means skipping reference *images*, not forgetting who
  the characters are. **Zero extra API calls, zero extra wall clock.**
- **`resolve_name()` (gemini_client) — alias-tolerant name matching.** A shot
  saying "Lead Thug" against a cast entry called "Thug Leader" used to match
  nothing and silently drop both the description and the reference image. Exact
  normalised match, then most-shared-identifying-words; **a tie returns None on
  purpose** — with "Thug 1" and "Thug 2" both half-matching, guessing puts the
  wrong face in the panel, which is the bug it exists to prevent. `_gather_refs`
  uses it too, so reference images benefit from the same matching.
- **Panels are no longer drawn as independent pictures.** `run_storyboard` now
  renders in TWO WAVES: wave 1 draws the first shot of every scene (those become
  the scene's **look anchor**), wave 2 draws the rest with their scene's anchor
  attached as `scene_reference_image` — "same people, same room, same light,
  same style; DIFFERENT moment and camera, do not copy the composition". One
  fixed anchor per scene, **never chained panel→panel** — chaining compounds
  drift, exactly as `panel_sequence`'s docstring says about frames. Both waves
  stay concurrent, so the wall clock barely moves.
- **Every panel now knows where it sits in the film.** `story_context`
  {previous, next, scene, shot N of M} goes in as context the model must NOT
  draw. This is what makes shot 5 continue shot 4's action instead of being a
  fresh illustration of a sentence.
- **Holes get one automatic retry.** A refused panel used to just sit there as a
  gap — and "Make animatic" *silently drops gaps*, so a missing panel quietly
  became a missing BEAT. After both waves, each failed panel is redrawn once at
  a different seed (now with its scene anchor available). One retry, not a loop.
- **`regenerate_panel` was the easiest way to knock a panel off-model** — the one
  call that knew nothing about the rest of the board. It now takes `cast`,
  `assets` and `board_panels`, and picks the nearest already-drawn panel of the
  same scene as its anchor.
- **THE MISSING FRAMES BUG, exactly as reported.** `_sequence_info` counted key
  poses with `while os.path.isfile(frame_path(n)): n += 1` — **it stopped at the
  first hole.** One refused drawing in a 16-pose run hid the ten good drawings
  after it, reported the sequence as short, and made the next Generate *re-buy
  frames already on disk*. Now `panel_sequence.frames_on_disk()` checks every
  planned index; `PanelSequenceInfo` gained `missing` (the holes) and
  `frame_numbers` (which pose each url IS). Resume takes a `resume: bool` and
  fills **exactly the holes**, wherever they fall — verified offline: a hole at
  pose 3 of 8 leaves `frames: 7, missing: [3]` with poses 4–7 intact, the resume
  costs **one** image call, and a third run costs zero.
- **Key poses no longer cut the camera mid-shot.** `panel_sequence._SYSTEM` used
  to say "describe the pose and the CAMERA… where the camera is now", and the
  model took the invitation: half way through a close-up of a sleeping man it
  called for a wide of the bedroom and the image model drew it — visible in the
  user's screenshot as pose 5 of shot 1. **A CUT IS A NEW SHOT.** The camera is
  now nailed down for the whole sequence, and consecutive poses must be a
  quarter-second apart, not a new action.
- **"Make animatic" is a real flipbook now.** `_frames_from_board` used to lay
  down one still per shot and ignore the key poses entirely — the motion the user
  paid for never reached the animatic. A shot WITH a sequence now contributes
  each pose at 250ms (`1000/KEY_POSES_PER_SECOND`), so it plays for the length it
  was planned as; shots without one still fall back to a held panel. New frame
  source `kind: "pose"` (+ `frame`), referenced not copied, so redrawing a pose
  updates the animatic. Over `MAX_ANIMATIC_FRAMES` it degrades to panels-only
  rather than 413-ing.
- **The breakdown is told the shot list is a FILM.** `_SYSTEM_INSTRUCTION` gained
  the flow rules — each shot picks up where the last left off, nothing changes
  between shots of a scene except what the story changes, vary the framing
  between neighbours, keep screen direction — and descriptions must **name
  characters every time, never "he" or "the man"**, because the artist drawing
  that panel sees only that one sentence.
- **Tested offline** (stubbed image model, scratch scripts): wave order, anchor
  attachment, alias resolution, per-shot asset scoping, hole retry (5/5 panels
  recovered from one induced failure), and the three key-pose resume cases above.
  Client `vite build` passes; the FastAPI app imports with the new schemas.
  **Not yet run against the real image API — the prompt changes are the whole
  point of this entry and only a live board will show whether they hold.**

### 2026-08-09 — Every panel redraw is KEPT as a version (user-reported)

- **Reported:** "when i generate image shot so my older image hide" — redrawing
  a panel replaced it and the previous picture was gone. With an image model you
  frequently want the one before.
- **`panel_NN.png` still exists and is still THE picture** — that was the design
  constraint. Renders are archived to
  `versions/panel_NN/v000.png, v001.png, …` and the active one is COPIED over
  `panel_NN.png`. So the PDF, the ZIP, the key-pose generator and the animatic
  need no changes whatsoever: they read the current picture and never learn that
  versions exist. **Keep it that way** — resolving versions at read time would
  mean touching all four.
- `save_panel_version()` in `storyboard_pipeline.py` is the ONE writer; both
  `run_storyboard` (first draw = v0) and `regenerate_panel` go through it, so
  the archive can't be bypassed by one path.
- **Endpoints:** `GET /storyboards/{id}/panels/{i}/versions` (counted from DISK,
  so boards drawn before this feature work — they just start collecting from
  their next redraw), `GET …/versions/{n}` serves one, `POST …/versions/{n}`
  makes it current again. Nothing is ever deleted.
- **UI:** `PanelVersions` — a "‹ 2 / 3 ›" pill on the panel, hidden until a shot
  has two versions, so an untouched board looks exactly as before. Stepping
  WRAPS (same reasoning as the pose viewer) and **switches the panel**, not just
  a preview: "you can see the old one but can't have it back" would be worse
  than not showing it. It calls the board's `reloadBoard()` afterwards, because
  the bytes behind `panel_NN.png` change while its URL does not — without that
  the cached blob would keep showing the old picture.
- **The bug this shipped with, and the lesson.** First cut archived only NEW
  renders, so on a board drawn BEFORE the feature the first redraw overwrote the
  existing `panel_NN.png` and archived just the replacement — one version, no
  arrows, original gone. Reported immediately ("i generate new shot panel image
  but i not see my older image"). My test had drawn a panel from scratch and
  never covered a board that already had one. **`adopt_existing_as_version()`**
  now rescues a pre-versions picture as v0 before anything overwrites it; it is
  idempotent and also runs on the versions GET, so an old board archives its
  original the moment it is looked at. **When a feature changes how existing
  data is written, test against data created BEFORE the feature.**
- **Tested** (`smoke_versions.py`, scratch) on PIXELS, with each stubbed render
  a different flat colour. Case A — fresh panel: three redraws give three
  distinct versions, switching back restores the exact first picture,
  `GET /panel/0` serves it too, a later redraw appends without damaging v1.
  Case B — legacy board: reports 1 version on first look, 2 after one redraw,
  and version 1 is byte-for-byte the picture the user already had.

### 2026-08-09 — Lightbox controls were invisible on a white panel (user-reported)

- **Reported:** "arrow keys merger in with image" — the ✕ and the ‹ › arrows
  disappeared into the picture, and the fix was asked for **across all
  workflows**.
- **Cause:** they were `rgba(255,255,255,0.12)` on a thin gold border. Over a
  storyboard panel — near-white paper — a translucent WHITE fill is
  approximately nothing.
- **Fix:** one shared shell for `.lightbox-close` and `.lightbox-nav` — solid
  dark fill (`rgba(12,14,18,0.88)`), a full 2px gold ring, and a drop shadow
  plus a dark halo so the picture can't bleed into the button's edge. Reads on
  white paper and on a night exterior alike.
- **It is deliberately ONE rule for every lightbox in the app** — the board's
  panel viewer and the key-pose viewer share these classes. Don't add a
  per-workflow variant; fix it here and it is fixed everywhere, which is what
  was asked for.
- The disabled arrow dims via **colour, not `opacity`** — opacity would fade the
  dark fill too and put the button straight back into the white picture.
- **Verified by rendering over a pure-white panel**, the case that failed.

### 2026-08-09 — A silent server left every screen shimmering forever (user-reported)

- **Reported:** "why all workflow panel look like [ghost cards]" — the board
  library stuck on "Loading your storyboards…" with skeleton cards, no error.
- **Cause, and it was app-wide:** `fetch()` has **no timeout**. `fetchWithRetry`
  retried a *failed connection*, but a connection that is ACCEPTED and then goes
  quiet simply never settles — so `loading` stayed true, no `catch` ever ran,
  and the page shimmered until it was reloaded. Every request in the app had
  this. The usual way to trigger it here is `GET /storyboards` waiting on
  **MongoDB Atlas**, which this project already documents as intermittently
  unreachable from the owner's machine (SSL handshake) — and both
  `API_JOB_STORE` and `API_USER_STORE` are `mongo`.
- **Fixed in `api.js`:** an `AbortController` gives every request a 120s ceiling.
  Generous because two calls are legitimately slow (the script breakdown and a
  single-panel redraw are synchronous AI calls), but finite. A **timeout is not
  retried** — re-sending only waits another two minutes on the same wedged
  server — and its message survives `request()`'s catch instead of being
  flattened into the generic "can't reach the server", because "up but stuck"
  and "not running" need different fixes.
- **`StoryboardLibrary` also says something after 10s** rather than making the
  user wait out the full timeout wondering.
- **To diagnose it live:** `GET /health` reports MongoDB connectivity and flips
  `status` to `degraded` when it is down (`?check_db=false` skips the ping).

### 2026-08-09 — Image to Animatic Image: one panel → its KEY POSES (the flipbook)

The feature the workflow exists for. The owner is an animator and asked for it
in those terms: *"i know this in 24fps means 1 sec in 24 images … so gemini
culculate 4 sec = 96 image then separete 10/20 image shot scene required"*.

- **The arithmetic, which IS the feature.** Generate asks for a shot length
  (2/4/6/8/10s). The model is told the real budget — 4s × 24fps = **96 frames** —
  and asked for the **key drawings** that carry that motion, the poses an
  animator blocks out first. `KEY_POSES_PER_SECOND = 4`, so 2s=8, 4s=16, 6s=24,
  8s=32, 10s=40 (owner's choice; 4s=16 lands in the "10/20" he described).
  It is NOT a video and NOT all 96 frames. The dialog shows the sum, because
  that is what makes it make sense.
- **`panel_sequence.py`** — two calls, two backends: `plan_beats()` uses the
  TEXT model to split the shot into N ordered poses (JSON schema, falls back to
  even spacing if it can't be reached — a rough sequence beats no images), then
  `generate_frame()` draws each with the IMAGE model.
- **EVERY frame is anchored on the SOURCE PANEL** via
  `generate_storyboard_panel(composition_reference_image=…)`, never on the
  previous frame. Chaining frame→frame is the obvious idea and a trap: errors
  compound and by frame 12 the character has drifted into someone else in
  another room. One fixed anchor keeps staging, character and lighting still so
  only the pose moves. **Don't "improve" this into a chain.**
- **Endpoints:** `POST|GET|DELETE /storyboards/{id}/panels/{index}/sequence` and
  `GET …/frames/{n}`. The count is derived server-side from the duration, so the
  client can never order hundreds of images. Frames live in
  `_storyboards/{id}/seq/panel_NN/frame_NNN.png`, per panel, so regenerating one
  shot can't touch another's.
- **The board job carries it**, like a panel draw or a re-style — so the
  existing progress bar and `POST /storyboards/{id}/stop` work with no new
  plumbing, and the PANELS are never modified: a stopped or failed run leaves
  the board exactly as it was plus whatever frames it drew.
- **STOP → RESUME never pays twice.** `GET …/sequence` counts files on DISK
  rather than trusting the stored summary (a crashed run makes them disagree),
  and Generate resumes from that count. Verified: stopped at 5/16, resume issued
  **exactly 11** image calls. `DELETE` is the explicit "start over".
- **UI:** `sequenceMode` on `StoryboardBoard` — off by default, so Script to
  Storyboard's board is untouched. It stacks shots in ONE column (shot 2 below
  shot 1, as asked — a grid would squeeze the strip into a third of the page),
  relabels Regenerate → **Generate**, and hangs a `PanelSequenceStrip` under each
  shot.
- **The strip must SHOW THE WORK** (user-reported: "i click generate 16 image so
  i see nothing"). All N tiles appear as shimmering placeholders **the moment
  the button is pressed** and fill in one by one — the same skeleton treatment
  Text to Turnaround Image uses, deliberately, so "work is happening" looks the
  same everywhere. Two details make it work:
  - the tile count comes from the LOCAL request (`expected`, set before the
    `await`), not from server progress — otherwise the strip stays empty until
    the first poll, which is what looked broken;
  - `mine` is true if this strip started the run OR the worker reports this
    panel, covering the gap before the worker picks the job up.
  Polling is 2.5s while drawing, and `expected` is cleared when the board goes
  idle so a stopped run shows what it really has.
- **Download:** `GET /storyboards/{id}/panels/{index}/frames.zip` →
  `pose_001.png…` in play order, so an unzipped folder already flips correctly.
  The button appears per shot once it has poses and the run is finished.
- **Click a pose to open it full size**, and step through the set with the ‹ ›
  arrows or the arrow KEYS (Escape closes). The thumbnails are ~135px — enough
  to see that something moved, nowhere near enough to judge a drawing, which is
  what was reported. It reuses the board's own `.lightbox-*` shell so opening a
  pose feels like opening a panel; the arrows and the "Shot 1 · pose 5 / 16"
  counter are the additions. Pending placeholders aren't clickable.
- **Stepping WRAPS** (`(n + delta + frames) % frames`): 16/16 → 1/16, and back
  from 1/16 → 16/16. Neither arrow is ever disabled. These are the frames of a
  LOOP of motion, so flipping them round and round is how you judge whether the
  cycle reads — an end-stop at 16 just made the user close the viewer and
  reopen it (reported). The counter is what tells you where you are, since
  there is no longer an end to bump into.
- **Layout, after two rounds of user feedback:** shots sit **TWO per row**, then
  the next two below. One column was tried first (the original ask) and was
  wrong in practice — it wasted the right half of a wide screen and made each
  shot enormous beside its own strip. `auto-fill` is deliberately NOT used: it
  would drop to 1 or jump to 3 with width, and two is what was asked for. Grid
  `stretch` equalises the tile heights; `.board-column .board-tile` is a flex
  column and `.seq-strip` takes `margin-top: auto` so the strips across a row
  land on the same line whatever the shot descriptions do. Single file below
  1100px, where a half-width shot is too small to judge.
- **The header is trimmed to what this workflow can act on.** In `sequenceMode`:
  - **"You stopped this generation — N of M panels drawn" is hidden.** It
    reports on the PANEL draw, which happened in Script to Storyboard before
    this copy existed — stale news about someone else's run. Each shot's
    key-pose strip reports its own state.
  - **"Start over" is hidden** (it resets the script→shots flow, and there is
    nothing to restart on a board you opened), and **Download assets (ZIP) +
    Make animatic move into that spot** in the top row.
  - They are ONE render function (`finishActions`), placed either in the toolbar
    or the top row — not duplicated JSX, so the two placements can't drift.
    Wrapped in `.review-actions-right`, because `.review-actions` is
    `space-between` and two loose children would spread across the row.
  - `.top-actions .btn` now states one size family: an emoji label
    ("⬇ …", "🎬 …") grows the line box and sat 3px taller than "← Your Boards".
    Measured 40px for all three, same baseline. Same class of bug as the board
    toolbar's, which documents the identical cause.
- **"Add a style" and "Download PDF" are hidden in `sequenceMode`.** Re-styling
  would throw every key pose out of step with the panel it was drawn from
  (restyle in Script to Storyboard, then copy the board over), and a PDF is a
  document to hand someone — this workflow's output is images, so its downloads
  are the assets ZIP and the per-shot poses.
- **Tested** (`smoke_sequence.py`, scratch) with BOTH AI backends stubbed — real
  runs cost 16 image credits a press. Covers the arithmetic, anchoring (asserted
  on the composition-reference count), frame files and serving, stop mid-run,
  resume drawing only the missing frames, start-over clearing one shot only,
  bad-duration 400, and a cross-account 404.

### 2026-08-09 — Image to Animatic Image works on COPIES, never the original board

**Read this before touching either board workflow.** The requirement, in the
owner's words: *"when user create new From A storyboard and add/generate more
image so not update in real Script to Storyboard project — change should only in
Image to Animatic image."*

- **The old behaviour was wrong and would have corrupted work.** The library
  listed EVERY storyboard and opened the source board directly, so redrawing or
  restyling a panel there edited the Script to Storyboard board itself.
- **`POST /storyboards/{id}/copy?workflow=…`** (`server/main.py`) deep-copies a
  board: a new job record **and** `shutil.copytree` of its panel folder,
  variants included. The copy is a normal `STORYBOARD` job, so every existing
  endpoint, the animatic and the video workflows all keep working on it.
  - **Panel urls are re-pointed at the new id, keeping `?v=<variant>`.** Miss
    this and the copy serves the ORIGINAL's files — it looks correct while being
    a live link back into the board it is supposed to be independent of.
  - The **share token is deliberately dropped** (a copy is not published because
    its source was) and `copied_from` is recorded for information only —
    **nothing resolves through it**, or independence is lost again.
  - Copying a RUNNING or DRAFT board is refused: a half-drawn board is a moving
    target. If the file copy fails the new record is deleted, so a copy is never
    left without its panels.
- **`GET /storyboards?workflow=`** decides whose boards a library shows.
  Untagged = Script to Storyboard's originals; `animatic-image` = the copies;
  **`*` = everything**, which is what the downstream animatic/video pickers ask
  for — a board refined here is exactly what you then want to animate, and
  filtering it out would have made copies a dead end.
- `Home.jsx` fetches BOTH lists; its two board groups are different sets.
- **Tested** (`smoke_copy.py`, scratch) on files, not just status codes:
  copytree includes the `v1` variant, urls repoint and keep `?v=1`, the share
  token does not ride along; then **overwriting a copy's PNG, deleting one of
  its panels, renaming it, and deleting the copy outright all leave the source
  record and files untouched**; and each library sees only its own boards while
  `*` sees both.

### 2026-08-07 — Same "+ From a Storyboard" tile in both board workflows (user request)

- Image to AI Video's tile lost its 📝 for a **+** (it is the only way in now, so
  it should read as the create button every other library uses), and the same
  tile was added to **Image to Animatic Image**.
- `StoryboardLibrary` gained `newLabel` / `newHint` so the tile can be worded per
  workflow, and **`onNew` is now handed the fetched `boards`**. That last bit
  matters: the caller's picker gets the list the library ALREADY loaded, so the
  modal costs no second request and can never disagree with the cards beneath
  it. Defaults are unchanged, so Script to Storyboard still shows
  "New Storyboard" / "N storyboards created" with no props passed.
- **Verified by rendering:** both tiles measured 213×280 — identical.

### 2026-08-07 — Image to AI Video has ONE way in (user request)

- **"Create Video" (blank project) tile removed.** The library now offers only
  **From a Storyboard**, which is the route that arrives with the pictures AND
  the prompts already written — a blank project was a slower path to the same
  place. `createBlank` deleted with it.
- **`FinalVideoWorkspace`'s `isEmpty` / discard-on-back guard is now
  UNREACHABLE but deliberately KEPT** (commented as such). Every project now
  starts from a board, so it always has shots and a real title. The guard stays
  because the junk-library bug it prevents was user-reported once already —
  restore any blank-create path and the protection is already there.
- `POST /final-videos` still accepts a bare `title` (and `source_animatic_id`);
  only the UI route is gone. Nothing server-side was removed.

### 2026-08-07 — Sidebar rows are OUTLINED panels now (user-reported)

- **Reported:** *"workflow name panel merge in bg ui"* — the nav rows were
  `border: none` on `background: transparent`, so they dissolved into the
  sidebar and read as one dark block.
- **Fix:** each `.sb-item` is now a panel — `1px` stroke plus a `--panel-2`
  background. Its own token pair, **`--nav-stroke` / `--nav-stroke-hover`**,
  NOT `--border`: the rows sit on `--panel`, where `--border` is nearly
  invisible, and changing `--border` would have restyled every card and input in
  the app. Cool near-white in dark, cool dark-blue in light — defined in BOTH
  theme blocks, so light mode isn't an afterthought.
- The **active** row takes `--border-gold`, so "selected" is never confused with
  "hovered" (hover only brightens the stroke).
- `.sb-nav` gap went 0.15rem → 0.35rem: at the old spacing two outlined
  neighbours read as one doubled line.
- Row height is UNCHANGED (41px measured) — the padding sheds the 1px the border
  adds, so nothing below shifted.
- **Verified by rendering**, not by eye: screenshots in both themes, computed
  border/background/height read back from the DOM, and every label confirmed to
  fit at the real 264px sidebar width without truncating.

### 2026-08-07 — NEW WORKFLOW: Image to Animatic Image (promoted out of Image to Video)

- **Asked for:** open a storyboard and land on *"only Show Storyboard last
  page"* — the board screen Script to Storyboard ends on (restyle, redraw a
  panel, PDF, ZIP, Make animatic). First built as a tile inside Image to Video,
  then **promoted to a workflow of its own**.
- Shown as **"Image to Animatic Image"** (briefly "Create Animatic Image"). Its
  nav id stays `create-animatic-image` and its file stays
  `CreateAnimaticImage.jsx` — ids and filenames are internal keys; a rename
  changes the `label` only. Final rail order:
  Plan & Script · Text to Turnaround Image · Script to Storyboard ·
  **Image to Animatic Image** · **Image to AI Video** · Storyboard to Animatics.
  (The video workflow's label went Animatics to Final Video → Image to Video →
  Image to AI Video; its id is still `animatics-to-video`.)
- `client/src/components/CreateAnimaticImage.jsx` — a two-state shell (board
  library ⇄ board page) and **nothing else**. Both screens are the components
  the storyboard workflow already uses.
- **`StoryboardLibrary` is now shared by two workflows.** It gained optional
  `icon` / `title` / `subtitle`, and its **"New Storyboard" tile renders only
  when `onNew` is passed**, its **Duplicate button only when `onDuplicate` is**.
  Create Animatic Image passes neither, because creating a board belongs to
  Script to Storyboard and a second front door to it would be confusing.
  Defaults reproduce the old behaviour exactly, so Script to Storyboard is
  untouched. **Don't fork this component** — parameterise it.
- `StoryboardBoard` is likewise mounted, not copied; it already fetches its own
  job. It only had to export `styleLabelFor` so the style names can't drift.
  **There is ONE board page in the app.**
- `onOpenAnimatic` is threaded from `App.jsx`. Without it the board's "Make
  animatic" button hides itself (it is guarded), so the page would have been
  subtly *different* from the one reached via Script to Storyboard — exactly the
  near-miss this project keeps getting reported for.
- The board summary is held whole (not just its id) so style and aspect come
  from the list already fetched, with no second request.
- Image to Video is back to a **two-state** shell and its third tile is gone.
- Added to `Home.jsx`'s `groups` in the same position as the sidebar (see the
  note on that file — it is a second workflow list and does not read the first).
  It lists the same boards as Script to Storyboard on purpose: it is a second
  door to them, not a separate store.

### 2026-08-07 — Sidebar: "Image to Video" + Storyboard to Animatics moved up (user request)

- Renamed **Animatics to Final Video → Image to Video**, reordered the rail, and
  **removed Final Video Export**. It now reads, and this is the owner's chosen
  order — deliberately NOT pipeline order, don't "fix" it back:
  1. Plan & Script
  2. Text to Turnaround Image
  3. Script to Storyboard
  4. Image to Video
  5. Storyboard to Animatics
- With Final Video Export gone, **every sidebar entry is `live`**, so the `SOON`
  map, its `else if (SOON[nav])` branch and the `WorkflowSoon` import are all
  removed from `App.jsx`. `components/WorkflowSoon.jsx` is KEPT for the next
  roadmap item — but adding a `status: "soon"` entry now also means restoring
  that branch, or the item navigates to a blank page. Noted in both files.
- Also renamed **Text to Image → Text to Turnaround Image**. A workflow's name
  appears in FOUR places, not one — the sidebar, its `WorkflowHeader` title in
  `App.jsx`, its section label on `Home.jsx`, and the character-run picker in
  `FinalVideoArtStep.jsx`. All four were updated together; grep the label before
  calling a rename done.
- **`Home.jsx`'s `groups` array is a SECOND list of the workflows** and it does
  not read from `Sidebar.WORKFLOWS`. Image to Video was missing from "Recent
  work" for exactly that reason (user-reported). When a workflow is added,
  renamed, moved or removed in `Sidebar.jsx`, do the same in `Home.jsx`: its
  `id` must be the real nav key (the "View all" button passes it to
  `onNavigate`), its `label` must match, and its list needs a fetch in `load()`.
  Both files now say so.
- **The `id` did NOT change** — `animatics-to-video` and `text-to-image` are nav
  keys, not labels.
  Renaming it would strand anyone mid-session and break the hand-off deep links
  App.jsx sets (`setNav("animatics-to-video")` from the animatic editor). The
  same rule applies to the `AnimaticsToVideo` / `FinalVideo*` component and file
  names: they are internal. **Rename `label`, leave `id` alone.**
- The library's own title stays **"Your Final Videos"** — it names the artifacts,
  matching "Your Storyboards" / "Your Animatics", not the workflow.

### 2026-08-07 — Final-video tiles: storyboard only (user request)

- **Asked for:** drop "From an Animatic" (*"i not need animatic project only From
  a Storyboard project"*), rename "New Final Video" → **Create Video**, and add
  a third tile **Create Animatic Image**.
- Tiles are now: **Create Video** · **From a Storyboard** · **Create Animatic
  Image**. The animatic picker, its `animatics` state and the `listAnimatics()`
  fetch are gone from this library, and the two user-facing strings that still
  said "animatic" (the page blurb and the delete confirm) now say storyboard.
- **Create Animatic Image is a PLACEHOLDER on purpose.** The owner asked for the
  panel now and will specify the behaviour later; clicking it shows a dismissible
  line rather than doing nothing, which would read as a broken button. To wire
  it up, replace its `onClick` and subtitle — nothing else moves.
- **Deliberately NOT removed:** `source_animatic_id` on `POST /final-videos`, and
  the animatic editor's "🎞️ Make final video" button that uses it. The request
  was about the library's tiles; that button is a different entry point and
  removing it wasn't asked for. Say the word if it should go too.

### 2026-08-07 — "From a Storyboard" tile + shots arrive WITH their prompt (user request)

- **Asked for:** a third way in — *"add From A Storyboard panel … so storyboard
  image with prompt show in panel … i want in this page both image and prompt so
  user generate easily."*
- **The tile:** `FinalVideoLibrary` now has three New tiles (New / From an
  Animatic / From a Storyboard) and two pickers, switched by one
  `picking: "animatic" | "storyboard" | null` state so both modals can't open at
  once. The board picker offers only boards with a drawn cover — an undrawn
  panel has no picture to animate and could only produce a *paid* failure.
- **The real fix was on the server**, and it also repairs the ANIMATIC route the
  user was already using: their project showed *"24 without a prompt"*, because
  `_shots_from_animatic` copied the picture but not the text. An animatic frame
  usually points at a storyboard panel, and that panel still has its
  `description` — so the animatic dropped the words only because nobody looked
  them up. `_shots_from_animatic` now resolves the board (owner-checked, and
  **cached per board** so 26 frames are one fetch, not 26) and brings the
  description across as the starting prompt. `_starting_prompt()` is shared with
  `_shots_from_board`.
- **A description is not a motion prompt** — it says what the picture IS, and Veo
  wants what MOVES. It is deliberately offered as an editable first draft, which
  the textarea placeholder explains. Better than an empty box; not a finished
  prompt.
- **Tested** (`smoke_prompts.py`, scratch): a synthetic board → both routes.
  Straight from the board and via an animatic built from it, all shots come back
  with `image_url` AND a non-empty `prompt`, and the estimate then prices every
  shot instead of skipping prompt-less ones.

### 2026-08-07 — Veo model ids were WRONG for Vertex (user-reported: first real render 404'd)

- **Reported:** the first live render failed —
  `Publisher model .../veo-3.1-lite-generate-preview was not found or your
  project does not have access to it`.
- **Cause:** I used the **Gemini Developer API's** model names on **Vertex AI**.
  They are not the same:
  | tier | Vertex AI | Gemini Developer API |
  |---|---|---|
  | lite | `veo-3.1-lite-generate-001` | `veo-3.1-lite-generate-preview` |
  | fast | `veo-3.1-fast-generate-001` | `veo-3.1-fast-generate-preview` |
  | standard | `veo-3.1-generate-001` | `veo-3.1-generate-preview` |
  Google's 404 blames *project access*, which sends you hunting IAM for what is
  really a wrong string. **Don't copy an id between the two backends.**
- **Verified against the live project** with `client.models.list()` — it has
  `veo-2.0-generate-001`, `veo-3.0-{,fast-}generate-001`,
  `veo-3.1-{,fast-,lite-}generate-001` in `us-central1`. All three tiers now
  resolve to ids that exist, and `verify_access()` returns ok.
- **So it can't recur:** `video_client.available_models()` asks the backend what
  it has. `verify_access()` (which fills the workflow's setup banner) now checks
  the configured model against that list and **names the usable ids** if it
  isn't there — so a bad id is caught on page load, not after a two-minute
  render. The 404 branch of `_friendly()` does the same. Guessing model names is
  what caused this; the code now asks instead.
- The per-provider table lives in `_MODEL_IDS` in `video_client.py`. `.env.example`
  documents both naming schemes.

### 2026-08-07 — Empty final videos discarded on exit; the two New tiles line up (user-reported)

Both of these were **already-solved problems** that the new workflow failed to
inherit. If you add a workflow, walk the sibling workflow's fixes first.

1. **"New Final Video" created a permanent project even if you did nothing.**
   Exactly the junk-library bug fixed for animatics on 2026-07-31, not carried
   across — the user's library filled with "Untitled final video" rows and their
   folders. → `FinalVideoWorkspace` now has the same `isEmpty` test and
   `handleBack` discard as `AnimaticEditor`: no shots, no art, no cut, not
   running, still the placeholder name ⇒ deleted on the way out. A project
   started **From an Animatic** arrives with shots, so it is never empty by this
   test and is always kept.
   → `create_final_video` also **no longer pre-creates any folder**. Every writer
   (`upload_art`, `render_one_shot`, `_copy_animatic_uploads`,
   `assemble_final_video`) already does `makedirs(exist_ok=True)` when it has
   something to store, so pressing New and leaving now touches the disk zero
   times. Asserted in the API smoke test.
   → **Note:** this stops NEW junk. Projects created before this fix are still
   in the library and must be deleted from their cards.

2. **The two "New" tiles didn't line up** — the title and subtitle of the emoji
   tile sat ~3px lower than the "+" tile. `.lib-new-plus` is a **flex child**,
   whose `min-height` defaults to `auto`, so an emoji with a taller line box
   than `4.5rem` grew the square and pushed everything under it down. This is
   Responsive rule 3 (`min-height: 0` on every flex child) simply not applied.
   → Fixed on the shared `.lib-new-plus`, so **Your Storyboards** and **Your
   Animatics** get the same alignment fix.

### 2026-08-07 — Buttons: match the existing workflow, don't invent (user-reported, AGAIN)

- **Reported:** the render confirm dialog's Cancel and Render buttons were
  different sizes and didn't line up. Plus the standing instruction, said again:
  *"when you generate new button, panel, x cross, popup and arrange etc you first
  see my whole workflow and keep same to same."* This is the same class of thing
  as the 2026-07-31 top-bar report. **Read this section before adding any UI.**
- **Causes — four separate mistakes, all mine:**
  1. The modal used **`.lib-confirm-btns` + `btn small`**. That pair is the
     inline confirm strip INSIDE a library card, not a modal footer. The modal
     footer in this app is **`.an-name-actions`** with a full-size `btn ghost`
     Cancel and a full-size `btn primary` action + `<Icon>` — see the animatic
     Export dialog.
  2. **`.btn.primary` carries `margin-top: 1.1rem`** (it is designed to end a
     form). Dropped into a ROW it hangs lower than its neighbour and reads as a
     bigger, unrelated control. `.an-name-actions .btn.primary` zeroes it; my
     container didn't.
  3. **Invented classes that don't exist:** `btn tiny ghost` and
     `btn small danger` / `btn small ghost danger`. There is no `.btn.tiny` and
     no `.btn.danger` — the destructive class is **`danger-btn`**. They silently
     rendered full-size and un-red.
  4. **26 buttons were missing `type="button"`**, which the rest of the codebase
     sets on every non-submit button.
- **Fix:** modal footer switched to `.an-name-actions`; a commented
  BUTTON SIZING block in `styles.css` gives `.fv-top`, `.fv-section-actions`,
  `.fv-batch`, `.fv-final-actions` and `.fv-shot-actions` **one size family**
  (height 2.3rem, padding 0 0.95rem, font-size 0.84rem, `margin-top: 0`) —
  the same shape as the `.an-topbar .btn` fix; real classes everywhere;
  `type="button"` on all 41.
- **Also:** there is **no prettier config in this repo**. Running prettier with
  defaults adds trailing commas the codebase doesn't use. If you format, use
  `--trailing-comma none --print-width 80` (measured against existing files).
- **Not browser-verified** — the standing rule is to run Playwright only when
  asked. Checked by reading the computed rules and by build.

### 2026-08-07 — NEW WORKFLOW: Animatics to Final Video (Veo). Spends money.

**The workflow the sidebar has promised since day one.** Each animatic frame
becomes real footage via Veo, then the clips are cut together. Three steps, as
the placeholder screen advertised: *Apply final art & characters* → *Render
shots* → *Assemble the sequence*.

**First, the finding that shaped everything: THERE IS NO GOOGLE FLOW API.**
The user asked to send storyboards to Flow and get results back. Flow is a Google
*Labs web app* — no public API, no OAuth scope, no service account. Its credits
are a separate ledger from the API, and a Google AI Pro subscription grants no
API access at all. Scripting its UI with a session cookie would breach Google's
terms and break on every redesign. But Flow is only a front-end over **Veo**, and
everything it does for image→video (reference "ingredients", first/last-frame
interpolation, scene extension) is on the Gemini API and Vertex AI. So this calls
Veo directly. Told the user before building.

**MONEY is the design constraint, not a footnote.** Veo bills per second of
output: ~$0.24 for an 8s lite/720p clip, over $3 for standard/1080p with sound. A
20-shot project is 20 clips. So:
- No button renders anything directly. Every path goes through a confirm dialog
  that asks `POST /final-videos/{id}/estimate` FIRST and shows the number.
- A shot with no motion prompt is never submitted — it could only produce a *paid*
  failure. Its Render button is disabled and says why.
- A rendered shot is never silently re-rendered; "Re-render" is a separate action
  with different wording.
- Batch capped at `API_MAX_VIDEO_BATCH` (12) so a mis-click can't spend hundreds.
- Running spend (`~$x.xx`) is in the workspace header and on every library card,
  in the gold accent, so the number that can surprise you always looks the same.
- The estimate is *labelled* an estimate. List prices drift and only Google bills.

**New modules**
- `video_client.py` — the ONLY module that knows Veo exists. Dual backend
  (`VIDEO_PROVIDER=vertex|gemini`) mirroring `gemini_client.py` exactly, so the
  switch is already familiar. Long-running-operation submit→poll→download, both
  response shapes (Vertex inline bytes, Gemini file handle). `estimate_cost_usd`
  lives here because the rate table belongs with the model knowledge. Errors are
  translated for humans (`_friendly`) — quota, safety, credentials and the
  "your Pro subscription is not API access" case each get their own sentence.
- `video_assemble.py` — clips → one MP4. `cut` = stream copy (instant, lossless);
  `crossfade` = xfade/acrossfade re-encode. Spends nothing, so re-cutting is free
  and unlimited. Reuses `animatic.ffmpeg_exe` / `run_ffmpeg` / `AnimaticError`
  rather than growing a second ffmpeg integration.
- `retry_policy.py` — **extracted** the retry/backoff policy out of
  `gemini_client.py` so the video backend obeys the same tuned rules instead of a
  second copy that drifts. `gemini_client` now aliases to it; behaviour unchanged.
- `server/videos.py` — `/final-videos` router, 13 endpoints. Also exports
  `render_one_shot` / `update_shot`, which the worker calls.

**Wiring:** `JobKind.FINAL_VIDEO` (a project IS a job, per the Storage rule — no
new store). `worker.py` gains a **separate** `_video_executor`: a Veo call holds
its thread for minutes, and sharing the pipeline pool would starve every
storyboard behind one video project. Shots render *sequentially* within a batch —
Veo's concurrency quota is small and firing a board at once turns it into twenty
429s while still paying for the few that got through.

**Client:** `AnimaticsToVideo` (shell) → `FinalVideoLibrary` (mirrors
`AnimaticLibrary`, same `.lib-*` classes) → `FinalVideoWorkspace` + three step
components. The steps are **tabs, not a wizard**: the real loop is render a few,
look, fix a prompt, render again, and a one-way wizard would mean walking the
whole thing per retry. The animatic editor gains "🎞️ Make final video".

**The reuse that makes this pipeline worth having:** step 1 can pull references
straight from a **Text-to-Image character run** (`kind: "asset"` → that job's
`part_view.png`). The character is already drawn, so it doesn't have to be
described in words and re-guessed per shot. That is the thing Flow cannot do.

**Two real bugs found and fixed while testing — both worth remembering:**
1. **"Leave this shot out" was modelled as a `status`.** But render state is
   *server-owned* (so an autosave racing a finished render can't roll it back and
   lose a paid clip), which meant the toggle silently never persisted — and
   excluding a rendered shot would have erased the record of a render you paid
   for. Fixed with a separate, user-owned `FinalVideoShot.include: bool`.
   `ShotStatus` is now purely render state. **Keep these two apart.**
2. **`ffprobe` does not exist on this install.** `imageio-ffmpeg` ships the
   ffmpeg binary ONLY, so the assembler's duration probe returned 0 and fell back
   to a guess: a 6s cut reported itself as 12s, and — far worse — crossfade
   offsets are computed *from* those durations, so joins would have landed on
   black frames. Fixed by passing `durations_ms` from the router, which knows what
   it asked Veo for. ffprobe is now only a refinement. **Don't reintroduce a
   probe dependency.**

**Testing (both suites green; scripts were scratch, not committed):**
- API, via `TestClient` on a temp memory store: create → save → estimate →
  assemble-guard → batch cap. Verified the promptless shot is excluded from
  pricing, `include` survives the round-trip, a path-traversal shot id
  (`../../evil`) is refused 400, and **another account gets 404 and an empty
  library**.
- Assembler, against **real ffmpeg** with synthetic `testsrc` clips (Veo costs
  money; the assembler can't tell the difference): stream-copy 6000ms exact;
  mismatched clips correctly fall over to a re-encode instead of erroring;
  crossfade measured **4950ms against 5000ms predicted** — the offset arithmetic
  is right; silent cut; a missing clip is skipped not fatal; no-clips raises the
  user-facing message.
- `npm run build` clean; verified `fv-*` CSS and the workflow strings are in the
  bundle. **No browser test run** (per the standing rule — ask first).
- **Never called Veo.** Not once. See Current State.

**Config:** `VIDEO_PROVIDER`, `GOOGLE_CLOUD_VIDEO_LOCATION` (Veo needs a *region*
— `global`, which the image models require, does not serve it),
`VERTEX_VIDEO_MODEL` / `GEMINI_VIDEO_MODEL`, `VIDEO_MAX_CONCURRENCY`,
`VIDEO_POLL_*`, and the `API_MAX_VIDEO_*` spend guards. All in `.env.example`
with the pricing warning.

### 2026-08-04 — Plan & Script: pick the plan's LANGUAGE on Generate

- **Asked for:** "I want any user use my workflow, no language barrier." Clicking
  Generate now opens a picker — English / Hinglish / Hindi / any typed language —
  and the calendar is written in that language.
- **Hinglish had to be spelled out.** Left to itself the model writes Devanagari
  and calls it Hinglish. `LANGUAGES["hinglish"]` demands **LATIN (Roman) script,
  NOT Devanagari**, with a worked example. Verified live: it returned
  *"Shiv ji ka teesra netra kyu khula? Asli wajah jaan kar hairan reh jaoge!"* —
  which is what Indian creators actually publish. Hindi returned proper
  Devanagari.
- **`goal` and `effort` are explicitly EXEMPT** from translation: the app reads
  them as data (chip colours, `_coerce_items` validates them against English
  enums), so a translated "reach" would silently blank the field. Confirmed live
  that both stayed English while everything else translated.
- **The instruction is appended LAST**, after the plan brief — the most reliable
  position for a hard rule.
- **Unknown languages pass through verbatim**, so "Tamil", "Bhojpuri" or
  "Spanish" work with no code change; the picker's "Other" box feeds that path.
- The chosen language is stored on the plan, shown in the session header, and
  offered as the default next time — regenerating in another language is one
  click.
- **Verified:** 13 new checks (language reaches the agent, is stored, typed
  languages pass through, default is English, the Hinglish instruction demands
  Latin and rules out Devanagari, Hindi asks for Devanagari, goal/effort
  protected) plus live generations in both Hinglish and Hindi.
- **Also fixed while here:** the suite was making a REAL model call —
  `research_channel()` falls back to Gemini's `url_context` when there's no
  YouTube key, so `/plans/{id}/channel` hit the network. Now stubbed, so the
  suite is genuinely quota-free and doesn't depend on a third party's page being
  reachable. Any test touching that endpoint must stub `_read_with_gemini`.

### 2026-08-04 — Calendar cards: one fixed size, overflow scrolls inside

- **Reported:** the generated calendar's cards were all different heights (a
  four-beat outline towered over a three-beat one), so the grid looked broken.
- `.plan-grid` was `align-items: start` — the same trap already fixed twice, in
  the storyboard library and on the Home dashboard. Now `stretch`, PLUS a fixed
  `--plan-card-h: 400px`. Fixed rather than "as tall as the tallest in the row",
  because one long entry would otherwise stretch every card beside it into a
  wall of whitespace.
- **Slot, chips and title stay pinned; only the detail scrolls** (new
  `.plan-item-body`), so a scrolled card is still identifiable at a glance.
- Measured against the user's real 36-item plan: content per card runs 361-611
  characters (median 448), so 400px fits the median comfortably and only the
  longest few scroll.
- **THIRD time `align-items: start` has caused this.** It is now called out in
  the UI rule section — check for it before adding any new card grid.

### 2026-08-04 — Plan & Script: the agent asks in CLICKABLE questions

- **Asked for (with reference screenshots):** when the agent needs to know
  something, show a panel above the chat with pickable options, so the user
  clicks instead of typing a paragraph.
- **One call returns both.** `plan_agent.chat()` now answers
  `{reply, questions[]}` through `response_schema` — the prose reply AND the
  clickable questions in the same turn. A second "now generate some options"
  call would double the cost of every turn and let the two drift apart (a reply
  asking one thing, buttons offering another).
- **Bounded on purpose:** `MAX_QUESTIONS=3`, `MAX_OPTIONS=4`. More than that is
  a form, not a conversation. `_coerce_questions()` drops anything malformed —
  a question with fewer than two options isn't a choice — dedupes options, and
  caps both lists. The model's shape is never trusted straight through.
- **`header` is REQUIRED in the schema.** Left optional, the model skipped it
  and every tab read "Question 1". Verified live: it now returns 'Audience',
  'Cadence'.
- **Questions ride on the agent MESSAGE**, so the panel survives a refresh and
  only the NEWEST agent turn is answerable — an older question can't be
  answered again and send a stale reply.
- **Answers become an ordinary chat message** ("Cadence: 2 per week"), not a
  hidden structured payload. The transcript stays readable and the agent handles
  them like any other reply, with no separate answer channel to keep in sync.
- **Always dismissable, and always an "Other" box.** The panel is a shortcut,
  never a gate: the options are the model's guesses and the creator knows their
  own situation better than the list does.
- **Verified:** 13 new checks in `tests/plan_check.py` (questions persist across
  a reload, user turns carry none, one-option/no-text questions dropped,
  duplicates deduped, both caps enforced, bare strings accepted, junk input
  safe) plus a live call confirming real options with consequences and a
  '(Recommended)' marker. Whole suite passes; `npm run build` clean.

### 2026-08-04 — NEW WORKFLOW: Plan & Script (first in the pipeline)

- **Asked for:** a workflow ABOVE Text to Image where the user chats with an
  agent (Gemini-chat style), asks for a 1/3/6/12-month content plan for their
  channel, pastes a YouTube link for the agent to research, and exports to
  Excel/Word. Aimed at creators, 2D/3D artists, editors, influencers, business.
- **Sits first in the sidebar** (`plan-and-script`) — decide what to make before
  making any of it. Spends TEXT quota only; never generates an image.
- **`plan_agent.py`** — two capabilities kept deliberately apart:
  `chat()` (multi-turn; the agent ASKS the questions a strategist would rather
  than guessing) and `generate_plan()` (structured calendar via
  `response_schema`, so it renders as cards and exports as a table — never free
  text pretending to be a schedule). Reuses script_breakdown's provider switch,
  retry policy and greedy sampling rather than growing a second copy.
- **`youtube_research.py`** — resolves every channel URL shape (`/@handle`,
  `/channel/UC…`, legacy `/c/`, `/user/`, bare handle or id), then reads the
  channel by whichever of TWO paths is available:
  1. **YouTube Data API v3** (`YOUTUBE_API_KEY`) — EXACT subscriber/view counts,
     measured publishing rhythm, best performers.
  2. **Gemini `url_context` tool** (NO key — uses the Vertex/Gemini credentials
     already configured). The model opens the channel page and reads it.
     **Verified live against a real channel** (@MSKBhaktisagar): returned the
     channel name, an accurate description of the content, and 12 real recent
     video titles including Devanagari ones.
  The user asked "is the API key the only option?" — it is not, and the key is
  now the optional upgrade for exact figures rather than the requirement.
  Tested: `google_search` alone answers CANNOT ACCESS on a channel URL, and
  `url_context` + `google_search` together blow the 100k tool-output limit — so
  `url_context` is used ALONE. Don't "improve" it by adding search back.
  **THE RULE, per source: it never invents channel data.** Every result carries
  a `source`, and `as_context()` grants exactly the claims that source supports —
  exact figures only from the Data API; name/topics/titles from a page read with
  an explicit "Subscriber and view counts were NOT available: do not state,
  estimate or imply any"; and "ask the user" when neither path worked. All three
  branches asserted in the tests, not just the one this machine is configured for.
- **`plan_export.py`** — xlsx (frozen header, auto-filter, set column widths, a
  second Strategy sheet so the thinking travels with the schedule), docx (laid
  out per upload, not one unreadable 40-row table), csv (UTF-8 BOM so Excel
  opens it cleanly). Added `openpyxl` + `python-docx` to requirements.
- **Storage: nothing new.** A session is a `JobKind.PLAN` job — adding the enum
  value was the whole persistence job, exactly as the Storage rule promises.
  Owner-scoping, listing, rename and delete came for free.
- **A failed reply does not corrupt the transcript**: the user's message is only
  saved once the agent has answered. Otherwise the conversation grows a question
  that was never answered and re-sends it on the next turn. Tested explicitly.
- **Verified:** `tests/plan_check.py` — 66 checks with the model STUBBED (no AI
  quota): session CRUD, transcript persistence both sides, auto-titling, failed
  reply leaving history intact, the no-invented-data rule, all six channel-URL
  shapes, generation, all three exports (valid zip/BOM/filename), export-before-
  generate → 409, owner isolation across five endpoints, 401s, cleanup.
  Exporters separately opened back with openpyxl/python-docx. **One live chat
  turn run for real** — the agent asked clarifying questions instead of dumping
  a generic plan, which is the behaviour the system prompt is built around.
  `npm run build` clean; 75 endpoints.
- **Follow-up the same day — library rebuilt on the SHARED layout.** It first
  shipped with a bespoke gallery (flat grid, plain Delete buttons); the user
  asked for the New / Recent / All layout the other workflows use. Now reuses
  **27** `lib-*` classes from `StoryboardLibrary` and invents **0** — same New
  tile with a count, Recent + All sections, loading ghosts, empty-state card,
  cover/badge/chips/date/icon-actions cards. Dead `.plan-card` CSS removed.
  Written up as the **UI rule** section near the top of this file so the next
  workflow starts from the shared layout instead of a new one.
- **Follow-up — exports PREVIEW before they download.** Clicking XLSX/DOCX/CSV
  used to download immediately, so the only way to check an export was to open
  it in Excel or Word. Each button now opens a large modal
  (`PlanExportPreview.jsx`, reusing `modal-overlay` / `modal-close` / `Icon`)
  showing what THAT format will contain, laid out the way that format lays it
  out: xlsx gets a Calendar/Strategy sheet switcher with a sticky header and
  sticky row numbers (mirroring frozen panes), docx renders as a document, csv
  shows the grid and says plainly that it holds the calendar only. Download sits
  in a pinned footer; Escape and the overlay close it.
- **The preview's columns are GUARDED, not hoped for.** It renders client-side
  from data the browser already has, so `EXPORT_COLUMNS` in the JSX mirrors
  `plan_export.COLUMNS`. `tests/plan_export_columns_check.py` parses the JS list
  and asserts it matches Python exactly, in order — and I verified the guard by
  deliberately renaming a column and confirming it fails with the offending
  index and the file to fix. A preview that disagrees with the file is worse
  than no preview.
- **Not done / next sections:** the plan doesn't yet flow INTO Script to
  Storyboard (a "write the script for this upload" button is the obvious next
  link). Not browser-tested.

### 2026-08-04 — Panels now fill their frame consistently + board layout

- **Reported:** generated panels looked different sizes — "some small and some
  full cover page". **Measured, not guessed:** every panel is already the same
  1365×768 16:9 file. The variance is the blank margin the MODEL bakes in — the
  drawing covered 64%-96% of the frame across one board (borders 0%-11% a side).
- **New `normalise_panel()`** in `storyboard_pipeline.py`, applied wherever a
  panel is written (`run_storyboard` and `regenerate_panel`): measure the blank
  margin, remove it, then grow the content box back to the target aspect using
  REAL pixels wherever the source still has them (never invented bars), leaving
  a uniform 2% margin. Output keeps the original frame size so a board stays
  pixel-uniform.
- **The bug the tests caught, worth not reintroducing:** the first version read
  the paper colour from the four CORNERS. On a panel drawn edge to edge the
  corners ARE the picture, so it treated artwork as paper and cropped the frame
  down to whatever happened to be brightest. Now the whole outer ring is checked
  for UNIFORMITY (`_BORDER_UNIFORMITY`); a textured ring means the art reaches
  the edge and the panel is returned untouched. Plus a `_MAX_TRIM` rail: a
  content box under 65% of a side is treated as a misread, not a margin.
- **Prompt strengthened too** so fewer bordered panels are produced at source
  ("artwork must FILL the entire frame edge to edge… no paper edge").
  `normalise_panel` cleans up what still slips through.
- **Verified** on synthetic panels AND all **16 real boards on disk**: the three
  with a wide spread improved (39.0→25.2, 31.7→16.5, 31.2→13.8 points) and
  **none got worse** — that's the asserted invariant, since a board that is
  already uniform has nothing to narrow. Before/after images inspected by eye:
  content preserved, no crop into the picture.
- **Board layout** (also reported): the script panel had been added UNDER the
  grid, which pushed the export buttons off the end of the page. **Only the
  script moved** — up above the grid, still collapsed. Order is now:
  style bar → **script (collapsed)** → panel grid.
  **The toolbar stays exactly where it always was, above the grid**, with every
  button in its original order: Stop / Generate remaining / Retry all failed /
  Download PDF / Download assets (ZIP) / Make animatic.
  **Do not move the toolbar below the grid.** That was tried and the user
  rejected it immediately — on a long board the buttons end up far from the
  controls they sit with, and the page bottom is nowhere near the eye while
  work is happening at the top.
- **Existing boards keep their old look** — normalisation happens at generation
  time. Re-style or regenerate a panel to apply it.

### 2026-08-04 — Account avatar in the sidebar header → Profile

- **Asked (with a reference image):** the round person avatar in the sidebar next
  to "Character Studio", clicking it opens the profile page.
- **New `client/src/components/Avatar.jsx`** — the glyph as an SVG, not an emoji
  or a PNG: crisp at any size, no network request (so it can't flash in late),
  same silhouette in both themes. Colours match the reference (`#4f52c9` circle,
  `#9aa3f5` figure).
- **Shows the user's INITIAL once we know their name**, falling back to the
  neutral figure — a personalised avatar reads faster than a generic one. The
  initial comes from `display_name` → `full_name` → email, in that order.
- **Placed in the sidebar brand row** (`margin-left:auto`, so it sits at the far
  right of the "🎭 Character Studio" line) and highlights when the profile page
  is open. The SAME avatar is now used in the sidebar footer chip, the account
  modal, Home and the Profile header, so all five read as one identity.
- `App.jsx` fetches `display_name` and passes it to the sidebar, refreshing on
  every nav change — the profile page is the only place it can change, so
  leaving that page picks the new name up.
- **Removed three now-dead CSS rules** (`.sb-avatar`, `.profile-avatar`,
  `.account-modal-avatar`) rather than leaving them to rot.
- **Verified by RENDERING the exact SVG shapes** at high resolution and looking
  at both variants (figure and initial) — the silhouette matches the reference.
  `npm run build` clean; profile / draft / job-store suites still pass.

### 2026-08-04 — Home is the landing page + a real user Profile

- **Asked:** open on Home rather than a workflow; build a proper profile holding
  name/email/etc; move Delete account into it; research what a profile should
  hold.
- **Landing:** `App.jsx` defaulted `nav` to `"text-to-image"` AND `onAuthed()`
  set the same on login — both now `"home"`, so a fresh login and a returning
  session both open the dashboard.
- **What a profile holds, and why** (each field earns its place; a field nobody
  reads goes stale and lies):
  - *Identity* — `full_name`, `display_name` (people want to be "Manish", not
    "Manish Shankar"), `timezone`. **Email is shown read-only**: it is the
    login, so changing it is an account migration, not a profile edit.
  - *Work* — `company`, `role`. This is a studio tool; a shared board is more
    useful when it's attributable to a person.
  - *Storyboard defaults* — `default_style`, `default_aspect_ratio`,
    `default_genre`. **The part that actually saves work**: the storyboard form
    asked these every single time. Empty = "ask me each time".
  - Deliberately NOT included: avatar uploads (a file-storage feature; the
    initial already works), phone/address (nothing reads them — collecting
    personal data no feature uses is a liability), plan/credits (billing, not
    person).
- **`PATCH /auth/me` is an ALLOW-LIST**, not a passthrough (`users.PROFILE_FIELDS`).
  Without it a crafted body could set `password_hash`, `disabled` or `email` and
  take over or lock out the account. There are explicit tests that a PATCH
  carrying all four is ignored while the legitimate field still applies.
- **`POST /auth/me/password` requires the CURRENT password** even though the
  caller holds a valid token — an unattended session must not be able to lock
  the real owner out.
- **Options extracted to `client/src/storyboardOptions.js`** (styles, aspects,
  genres, roles). Profile and the storyboard form now import the same lists; two
  copies would drift the moment a style was added.
- **Home is now purely a dashboard.** 3D API keys, Delete account and the
  account block moved to Profile — Home was half dashboard, half settings.
  Delete now needs the word **DELETE** typed, not one click.
- **Defaults are applied only to an UNTOUCHED form**, and a resumed storyboard
  draft beats them: reopening a 9:16 draft must not snap back to your usual
  16:9. Guarded with a ref set by whichever effect resolves first, since both
  are async.
- **Verified:** `tests/profile_check.py` — 40 checks: empty-not-null defaults,
  trimming, partial PATCH not blanking siblings, the allow-list cases above,
  length limits (422), owner isolation, 401s, password change (wrong current →
  400, same password → 400, short → 422, success → old fails / new works,
  profile survives), delete account. Draft/job-store/grounding suites still
  pass; `npm run build` clean; 65 endpoints.
- **Not browser-tested** — backend is covered and the build is clean, but the
  Profile page hasn't been rendered in a browser.

### 2026-08-04 — Board page and PDF show the title and the script

- **Reported (with a screenshot):** the finished board page said "Your
  storyboard" and showed no script; wanted the real title and the script there
  and in the PDF.
- **Board page title was HARDCODED** — `StoryboardBoard.jsx` printed a literal
  "Your storyboard" regardless of the board. Now renders `job.character_name`
  (falls back to the old string while the first poll is in flight). That name is
  what labels the library card, the PDF and the ZIP, so the board is now
  identifiable at a glance.
- **Script on the board:** reused the existing `ScriptPanel` rather than writing
  a second viewer, with a new `defaultOpen` prop — `true` on review (unchanged),
  `false` on the board, where the script is reference material and not the
  subject of the page. Reads `job.params.script`, so it's there for a freshly
  generated board AND one reopened from the library.
- **PDF already printed the title** on page 1 (verified by rendering it) — the
  missing half was the script. New `_script_pages()` appends it line-numbered
  AFTER the panels. Appended rather than put on page 1 so the panel layout,
  including the tested per-page 6-up/4-up decision, is untouched.
- **Line numbers are the point:** every shot card cites "FROM YOUR SCRIPT ·
  LINE n", and without numbers in the export that citation points at nothing.
  Wrapped continuation lines are left un-numbered so numbering still matches the
  writer's own file, and blank lines keep their number.
- **Verified by rendering and LOOKING at the pages** (not just page counts):
  script page correct — heading, title subtitle, numbering 1-17 matching the
  source, wrapping, blank lines preserved; page 1 header shows "Shorts_1".
  Counts: 3 panel pages → 4 with the script, i.e. exactly the 1 page rendered,
  so nothing was displaced. Empty and whitespace-only scripts add NO pages.
  Draft + job-store suites still pass; `npm run build` clean.
- **Known cosmetic limit:** emoji in a script render as tofu boxes in the PDF —
  the bundled font has no emoji glyphs. Pre-existing, affects captions too.
- **Older boards have no script** (`params.script` empty) — they predate it
  being stored. Their PDFs simply get no script pages; nothing breaks.

### 2026-08-04 — The review step is now backed by Mongo (storyboard DRAFTs)

- **The gap:** script autosave covered the text panel, but the REVIEW step —
  reviewed shots, cast, assets, world edits, generated references — still lived
  only in React state. Refresh there and it all went, and unlike a script that
  work had already **cost AI quota** (the breakdown call, plus any reference
  images). The least-protected thing was the most expensive one.
- **Chose the architectural fix over a bigger client draft:** the breakdown is
  saved as a **`JobStatus.DRAFT` storyboard job the moment it returns**, so the
  review step is backed by the same store as everything else instead of a
  parallel one. Follows the storage rule rather than working around it.
- **Promotion, not duplication.** `POST /storyboards` takes an optional
  `draft_job_id` and promotes that record to `QUEUED`. Same job id from
  breakdown through to finished board — no orphan draft, no showing the same
  work twice. An unknown/foreign/already-generated id logs a warning and falls
  back to creating a job, so an older client still works.
- **Drafts are hidden from the library** (`GET /storyboards` filters
  `status != DRAFT`) — a draft has no panels and isn't a board yet. It's resumed
  through `GET /storyboards/draft`, which returns `job_id: null` rather than 404
  when there's nothing in progress.
- **`PATCH /storyboards/draft/{id}` is partial** (`exclude_unset`), so saving an
  edited shot list can't wipe references chosen on a different step. Tested
  explicitly — a refs-only PATCH must leave shots/title/world intact.
- **Two mount races guarded.** `shots` is `[]` on mount, so an unguarded
  autosave would PATCH an empty shot list over a good draft before the resume
  request returned (`draftHydrated`), and the restore refuses to overwrite
  anything typed while it was in flight. Same class of bug as `draftReady`.
- **A breakdown that can't be saved is still returned.** Storing the draft is
  wrapped in try/except: never lose a breakdown the user just paid for because
  of a storage hiccup. `draft_job_id: null` then, and the client behaves as
  before.
- **Verified:** `tests/storyboard_draft_check.py` — 43 checks covering
  auto-create, hidden-from-library, resume, edit persistence, partial-PATCH
  safety, owner scoping (a stranger gets 404 on PATCH and DELETE), 401 without a
  token, promotion keeping the same job id with no extra record, 409 on
  PATCHing a promoted board, bad-id fallback, and discard. `npm run build` clean.
- **MISTAKE WORTH NOT REPEATING:** the first version of that test did not stub
  `worker.submit_storyboard_job`, so `POST /storyboards` really generated panels
  and **burned image quota** on two runs. The worker is stubbed now. Any test
  that posts to `/storyboards` MUST stub the worker. Stray boards and their
  panel folders were cleaned up; 9 OLDER orphaned panel folders in
  `output/_storyboards/` (10-11 days, no job record) were left alone — they
  predate this work and are the user's data.
- **Not browser-tested.** Backend is covered by the checks above and the build
  is clean, but the resume-on-refresh flow hasn't been exercised in a browser.

### 2026-08-04 — MongoDB is now the system of record for ALL workflows

- **User's rule:** "other than images and media, whatever I make should go into
  Mongo — and once I give it GCS access those URLs too — and this holds for
  anything I add to this workflow later." Written up as the **Storage rule**
  section near the top of this file.
- **The architecture already had the choke point.** Character runs, storyboards
  AND animatics all persist through the single `JobStore` interface
  (`get_store()`), so ONE backend covers every workflow present and future. No
  per-workflow storage code exists or should be written.
- **New `MongoJobStore`** in `server/jobs.py` — `_id` IS the job_id (duplicate
  ids impossible by construction). Indexes: `(owner, kind, created_at desc)` for
  the library screens, sparse `params.share_token` for public links.
- **`update()` validates the merged record through `Job` but writes only the
  CHANGED keys with `$set`.** This is the one real improvement over the other
  backends: the worker writes `progress` continuously while a request may be
  writing `result`, and a whole-document read-modify-write lets one silently
  erase the other. Covered by a test that asserts `result` survives a later
  progress write, plus 12 concurrent writers.
- **GCS URLs need no new plumbing** — `storage.save_character_assets` already
  returns public URLs and the pipeline writes them into `result`, which is now
  in Mongo. Asserted explicitly in the tests rather than assumed.
- **Unlike Firestore, the kind filter runs in the QUERY.** Firestore needed
  over-fetch-and-trim to dodge a composite-index requirement; Mongo doesn't, so
  `limit` is applied after filtering and a page is always full.
- **`API_JOB_STORE` default changed `firestore` → `mongo`**; `.env` switched from
  `memory`. Mongo failure falls back to the local store but logs at **ERROR** —
  a silent fallback means work is being written where nobody will look for it.
- **`server/mongo.py`**: one shared `MongoClient` for users + drafts + jobs
  (was: each opening its own pool). Verified all three now share one object.
- **Migration:** `migrate_jobs_to_mongo.py` moved all **18** existing jobs
  (11 storyboards, 4 character runs, 3 animatics). Validates every record before
  writing anything, skips ids already present so re-runs can't clobber newer
  work, and never modifies the source file. Verified idempotent (second run:
  0 inserted). `.local_jobs.json` deliberately left in place as a backup.
- **Verified:** `tests/mongo_job_store_check.py` runs the SAME 29-check contract
  against MemoryJobStore and MongoJobStore so they're proven equivalent, not just
  plausible — CRUD, owner scoping, kind filtering, share tokens, concurrency,
  GCS-url round trip, delete. Both pass. Auth (16) and drafts (25) re-run clean
  after the shared-client refactor. Post-migration counts confirmed against the
  live API layer: 8 boards + 4 runs + 3 animatics for the real user, 3 boards for
  `z@t.dev` = the original 18.
- **Not done:** GCS itself is still off (no bucket keys in `.env`), so URLs in
  `result` remain local paths until it's enabled — the storage path for them is
  tested and ready. Job records are not yet browser-verified end to end.

### 2026-08-04 — Script autosave (Mongo) + user store switched to Mongo

- **Reported:** a script typed into the text panel was lost on refresh. Confirmed:
  the script only became durable once it had been turned into a board (saved on
  the job as `script`). Before that it was React state only — no localStorage,
  no server. `grep localStorage client/src/` finds only the auth token and theme.
- **New `server/drafts.py`** — ONE autosaved draft per user, `/scripts/draft`
  (GET / PUT / DELETE), owner-scoped. `GET` never 404s: "nothing saved yet" is an
  empty draft, so the client has no special case to write.
- **Backend follows `API_USER_STORE`**, deliberately — no second switch, so
  accounts and their drafts can never land in different stores. Mongo collection
  `script_drafts` (unique index on email), local JSON fallback mirroring
  `users.py`. Reuses `users._client` rather than opening a second pool.
- **Client:** `api.js` gets `getScriptDraft` / `saveScriptDraft` /
  `clearScriptDraft`; `ScriptToStoryboard.jsx` loads the draft on mount and
  autosaves on a 1.2s debounce, with a quiet "✓ Draft saved" line under the box.
- **The bug that mattered, guarded with `draftReady`:** `script` starts as `""`,
  so a naive debounce fires on mount and OVERWRITES the saved draft with an empty
  string before the GET returns — autosave that eats your work. Nothing saves
  until the load settles. The load also refuses to clobber text typed while it
  was in flight.
- **Also switched `API_USER_STORE=local` → `mongo`** (user asked). Verified
  register → login → `/auth/me` → wrong-password-401 through the real API, and
  that `lib@test.dev` (local-file-only) is now invisible — proving it isn't
  falling back. `.env` backed up to `.env.bak.20260803235007`.
- **Dropped the `sample_mflix` database** (67,661 docs, 123 MB of Atlas sample
  data — nothing to do with this project; its 185-doc `users` collection was what
  made Atlas look full of strangers). Guarded: asserted the target wasn't
  `config.MONGODB_DB` and re-counted the real users collection either side.
- **Verified:** 25 draft checks (empty read, save/read-back, overwrite, one doc
  per user in Mongo, owner isolation between two accounts, 401 without a token,
  413 oversize with the previous draft surviving, delete, full cleanup) + 16 auth
  checks. `npm run build` clean. 60 endpoints in the OpenAPI spec.
- **Gotcha for the next agent:** enumerate routes with `app.openapi()['paths']`,
  NOT by walking `app.routes` — included routers appear as `_IncludedRouter`
  objects with no `.methods`, so a naive walk silently drops `/auth/*`,
  `/animatics/*` and `/scripts/*` and reports 41 endpoints instead of 60.
- **Not done:** a multi-script library (list/rename/delete) — user chose the
  single-draft scope. Not browser-tested; backend is covered by the checks above
  and the client build is clean, but the "✓ Draft saved" line hasn't been seen
  in a real browser.

### 2026-08-03 — Image seeding: measured, wired in, and NOT a guarantee

- **Question:** are the varying faces/framing fixable with a seed?
- **Measured live** (`gemini-3.1-flash-image`, Vertex). The API **accepts
  `seed` and it does influence output**: two back-to-back calls at seed 42 came
  back **pixel-identical** (max channel diff 0), seed 999 came back clearly
  different (max diff 223).
- **But it does NOT reproduce reliably.** The same test against real
  `generate_storyboard_panel` requests returned different images both times,
  with the seed instrumented and confirmed identical in the sent config. The
  runs that reproduced were back-to-back; the ones that failed were separated by
  429 backoff. Best guess: reproducibility holds while you land on the same
  serving replica. **Do not tell users a board redraws identically.**
- **Methodology note that changes the answer:** compare **pixels, not PNG
  bytes**. The encoder isn't byte-stable — the first probe hashed file bytes and
  wrongly concluded "seed ignored" when the images were pixel-identical.
- **Wired in anyway** (harmless, sometimes helps): `_seed_for()` + `_image_config()`
  in `gemini_client.py`, applied to all four generators. The seed is derived
  **per request from the prompt**, never fixed globally.
- **Why not one global seed:** every Retry button resends an *identical*
  request, so a constant seed would hand back the identical picture forever.
  `variation=None` (no seed) is passed by `regenerate_panel`,
  `regenerate_single_part`, `regenerate_single_view`, and both reference
  endpoints — for references, re-running IS how the user asks for a different
  face, and downstream consistency comes from the SAVED reference file anyway.
- **Bug caught by testing, worth not reintroducing:** the first version mixed the
  retry `attempt` number into the seed (so a rejected sheet wouldn't redraw
  identically). That meant **any transient 429 changed the seed** and destroyed
  reproducibility. Replaced with a `redraw` counter bumped ONLY when an image is
  returned and rejected by `_is_valid_sheet` / `_is_valid_reference` / the size
  check — transport failures never touch it.
- **Verified:** seed-derivation logic (8 cases: reproducible, prompt-sensitive,
  `variation=None` → no seed, redraw bumps it, explicit variation, `IMAGE_SEED=none`
  kill switch, range, config carries/omits). Live: 8 image calls. All modules import
  clean. **Quota was exhausted by the end** — the teapot re-test could not be
  repeated, so the "same replica" explanation is a hypothesis, not a finding.
- **Not changed:** `run_character.py`'s CLI Step 0 reference stays seeded (it's a
  pipeline run, and `IMAGE_SEED=none` is the escape hatch).

### 2026-08-03 — Breakdown determinism + a grounding (hallucination) report

- **Asked:** what temperature do we use, is the same script reproducible, how is
  hallucination handled, and is the model hallucinating?
- **Found:** exactly ONE temperature existed in the repo — `temperature=0.4` on
  the breakdown call. Every image call (`gemini_client.py` ×4) sent only
  `response_modalities=["IMAGE"]`, i.e. provider defaults. No `top_p`, no seed
  anywhere. Grounding covered ONE field (`script_line`, via `_find_span`);
  `description`, the cast list and the asset list were taken on trust — and
  `description` is what actually gets drawn.
- **Determinism.** The breakdown is extraction, not invention, so it now defaults
  to **greedy decoding with a fixed seed**: `TEXT_TEMPERATURE=0.0`,
  `TEXT_TOP_P=1.0`, `TEXT_SEED=42`, all env-overridable via `_sampling_kwargs()`
  (`TEXT_SEED=none` restores per-run variation). Unknown kwargs are dropped with
  a warning rather than crashing on an older google-genai. Verified against
  google-genai 2.12.1: `seed`/`top_p`/`temperature` all exist on
  `GenerateContentConfig`.
- **Honest limits, stated in `.env.example`:** no Gemini endpoint promises
  bit-exact reproducibility (serving-side batching), `gemini-2.5-flash` is a
  rolling ALIAS so pin a dated snapshot for cross-week comparability, and
  **image generation has no seed parameter at all** — panels and character refs
  vary run to run no matter what. Default model id left ALONE rather than
  guessing a snapshot id that might 404.
- **New `build_grounding_report()`** in `script_breakdown.py` — measures what the
  script actually supports and REPORTS (never deletes; a low score is evidence,
  not proof). It covers: quote match kind, description/script content-word
  overlap (`MIN_DESCRIPTION_OVERLAP = 0.30`, with camera vocabulary excluded so
  framing words don't count either way), cast + asset names absent from the
  script, names used in a shot but missing from the cast/asset list (that
  spelling drift also breaks Stage B reference lookup), and dialogue attributed
  to non-cast speakers.
- **The fuzzy match is no longer invisible.** `_find_span` returns `(start, end,
  kind)` and each shot carries `script_line_match` = `"exact"` | `"fuzzy"` |
  `""`. The ≥50%-coverage fallback can resolve a half-paraphrased quote, and
  that used to look identical to a verbatim one on the card.
- **Deliberately NOT checked:** whether `dialogue` is verbatim. The prompt asks
  the model to turn reported speech into first-person spoken lines, so rewording
  there is the feature working. Documented in the code so nobody "fixes" it.
- **Surfaced through the API:** `Grounding` + `WeakDescription` schemas,
  `ScriptBreakdownResponse.grounding`, `Shot.script_line_match`. Both new fields
  default, so existing clients and stored boards still validate. A per-breakdown
  summary + one warning per issue now goes to the log.
- **Verified:** `python tests/grounding_check.py` — 30 checks, all pass (no
  network, no AI quota). Covers env override + junk-value fallback, exact vs
  fuzzy vs invented quotes, and every report field, including that a CLEAN
  breakdown emits zero warnings (a report that cries wolf is worthless). Also
  confirmed the real `GenerateContentConfig` constructs with the new kwargs, the
  response model round-trips, and `server.main` imports clean.
- **Not done:** the React client doesn't display `grounding` yet — it's in the
  API response and the logs only. Left out deliberately (UI work + browser
  testing wasn't asked for).

### 2026-08-03 — PDF: dialogue is LABELLED, and the blank band is gone

- **Reported, off a real export:** the PDF printed a bare "VIVAN" (say
  *Dialogue Vivan*, not just the name), and every card had a hole in it —
  description, then a blank strip, then Camera / Location / the name tags. It
  showed on silent shots AND on speaking ones.
- **Cause of the hole:** the dialogue band was a **fixed baseline**. Every cell
  reserved `DIALOGUE_H` and jumped the Camera row past it so the rows lined up
  across a page — which meant a shot with no dialogue printed the reservation as
  white space, and a shot with one short line printed the remainder.
- **The card FLOWS now.** Each row is drawn straight after the one above it, so
  the details sit together: description → dialogue (if any) → Camera → Location
  → cast. Measured on a rendered page: the longest blank run inside a card is
  **11px** (silent) and **15px** (speaking), down from ~150px.
- **The picture frame is drawn around the PICTURE**, not around the reserved
  box. A 16:9 panel in a tall cell used to sit in a grey box with bars above and
  below it, pushing the caption down past them.
- **Rows are packed, and reserved per row:** a row of silent shots doesn't
  reserve the dialogue band at all, and each row is only as tall as its own
  pictures plus its own text. Leftover space collects at the foot of the page,
  where it reads as a margin instead of a hole.
- **6-up vs 4-up is now decided PER PAGE** (it was per document): a page whose
  rows all fit with pictures at least `MIN_PIC_H` (210px) prints 6-up as it
  always did; a page carrying a two-line exchange drops to 4-up rather than
  squeezing its panels to 138px. A board with no dialogue is unchanged — still
  6 to a page.
- **"Dialogue" now labels the first speaker**, in the same muted label voice as
  the Camera and Location rows beneath it. `DialogueBox.jsx` prints the same tag
  on the board so the app and the PDF read alike.
- **Verified by rendering pages and measuring pixels** (6 checks): no blank band
  inside a silent card, none inside a speaking card, a silent board still fits 6
  panels to a page, and nothing prints off the bottom. Pages inspected as images
  at each step. `npm run build` clean; backend imports clean.
- **Not browser-checked:** the `Dialogue` tag on the board page. It is a
  three-line JSX addition and the build is clean, but the storyboard board needs
  a generated board (AI quota) to exercise, unlike the animatic suites.

**Follow-up the same day — ONE field order everywhere.** A panel read
differently in each of the three places that show it: the board tile put
dialogue ABOVE the image prompt, the review card put it BELOW camera/location,
and the PDF put it between the two. The order is now **image prompt → dialogue
→ camera / location → cast tags** on all three, with a comment saying so at each
site. There is no shared component to enforce it (the three are a textarea, an
editor and Pillow drawing calls), so it is pinned by a source-order check
instead — that is what silently drifted.

### 2026-08-02 — "+ Add layer" makes a BLANK lane; image layers composite

- **Reported:** "+ Add layer" created content — an upload dialog for Images, a
  caption for Text, a shape for Shapes — and everything piled onto the single
  lane of its kind. Wanted: **an empty row**, filled afterwards, for all four.
- **`layers` is now a first-class list** (`{id, kind, name}`), and every clip
  carries a `layer_id`. `""` means the **default lane** of that kind — which is
  what every project saved before this is made of, so old animatics open showing
  exactly the lanes they always did. `frames` is not a layer: it is the video.
- **The timeline is no longer four hard-coded lanes.** The editor builds ONE
  `lanes` list and the gutter labels *and* the tracks are generated from it.
  ⚠ That is also the alignment contract now: every row takes its box from
  `.tl-lane-row` / `.tl-lane` — one height, one gap, no exceptions. The per-kind
  height rules are deleted; with any number of lanes per kind they could only go
  wrong, which is how labels ended up beside the wrong tracks once before.
- **Image layers are OVERLAYS** (chosen by the user over "extra lanes, one
  sequence"): `AnimaticOverlay` is a picture with a shape's geometry — fractions
  of the frame, `x`/`y` the centre — placed with the same drag handles, because
  it IS the same box with a picture in it instead of a colour. `draw_overlays()`
  composites them, `plan_segments` cuts on their boundaries, and the render cache
  key includes their ids.
- **⚠ Stacking is frame → shapes → pictures → text**, and the PREVIEW had it
  backwards at first: it painted overlays under shapes while the exporter drew
  them over. The browser test caught it by hit-testing the centre of the
  preview. Change one order, change both.
- **⚠ `thumbnail()` never upscales.** Overlays used it, so a small logo dragged
  out to half the frame stayed small in the export while the preview showed it
  big. It is `resize()` with an explicit contain-scale now, which goes both ways.
- **⚠ `onClick={addText}` passes the CLICK EVENT as the first argument.** Once
  `addText` took a `layerId`, that event became the lane id and the caption
  landed on a lane that doesn't exist — invisible AND unreachable. Fixed at the
  call site, plus a `laneId()` guard that refuses anything that isn't a string,
  because the next handler wired up will make the same mistake.
- Each lane's ＋ adds to THAT lane (`addToLane`); a lane the user made carries a
  ✕ that removes it *with its contents* (undoable — it is an ordinary document
  edit). `API_MAX_ANIMATIC_LAYERS` (default 24) caps the rows.
- **Verified: 35 browser checks** — the default lanes unchanged, Add-layer
  creating an empty row of each kind *without* creating content, each lane's ＋
  filling only that lane, clips saving with the right `layer_id`, an overlay
  dragged on the preview and **composited into a real exported MP4 at the
  dragged position**, lane removal + undo, and gutter/track row counts staying
  equal throughout. Plus **24 exporter checks** (aspect preserved, cut-out alpha
  kept when faded, clockwise rotation, upscaling, missing file skipped), and the
  three earlier suites re-run green (shapes 22, shortcuts 39, delete 18).
  `npm run build` clean.

### 2026-08-02 — Premiere's keyboard: tools, shuttle, marks, undo/redo

- **Asked for:** the standard Premiere shortcut set in the animatic editor.
- **Implemented, with each key doing a REAL thing on this timeline:**

  | Key | Does |
  |-----|------|
  | `V` `C` `B` `N` `H` `Z` | Selection · Razor · Ripple · Rolling · Hand · Zoom |
  | `Space` | Play / pause |
  | `J` `K` `L` | Shuttle back / stop / forward — press again for 2×, 4× |
  | `←` `→` | One VIDEO frame (1/fps), not one picture |
  | `↑` `↓` | Previous / next edit point |
  | `Ctrl+K` | Add edit — splits the picture at the playhead |
  | `I` `O` | Mark in / out · `Ctrl+Shift+X` clears them |
  | `Ctrl+S` | Save (works from inside a text field too) |
  | `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
  | `Del` / `Backspace` | Delete the selection (frame · text · shape · audio track) |
  | `S` | Snapping on/off · `~` maximizes the pane under the pointer |

- **NOT built: the Pen tool (P).** A pen pulls keyframes and an animatic has
  none — there is no property that varies over time. Saying so beats a tool that
  does nothing.
- **Ripple vs rolling is a real distinction here**, not a label: dragging a cut
  with **B** moves everything after it (the video gets longer — what edge-drag
  always did), with **N** the next frame absorbs exactly what this one gains, so
  **the video stays the same length**. Rolling declines on the last frame:
  nothing follows it to absorb the change. Measured: 12000ms → 12000ms rolling,
  12000 → 13800 rippling.
- **Undo is one stack over the WHOLE document** (title, settings, frames, texts,
  shapes, audio) — that is the unit a person means by "undo". It lives in a ref,
  not state, so pushing an entry doesn't re-render; a tick counter re-renders
  the buttons. Bursts inside 500ms share one entry, or undoing a drag would
  take fifty presses.
- **⚠ Bug the browser test caught, and the reason to keep testing this:
  the first `Ctrl+Z` wiped the animatic.** An editor mounts with empty
  frames/texts/shapes and fills them from the server a moment later; that fill
  was recorded as an edit, so the empty document became an undo target. History
  now starts only once `loadedRef` is set, and the load handler RESETS the
  stack. The regression is asserted first in the test.
- **Marks bound PLAYBACK, not the export.** Play starts at In and stops at Out;
  the export dialog still says (correctly) that it encodes the whole timeline.
  Drawn as a band on the ruler, under the tick labels.
- **Shuttle detail:** only 1× uses the audio as master clock. Faster rates set
  `el.playbackRate`; reverse pauses audio entirely, because **no browser can
  play an `<audio>` element backwards**. The pictures still run in reverse.
- **Snapping (`S`, on by default)** pulls a dragged edge to the nearest cut,
  the playhead, another clip's edge or a mark within 8px. The clip's own edges
  are excluded from its targets or it would stick to where it already is. Off,
  drags round to the 100ms grid exactly as before.
- **Verified in a real browser: 39 checks**, covering all six tools by key, the
  shuttle actually running faster and backwards, arrows stepping one fps-frame
  vs edit points, Ctrl+K and razor-click both splitting (halves sharing one
  source, total length unchanged), undo/redo, the marks band, snapping toggle,
  `~` maximize/restore, rolling vs ripple by measured total duration, Ctrl+S
  from inside a text field, and that **typing "Voice CBN keys" into the title
  neither switched tools nor cut anything**. Zero console errors.
  `npm run build` clean.
- **`Del` / `Backspace` delete the SELECTION**, resolved in the same order the
  Properties pane picks what to show — so Delete always removes the thing the
  pane is describing, which is the only reading of "the selection" a person can
  act on. Deleting a frame then selects its neighbour, so Delete-Delete-Delete
  walks a sequence without reaching for the mouse. With nothing selected it says
  so rather than silently doing nothing. Backspace is included because on a Mac
  keyboard that IS the delete key — and unhandled it navigates the page back,
  which loses the editor. Undo restores anything deleted (it is an ordinary
  document edit). Verified with 18 more browser checks, including that Delete
  while typing in a caption edits the caption and that Backspace in the title
  field edits the title rather than removing a frame.
- **Still in the scratchpad, not in `tests/`:** this suite and the shape one.

### 2026-08-02 — SHAPES: a fourth layer in the animatic editor

- **Asked for:** shapes in the animatic editor, with a picker panel like the
  reference editor the user sent (square / circle / pentagon / star).
- **A shape is a CLIP, not a frame decoration.** `AnimaticShape` has its own
  `start_ms` / `duration_ms`, exactly like a text clip, so it can appear
  part-way through a held image and run across a cut. It is stored on the
  project as `shapes` and is absent on every animatic saved before this, which
  reads as an empty list and changes nothing about them.
- **⚠ Geometry is FRACTIONS of the frame (0–1), never pixels**, and `x`/`y` are
  the shape's CENTRE (which is what makes rotation not move it). The preview box
  is a few hundred pixels wide and the export can be 4K — a fraction is the only
  thing that means the same in both. Verified: identical coverage at 320×180 and
  1920×1080.
- **⚠ The polygons live in TWO files and must match:** `_SHAPE_POINTS` in
  `animatic.py` and `POINTS` in `client/src/components/Shapes.jsx` (as CSS
  clip-paths). That pair is what makes the preview and the MP4 agree. Both carry
  a comment pointing at the other.
- **Exporter:** `draw_shapes()` gives every shape its own RGBA layer — the only
  way to rotate an ellipse (Pillow can't draw one rotated) and it keeps opacity
  exact. Rotation is NEGATED going into Pillow, which turns anticlockwise while
  the editor and CSS treat a positive angle as clockwise. Shapes are drawn UNDER
  the text, same as the preview stacks them: a shape is a highlight ON the art,
  and a caption you can't read over it would be pointless.
  `plan_segments()` now cuts on shape boundaries too, **and the render cache key
  gained the shape ids** — without them two segments differing only in which
  shapes are up would share one still and a shape would pop at the wrong moment.
- **Client:** the Media pane has **Media / Shapes tabs** (the picker is a library
  you take from, not this animatic's footage — under the frames it sat below a
  60-panel board and would never be found). A shape is **dragged on the picture**
  to place it and **resized by its corner handle**, with the opposite corner
  pinned. Timeline gained a **Shapes lane**; `ShapeProperties` covers kind,
  timing, position, size, opacity, rotation and colour.
- **⚠ The clipped fill is a CHILD of the shape element.** `clip-path` on the
  outer box cuts off the selection outline and the resize handle — on a star
  they sit exactly where the clipping is.
- **Text and shape clips share ONE drag implementation** (`startClipDrag(e, clip,
  mode, kind)` in `Timeline.jsx`). They are the same object on a timeline; two
  copies would drift.
- **Bug fixed while here (pre-existing, all three drags):** every pointerup
  handler called the parent's `onChange` **inside a `setDraft(current => …)`
  updater**. React runs updaters during the render phase, so that is a
  setState-in-render — it logged "Cannot update a component while rendering a
  different component", and in StrictMode the updater runs twice, firing the
  parent write twice. All three now remember the value in `dragRef.current.latest`
  while moving and write it on pointerup. **Keep that pattern.**
- **Verified.** 28 exporter checks (polygon vs bounding box, opacity blending,
  clockwise rotation, resolution independence, segment cutting, and a real MP4
  whose decoded frames show the shape absent at 0.2s and present at 0.9s); 13
  checks through the **real API** (round-trip, a partial save leaving shapes
  alone, an empty list clearing them, out-of-range opacity 422'd, then export →
  download → decode); and a **short browser smoke** (one viewport, not the
  five-viewport suite) covering the tabs, the gallery, add, drag, resize,
  persistence, and that text clips still re-time — 22 checks, zero console
  errors. `npm run build` clean.
- Also fixed a visible glitch next door: `.tl-track-empty` prompts wrapped to a
  second line and were sliced by the lane's `overflow: hidden`; they now
  ellipsise on one line.
- **Not done:** shapes are not in `tests/e2e_animatic.py` yet — the smoke above
  lives in the session scratchpad, which is wiped between turns. Folding it in
  is the obvious next step (same gap the backend suites have).

### 2026-08-01 — Shots carry DIALOGUE (breakdown → review → board → PDF)

- **Asked for:** the shot panel should show what is SPOKEN in that shot, worked
  out during the script breakdown, and shown on the review page, the board and
  the PDF — **and shown nowhere at all when the shot has no dialogue.**
- **`dialogue` is a list of `{character, line}` on every shot and panel**, empty
  for a shot where nobody speaks. Every consumer returns null / draws nothing on
  an empty list, so a silent establishing shot looks exactly as it did before —
  no heading, no empty box. That "empty means invisible" rule is the whole
  feature; don't add a placeholder row to any of the three surfaces.
- **Breakdown** (`script_breakdown.py`): new prompt block + schema + `_coerce_dialogue`
  (caps 6 lines/shot at 300 chars, drops a speaker with no words, tolerates a
  bare string or a lone object). Quoted speech is copied verbatim; **reported
  speech is converted to first person as spoken** ("he declares they will be
  kings" → "We will be kings") — the first live run returned the narrator's
  third person, which reads wrong on a board, so the prompt now says so
  explicitly. Invented dialogue is forbidden in the prompt.
- **⚠️ `_PROMPT_TEMPLATE` goes through `str.format`** — the literal braces in
  `{{character, line}}` MUST be doubled. A single brace raises
  `KeyError: 'character, line'` at call time, which no import or build catches.
- **PDF** (`storyboard_pdf.py`): a board WITH dialogue prints **2×2 instead of
  2×3**. Taking the dialogue band out of the picture at 2×3 dropped a 16:9 panel
  from ~308px to ~215px tall; at 2×2 the cell is tall enough that **the pictures
  stay exactly the size they always were** and the band is simply extra room.
  The grid is chosen once for the whole document (mixed 6-up/4-up pages read as
  two documents stapled together). A board with no dialogue is byte-for-byte the
  old layout. Overflow says "+N more lines" and reserves the row to say it.
- **Client:** `DialogueBox.jsx` (read-only, used on the board) and
  `DialogueEditor.jsx` (review step: speaker + line + ✕, "＋ Add a line", and
  only a quiet "＋ Add dialogue" link when the shot is silent). Speaker fields
  autocomplete from the cast via a `<datalist id="sb-cast-names">`.
- **Dialogue is deliberately NOT in the image prompt.** Asked to draw a line of
  speech, an image model letters it into the panel as a caption or speech
  bubble. It travels beside the prompt, never inside it.
- **Verified.** 23 backend checks green (coercion incl. junk input, `Shot`
  defaulting so an old client payload without the key still validates, dialogue
  reaching real `run_storyboard` panels with the image call stubbed, legacy
  panels with no key). PDF rendered and **inspected as images**: silent shots
  draw nothing, a two-line exchange fits, "+4 more lines" appears on a
  six-speaker panel, an unattributed line drops the name and keeps the rule, and
  Camera/Location stay on one baseline across a row. **One live breakdown call**
  on a prose script: 2 of 9 shots got dialogue, the tea-stall scene stayed
  silent, nothing was invented, no blank lines. `npm run build` clean.
- **Not browser-checked** (standing instruction: Playwright on request only).

### 2026-07-31 — Audio can be TRIMMED; the export covers the whole timeline

Two user-reported gaps, both about length.

1. **Audio had no trim.** Images and text could be dragged to length; audio was
   stuck at whatever the file was. `AnimaticAudio.trim_ms` (None = whole file
   from `offset_ms`) is now set by **dragging the clip's right edge**, exactly
   like a frame hold or a text clip, or by typing it under **Plays for** in
   Properties (with a "Use whole track" reset). The audio lane is a real CLIP
   now — as wide as the track actually plays — not a band spanning the timeline.
   ffmpeg gets `-t` before that input, so only the trimmed part is read.
2. **The export was cut to the images.** 29s of pictures under a minute of music
   exported 29s. **The video now runs to the end of the LONGEST layer** and the
   last picture is HELD while the audio (or a late caption) plays on.
   `plan_segments()` takes `end_ms` and simply extends the final frame's span,
   so a text clip landing in that tail is still cut in correctly.
   `AnimaticSettings.end_at` = `"timeline"` (default) or `"frames"`, offered in
   the export dialog as **"Whole timeline — 0:20" / "Just the images — 0:03"**
   with the real numbers in the labels.
   **The default changes behaviour only in the case that was reported** — when
   nothing runs past the pictures, both options are identical.

- **Verified end to end.** Dragging the handle shortened the clip 960→656px and
  saved `trim_ms=20500`; exporting then produced a video measured at **20.50s**,
  matching the trim rather than the 3s of images, and switching to "Just the
  images" produced exactly **3.00s**. Decoding frames of a held export: red
  image at 1s, green at 2.5s, and **green still held at 15s** — the last picture
  is held, not black — with audio present throughout. No console errors.
- **Note:** `end_ms` only ever EXTENDS (the exporter takes
  `max(frames_total, end_ms)`), so it can't accidentally truncate a sequence.

### 2026-07-31 — Export is a dialog now: name, resolution, frame rate, quality

- **Asked for:** an export pop-up like Premiere's, kept simple, with the
  settings an export actually needs.
- **Export no longer encodes on click.** It opens a dialog: **file name**
  (defaults to the title, `.mp4` shown fixed beside it), **format** stated as
  fixed (MP4 · H.264 + AAC), **resolution**, **frame rate**, **quality**, and an
  **include-audio** checkbox that reports how many tracks there are. Cancel does
  nothing at all.
- **New settings, persisted on the project:**
  - `resolution` — the **SHORT** edge, so 1080 means 1920×1080 for 16:9 and
    1080×1920 for 9:16, the way "1080p" is normally meant. 720p / 1080p / 1440p
    / 4K. `resolve_size()` now scales the familiar size table by
    `resolution / 1080`, so **the default returns exactly what it always did**.
  - `quality` — high/medium/low → x264 CRF 18/21/25.
  - `include_audio` — makes a silent export without removing the tracks.
- **The dropdown shows the real output size** ("1080p — 1920×1080") and the
  summary line updates live, because the client mirrors the server's sizing rule
  in `frameSizeFor()`. **Those two must be kept in step.**
- **No estimated file size, deliberately.** Premiere shows one, but an animatic
  is mostly still frames and compresses far better than normal video — measured,
  the same 3s at "high" was 3.4 MB with detailed frames and 8 KB with flat ones.
  Any figure would be wrong by orders of magnitude, so the dialog states only
  what is actually known: length, frame size, fps, frame count.
- **Verified end to end.** Encoder: 720p/1080p/4K produce 1280×720 / 1920×1080 /
  3840×2160, 9:16 at 1080 gives 1080×1920, omitting the resolution still gives
  1920×1080, and duration is still set by the frames. Quality genuinely changes
  the encode — on detailed frames low/medium/high came out 2.45 / 3.01 / 3.46 MB
  (the first attempt used flat colour images, where the file is all container
  overhead and the sizes inverted — a bad test, not a bad encoder). In the
  browser: the dialog opens without starting an encode, lists exactly the six
  settings, follows the resolution live, and choosing 720p produced a real file
  measured at **1280×720** with audio, settings persisted. No console errors.

### 2026-07-31 — "video ends" marker removed from the timeline (user-reported)

- **Reported:** the dashed "video ends" line and the hatched shading over the
  stretch past it were distracting — and unnecessary, since the user already
  knows how long their images are.
- Both are gone (`.tl-past-end`, `.tl-end-mark` and their CSS). **The behaviour
  behind them is unchanged**: the timeline still SPANS the audio, which is what
  lets the playhead reach the end of a long track.
- The same information is still available where it doesn't sit on top of the
  working surface: the timeline header reads "audio 0:59 — video ends early",
  and the transport clock says "past the end of the video" once the playhead
  goes beyond the last frame.
- `npm run build` clean. Not browser-checked — this only deletes two decorative
  elements and their rules; the seek/​span logic it sits next to was measured in
  the previous entry.

### 2026-07-31 — The playhead couldn't reach the end: a width/time mismatch

- **Reported:** the playhead wouldn't travel to the end of the music, plus a
  standing instruction — **anything with settings belongs in the Properties
  pane**, so audio volume shouldn't have been a mixer bolted onto Media.
- **The bug, found by measuring:** `.tl-inner` carried `min-width: 100%`, so
  whenever the timeline was **narrower than its pane** it stretched to fill —
  but the time mapping is `x / pxPerSec`, which does **not** stretch with it.
  The lanes were then drawn wider than the time they represent: measured, a
  click at the visual half-way point of a 59s track landed at **0:44**, and the
  playhead physically could not reach the right-hand side because there was no
  time left to map to. Zoomed IN (timeline wider than the pane) everything was
  fine, which is why it looked intermittent.
  → `min-width` removed: **the timeline is now exactly as wide as the time it
  shows.** `msFromEvent` also clamps to the span as a second line of defence.
  **Don't re-add a min-width here** — any rule that changes the track width
  without changing `pxPerSec` reintroduces this.
- **Audio settings moved into Properties**, where every other selectable thing's
  settings live. An audio track is now **selectable** — click its lane or its
  gutter row (or its row in Media) and Properties shows `AudioProperties`:
  volume with a mute toggle and a % readout, "starts at" (how far into the file
  playback begins), its length, and Remove. Media is back to being a plain list
  that shows each track's level. Selection stays exclusive across all four
  states via one `selectOnly()` helper, so the pane can't show the wrong thing.
- **Verified by measurement at both zoom levels:** every track is exactly the
  ruler's width; clicking a waveform at 25% / 50% / 98% seeks to 14s / 29s / 57s
  of 59s (the 98% case used to be unreachable); the playhead lands under the
  click to within 2px; dragging the playhead reaches 0:59; and with the timeline
  scrolled fully right a click still seeks to 0:58 with the playhead under the
  cursor. Volume set to 15% in Properties shows there and echoes in the Media
  list. No console errors.
- **Test-artifact note for next time:** clicking at `boundingBox.x + width*f` on
  a timeline WIDER than its pane targets an off-screen coordinate and Playwright
  clamps it — that produced two false failures. Click within `.tl-scroll`'s
  visible box, or scroll first.

### 2026-07-31 — Timeline spans the audio; delete a layer; ＋ Add layer picker

Three user-reported problems with the new audio layers.

1. **The playhead couldn't reach the end of the audio.** With 2 minutes of music
   under 2 seconds of pictures, the ruler stopped at 2 seconds — so there was no
   way to scrub into the track you were trying to time against. **The cause was a
   deliberate-but-wrong conflation:** the timeline's width came from `totalMs`,
   the VIDEO length, which is the sum of the frame holds.
   → The timeline now spans **`spanMs = max(totalMs, longest audio end)`**.
   Ruler, width, scrubbing and playback all use the span; **the video length is
   unchanged and still what exports** — the frames decide that, as before. The
   stretch past the last picture is shaded with a dashed **"video ends"** marker
   so the difference is visible rather than implied, and the transport clock now
   counts against the span (it read a nonsensical "0:30 / 0:02" before) with a
   "past the end of the video" note beyond that point.
2. **No way to delete an audio layer.** Only the Media pane had a remove. Each
   audio row in the timeline gutter now carries its own ✕ next to the mute.
3. **＋ Add layer added audio blindly.** It now opens a picker — **Images /
   Text / Audio / Video** — and does the right thing for each. Video is listed
   but disabled with the reason ("an animatic is stills plus audio"), because
   leaving it out entirely just makes people hunt for it.

- **Verified in the browser** (the user reported these, so a run was warranted):
  1 frame under a 30s track → ruler reaches 0:30 while the header still reads
  "Video length 0:02", the end marker and shaded region appear, clicking the far
  right of the ruler seeks to 0:30 (it used to clamp at 0:02) and the playhead
  actually moves there; the lane's ✕ removes the track and the timeline snaps
  back to the video length; the picker offers exactly Images/Text/Audio/Video
  with Video disabled, and choosing Text really adds a clip. Zero console errors.

### 2026-07-31 — Audio is a LAYER: several tracks, mixed on export

- **Asked for:** "add layer" — the user confirmed they meant **more tracks**,
  and picked **a second audio track (music + voiceover)** as what they need.
  (Image-overlay and extra text rows were offered and not chosen; they'd need
  real compositing, so they're deliberately not built.)
- **Schema:** `AnimaticAudio` gains `volume` (0–2, default 1) and `muted`.
  `AnimaticProject.audio` (one object) became **`audio_tracks` (a list)**, and
  `AnimaticSaveRequest.clear_audio` is gone — with a list, "no audio" is just an
  empty list, so there's no flag to keep in step.
  **Old records are migrated on READ** (`_audio_tracks_of` wraps a legacy
  `audio` object in a one-item list); nothing rewrites them on disk, and a save
  drops the old key so the migration can't resurrect it.
- **Upload no longer wipes what's there.** It used to delete every `audio_*`
  file before writing, because only one track existed. It now just stores the
  file and returns the id — **which tracks an animatic HAS is decided by the
  saved project, not by what's on disk.**
- **`API_MAX_ANIMATIC_AUDIO_TRACKS`** (default 4): every extra track is another
  ffmpeg input to decode and mix.

**Mixing (`animatic.py`)**

- Each track is its own `-i`, with `-ss` before it for that track's `offset_ms`.
- With one track at its recorded level, the **old simple path is unchanged** —
  that path was already verified, and there was no reason to put it through a
  filter graph.
- Otherwise a `filter_complex`: `volume=` per track, then `amix`. **Video goes
  through the same graph** (`[0:v]fps=…[vout]`) so ffmpeg never has to reconcile
  a simple `-vf` with a complex one.
- **`amix=…:normalize=0` is the critical bit.** amix divides every input by the
  number of inputs by default, so a voiceover mixed over music would come out at
  half the level the user set. Measured: two tracks at full go from −11.7 to
  −8.7 dBFS (louder, as they should), not quieter.
- A track whose file has gone is skipped; if they all have, the video exports
  silent rather than failing.

**Client**

- One `<audio>` element per track. **The first track that is genuinely playing
  is the clock master**, exactly as the single track used to be; if it ends
  early the wall clock takes over seamlessly. All elements are *placed then
  started together* — starting one before placing another is what makes two
  tracks drift apart at the top of playback.
- Preview volume is clamped to 1 because that's the browser's ceiling; **the
  export still applies the real figure** through ffmpeg. Worth knowing if a
  boosted track sounds quieter in the editor than in the MP4.
- Timeline: **one lane per track**, each with its own waveform, its own gutter
  row (filename + mute), and a **"＋ Add layer"** control under them. A muted
  lane is dimmed, not hidden — it's still part of the edit. The timeline pane
  now grows with the number of lanes (up to half the window, then scrolls).
- Media pane: per-track mute, a volume slider with a % readout, and remove.
  Dropping several audio files at once creates several tracks.
- `audioMs` (what "fit frames to audio" matches) is now the **longest** track.

- **Verified:** 24 checks against real MP4s, measuring loudness with ffmpeg's
  `volumedetect` — one track unchanged, two tracks mixed into ONE stream without
  being halved, `volume: 0.25` measured **12.1 dB** down (theory says 12.04),
  a ducked bed quieter than two at full, per-track offsets not changing the
  video length, a missing track skipped while the other still plays, all-missing
  exporting silently, and four tracks mixing to one stream. `npm run build`
  clean; backend imports clean.
**⚠️ I shipped this broken, and `npm run build` said nothing.** Two mistakes,
both from indent-sensitive string replacements that silently matched nothing:

1. A stale `audioOffsetMs={offsetMs}` was left on `<Timeline>` after `offsetMs`
   was deleted → **ReferenceError on render → the whole app was a blank page.**
2. The new `audioTracks` / `audioUrls` props were never actually added to the
   `<Timeline>` call, so only one empty lane drew and no waveform appeared.

**Vite/esbuild does not check for undefined identifiers inside function bodies**
— a clean build proves the code *parses*, not that it *runs*. After any rename
of a variable used in JSX, either load the page or grep the old name; and when
patching by string replacement, assert the match count (the helper used here now
prints a warning when a replacement finds 0 occurrences, which is what should
have caught both).

- **Fixed and verified in a real browser** (the user reported the blank page, so
  this warranted a run): the app renders, New Animatic opens the editor, three
  files dropped at once give 1 frame + **2 audio lanes each with its own drawn
  waveform** and correctly-named gutter rows, mute dims a lane, the volume
  slider is disabled while muted, moving it to 35% shows "35%" and **persists to
  the server** (`[0.35, 1.0]`), the library reports `audio_count: 2`, playback
  advances the clock, and there are zero console errors throughout.
- The gutter widened 6.6rem → 9rem: at the old width a filename truncated to
  "m…", which told you nothing about which lane was which.

### 2026-07-31 — Icons are inline SVG, so they finally share one colour

- **Reported:** the ▶ / ✏️ / 🗑 row on a library card was three different
  colours.
- **Cause:** ✏️ and 🗑 are **colour emoji** — the font supplies a pink pencil
  and a teal bin, and **no CSS can recolour them**. ▶ and ⧉ are monochrome text
  glyphs that do take `color`. Side by side they looked like three different
  apps.
- **`Icon.jsx` (new)** is the app's icon set as inline SVG, stroked with
  `currentColor` and sized in `em`. An icon simply takes the colour and size of
  the button it sits in, which is what makes the muted default, hover, and the
  red danger state work on all of them at once. Set: play, pencil, trash,
  download, link, copy, close, save, text.
- **Swapped everywhere they act as icons:** both library card action rows
  (▶ ✏️ 🗑 ⬇ / 🔗 ⧉ ✏️ 🗑), the animatic editor's top bar (save, export,
  delete), the frame-card tools (⧉ ✕), the properties Duplicate/Remove buttons,
  the audio remove ✕, the Text-to-Image job list 🗑, and Home's "Delete account".
- **Deliberately left as emoji:** style-chip labels like "✏️ Rough Sketch",
  "💥 Comic", "🎬 Cinematic". Those are decorative names in a set, not icon
  buttons, and their colour is the point.
- **Verified in the browser before the "don't auto-test" instruction landed:**
  6 SVG icons in the action row, all rendering `rgb(91,99,119)` at 16px, no
  emoji left in the row or the top bar, and hovering delete recolours the glyph
  to red — proving `currentColor` is doing the work. The only non-ASCII glyph
  left in the top bar is `←` (U+2190) in "← Your Animatics", a plain monochrome
  arrow that inherits colour correctly.

### 2026-07-31 — Media pane: one "Add assets" control instead of three (user-reported)

- **Reported:** the Media pane had three add/upload controls — a "＋ Add images"
  button, an "Add images or drop them here" card, and a separate "♪ Add an MP3"
  — for what is really one action. Wanted: **one** target that takes anything.
- **Now one control**, "＋ Add assets or drop them here", which accepts images
  **and** audio together and sorts them out by file type: images become frames
  (still filename-sorted), an audio file becomes the track. **The whole pane is
  the drop target**, not just the little dashed box, and it highlights while a
  file is over it.
- `kindOf()` classifies by MIME with an **extension fallback** — a drag from
  some file managers arrives with an empty `type`.
- **Nothing is silently dropped.** Unsupported files are named in the status
  strip: video says so explicitly ("video isn't supported yet" — there is no
  video path in the backend at all), as do a second audio file (one track only)
  and anything else.
- The "Audio" section only appears **once there is audio**; the empty heading
  with its own button was the third of the three controls.
- `FrameStrip` gained `showAdd` so the strip's own button and trailing add-card
  disappear in the Media pane; it keeps them for any other use. Its file-drop
  path now routes through `addAssets` too, so dropping an MP3 onto the frame
  list works the same as dropping it on the pane.
- **Verified in the browser:** exactly one add control in the pane, no
  "Add images"/"Add an MP3" anywhere, no empty Audio heading; then two images
  **and** a WAV through that single input in one go → 2 frames + a drawn
  waveform + "Added 2 images and audio “score.wav”"; and an .mp4 → "Skipped 1
  video file(s) — video isn't supported yet" with the frame count unchanged.
  Folded into `tests/e2e_animatic.py` (**99 checks**), which now uploads its
  images and audio through the one control.

### 2026-07-31 — Top-bar buttons are one size family (user-reported)

- **Reported:** Save, Export video and the delete icon didn't match in size or
  look.
- **Cause:** they carried different `.btn` modifiers — `small` for Back, Save
  and Delete, full-size for `primary` Export — so they sat at different heights
  with different padding and font sizes, reading as unrelated controls.
- **Fix:** one rule, `.an-topbar .btn`, gives every button in the bar the same
  height (2.3rem), padding, font size, radius and border. The icon-only Delete
  is a square of that same height rather than a wide slab. **Only the FILL
  distinguishes them** — Export stays gold because it's the primary action.
- **Verified in the browser, both themes:** all four buttons measure identical
  height (37px), baseline, font size, corner radius and border width, and the
  delete button is 37×37. Now asserted permanently in `tests/e2e_animatic.py`
  (**95 checks**), so a future `btn small` can't quietly break the row again.

### 2026-07-31 — Empty animatics discarded on exit; Save asks for a name

Two user-reported bugs, both visible in one screenshot of a library full of
junk: seven "Untitled animatic" records, every one of them claiming to export.

1. **Opening a new animatic and leaving without touching it kept the record**,
   so the library filled with empty "Untitled animatic" rows.
   → **New Animatic still goes straight into the editor** (no dialog in the
   way — an earlier attempt that asked for the name up front was rejected as
   the wrong shape, and rightly: naming a thing before you know if you want it
   is backwards). Instead, **leaving discards an animatic that has nothing in
   it** — no frames, no text, no audio, no export, and still the placeholder
   name. Anything with content is kept, named or not.
   → **Save on an unnamed animatic opens a "Save animatic as…" panel**; type a
   name, press Save, and it's written under that title. Once it has a real
   name, Save just writes with no interruption. Autosave is unchanged.
   `UNTITLED` is exported from `AnimaticLibrary.jsx` so both sides agree on what
   "not named yet" means.
   **Known limit:** the discard runs on the Back button. Closing the tab
   outright still leaves the empty record (a `beforeunload` request is not
   reliable enough to depend on).
2. **Every card said "Exporting…" with a spinner.** `JobStatus.QUEUED` means two
   different things in this codebase: for a storyboard it's "work waiting to
   start", but for an animatic it's **"a draft that has never been exported"**.
   `AnimaticLibrary` was copied from `StoryboardLibrary` and inherited the
   storyboard reading, so every un-exported animatic looked busy forever.
   → The library now treats **only `running`** as an export in progress (the
   export endpoint sets RUNNING before it submits, so nothing is missed).
   **Remember this whenever you copy something from the storyboard library:
   the two workflows do not mean the same thing by `queued`.**

- **Verified in the browser** (`tests/e2e_animatic.py`, now **89 checks**): New
  goes straight into the editor; opening one and leaving without touching it
  leaves the server count unchanged; Save on an unnamed animatic opens the
  panel, its Save is disabled until a name is typed, and the title takes; a
  named animatic saves without asking again; and no card carries a false
  "Exporting…" or a stuck spinner. A separate five-scenario pass also confirmed
  an animatic with frames but no name is **kept** (only truly empty ones are
  discarded), and that a reopened named animatic saves silently.
- **Not done:** the seven junk animatics already in the user's library are left
  alone — deleting someone's records isn't mine to do. They can be removed with
  the 🗑 on each card.

### 2026-07-30 — Editor top bar + full-screen fit + equal layer buttons (user-reported)

Four things reported off one screenshot; the second turned out to be a real
layout bug and the first uncovered another.

1. **"✓ Saved" was permanently on screen.** It's the DEFAULT state, so showing
   it always says nothing. Now the indicator is **silent when saved**, shows
   "• Unsaved changes" while dirty, a spinner while saving, and flashes
   "✓ Saved" for 2.2s after a save before going quiet. Its width is fixed so the
   buttons beside it don't jump.
2. **An explicit 💾 Save button, before Export.** Saving is still automatic; the
   button is reassurance and a way to force the write. Disabled when there's
   nothing to save.
3. **🗑 Delete moved out of Properties and into the top bar, after Export** —
   destructive, so it sits furthest from the button you came to press. Two-step
   confirm inline. `VideoProperties` lost its delete props.
4. **The three layer buttons are now identical.** One `--tl-track-h: 2.6rem`
   drives all three tracks (they were 35 / 32 / 50px — three sizes read as three
   different kinds of thing, which they aren't). The waveform height follows.

**⚠️ The real bug: ~390px of dead space under the timeline.** `.an-nle` used
`grid-template-rows: auto auto minmax(0,1fr) auto`, but **the status strip only
renders when there's something to say.** On a fresh animatic it isn't there, so
every child shifted up a row, the *timeline* inherited the `1fr`, and the panes
were left at content height. My earlier browser test never caught it because a
notice was always showing by the time it measured.
→ `.an-nle` is now a **flex column** with `.an-panes { flex: 1 1 auto }`. Flex
doesn't care how many children there are. **Don't reintroduce a positional row
template with optional children.**
Editor padding also tightened to `0.85rem` (the app's usual `1.8rem` is a lot of
dead margin on a workspace). Measured after: 14px above, 14px below, at 2559,
1920, 1440 and 1280 wide — symmetric framing, nothing wasted.

**⚠️ Second bug, found by the new assertions: a newly created animatic opened
DIRTY and fired a pointless save.** The "have we finished loading?" guard was a
`setTimeout(0)` flag, and that race is lost whenever React invokes the load
effect twice (StrictMode in dev) — the second `.then` landed after the flag was
set and looked like user edits. Reopening an existing animatic was fine, which
is why it hid.
→ Dirtiness is now decided by **comparing a content signature against a baseline
of what's on the server**, not by "did an effect fire". Set on load, updated
after each successful save, and captured *before* the request so an edit mid-
flight correctly stays dirty. Editing a value back to its original now also
reads as saved again. Verified: opening a new animatic fires **zero** PUTs.

- **Verified in the browser** (`tests/e2e_animatic.py`, now **80 checks**): the
  quiet-then-flash-then-quiet save cycle, Save-before-Export and
  Delete-after-Export by measured x-position, Save disabled when clean, the
  delete confirm, no dead space + symmetric framing, and all three layer buttons
  *and* tracks measuring identical. Plus everything it already covered. Backend
  untouched this turn (`git diff` confirms no Python changed beyond last turn's
  `server/animatics.py`); imports, `plan_segments` and the audio media route
  re-smoked green.
- **Gap to close:** the backend suites (`test_animatic`, `test_text_layer`,
  `test_text_api`, `test_duration_exact` — ~150 checks) still live in the
  session scratchpad, which gets wiped between turns. They should be moved into
  `tests/` beside the e2e one.

### 2026-07-30 — Playwright: the app is finally tested in a REAL browser (4 bugs found)

- **Asked for:** install Playwright and test on a real browser. Every session
  before this had to sign off with "NOT viewed in a browser"; that gap is closed.
- **`tests/e2e_animatic.py` (new, 68 checks)** drives Chromium against a live
  API + Vite on non-default ports (8124 / 5199) so it can never touch real data.
  `requirements-dev.txt` holds `playwright`; run instructions are in the test's
  own docstring. Screenshots of every viewport are written to `%TEMP%/pw_test/shots`.
- **It found four real bugs on the first run — all invisible to the build:**
  1. **Timeline gutter labels were mis-aligned.** `.tl-gutter-row:nth-of-type(n)`
     counts *every* sibling `<div>`, and the ruler spacer is the first one — so
     each label got the NEXT track's height and Audio got none at all. Now
     explicit `.tl-gutter-images` / `-text` / `-audio` classes. **Never go back
     to positional selectors there.**
  2. **The waveform never drew, and playback was silent, until a reload.** The
     upload returned 200 and then `GET /animatics/{id}/audio` **404'd**: the
     editor's save is debounced, so for ~900ms the track is on disk but not yet
     ON the project, and that route reads the project. Images already avoided
     this via the raw-upload route; audio didn't. `/media/{upload_id}` now serves
     **image OR audio**, and the client fetches audio by upload id.
  3. **The preview was not the frame shape being exported** — measured 1.66:1 for
     a 16:9 project, so a 16:9 image showed false letterbox bars. `aspect-ratio`
     is silently dropped when a box is constrained on both axes, which is exactly
     what "fit inside this pane" does. The screen is now sized
     `width: min(100cqw, 100cqh * --ar-num)` inside a size-container
     (`.an-screen-fit`), making width definite so the ratio holds. Verified at
     16:9 (913×514 = 1.778) and 9:16 (309×549 = 0.563).
  4. **Whole-video settings became unreachable.** Selecting a frame or a caption
     swapped the Properties pane, and nothing ever deselected — so aspect ratio,
     fps and Delete were gone for the rest of the session. Added a **"← Video"**
     button in the pane header.
- **What the suite covers:** login, the library (two New tiles genuinely side by
  side, Recent/All), all four workspace regions rendering with real sizes, no
  page scroll in either axis, timeline track heights, image upload → frames →
  bars, typing a hold and seeing the bar widen, adding a caption and seeing it
  over the picture *scaled to the frame*, waveform pixels actually lit, the clock
  advancing during playback, Properties following the selection, and **five
  viewports** (1920/1440/1280/1024/390) asserting no horizontal scroll, tracks
  keeping height, the picture staying visible, panes side-by-side above 1180px
  and stacked below — plus zero console errors throughout.
- **Confirmed fixed in the browser:** the reported empty-area-below-the-workspace
  is gone — `document.scrollHeight == window.innerHeight` at every desktop size.
- **Gotcha for the next agent:** `pkill -f uvicorn` does **not** kill a Windows
  python process. The old server keeps the port, the new one fails to bind, and
  you spend twenty minutes testing stale code. Use
  `Get-NetTCPConnection -LocalPort <n> | Stop-Process -Id $_.OwningProcess`.
  Also `sys.stdout.reconfigure(encoding='utf-8')` in any test that prints arrows
  or emoji — the Windows console is cp1252 and will crash the run.

### 2026-07-30 — Responsive pass; editor stops guessing the viewport (user-reported)

- **Reported:** the editor workspace filled only the top of the screen with a
  large empty area beneath, plus a standing instruction: **keep every page
  responsive on every screen**. That's now written up as its own section above
  ("Responsive rules") so it survives past this session.
- **Root of the editor problem: it was doing arithmetic on someone else's
  padding.** `height: calc(100vh - 3.6rem)` hard-coded `.shell-main`'s
  `1.8rem` top+bottom. Any change to that padding, or anything that makes the
  document taller than the viewport, and the workspace is the wrong height with
  dead space under it.
- **Fix — let it take the space that actually exists.** `.shell:has(.an-nle)`
  pins the shell to the viewport, clips `.shell-main`, and the editor becomes a
  flex child with `flex: 1 1 auto; min-height: 0`. No constant, and App.jsx
  doesn't need to know which page is mounted. The old `calc()` stays as the
  fallback for a browser without `:has()`.
- **`100vh` → `100vh` + `100dvh` everywhere** (9 sites). `vh` doesn't shrink as
  a phone's address bar slides away, so every "full height" screen overshot on
  mobile — landing, login, the public shared board and the shell included.
- **Fluid panes:** `clamp(12rem, 15vw, 17rem) / minmax(0,1fr) / clamp(16rem,
  20vw, 22rem)`. A 4K screen gives the side panes more room; a 1280 laptop stops
  crushing the picture between two fixed columns. Timeline height likewise
  `clamp(11.5rem, 24vh, 15rem)`.
- **Three stages, not two:** side panes narrow at **1400px**, the split stacks
  at **1180px**, and a new **`max-height: 620px`** rule releases the
  viewport-pinning entirely — with devtools open, a scrolling workspace beats
  four unusable slivers.
- **Honest limit:** I could not reproduce the empty area itself. The CSS was
  intact, the sidebar scrolls internally, and without a browser I can't tell
  whether the screenshot was a full-page capture or a real overflow. The change
  above removes the *class* of bug regardless — the workspace no longer asserts
  a height, it fills what it is given.
- **Verified:** a stylesheet audit — no unconditional `width`/`min-width` ≥
  320px anywhere, every `100vh` paired with a `dvh` line, the three editor
  breakpoints confirmed by parsing the media blocks, and the primary height path
  confirmed free of any padding constant. `npm run build` clean.
  **NOT viewed in a browser at any size.**

### 2026-07-30 — Animatic editor rebuilt as an NLE workspace (Premiere-style)

- **Asked for:** the editing layout of a real NLE (user sent Premiere Pro
  screenshots) — *"keep now simple, then we go advance"*. So: the shape, not the
  feature set. No tool palette, no multi-track video, no audio meters.
- **The page is now a fixed-height grid, not a scrolling page:**
  ```
  top bar   ← back · title · saved · Export
  status    ← errors / notices / export progress, one line, never shifts layout
  ┌ Media ─┬─ Program ─────────┬─ Properties ─┐
  │ frames │  the picture      │  selection-  │
  │ +audio │  ⏮ ◀ ▶ ▶  0:13    │  driven      │
  └────────┴───────────────────┴──────────────┘
  Timeline (full width) — 🖼 Images / T Text / ♪ Audio
  ```
  `height: calc(100vh - 3.6rem)` (`.shell-main`'s padding), `min-height: 0` on
  every grid child, and each pane scrolls **inside itself**. That's the whole
  point: in an editor the picture must not slide off screen while you drag a
  clip.
- **Properties is one pane with three states** — text clip / frame / whole video
  — so there is exactly one place to look for a setting. The old collapsible
  "Video settings" block and the separate text inspector are both gone into it.
  Selection is made **exclusive** (`selectedFrame` is null whenever a text clip
  is selected, and the timeline clears the other on click), so the pane can
  never show something that isn't selected.
- **Frame properties gained an editable `label`** — it was only ever settable
  from a storyboard before, yet it's what the timeline shows and what
  "burn shot labels" burns in.
- `FrameStrip` takes a `vertical` prop for the Media pane: same component, same
  drag-to-reorder and typed hold, just stacked. Deliberately not a second
  component — the reorder logic must not fork.
- The download button moved into the top bar and turns amber reading
  "⬇ MP4 (out of date)" when the project has been edited since the export.

**⚠️ Two traps worth knowing if you move this layout again**

1. **`--tl-ruler-h` / `--tl-img-h` / `--tl-txt-h` / `--tl-aud-h` now live on
   `.tl-wrap`, the Timeline's OWN root** — they used to be on `.an-timeline`,
   the wrapper the old layout provided. Deleting that wrapper would have left
   every track height undefined and collapsed the whole timeline to nothing.
   Caught by a class audit, not by the build (CSS never errors).
2. Removing dead CSS with a regex over selectors is dangerous: the pass that
   cleaned up the old layout also ate the NEW `.an-prop-actions.an-danger-row`,
   because it matched `.an-danger-row` as a substring of a compound selector.
   Re-audit after any such cleanup.

- **Verified:** a class audit across every `.jsx` and `styles.css` — no JSX class
  is left without a rule (bar two intentional no-style hooks), the `--tl-*`
  variables are confirmed present on `.tl-wrap`, and 19 dead rule-blocks from
  the old layout are gone (CSS is ~2 kB smaller despite the new workspace). A
  structural check confirms all four regions and all three Properties states
  render, and that selection is exclusive. Backend suites still pass;
  `npm run build` clean. **NOT viewed in a browser** — the pane sizing, the
  `100vh` fit and the 1180px stacking breakpoint are unexercised by any test.
- **Deliberately still simple, for the "advance" pass:** one video track and one
  audio track, no clip trimming on the image layer (a frame is a hold, not a
  range), no drag-from-Media-to-Timeline, no keyboard shortcuts beyond
  space/←/→, no snapping, no undo history.

### 2026-07-30 — Rename icon is a pencil now, not a cog (user-reported)

- **Reported:** the ⚙ rename button doesn't read as "rename" at a glance.
- Swapped to **✏️ (U+270F U+FE0F)** on **both** library cards — the storyboard
  one and the animatic one — since they share the same card design and a split
  would be worse than the original problem. Those were the only two rename
  affordances in the app; the animatic editor renames by typing straight into
  its title field, so it needs no icon.
- Tooltips sharpened at the same time: "Rename" → "Rename this storyboard" /
  "Rename this animatic", so hover confirms what the pencil will touch.
- **Verified:** grepped for any remaining ⚙ used as an icon (none), and checked
  the codepoints in both buttons are U+270F U+FE0F rather than trusting how the
  glyph renders in a terminal (the Windows console can't print it at all).
  `npm run build` clean. **NOT viewed in a browser.**

### 2026-07-30 — A ＋ on every timeline layer, not just Text (user request)

- **Asked for:** the same ＋ the Text layer has, on the Audio layer too.
- Added there, **and on Images** — with two of the three carrying it the odd one
  out would have looked like a missing feature. All three gutter rows now behave
  identically: ＋ adds to that layer.
- **The empty band of a layer is now itself the button** ("♪ No audio yet —
  click to add an MP3…"), which is the thing people actually reach for. It
  stops the pointer event so it can't scrub instead, and nothing is lost: with
  no waveform there's nothing there to scrub against, and the ruler still does.
- **One hidden `<input>` per media type, at the editor level.** The audio picker
  used to be a `<label>` wrapping its own input inside the tools row, which the
  timeline couldn't reach; it's now a ref'd input that both entry points click.
  `FrameStrip` keeps its own image picker (it needs one for drop-at-index), but
  both routes end in `addFiles()` — the single place an upload becomes frames.
- **Verified:** a script asserting all three gutter rows render a `tl-layer-add`
  button and that all three `onAdd*` props are wired from the editor.
  `npm run build` clean. **NOT clicked through in a browser.**

### 2026-07-30 — "Your Animatics" rebuilt to match "Your Storyboards" (user-reported)

- **Reported:** the two library pages didn't look like the same product. The
  animatics page had its own header wording, its own card layout and its own
  bespoke `.an-lib-*` styles, while the storyboard page had the New tile plus
  **Recent / All** sections, chips and icon actions.
- **`AnimaticLibrary.jsx` now mirrors `StoryboardLibrary.jsx` structurally** —
  same `workflow-header`, same `card lib-new` tile, the same `renderItem` /
  `renderSection` split, the same loading ghosts and empty-state card, the same
  `chip` meta row and `lib-icon` action row, the same delete confirm. The whole
  bespoke `.an-lib-*` CSS block is **deleted**; both pages now draw from one set
  of `.lib-*` rules, so a change to a card lands on both.
- **Two New tiles sit in the `lib-new-row`** — "New Animatic" and "From a
  Storyboard" — instead of the old mixed grid, so both ways in are one click and
  the sections below hold only real projects.
- Card actions are the animatic equivalents of the board's: ⬇ (download the MP4,
  only when one exists), ▶ Open, ⚙ Rename, 🗑 Delete. There is no share icon —
  animatics have no public link, and inventing one would have meant a new
  token-gated route.
- **The one deliberate difference:** running time on the thumbnail
  (`.lib-badge.time`, bottom-right). A storyboard card has no equivalent, and
  it's the convention every video tile follows.
- Copied two hard-won details from the board library rather than re-learning
  them: per-card state is keyed `"<section>:<job_id>"` (the same card renders in
  Recent *and* All, and a shared key made rename steal focus), and the section is
  a render **function**, not a nested component, or React remounts it on every
  keystroke.
- Also picked up the 5s refresh while an export is running, so a card made this
  session fills in instead of sitting on "Exporting…" until a reload.
- **Verified:** a script comparing the class vocabulary of both files —
  **42 shared layout classes**, with only `lib-share` (boards only) and
  `spinner-inline` (the picker) differing. `npm run build` clean; grepped for
  leftover `an-lib-*` / `an-from-board` references — none. **NOT viewed in a
  browser.**

### 2026-07-30 — Animatic TEXT layer (3 tracks) + an export-length bug found on the way

- **Asked for:** add text to a frame in the animatic, control **how long that text
  stays** on the timeline, and show the editor as **layers — image, text, voice**.
- **Text clips are TIME-based, not attached to a frame.** `AnimaticTextClip` has
  its own `start_ms` + `duration_ms`, so a caption can appear part-way through a
  held image or run across a cut. Pressing **T ＋ Add text** still defaults the
  new clip to exactly the frame under the playhead — "text on this shot" is what
  people mean — but from then on it is free to be dragged and stretched.
- **The timeline is now three tracks** with a fixed label gutter (`🖼 Images` /
  `T Text` / `♪ Audio`) that does not scroll with the tracks. Track heights come
  from CSS variables (`--tl-img-h`, `--tl-txt-h`, `--tl-aud-h`) so the gutter rows
  and the tracks can't drift out of alignment. Text clips: drag the body to move,
  the right edge to stretch, both snapped to 100 ms. A clip hanging past the last
  frame gets a dashed amber border — the FRAMES decide the video's length, so it
  would never be seen.
- **Inspector under the timeline** when a clip is selected: the caption itself,
  numeric start / length, position (top/middle/bottom), align, size (S/M/L),
  backdrop (shaded bar / solid box / outline only), colour, duplicate, remove.
- **Preview and export agree by construction.** The overlay is sized in `cqh`
  (a fraction of the preview's own height) with the SAME divisors the exporter
  uses — 30 / 21 / 14 — so nothing has to be kept in step by hand. `.an-screen`
  gets `container-type: size` to make those units work. "Outline only" draws a
  dark stroke in both, because white text on a pale sketch is invisible otherwise.

**How text is burned in (`plan_segments`)**

- A clip boundary can fall in the middle of a held image, so **the unit of
  rendering is now a SEGMENT — a stretch where the picture AND the visible text
  are both constant** — not a frame. The timeline is cut at every frame edge and
  every clip start/end, and each piece is rendered as its own still. Chosen over
  ffmpeg `drawtext` filters: no escaping or font-path problems, and it reuses the
  Pillow code that was already normalising frames.
- **With no text there is exactly one segment per frame**, so an animatic without
  captions renders precisely as it did before.
- Segments are **cached by (frame, active clip ids)**, so a caption appearing and
  disappearing over one long hold re-renders that picture twice, not per boundary.
- Slivers under 40 ms are folded into their neighbour (a boundary landing almost
  exactly on a cut would otherwise be too short to encode).
- `frame_count` still means PICTURES; the new `segment_count` reports the real
  work done.

**⚠️ Bug this uncovered — the exported video was SHORTER than promised**

- Symptom while testing: a 14 s animatic exported as **13.46 s**; a **single 2 s
  frame exported as a 0.04 s video**; two frames of 1 s each gave 1.04 s.
- **Cause:** the concat demuxer hands over a variable-rate stream (one image,
  held for its declared `duration`), and **`-r 24` on the output does not
  reliably expand those holds into real frames.** Whether it worked depended on
  the exact pattern of durations — which is why the original 3-frame tests passed
  and nobody noticed. `-t` then cut an already-too-short stream, and padding the
  tail made no difference (the input wasn't running out; the holds were being
  dropped).
- **Fix:** resample with the **`fps=` FILTER** (`-vf fps={fps}`), which works off
  the input timestamps and is exact. `-r` is kept so the container is tagged with
  the same rate. Measured worst case is now one frame.
- **This was a pre-existing bug in yesterday's export**, not something the text
  layer introduced — short animatics were affected regardless of text.
- **New `test_duration_exact.py` exists to stop it coming back:** 1/2/3/8 frames,
  all-minimum 100 ms holds, a single 30 s hold, non-frame-aligned holds
  (333/777/1234/2999 ms), text inside a frame / across a cut / overlapping /
  past the end, at 12/24/25/30 fps — every case asserts the REAL file duration
  read back with ffmpeg against what the UI promises.

**Verified (no AI quota spent)**

- `test_text_layer.py` — 38 checks. Segment planning: a clip inside one frame
  splits it in three with the boundaries landing exactly on the clip, a clip
  spanning a cut marks both frames, overlapping clips both apply, a blank caption
  is ignored, a clip past the end is cut not extended, slivers folded. Rendering
  checked **per-pixel band**: bottom text darkens only the bottom, top only the
  top, middle only the middle; large covers >2× small; "outline only" is visible
  on white yet draws no bar; long captions wrap; two captions stack. Export:
  decoded frames at 1 s / 3 s / 5 s prove the text is on screen **only inside its
  window**.
- `test_text_api.py` — 19 checks: save/read-back/reload, a frames-only save does
  not wipe the text, library `text_count`, 422 on negative start and zero length,
  413 over the cap with the good clips left untouched, and the caption present in
  the exported pixels.
- Re-ran `test_animatic.py` (38) and `test_board_export.py` (17, now asserting the
  caption appears at 4 s and not at 1 s or 6 s) — both pass, so the segment
  rewrite didn't regress letterboxing, audio length rules, frame skipping, cancel
  or progress. `npm run build` clean.
- **NOT done:** still never clicked through in a browser — the clip drag/stretch
  interactions and the `cqh` preview sizing are unexercised by any test. One
  manual pass is worth it. Text is one style per clip (no per-word styling), and
  there are no fades on text in or out.

### 2026-07-29 — Every DELETE in the app was reported as an error (user-reported)

- **Reported:** deleting an animatic showed *"Failed to execute 'json' on
  'Response': Unexpected end of JSON input"* and the card stayed on screen.
- **Cause — in the SHARED `request()` helper, not in the animatics code.** A
  `204 No Content` has no body, but FastAPI still labels it
  `content-type: application/json`. `api.js` decided how to read a response from
  the content-type alone:
  ```js
  return ct.includes("application/json") ? res.json() : res;   // ← threw on 204
  ```
  so `res.json()` parsed an empty string and threw. **The delete had actually
  SUCCEEDED** every time — the server was fine; only the client's reading of the
  reply failed, and the thrown error skipped the line that removes the card.
- **This affected every 204 endpoint, not just animatics:** `deleteStoryboard`,
  `deleteJob`, `deleteApiKey` and `deleteAnimatic` all go through `request()`.
- **Fix:** check for a body before reading one — `204`/`205`, plus a
  `content-length: 0` belt-and-braces guard, return `null`. Callers already
  ignore the return value of a delete, so nothing else changed.
- **Why the earlier animatics tests missed it:** they called the API through
  `TestClient` and asserted on status codes, so no test ever ran `api.js`. The
  new test closes that hole — it **imports the real `client/src/api.js`** (with
  `import.meta.env` and `localStorage` shimmed) and drives it against a **real
  uvicorn** on port 8123, so the browser's exact code path is exercised.
- **Verified:** 10 checks — the reported delete no longer throws and the item
  really leaves the library; deleting a saved API key (the same 204 shape)
  works; POST/GET/PUT still parse their JSON normally; and 404s still throw with
  the server's own message rather than being swallowed by the new early return.
  Plus a **negative control**: the same test run against a copy of `api.js` with
  the guards removed reproduces *"Unexpected end of JSON input"* exactly, and
  shows the record being deleted anyway — proving both the diagnosis and that
  the test would catch a regression. `npm run build` clean.

### 2026-07-29 — Storyboard → Animatic: timeline editor + MP4 export (NEW WORKFLOW)

- **Asked for:** a simple editor to sequence storyboard images, give each one its
  own hold ("image 1 = 2 sec, image 5 = 5 sec"), drop an MP3 under it to see how
  the cut plays against the audio, and export a video. Two ways in: a button on a
  finished board, and manual upload of images one-by-one or as a sequence.
- **The workflow is now live** — `storyboard-to-animatics` went from `soon` to a
  real screen in `Sidebar.jsx`, and `App.jsx` routes it.
- **It spends NO AI quota.** Images, timing, audio, one video encode. Worth
  saying out loud, because every other workflow here bills per image.

**Three decisions that shaped everything else**

1. **Preview in the browser, export on the server.** Browser-side recording
   (`MediaRecorder`) runs in REAL TIME — a 3-minute animatic takes 3 minutes and
   yields WebM. Server ffmpeg does the same job in seconds and yields H.264 MP4.
   `imageio-ffmpeg` is in `requirements.txt` so there is **no system install**;
   resolution order is `FFMPEG_BINARY` → PATH → the bundled binary, and
   `GET /health` now reports whether one was found.
2. **Audio is the playback clock, never a timer.** `AnimaticEditor` reads
   `<audio>.currentTime` on every `requestAnimationFrame` and picks the frame
   whose slice contains it, so pictures cannot drift from sound — which is the
   one thing the feature exists to let you check. If the track ends before the
   sequence does, it falls back to a wall clock anchored fresh every frame, so
   the handover is seamless instead of freezing.
3. **A saved animatic IS a job** (`JobKind.ANIMATIC`) — the same call the
   storyboard library made. The library is a view over `GET /animatics`, and
   ownership, persistence and polling came for free. The job's **status describes
   the EXPORT**: `queued` = draft never exported, `running` = encoding,
   `succeeded` = a video exists, `failed` = last export failed (project still
   editable).

**Backend**

- **`animatic.py`** — every frame is normalised with **Pillow** to the exact
  video size BEFORE ffmpeg sees it. Uploads arrive at every size and the concat
  demuxer needs them uniform; doing the letterbox/crop/label work in Python keeps
  it out of an unreadable filter graph. Then one concat-demuxer pass.
  - **Length is set by the FRAMES, not `-shortest`.** Output is cut at `-t <sum
    of holds>`, so a short track can't truncate the video and a long one can't
    stretch it. Both are tested against real files.
  - The concat list repeats the last image with no duration — without that quirk
    the demuxer drops the final frame and the animatic ends a picture early.
  - Filenames in the list are RELATIVE to the list file, which sidesteps every
    Windows path-quoting problem the demuxer has.
  - Progress comes from `-progress pipe:1` (`key=value` lines, stable across
    ffmpeg versions) rather than scraping human-readable stderr; stderr is
    drained on a thread so a long error log can't deadlock the pipe.
  - A frame whose file has vanished is **skipped and reported**, not fatal.
    Cancel is checked between frames and during encoding (`cancel.py`, shared
    with both other workflows).
- **`server/animatics.py`** — frames reference board panels **by index, never by
  copy**, and are resolved through the board's ACTIVE style variant on every
  request. Re-draw or re-style a panel and the animatic picks it up with nothing
  to re-import (verified by decoding the exported pixels).
  - **The security bit worth knowing:** frames are user-editable JSON, so a
    crafted `storyboard_id` would otherwise read another account's panels.
    `_resolve_frame_path` checks `board.owner == job.owner`, and `upload_id` must
    match `^[a-f0-9]{6,32}$` before it becomes a filename.
  - Saving is refused (409) while an export runs — the encoder is reading those
    exact frames. Editing after an export marks the video `stale` rather than
    deleting it; the old cut is still worth having until the new one exists.
- **`server/common.py` (new)** — `get_owned_job` / `board_dir` / `variants_of` /
  `panel_path` moved out of `main.py` so the two route modules don't import each
  other. `main.py` imports them under their old private names, so its ~30 call
  sites are untouched.

**Client**

- `AnimaticEditor` + `FrameStrip` + `Timeline` + `Waveform` + `AnimaticLibrary` +
  `StoryboardToAnimatics`. **No new npm dependency** — the waveform is decoded
  with WebAudio and drawn on a canvas; peaks are computed ONCE at 4000 buckets
  and re-bucketed for the canvas, so zooming never re-decodes the MP3.
- **A hold can be set three ways**, because different moments want different
  ones: typed on the frame card, dragged on the timeline (snapped to 100 ms), or
  bulk "set all to 1/2/3/5s". **"⇔ Fit frames to audio"** scales every hold and
  puts the rounding remainder on the last frame so the total is EXACT.
- Autosave is debounced 900 ms, paused during an export, and flushed on unmount.
- Multi-file drops are sorted by filename with `numeric: true`, so `01.png …
  12.png` lands in the order the user named them.
- Defaults chosen (and stated in the UI): **letterbox**, 1920-long-edge, 24 fps,
  shot labels **off**. A cropped storyboard frame is a frame you can't read.

**Verified — no AI quota spent, real files throughout**

- `animatic.py`: 38 checks. Real MP4s written and **read back with ffmpeg**:
  8.5s of holds → a genuinely 8.5s file; 20s audio doesn't stretch it; 3s audio
  doesn't truncate it; 9:16 really is 1080×1920; letterbox bars and cover-crop
  checked per-pixel; missing frames skipped and reported; cancel writes nothing
  and cleans up; progress is monotonic and ends at 100; a throwing progress
  callback doesn't kill the export.
- API: 63 checks through a real `TestClient` — upload/save/serve, export → poll →
  download, stale-after-edit, from-board (drawn panels only, failed panel skipped
  without renumbering, board aspect inherited), cross-user 404s on every route,
  traversal refused, a frame pointing at another user's board serves nothing and
  can't be exported, all four 409 guards, 413/422 limits, and animatics staying
  out of both other lists.
- Board→video: exported a restyled board and **decoded the pixels** — the active
  variant's art is what lands in the MP4, and a re-drawn panel appears on the
  next export.
- `npm run build` clean (60 modules).
- **NOT done:** never clicked through in a browser. The drag interactions
  (reorder, edge-resize, playhead scrub) and the waveform are unexercised by any
  test — worth one manual pass. No transitions/crossfades, no Ken Burns, no
  burned-in dialogue, single audio track only.

**⚠️ Incident during this work — read if CSS looks wrong.** `git checkout --
client/src/styles.css` was run to undo a bad encoding-mangled append; the repo
was NOT clean (the session's git snapshot said it was), so it destroyed 38 lines
of **uncommitted** CSS: `.style-note` and the four `.scene-divider*` rules from
the 2026-07-26 scene-division work. All five were **recovered byte-for-byte from
`client/dist/assets/*.css`** (the previous production build) and re-expanded at
the end of the file, where they had been. Verified: every class present in that
build is present in the source again. The only thing not recoverable is any
comment that sat above them (comments are stripped from a build) — one 2-line
comment was written fresh. **Commit early; the working tree here carries
uncommitted work from several sessions.**

### 2026-07-26 — Scenes are actually divided now (everything was "Scene 1")

- **Reported:** every panel on the finished board said SCENE 1 — the AI wasn't
  dividing the script into scenes at all.
- **Cause:** the breakdown prompt said only *"scene_number: which scene it
  belongs to (start at 1)"*. It never said what a scene IS, so for a short
  continuous story the model put everything in scene 1 — technically obeying the
  instruction. `shot_number` was also asked for as a running index across the
  whole script, which is not how boards are labelled.
- **Prompt now defines the boundary:** a scene is one continuous action in ONE
  place at ONE time; start a new scene on a location change, a time jump, or a
  clearly separate beat; returning to an earlier location later is a NEW number;
  numbers only go up. With the explicit example that forest-by-day → tree-at-
  night → next-morning is three scenes, and the caveat that a script which truly
  never leaves one place is legitimately one scene. `shot_number` now restarts
  at 1 inside each scene.
- **`_normalise_scenes()` repairs it deterministically** — a prompt alone can't
  be trusted for something this structural:
  1. Renumber 1..N **by run**, so gaps vanish and revisits become new scenes:
     `[1,1,5,5,1] → [1,1,2,2,3]`.
  2. **The targeted repair for the reported bug:** if that still leaves ONE scene
     for the whole board but the shots name ≥2 distinct locations, scenes are
     re-derived from consecutive runs of `location` — a change of place is the
     one boundary inferable from the data with confidence. A single location
     stays a single scene, so no breaks are invented.
  3. `shot_number` rewritten as position within its scene.
- **Review page now SHOWS the division:** a full-width scene divider
  (`grid-column: 1 / -1`) appears wherever the scene changes, with the location
  and the shot count for that scene; each card's tag reads "Scene 2 · Shot 1".
  The board tile and the PDF pill pick up the corrected numbers for free.
  The board keeps its global "Shot 7" label — that's what the ZIP filenames use.
- **Verified** on the exact failing shape (6 shots, all scene 1, three locations)
  → 1/1, 1/2, 2/1, 2/2, 2/3, 3/1; plus single-location stays one scene, an
  already-correct breakdown is left alone, `[1,1,5,5,1]` normalises, missing and
  junk values don't crash, and an empty shot list is a no-op. A full-chain check
  confirms scene numbering doesn't disturb script-line tracing and the result
  still satisfies the `Shot` API model. `npm run build` clean. No live breakdown
  run — how well the model divides scenes on its own is now backed by the
  location fallback either way.

### 2026-07-26 — "Rough Sketch": new DEFAULT style that skips cast + props

- **Asked for:** a real storyboard-thumbnail look (user supplied reference
  images: grey-marker animation boards, Wall-E thumbs, pencil fight beats) as a
  NEW style called Rough Sketch — the old `sketch` kept — made the default, and
  with the cast and props/backgrounds steps **removed for that style only**.
  Every other style keeps today's flow untouched.
- **The cost question they asked first, answered honestly:** per-image billing
  doesn't care about style — a rough sketch and a photoreal frame at the same
  size cost the same. Simple is cheaper *indirectly*: there's no rendered detail
  to come out wrong, so far fewer re-draws, and with Rough Sketch there are also
  **zero reference images to generate** (a 15-shot board with 3 refs drops from
  ~18 images to 15).
- **`gemini_client.py`:** new `rough-sketch` entry in `_STORYBOARD_STYLE_PROMPTS`
  — deliberately the longest one in the dict, because it has to fight the
  model's pull toward rendering: white paper, greyscale, flat marker tones in
  **two or three values**, visible construction lines, silhouette-first posing,
  background only where the shot needs it, and explicit no-colour /
  no-photorealism / no-fine-detail. The old `sketch` string is **byte-identical**
  — verified by assertion, not by eye.
- **`REFERENCE_FREE_STYLES`** (a Set in `ScriptToStoryboard.jsx`) is the single
  definition of "this style needs no reference images"; `skipsRefs()` reads it
  from the EFFECTIVE style, so switching style anywhere — including inside the
  pre-flight modal — re-routes the flow immediately. It drives four things:
  `handleReviewNext` launching straight past cast/props, the review button
  reading "🎬 Generate panels" instead of "🎭 Next: cast", the modal's
  cast/props section becoming an explanation, and the how-it-works list losing
  step 4 (and renumbering) so the default user isn't promised a step they'll
  never see.
- **`startStoryboard` strips refs when `skipsRefs()`** even if some were already
  set up and the user then switched style in the modal — otherwise the modal's
  "not used by this style" would be a lie.
- `DEFAULT_STYLE` is now `rough-sketch`; `RESTYLE_OPTIONS` on the board offers it
  too, so an expensive board can be re-cast down into thumbnails. A note under
  the style chips states the trade-off both ways (rough = no cast step, cheap /
  detailed = locked refs, more images).
- **Unchanged on purpose:** the WORLD block still reaches every Rough Sketch
  panel — culture and period matter just as much in a grey thumbnail, and it
  costs nothing.
- **Verified:** the prompt builder resolves `rough-sketch` to the new art
  direction, `sketch` to the old string unchanged, `cinematic` untouched, all 18
  style entries intact; `npm run build` clean. **No image was actually generated**
  (that costs quota) — how closely the model matches the reference boards is
  unmeasured. Draw 2 panels and Stop before committing to a full board.

### 2026-07-26 — "⏹ Stop generation" in Text to Image too

- **Asked for:** the same stop button in the character workflow.
- **`cancel.py` (new)** now owns the flag registry for BOTH pipelines —
  `request_cancel` / `is_cancelled` / `clear_cancel`, a lock + a set of job ids.
  `storyboard_pipeline` re-exports the three names (callers already import them
  from there), so yesterday's board Stop is unchanged. Flags are per-process:
  a multi-process deployment would have to move them into the job store.
- **`pipeline.py` takes `cancel_check` — a CALLABLE, not a job id.** It's also
  the CLI entry point and has no concept of a job; the worker passes
  `lambda: is_cancelled(job_id)`. Checked at the top of the part loop, so a stop
  costs nothing beyond the sheet already being drawn. `_stop_requested()`
  swallows exceptions from the callback — a broken check must never kill a run.
- **What a stopped character run does:** keeps every finished part, still zips
  them (they're real and downloadable), **skips Meshy entirely** (3D is the most
  expensive step and stopping means spend nothing more), and returns
  `stopped: True`.
  - **The empty case matters:** stopped before ANY part finished used to fall
    into `return {"error": "No sheets generated"}`, which the worker turns into
    a FAILED job. A stop is a choice, not a failure, so that path now returns a
    proper stopped summary (`zip: None`) instead.
- **`POST /jobs/{id}/stop`** — generic and owner-scoped. `/storyboards/{id}/stop`
  now delegates to the same `_request_stop()` helper, so both routes share one
  set of guards (409 unless RUNNING/QUEUED). A storyboard can be stopped through
  either route.
- **Client:** `api.stopJob()`; `JobDetail` shows the button in a new `.jp-foot`
  row inside the progress block (parts-done count left, Stop right), and prints
  "⏹ You stopped this generation — N of M parts were made" afterwards, read from
  `job.result.stopped`.
- **Verified** with the image calls faked out (no quota): a 10-part run stopped
  after 3 → **7 parts never drawn**, the 3 kept and zipped, `stopped=True`, no
  error, only the in-flight part still finished. Then: the flag doesn't leak
  into the next run, and the CLI path (no `cancel_check`) behaves exactly as
  before. Endpoint guards: cross-user → 404, finished job → 409, a rejected stop
  leaves no flag, and the board route still works through the shared helper.
  `npm run build` clean; backend imports clean. NOT clicked through in a browser.

### 2026-07-25 — The two workflows no longer share a job list

- **Reported:** storyboard projects were showing up in the Text-to-Image
  workflow's "Your jobs" list (a board titled "A hunter accidentally worshipped
  Lord Shiva…" sat next to a character run). The two workflows must stay apart.
- **Cause:** `GET /jobs` returned every job the user owned, of every kind, and
  both `JobList.jsx` and `Home.jsx` ("Recent work") rendered the lot. Storyboard
  jobs there were also broken, not just misplaced: clicking one opened the
  Text-to-Image job detail, and its ⬇ Download called `/jobs/{id}/download`,
  which needs a `result.zip` a storyboard has never had.
- **`JobStore.list()` takes `kinds`** now (base + memory + Firestore), so the
  filter happens BEFORE `limit`. That also fixes the mirror-image bug in
  `GET /storyboards`, which fetched `limit` jobs and filtered in Python — a burst
  of character runs could push a board off the page before it was considered.
  Tested: a board is still found behind 30 character jobs.
- **Firestore deliberately filters in PYTHON, not `where("kind", "in", …)`.**
  A second equality filter next to the owner filter and the `created_at`
  ordering needs another composite index, and any deployment without it would
  start throwing FailedPrecondition at runtime. It over-fetches (`limit * 4`,
  capped at 500) and trims instead. Don't "optimise" this into a `where` clause
  without creating the index first.
- **`GET /jobs?kind=generate,meshy`** — comma-separated, validated against
  `JobKind` (400 on anything else). Omitting it still returns all kinds, so
  nothing else that calls the endpoint changes behaviour.
- **Client:** `api.CHARACTER_JOB_KINDS = ["generate", "meshy"]` is the single
  definition of "what the Text-to-Image workflow owns"; `JobList` and `Home`
  both pass it. 3D (meshy) jobs stay in that list on purpose — they're
  submissions for a character run, and the list already labels them "· 3D".
- **Verified:** with a mixed store — the job list returns only the character +
  meshy jobs, the library returns only the board, an unfiltered call still
  returns all three, a bogus kind is a 400, another user's board is invisible to
  both, and the board survives 30 character jobs at `limit=5`. `npm run build`
  clean; grepped for any remaining unfiltered `listJobs(` caller — none.

### 2026-07-25 — "⏹ Stop generation" on the board

- **Asked for:** a stop button next to "Download assets (ZIP)" — if the first
  one or two panels come back wrong, kill the run instead of paying for the
  other thirteen.
- **`storyboard_pipeline.py`** owns the cancel registry (`request_cancel` /
  `is_cancelled` / `clear_cancel`, a lock + a set of job ids, in-process next to
  the worker pool that reads it). `_render()` checks the flag **first thing**, so
  every panel still queued in the ThreadPoolExecutor returns without calling the
  image API. `run_storyboard` clears the flag on entry (a stale stop must not
  kill the next run) and again on exit, and returns `stopped` in its result.
- **The honest limit, stated in the UI:** an HTTP call already in flight cannot
  be un-sent, so the 1–2 panels mid-request still finish. The button reads
  "Stopping…" until the job goes terminal.
- **Skipped panels are left `url=None, failed=False`** — not marked failed. The
  board already renders that state as "✏️ New panel" with "✨ Generate this
  panel", so a stopped board is directly resumable, one panel at a time.
- **`POST /storyboards/{id}/stop`** — owner-scoped, 409 unless RUNNING/QUEUED.
- **Deliberate call — a stopped run is marked SUCCEEDED, not FAILED.** There is
  no CANCELLED in `JobStatus`, and adding one would mean touching every
  terminal-state check in the board, the library and the board guards. The
  panels it did draw are real and downloadable, so the job genuinely finished;
  honesty is carried by `result["stopped"]`, which the board reads to show
  "⏹ You stopped this generation — N of M panels drawn" and which the progress
  line reports as "Stopped by you". Revisit if a real CANCELLED status is ever
  needed elsewhere.
- Restyle stops the same way (`_compose(panels, stopped)` in `worker.py`).
- **Client:** the toolbar now renders **while generating too** (it used to need a
  drawn panel), so Stop is reachable from the first suspicious panel; the ZIP
  button is gated on `okCount > 0` so it can't offer an empty zip. A `useEffect`
  clears `stopRequested` when the run ends, so a later re-style doesn't open
  with the button stuck on "Stopping…".
- **Verified** with a fake slow generator (no quota spent): 20 shots, Stop after
  ~1s → **6 calls made, 14 panels never requested**, `stopped=True`, skipped
  panels not marked failed, the flag cleared so an immediate re-run draws all 3
  of its shots. Endpoint guards checked: cross-user → 404, finished board → 409,
  and a rejected stop leaves no flag behind. `npm run build` clean; backend
  imports clean. NOT clicked through in a browser, and not tested against the
  real image API.

### 2026-07-25 — ZIP: images named after the thing, not the board title

- **Reported:** the folders in the assets ZIP were right, but every image was
  prefixed with the board title (`Postmarked After Death_character_01_Lubdhaka
  .png`). The user wants the name they see on the page — `Lubdhaka.png`.
- **`GET /storyboards/{id}/bundle`** now names files as the app labels them.
  The folder already says what kind of thing it is, so the title and the
  `character_`/`prop_`/`background_` word were pure noise:
  ```
  panels/Shot 03.png     characters/Lubdhaka.png
  props/Bilva Bel Tree.png   backgrounds/Dense Forest.png
  Postmarked After Death.pdf
  ```
- **Two deliberate calls:**
  1. **The PDF keeps the board title.** It isn't one image — it IS the board, and
     it lands at the zip root where "Storyboard.pdf" would be meaningless in a
     downloads folder.
  2. **Panels now carry the BOARD's number** (`index + 1`), not a contiguous
     count over drawn panels. This REVERSES the 2026-07-24 decision to close
     gaps: a failed panel now leaves `Shot 02.png` missing. That's the point —
     the name has to match the tile on screen, and a gap says a picture really is
     missing rather than silently renumbering the rest.
- **Collision safety:** two names can clean to the same string ("Shiva." and
  "Shiva"), and a zip with duplicate entry names is corrupt. `_unique()` per
  folder appends " (2)", so nothing is silently overwritten or dropped.
- **Verified** by building real PNGs + refs on disk, calling the endpoint and
  reading back the ACTUAL zip entry names: panel 2 fails and is absent while 3
  and 4 keep their board numbers, the two Shivas come out as `Shiva.png` +
  `Shiva (2).png`, punctuation is cleaned (`Bilva (Bel) Tree` → `Bilva Bel
  Tree.png`), the uploaded ref stays out, no image carries the title, and the
  PDF still does. Backend imports clean.

### 2026-07-25 — Script panel on review; per-shot quotes fixed; even shot cards

Three user-reported issues from the review step, one of them a real bug.

**1. Every shot showed the SAME script text (the bug).** All five cards read
"FROM YOUR SCRIPT · LINE 1" with the identical paragraph.
- **Cause:** `_flatten_script()` mapped each character to its LINE index, and
  `_attach_script_lines()` then displayed the whole line. The user's script is
  one long paragraph on one line, so every shot resolved to line 1 and showed
  the entire paragraph. The matcher was right; the display granularity was wrong.
- **Fix:** the flattener now maps each normalised character back to its
  **original character offset**, so a span can be sliced out of the middle of a
  paragraph. `script_line` is now EXACTLY the matched passage — one sentence per
  shot — and the line number is derived by counting newlines before the offset.
  `MAX_EXCERPT_LINES` (12) is replaced by `MAX_EXCERPT_CHARS` (420), trimmed at
  a word boundary with an ellipsis.
- `_find_span()` gained `since`: shots run in reading order, so a match at or
  after the previous shot's end is preferred, with a global search as fallback —
  a repeated phrase resolves forward instead of snapping back to occurrence one.
- Prompt hardened too: quote only the part that becomes THIS panel, each shot a
  different passage, moving forward through the script.
- **Verified** with the exact failing shape (one-paragraph script, 5 shots): five
  distinct sentences, each `in` the script verbatim. Multi-line scripts still
  report true line numbers (3 and 5 in the fixture); hallucinated quotes still
  rejected; runaway quotes trimmed. The earlier 7-case test still passes (its
  whole-line assertion was updated to a substring check — the behaviour it
  encoded is what changed).

**2. "📄 Your script" panel on the review step** (`ScriptPanel.jsx`), under the
World card. Line-numbered and scrollable, `<details open>` so it collapses. The
numbers are the point: they're what each card's "LINE n" refers to.
- The resolved script text (pasted OR read out of an uploaded file) is now kept
  in `scriptText` state, sent with `POST /storyboards`, stored in
  `params["script"]` **capped at `MAX_STORED_SCRIPT_CHARS` = 200k** (Firestore
  has a 1 MB document limit), and returned by `/project` so a duplicated board
  still shows its script. Display only — never fed to any model.

**3. Ragged shot cards.** `.sb-review .shot-list` used `align-items: start`, so a
shot with no cast chips sat visibly shorter than its neighbours. Now `stretch`,
with `.shot-card` a full-height flex column and `.shot-chars` pushed to the foot
(`margin-top: auto`) so chip rows line up across the grid. The in-card quote is
capped at 5.6rem so context can't dominate the prompt.

**Verified:** matcher tests above; a round-trip test proving script + world
survive create → project, that an oversized script is capped rather than
rejected, and that a board with no script still works; `npm run build` clean (54
modules). NOT viewed in a browser, and no live breakdown was run — whether the
model now returns five DIFFERENT quotes is prompt-dependent and unmeasured, but
even if it repeats one, each shot now shows only the matched sentence.

### 2026-07-25 — WORLD of the story: culture/period now drives every image

- **Reported (a real quality bug):** a Shiva Purana script produced a cast
  reference for Lubdhaka — an ancient Indian hunter — that looked like a white
  Western man. The user asked for the breakdown to capture the story's culture
  once and apply it to every generated image.
- **Cause:** nothing in any image prompt said where or when the story is set.
  The cast description the model wrote ("a lean, rugged hunter in simple, worn
  forest attire") named no ethnicity or period dress, and image models default
  to Western/European faces and clothing when not told otherwise. The panels,
  props and backgrounds had the same hole.
- **`script_breakdown.py`** now returns a **`world`** block alongside shots /
  characters / assets: `setting` (place+period), `culture` (cultural/religious
  tradition), `ethnicity` (what the people look like), `wardrobe`, `environment`,
  `notes` — `WORLD_FIELDS` is the one list, `_coerce_world()` normalises it.
  The system instruction tells the model to read this off the script's names,
  places, deities and festivals ("Lubdhaka + Shiva Purana = ancient India")
  and never fall back on a Western default. Belt and braces: **every character
  description must now state ethnicity and period-correct clothing**, and asset
  descriptions must be region-correct — so even a dropped world block leaves the
  cue in the text.
- **`gemini_client.build_world_context()`** is the single place that turns that
  block into prompt text, and it is prefixed onto **all four** image paths:
  panels, character references, prop references, background references. Naming
  the culture isn't enough on its own, so it ends with an explicit rule: *"Do
  NOT default to Western/European faces, dress or settings."* An empty/missing
  world returns `""` — prompts are then byte-identical to before.
- **Plumbing:** `World` model in `schemas.py`; carried on `ReferenceRequest`,
  `AssetReferenceRequest`, `StoryboardCreateRequest`, `ScriptBreakdownResponse`
  and `StoryboardProject`. Stored in job `params["world"]`, so **re-style and
  single-panel redraw stay in the same world** rather than reverting — this was
  the easy thing to miss. Duplicate gets it back via `/project`.
- **Client:** `WorldSetting.jsx` — an **editable** card ("🌍 World of your
  story") on the review step, and the same component collapsed inside the
  pre-flight modal. Editable on purpose: the AI's reading is a starting point
  and the writer is the authority on their own world. `world` lives in
  `ScriptToStoryboard` state, is passed to the cast and props steps (so every
  reference call carries it), and is part of `currentSig()` — editing it marks
  the board out of date, exactly like editing a shot.
- **Verified (no API spend):** the real generation client was replaced with a
  stub that captures the prompt, and all four paths were checked for the
  ethnicity, the culture AND the anti-default rule — all four carry them; a
  `None`/`{}` world provably adds nothing. A second test drove the endpoint
  functions directly with stubbed generators: the world reaches the character
  ref, the asset ref, the worker kwargs, the stored job params and the Duplicate
  payload, and a request with no world stores `{}`. `npm run build` clean (53
  modules), backend imports clean.
- **NOT verified:** no real image was generated (that costs quota), so how much
  the prompt actually moves the model is unmeasured — worth one Lubdhaka run to
  confirm. If a character still comes out wrong, the ethnicity field is editable
  on the review step and applies to the next generation.

### 2026-07-25 — Each shot now shows the SCRIPT LINE it came from

- **Asked for:** on the review shot card, show the writer's own script line above
  the AI's image prompt, so it's obvious which line of the script became which
  panel. Order requested: script line box on top, then the prompt, then camera /
  location.
- **The data didn't exist** — the breakdown returned a description and nothing
  tying it back to the source text. `script_breakdown.py` now asks for
  `script_excerpt` (a verbatim quote) per shot, in both the prompt and the
  response schema.
- **Quotes are NEVER trusted — this is the important part.** Models paraphrase,
  so `_attach_script_lines()` matches every quote back against the real script
  and replaces it with the actual lines found there:
  - `_flatten_script()` builds a whitespace/case-normalised one-line script plus
    a per-character map back to line indexes.
  - `_find_span()` tries an exact match, then anchors on the longest word-PREFIX
    that IS present and stretches to the longest findable word-SUFFIX (models
    drift at the tail of a quote more than the head). The result is rejected
    unless it covers ≥50% of the quote, so a coincidental phrase can't pass.
  - No match → the shot carries **no** script line. A blank box is honest; an
    invented "your script says…" is not.
  - Runaway quotes (the model handing back half the script) cap at
    `MAX_EXCERPT_LINES` = 12.
  - The raw `script_excerpt` is popped, so the unverified quote never reaches
    the client.
- Shots gain `script_line` + 1-based `script_line_start`/`script_line_end`
  (`schemas.py: Shot`), which flow through `POST /storyboards` into `params`, so
  a saved board and Duplicate keep them.
- **Client:** `ScriptLineBox.jsx` (new, shared) renders the box — gold left
  spine, "📄 From your script · line 12", `white-space: pre-wrap` so the writer's
  own line breaks survive, and it renders **nothing** when there's no verified
  text (hand-added shots included). Used on the review card *and* in the
  pre-flight modal. The review card's prompt got an "Image prompt" label so two
  stacked boxes can't be confused.
- **Deliberately NOT done:** the board tiles and the PDF don't show script lines
  — the ask was about the review panel, and `storyboard_pipeline` doesn't copy
  the field onto panels. Adding it there is a one-line pipeline change plus the
  same component if it's wanted later.
- **Verified:** an offline matcher test (no API call) over 7 cases — exact
  multi-line quote, exact single line, drifted tail, case+whitespace differences,
  a **hallucinated quote correctly rejected**, an empty quote, and a
  whole-script dump capped at 12 lines; every reported line was checked byte-for-
  byte against the source. Plus a pydantic round-trip test proving the fields
  survive breakdown → response → create-request and that `script_excerpt` never
  leaks. `npm run build` clean (52 modules). The live model has NOT been asked
  for a real breakdown (that costs a text call) — how *often* it quotes verbatim
  enough to match is still unmeasured; the fallback is a missing box, not a
  wrong one.

### 2026-07-25 — Pre-flight modal: confirm (and re-pick the frame) before drawing

- **Reported:** "when I enter this page I see automatically generate all images" —
  reaching the board started 15 generations with no confirmation. The user wanted
  a final reminder showing every prompt with its cast/props/backgrounds plus
  genre/style/aspect, editable, then an explicit Generate. Their earlier 9:16
  problem is solved by this: the frame is now changeable at the last moment.
- **Where generation actually started (the cause):** `startStoryboard()` POSTs
  `/storyboards`, which *is* the generation — and the review / cast / props steps
  all called it directly, then navigated to the board. The board page looked like
  the culprit but only ever polled a job that was already running.
- **`PreflightModal.jsx` (new):** header (panel count + "changing it here costs
  nothing"), a Settings block (style + genre selects, aspect chips, custom text
  boxes for each), a Cast/props/backgrounds grid showing each name's thumbnail
  and whether a locked reference is really going along, and every shot as an
  **editable** prompt with its Scene tag, camera, location, cast chips and
  prop/background badges. Scrolls in the middle; head and Generate stay put.
- **`ScriptToStoryboard.jsx`:** new `preflight` state holds `{charRefs,
  assetRefs}` — the launch the user asked for but hasn't confirmed. All three
  launch paths (review with no cast/props, cast→done, props→done) now call
  `requestLaunch()`; **`startStoryboard` is called from exactly one place, the
  modal's Confirm.** Grep for it before adding a fourth path.
  - The modal is built once and dropped into the review / cast / props returns —
    it's a fixed overlay, so it renders over whichever is on screen.
  - Settings write straight to the existing form state, so `currentSig()` /
    `boardUpToDate` and the POST pick the new aspect up with no extra plumbing.
  - **On failure the modal STAYS OPEN** with the error, because `preflight` still
    holds the chosen references — pressing Generate again just retries. Cancel
    lands on review.
- Aspect is still baked in at generation time (the model is prompted with it and
  the result is centre-cropped), so the finished board can't be re-framed — the
  modal says so. The variant-based re-frame plan from earlier today is unbuilt
  and now much less urgent.
- **Verified:** `npm run build` clean (51 modules — the new component is in), and
  grepped every `startStoryboard` / `requestLaunch` call site to confirm no path
  bypasses the modal. The modal has NOT been viewed in a browser.

### 2026-07-25 — Board tiles + PDF now show the SCENE number (user-reported)

- **Reported:** on the final board every tile read only "SHOT 1" — the scene was
  nowhere, even though the Review step shows "Shot 1 · Scene 1".
- **The data was already there** — `storyboard_pipeline.build_storyboard` puts
  `scene_number` on every panel and it survives into `job.result.panels`. Only the
  board's caption never rendered it.
- `StoryboardBoard.jsx`: the caption head now renders
  `Shot N` + `<span className="board-scene">Scene X</span>`, mirroring
  `.shot-index`/`.shot-scene` on the review card. Rendered only when
  `scene_number` is set, so an older board without it doesn't print "Scene
  undefined". `.board-shotnum` became a baseline flex row (was `display:block`)
  with a `.board-scene` muted tag beside it.
- `storyboard_pdf.py`: the PDF *did* already draw "SCENE n", but as 16px MUTED
  grey that vanished next to the bold shot label. New `_scene_pill()` draws it as
  an outlined tinted pill (`SCENE_BG/LINE/INK` — deliberately NOT gold; the cast
  chips own gold in this layout, and two gold pills on one row read as the same
  kind of thing). Same conditional: no scene → no pill, no layout shift.
- **Bug fixed on the way:** `POST /storyboards/{id}/panels/insert` hardcoded
  `scene_number: 1`, so a panel inserted in the middle of scene 3 claimed scene 1
  on the board and in the PDF. It now inherits from the panel it displaces (or
  the one before it when appending).
- **Not changed:** the public shared board (`PublicStoryboard.jsx`) still shows
  "Shot N" only — the public payload deliberately exposes just the drawn panel
  indexes, and adding scene numbers there means widening what a share token
  leaks. Say so before "fixing" it.
- **Verified:** rendered a 6-panel sample PDF and **looked at page 1** — pills
  read clearly at Shot 1–5 (scenes 1/1/2/2/3) and Shot 6, whose `scene_number` is
  None, correctly has none. `npm run build` clean (50 modules); backend imports
  clean. The board tile itself was NOT viewed in a browser.

### 2026-07-24 — Stage G: "Your Storyboards" project library (save / reopen / share)

- **Asked for:** a saved-project library like the reference mock — a New Storyboard
  tile plus cards for past boards, so a returning user lands on their old work.
  Agreed with the user: **finished boards only** (no draft-resume), card actions =
  delete + rename + duplicate + share link, and the title comes from a new field
  on the form.
- **Key decision — a saved project IS a storyboard job.** Storyboard jobs were
  already persisted per-owner with their shots, style and panels, so the library
  is a *view* over `GET /storyboards` rather than a second store that could drift.
  No migration, and old boards show up in the library for free.
- **Backend**
  - `jobs.py`: `JobStore.delete()` and `find_by_share_token()` on both the memory
    and Firestore backends (Firestore uses a `params.share_token` field query).
  - `schemas.py`: `StoryboardSummary` (lean — the grid must not drag every board's
    panel+shot list over the wire), `StoryboardProject` (Duplicate),
    `StoryboardRenameRequest`, `ShareResponse`, `PublicStoryboard`. `genre` added
    to `StoryboardCreateRequest` and stored in params purely to label the card.
  - `main.py`: the endpoints listed in the API section above. `_drawn_panels()`
    reads the **active style variant**, so a restyled board's cover and shared
    view show the style the owner last picked, not variant 0.
  - **Share links:** token = `uuid4().hex` (32 hex chars) stored in params; it is
    the credential, so the public routes expose ONLY title/style/aspect/genre and
    the drawn panel indexes — never shots, refs or the owner email. Panel requests
    are validated against that index list so a token can't probe for other files.
    Revoke deletes the token and the old link 404s immediately. Sharing is
    idempotent (re-clicking returns the same token, not a new one).
  - Delete removes the record **and** `output/_storyboards/{id}/`; a running board
    returns 409 so the worker isn't yanked out from under itself.
- **Client**
  - `StoryboardLibrary.jsx` — the grid. Covers are owner-scoped so each is fetched
    as an authed blob (revoked on unmount). Polls every 5s **only while a board is
    still generating**, so a board made this session fills in its cover instead of
    saying "Generating…" until a reload. Delete is a two-step inline confirm.
  - `ScriptToStoryboard.jsx` — new `"library"` step, and it's now the **entry
    point** (`useState("library")`). Added a `title` field to the form
    (`effectiveTitle()` falls back to the script's first line, then the filename,
    so a board is never just called "Storyboard"). `applySavedSettings()` restores
    a saved board's style/aspect/genre chips, mapping unknown values back into the
    "custom" text fields. `boardOrigin` makes the board's ← Back return to the
    library for a re-opened board (whose shots aren't loaded) and to the review
    step for a freshly generated one. Duplicate reuses the stored shots, so it
    **skips the paid breakdown call**.
  - `PublicStoryboard.jsx` + `App.jsx` — a shared link is `?s=<token>`, read once
    at boot (the app has no router) and rendered before the auth check, since this
    is the one screen that must work logged out. The token is never persisted.
  - `styles.css`: `.lib-*` grid/cards and `.public-*` viewer, in the existing
    dark+gold language rather than the light mock's look.
- **Verified:** a scripted TestClient pass over the whole surface — list (excludes
  other users' boards and non-storyboard jobs), cover skips a failed panel and
  honours the active variant, project/rename/duplicate, share idempotency, public
  view **without an auth header**, failed panels not served publicly, revoke kills
  the token, cross-user access is 404 (not 403), running-board delete is 409, and
  delete empties the library. `npm run build` clean (48 modules).
  NOT clicked through in a browser, and not tested against Firestore — the
  `find_by_share_token` field query only ran against the memory store.
- **Caveat worth knowing:** with `API_JOB_STORE=memory` (the local dev fallback)
  the library empties on every backend restart. Persistent saving needs Firestore.

### 2026-07-24 — Cast/props references now SURVIVE stepping Back (user-reported bug)

- **Bug (reported by the user):** upload a character image on "Set up your cast" →
  press ← Back → "Review your shots" → forward again to the cast page, and the
  uploaded picture is gone. Same for generated refs, for edited descriptions, and
  for the props/backgrounds page.
- **Root cause:** `StoryboardCast` / `StoryboardAssets` owned everything in local
  `useState` seeded once by a `useState(() => …)` initializer. `ScriptToStoryboard`
  swaps steps by returning a different component, so stepping away **unmounts**
  them and throws the state out. Worse, each had an unmount cleanup that called
  `URL.revokeObjectURL` on its blob previews — so even a retained URL would have
  rendered blank.
- **Fix — lift the state to the workflow** (`ScriptToStoryboard.jsx`), which stays
  mounted for the whole session:
  - `savedCastRefs` / `savedAssetRefs`: maps of **lowercased name → `{ description,
    referenceId, previewUrl }`**. Name-keyed (not index-keyed) so editing/reordering
  /deleting shots between visits can't shift a picture onto the wrong character.
  - `previewUrls` ref now owns every blob URL; the only revoke is on workflow
    unmount or `clearSavedRefs()`. The per-step unmount cleanups are **deleted**.
  - `clearSavedRefs()` runs on **Start over** and on a **new breakdown** — a new
    script means a new cast, so a same-named character must not inherit the old
    picture.
- `StoryboardCast.jsx` / `StoryboardAssets.jsx`: take `saved` + `onSave` props.
  They still keep local state for live editing (busy/error stay local), but they
  **seed from `saved`** on mount and `patch()` mirrors the three durable fields up
  via `onSave`. `DURABLE` const lists them in one place; `busy`/`error` deliberately
  don't persist.
- **Verified:** `npm run build` clean, and the flow re-checked by reading the code
  paths (review→cast→back→cast, cast→props→back→cast, board→back→review→forward).
  NOT clicked through live in a browser this session — worth one manual pass.

### 2026-07-24 — Board editing: insert / delete a panel in place (add + generate)

- **Asked for:** on the final board (where the real images are visible), add a
  panel between shots, type its prompt, and generate it there — plus delete —
  instead of going back to Review and regenerating the whole board. Scope chosen
  WITH the user: add + delete + generate (NO reorder — the riskiest part), and
  the new panel generates on demand (type prompt → Generate), not automatically.
- **Design decision that kept everything else working:** panels are addressed by
  position (`panel_00.png`…) in the serve route, PDF, ZIP and public view — all
  assume `index == position`. So insert/delete **renumber** rather than break
  that: they shift the PNG files on disk and rebuild each panel's `index`+`url`
  in EVERY style variant, preserving `index == position`. Nothing else changed.
  - `_shift_panel_files` moves files descending (insert) / ascending (delete) so
    none clobbers another; missing files (failed/empty panels) are skipped.
  - `_renumber` sets index=position and rebuilds url via `_panel_url` for drawn
    panels (empty ones keep `url=None`).
  - Guards: 409 while the board is still generating; 404 bad index; 400 refuses
    to delete the last panel. `count` (params + result) updated.
- **Client (`StoryboardBoard.jsx`):** each tile gets ＋ (insert before this shot)
  and ✕ (delete); a "＋ Add a panel" card appends at the end. A freshly inserted
  panel shows "✏️ New panel — write a prompt, then Generate" and its button reads
  "✨ Generate this panel" (reuses the existing per-panel regenerate call). All
  edit controls hide while the board is generating. `reloadBoard()` clears the
  panel-blob cache AND the index-keyed `editedDesc`/`retrying` (indices shift, so
  stale entries would land on the wrong tile) then refetches the job.
- **Verified:** backend test with real PNGs + 2 style variants — insert@1 shifts
  files in both variants and `/panel/3` serves the moved image; the new panel
  generates at its index; delete@0 shifts down; guards (404/400) hold; indices
  and urls stay contiguous throughout. `npm run build` + backend import clean.
  NOT clicked through live.

### 2026-07-24 — Cast/Props: "Generate all" + "Retry failed" bulk buttons

- **Asked for:** on the cast and props/background steps, board-style bulk actions
  — one button to generate every missing reference and one to retry the failed
  ones — and crucially, **don't regenerate an image the user uploaded**.
- **`StoryboardCast.jsx` + `StoryboardAssets.jsx`:**
  - Each item now carries an `uploaded` flag (true when set via Upload, false
    when generated). It's in `DURABLE` + seeded from `saved`, so it survives
    Back navigation.
  - New toolbar above the grid: **✨ Generate all (N)** targets items with no
    reference AND not uploaded (`needsGen`); **🔄 Retry failed (N)** targets items
    with an error AND not uploaded (`isFailed`). Uploaded images are skipped by
    both — that's the whole point.
  - `runBulk(predicate)` snapshots the target set up front and generates
    **sequentially** (one at a time), matching the board's "Retry all failed" —
    gentler on the image quota than firing them all at once.
  - `generateRef`/`uploadRef` refactored around a shared `runGenerate(i, item)`
    that takes the item snapshot, so the bulk loop doesn't depend on React state
    updating between iterations. Generating clears `uploaded`; uploading sets it.
  - `.cast-toolbar` CSS (flex-end, like `.board-toolbar`).
- **Verified:** `npm run build` clean; both files wired (toolbar + predicates).
  NOT clicked through live.

### 2026-07-24 — Assets ZIP: skip UPLOADED refs again (user request)

- **Reported:** the ZIP included the user's own uploaded character images, which
  they already have — they only want the AI-generated ones.
- Reverted the "bundle everything" decision from the earlier ZIP entry. Re-added
  `_ref_is_generated()` (reads the `source.txt` marker `_mark_ref_source` already
  writes: uploads → "uploaded", generations → "generated"; missing marker =
  treated as generated so pre-marker refs still bundle). The bundle now skips any
  ref whose marker is "uploaded", for BOTH characters and props/backgrounds.
  Numbering runs over the INCLUDED refs so it stays contiguous. Panels + PDF
  unchanged.
- **Verified:** TestClient test with one generated + one uploaded of each kind —
  ZIP contained the generated character/background + panels + PDF, and NEITHER
  uploaded ref. Backend imports clean.

### 2026-07-24 — "Back to shots" no longer discards the generated board

- **Reported:** on the board (even mid-generation), pressing "← Back to shots"
  then going forward again started a FRESH generation from shot 1 — the drawn
  panels were lost.
- **Cause:** the review step's primary action always called `startStoryboard`,
  which POSTs `/storyboards` = a brand-new job. The old job (and its panels) was
  just abandoned.
- **Fix (`ScriptToStoryboard.jsx`):** record `generatedSig` — a JSON signature of
  the shots+style+aspect the current board was drawn from — when a board is
  created. `boardUpToDate = jobId && generatedSig === currentSig()`. On the
  review step:
  - up to date → primary button is **"→ Back to your storyboard"** (reopens the
    SAME job via `setStep("board")`, keeping all panels; if it's still
    generating, the board just resumes polling the ongoing server job), plus a
    separate **"🔄 Regenerate"** for a deliberate fresh draw.
  - edit any shot → the signature changes, `boardUpToDate` flips false, and it
    collapses back to the normal "Next: cast / props / Generate panels" button.
  - `generatedSig` cleared in `resetWorkflow`.
- Library-opened boards are unaffected (their Back goes to the library, not
  review). `.review-actions-right` groups the two right-hand buttons.
- **Verified:** `npm run build` clean. Flow reasoned through the code paths
  (generate → back to shots → return reopens same job; edit a shot → regenerates)
  but NOT clicked through live.

### 2026-07-24 — Upgrade button now opens a full pricing/plans modal

- Replaced the one-line "coming soon" popup with `PricingModal.jsx` — a 4-tier
  plans table (Trial/Free · Starter · Pro Unlimited · Production Unlimited) with a
  Monthly/Yearly toggle (Yearly shows the discounted per-month price + "Save 25%"),
  struck-through "was" prices, feature lists with ✓/✕, and the "Most Popular"
  card highlighted in brand gold. Modeled on the reference layout but themed in
  the app's dark+gold language, not the reference's branding.
- **Payments are NOT wired** — the Upgrade CTAs show an inline "checkout coming
  soon" note instead of starting a charge. The tier data is a single `PLANS`
  array, ready to hook to a billing provider later. Trial shows "Current Plan"
  (disabled). Prices are placeholders.
- `App.jsx` renders `<PricingModal>` for `upgradeOpen`; old inline modal +
  orphaned `.upgrade-modal` CSS removed (`.soon-icon` kept — still used by
  WorkflowSoon/JobDetail).
- **Verified:** `npm run build` clean. Modal not viewed in a browser.

### 2026-07-24 — Panels failing en masse: patient quota retries + gentler pacing

- **Reported:** generating a 10-panel board, only 2 succeed; the rest show
  "Couldn't draw this panel". Same `429 RESOURCE_EXHAUSTED` as before. The
  succeed-some/fail-rest pattern = a per-minute rate quota, not a hard/daily zero.
- **Why it wiped out most panels:** (1) the token bucket started FULL (120
  tokens) so a whole board fired instantly with zero pacing; (2) 4 panels ×
  6-wide concurrency slammed the API together; (3) the 429 retry ladder was only
  ~20s — far too short for a per-minute quota to refill — so panels gave up and
  were marked failed.
- **Fix (`gemini_client.py`, `storyboard_pipeline.py`):**
  - **Quota-aware backoff:** new `_is_quota_error()`; 429s now use a long ladder
    (`QUOTA_BACKOFF` 15s→cap 50s) for ~140s of patience across `MAX_RETRIES`=5,
    vs ~39s for ordinary 503 blips. A server `retryDelay`/Retry-After hint still
    wins. So a panel now WAITS for the quota to refill instead of failing.
  - **Gentler pacing:** token bucket starts with a 2-token burst, not a full
    minute's worth — pacing applies from the first call. Defaults lowered:
    `IMAGE_MAX_CONCURRENCY` 6→3, `IMAGE_RPM` 120→60, `STORYBOARD_PANEL_CONCURRENCY`
    4→2. Fewer calls in flight → the quota keeps up.
  - New `IMAGE_MAX_RETRIES` env (default 5); all documented in `.env.example`.
- **Trade-off (intended):** boards now generate SLOWER but should COMPLETE. A
  panel can wait up to ~2 min riding out the quota before failing.
- **Verified:** ladder math printed (quota ~140s vs 503 ~39s, hint honoured),
  burst reduced 120→2, imports clean. NOT run against the live API (needs the
  user's billed quota) — the real per-minute limit is still unknown; these make
  the client patient and gentle rather than assuming a specific number.
- **Still recommended:** confirm the actual quota in GCP Console → IAM & Admin →
  Quotas (Vertex AI, region — note `GOOGLE_CLOUD_LOCATION=global`), and that
  billing is enabled; a trial project's image quota is often very low.

### 2026-07-24 — Breakdown ring: EVEN climb + quick finish (final shape)

- **Reported (again):** "1→86 fast then slow to 100" still looked wrong.
- **Honest root cause, stated plainly:** the breakdown is a single AI call with
  no progress signal, so the % is unavoidably an estimate. Earlier passes had the
  motion shape BACKWARDS — fast (22%/s) then slow (0.9%/s), a 24× slowdown, which
  is the classic "stuck" read. Good loaders do the reverse: even climb, quick
  finish.
- **Fix:** one constant `FILL_RATE` 6.5%/s carries it 0→`SOFT_CAP` 96% (≈15s) —
  no rush at the start; a gentle `CRAWL_RATE` past 96 only for slow outliers; on
  `done`, `FINISH_RATE` 34%/s sweeps to 100 (speeding up at the end = "done").
- **Verified:** Node trace shows an even line — 7/13/26/39/52/65% at 1/2/4/6/8/10s
  (~6.5%/s early AND late, no rush) — and API waits of 3/8/13/25s all reach 100%.
  `npm run build` clean. Not viewed in a browser.
- **Known limit (documented so it isn't "fixed" in a circle):** a fake bar can
  never perfectly track an unknown-duration call. If this still isn't wanted, the
  only truly honest alternatives are (a) an indeterminate spinner with no number,
  or (b) real server-side progress — which the one-shot breakdown call cannot
  emit without faking sub-steps there instead.

### 2026-07-24 — Breakdown ring: creep while waiting (no freeze at the cap)

- **Reported:** ring parked at 90% and looked hung.
- **Cause:** the previous fix held a HARD cap of 90% until the breakdown API
  returned. That's correct in spirit, but the text model is sometimes throttled,
  so the wait is long and a frozen 90% reads as broken.
- **Fix (`BreakdownProgress.jsx`):** pre-completion motion is now two-phase —
  brisk `FILL_RATE` up to `SOFT_CAP` 85%, then a slow `CRAWL_RATE` toward
  `HARD_CAP` 97% that keeps inching the whole wait (never reaches 100 until
  `done`). On `done` it still sweeps to 100 and hands off. Added a
  `LONG_WAIT_MS`=16s reassurance sub-line ("Still working — longer scripts take a
  little more time…"). No wiring change; `done`/`onDone` still drive completion.
- **Verified:** Node simulation of the exact loop — a 30s wait shows 86→95→97%
  (always rising, never frozen, never 100 early) and completes at 30.03s; fast
  and instant cases still finish; a stand-alone check confirms pct keeps rising
  10s→20s while waiting. `npm run build` clean. Motion not viewed in a browser.

### 2026-07-24 — Storyboards survive restart; library: Recent=1 + ghost cards

**1. Saved storyboards no longer vanish (the real "it disappeared" fix).**
`API_JOB_STORE=memory` (this dev machine) keeps jobs in RAM, so any backend
restart — including uvicorn --reload picking up a code edit — wiped them.
`MemoryJobStore` now mirrors every create/update/delete to a JSON file
(`API_LOCAL_JOBS_PATH`, default `.local_jobs.json`, atomic via tmp+os.replace)
and reloads it on boot. Panels/covers already live on disk under
`output/_storyboards/`, so a reloaded board shows its cover too. `persist_path=
None` keeps the old pure-RAM behaviour. Added to `.env.example` + `.gitignore`.
- **Verified:** a Node-style Python test creates two boards in one store
  instance, opens a SECOND instance on the same file (simulating a restart) and
  confirms both boards + their status survive; delete persists; RAM-only mode
  still works. NOT the production path — Firestore remains the real backend.

**2. Library layout** (`StoryboardLibrary.jsx` + CSS):
- `RECENT_COUNT` 4 → **1**: "Recent Storyboards" now highlights only the newest
  board; "All Storyboards" still lists every board (the newest appears in both,
  which is the intended highlight-vs-index split).
- **Empty/loading sections now show dimmed "ghost" cards** shaped like a real
  board card (blank cover + title bar + two chip pills) instead of a bare line of
  text — 1 ghost in Recent, 3 in All. While `loading` they shimmer; once known-
  empty they hold still with a "hit New Storyboard" hint on the first card.
  `prefers-reduced-motion` drops the shimmer.
- **Verified:** `npm run build` clean; backend imports clean. Ghost/empty visuals
  NOT viewed in a browser.

### 2026-07-24 — Breakdown ring now actually reaches 100% before Review

- **Reported:** the ring stopped at a different value every time (56/63/66%) and
  never hit 100%.
- **Real cause (the earlier passes only polished a fake timer):** the ring was
  cosmetic. `handleGenerate` `await`ed the breakdown call and then immediately
  `setStep("review")`, unmounting the ring at whatever % it had reached. The
  value differed run-to-run because the API returned at a different moment.
- **Fix — tie the ring to the actual call** (`ScriptToStoryboard.jsx` +
  `BreakdownProgress.jsx`):
  - The result is stashed in `pendingBreakdown` ref and `breakdownDone` state is
    flipped true; navigation no longer happens inline.
  - `BreakdownProgress` gained `done` + `onDone`. It fills steadily but **holds
    at 90%** while `done` is false (never claims completion before the work is),
    then on `done` sweeps to 100% at `FINISH_RATE` and calls `onDone` after a
    350ms beat. `finishBreakdown()` then applies shots/characters/assets and
    goes to Review — so number, ring and label reach 100% together every time.
  - At 100% the label reads "Scene breakdown ready!", the sublabel "ready", and
    all dots light.
  - Error path clears `busy`/`breakdownDone`/`pendingBreakdown`, so a failed
    breakdown drops back to the form with the message (no stuck ring).
  - `done`/`onDone` are read through refs so the rAF effect keeps its empty deps
    and isn't restarted each render.
- **Verified:** simulated the exact loop+constants in Node for fast (1.5s), slow
  (8s) and near-instant (0.2s) API returns — all reach 100% and fire `onDone`;
  the slow case correctly parks at 90% until the call returns. `npm run build`
  clean. In-browser motion still not directly viewable — but the "never reaches
  100%" bug was logic, and the logic is now proven.

### 2026-07-24 — Breakdown ring: steady fill + label derived from the percentage

- **Reported (follow-ups):** the ring "rushed 0→80 then crawled to 100", and the
  step label didn't line up with the number.
- **Two causes:**
  1. Asymptotic easing (`+= (95-p)·k`) moves fast when far from target and slow
     when near — hence rush-then-crawl.
  2. The label rotated on its own `setInterval`, unrelated to the percentage, so
     it never matched where the ring was.
- **Fix (`BreakdownProgress.jsx`):**
  - **Constant-speed fill:** `FILL_RATE = 90/13 %·s⁻¹` up to 90%, then a slow
    `CREEP_RATE` 1.2%/s to a 99% ceiling so it never looks frozen if the call
    runs long. No easing curve → even pace the whole way.
  - **Label is now `stepFor(pct)`** — each step owns a %-slice (0–20, 20–40, …),
    so the text changes exactly as the ring passes that mark. The active dot
    follows the same function. Text and ring can no longer disagree.
  - Earlier fix (SVG stroke, no CSS transition on the arc) is retained.
- **Verified:** `npm run build` clean. Motion NOT viewed in a browser.
  If a stale bundle is served, a hard refresh (Ctrl+Shift+R) is needed to see it.

### 2026-07-24 — Breakdown progress ring: smooth fill instead of stepping

- **Reported:** the "Generating your scene breakdown" ring jumped in visible
  steps ("break break") instead of moving smoothly.
- **Cause:** the ring was a `conic-gradient` background driven by a `--pct`
  variable, with `transition: background 0.2s linear`. Browsers **cannot tween a
  conic-gradient**, so the transition was a no-op and the arc snapped each time
  the 180ms interval bumped the number.
- **Fix (`BreakdownProgress.jsx` + `styles.css`):**
  - Ring is now an **SVG** two-circle (track + arc) with `stroke-dashoffset` —
    which does animate — rotated -90° to start at 12 o'clock, round line cap.
  - Progress is eased in a `requestAnimationFrame` loop with **time-based**
    (frame-rate-independent) asymptotic easing toward 95% — `+= (95-p) *
    (1 - e^(-0.9·dt))` — so the number and arc glide continuously instead of
    ticking. `dt` clamped to 50ms so a backgrounded tab doesn't jump on return.
  - Percentage held in a `useRef`, only the rounded display value in state;
    `tabular-nums` stops the digits jittering as they change.
  - Step label cross-fade strengthened (0→1 opacity + 6px rise, re-keyed per
    step); added a 5th step so the rotation matches a longer breakdown.
  - `@media (prefers-reduced-motion)` drops the slide/transition (the ring still
    fills — that's the actual feedback).
- **Verified:** `npm run build` clean; no stale `bp-ring2` refs remain. Motion
  itself NOT viewed in a browser (can't see animation from a build).

### 2026-07-24 — Assets ZIP = full package; PDF gains camera / location / cast

**1. PDF now carries the shooting detail** (`storyboard_pdf.py`). Each cell used
to be just the panel + "Shot N" + two description lines. It now mirrors the app's
shot card: `Shot N  SCENE n`, description, **Camera**, **Location**, then the
character names as gold pills.
- `TEXT_H = 196` (was a hardcoded 96) reserves the room under each image; the
  grid stays 2×3 per A4 page, panels land ~306px tall.
- New helpers: `_meta_row()` (label + single-line ellipsised value),
  `_cast_chips()` (rounded gold pills, wraps to a second row then collapses the
  remainder into a `+N` pill so a crowded shot can't bleed into the panel below),
  `_truncate()`.
- **Verified by eye, not just assertion:** rendered a 6-panel sample board and
  viewed page 1 as a PNG — long locations ellipsise, a 6-character cast fits.

**2. Assets ZIP is now the complete package** (`GET /storyboards/{id}/bundle`).
Previously it held only the AI-generated refs + the PDF — no panel images at all.
Now:
```
panels/<Title>_shot_01.png ...        every drawn panel, full resolution
characters/<Title>_character_01_<Name>.png
props/<Title>_prop_01_<Name>.png
backgrounds/<Title>_background_01_<Name>.png
<Title>.pdf
```
- Every file is prefixed with the board title and numbered in board order, so the
  sequence survives being unzipped into a flat folder.
- **Panel numbering is contiguous over DRAWN panels** — a failed panel is skipped
  without leaving a gap, so `shot_02` is the second picture you actually have.
- Panels come from the **active style variant** (what the board shows), not
  variant 0.
- **Behaviour change:** uploaded references are now included. They used to be
  skipped on the reasoning that "the user already has those images", which is
  wrong for a hand-off package. `_ref_is_generated()` was the only reader of the
  `source.txt` marker and is deleted; `_mark_ref_source()` still writes it as
  provenance.
- **props/ vs backgrounds/ needed new data:** the job stored `asset_ref_paths`
  but no categories. Added `asset_categories` to `StoryboardCreateRequest` +
  params; `ScriptToStoryboard.startStoryboard()` derives it from
  `computeAssets()` (the props step doesn't report category upward). Duplicated
  boards have no `assets` state, so their refs fall back to `prop`.
- `_safe_filename()` is now one shared helper used by both the PDF download and
  the ZIP. It maps punctuation to a space and collapses runs, so
  "Postmarked: After Death!" → "Postmarked After Death" rather than the ragged
  "Postmarked_ After Death_".
- **Verified:** scripted test builds real PNGs on disk, calls the endpoint and
  inspects the actual zip entry names — panel numbering skips a failed panel,
  characters/props/backgrounds land in the right folders with the right
  sequence, the PDF is a real `%PDF-` payload, and a board with nothing drawn
  returns 409 instead of an empty zip. Library regression test still passes;
  `npm run build` clean. NOT exercised against a live generated board.

### 2026-07-24 — Library: rename was broken by the Recent/All duplicate render

- **Reported:** clicking the rename (⚙) icon didn't let the user rename; delete
  and duplicate also felt wrong.
- **Root cause — one bug, three symptoms.** The Recent and All sections render the
  SAME board, and every per-card UI flag was keyed by `job_id`. So one click set
  the flag for *both* copies:
  - **Rename (broken):** two `<input autoFocus>` mounted; the second stole focus,
    which blurred the first, whose `onBlur={saveRename}` ran with an unchanged
    value and set `renamingId = null` — unmounting both. The box flashed and
    vanished, so renaming appeared to do nothing.
  - **Delete (confusing):** the confirm panel opened on two cards at once.
  - **Duplicate (confusing):** both copies greyed out while the fetch ran.
- **Fix:** transient state is now keyed by a per-instance card id,
  `` `${section}:${job_id}` `` — `renamingId`, `confirmId`, `copiedId`, and the
  React `key`. `renderBoard(b, section)` and `renderSection(section, …)` thread it
  through. `saveRename` also closes via `setRenamingId(id => id === uid ? null : id)`
  so a slow response can't close an editor the user reopened elsewhere.
- **Deliberately still keyed by `job_id`** (shared across both copies is correct):
  `busyId` (an in-flight action must disable that board in both sections so it
  can't be fired twice), `covers` (fetch the cover once), and `patchBoard` (a
  rename must update both copies).
- **Checked and ruled out** as causes: CORS (`allow_methods=["*"]`, so the new
  PATCH/DELETE preflights pass) and the 204 delete response (`request()` returns
  the raw Response for non-JSON, which the caller ignores).
- **Verified:** backend smoke test re-run, all pass (rename/delete/project
  included); `npm run build` clean; grepped for leftover `=== b.job_id`
  comparisons — only the three intended ones remain. NOT clicked through in a
  browser.

### 2026-07-24 — Light mode: chips/pills filled gold with white ink

- **Reported:** the review summary's "Dark Anime / 16:9 / 6 shots" pills and the
  character-name chips ("Vivan") looked weak in light mode; user asked for the
  same gold fill + white text as the buttons, on every page.
- `.chip` on the dark theme is a faint gold tint with gold text, which on white
  collapses to a barely-there outline. Light mode now fills it with `--gold-fill`
  and puts `--gold-ink` (white) on top. One rule covers every page: the review
  summary pills, per-shot character chips, the library cards' genre/ratio/panel
  pills, GenerateForm's removable custom-asset chips, **and** JobDetail's download
  chips — so both workflows match.
- `.opt-chip.active` (selected style / genre / ratio on the form) fills the same
  way — a picked option must not look weaker than a read-only pill beside it —
  and its `.opt-chip-note` flips to white. `.opt-chip:hover`'s 6%-gold tint was
  invisible on white, now a light-appropriate 8%.
- `.chip-x` (remove button inside a removable chip) goes translucent-white so it
  reads on the gold fill; its red hover already used white.
- **`.asset-badge-prop` / `-background` fill too, but stay two DIFFERENT colours**
  (gold / blue `#3a6cbf`). That difference is information — a prop vs. a location
  — so they were not merged into one gold.
- **Verified:** white ink measures 3.41:1 on the gold pills and 5.15:1 on the blue
  badge; chip render sites grepped across all components to confirm coverage.
  `npm run build` clean. Dark mode untouched. NOT viewed in a browser.

### 2026-07-24 — Light mode: plain `.btn` was invisible against the page

- **Reported:** "← Your Storyboards" looked flat/inconsistent next to the other
  buttons in light mode.
- **Cause:** `.btn` filled with `--panel-2` (#eceff5) sits on `--bg` (#f4f6fa) —
  a 1.02:1 difference, so on the page background it read as plain text. On the
  dark theme that same recessed grey reads correctly as a button.
- **Fix:** the plain button's surface now comes from `--btn-bg` / `--btn-border`
  / `--btn-shadow`. Dark keeps the old values exactly; light raises the button to
  white with a firmer `#c7cedd` edge and a soft shadow.
  - **Why variables and not a `:root[data-theme="light"] .btn` override:** that
    selector scores 0,3,0 and would have beaten `.btn.primary` (0,2,0) — silently
    stripping the gold off every primary button. Variables let `.primary` /
    `.secondary` / `.ghost` keep overriding exactly as they do today.
  - `.btn.ghost` gained `box-shadow: none` (it's borderless — it must not inherit
    the raised shadow), and `.btn:hover` / `.btn.secondary:hover` are now gated on
    `:not(:disabled)`, so a disabled button no longer lights up gold on hover.
- **Verified:** `npm run build` clean; dark-mode values unchanged by inspection.
  NOT viewed in a browser.

### 2026-07-24 — Light mode: white ink on gold fills (user-reported)

- **Reported:** in light mode the active tab ("✨ Describe Character") was a muddy
  dark-gold block with near-black text, and the disabled "Generate Reference
  Image" button was washed out to nothing. User asked for **white text on a gold
  fill, applied everywhere**.
- **Root cause:** the previous entry set `--primary` to a deep gold so gold-as-
  *text* would read on white — but ~9 rules also use `var(--primary)` as a
  *background* and pair it with `--primary-ink` (near-black). Deep gold + dark
  ink = the muddy block. Separately, `.btn:disabled`'s `opacity: 0.45` is fine on
  a dark surface and invisible on a light one.
- **Fix — one source of truth for gold fills** (`styles.css`). Added
  `--gold-grad`, `--gold-grad-hover`, `--gold-grad-rich(-hover)`, `--gold-bar`,
  `--gold-fill`, `--gold-ink` and `--disabled-opacity`, then routed **every**
  gold-filled surface through them — 23 literal gradients / `var(--primary)`
  backgrounds and 17 `color: var(--primary-ink)` declarations. Covers both
  workflows and every page: `.btn.primary`, `.tab-btn.active`,
  `.btn.secondary:hover`, `.sb-upgrade`, `.upgrade-inline`, `.lib-new-plus`,
  `.lib-badge`, `.step-num`, `.sts-guide-num`, all four avatars, the three
  regen/retry hovers, and the two progress bars.
- **Dark mode is byte-identical** — its variables hold exactly the old literals.
- **The gold had to darken, then the user pulled it back up.** White on the dark
  theme's `#e5c158` is 1.7:1 (unreadable), so the first pass used an antique-gold
  ramp at 4.4:1+. The user judged that "dark golden" and asked for **mid golden**,
  so the shipped ramp is `#bd8d1e → #a87914` (solid fill `#b0841a`), landing at
  **3.0–3.9:1** with white. That clears WCAG AA's 3:1 large-text bar — which the
  bold button labels these fills carry do qualify for — but not the 4.5:1 body
  bar. **Keep these gold surfaces to short bold labels**; don't put paragraph
  text on them. Brightness here is a deliberate, user-made trade.
- Two `border-top-color: var(--primary-ink)` spinner arcs were deliberately NOT
  switched to `--gold-ink`: they also spin on plain (non-gold) buttons, where
  white would vanish.
- **Verified:** contrast ratios computed, not eyeballed — every white-on-gold
  stop 4.47–6.45:1; dark-mode ink on gold unchanged at 8.7–10.9:1; light `--text`
  17.7:1 and `--muted` 6.0:1 on white. No gold literal survives outside the two
  `:root` blocks. `npm run build` clean. NOT viewed in a browser.

### 2026-07-24 — Light / dark mode toggle (sidebar, above the account button)

- `theme.js` (new): `getTheme()` reads `localStorage.cas_theme`, falling back to
  the OS `prefers-color-scheme` on first visit; `applyTheme()` stamps
  `<html data-theme>` and persists. `main.jsx` applies it **before the first
  render** so a light-mode user doesn't get a flash of the dark palette.
- Because the switch is one attribute on `<html>` and every colour reads from a
  CSS variable, it re-skins the WHOLE frontend — including screens rendered
  outside the sidebar (landing, login, the public shared board) that never see
  the React state. `App.jsx` owns the state, `Sidebar.jsx` renders the control.
- `styles.css`: `:root[data-theme="light"]` palette. **The gold button gradients
  are unchanged** (brand), but gold used as *text* or a *border* washes out on
  white, so `--primary` / `--border-gold` go several shades deeper. Added
  `--shadow-sm/-/-lg` (black in dark, soft blue-grey in light) and `--frame-bg`
  (image letterboxing: still `#000` in dark, grey in light — so dark mode is
  pixel-identical to before). `color-scheme` is set per theme so the browser's
  own scrollbars / `<select>` / autofill follow along.
- **Contrast bugs found while auditing, not just the palette:**
  - `.btn:hover` was `color: #fff` over a `--panel-2` background — invisible in
    light mode. Now `var(--text)`.
  - `.error` / `.danger-btn` / `.shot-btn.danger:hover` used a hardcoded pale red
    `#fca5a5` on a pink tint — now `var(--fail)`.
  - `.auth-wrap`'s radial backdrop was hardcoded dark, so **login and landing
    would have stayed dark** regardless of the toggle.
  - Light-mode overrides for tints written as translucent white or pale
    gold/blue (`.chip`, `.tab-btn:hover`, `.asset-badge*`).
  - Deliberately left dark: `.lightbox-img` shadow and `.modal-overlay` scrim
    (they sit over a dark backdrop in both themes) and `.art-cell`/`.gallery
    figure`'s white image backing (intentional, for transparent PNGs).
- **Verified:** `npm run build` clean (49 modules); swept every hardcoded colour
  in styles.css and classified each. NOT viewed in a browser — the light palette
  has not been eyeballed on a real screen, and contrast was reasoned about
  rather than measured.

### 2026-07-24 — Library: "Recent Storyboards" + "All Storyboards" sections

- Page 1 now has two labelled folder-style sections under the New Storyboard
  tile: **Recent Storyboards** (newest `RECENT_COUNT` = 4) and **All
  Storyboards** (everything). An empty section keeps the existing "Nothing here
  yet…" note, so a brand-new account looks the same as before.
- The card markup moved into a shared `renderBoard(b)` so the two sections can
  never drift apart; both read the same `boards` state, so a rename/delete/share
  updates in both at once.
- **Trap avoided:** the section is a render FUNCTION, not a nested component. A
  component declared inside `StoryboardLibrary` gets a new identity every render,
  so React would remount the section on each keystroke and the inline rename
  input would lose focus after one character.
- `styles.css`: `.lib-section*` heading + rule, `.lib-new-row` spacing, and
  `.lib-empty` re-padded now that it sits inside a section rather than after the
  whole grid.
- **Verified:** `npm run build` clean. NOT viewed in a browser; the populated
  layout (cards in both sections) has only been reasoned through, not seen.

### 2026-07-24 — Step actions sit UNDER the page title (follow-up placement fix)

- The previous entry put the Back/Next bar above `.workflow-header`, which pushed
  the page title and its icon below the buttons. The user wants the title to read
  first, with the buttons directly under it.
- Swapped the order in all five headers — form (← Your Storyboards), review, cast,
  props, board. The bar keeps its `.top-actions` divider, which now separates the
  whole title+buttons block from the page content.
- `styles.css`: `.workflow-header:has(+ .top-actions)` tightens the title→buttons
  gap to 1.1rem (the header's default 1.8rem is meant for spacing off content).
- **Verified:** `npm run build` clean; header-before-actions confirmed in all five
  render paths. NOT viewed in a browser.

### 2026-07-24 — Storyboard steps: Back / Next action bar moved to the TOP

- **Why:** on every storyboard step the Back + primary (Next / Generate panels)
  buttons sat at the very bottom, so with a long shot list, a full cast grid or a
  big panel board the user had to scroll to the end just to move forward or back.
- Moved the `.review-actions` bar from the bottom to the **first element inside
  the step wrapper**, above `.workflow-header`, in all four storyboard screens —
  the markup and handlers are unchanged, only the position:
  - `client/src/components/ScriptToStoryboard.jsx` (review-shots step:
    ← Back / 🎭 Next: cast · 🎬 Next: props · 🎬 Generate panels)
  - `client/src/components/StoryboardCast.jsx` (← Back / 🎬 Generate panels)
  - `client/src/components/StoryboardAssets.jsx` (← Back / 🎬 Generate panels)
  - `client/src/components/StoryboardBoard.jsx` (← Back to shots / Start over)
- `client/src/styles.css`: new `.top-actions` modifier on `.review-actions` —
  `margin: 0 0 1.4rem`, bottom rule (`1px solid var(--border)`) to separate it
  from the page title, `align-items: center` + `flex-wrap` so the two buttons sit
  on one line, and `margin-top: 0` overriding `.btn.primary`'s `1.1rem` (which had
  been pushing the gold button out of alignment with Back).
- **Verified:** `npm run build` in `client/` is clean (46 modules, no warnings).
  NOT clicked through in a live browser this session.

### 2026-07-24 — Image-API throttle + PARALLEL panel generation
- **Quota investigation (done with the user, GCP project `project-cf56be07-4f9e-45d4-9f4`):**
  the app calls `gemini-3.1-flash-image` via `generate_content`, so it uses the
  **"Generate content requests per minute"** buckets (**300–1600/min**) — NOT the
  Imagen `imagegeneration`/`imagen-3.0-*` buckets (10–20/min) that looked alarming.
  There is no `global` model-serving bucket (only non-regional job/CRUD ones).
  **Conclusion: quota was never the real constraint.** The real problems were
  (a) the synchronous cast/props/retry endpoints run on FastAPI's own ~40-thread
  pool, bypassing `API_MAX_WORKERS=2` entirely (7 Cast cards = 7 unbounded calls),
  and (b) every error — transient 503 OR permanent safety block — burned 3 attempts
  and ~28s of blocking sleep.
- **Phase 1 — one governor at the chokepoint (`gemini_client.py`).** Every image
  call in the process funnels through this module, so capping here bounds both the
  worker path and the interactive endpoints:
  - `_TokenBucket` (thread-safe, rolling-minute) + `BoundedSemaphore`, exposed as a
    `_throttle()` context manager wrapped around ALL FOUR `generate_content` call
    sites (turnaround sheet, storyboard panel, character ref, asset ref).
  - **Error classification** — `_is_retryable()`: retry 429/500/503/504 only; fail
    fast on 400/401/403/404/safety/blocked. This is the actual fix for the wasted
    28s waits.
  - `_backoff_delay()` — exponential **with jitter** (0.5–1.5×) so parallel workers
    don't retry in lockstep, and `_retry_after_seconds()` honours a server
    `Retry-After` / `retryDelay` hint (capped 120s).
  - Env: `IMAGE_MAX_CONCURRENCY` (default 6), `IMAGE_RPM` (default 120, 0 = off).
- **Phase 2 — parallel panels (`storyboard_pipeline.run_storyboard`).** Replaced the
  serial `for` loop with a `ThreadPoolExecutor` (`STORYBOARD_PANEL_CONCURRENCY`,
  default 4). Panels are **pre-built up front** so the streamed list is always
  FULL-LENGTH — pending entries have `url=None, failed=False`, which the board
  already renders as skeletons, so **no frontend change was needed** (and
  `pendingCount` naturally becomes 0). Mutations guarded by a lock; progress emits a
  snapshot; a crashed panel is flagged without killing the board.
- `.env.example`: documented all three new vars.
- **Verified:** throttle bounds peak in-flight to exactly 6 under 30 threads;
  classification table (7 cases); Retry-After parsing; jitter produces varied
  delays; 12-panel parallel run = 0.68s vs ~2.4s serial (**3.5×**) with order
  preserved, failure flagged, and every emit full-length; **regression** — restyle
  variants, composition refs, variant switch and per-panel regenerate all still
  pass under parallel rendering. Backend imports + `npm run build` clean.
  NOT run live (needs billed calls).
- **NOT done (deliberately):** grid batching (Phase 3) was dropped — it was
  justified by a 4× quota saving the numbers show you don't need. Circuit-breaker /
  provider-failover / durable queue remain future work.

### 2026-07-24 — Board: change visual style → switchable style VARIANTS (keep-both)
- **Feature:** re-cast the whole board into another style (Sketch→Comic→…). Each
  restyle is saved as a **variant** you can switch between instantly (keep-both);
  every panel reuses the locked character/prop/background refs PLUS its previous
  render as a **composition reference**, so only the art style changes.
- `gemini_client.generate_storyboard_panel`: new `composition_reference_image`
  (appended last, with a "keep the same staging, only change the art style"
  instruction).
- `storyboard_pipeline`: `run_storyboard`/`regenerate_panel` are variant-aware —
  `variant` writes panels to a `v{N}/` subfolder and tags URLs with `?v={N}`;
  `composition_ref_dir` feeds the matching prior panel. Helpers `_variant_dir` /
  `_panel_url` / `_load_composition_ref`. `storyboard_pdf.build_storyboard_pdf`
  gained a `subdir` arg.
- `server/worker.py`: `_run_restyle` streams the new variant in, preserving all
  existing variants (result carries `variants:[{style,panels,ok_count}]` +
  `active_variant`; flat `panels/style/ok_count` mirror the active one).
- `server/main.py`: `_variants_of` (synthesises variant 0 for old jobs);
  `POST /storyboards/{id}/restyle` (async, new variant, composition ref = active
  variant's folder); `POST /storyboards/{id}/active-variant` (switch, no regen);
  panel serve takes `?v=`; regenerate-panel + PDF + ZIP all act on the ACTIVE
  variant/subdir. Shots are stored on the job so restyle can re-draw.
- Client: `api.restyleStoryboard` / `setActiveVariant`; `fetchStoryboardPanel`
  fetches by the panel's own URL (variant-tagged) + cache-busts. `StoryboardBoard`
  gained a **style variant switcher** (chips) + **"Add a style" select + 🎨 Restyle
  all**; panel blobs are cached by URL (per-variant); a nonce restarts polling for
  the restyle run. `.board-styles/.board-variant-switch/.board-restyle/.board-style-select`.
- Verified end-to-end (memory store, stubbed image call): initial render → restyle
  to comic creates variant 1 (URLs `?v=1`, files in `v1/`, composition ref fed on
  every panel), switch active back to 0, and variant-aware regenerate writes to
  `v1/` with an edited prompt. `npm run build` clean; all backend modules import.
  NOT run live (needs billed image calls per restyled panel).

### 2026-07-24 — Board: fix Retry "Panel N not found" + editable per-shot prompts
- **Bug:** retrying the last failed panel showed "Panel 16 not found." Root cause:
  `regenerate-panel` looked up `panels[index]` by LIST POSITION and 404'd whenever
  the persisted `result.panels` was momentarily shorter than the index (a poll
  landing on a partial/streamed result, or any gap). Confirmed `run_storyboard`
  itself DOES persist all N panels (17-shot repro), so this was purely the retry
  endpoint's fragility.
- **Fix (`server/main.py` regenerate_storyboard_panel):** locate the panel by its
  `index` FIELD, fall back to list position, then REBUILD it from the shot list now
  stored on the job (`params["shots"]`, added in `create_storyboard`). Write back by
  matching index (insert + re-sort if absent). So retry works even for a panel
  that isn't in the streamed result yet. Also backfills count/style/aspect on the
  result. Verified end-to-end against a job whose result had 16 panels but 17 shots:
  retry index 16 reconstructs, no 404.
- **Editable prompts (user ask):** each board tile's caption is now an editable
  textarea; a per-tile "🔄 Regenerate" (or "🔄 Retry" when failed) re-draws the shot
  with the edited wording. `PanelRegenerateRequest` gained optional
  `description`/`camera`/`location` overrides (persisted onto the panel);
  `api.regenerateStoryboardPanel(jobId, index, overrides)`; `StoryboardBoard.jsx`
  tracks `editedDesc` per index and sends it. Removed the redundant hover-only regen
  overlay (`.panel-regen`) in favour of the always-visible caption button.
  `.board-caption-edit` / `.board-regen-btn` styles.
- Verified: `npm run build` clean; endpoint repro test passes (reconstruct + edit +
  no 404); schema override round-trip.

### 2026-07-24 — Cast/Props: click-to-enlarge, bigger cards, reusable-assets ZIP
- **Enlarge on click:** new shared `ImageLightbox.jsx` (reuses `.lightbox-*`). Cast
  and Props/backgrounds portraits are now clickable when they have a preview
  (generated OR uploaded) → full-screen viewer with ✕ / backdrop close. Portrait
  gets `.clickable` (zoom-in cursor + gold hover ring).
- **Bigger UI:** `.cast-grid` min column 440→500px, gap 1.4rem, card padding 1.25rem;
  portrait 128→150px; `.cast-desc` min-height 60→76px. (Container already 2200px.)
- **Reusable-assets ZIP (final download):** so the user generates references once and
  re-uploads next time instead of regenerating. `GET /storyboards/{id}/bundle` zips
  the GENERATED character refs (`characters/<name>.png`) + generated prop/background
  refs (`assets/<name>.png`) + the board PDF. UPLOADED refs are EXCLUDED — refs now
  carry a `source.txt` marker written at save time (`_mark_ref_source`): "generated"
  for `/characters/reference` + `/assets/reference`, "uploaded" for
  `/characters/reference/upload`; `_ref_is_generated` includes everything except
  explicit "uploaded" (missing marker → included, for older refs). Button lives on
  the board (`StoryboardBoard.jsx` "⬇ Download assets (ZIP)"), api `downloadStoryboardBundle`.
- Verified: `npm run build` clean; bundle route registered; marker logic unit-tested
  (generated→include, uploaded→exclude, missing→include). NOT run live (needs billed
  refs + a real job to produce a populated zip).

### 2026-07-24 — Storyboard form: ＋More genre/style pickers + friendly defaults
- User ask: fewer chips up front, the rest behind a "＋ More" popup (with an ✕
  close), more visual-style options, and sensible pre-selected defaults.
- `ScriptToStoryboard.jsx`: **Genre** now shows 7 primary chips (Default, Animation,
  Cinematic, Commercial, Documentary, Educational, Mythology — Cinematic + Mythology
  are new); the other 11 (incl. ＋ Custom) live behind **＋ More** (`MorePopup`
  overlay, ✕ / backdrop-click to close). The picked overflow genre also renders as
  an active chip so the selection stays visible. **Visual style** rebuilt to match
  the reference set — 7 primary chips (Comic, Cinematic, Soft Pencil, Animation 3D,
  Watercolor Paint, Photo/Commercial, Charcoal Sketch) + ＋ More (Dark Anime,
  Flat/Vector, Noir, Stick Figure, Graphic Novel, ＋ Add Your Own Style). "Add Your
  Own Style" reveals a free-text box; `effectiveStyle()` sends that text as the style.
  **Defaults:** style pre-selected = `soft-pencil` (sketch), aspect = `16:9`
  (`DEFAULT_STYLE`/`DEFAULT_ASPECT`), so the script is the only thing gating
  generation. Reset on Start over.
- `gemini_client.py`: `_STORYBOARD_STYLE_PROMPTS` expanded with art direction for
  all new style ids (+ old ids kept as aliases). `generate_storyboard_panel` now
  falls back to treating an unknown, non-empty style value as freeform art
  direction (honours "Add Your Own Style"); empty → neutral "custom".
- `client/src/styles.css`: `.opt-chip-more` (dashed opener) + `.more-overlay/
  .more-panel/.more-head/.more-close` modal styles.
- Verified: `npm run build` clean (45 modules); style-prompt keys + freeform
  fallback unit-checked. Frontend still sends real backend calls only on generate.

### 2026-07-24 — Script→Storyboard Stage B2: prop & background consistency
- **Problem the user hit:** props/backgrounds drift between panels (the slipper in
  Shot 2 ≠ Shot 3) because ONLY characters were locked with reference images.
  Fixed by mirroring the Cast mechanism for non-character assets.
- `script_breakdown.py`: breakdown now also returns `assets` — `[{name, category
  ('prop'|'background'), description}]` — and tags each shot with `assets: [names]`.
  Prompt asks for recurring important objects (props) + revisited locations
  (backgrounds). `_coerce_assets` (dedupe by name, category→'prop' fallback) +
  `assets` added to `_coerce_shots`. Return is now `{shots, characters, assets}`.
- `gemini_client.py`: new `generate_asset_reference(description, category, provider)`
  — clean prop-on-white OR empty establishing-background plate (two new prompt
  templates), full retry/backoff, reuses `_is_valid_reference`. `generate_storyboard_panel`
  gained `asset_reference_images` + a distinct "keep these props/background
  consistent" instruction; asset refs appended AFTER character refs in `contents`.
- `storyboard_pipeline.py`: generalised ref loading (`_load_refs`) + `_gather_refs`
  (deduped BY NAME, not image value — two similar assets stay distinct). `run_storyboard`
  + `regenerate_panel` take `asset_ref_paths`, feed each shot its assets' refs
  (cap 3) alongside character refs. Panel dict carries `assets`.
- `server/schemas.py`: `Asset` model, `Shot.assets`, `ScriptBreakdownResponse.assets`,
  `StoryboardCreateRequest.asset_refs`, `AssetReferenceRequest`.
- `server/main.py`: breakdown returns `assets`; new `POST /assets/reference`
  (generate a prop/background ref — saved under the SAME `_references/{id}/reference.png`
  layout, previewed via the existing image-serve route, uploads reuse the existing
  `/characters/reference/upload`); `POST /storyboards` resolves `asset_refs` → paths
  (shared `_resolve_refs` helper) + stores `asset_ref_paths`; regenerate-panel passes
  them through. `worker.py` needed no change (kwargs spread).
- Client: new `StoryboardAssets.jsx` (mirrors `StoryboardCast.jsx`; prop/background
  badge, generate via `generateAssetReference`, upload via `uploadReference`). Flow
  is now Review → **Cast** → **Props & backgrounds** → Board, each step skippable
  (`ScriptToStoryboard.jsx`: `computeAssets()`, `handleCastNext`, `startStoryboard`
  now takes char+asset refs). `api.js`: `generateAssetReference` + `assetRefs` on
  `createStoryboard`. `.asset-badge` styles. "How it works" step 4 reworded.
- Verified WITHOUT billed calls: breakdown coercion (assets + per-shot tags);
  `run_storyboard`/`regenerate_panel` feed the RIGHT char+asset ref counts per shot
  with a STUBBED panel generator (caught + fixed a dedupe-by-value bug); route
  `/assets/reference` registered; schema round-trip; `server.main` imports;
  `npm run build` clean (45 modules). NOT run live (needs billed image calls).
- NOTE: "important movement" the user mentioned isn't lockable via a reference
  IMAGE — it lives in the shot description; only props + backgrounds get refs.

### 2026-07-24 — Storyboard: fill-width layout + bigger titles (laptop == 2K)
- Blank-space complaint was screen-scaling: fixed max-width caps left voids on a 2K
  monitor. Bumped step caps to 2200px (review/board) so content fills up to 2K;
  cast kept 1400 (compact centered), form 1500.
- Review shots now a FIXED 3-column grid (`repeat(3,1fr)`, →2 @1200px, →1 @720px) so
  the same 3-up layout shows on laptop and 2K (cards just scale). Moved the insert
  action into each card's button row (`＋`, grid-friendly) and dropped the
  between-card insert rows (Fragment removed); "Add a shot" moved below the grid
  (`.add-shot-row`).
- Board grid now `auto-fill minmax(340px,1fr)` (fills wide screens).
- Bigger workflow titles: `.wf-title` clamp(1.9→2.6rem), larger `.wf-icon` (4rem) +
  subtitle. Frontend-only; `npm run build` clean.
- NOTE: pixel-identical across resolutions isn't possible; goal is same column
  layout + no blank voids on both.

### 2026-07-23 — Breakdown: animated "scene breakdown" checklist
- New `BreakdownProgress.jsx`: replaces the form while the breakdown call runs — a
  staged checklist (Reading story → Aligning genre → Identifying characters →
  Creating breakdown) that ticks off on a ~1.1s timer (gold checks, last step keeps
  spinning until the API returns → Review). `ScriptToStoryboard` renders it when
  `busy` on the form step. `.bp-*` styles (gold theme, not the reference's green).
- Frontend-only; `npm run build` clean.

### 2026-07-23 — Genre: Default (pre-selected) + Custom (free text)
- Added a "✨ Default" chip (pre-selected; sends `genre=""` → no bias) and a
  "＋ Custom" chip (reveals `.custom-genre-input` to type any genre). `effectiveGenre()`
  resolves: Default→"", Custom→typed text, else the label minus emoji (e.g.
  "Science Fiction"). Chips now plain-select (Default is the neutral, no toggle-off).
  Resets to Default on Start over. Frontend-only; build clean.

### 2026-07-23 — Form: Genre chip section (shapes story tone)
- Added a **Genre** section to the Script→Storyboard form (same `.opt-chips` UI as
  Visual style / Aspect ratio, placed after the script): 14 genres (Action…Thriller),
  single-select, **optional** (toggle off by re-clicking; not in `canGenerate`).
  Took only the genre content from the reference — no step-bar / extra button.
- Threaded genre through: `api.js breakdownScript({genre})` →
  `ScriptBreakdownRequest.genre` → `break_down_script(..., genre=)` prepends a
  "Genre: X. Shape tone/pacing…" line to the LLM prompt.
- `.label-optional` hint style; genre reset on Start over.
- Verified: genre lands at the front of the prompt (mocked), server imports,
  `npm run build` clean, uvicorn restarted (health 200).

### 2026-07-23 — Board: "Retry all failed" bulk button
- `StoryboardBoard.jsx`: added `retryAllFailed()` + a "🔄 Retry all failed (N)"
  toolbar button (shown when `failedCount>0`, beside Download PDF). Loops failed
  panel indices sequentially through the existing `retryPanel()` (gentler on rate
  limits; per-tile spinners; in-place update). `.board-retry-all` style + toolbar gap.
- Frontend-only (reuses `POST /storyboards/{id}/regenerate-panel`); build clean.

### 2026-07-23 — Board: per-panel Retry/regenerate + safer prompt + zoom
- **"Couldn't draw this panel" cause:** the image model returns EMPTY (safety
  filter) on mildly-aggressive shots ("threateningly", "accusing", "mocking",
  "insult", "flies at the camera"). Added a family-friendly / non-graphic line to
  the panel prompt (`gemini_client.generate_storyboard_panel`) to cut blocks.
- **Retry:** `storyboard_pipeline.regenerate_panel()` (one panel, reuses refs +
  crop); `POST /storyboards/{id}/regenerate-panel {index}` (sync; updates
  job.result.panels[index] + ok_count); `character_ref_paths` now stored in
  job.params so retries keep character consistency. `api.js
  regenerateStoryboardPanel`. Board: **🔄 Retry** button on failed tiles + a hover
  **🔄** regenerate on good tiles (like Text-to-Image per-view regen), per-tile
  spinner, live in-place update.
- **Zoom:** click-to-zoom lightbox was already implemented (`.lightbox-*`); it
  works — likely a cached build; hard-refresh. (No change needed.)
- `client/src/styles.css`: `.panel-regen/.board-failed(flex)/.board-retry`;
  `.board-frame{position:relative}`.
- Verified: route registered, `regenerate_panel` imports, `npm run build` clean,
  uvicorn restarted (health 200). NOT run live (billed image call per retry).

### 2026-07-23 — Form step: two-column hero (fill the empty space)
- Vertically centering the lone form card just moved the void top+bottom. Replaced
  with a two-column hero (`.sts-hero-grid` 1.3fr/0.7fr, stacks <860px): form card
  LEFT + a "How it works" 5-step guide panel RIGHT (`.sts-guide*`). `.sb-form` now
  max-width 1100, top-anchored (no vertical centering). Fills the horizontal space
  so the form no longer floats as a small card in a big black area. Paste/upload
  160px height-match retained. Frontend-only; `npm run build` clean.

### 2026-07-23 — Form step centered as a hero (kill bottom whitespace void)
- Script→Storyboard input: `.sb-form` on the form wrapper — max-width 720, margin
  auto, `min-height: calc(100vh - 6rem)` + flex column `justify-content:center`.
  Header + card now share the 720 column (aligned) and sit vertically centred, so
  whitespace balances above/below instead of a big empty area at the bottom.
- Frontend-only; `npm run build` clean.

### 2026-07-23 — Storyboard UI polish: matched cast buttons + centered columns
- Cast page: Generate + Upload buttons now identical (both `.btn.secondary.cast-btn`,
  equal `flex:1`, same colour/size) instead of one gold-outline + one plain.
- Cast grid: grid→flexbox `justify-content:center` so an odd number of characters
  (e.g. 3) is balanced, not left-hugging with an empty cell.
- Reduced dead right-side whitespace across storyboard steps: centered content
  columns via `.sb-review` (880px) / `.sb-cast` (1000px) / `.sb-board` (1120px) on
  each step's `workflow-head-wrap`; review inner blocks fill the column.
- Frontend-only; `npm run build` clean.

### 2026-07-23 — Review shots: honest cast count + button loader/label tweaks
- **Cast count bug:** "Next: cast (N)" used the full breakdown cast (e.g. 7) even
  after deleting shots. Added `computeCast()` — derives the ACTIVE cast from the
  CURRENT shots' character names (deduped, descriptions pulled from the breakdown
  when available). Used for the review button count, the cast-step props, and the
  cast-vs-generate branch. Deleting shots now shrinks the cast honestly.
- Renamed the between-shots inserter label "＋ Insert shot here" → "＋ Add a shot".
- Breakdown loading button: replaced the tiny inline dot with a circular ring
  spinner (`.btn-loading`/`.btn-ring`) + text (indeterminate — single call, no %).
- Frontend-only; `npm run build` clean.

### 2026-07-23 — Review shots: insert-a-shot-between button
- `ScriptToStoryboard.jsx`: added `insertShot(index)` (splices a blank shot AFTER
  a position) + `blankShot()` helper; each shot card now renders a subtle dashed
  "＋ Insert shot here" pill BETWEEN it and the next card (wrapped shots in
  `Fragment`). Bottom "＋ Add a shot" (append) kept. Shot numbers are positional
  (i+1) so they renumber automatically; inserted shot inherits the prior scene_number.
- `client/src/styles.css`: `.shot-insert-row` / `.shot-insert-btn`.
- Frontend-only; `npm run build` clean (no backend restart needed).

### 2026-07-23 — Cast: upload-your-own character image
- `server/main.py`: `POST /characters/reference/upload` (multipart) — validates
  type/size, normalises any JPEG/PNG/WebP to a clean RGB `reference.png` under the
  SAME `uploads/_references/{id}/` layout as generated refs, returns a reference_id.
  So uploaded refs plug straight into `POST /storyboards` `character_refs`.
- `client/src/api.js`: `uploadReference(file)`.
- `client/src/components/StoryboardCast.jsx`: each cast card now has **✨ Generate**
  AND **📁 Upload** (hidden file input per card; previews the chosen file directly).
- `client/src/styles.css`: `.cast-actions/.cast-upload-btn` (two-button row).
- Verified E2E via TestClient (local user store): register → upload JPEG → saved as
  reference.png → served back as PNG (200) → bad type 415; route registered;
  `npm run build` clean; uvicorn restarted (health 200, route 401 w/o token).

### 2026-07-23 — Script→Storyboard Stage B: character consistency
- `script_breakdown.py`: breakdown now returns `{shots, characters}` in ONE call
  (schema changed ARRAY→OBJECT; prompt also asks for a cast with visual
  descriptions; `_coerce_characters` dedupes by name). `break_down_script` return
  type changed list→dict — callers updated.
- `gemini_client.py`: `generate_storyboard_panel(..., reference_images=[...])` —
  passes character reference images alongside the prompt with a "keep characters
  consistent, redraw in this panel's style" instruction.
- `storyboard_pipeline.py`: `run_storyboard(..., character_ref_paths={name:path})`
  loads refs once and feeds each shot only the refs for the characters IN that shot
  (cap 3/panel).
- `server/schemas.py`: `Character` model; `ScriptBreakdownResponse.characters`;
  `StoryboardCreateRequest.character_refs` ({name: reference_id}).
- `server/main.py`: breakdown returns characters; `POST /storyboards` resolves
  reference_ids → `uploads/_references/{id}/reference.png` paths (skips missing).
  Reuses the existing `POST /characters/reference` to MAKE the refs.
- `client/src/components/StoryboardCast.jsx` (new): cast page — per character, edit
  description + "Generate reference" (reuses `generateReference`), optional/skippable.
  New flow: form → review → **cast** (only if named characters) → board.
  `ScriptToStoryboard.jsx` wires it (`handleReviewNext` → cast or straight to
  generate; `startStoryboard(refs)`); `api.js createStoryboard` sends `character_refs`.
- `client/src/styles.css`: `.cast-grid/.cast-card/.cast-portrait/.cast-body` etc.
- Verified WITHOUT billed calls: breakdown returns deduped `{shots, characters}`
  (mocked genai); pipeline feeds exactly the right ref count per shot ([1,0] test);
  server imports; `npm run build` clean; uvicorn restarted (health 200). NOT run
  live. NOTE: refs reuse the T-pose "reference" generator (Pixar-ish white-bg
  portrait) as the identity anchor — panel style still applies; may bias look, tune
  later. Upload-your-own-character-image not built (generate-from-text only).

### 2026-07-23 — Script→Storyboard Stage F: PDF export
- New `storyboard_pdf.py`: `build_storyboard_pdf(job_id, output_dir, title, panels)`
  — composes panels into a printable PDF (2×3 grid/page, title, wrapped captions,
  shot numbers) using **Pillow only** (each page rendered as an image → multi-page
  PDF, zero new deps). Skips failed/missing panels; raises ValueError if none.
- `server/main.py`: `GET /storyboards/{job_id}/pdf` (owner-scoped, STORYBOARD only,
  409 if no panels; streams the PDF as a download with a safe filename).
- `client/src/api.js`: `downloadStoryboardPdf(jobId, filename)` (authed blob).
- `client/src/components/StoryboardBoard.jsx`: "⬇ Download PDF (N)" button in a
  toolbar (shown once ≥1 panel succeeded), spinner while preparing, error surface.
- `client/src/styles.css`: `.board-toolbar`.
- Verified WITHOUT billed calls: `build_storyboard_pdf` on stub-generated panels →
  valid `%PDF-` file, 7 panels→2 pages, caption wrapping, empty-panels guard; route
  registered; `npm run build` clean; uvicorn restarted (health 200, `/pdf` 401).
- **Simple MVP pipeline is now complete end-to-end: script → shots → review →
  panels → board → PDF.** (Live billed run still not executed.)

### 2026-07-23 — Script→Storyboard Stage D: panel generation + live board
- `gemini_client.py`: added `generate_storyboard_panel(description, style,
  aspect_ratio, characters, location, camera, provider)` — single text→image call
  (uses the IMAGE backend), per-style + per-aspect prompt phrasing, retry/backoff.
- New `storyboard_pipeline.py`: `run_storyboard(job_id, shots, style, aspect_ratio,
  output_dir, provider, progress_cb)` — loops shots, generates each panel,
  centre-crops to the exact ratio (`_crop_to_aspect`), saves to
  `output/_storyboards/{job_id}/panel_NN.png`, streams progress + partial panels.
  Failed panels flagged (not fatal). Hard result: {style, aspect_ratio, count,
  ok_count, panels[]}.
- `server/schemas.py`: `JobKind.STORYBOARD`, `StoryboardCreateRequest`.
- `server/worker.py`: `submit_storyboard_job` + `_run_storyboard` (streams partial
  panels into job.result, mirrors `_run_generate`).
- `server/main.py`: `POST /storyboards` (async job) + `GET /storyboards/{job_id}/
  panel/{index}` (owner-scoped PNG serve). Poll via existing `GET /jobs/{id}`.
- `client/src/api.js`: `createStoryboard(...)`, `fetchStoryboardPanel(jobId, index)`
  (authed blob).
- `client/src/components/StoryboardBoard.jsx` (new): polls the job, gold live
  progress bar, panel grid that fills in as each finishes (skeletons for pending,
  ⚠️ for failed), click-to-zoom lightbox. `ScriptToStoryboard.jsx`: "Generate
  panels" now creates the job → board step; Back-to-shots / Start-over.
- `client/src/styles.css`: `.board-grid/.board-tile/.board-frame/.board-skeleton/
  .board-failed/.board-shotnum` etc.
- Verified WITHOUT billed calls: backend imports + all 3 storyboard routes
  registered; `_crop_to_aspect` (16:9→1280×720, 9:16, 1:1, 21:9); full
  `run_storyboard` with a STUBBED panel generator (saves cropped files, flags empty
  shot as failed, 5 progress ticks, correct result); `npm run build` clean; uvicorn
  restarted (health 200, `/storyboards` 401 w/o token). NOT run live (needs real
  billed image calls per panel).
- NEXT (Stage E/F): board polish (per-panel regenerate is v3) + **PDF export**.

### 2026-07-23 — Script→Storyboard: button wired + Review-shots page (Stage C)
- `ScriptToStoryboard.jsx`: "Generate storyboard" now resolves the script (pasted,
  or read from an uploaded TXT/Fountain/FDX/MD file — PDF/DOCX show a "paste for
  now" message), calls `api.breakdownScript(text, {style, aspectRatio})` with a
  loading state, then advances to a **Review shots** step. Review page (T2I look:
  WorkflowHeader + `.card`s) lists each shot with an editable description +
  camera/location inputs + character chips, and per-shot **↑ / ↓ reorder** and
  **✕ delete**, plus **＋ Add a shot**. Footer: Back (to form) + "Generate panels"
  (shows a "coming soon" notice — panel generation is the next step).
- `client/src/styles.css`: added `.review-summary/.shot-list/.shot-card/.shot-head/
  .shot-index/.shot-actions/.shot-btn/.shot-desc/.shot-meta/.shot-chars/
  .add-shot-btn/.review-actions`.
- Restarted uvicorn so `POST /storyboards/breakdown` is live (health 200; route
  returns 401 without a token, as expected). Verified `npm run build` clean.
- NOT run live end-to-end (needs a real Vertex/Gemini text call from the browser).
- NEXT (Stage D): generate one image per reviewed shot (reuse job/worker + live
  progress) → board (Stage E) → PDF (Stage F).

### 2026-07-23 — Script→Storyboard Stage A: switchable script breakdown (Vertex⇄Gemini)
- New `script_breakdown.py` (project root, mirrors `gemini_client.py`): `break_down_script(
  script_text, provider=None, max_shots=60) -> list[shot dict]`. Uses the Gemini
  text model via the SAME dual-backend pattern as images — `TEXT_PROVIDER` env
  (vertex default | gemini), independent of `IMAGE_PROVIDER`, per-provider client
  cache, `VERTEX_TEXT_MODEL`/`GEMINI_TEXT_MODEL` overrides (default
  `gemini-2.5-flash`). Structured JSON output via `response_schema`; each shot =
  `{scene_number, shot_number, description, characters[], location, camera}`.
  Retry/backoff + `ScriptBreakdownError` with human-readable reasons; short-script
  guard; hard cap of 60 shots.
- `server/schemas.py`: `ScriptBreakdownRequest` (script + optional style/aspect_ratio/
  provider), `Shot`, `ScriptBreakdownResponse`.
- `server/main.py`: `POST /storyboards/breakdown` (auth'd, sync; lazy-imports the
  genai chain; 400 on bad provider, 502 with the real reason on failure).
- `client/src/api.js`: `breakdownScript(script, {style, aspectRatio, provider})`.
- `.env` + `.env.example`: documented `TEXT_PROVIDER` + text-model overrides.
- Verified WITHOUT a billed call: provider resolution (default/explicit/env/override/
  bad), model-id overrides, shot coercion (defaults, empty-desc drop, non-dict skip),
  short-script guard; `server.main` imports and the route is registered; `npm run
  build` clean. NOT verified live (needs a real Vertex/Gemini text call).
- NEXT: wire the "Generate storyboard" button → call breakdown → **Review shots**
  page (Stage C) → then panel generation.

### 2026-07-23 — Script→Storyboard input redesigned (single page, matches T2I)
- User disliked the Drawstory-copied look (centered composer + style/aspect card
  wizard). Rebuilt `ScriptToStoryboard.jsx` as ONE page in the Text-to-Image
  design language: WorkflowHeader (📝 icon + title + subtitle) → a single `.card`
  with stacked sections — script (tab-bar Paste/Upload → `.prompt-textarea` /
  `.dropzone`), visual style (selectable `.opt-chip`s), aspect ratio (chips) → one
  gold `.btn.primary` "Generate storyboard" (disabled until script + style +
  aspect chosen). Removed the multi-step wizard + all Drawstory-style CSS.
- `client/src/styles.css`: deleted the old `.sts-*/.style-*/.ratio-*` block
  (~390 lines); added `.sts-form-wrap/.opt-chips/.opt-chip(.active)/.sts-generate`.
- Decisions locked with user: Simple pipeline **+ Review/edit-shots** feature;
  single-page input; keep everything in the T2I look (no Drawstory copy).
- Verified: `npm run build` clean (CSS shrank). NEXT: backend Stage A (LLM shot
  breakdown) → review-shots page → panel generation → board → PDF.

### 2026-07-23 — Local user-store fallback (fixes login when Mongo is down)
- **Problem:** login showed "Can't reach the server" — backend WAS up (`/health`
  200) but `/auth/*` 500'd because MongoDB Atlas is unreachable (TLS handshake);
  the 500 reaches the browser without CORS headers, so fetch throws the network
  message. Root cause is Atlas connectivity (IP allowlist / paused cluster /
  corporate TLS), not the repo.
- **Fix:** `server/users.py` now supports two backends behind the same public API,
  selected by `config.USER_STORE`: `"mongo"` (default) or `"local"` (a JSON file,
  `API_LOCAL_USERS_PATH`, default `.local_users.json`). Auth now works with no
  MongoDB. `check_connection()` reports the local store as connected.
- `server/config.py`: added `USER_STORE` + `LOCAL_USERS_PATH`.
- `.env`: set `API_USER_STORE=local` (documented why); `.env.example`: documented
  both keys (default `mongo`). `.gitignore`: ignore `.local_users.json` (holds
  bcrypt hashes).
- Verified: unit test (register/dup/case-insensitive lookup/verify), then live
  over HTTP after restarting uvicorn — `/health` → `status: ok`, register → 201,
  correct login → token, wrong pw → 401. Reset store to empty afterward.
- **NOTE:** local store starts EMPTY — the user must **Register** before logging
  in. To go back to Atlas once fixed, set `API_USER_STORE=mongo` and restart.
  uvicorn does NOT reload on `.env` changes — full restart required.

### 2026-07-23 — Script→Storyboard step 3 (Select Your Aspect Ratio)
- `client/src/components/ScriptToStoryboard.jsx`: added `step: "aspect"`. Step 2's
  Next now advances to Step 3; Step 3 is a gold-themed aspect-ratio picker (21:9,
  16:9, 9:16, 2:3, 1:1) with an outlined frame icon sized to each ratio, Back →
  step 2, Next disabled until a ratio is chosen (then "coming soon" notice).
- `client/src/styles.css`: added `.ratio-card/.ratio-frame(-box)/.ratio-name/
  .ratio-desc` (reuses `.style-grid` + `.style-card` base + gold selected ring).
- Verified: `npm run build` clean.

### 2026-07-23 — Script→Storyboard step 2 (Select Your Style)
- `client/src/components/ScriptToStoryboard.jsx`: now a two-step flow with internal
  `step` state. Step 1 (script composer) arrow button advances to Step 2 "Select
  Your Style" (only when there's text/file). Step 2 is a gold-themed card picker
  (Sketch / Comics / Realistic / 3D Animation + a dashed Custom card), Back returns
  to step 1, Next is disabled until a style is chosen and (no backend yet) shows a
  "coming soon" notice. Content + buttons mirror the Drawstory reference; design is
  our own (icon + gradient preview tiles, gold selected-ring + ✓ badge) rather than
  a copy. Both steps share one visual language (panels, pill buttons, title styling).
- `client/src/styles.css`: added `.style-wrap/.sts-page/.sts-back/.style-grid/
  .style-card(.selected)/.style-preview/.style-check/.style-custom/.sts-next` styles.
- Verified: `npm run build` clean.

### 2026-07-23 — Login password show/hide toggle + Mongo-error diagnosis
- `client/src/components/Login.jsx`: added a show/hide password toggle (eye /
  eye-off inline SVG icons, `showPassword` state, `type` switches text/password).
- `client/src/styles.css`: added `.password-field` / `.password-toggle` styles
  (gold hover, positioned inside the input).
- **Diagnosed the "Can't reach the server" login error:** the backend runs fine
  (`/health` → 200) but auth returns 500 because **MongoDB Atlas rejects the TLS
  handshake** (`TLSV1_ALERT_INTERNAL_ERROR`). Confirmed NOT a code bug — DNS
  resolves, TCP to shard:27017 connects, general outbound TLS works, and passing
  `certifi.where()` as `tlsCAFile` does NOT help (Atlas sends the alert). Root
  cause is almost certainly the **client IP not in Atlas Network Access allowlist**
  (or a paused cluster). Fix is in the Atlas dashboard, not the repo. The frontend
  "Can't reach the server" text specifically appears only when uvicorn isn't
  running (fetch rejects); once it's up the user sees the 500 detail instead.
- Verified: `npm run build` clean.

### 2026-07-22 — Script→Storyboard composer UI (design-matched, gold theme)
- New `client/src/components/ScriptToStoryboard.jsx`: centered "composer" hero
  matching the Drawstory reference (big title, subtitle, rounded script textarea
  with "Create…" placeholder, pill "+ Upload" button + circular send button,
  helper footnote). Reuses the app's champagne-gold palette instead of the
  reference's purple. Supports drag-and-drop file attach (PDF/txt/fountain/fdx/
  docx) with a removable file chip; send is disabled until there's text or a file.
  No storyboard backend yet, so submit shows a friendly "coming soon" notice.
- `client/src/App.jsx`: routes `nav === "script-to-storyboard"` to the new
  component (removed its entry from the `SOON` placeholder map).
- `client/src/components/Sidebar.jsx`: `script-to-storyboard` status `soon → live`
  (shows the live dot, not the "Soon" badge).
- `client/src/styles.css`: added `.sts-*` composer styles (focus-within gold glow,
  drag-over state, gold gradient send button, file chip, notice).
- Verified: `npm run build` clean (41 modules, 0 errors).

### 2026-07-22 — Client redesign, subject templates, live progress, per-section 3D
Large multi-part session (committed across `2fc6562`, `e58b276`, `28902a0`).

**Generation quality / pipeline**
- `splitter.py`: removed the forced square resize — quadrants keep natural aspect
  (fixes stretched/tall subjects from widescreen 1408×768 grids).
- `postprocess.py`: added `clean_and_normalize_group()` — the 4 views of a part
  share ONE scale factor, so the subject is the same size across front/left/¾/back
  (fixes "front doesn't match the other 3").
- `prompts.yaml`: hardened all prompts (NO text/labels/borders/gutters, single
  subject, keep proportions). Pants = one pair/panel; shoe = one shoe. Added
  `watch`. **Subject-type templates** with per-template `parts_order`: `default`
  (gender INFERRED from reference — fixes girl→boy), `human_male`, `human_female`,
  `robot`, `animal`, `bird`, `monster`, `ghost`. Removed `saree`. `body` is a
  safe mannequin-style base body (avoids the safety-filter block on "shirtless").
- `pipeline.py`: process each part fully (generate→split→clean→save) in ONE loop
  with a `progress_cb` (percent/stage/current_part/done/total + partial urls).
  Clears the character dir at run start (no stale files). Tracks `failed_parts`.
  `_resolve_prompts` uses a safe formatter (missing `{gender}` etc. → blank, so
  non-human templates don't crash) + `_resolve_parts_order`. `_generic_part_prompt`
  lets CUSTOM assets (e.g. "mobile") generate. Reference-only fullbody is excluded
  from the visible count and shown as a quiet "Preparing base reference" step.
  Added `regenerate_single_part` (clears failed flag) + `regenerate_single_view`.
- `storage.py`: zip skips `_`-prefixed helper files (raw fullbody sheet).
- `gemini_client.py`: `generate_character_reference` now raises
  `ReferenceGenerationError` with the ACTUAL cause (429 / API error / block /
  empty) instead of returning a generic "content filter" message.

**Backend API**
- `server/main.py`: partial `/assets` while running; `/image/{part}/{view}`;
  per-section `/download/{part}`; `/regenerate-view`; `provider` on `/meshy`
  (routes to `meshy` or `tripo`, resolves saved key); `/templates` returns
  per-template parts + label.
- `server/auth.py` + `users.py`: `DELETE /auth/me`; saved 3D API keys
  (`api_keys.{provider}`, plaintext) with GET/PUT/DELETE endpoints.
- `server/worker.py`: `_run_generate` wires the progress callback; `_run_meshy`
  dispatches Meshy vs Tripo.
- `tripo.py`: new Tripo client mirroring `meshy.py`. **UNVERIFIED** (no live key).

**Client (React)**
- New flow: `Landing → Login → sidebar dashboard`. PicLumen-style dark sidebar
  (Home + Workflows: Text-to-Image live, others "Soon"; profile chip → logout
  popup; gold Upgrade). Home page (profile, plan/credits placeholder, recent work
  + downloads, saved API keys, delete account). Login gained a Google button
  (UI only) + drag-and-drop upload.
- `JobDetail`: live gold progress bar + per-section loading skeletons (incl. the
  first part), incremental gallery, per-view 🔄 and per-section regenerate,
  failed-part retry cards, per-section Download/Generate-3D (matched buttons),
  3D popup (Meshy/Tripo + key, optionally saved to profile).
- `GenerateForm`: subject-type dropdown drives the parts multi-select (chips + X),
  custom-asset field; part tags persist after submit.
- `api.js`: friendly "can't reach server" errors; cache-busted cloud zip download.
- Login lands on Text-to-Image; all-gold gradient buttons.

**Verified:** `npm run build` clean throughout; backend imports + all templates
resolve (no missing prompts / unresolved placeholders); mocked pipeline run
confirms progress stages, per-part incremental urls, custom-asset generation, and
correct visible part count. **NOT verified live:** Meshy/Tripo 3D calls (need real
keys; Tripo entirely unverified), and the shirtless-mannequin `body` prompt may
still occasionally be safety-filtered.

### 2026-07-21 — Step 0: text-to-reference image generation
- `gemini_client.py`: added `generate_character_reference(description, provider)`
  with a Pixar-style T-pose prompt template, validation, and full retry/backoff.
- `server/schemas.py`: added `ReferenceRequest` and `ReferenceResponse` models.
- `server/main.py`: new `POST /characters/reference` (generates + saves under
  `uploads/_references/{id}/`) and `GET /characters/reference/{id}/image` (serves
  preview). Updated `POST /characters` to accept optional `reference_id` OR file
  upload (exactly one required). Uses `shutil.copy2` to link generated refs.
- `client/src/api.js`: added `generateReference()` and `getReferenceImageUrl()`.
- `client/src/components/GenerateForm.jsx`: redesigned with two-tab UI
  (“✨ Describe Character” / “📁 Upload Image”). Describe tab: textarea +
  generate button + loading spinner + preview + regenerate. Upload tab: existing
  file picker. Both paths feed into the same pipeline form.
- `client/src/styles.css`: added tab-bar, prompt-textarea, ref-preview,
  ref-actions, secondary button, spinner-inline styles.
- `run_character.py`: added `--prompt` flag via mutually exclusive group
  (`--image | --prompt`). When `--prompt` is used, Step 0 runs first, saves
  `reference_generated.png`, then feeds it into the pipeline.
- Verified: `npm run build` passes (36 modules, 0 errors); CLI `--help` shows
  `(--image IMAGE | --prompt PROMPT)` correctly.

### 2026-07-20 — Per-user Meshy API key in client
- `client/src/components/JobDetail.jsx`: added `meshyKey` state and a
  `type="password"` input field in the Meshy bar. Button is disabled until both
  parts are selected AND an API key is entered. Key is NOT persisted to
  localStorage (session-only for security).
- `client/src/api.js`: `submitMeshy()` now accepts optional `meshyApiKey`
  param, sends it as `api_key` in the request body.
- `client/src/styles.css`: added `.meshy-key-input` styling.
- Backend already supported `api_key` in `MeshyRequest` schema — no backend
  changes needed.
- Verified: `npm run build` passes cleanly (36 modules, 0 errors).

### 2026-07-20 — Live end-to-end verified + config
- Configured `.env` for real: **MongoDB Atlas** URI, generated `JWT_SECRET`,
  `API_JOB_STORE=memory`. Seeded first user via `seed_admin.py`.
- Ran the full flow in the browser successfully: login → upload → generate
  (Vertex AI) → GCS upload → 4-view gallery → download zip. Confirms Vertex auth
  and GCS are working.
- Fixed a UX nit in `JobList.jsx`: transient poll errors no longer show a red
  banner when jobs are already loaded (only shown when the list is empty).
- Gotcha learned: `uvicorn --reload` does NOT reload on `.env` changes (only code)
  — the backend must be fully restarted after editing `.env`.

### 2026-07-20 — Phase 3 client (React + Vite)
- New `client/` app (React 18 + Vite 5, no router — view state in `App.jsx`).
  Full flow: login/register → upload + template/provider/options → start job →
  live poll → gallery of views → download zip → select parts → trigger Meshy 3D.
- Files: `src/api.js` (fetch client, JWT in localStorage, authenticated blob
  download), `components/Login.jsx`, `GenerateForm.jsx`, `JobList.jsx`
  (auto-polls while jobs active), `JobDetail.jsx` (polls, gallery, download,
  Meshy), `App.jsx`, `styles.css` (dark theme).
- API base via `VITE_API_BASE` (default `http://127.0.0.1:8000`); backend CORS is
  already `*` so no proxy needed.
- Verified: `npm install` + `npm run build` succeed (36 modules, clean bundle).
  NOTE: not yet run against a live backend in a browser. Gallery previews need a
  cloud (non-local_only) run since local_only URLs are server file paths.
  `npm audit` shows 2 dev-only (vite/esbuild) advisories — not runtime-blocking.

### 2026-07-20 — MongoDB health + seed-admin script
- `api/users.py`: added `check_connection()` (pings Mongo, never throws) and
  `update_password()`.
- `api/main.py`: `/health` now reports `mongodb` connectivity and flips `status`
  to `degraded` when Mongo is down. `?check_db=false` skips the ping for a fast
  liveness check.
- Added `seed_admin.py`: create the first user (or reset a password with
  `--update-password`) from the CLI. Prompts securely for the password if omitted;
  fails fast with a clear message if Mongo is unreachable.
- Verified (no live Mongo): health up/down/degraded logic (mocked), seed --help,
  and graceful "cannot reach MongoDB" failure against a bogus URI.

### 2026-07-20 — Provider smoke-test script
- Added `smoke_test_providers.py`: makes ONE minimal real image call per backend
  (`--provider vertex|gemini|both`) to confirm auth + model name + image output
  before a full run. Actionable hints for common failures (wrong model → set
  *_IMAGE_MODEL, missing key, ADC not set, quota/permission). `--save` writes to
  `output/_smoke/`. Exit code 0 only if all tested providers pass (CI-friendly).
- Verified without billed calls: --help, reference loader, error-hint mapping,
  and the graceful no-key failure path. NOTE: a real pass/fail per backend still
  needs the user to run it with live credentials.

### 2026-07-20 — Per-job asset-listing endpoint
- Added `GET /jobs/{id}/assets` (owner-scoped) returning individual PNG URLs:
  nested `parts` map ({part: {view: url}}) + flat `assets` list + `zip`, with an
  `is_local` flag (true for local_only runs). Added `AssetItem` / `AssetsResponse`
  to `api/schemas.py`.
- Verified via TestClient: ready (200, 4 flat items), not-ready (409),
  local_only (is_local=true), owner isolation (404), no-token (401).

### 2026-07-20 — Image backend switch (Vertex AI ⇄ Gemini API)
- `gemini_client.py`: added `_resolve_provider`, `_model_id`, per-provider client
  cache; `generate_turnaround_sheet(..., provider=...)`. Backend selectable via
  `IMAGE_PROVIDER` env, with per-provider model overrides. Clear error if
  `gemini` chosen without `GEMINI_API_KEY`.
- `pipeline.py`: `run_pipeline(..., provider=...)`, logs active provider.
- `run_character.py`: `--provider {vertex,gemini}` flag.
- `api/main.py`: `provider` form field on `POST /characters` (validated 400 on bad
  value, stored on job), `default_image_provider` in `/health`.
- `.env.example`: documented `IMAGE_PROVIDER`, `GEMINI_API_KEY`, model overrides.
- Verified: provider resolution, model overrides, missing-key error, CLI help,
  API validation + pass-through. (Not verified live: exact model name parity
  across both backends — override via `GEMINI_IMAGE_MODEL` if needed.)

### 2026-07-20 — Auth (JWT + MongoDB), no Firebase
- Added `api/security.py` (bcrypt + JWT), `api/users.py` (MongoDB user store,
  unique index on email), `api/auth.py` (register/login/me + `get_current_user`).
- `api/main.py`: mounted auth router; all pipeline endpoints require a Bearer
  token; `/health` public.
- Jobs scoped to owner: added `Job.owner`; `create`/`list` take an owner;
  cross-user access returns 404. Updated `api/jobs.py`, `api/schemas.py`.
- `requirements.txt` + `.env.example`: pyjwt, bcrypt, pymongo, email-validator;
  `JWT_SECRET`, `MONGODB_URI`, etc.
- Verified full flow via TestClient (mocked Mongo, stubbed worker): register/
  login/me, wrong-password 401, duplicate 409, owner isolation.

### 2026-07-20 — Phase 2 API (async jobs + Firestore)
- Built FastAPI backend in `api/`: `config.py`, `schemas.py`, `jobs.py`
  (Firestore + in-memory fallback), `worker.py` (ThreadPoolExecutor), `main.py`.
- Endpoints: upload+generate, list/poll jobs, download zip, standalone Meshy,
  templates, health. Async job model — `POST /characters` returns `job_id`
  immediately; poll `GET /jobs/{id}`.
- Installed fastapi, uvicorn, python-multipart. Verified imports, routes,
  upload validation, and job enqueue (worker stubbed).

### (pre-existing) — Phase 1 CLI pipeline
- Full character asset pipeline (Gemini → split → post-process → store → zip →
  Meshy) driven by `run_character.py`. Successfully generated `kamla` hair assets.

---

## 🎯 Current State / Next Steps

**Current state:** Full app works end-to-end in the browser (verified live by the
user against Vertex AI + GCS): Landing → Login → Text-to-Image workflow with
subject-type templates, live per-section progress, incremental gallery,
per-view/per-section/failed-part regeneration, per-section + full zip download,
and a per-section 3D popup. Home dashboard (profile, recent work, saved API keys,
delete account) done. Sidebar has placeholder "Soon" workflows for the future
script→storyboard→animatics→video pipeline.

**Known-good this session (user-confirmed in browser):** default (gender-inferred)
human generation, per-part progress + skeletons, custom assets, safe body base
mesh, zip cache-bust.

**CONTINUITY (2026-08-09) — read before changing any panel prompt.** A board is
generated as ONE FILM, not as N independent pictures. Four things hold it
together, in order of how much work they do:
1. **The written bible** — the cast's and assets' visual descriptions go into
   every panel prompt, scoped to who/what is in that panel. This is the one that
   works with no reference images, which is the *normal* case: Rough Sketch (the
   default style) skips the cast step by design.
2. **Scene look-anchors** — the first drawn panel of each scene is fed to the
   rest of that scene. One anchor per scene, never chained.
3. **Story flow** — each panel is told the shots either side of it as context it
   must not draw.
4. **Reference images** — as before, when the style generates them.
Sending a panel prompt without (1) is what made the same character come back as a
different person; don't add a code path that skips it.

**KEY POSES (2026-08-09) — a shot animates its own moment and NOTHING further.**
The same continuity discipline applies one level down, and two rules carry it:
1. **Pose 1 is the panel, copied — never generated.** The board's panel is
   already approved; drawing it again produces a different picture, which is the
   first thing anyone sees on opening the zip. `run_panel_sequence` copies the
   file. Don't "improve" this by prompting for a faithful reproduction.
2. **A shot may not outrun its own description.** `plan_beats` is given the
   shots either side of this one AND returns `hold` — one sentence naming what
   stays true in every drawing, which is then handed to every drawing and
   overrides the "the body must have MOVED" push. Without both, an establishing
   wide of a sleeping man came back as eight drawings of him waking up, directly
   before the close-up that shows him still asleep. A held shot is a real shot:
   the motion is breathing and the quilt settling, not an invented event.
Regression check: `python tests/key_pose_scope_check.py [--live]`.

**REGENERATING A PICTURE THAT IS ALREADY ON SCREEN (2026-08-09) — three rules,
and they apply to EVERY workflow, not just key poses.** All three were broken at
once and the user's report was simply "I can't see any changes":
1. **A regenerate must actually redraw.** Sending the resume flag is not a
   regenerate — on complete work there is nothing missing, so the server
   correctly does nothing and the click is a no-op. Keep resume as its own,
   separately-labelled button ("Draw the remaining N") so the two costs stay
   visibly different.
2. **A redrawn image must get a NEW URL.** Every image in this app is fetched as
   an authed blob and cached by path; a path that survives a redraw is a picture
   that never updates. Stamp a version in — `?v=<mtime>` for key-pose frames,
   `?v=<variant>` for panels, `?t=` for character views.
3. **It must LOOK like it is working, over the old picture.** A shimmer that only
   renders when there is no image shows on a first run and never on a redraw.
   Use `.is-redrawing` + `.redraw-veil` (blur the old picture, veil it, name the
   action) — the shared treatment used by `PanelSequenceStrip`, `StoryboardBoard`,
   `JobDetail` and `RegeneratePanelInline`. Blur, don't blank: the layout must
   not jump and the user has to see WHICH image is being replaced.
Regression check: `python tests/key_pose_refresh_check.py`.

**RULE 2 HAS TWO HALVES, and Phase 7 found that out (2026-08-17).** Stamping a
version into the url is only the SERVER's half. The client caches a blob per url
and has to notice the url MOVED — which means remembering what it fetched, not
just whether it fetched. The animatic editor cached by frame id alone, so a
redrawn panel (same frame id, same route, new `?v=`) was a picture that never
updated even though the server was serving the new bytes. See `_frame_version`
in `server/animatics.py` and `urlSrcRef` in `AnimaticEditor.jsx`; the pair is
checked in `tests/autoframe_check.py`. If you add a third place that shows these
pictures, it needs both halves too.

**REFRESH ONLY WHAT CHANGED (2026-08-09).** A fourth rule, learned the same way:
every picture here is an authed blob cached by URL, so *how much cache you throw
away decides how much of the page blinks*. `StoryboardBoard` has two tools and
they are not interchangeable:
- `refreshPanelImage(index, url)` — ONE panel. Fetches the new bytes first, then
  swaps, so the tile never renders empty. Use for a version switch, a single
  redraw, anything scoped to one shot.
- `reloadBoard()` — the whole cache. Use ONLY for insert / delete, where indices
  shift and a blob keyed by `/panel/2` now belongs to a different shot.
Wiring the second to a single-panel action is what made the entire board
re-download on every ‹ › press. When a swap replaces a blob, retire it rather
than revoking it on the spot — the `<img>` is still showing it until React
commits.

**NEVER ASK FOR A "PANEL" (2026-08-09).** In comics and storyboard training data
a panel IS a bordered rectangle, so the word makes the model draw the box as well
as the picture — a freehand frame just inside the edge, no two alike, on 138 of
371 real panels. Prompts say **full-bleed IMAGE / artwork**, never "panel", and
`strip_drawn_border()` (first step of `normalise_panel`, so panels, key poses and
redraws all get it) crops any frame that still appears. If you add a style
string, do not put "panel" in it. Regression check:
`python tests/panel_border_check.py`.

`normalise_panel` returns the size it was GIVEN, on every path — a board stays
uniform only because of that. It has one exit for the same reason; don't add an
early `return` that skips the final resize.

**Script → Storyboard now opens on "Your Storyboards"** (Stage G): every generated
board is saved and re-openable, with rename / duplicate / delete / public share
link per card. Note that persistence follows the job store — under
`API_JOB_STORE=memory` the library empties when the backend restarts.

**Profile (2026-08-04):** the app opens on **Home**. A Profile page holds
identity (name, display name, timezone), work (company, role) and **storyboard
defaults** (style / aspect / genre) that pre-fill the new-storyboard form.
3D API keys, password change and Delete account moved there from Home.
`PATCH /auth/me` is allow-listed — it can never write `password_hash`,
`disabled` or `email`.

**Review step is durable (2026-08-04):** a breakdown is saved immediately as a
`DRAFT` storyboard job, so reviewed shots / cast / assets / world survive a
refresh; Generate PROMOTES that same record into the board. Drafts are hidden
from the library and resumed via `GET /storyboards/draft`.

**Storage (2026-08-04):** MongoDB is now the system of record for EVERYTHING
except image/video bytes — accounts, script drafts, and every job (character
runs, storyboards, animatics, and any workflow added later) via the one
`JobStore` interface. 18 existing jobs migrated. GCS URLs land in the job's
`result`, so they persist in Mongo the moment GCS is enabled. See the
**Storage rule** section near the top before adding a workflow.

**Script autosave (2026-08-04):** the text panel now saves what you type to
MongoDB on a debounce (`/scripts/draft`, one draft per user) and restores it on
load, so a refresh no longer loses an ungenerated script. User accounts moved to
Mongo too (`API_USER_STORE=mongo`). Generated WORK is still not in Mongo: jobs
and storyboards remain `API_JOB_STORE=memory` + `.local_jobs.json`, and images
stay on local disk under `output/` and `uploads/`.

**Breakdown trust (2026-08-03):** the script breakdown runs greedy with a fixed
seed (`TEXT_TEMPERATURE` / `TEXT_TOP_P` / `TEXT_SEED`), and every breakdown now
returns a `grounding` report saying which panels the script actually supports.
Image generation stays non-reproducible — that API exposes no seed.
**Open follow-up:** show `grounding.warnings` on the shot-review screen (it's in
the API response and the logs today, but invisible in the UI).

**VEO WORKS — confirmed live 2026-08-16.** The first real call was made from the
**animatic editor's** ✨ Animate button (one 4s/720p frame, $0.20): Veo returned
a 1280×720 MP4 that `video_frames` reads and extracts correctly. So
`video_client.py`, the credentials, the region and the prompt shape are all
proven. Read the money notes in the 2026-08-07 entry before running more.

**`FinalVideoWorkspace` itself is still un-run against Veo**, though it shares
`video_client.render_shot` with the path that now works, so what is unproven
there is its own wiring (art-tray references, `last_frame`, batch sequencing),
not the model call. The art tray in particular passes reference images the
editor's path does not use.

**There is no Google Flow API** — researched 2026-08-07. Flow is a Labs *web app*:
no public API, no OAuth scope, no service account, and its credits are a separate
ledger from the API. A Google AI Pro/Ultra subscription grants **zero** API
access. Flow is a front-end over Veo, so we call Veo directly and get the same
models. Do not add "Flow integration" to the roadmap; it isn't a thing that
exists. (Driving the Flow UI with a session cookie would breach Google's terms
and break on any redesign.)

**VIDEO EDITOR ROADMAP (researched 2026-08-16, with the user).** The goal is an
animatic editor in the class of CapCut / VN. The audit's conclusion, which
should survive contact with any future session: **do not try to out-CapCut
CapCut on filters and stickers.** They have hundreds of engineers on that table.
What no one else can do is the thing we are already 70% of the way to — the
timeline knows what a *shot* is (scene/shot number, camera, location, dialogue,
the script line), the key poses make an animatic that genuinely moves, and the
panel can be regenerated from inside the editor. **Position it as the editor
where the storyboard, the animatic and the final film are one timeline.**

- **Phase 0 — the scene model. ✅ DONE 2026-08-16** (top Work Log entry).
- **Phase 1 — keyframe UI. ✅ DONE 2026-08-16** (see the two Work Log entries).
  ⏱ per property, draggable diamonds on the timeline, easing per key; frames
  gained Motion (zoom/pan/fade), captions gained opacity; undo groups per
  gesture. Nothing is left open in the data layer.
- **Phase 1a — SEE IT IN A BROWSER. ⬅ NEXT, and it is small.** Nothing built on
  2026-08-16 has been looked at. Fold into `tests/e2e_animatic.py`: the ⏱ row
  laying out inside `.an-prop-row`, the diamonds being hittable, and — the one
  that actually matters — **a keyframed push looking the same in the monitor as
  in the exported MP4**. The CSS transform is matched to `place_picture` by
  reasoning, not by looking.
  **Phase 2 added a second thing to look at:** the transition badge sitting on
  the cut and draggable, and the four kinds looking in the monitor like they
  look in the MP4 — `pictureStyle` is matched to `_transition_canvas` by
  argument too. A wipe previewing as a dissolve is exactly the class of bug this
  step exists to catch.
- **Phase 2 — TRANSITIONS. ✅ DONE 2026-08-16** (see the top Work Log entry).
  `AnimaticTransition {id, after_frame_id, kind, duration_ms}` — dissolve, dip
  to black, wipe, slide — written twice like the rest of the scene model.
  **They are BOUNDARY-LOCAL, not overlapping: the blend straddles the cut and
  the timeline does not get shorter.** That was the design decision of the
  phase; read `client/src/animatic/transitions.js` before changing any of it.
  Ripple and rolling trims therefore needed no re-verifying — nothing moved.
  **SPEED WAS DROPPED FROM THIS PHASE ON PURPOSE.** Constant speed, reverse and
  freeze-frame are meaningless for a held still: a still at 2× is just a shorter
  hold, which the duration field already does. They only become real once video
  clips exist, so they moved to Phase 3.
- **Phase 3 — VIDEO CLIPS on the animatic timeline. ✅ DONE 2026-08-16** (see the
  top Work Log entry). `AnimaticFrame` is a clip with
  `kind: image | video | color`, an `in_ms`/`out_ms` source range and `speed`;
  `video_frames.py` extracts stills with `ffmpeg -vf fps=N` and caches them by
  content. **`duration_ms` stays the TIMELINE length — speed widens the source
  window rather than re-timing the clip**, which is what keeps every later cut,
  caption and transition where it was. A clip arrives either by drag-and-drop or
  from **✨ Animate with Veo**, and a generated clip lands as an ordinary video
  upload so there is ONE code path downstream. Render records are server-owned
  (`result`, not `params`) — read the top entry before changing that.
  Reverse and freeze-frame were NOT built. `FinalVideoWorkspace` is NOT yet
  deleted: it still owns the art tray, per-shot references and assembly, so
  collapsing the two is a separate decision about what to keep.
- **Phase 4 — look: colour, LUT (.cube), masks, chroma key, blend modes.
  ✅ DONE 2026-08-17** (see the top Work Log entry). A frame and an overlay carry
  `effects` / `mask` / `blend`; every numeric parameter is keyframable through
  the ordinary ⏱ using a FLAT track name (`fx:<id>:<param>`, `mask:<field>`), so
  nothing about `keyframes`, the timeline rows or the undo stack had to change.
  The DOM preview IS a WebGL canvas now (`ProgramCanvas` + `animatic/gl/`) and
  **the shape fills moved into it with the pictures** — forced, not chosen: an
  overlay's blend mode needs every pixel beneath it. The drag handles stayed in
  the DOM, which is the cheap answer this plan itself suggested.
  **Pixel parity is by TOLERANCE, never exactly** — `tests/effects_check.py`
  pins the Python side to golden values, `tests/effects_parity_check.py` compares
  the shaders against Pillow at mean |Δ| < 3/255. ⚠ **Its pixel half has never
  run** (headless-gl would not build here); its static half does and passes.
  NOT built: effects on shapes and captions, and user-uploaded .cube files —
  the LUTs are the five built-ins in `luts/`.
- **Phase 5 — text engine + captions. ✅ DONE 2026-08-17** (see the top Work Log
  entry). Six bundled OFL fonts loaded from ONE file by both the browser and the
  exporter (`animatic_fonts.py` ⇄ `fonts.js` — a twin pair, checked); stroke,
  shadow and letter spacing in units that are the same number on both sides;
  `place: flow | free` with keyframable `x`/`y`; in/out presets that are
  **keyframe macros, not a second animation system**; auto-captions from an audio
  track and dialogue → TTS, both timed from data we already hold.
  **⚠ NO REAL GEMINI CALL HAS BEEN MADE** — transcription and TTS are proven
  against stubs, so the timing rules and spend guards hold but the request shape,
  the model ids and the response parsing are unverified. NOT built: gradient
  fill, saved text templates, and per-speaker voices in the UI (the server
  already honours a per-line `voice`).
- **Phase 6 — AUDIO DEPTH. ✅ DONE 2026-08-17, EQ included** (see the top two Work
  Log entries). A track gained `fade_in_ms` / `fade_out_ms`, a three-band
  `eq_low`/`eq_mid`/`eq_high`, `duck_to` and `role` / `duck_target`; **preview
  playback moved into a WebAudio graph** (`audio_engine.js`) because an `<audio>`
  element caps volume at 1 and cannot be EQ'd — the element is still the clock,
  and the file falls back to `el.volume` on any failure. The whole audio filter
  graph is now
  `audio_graph()` in `animatic.py`, unit-tested as a string AND encoded, decoded
  and measured. Three rules carry it:
  **(1)** `track_play_ms` / `fade_window` are a TWIN PAIR with
  `client/src/animatic/audio_mix.js`, checked by running the JS through node —
  a fade is placed against what the track PLAYS (its trim, or the end of the
  video), never against the file.
  **(2) Which track is the voice is stated, never guessed** (`role`), and
  `amix=…:normalize=0` still has to survive every edit to that function.
  **(3)** The duck is a compressor, so the preview is deliberately CLOSE rather
  than exact — the one honest gap in this editor's preview, stated in the pane
  where the control is. Beat markers are drawn AND are snap targets; the decode
  is shared and cached in `beats.js`.
  **(4)** The EQ's shelves must keep stating `t=s:w=1` — at ffmpeg's defaults
  they are not the same filter WebAudio builds — and its three bands stay FIXED
  so that each one is exactly one biquad on each side.
  **Nothing is left unbuilt.** ⚠ **No browser run**: the grips, the ticks, the
  Tone and Mix rows, and — most importantly — the rebuilt playback signal path
  have not been looked at.
- **Phase 7 — THE MOAT. ✅ DONE 2026-08-17** (see the top Work Log entry).
  Regenerate-panel inside Properties, cut to beat, auto-reframe for a new shape,
  and "make this shot 2s longer". Four rules carry it:
  **(1) A frame's url carries `?v=<mtime>` and the client re-fetches when it
  moves** (`_frame_version` ⇄ `urlSrcRef`). Both halves are needed; either alone
  is a redraw nobody sees. The url is not part of the saved document, so writing
  one is not an edit.
  **(2) The board's two actions have ONE implementation each**, in
  `server/common.py`, shared by `main.py`'s routes and the editor's proxies.
  **(3) A longer shot EXTENDS its plan, never re-plans it** — the existing pose
  lines are kept word for word and only the tail is bought
  (`plan_beats(existing_poses=…)`). A re-plan leaves drawing 17 continuing a
  motion drawings 1–16 never made, and only playing it reveals that.
  **(4) The reframe asks for the SUBJECT, never for the crop**, and writes
  ordinary `scale`/`x`/`y` — no crop concept, no new render path, and a clip
  that was already keyframed keeps its move.
  **Nothing is left unbuilt.** ⚠ **No real AI call and no browser run**: the
  vision request shape and the extend-plan prompt are stubbed, and the four new
  pieces of UI have not been looked at.
- **Phase 8 — PERFORMANCE & EXPORT. ✅ DONE 2026-08-17** (see the top Work Log
  entry). The still-render loop runs across processes (29.0s → 11.8s on 8
  workers over 216 stills, **byte-identical output**), the editor scrubs on
  half-res proxies, and the export dialog opens on a preset —
  YouTube / TikTok / Reels / GIF / Still. Four rules carry it:
  **(1) The stills are PLANNED, then DRAWN.** Names are assigned in one pass so
  they cannot depend on which worker finishes first; that is the whole reason
  parallel and serial encode to the same bytes, and `tests/export_perf_check.py`
  hashes both to prove it. Under 48 distinct stills it stays serial on purpose.
  **(2) `_detached_main` closes the Windows-spawn trap for every caller**, so no
  script has to guard its entry point — proved by neutering it and watching an
  unguarded probe run 4×. Everything crossing the process boundary is plain
  data, and **cancellation stays in the parent** because a worker cannot see the
  job store's flag.
  **(3) A preset states only what it means.** GIF and Still do not state an
  aspect ratio (a thumbnail must not reshape the film); the platform ones do.
  `match()` is the exact inverse of `apply()`, so a hand-edited field just reads
  "Custom". A PNG never reaches ffmpeg — the composite IS the file.
  **(4) A proxy saves PIXELS; bytes are the usual case, not the guarantee** —
  a downscaled PNG of line art can be larger, which the test states both ways.
  **The export never touches `proxies.py`.**
  NOT built: the thumbnail/waveform cache from the older one-line roadmap entry
  — it was not in the phase's file list. The waveforms are already decoded once
  and shared via `beats.js`; what is uncached is the drawn strip.

**✅ THE REFACTOR BREAK IS DONE (2026-08-16), and Phase 4 was built on it
(2026-08-17).** `AnimaticEditor.jsx` was split into `useAnimaticProject`,
`useTimelineTransport`, `useUndoStack` and the six `*Properties` panes;
`styles.css` is 22 files under `client/src/styles/`. Three rules came out of the
two phases that a later one must not break: **the `@import` order in
`styles/index.css` is the cascade** (add a new file at the END — that is where
`animatic-effects.css` went); **the transport hook owns the clock, so `scene` can
never be one of its arguments**; and **the export payload in `server/animatics.py`
is a `model_dump`, never a hand-written dict** — it was one for three phases and
had silently fallen behind by `id`, `keyframes` and the whole transform.

**Not yet verified live** (needs real keys / steady backend):
- **THE PHASE 5 AI PATHS — auto-captions and TTS.** Not one real Gemini call has
  been made. `tests/captions_check.py` drives both through stubs, so the timing
  arithmetic, the spend guards and the plumbing are proven and the REQUEST SHAPE
  is not: the model ids (`gemini-2.5-flash-preview-tts`), the audio mime types,
  the inline-audio part, `response_modalities=["AUDIO"]` and the PCM extraction
  are all written from the documented API and may need adjusting on first
  contact. Try the captions pass first — it is one call and costs a fraction of
  a cent, whereas a voiceover is one call per line.
- **The Phase 5 TYPE, in a browser.** The stroke, shadow and letter spacing in
  the monitor are matched to `_draw_text_block` by unit and by argument, not by
  looking — `stroke_px` scaling as `calc(100cqh * n / 1080)`, `shadow` and
  `letter_spacing` as `em`, the shadow's blur being zero on both sides. Same
  standing gap as Phase 1a, and the same fix: look at one.
- **EVERYTHING built on 2026-08-16 — the scene model, the keyframe UI AND the
  transitions.** Not one pixel of it has been seen in a browser. The Program
  monitor's CSS transform is matched to `place_picture` by reasoning about the
  geometry (`translate` in % of an element that fills the screen box == a
  fraction of the canvas; `scale` about the same centre; `.an-screen` already
  has `overflow: hidden` to clip a zoom), and the four transition branches in
  `pictureStyle` are matched to `_transition_canvas` the same way — by argument,
  not by looking. The offline tests are thorough about the numbers, and about
  what lands in the MP4, and say nothing about the layout. See **Phase 1a**.
- **The 2026-08-09 continuity pass** — the bible, scene anchors, flow context and
  the locked-camera key poses are all PROMPT changes, tested only against a
  stubbed image model. Whether they actually hold the characters still can only
  be judged by generating a real board. **Do that before building anything on
  top of them.** The mechanical parts (hole retry, gap-aware resume, flipbook
  animatic) are verified offline and don't need the API.
- **Veo video rendering** — ✅ NO LONGER UNVERIFIED. Called for real on
  2026-08-16 from the animatic editor and it worked (see above). What is still
  unverified is `FinalVideoWorkspace`'s own wiring — art-tray reference images,
  `last_frame`, and a multi-shot batch — none of which the editor's path
  exercises.
- **3D generation** — Meshy path is coded but not run live; **Tripo is entirely
  unverified** (built from public docs, no test key — adjust request shape when tried).
- The shirtless-mannequin `body` prompt may still occasionally be safety-filtered
  (fallback = regenerate, or switch to the modest-clothed wording).
- MongoDB Atlas was intermittently unreachable from the user's machine (SSL
  handshake) — auth needs it; jobs use in-memory store.

---

## 🎬 Script → Storyboard workflow — build plan (researched 2026-07-23)

**Goal:** turn a script into a shot-by-shot storyboard, competing with
storyboarder.ai / drawstory.ai. UI must stay dead-simple (any-age users). We have
two unfair advantages already built: (1) **consistent-character generation** (the
Text-to-Image turnaround pipeline) and (2) the **async job + live-progress system**
(`server/worker.py`, `JobDetail` progress UI). Reuse both.

**Done so far (UI only, no backend):** `ScriptToStoryboard.jsx` — a SINGLE-page input
form (redesigned 2026-07-23 to match the Text-to-Image workflow: WorkflowHeader +
one `.card` + tab-bar + dropzone + selectable `.opt-chip`s + gold `.btn.primary`).
Sections: script (paste/upload) → visual style → aspect ratio → one "Generate
storyboard" button. Button only shows a "coming soon" notice for now. Everything
below is the missing engine + screens.

**DECIDED (2026-07-23, with user):** build the **Simple** pipeline **+** the ONE
important full-control feature = **Review/edit shots** (Stage C). Input stays a
**single page** (not a wizard). Keep every screen in the Text-to-Image visual
language — do NOT copy the Drawstory reference's look/colours.

### Pipeline (the "AI film crew")
| Stage | What it does | Reuse / build |
|-------|--------------|---------------|
| **A. Shot breakdown** ⭐ ✅ | LLM parses script → ordered shot list. **DONE** — `script_breakdown.py` + `POST /storyboards/breakdown` (switchable `TEXT_PROVIDER`). Not yet run live. | Built 2026-07-23. |
| **B. Character consistency** ⭐ ✅ | Cast extracted in the breakdown; per-character reference generated on the Cast page; fed into every panel the character appears in. **DONE** (generate-from-text refs; upload-your-own later). Not run live. | Built 2026-07-23. |
| **B2. Prop & background consistency** ⭐ ✅ | Breakdown also extracts `assets` (props + backgrounds) and tags each shot; a "Props & backgrounds" page (after Cast) generates/uploads a reference per asset; fed into every panel the asset appears in (so the slipper/bedroom stay consistent). **DONE** (`generate_asset_reference` + `POST /assets/reference` + `StoryboardAssets.jsx`). Not run live. | Built 2026-07-24. |
| **C. Review shot list (page 4)** ✅ | Editable shot list before generating (edit/reorder/delete/add). **DONE** — Review step in `ScriptToStoryboard.jsx`, wired to Stage A. | Built 2026-07-23. |
| **D. Generate panels (page 5)** ✅ | Loop shots → one image each. **DONE** — `storyboard_pipeline.py` + `POST /storyboards` + worker; live progress, panels stream in. NO character-ref lock yet (Stage B, later). Not run live. | Built 2026-07-23. |
| **E. Storyboard board (page 6)** 🟡 | Grid of panels + captions — **basic board DONE** (`StoryboardBoard.jsx`, live fill-in + lightbox). Per-panel regenerate / caption edit / reorder = v3. | Built 2026-07-23. |
| **F. Export/share (page 7)** ✅ | PDF export **DONE** — `storyboard_pdf.py` + `GET /storyboards/{id}/pdf` + board download button. Images/share-link later. | Built 2026-07-23. |

### Build order (ship incrementally)
- **MVP (end-to-end first):** A → D (simple panels, NO character lock yet) → E (basic
  board) → F (PDF). Already feels magic: script in, storyboard out.
- **v2 (make it good):** add B (character consistency) + C (review/edit page).
- **v3 (polish/retention):** per-panel regenerate, reorder, camera tags, then
  animatics/video.

### Notes / decisions
- **Persist as a "project"**: project → scenes → shots → panels, so users can leave
  and return (extend the job store, or a new `storyboards` store).
- **Image gen costs time/money** → that's WHY the review page (C) exists: confirm the
  plan before generating ~50 images.
- **UX**: one clear action per page, big buttons, visible progress bar, board that
  reads like sticky notes on a wall.
- **RESOLVED (2026-07-23):** editing depth = **Simple + Review/edit-shots** (the one
  full-control feature the user wants mid-flow). Per-panel regen / caption edit /
  reorder are LATER (v3), not now.
- **Refs:** storyboarder.ai (help.storyboarder.ai), drawstory.ai/blog/text-to-storyboard
  + /blog/character-design-ai, boords.com/ai-storyboard-generator.

---

**Next steps** (pick the top unchecked item when told to "start next"):
- [ ] **WATCH THE PANELS MOVE, IN THE REAL EDITOR — AND DECIDE WHAT A SECOND TAKE
      OF THE SAME SHOT SHOULD DO.** `tests/veo_ripple_check.py` pins the arithmetic
      (2026-08-21), but two things want eyes and one wants a decision. Eyes: does
      the ripple read as helpful or as the timeline jumping, when it happens while
      you are looking at the other end of a 42-panel board — and is the notice
      enough, or should the moved panels flash. The decision: **"Render again with
      Veo" still attaches a SECOND take at the same start as the first**, so two
      takes of one shot overlap each other on the video row (pre-existing; the
      spread clears the panels past the longer of the two, so nothing is buried
      that was not buried before). The options are (a) leave it — the newer take
      wins in `stackAt` and the older one is one drag away, (b) put the new take
      after the old one and ripple again, or (c) replace the old take, which throws
      away something that was paid for. Ask the user; this pass deliberately only
      fixed the overlap BETWEEN DIFFERENT SHOTS, which is what was reported.
- [ ] **DECIDE WHERE A PLAIN IMAGE IMPORT LANDS, NOW THAT "Stills track" IS OFF
      THE MENU.** ＋ Add layer no longer offers a `stills` row (2026-08-21), but
      `addAssets` still auto-creates one for images when no row is named
      (`rowOfKind("stills")` → `addPictureTrack("stills")`, `AnimaticEditor.jsx`
      ~4551), so a drop can build a row the user can no longer ask for by name.
      ⚠ **THE ROW KIND CANNOT SIMPLY BE DELETED** — saved projects carry it, and
      `ROW_KIND.stills` is what names and validates those rows on load. The
      options are (a) route unrouted images onto a `video` row and keep `stills`
      as a legacy kind that still loads, or (b) leave it as is and accept that
      the two kinds are made by different doors. Ask the user which; this pass
      only removed the menu entry, which is all that was asked for.
- [ ] **THE STORYBOARD STRIP'S OWN DRAG IS STILL THE LOOSE ONE.** The Media
      library and the timeline's own bar drag both route by
      `cardRowKind` / `clipRowKind` now, but `FrameStrip.jsx` stamps its marker off
      `frameOrigin`, which answers "board" — so a Veo clip dragged out of the
      STRIP is marked `image`, the Stills row accepts it, and `dropAsset`'s
      `"frame"` branch refuses nothing by kind. Left alone deliberately (outside
      the report), and the fix is small: the same `application/x-anim-board` marker
      plus a `clipRowKind` check on that branch. Decide first whether a strip drag
      should be a MOVE that obeys the strict rows, or stay the one place a clip can
      change which KIND of row it lives on.
- [ ] **EYES ON THE MEDIA LIBRARY WITH A REAL PROJECT.** Built and covered by two
      tests, but only ever seen against fixtures. Worth looking at on the user's
      42-panel board: does the pane stay legible with 43 cards in four sections;
      does the ×N badge read as "how many clips use this" without being explained;
      is `LIBRARY_MAX_EDGE` (240px) sharp enough for the list view and cheap enough
      for 43 of them at once. ⚠ And the one behaviour no fixture proves: a project
      saved BEFORE the library existed derives one on first open
      (`libraryFromProject`) and saves it — so the very first open of an existing
      animatic is the one that has to be right.
- [ ] **DECIDE WHETHER A LOCK SHOULD SURVIVE INTO THE EXPORT UI.** It deliberately
      does not touch the export (a locked row plays exactly as it did), which is
      right — but there is currently no reminder anywhere that a row is locked
      except the gutter. If someone locks a row, closes the editor and comes back a
      week later, the italic name and the hatch are the whole signal. Possibly
      enough; not decided with the user.
- [ ] **AUDIO ROWS CANNOT BE LOCKED OR HIDDEN**, because `laneToken` gives them no
      token — a loose audio row is keyed by the FILE it holds, which changes as
      clips are dragged in and out. The padlock and the eye are both disabled
      there and say so. Fixing it properly means giving every audio row a stable
      identity (a layer record, as the picture rows got), which is the same shape
      of change and worth doing together with anything else that needs it.
- [ ] **ANIMATE A PANEL WITH VEO, IN THE REAL EDITOR — THE PARTS A FIXTURE
      CANNOT REACH.** `tests/editor_veo_attach_check.py` now proves the ATTACH: a
      ready render lands on a Storyboard video row above its panel, at the panel's
      start, and shows its own picture in Media and in the monitor. What a
      `veo_clips` fixture still cannot drive, because it starts from an
      already-finished render: the animate dialog and its price, the poll from
      queued → rendering → ready, whether 👁 on that row shows the board again,
      whether a SECOND render re-uses the row rather than making another, and a
      reload after a render now that idempotency keys on `upload_id`. ⚠ **AND THERE
      ARE TWO BUTTONS TO DRIVE NOW** (2026-08-20): the Properties one and the new
      one in the timeline's add row. The second aims at `selectedFrame ||
      currentFrame`, so the thing to check by eye is that it animates THE SHOT YOU
      MEANT with nothing selected — the one under the playhead — and that its label
      flips to "Render again with Veo" once that shot has a render. ⚠ **THE IMPORT HALF OF THIS ITEM IS CLOSED**
      — the picker's thumbnail handshake was exactly the bug the user reported, and
      `tests/editor_board_import_check.py` now drives it end to end. What is left
      to eyeball there is only the board LIST against real data (are the panel
      counts sensible on a board with undrawn shots).
- [x] **A STORYBOARD FIXTURE FOR THE BROWSER SUITE.** Done, in
      `tests/editor_board_import_check.py`, and it turned out not to need a real
      storyboard job at all: the fixture that matters is the SERVER'S RULE, not the
      board. Its router answers `/storyboards` with one board and `GET
      /animatics/probe/frame/{id}` with a 404 until a PUT has carried that id,
      which is what `get_frame_image` does. Copy that router for anything else that
      shows a referenced panel — it is the only fixture that can catch a
      too-early url, and a router that always serves the picture cannot.
- [ ] **DECIDE WHETHER A BOARD ROW SHOULD ACCEPT A SECOND IMPORT.** Today's ＋ on a
      Storyboard images row opens the picker and appends to that row, so two boards
      can share it. That may be right (one animatic, two reels) or may want a row
      per board. Not decided with the user; the current behaviour is the permissive
      one and appending is at least non-destructive.
- [ ] **`tests/editor_lane_move_check.py` IS STALE IN 3 CHECKS — FIX THE TEST, NOT
      THE CODE.** Its `promptFit` probe walks `.tl-track-empty` looking for a TEXT
      NODE child and measures 0 prompts, so all three "no empty-row prompt is
      sliced by its row" checks fail on `bool(fit)`. The prose those checks were
      written for was deliberately REMOVED on 2026-08-20 ("remove information text
      look in blanck layer") — the band is a childless `<button>` carrying its
      sentence on `title` now. Two honest options: assert the band FILLS its row
      and clips nothing (geometry, no text), or delete the three checks and say in
      the docstring why. ⚠ It was already failing before the 2026-08-20 naming
      work; do not read it as a regression from that.
- [ ] **THE ROW NAMING QUESTION IS ANSWERED — "EYES ON THE PICTURE TRACKS" IS
      SHORTER NOW.** The sub-question "does the row NAMING (‘Pictures’, ‘Pictures
      2’) make sense beside ‘Text’/‘Text 2’" was answered by the user directly:
      it did not, and they are `Video` / `Video 2`. What still needs a human eye
      on that item: the gap, `.tl-bar.clash`, the ▶⇧ button, and the
      first-open autosave.
- [ ] **A DROP ON AN EMPTY ROW NOW LEAVES A GAP IN FRONT OF IT — LOOK AT IT.**
      `insertPictures` honours the drop time on an end-of-track insert, which is
      what makes "put this video on Video 2 at 0:45" land at 0:45. It also means a
      first clip can sit in the middle of a row with bare lane behind it. Worth
      confirming that reads as deliberate rather than as a clip that failed to
      snap — same open question as the hatch under "EYES ON THE PICTURE TRACKS".
- [x] **THE PICTURE TRACK IS A STACK OF INDEPENDENT TRACKS — DONE 2026-08-20.**
      A picture carries `track` and `start_ms`; a plain trim moves one clip; a gap
      shows the track underneath; transitions are track-local. See the Work Log,
      and `picture_tracks_check.py` / `editor_picture_tracks_check.py`. The question
      this item asked ("what does a gap show?") was answered "the track below it,
      and the letterbox colour when there is nothing below" — which is what made
      the rest of it fall out.
- [ ] **THE TIMELINE BAR NOW MIXES SIX DRAWN ICONS WITH FOUR GLYPHS.** The six
      tools are SVG since 2026-08-20; ↶ undo, ↷ redo, 🧲 snapping and 🥁 cut-to-beat
      sit right beside them and are still text/emoji, and 🧲 / 🥁 render
      full-colour next to monochrome strokes. That is the fault `Icon.jsx`'s header
      comment was written about, and replacing the letters made the row MORE mixed,
      not less — it was out of scope (the ask named V/C/B/N/H/Z and nothing else)
      but it is the obvious next pass: four paths (undo-arrow, redo-arrow, magnet,
      drum or metronome) and `<Icon>` in four buttons.
- [ ] **THE SPEAKER IS THE LAST EMOJI IN THE GUTTER.** ⚠ **THE LANE ICONS HALF OF
      THIS ITEM IS CLOSED, AND NOT THE WAY IT WAS PLANNED** — the user asked for the
      icons GONE rather than redrawn, so `LANE_ICON` was deleted and each row opens
      with its number (2026-08-20). The four `PATHS` this item wanted (picture,
      shape, note, quote) are therefore not needed. What is still emoji is audio’s
      🔇/🔊, which renders full-colour beside the monochrome SVG eye, lock and ✕
      next to it — the exact fault `Icon.jsx`’s header comment was written about.
      The work is two paths in `PATHS` (speaker, speaker-off) and `<Icon>` in the
      mute button, and it should be done in the same pass as any other change to
      that cluster.
- [ ] **EYES ON THE PICTURE TRACKS, IN THE REAL EDITOR.** Two browser suites drive
      every gesture and assert on what moved; neither can say whether the bar READS
      as a stack. What needs looking at: does a GAP look like a deliberate hole
      rather than a rendering fault (it is bare lane background — it may want a
      hatch); is `.tl-bar.clash` noticeable enough when two clips overlap, given
      only the later one plays; does the ▶⇧ button read as "split this row" without
      its tooltip; and does the row NAMING ("Pictures", "Pictures 2") make sense
      beside "Text"/"Text 2" once a project has three of them. Also: a project
      opening for the first time gets its `start_ms` filled in, which marks it
      dirty and triggers an autosave — worth watching once that the save lands and
      the timeline does not flicker.
- [ ] **DECIDE WHETHER A PICTURE CAN BE GROUPED.** `MOVABLE` gained `frame` and
      `GROUPABLE` deliberately did not, because `group_id` is not a field on
      `AnimaticFrame` — tagging a picture would write something the server drops.
      Now that pictures move like every other clip, "tie this shot to the caption
      over it" is a reasonable thing to want, and it is a one-field schema change
      plus `groupSelection`. Not started; noted because the asymmetry will look
      like an oversight to the next reader.
- [ ] **RIPPLE-DELETE.** Deleting a picture now leaves a GAP, which is correct and
      consistent with every other clip kind — but "delete this shot and close up"
      was free when the track was a sequence and is now impossible without dragging.
      The natural home is the RIPPLE tool (B): with it armed, Delete closes the gap.
      Perhaps ten lines in `deleteSelection`, and it needs the tool's hint updating.
- [ ] **EYES ON THE CROSS-LANE DRAG.** `tests/editor_lane_move_check.py` proves
      the clip lands on the right row and that only the row changed, and says
      nothing about how the gesture FEELS. What needs looking at: does the ghost
      outline read as "it will land here" against the dimmed original; is the
      3px tolerance either side of a row (`laneAtPoint`) enough to make the gap
      between two rows unnoticeable, or does a clip occasionally refuse to move
      because the pointer was in the crack; and does the promotion of a
      file-grouped audio row to a layer LOOK acceptable — the row keeps its name
      but changes position in the stack, because layer rows are drawn after the
      loose ones.
- [ ] **DRAG SOMETHING OUT OF THE EFFECTS TAB, IN THE REAL EDITOR — the LOOK of
      it, not the mechanics.** ⚠ **PARTLY DONE 2026-08-19.** "A drag is the one
      thing no test here can drive" turned out to be wrong, and believing it is
      what let a crash ship: `tests/editor_effects_drop_check.py` now drives the
      drop in Chromium against the real editor and proves an effect lands, the
      pane opens with its controls filled in, and the monitor survives. What
      still needs EYES is everything about how it looks and feels, which that
      test says nothing about: does the
      right bar light up as you cross the picture rows (`drop-onto`), does the
      Video row refuse a drop into its gaps, does a transition land on the cut
      you meant or on a surprising one (it snaps to the NEAREST edit point, which
      may be far away mid-clip), and does the Properties pane land open on
      Effects. Also worth a look: the ƒx badge against the keyframe diamonds and
      the transition badge — three things now share one bar, and the other two
      each have a band they may not be grown into.
- [ ] **Decide whether an effect should have a REPRESENTATION on the timeline
      beyond the ƒx count.** The user asked to "click effects in the timeline" and
      what shipped is a badge that opens the chain in Properties. The fuller
      reading — an effect drawn as its own band on the clip, clickable per
      effect — is a real design question (it costs vertical room the keyframe
      rows and the transition badges are already sharing) and should be answered
      by looking at the badge first.
- [ ] **PUT A WIPE ON A REAL CUT AND POINT IT SOMEWHERE, IN THE REAL EDITOR.**
      The maths is proved in both directions — `render_parity.py` compares the two
      evaluators on a non-default direction and `transition_check.py` decodes real
      MP4s and measures the opposite half of the frame — but nobody has clicked an
      arrow chip. What needs eyes: do four chips fit on the Travels row at the
      narrow viewports; does the dip swatch read as "the bar colour" when nothing
      has been picked (it shows the bar colour and the hint says so); and does the
      monitor agree with the exported file on an UP wipe, which is the direction
      with no prior art in this codebase at all.
- [ ] **LOOK AT THE EIGHT NEW TRANSITIONS ON A GPU, AND IN A BROWSER.** ⚠ The
      matte GLSL has been parsed, its generated `#define`s checked and its
      structure asserted — but it **has never been executed**. `headless-gl` is
      not installed here, so `effects_parity_check.py` exits before the pixel
      half; install it (`cd client && npm install --no-save gl`) and run it, and
      that alone would prove the shader against the NumPy twin at nine shapes
      × several softnesses. Then eyes on the editor: does the Treatment chip row
      still read at 12 kinds (it was 4), does the `Edge` slider look like a
      feather rather than a fade, and does the CLOCK agree between monitor and
      export — it is the one shape whose field wraps, so a sign error there
      shows as the hand sweeping the wrong way rather than as a broken picture.
- [x] **Step 2 of the transitions plan — DONE 2026-08-19.** The reveal region is
      a real matte multiplied into the arriving picture's alpha, beside the mask
      multiply. Eight shaped reveals plus a soft edge; `revealRegion`, `clipTo`
      and `_wipe_box` are gone. Dissolve is deliberately NOT routed through it —
      see the Work Log for why (rounding vs truncation).
- [x] **Step 3 of the transitions plan — DONE 2026-08-19.** (a) The Treatment
      row is five families off a `family` field on the descriptor; (b) six
      point-wise effects landed with 21 goldens. Blur/sharpen/grain still out.
- [ ] **PUT A GUARD FOR THE BACKTICK TRAP IN `effects_parity_check.py`.** A
      backtick inside a `/* glsl */` template literal ends the JS string and the
      parse error surfaces dozens of lines from the cause — it cost two build
      failures in one session. The static half already loads every shader module
      under node; asserting that no `/* glsl */` body contains a backtick is
      three lines there and would turn a confusing parse error into a named
      failure.
- [ ] **The FILTERED transitions (blur dissolve, pixelize) still need a genuine
      two-texture stage** and were always the deferred tier. Nothing in the matte
      work changes that — a matte can only choose BETWEEN two pictures, never
      filter one of them — but note it now costs a new program rather than a
      rewrite, because the matte path leaves the existing compositor untouched.
- [ ] **PUT A COLOUR LOOK ON A REAL SHOT, IN THE REAL EDITOR.** The crash is fixed
      and `tests/monitor_effects_check.py` drives the monitor in Chromium, but that
      test uses a COLOUR CARD (no image to load, no fit, no resample) and the user
      reported this against a storyboard panel. Open the animatic editor on the
      project they reported against, add **Colour look (LUT)**, and step through
      every name in the dropdown: **(a)** Identity must change nothing at all —
      that is the case that looked broken, because the correct result is
      indistinguishable from no effect; **(b)** Noir must go grey, Cool/Warm must
      shift; **(c)** drag Amount from 0 to 100% and watch it dial in; **(d)** put
      the LUT above and below a Brightness in the chain and confirm the two are
      different pictures; **(e)** keyframe Amount and scrub — the grade must
      animate without the picture flickering, which is the case the context
      rebuild would have made ugly; **(f)** export and confirm the MP4 matches
      the monitor. Also worth a look now the context is no longer rebuilt per
      render: **scrubbing should be visibly smoother**, since every picture used
      to be re-uploaded on every tick.
- [ ] **DRAG THE SEAMS, AND LOOK AT BOTH WORKSPACES.** Landed 2026-08-17/18 and
      it is all layout, so a build passing proves very little. In order:
      **(a)** ⚙ → Reel / Shorts, and check the panes reorder (Program left and
      **big enough to work in** — that was the report — Media wide with its
      frames in a grid, Properties right);
      **(b)** confirm the picture's SHAPE did not change — a 16:9 project must
      still show a 16:9 monitor, just a shorter one, and the Video tab's frame
      size must be untouched;
      **(c)** drag each of the three seams (Program|Media, Media|Properties,
      panes|Timeline): the pointer should stay on the line the whole way, the
      pane should stop at the limit rather than the pointer running away from
      it, and no text either side should get selected;
      **(d)** double-click a seam → that pane only goes back to its default;
      **(e)** reload → same workspace, same sizes; switch workspaces and back →
      each one keeps its own sizes;
      **(f)** press `~` over a pane and over the timeline: every seam disappears
      while it is maximized, and the dragged timeline height comes back after;
      **(g)** narrow the window past 1400px and 1180px — at 1180 the panes stack,
      the seams go, and the sizes must be REMEMBERED, not lost, when it widens
      again.
- [ ] **DRAG A RUBBER BAND ROUND SOME CLIPS, IN THE REAL EDITOR.** Landed
      2026-08-17 and it is a NEW MOUSE GESTURE on a surface that already had one
      — a press on a lane still scrubs if it doesn't travel, and that 4px slop is
      the whole distinction. In order:
      **(a)** click a lane's empty space and check the playhead still goes there;
      then drag and check a band appears with a live count and dashed outlines on
      what it is over;
      **(b)** the band across several lanes at once — pictures, captions, audio —
      and check the Properties pane switches to **Selection · N** with the right
      breakdown, and that Delete removes exactly those in ONE Ctrl+Z;
      **(c)** drag one of the selected clips and check they ALL move, keeping
      their spacing, and that dragging hard left stops when the earliest one
      reaches 0:00 rather than piling them up against it;
      **(d)** shift-click to add and remove one; click a selected clip without
      moving and check the selection narrows to it;
      **(e)** double-click the Captions row's label — the whole row must select
      even where it runs off the end of the pane — then Delete. **This is the
      thing the user actually asked for; if only one gesture works, make it this
      one.**
      **(f)** Ctrl+G on two clips, then click one: both must select, and both
      must still be grouped after a save + reload. Razor a grouped audio clip and
      check you can still delete just the middle piece.
      Then run `python tests/e2e_animatic.py` — §5 and §9 both press on lanes.
- [ ] **RUN A CAPTIONS PASS ON A CUT TRACK, IN THE REAL EDITOR.** Landed
      2026-08-17 and this is the one path where offline tests can only prove the
      arithmetic — the transcription itself has still never been called for real
      (see the Phase 5 entry). In order:
      **(a)** cut a voiceover in three places (head, middle, tail), delete the
      middle piece, then Write captions from that track. Every caption must sit
      under the words you can hear, nothing may be written for the piece you
      deleted, and a sentence you cut through must come back as two captions
      carrying the words each half actually says;
      **(b)** the captions must land on a **Captions** row at the TOP of the
      timeline, above Images, with your own typed text untouched on its own row;
      **(c)** re-run it — the second pass must replace the first row, not add a
      second copy of every subtitle — then delete the lane with its ✕ and check
      the clips go with it and stay gone after a save + reload;
      **(d)** confirm the lane survives a reload at all (the poll now re-reads
      `layers`; if that regressed, the row comes back nameless from the clip
      fallback and then vanishes on the next save).
- [ ] **LOOK AT THE PICTURE LANE AGAINST THE RULER.** The bars are placed by
      time now, not by flow (2026-08-17). Put a few 0.1–0.2s frames early in a
      sequence and check that the cut between shots 20 and 21 still sits exactly
      under the ruler tick it should, that the last shot ends AT the end of the
      timeline, and that the horizontal bar reaches it without touching + or −.
      Then check a very short frame is still clickable, and that a transition
      badge on a cut is still centred on it.
- [ ] **CUT AN AUDIO TRACK IN THE REAL EDITOR, and look at the ↺ column.** Both
      landed 2026-08-17 with no browser run at all, and both are the kind of
      change offline tests cannot judge. In order:
      **(a) the razor** — take `C` to a waveform, check the cut lands under the
      pointer, that the two halves butt up with no click or gap in playback, and
      that Delete on the middle piece leaves silence where the pause was;
      **(b) three drags on one clip** — the body moves it (and a press that does
      NOT move still scrubs, which is decided on pointerup), the right grip
      trims the tail, the LEFT grip trims the head without the waveform sliding
      under it. Note the left grip shares its top 10px with the fade-in grip, by
      design — check the trim handle is still grabbable below it;
      **(c) playback across a gap** — the sound must stop when the playhead
      leaves a clip and start again when it reaches the next, with the pictures
      staying in step (the first playing clip is the master clock);
      **(d) the ↺ column** at all five viewports — it should read as one column
      down the right-hand edge, lit only on rows you have changed, and must not
      squeeze the ⏱ on an animated row or the value box on a narrow pane.
      Then run `python tests/e2e_animatic.py` — §9 clicks `.tl-audio` at (5,5),
      which is now a grip on a positioned clip rather than a bare lane.
- [ ] **DRAG THE NEW TIMELINE SCROLL BARS IN THE REAL EDITOR.** Added
      2026-08-17 and proven only in an isolated harness, with no audio lane, no
      video clips and no keyframes on screen. What to look at, in order: the
      gutter labels stay beside their own tracks while the lanes are scrolled
      down (that is a hand-written `translateY`, not layout); the ruler stays
      pinned and the playhead's grip is still drawn over it; a grip drag zooms
      with the OPPOSITE end pinned and does not creep while the pointer is
      still; the vertical grips make every track taller together, waveform
      included; the wheel still scrolls with no visible scrollbar; and the Hand
      tool (`H`) still drags the view. Then run `python tests/e2e_animatic.py` —
      §5 checks exactly the gutter/lane alignment this touched.
- [x] **UNSTICK `tests/e2e_animatic.py`** (done 2026-08-16 — it had been dying at
      section 5 of 14 since the LANES work landed, so sections 6-14 had not run
      for several sessions. All 14 pass now; three of the four failures were a
      stale test and one was the test misreading the app. See the Work Log.)
- [x] **Split `AnimaticEditor.jsx` and `styles.css` before Phase 4** (done
      2026-08-16 — pure move, see the Work Log).
- [x] **`.an-clip-short` had no CSS rule** (done 2026-08-16 — the video-clip trim
      warning was rendering unstyled).
- [x] **Wire up "add a colour card"** (done 2026-08-16 — `＋ Colour card` beside
      `Text` in the timeline header, the two clips you can make without a file.
      `addColorCard()` had had no caller since Phase 3. See the Work Log; e2e
      §11b covers it.)
- [x] **Render one shot with Veo for real** (done 2026-08-16 — one 4s/720p frame,
      $0.20. It worked, and the returned MP4 extracts correctly. It also found
      the polling bug that lost the clip; see the top Work Log entry.)
- [ ] **LOOK AT THE REBUILT PROPERTIES PANE, at all five viewports.** It was
      converted from hand-rolled rows to `PropGroup`/`PropRow` (2026-08-17) and
      `npm run build` is the only thing that has checked it. What to look at, in
      order: labels line up down a Frame pane with a video clip selected (five
      sections, ~20 rows); a live ⏱ with ‹ ◆ › and an easing menu does not squash
      the value box beside it; the segmented button groups (shape kind, text
      align/size) and the chip strips (speed, aspect) still fit their rows; a
      collapsed group stays collapsed when you click the next clip; and the
      1320px / 1180px label-column steps in `properties.css` land where the pane
      actually changes shape. Then run `python tests/e2e_animatic.py`.
- [ ] **OPEN THE EDITOR AND LOOK AT THE MONITOR.** Phase 4 replaced the whole
      preview with a WebGL canvas and **not one line of that GLSL has ever been
      compiled** — headless-gl would not build on this machine, so the pixel half
      of `tests/effects_parity_check.py` has never run, and the browser has not
      been opened. Everything else about the phase is tested; this is the part
      that isn't. What to check, in order: a plain still still shows; a shape
      still draws and still drags (its FILL is in the canvas now, only its handle
      is DOM); an overlay still draws above the shapes; a dissolve still blends;
      then a LUT, a mask and a chroma key. If the canvas is black, the console
      will say why — a shader that failed to compile throws with its own log.
- [ ] **Get `tests/effects_parity_check.py`'s pixel half to run, somewhere.**
      `cd client && npm install --no-save gl`. Tried again 2026-08-19 and it
      **fails on this machine**: node-gyp reports "could not find a version of
      Visual Studio 2017 or newer", so it needs the Visual Studio "Desktop
      development with C++" workload (Linux: libx11-dev, libxi-dev, mesa).
      ⚠ **NO LONGER THE ONLY EVIDENCE THE SHADERS RUN** — `monitor_effects_check.py`
      executes every chunk in `shaders/` in Chromium on SwiftShader and compares
      it to the Python exporter, and all eleven kinds are covered there now. This
      file remains the STRONGER check (a whole frame with ramps, edges and a
      green block, rather than a flat colour) and its thirteen new point-wise
      cases have never been run, so it is still worth a machine that can build it.
- [ ] **Render a SECOND shot, and watch it land on the timeline.** The first one
      proved Veo and the extraction; what has still never been seen working is
      the fixed attach path — clip finishes → frame becomes `kind: "video"` →
      it plays in the monitor → it survives a reload. That is one more $0.20
      frame and it closes the loop.
- [ ] **Decide what happens to `FinalVideoWorkspace`.** The editor can now
      animate a shot, so the two overlap — but the workspace still owns the art
      tray (character/style references per shot) and the assemble step. Either
      bring those INTO the editor and delete it, or keep it for multi-shot batch
      work and say plainly which is for what. Right now there are two ways to
      spend money on the same thing, which is the worst of both.
- [ ] **Phase 3 leftovers, small:** reverse and freeze-frame (the source range
      exists now, so both are cheap), and a video clip's own audio track — the
      export currently mixes only the project's audio, which is why the monitor's
      `<video>` is muted.
- [x] **Phase 6 — audio depth: fades, EQ, ducking, beat markers** (done
      2026-08-17; see the top two Work Log entries. `tests/audio_mix_check.py` —
      it encodes, decodes and MEASURES, so this one genuinely covers the export.)
- [x] **EQ on an audio track** (done 2026-08-17 with the WebAudio refactor it
      needed — three fixed bands, previewed by three biquads and exported by
      three ffmpeg filters, measured out of the encoded file with an FFT.)
- [x] **CUT AUDIO IN THE MIDDLE, not just at its ends** (done 2026-08-17,
      user-reported. `start_ms` on `AnimaticAudio`, `adelay` in the graph, the
      razor on the audio lane, and a clip you can drag. See the top Work Log
      entry — the `id`-not-`upload_id` rule is the one to read first.
      `tests/audio_razor_check.py`.)
- [x] **↺ reset on every property row** (done 2026-08-17, user-reported.
      `ResetButton` in `PropGroup.jsx`; always rendered, disabled at the default,
      and it clears that property's keyframe track on an animatable row.)
- [ ] **LISTEN TO THE AUDIO LANE IN A BROWSER.** The one with the most riding on
      it: **the whole preview signal path was rebuilt** (every `<audio>` now goes
      through a WebAudio graph). It is written to fall back to `el.volume` on any
      failure, so a broken graph would be silent-ly *fine* rather than obviously
      broken — which is exactly why it needs ears on it. Check, in order:
      **sound comes out at all** when you press play; a track above 100% is
      audibly louder than one at 100% (it used to be identical); the three Tone
      sliders change what you hear and "Flat" puts it back; the two fade grips
      are grabbable at zero fade (they are clamped inside the clip for that
      reason) and dragging one writes `fade_in_ms`/`fade_out_ms` with the wedge
      following; playback audibly ramps at both ends; beat ticks appear under a
      music track and a dragged frame edge snaps to one. Console: an
      `AudioContext was not allowed to start` warning on load is expected and
      harmless — it resumes on the first play.
- [ ] **DRAG A CROSSFADE ONTO A CUT IN A BROWSER (2026-08-19).** The arithmetic
      and the encode are both measured (`tests/audio_crossfade_check.py`, 27
      checks), but nothing here has been dragged by hand. Check, in order: the
      **Audio Transitions → Crossfade** folder is in the Effects tab with three
      entries; dragging one lights up the AUDIO rows and leaves the picture rows
      refusing (that is the whole point of the second `x-anim-afx` marker — if
      the picture rows light up too, `laneTakes` is reading the wrong one);
      dropping on the right half of a razored clip crossfades that cut and the
      notice names the length it actually got; the outgoing clip visibly grows
      and the incoming one does NOT move; **Ctrl+Z undoes the whole crossfade,
      not half of it** (both clips are written in one `setAudioTracks` for
      exactly that reason); the wedge for Constant Power is visibly a different
      shape from Constant Gain; the "In shape" / "Out shape" chip rows appear in
      Properties only once that end has a fade. And listen: on a cut between two
      pieces of music, Constant Gain should audibly scoop and Constant Power
      should not — that is the difference the whole feature exists to offer.
- [ ] **LOOK AT THE ⓘ AND THE CUT CURSOR (2026-08-19).** Both are answers to a
      user report and both are things only eyes can confirm. The ⓘ: every row in
      the Effects tab should carry one, the descriptions should be GONE from the
      rows, and clicking one should open its note under that row without folding
      the tree or adding the effect. The razor: press C and check the pointer is
      the same blade over a picture bar, a caption, a shape, an overlay and an
      audio clip — and an ordinary pointer over the time ruler, which should
      scrub, not cut. Then cut one clip on each of the five rows and confirm only
      that row changed. `tests/editor_razor_check.py` asserts all of this in
      Chromium, so this pass is about whether it FEELS right — chiefly whether
      `crosshair` is the blade you want or whether it should be a drawn razor
      cursor, which is a five-line change to one CSS rule if so.
- [x] **Phase 4 — the LOOK: colour, LUT, masks, chroma key, blend modes** (done
      2026-08-17 — plus the WebGL monitor. Three pre-existing export bugs fell
      out of it; see the top Work Log entry. `tests/effects_check.py`,
      `tests/effects_parity_check.py`. ⚠ The shaders have never been compiled —
      the two unchecked items at the top of this list are what closes that.)
- [x] **Phase 3 — video clips on the timeline, dropped or Veo-generated** (done
      2026-08-16 apart from the items above; see the top Work Log entry.
      `tests/video_clip_check.py`, `tests/animate_guard_check.py`).
- [x] Client redesign (sidebar dashboard), subject-type templates, live progress,
      per-section 3D + saved API keys, regenerate part/view, custom assets (2026-07-22).
- [ ] **Re-run shot 2 of the Ep_4 board and compare against the known-bad set**
      (`output/_storyboards/284759b3ff034687a8bb5814b16cdcf5/seq/panel_01`). The
      head should now visibly move between poses and every pose should be the
      same medium as its panel. This is 8 images — the cheapest possible test of
      the `composition_purpose` fix.
- [x] **PREVIEW — stop paying 8–40 images to find out it didn't work** (done
      2026-08-09; see the Work Log entry).
- [x] **Pose 1 must BE the panel, and a shot must not animate into the next one**
      (done 2026-08-09; see the Work Log. `tests/key_pose_scope_check.py`).
- [x] **Regenerate must redraw, refresh the picture, and look like it is working**
      (done 2026-08-09; see the Work Log. `tests/key_pose_refresh_check.py`).
- [x] **Refreshing one panel must not re-download the whole board** (done
      2026-08-09; `refreshPanelImage` vs `reloadBoard` — see the Work Log).
- [x] **No drawn frame around a panel or a key pose** (done 2026-08-09; prompt +
      `strip_drawn_border`. See the top Work Log entry. `tests/panel_border_check.py`).
- [ ] **Re-generate a board and confirm the new prompt stops the frame at source.**
      `strip_drawn_border` is the safety net and is measured; what is NOT yet
      measured is how often the *reworked prompt* still draws one. Generate a
      board, then run `tests/panel_border_check.py` and compare the "carried a
      drawn frame" count against today's baseline of **138 / 371**. If the rate
      hasn't dropped, the wording needs another pass — the net is already
      catching them, but every framed render is a picture drawn smaller than it
      needed to be, then scaled back up.
- [ ] **Re-run shot 1 of the TTBB_EP_One board and look at the 8 poses.** The
      user's known-bad set is `TTBB_EP_One - shot 1 key poses.zip` (pose 1 not the
      panel; Kabir awake by pose 8). Expect: pose 1 identical to the board panel,
      and all eight with him still asleep — breathing, the quilt settling. The
      pose plan is already verified live; this is 7 images to confirm the
      DRAWINGS obey it too, and it is the cheapest test of the `hold` fence.
      Worth doing on a CLOSE-UP as well: the scope fix must not have cost the
      close-up its head movement (the fix logged directly below).
- [ ] **Contact-sheet view for a shot's key poses.** Tiling the eight poses next
      to their source panel made every defect obvious in one look, where
      thumbnails in a strip hid all of them. Small, and it is how the user will
      judge every future run.
- [ ] **Judge the continuity pass on a REAL board (2026-08-09).** Generate the
      same script the user reported against (rough sketch, no cast step) and look
      at whether the character holds across all shots. If it still drifts, the
      next lever is a cheap **auto-generated cast sheet for reference-free
      styles** — one small grey character sketch per named role, drawn once and
      fed to every panel — which keeps the style's promise (no cast STEP for the
      user) while giving the model a picture as well as words.
- [ ] **Surface holes in the UI.** The API now reports `missing` poses and failed
      panels honestly; the board still shows a failed panel as a quiet gap and
      "Make animatic" still drops it silently. Say so on screen, with a one-click
      "fill the gaps".
- [ ] **Live-test 3D** — run Meshy with a real key end-to-end; then fix/verify Tripo.
- [ ] **Make regenerate + 3D async jobs** — currently regenerate is synchronous
      (blocks the request ~30–60s and dies if the backend restarts). Convert to the
      job/worker model so it survives restarts and shows progress.
- [ ] **Wire Google OAuth** — the login button is UI-only; needs a Google client ID
      + a backend verify endpoint.
- [ ] Harden for production: lock down CORS origins, require `JWT_SECRET`,
      rate-limit auth endpoints, consider encrypting stored 3D API keys.
- [ ] **Script→Storyboard** — see the full "build plan" section above. Input UI
      (3 steps: script → style → aspect) is done; next is the backend engine.
      Suggested first task: **Stage A** (LLM shot breakdown) → then MVP D/E/F.
      ⚠️ Confirm the OPEN DECISION (Simple vs Full-control editing) with the user first.
- [ ] Build the later roadmap workflows (Storyboard→Animatics→Final Video).
