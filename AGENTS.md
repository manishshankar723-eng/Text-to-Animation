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

**Last updated:** 2026-07-24 (Board: change visual style → switchable style variants)

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

### Server (Phase 2 — FastAPI backend, in `server/`)
| File | Responsibility |
|------|----------------|
| `server/main.py` | FastAPI app, all endpoints, provider validation. |
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
| `client/src/styles.css` | Dark + champagne-gold theme. |

### API endpoints
- `POST /auth/register` · `POST /auth/login` · `GET /auth/me` · `DELETE /auth/me` (delete account)
- `GET/PUT /auth/me/api-keys` · `DELETE /auth/me/api-keys/{provider}` — saved 3D keys (plaintext)
- `POST /storyboards/breakdown` — Script→Storyboard Stage A: script → shot list (auth'd, sync; `TEXT_PROVIDER` backend)
- `POST /storyboards` — Stage D: generate panels from reviewed shots (async job; poll `GET /jobs/{id}`) · `GET /storyboards/{id}/panel/{index}` — serve a panel PNG · `GET /storyboards/{id}/pdf` — Stage F: export the board as PDF
- `POST /characters/reference` — generate T-pose reference from text (surfaces the REAL error via `ReferenceGenerationError`)
- `GET /characters/reference/{id}/image` — serve generated reference for preview
- `POST /characters` — upload image (or `reference_id`) + options → `job_id` (async)
- `GET /jobs` · `GET /jobs/{id}` — list / poll (owner-scoped). Job carries live `progress`.
- `GET /jobs/{id}/assets` — PNG URLs per part/view. Also serves PARTIAL assets while `running`.
- `GET /jobs/{id}/image/{part}/{view}` — serve a single asset PNG (enables local-run previews)
- `GET /jobs/{id}/download` — full zip · `GET /jobs/{id}/download/{part}` — per-section zip
- `POST /jobs/{id}/regenerate-part` — redo one part · `POST /jobs/{id}/regenerate-view` — redo one view
- `POST /jobs/{id}/meshy` — submit part(s) for 3D; body accepts `provider` (`meshy`|`tripo`) + optional `api_key` (falls back to saved key)
- `GET /templates` · `GET /health`

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
