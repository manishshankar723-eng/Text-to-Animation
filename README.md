# Text-to-Animation

An AI production workflow that takes an idea from a written script all the way to a
cut video: plan → script → storyboard → animatic → AI video. It also generates
character turnaround asset sheets (front / left / three-quarter / back) from a single
reference photo, and can push those views to Meshy for 3D.

Six workflows ship in the UI, all live:

| Workflow | What it does |
|---|---|
| 🗓️ **Plan & Script** | Talk to a strategist agent → a content calendar (`.xlsx` / `.docx` / `.csv`) → the actual script for any upload on it, which loads straight into Script to Storyboard. Shows what each generation cost in tokens. |
| 🖼️ **Text to Turnaround Image** | Reference photo or prompt → per-part turnaround sheets → optional 3D (Meshy). |
| 📝 **Script to Storyboard** | Script → shot list → drawn storyboard panels, with cast/set continuity. |
| 🖼️ **Image to Animatic Image** | One panel → its key poses for a 2–10s shot. |
| 🎬 **Video Editor** | The NLE-style editor: timeline, keyframes, text, colour, audio mix → MP4/GIF/PNG. |
| 🎞️ **Image to AI Video** | Per-shot **Veo** render + assembly into the final cut. ⚠ **Billed per second.** |

> **Agents (Claude / Codex / Gemini): read [`AGENTS.md`](./AGENTS.md) first**, not this
> file. It is the single source of truth for architecture, conventions and the work log.
> This README is the human "how do I run it" guide.

---

## Tech stack, in one line each

- **Frontend** — React 18.3 + Vite 5 in [`client/`](./client). Plain JSX, hand-written
  CSS, hand-rolled WebGL for the monitor's colour grade, Web Audio for the mixer.
  ⚠ Exactly two runtime dependencies: `react` and `react-dom`.
- **Backend** — Python 3.14 + FastAPI + uvicorn in [`server/`](./server), with the
  pipeline modules at the repo root. Jobs run on a `ThreadPoolExecutor`, not a broker.
- **Rendering** — Pillow + NumPy, encoded by ffmpeg. **ffmpeg ships with the install**
  (`imageio-ffmpeg`), so there is nothing to install by hand.
- **AI** — Google only, via `google-genai`: Gemini for images/text/TTS, **Veo** for
  video. Each capability has its own independent `vertex` | `gemini` switch.
- **Data** — MongoDB by default (accounts, jobs, drafts), with **file-based local
  fallbacks** so you can run the whole app without a database. GCS for shared assets.

