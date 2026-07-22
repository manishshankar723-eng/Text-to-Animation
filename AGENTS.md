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

**Last updated:** 2026-07-22 (client redesign, subject templates, live progress, per-section 3D)

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
- [ ] Build the roadmap workflows (Script→Storyboard→Animatics→Final Video).
