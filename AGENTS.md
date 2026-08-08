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

**Last updated:** 2026-08-07 (NEW WORKFLOW: Animatics to Final Video — Veo. First
workflow that costs money per click; read its Work Log entry before touching it.)

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
| `animatic.py` | **Storyboard → Animatic.** Timed image sequence + text layer + audio → MP4. Owns the ffmpeg integration: `ffmpeg_exe()` and `run_ffmpeg()` are public so `video_assemble.py` reuses them. `plan_segments()` cuts the timeline wherever a text clip starts/ends; `draw_texts()` burns captions in with Pillow. Spends no AI quota. |
| `video_client.py` | **Animatics → Final Video.** Veo image→video. The ONLY module that knows Veo exists. **Switchable backend (`VIDEO_PROVIDER`): Vertex AI or Gemini API** — same shape as `gemini_client.py`. **BILLED PER SECOND OF OUTPUT.** `estimate_cost_usd()` lives here. There is no Google Flow API — read the module docstring. |
| `video_assemble.py` | Joins rendered clips into the final cut (`cut` = stream copy, `crossfade` = re-encode). Free and repeatable — spends nothing. Reuses `animatic.py`'s ffmpeg helpers. Take `durations_ms` from the caller: **there is no ffprobe** on an `imageio-ffmpeg` install. |
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
| `client/src/components/AnimaticLibrary.jsx` | "Your Animatics": New / From a Storyboard tiles + Recent / All sections. **Mirrors `StoryboardLibrary.jsx` and shares its `.lib-*` styles** — change a card in one, change it in both. |
| `client/src/components/AnimaticEditor.jsx` | The editor, as an **NLE workspace**: top bar + status strip + Media / Program / Properties panes over a full-width timeline, fixed to the viewport height. Holds all the state (playback, autosave, export). **Audio is the playback clock** (see the Work Log). `TextProperties` / `FrameProperties` / `VideoProperties` at the foot are the three states of the Properties pane. |
| `client/src/components/FrameStrip.jsx` | Frame thumbnails: typed hold time, drag-reorder, duplicate, delete, add images. |
| `client/src/components/Shapes.jsx` | The shape layer's vocabulary: the unit-square polygons (**mirrored in `animatic.py`**), the CSS for them, and the picker gallery. |
| `client/src/components/Timeline.jsx` | **As many lanes as the project has** — the editor passes ONE `lanes` list and both the gutter labels and the tracks render from it. Kinds: 🖼 sequence · 🖼 image overlay · T text · ◆ shapes · ♪ audio. Fixed label gutter, ruler, playhead. Drag a frame's right edge to change its hold; drag a text clip to move it, its edge to stretch it. Exports `formatTime`. |
| `client/src/components/Waveform.jsx` | Decodes the audio in the browser (WebAudio) and draws peaks on a canvas. No library. |
| `client/src/components/DialogueBox.jsx` | A shot's spoken lines, read-only (board tiles). Renders **nothing** when the shot is silent. |
| `client/src/components/DialogueEditor.jsx` | The same lines, editable, on the review step. A silent shot shows only a "＋ Add dialogue" link. |
| `client/src/styles.css` | Dark + champagne-gold theme. |

### API endpoints
- `POST /auth/register` · `POST /auth/login` · `GET /auth/me` · `DELETE /auth/me` (delete account)
- `GET/PUT /auth/me/api-keys` · `DELETE /auth/me/api-keys/{provider}` — saved 3D keys (plaintext)
- `POST /storyboards/breakdown` — Script→Storyboard Stage A: script → shot list (auth'd, sync; `TEXT_PROVIDER` backend)
- `POST /storyboards` — Stage D: generate panels from reviewed shots (async job; poll `GET /jobs/{id}`) · `GET /storyboards/{id}/panel/{index}` — serve a panel PNG · `GET /storyboards/{id}/pdf` — Stage F: export the board as PDF
- **Board editing:** `POST /storyboards/{id}/panels/insert` (`{at, description}`) — add a blank panel, shifting the rest down · `DELETE /storyboards/{id}/panels/{index}` — remove a panel, shifting up. Both renumber files+indices+urls across ALL style variants so `index == position` stays true; the new panel is drawn with the normal `regenerate-panel` call.
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
  `POST /animatics/{id}/export` — 202, encodes off-request (poll `GET /jobs/{id}`) · `POST /animatics/{id}/stop` · `GET /animatics/{id}/video`
- **Animatics → Final Video (`server/videos.py`, kind `final_video`):**
  `POST /final-videos` — new project; with `source_animatic_id` and no shots it fills the shot list from that animatic's frames (the editor's "🎞️ Make final video"); `source_storyboard_id` does the same from drawn panels · `GET /final-videos` — library · `GET/PUT /final-videos/{id}` — read / save (`shots`, `art`, `settings`, `title`; PUT is the workspace autosave, 409 while busy) · `DELETE /final-videos/{id}`
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
| — | Export resolution / quality / include-audio are per-project **settings**, not env vars: `AnimaticSettings.resolution` (short edge), `.quality` (CRF), `.include_audio`. |

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

`tests/e2e_animatic.py` drives a real Chromium against a live API + Vite on
isolated ports. It has caught bugs a clean `npm run build` happily shipped
(mis-aligned timeline labels, a waveform that never drew, a preview that wasn't
the exported frame shape, dead space under the workspace).

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

**Animatics → Final Video is BUILT but NOT run against Veo (2026-08-07):** the
whole workflow is wired end-to-end — art tray, per-shot motion prompts, render,
assemble, download — and the API + assembler are tested (see the Work Log entry).
**No real Veo call has ever been made from this code.** It needs a
billing-enabled project (or a `GEMINI_API_KEY`) and the first render is the real
test. Read the money notes in that entry before running one.

**There is no Google Flow API** — researched 2026-08-07. Flow is a Labs *web app*:
no public API, no OAuth scope, no service account, and its credits are a separate
ledger from the API. A Google AI Pro/Ultra subscription grants **zero** API
access. Flow is a front-end over Veo, so we call Veo directly and get the same
models. Do not add "Flow integration" to the roadmap; it isn't a thing that
exists. (Driving the Flow UI with a session cookie would breach Google's terms
and break on any redesign.)

**Not yet verified live** (needs real keys / steady backend):
- **Veo video rendering** — see above. Coded, typed, unit-tested, never called.
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
