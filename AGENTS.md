# AGENTS.md — Shared Agent Memory & Work Tracker

> **This file is the single source of truth for any AI coding agent working on
> this project** — Claude, ChatGPT/Codex, Gemini, or any other model.
> Read it FIRST on every session, work from the "Current State / Next Steps"
> section, and UPDATE it when you finish (see "Protocol" below).

`CLAUDE.md` and `GEMINI.md` are thin pointers to this file so every tool loads it.

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

**Last updated:** 2026-07-29 (Storyboard → Animatic: timeline editor + MP4 export)

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
| `gemini_client.py` | Image generation. **Switchable backend: Vertex AI or Gemini API.** |
| `script_breakdown.py` | Script→Storyboard Stage A: script → shot list (LLM). **Switchable text backend (`TEXT_PROVIDER`): Vertex AI or Gemini API.** |
| `splitter.py` | Split 2×2 sheet → 4 views. |
| `postprocess.py` | Clean white bg + crop + normalize. |
| `storage.py` | Local save, GCS upload, zip. |
| `meshy.py` | Meshy multi-image-to-3D submit + poll. |
| `tripo.py` | Tripo.ai multiview-to-3D submit + poll. **UNVERIFIED** (no live test). |
| `prompts.yaml` | Prompt templates + per-template `parts_order`. Subject types: `default` (human, gender inferred), `human_male`, `human_female`, `robot`, `animal`, `bird`, `monster`, `ghost`. Global `parts_order` fallback. |
| `postprocess.py` | Clean white bg + auto-crop + **group-normalize** (4 views share one scale). |
| `splitter.py` | Split 2×2 sheet → 4 views at natural aspect (NO square resize). |
| `animatic.py` | **Storyboard → Animatic.** Timed image sequence + audio → MP4. The ONLY module that knows ffmpeg exists. Spends no AI quota. |

### Server (Phase 2 — FastAPI backend, in `server/`)
| File | Responsibility |
|------|----------------|
| `server/main.py` | FastAPI app, most endpoints, provider validation. |
| `server/animatics.py` | `/animatics` router: animatic CRUD, media upload, frame/audio serving, export, stop. |
| `server/common.py` | Helpers shared by `main.py` and `animatics.py` (`get_owned_job`, `board_dir`, `variants_of`, `panel_path`). They live here so the two route modules don't import each other. |
| `server/config.py` | Env-driven settings (paths, job store, auth, Mongo). |
| `server/schemas.py` | Pydantic models (`Job`, responses, `MeshyRequest`). |
| `server/jobs.py` | Job store: Firestore (default) + in-memory fallback. |
| `server/worker.py` | ThreadPoolExecutor running the pipeline off-request. |
| `server/security.py` | bcrypt password hashing + JWT create/verify. |
| `server/users.py` | MongoDB-backed user store (`users` collection). |
| `server/auth.py` | `/auth/register`, `/auth/login`, `/auth/me`, `get_current_user`. |

### Client (Phase 3 — React + Vite, in `client/`)
| File | Responsibility |
|------|----------------|
| `client/src/api.js` | Fetch client, JWT in localStorage, auth'd blob download, cache-busted zip, friendly network errors. |
| `client/src/App.jsx` | Landing → Login → sidebar shell. Nav state, upgrade + account (logout) popups. |
| `client/src/components/Landing.jsx` | Public marketing landing page (full-bleed). |
| `client/src/components/Login.jsx` | Login / register + "Continue with Google" (UI only, not wired) + back-to-home. |
| `client/src/components/Sidebar.jsx` | Nav rail: Home + Workflows (Text to Image live; others "Soon") + profile chip + gold Upgrade. |
| `client/src/components/Home.jsx` | Profile, plan/credits, recent work + downloads, saved 3D API keys, delete account. |
| `client/src/components/WorkflowSoon.jsx` | Placeholder for roadmap workflows. |
| `client/src/components/GenerateForm.jsx` | Describe/Upload tabs (drag-and-drop), subject-type dropdown, parts multi-select chips + custom asset. |
| `client/src/components/JobList.jsx` | Owner's jobs; auto-polls while active. |
| `client/src/components/JobDetail.jsx` | Live progress bar + per-section skeletons, incremental gallery, per-view/section regenerate, failed-part retry, per-section download + 3D popup. |
| `client/src/components/StoryboardToAnimatics.jsx` | Animatics workflow shell: library ⇄ one open animatic. |
| `client/src/components/AnimaticLibrary.jsx` | "Your Animatics" grid + New (blank) / From a storyboard. |
| `client/src/components/AnimaticEditor.jsx` | The editor: preview, transport, autosave, export. **Audio is the playback clock** (see the Work Log). |
| `client/src/components/FrameStrip.jsx` | Frame thumbnails: typed hold time, drag-reorder, duplicate, delete, add images. |
| `client/src/components/Timeline.jsx` | Proportional bars + ruler + playhead; drag a bar's right edge to change a hold. Exports `formatTime`. |
| `client/src/components/Waveform.jsx` | Decodes the audio in the browser (WebAudio) and draws peaks on a canvas. No library. |
| `client/src/styles.css` | Dark + champagne-gold theme. |