Full detail, including the twins rule and the deliberately-absent list, is in the
[Tech stack section of `AGENTS.md`](./AGENTS.md#-tech-stack--read-before-you-add-a-dependency).

---

## Prerequisites

| Need | Version / notes |
|---|---|
| **Python** | **3.14** is what this is developed and run on. 3.11+ should work; untested. |
| **Node.js** | **18+** (developed on v24). Only for the frontend dev server / build. |
| **A Google AI credential** | Either a **Gemini API key** (easiest) or a **Vertex AI** project with ADC. Nothing generates without one. |
| **ffmpeg** | ✅ **Not required** — bundled via `imageio-ffmpeg`. Set `FFMPEG_BINARY` only if you want your own build. |
| **MongoDB** | ⚠ **Optional for local dev** — see [Run without MongoDB](#run-without-mongodb-fully-local). Recommended if you want work to survive restarts. |
| **Google Cloud Storage** | Optional. Only the turnaround workflow uploads; tick **"Local only"** in the form to skip it. |

Windows is the primary dev platform, so the commands below are PowerShell. On
macOS/Linux the only differences are `source .venv/bin/activate` and `python3`.

---

## Quick start — run it locally

### 1. Clone and create a virtualenv

```powershell
git clone <this-repo> Text-to-Animation
cd Text-to-Animation

python -m venv .venv
.\.venv\Scripts\Activate.ps1     # macOS/Linux: source .venv/bin/activate
```

### 2. Install the Python dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure `.env`

```powershell
Copy-Item .env.example .env      # macOS/Linux: cp .env.example .env
```

Then open `.env` and set, at minimum, **one** AI credential:

```ini
# Easiest path — the Gemini Developer API, one key for everything:
IMAGE_PROVIDER=gemini
TEXT_PROVIDER=gemini
VIDEO_PROVIDER=gemini
GEMINI_API_KEY=your-key-here

# Sign your own JWTs (any long random string):
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET=paste-the-generated-string
```

`.env.example` is heavily commented and documents every other variable — read it
before changing anything unfamiliar. The most load-bearing ones are in
[Environment variables](#environment-variables-the-ones-that-matter-locally) below.

<details>
<summary>Using Vertex AI instead of the Gemini API</summary>

```ini
IMAGE_PROVIDER=vertex
TEXT_PROVIDER=vertex
VIDEO_PROVIDER=vertex
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global            # ⚠ the image models require "global"
GOOGLE_CLOUD_VIDEO_LOCATION=us-central1 # ⚠ Veo is NOT served from "global"
```

Then authenticate with application-default credentials:

```powershell
gcloud auth application-default login
gcloud config set project your-project-id
```

Billing must be enabled on the project.
</details>

### 4. Start the backend

```powershell
python -m uvicorn server.main:app --reload
```

- API: <http://127.0.0.1:8000>
- Swagger UI: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health> — reports the job store, whether ffmpeg was
  found, whether you are on the insecure dev JWT secret, and MongoDB connectivity
  (`?check_db=false` skips the Mongo ping).

**Check `/health` before anything else.** It is the fastest way to tell a
configuration problem from a bug.

### 5. Start the frontend (second terminal)

```powershell
cd client
npm install          # first time only
npm run dev          # http://localhost:5173
```

Open <http://localhost:5173>. The UI calls `http://127.0.0.1:8000` by default — no
proxy is configured because the backend already sends permissive CORS headers. To
point at a different API host, copy `client/.env.example` → `client/.env.local` and
set `VITE_API_BASE`.

### 6. Create an account

Register in the UI (**Login → Register**), or from the CLI if MongoDB is running:

```powershell
python seed_admin.py --email you@example.com                     # prompts for password
python seed_admin.py --email you@example.com --update-password   # reset it
```

Passwords must be at least 8 characters. In Swagger, `POST /auth/register` →
copy `access_token` → **Authorize** → every protected endpoint works.

---

## Run without MongoDB (fully local)

Both the account store and the job store have file-based backends, so the app runs
with no database at all. Add this to `.env`:

```ini
API_USER_STORE=local     # accounts → .local_users.json (drafts follow this setting)
API_JOB_STORE=memory     # jobs → RAM, mirrored to .local_jobs.json
```

⚠ **`API_JOB_STORE` defaults to `mongo`**, and if Mongo is unreachable the server
falls back to the local file **with a loud error in the log** — work is then saved
somewhere Mongo will never be looked at for. If you meant to run local, set it
explicitly rather than relying on the fallback.

⚠ `API_LOCAL_JOBS_PATH` is what makes `uvicorn --reload` survivable — with it empty
the memory store is pure RAM and a code-change restart loses saved storyboards.

---

## What costs money

Everything here spends AI quota except the animatic export, which is pure ffmpeg.
One thing is billed **per second of output**:

⚠ **Veo (Image to AI Video)** — roughly **$0.24** (lite/720p) to **$3+**
(standard/1080p with sound) per 8-second clip, and a 20-shot project is 20 clips.
The UI estimates every render before it spends, and `server/videos.py` is the only
router that can spend money, but the quota and the bill are yours. Server-side spend
guards: `API_MAX_VIDEO_SHOTS` (60), `API_MAX_VIDEO_BATCH` (12),
`API_MAX_VIDEO_WORKERS` (2).

⚠ **There is no Google Flow API.** Flow is a Labs web app on a separate credit
ledger; an AI Pro/Ultra subscription grants **no** API access. This calls Veo
directly — see the docstring in `video_client.py`.

Cheapest way to smoke-test the generation path:

```powershell
python smoke_test_providers.py                          # both backends: keys + model names
python run_character.py --name test --image ./face.jpg --parts hair --local-only
```

---

## Environment variables (the ones that matter locally)

`.env.example` is the complete, commented list. These are the ones you will actually
touch on a local run:

| Var | Default | Purpose |
|---|---|---|
| `IMAGE_PROVIDER` | `vertex` | `vertex` or `gemini` — image backend. |
| `TEXT_PROVIDER` | `vertex` | `vertex` or `gemini` — script breakdown / beat plans. |
| `VIDEO_PROVIDER` | `vertex` | `vertex` or `gemini` — **Veo**. Independent of the others. |
| `GEMINI_API_KEY` | — | Required for any `…=gemini` switch. |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | — / `global` | Vertex AI. ⚠ Location must be `global` for the image models. |
| `GOOGLE_CLOUD_VIDEO_LOCATION` | `us-central1` | Vertex region for Veo. ⚠ Not `global`. |
| `JWT_SECRET` | dev fallback | Signs JWTs. Unset = insecure dev key + a loud startup warning. |
| `API_USER_STORE` | `mongo` | `mongo` or `local` (JSON file). Drafts follow this. |
| `API_JOB_STORE` | `mongo` | `mongo`, `firestore`, or `memory`. |
| `MONGODB_URI` / `MONGODB_DB` | `mongodb://localhost:27017` / `character_api` | Accounts, jobs, drafts. |
| `API_MAX_WORKERS` | `2` | Concurrent pipeline jobs. Each makes several Gemini calls. |
| `IMAGE_MAX_CONCURRENCY` / `IMAGE_RPM` | see `.env.example` | Throttle for **every** image call in the process. Tune to your real Vertex quota. |
| `FFMPEG_BINARY` | bundled | Only if you want your own ffmpeg. |
| `VITE_API_BASE` (in `client/.env.local`) | `http://127.0.0.1:8000` | Where the UI sends requests. |

Export resolution / quality / audio / container are **per-project settings in the
editor**, not env vars.

---

## CLI (no server needed)

The turnaround pipeline has a standalone entry point:

```powershell
# Full run from an uploaded image, female template, skipping some parts:
python run_character.py --name kamla --image ./kamla.jpg --template human_female --skip goggles,headphone

# Generate the reference from a text prompt first, then run the pipeline:
python run_character.py --name kamla --prompt "Indian woman in red saree, age 30" --template human_female

# Cheap test — one part, no cloud upload:
python run_character.py --name kamla --image ./kamla.jpg --parts hair --local-only

# Force a backend for this run only:
python run_character.py --name kamla --image ./kamla.jpg --parts hair --local-only --provider gemini
```

Output lands in `output/`; uploads in `uploads/`. Subject templates and prompt text
live in `prompts.yaml` (`default`, `human_male`, `human_female`, `robot`, `animal`,
`bird`, `monster`, `ghost`).

---

## Tests

⚠ **There is no pytest suite.** Tests are standalone scripts that print and exit
non-zero on failure:

```powershell
python tests/render_parity.py           # the JS and Python renderers agree
python tests/effects_check.py           # the colour maths, pinned to golden values
python tests/audio_mix_check.py         # the mix, both sides
python tests/monitor_effects_check.py   # mounts the WebGL monitor; starts Vite itself, no backend needed
```

Browser tests need `pip install -r requirements-dev.txt` (Playwright).
`tests/e2e_animatic.py` drives real Chromium against a live API + Vite and takes
minutes — **run it when asked, not after every change.** Setup and commands are in
its docstring.

---

## Project layout

```
├── server/            FastAPI app — routers per workflow, auth, job store, worker
├── client/            React + Vite frontend (the app; `frontend/` at the root is dead)
│   ├── src/animatic/  The editor's engine: scene model, keyframes, audio, WebGL
│   └── public/fonts/  Bundled .ttf faces — used by the browser AND the exporter
├── tests/             Standalone *_check.py scripts + the Playwright e2e
├── luts/              Built-in .cube colour looks, read by both renderers
├── prompts.yaml       Prompt templates + per-template part ordering
├── *.py (root)        The pipeline: pipeline, gemini_client, animatic*, video_client, …
├── AGENTS.md          ⭐ Architecture, conventions, work log — the source of truth
└── .env.example       Every environment variable, commented
```

⚠ Several modules exist **twice** — once in Python for the export, once in JavaScript
for the preview (`animatic_render.py` ↔ `animatic/scene.js`, `animatic_effects.py` ↔
`animatic/gl/shaders/`, and four more). Change one side and you must change the other
in the same commit, then run its parity test. The full table is in
[`AGENTS.md`](./AGENTS.md#the-twins-rule--why-this-stack-has-two-of-some-things).

---

## Troubleshooting

**The UI loads but every request fails.** The backend isn't running, or it is on a
different port. Check <http://127.0.0.1:8000/health>, then `VITE_API_BASE`.

**"Model not found or your project does not have access to it."** Usually a model
name from the *wrong backend*, not an IAM problem — Vertex says `veo-3.1-generate-001`
where the Gemini API says `veo-3.1-generate-preview`. The workflow's setup banner
lists the ids your project can actually use. For images, set `GEMINI_IMAGE_MODEL`.

**Veo 404s while images work.** `GOOGLE_CLOUD_VIDEO_LOCATION` is probably `global`.
Veo needs a real region — `us-central1`.

**Startup warns about an insecure JWT secret.** `JWT_SECRET` is unset. Fine for a
local poke, never for anything reachable.

**The log screams that MongoDB is unavailable.** The job store fell back to a local
file. Either start Mongo, or set `API_JOB_STORE=memory` and `API_USER_STORE=local`
so the fallback is a decision instead of an accident.

**Export fails / `/health` reports `ffmpeg: false`.** The bundled binary didn't
resolve. `pip install --force-reinstall imageio-ffmpeg`, or point `FFMPEG_BINARY` at
your own build.

**Windows: the old server keeps the port after you kill it.** `pkill -f uvicorn`
does **not** kill a Windows Python process, and you end up testing stale code:

```powershell
Get-NetTCPConnection -LocalPort 8000 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

**A script crashes mid-run printing arrows or emoji.** The console is cp1252. Add
`sys.stdout.reconfigure(encoding='utf-8')` to the script.

**Gallery previews are empty.** That workflow was run with **Local only** ticked, so
nothing was uploaded — the gallery and Meshy both need public URLs.