### API endpoints
- `POST /auth/register` · `POST /auth/login` · `GET /auth/me` · `DELETE /auth/me` (delete account)
- `GET/PUT /auth/me/api-keys` · `DELETE /auth/me/api-keys/{provider}` — saved 3D keys (plaintext)
- `POST /storyboards/breakdown` — Script→Storyboard Stage A: script → shot list (auth'd, sync; `TEXT_PROVIDER` backend)
- `POST /storyboards` — Stage D: generate panels from reviewed shots (async job; poll `GET /jobs/{id}`) · `GET /storyboards/{id}/panel/{index}` — serve a panel PNG · `GET /storyboards/{id}/pdf` — Stage F: export the board as PDF
- **Board editing:** `POST /storyboards/{id}/panels/insert` (`{at, description}`) — add a blank panel, shifting the rest down · `DELETE /storyboards/{id}/panels/{index}` — remove a panel, shifting up. Both renumber files+indices+urls across ALL style variants so `index == position` stays true; the new panel is drawn with the normal `regenerate-panel` call.
- **Library (Stage G):** `GET /storyboards` — the caller's saved boards (lean summaries: title, genre, aspect, cover) · `GET /storyboards/{id}/project` — saved shots+settings for Duplicate · `PATCH /storyboards/{id}` — rename · `DELETE /storyboards/{id}` — delete record + panel files
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
  `POST /animatics` — new project; with `source_storyboard_id` and no frames it fills the sequence from that board's DRAWN panels (the board's "🎬 Make animatic") · `GET /animatics` — library · `GET/PUT /animatics/{id}` — read / save the project (PUT is the editor's autosave; 409 while exporting) · `DELETE /animatics/{id}`
  `POST /animatics/{id}/images` (multi-file) · `POST /animatics/{id}/audio` — uploads; images are stored but NOT sequenced (the client picks the order) · `GET /animatics/{id}/frame/{frame_id}` — ONE url shape for both source kinds · `GET /animatics/{id}/media/{upload_id}` — a just-uploaded image, before it's saved · `GET /animatics/{id}/audio`
  `POST /animatics/{id}/export` — 202, encodes off-request (poll `GET /jobs/{id}`) · `POST /animatics/{id}/stop` · `GET /animatics/{id}/video`
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
| `MESHY_API_KEY` | Meshy 3D generation. |
| `JWT_SECRET` | **Required in prod.** JWT signing key. Dev fallback warns loudly. |
| `JWT_ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT config (defaults HS256 / 1440). |
| `MONGODB_URI` / `MONGODB_DB` | User account store (default `mongodb://localhost:27017`). |
| `API_JOB_STORE` | `firestore` (default) or `memory` (local dev). |
| `API_MAX_WORKERS` | Concurrent pipeline jobs (default 2). |
| `FFMPEG_BINARY` | Optional path to your own ffmpeg. Unset → PATH → the `imageio-ffmpeg` bundled binary. |
| `API_MAX_AUDIO_BYTES` | Animatic audio upload cap (default 50 MB). |
| `API_MAX_ANIMATIC_FRAMES` | Frames per animatic (default 500). |

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

**Script → Storyboard now opens on "Your Storyboards"** (Stage G): every generated
board is saved and re-openable, with rename / duplicate / delete / public share
link per card. Note that persistence follows the job store — under
`API_JOB_STORE=memory` the library empties when the backend restarts.

**Not yet verified live** (needs real keys / steady backend):
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
- [x] Client redesign (sidebar dashboard), subject-type templates, live progress,
      per-section 3D + saved API keys, regenerate part/view, custom assets (2026-07-22).
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
