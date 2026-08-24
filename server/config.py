"""
config.py — API configuration read from environment variables.

Loads .env (same as the CLI) so the API and CLI share credentials/config.
"""

import os

from dotenv import load_dotenv

# Load .env once, before anything reads env vars.
load_dotenv()


# --- Google Cloud (shared with the CLI pipeline) -----------------------------
GOOGLE_CLOUD_PROJECT = os.environ.get(
    "GOOGLE_CLOUD_PROJECT", "project-cf56be07-4f9e-45d4-9f4"
)

# --- Paths -------------------------------------------------------------------
# Where uploaded reference images are stored before a job runs.
UPLOAD_DIR = os.environ.get("API_UPLOAD_DIR", "uploads")
# Base directory the pipeline writes local output to (mirrors the CLI default).
OUTPUT_DIR = os.environ.get("API_OUTPUT_DIR", "output")
# Prompts config used by the pipeline.
CONFIG_PATH = os.environ.get("API_CONFIG_PATH", "prompts.yaml")

# --- Job store ---------------------------------------------------------------
# Where the record of everything the app produces lives — character runs,
# storyboards, animatics, and any workflow added later. Only the image/video
# BYTES live elsewhere (disk, or GCS); their URLs are stored in the job record.
#   "mongo"     (default) MongoDB — the system of record.
#   "firestore" legacy, kept for existing deployments.
#   "memory"    in-process dict + JSON mirror. Dev only; not multi-process safe.
JOB_STORE = os.environ.get("API_JOB_STORE", "mongo").lower()
JOBS_COLLECTION = os.environ.get("API_JOBS_COLLECTION", "jobs")
FIRESTORE_COLLECTION = os.environ.get("API_FIRESTORE_COLLECTION", "character_jobs")
# When JOB_STORE == "memory", jobs are ALSO mirrored to this JSON file so a
# backend restart (e.g. uvicorn --reload picking up a code change) doesn't wipe
# saved storyboards. Set empty to disable and keep jobs purely in RAM.
LOCAL_JOBS_PATH = os.environ.get("API_LOCAL_JOBS_PATH", ".local_jobs.json")
# At startup, close out any job still marked RUNNING/QUEUED. Work runs in THIS
# process's thread pool, so such a job has no worker and never will — left
# alone its page is frozen for ever, showing "Stop generation" with every
# Regenerate button hidden because the board believes it is still busy.
# TURN THIS OFF if more than one API process shares a job store, or one will
# reap another's live work. See main._reap_orphaned_jobs.
REAP_ORPHANED_JOBS = os.environ.get("API_REAP_ORPHANED_JOBS", "1").lower() not in (
    "0", "false", "no",
)

# --- Worker ------------------------------------------------------------------
# How many pipeline jobs may run concurrently. Each job makes several Gemini
# calls (and optionally polls Meshy for up to 30 min), so keep this small.
MAX_WORKERS = int(os.environ.get("API_MAX_WORKERS", "2"))

# --- Uploads -----------------------------------------------------------------
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = int(os.environ.get("API_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

# --- Animatics (Storyboard → Animatic) ---------------------------------------
# Audio laid under an animatic. Browsers report mp3 as audio/mpeg; the extension
# is accepted as a fallback because content types vary by OS and browser.
ALLOWED_AUDIO_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/wave",
    "audio/mp4", "audio/x-m4a", "audio/aac", "audio/ogg", "audio/webm",
}
ALLOWED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".webm"}
MAX_AUDIO_BYTES = int(os.environ.get("API_MAX_AUDIO_BYTES", str(50 * 1024 * 1024)))
# Video clips dropped onto the animatic timeline. Like the audio list above, the
# extension is accepted as a fallback because browsers disagree about content
# types — a .mov arrives as video/quicktime on one machine and empty on another.
# The list is what ffmpeg can decode AND a browser can play in the Program
# monitor: the preview uses a real <video> element, so a format the browser
# can't show would export correctly and preview as a black rectangle.
ALLOWED_VIDEO_TYPES = {
    "video/mp4", "video/quicktime", "video/webm", "video/x-m4v", "video/mpeg",
}
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
# Deliberately larger than the audio cap and separately tunable: a 30-second
# phone clip is comfortably past 50MB, and refusing it is the first thing anyone
# would hit. The real guard on cost is MAX_EXTRACTED_FRAMES in video_frames.py —
# what matters is how much gets DECODED, not how much gets stored.
MAX_VIDEO_BYTES = int(os.environ.get("API_MAX_VIDEO_BYTES", str(300 * 1024 * 1024)))
# Guard rail on the sequence length — an animatic is a rough cut, not a feature.
MAX_ANIMATIC_FRAMES = int(os.environ.get("API_MAX_ANIMATIC_FRAMES", "500"))
# Text clips per animatic. Each boundary splits the timeline into another
# rendered still, so this also caps how much work an export can be asked to do.
MAX_ANIMATIC_TEXTS = int(os.environ.get("API_MAX_ANIMATIC_TEXTS", "400"))
# Shapes per animatic. Same reasoning as the text cap: every shape boundary is
# another cut in the timeline and another still to render.
MAX_ANIMATIC_SHAPES = int(os.environ.get("API_MAX_ANIMATIC_SHAPES", "400"))
# Lanes on the timeline. This is a rough cut, not a compositing suite — past a
# couple of dozen rows nothing is legible anyway.
MAX_ANIMATIC_LAYERS = int(os.environ.get("API_MAX_ANIMATIC_LAYERS", "24"))
# Items in the MEDIA LIBRARY. ⚠ Deliberately higher than MAX_ANIMATIC_FRAMES:
# the library outlives the timeline, so a project that has had clips deleted and
# re-added holds more sources than it has clips. It is a list of references, not
# of files, so the cost of a row here is a few hundred bytes.
MAX_ANIMATIC_ASSETS = int(os.environ.get("API_MAX_ANIMATIC_ASSETS", "1000"))
# Audio FILES per animatic (music + voiceover is the usual pair). Every extra
# file is another upload to store, so this stays small.
MAX_ANIMATIC_AUDIO_TRACKS = int(os.environ.get("API_MAX_ANIMATIC_AUDIO_TRACKS", "4"))
# Audio CLIPS per animatic. ⚠ NOT the same cap: the razor cuts one file into
# several clips without uploading anything, so counting clips against the file
# limit above would make a track uncuttable after three cuts. Every clip is one
# more ffmpeg input to decode, which is why there is a ceiling at all.
MAX_ANIMATIC_AUDIO_CLIPS = int(os.environ.get("API_MAX_ANIMATIC_AUDIO_CLIPS", "48"))
# Transitions per animatic. There is at most one per cut, so the frame cap is
# the real ceiling; this only stops a malformed save carrying thousands.
MAX_ANIMATIC_TRANSITIONS = int(
    os.environ.get("API_MAX_ANIMATIC_TRANSITIONS", str(MAX_ANIMATIC_FRAMES))
)

# --- Final video (Animatics → Final Video) -----------------------------------
# Veo renders one clip per shot, then the clips are concatenated. Every number
# here bounds SPEND as much as it bounds work: a render is billed per second of
# output, so a 40-shot project at 8s is a real bill. See video_client.py.
#
# Shots per final-video project. Deliberately lower than MAX_ANIMATIC_FRAMES —
# an animatic frame is free to add, a shot is not free to render.
MAX_VIDEO_SHOTS = int(os.environ.get("API_MAX_VIDEO_SHOTS", "60"))
# Reference stills ("ingredients") per shot. Veo itself accepts at most 3.
MAX_VIDEO_REFERENCES = 3
# How many shots one "Render all" may submit in a single batch. Past this the
# user is asked to render in passes, which keeps a mis-click from spending
# hundreds of dollars in one press.
MAX_VIDEO_BATCH = int(os.environ.get("API_MAX_VIDEO_BATCH", "12"))
# Concurrent RENDER jobs. Separate from MAX_WORKERS on purpose: a Veo render
# blocks its thread for minutes, and sharing the pipeline pool would let one
# video project starve every storyboard on the server.
MAX_VIDEO_WORKERS = int(os.environ.get("API_MAX_VIDEO_WORKERS", "2"))
# Uploaded final-art stills use the same allow-list as every other image upload.

# --- Auth (JWT login + MongoDB user store) -----------------------------------
# Secret used to sign JWTs. MUST be set in production. A dev fallback is used
# if unset (with a loud warning at startup) so local runs work out of the box.
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

# Where user accounts live: "mongo" (default, MongoDB Atlas) or "local"
# (a JSON file on disk — handy for dev when MongoDB is unreachable).
USER_STORE = os.environ.get("API_USER_STORE", "mongo").lower()
# Path for the local file-based user store (used when USER_STORE == "local").
LOCAL_USERS_PATH = os.environ.get("API_LOCAL_USERS_PATH", ".local_users.json")

# MongoDB connection for user accounts.
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "character_api")
USERS_COLLECTION = os.environ.get("API_USERS_COLLECTION", "users")

# --- Script drafts ------------------------------------------------------------
# The script being written in the text panel, autosaved so a refresh can't lose
# it. One draft per user; stored alongside the accounts (same backend as
# USER_STORE), so there is nothing extra to configure.
DRAFTS_COLLECTION = os.environ.get("API_DRAFTS_COLLECTION", "script_drafts")
LOCAL_DRAFTS_PATH = os.environ.get("API_LOCAL_DRAFTS_PATH", ".local_drafts.json")
# Upper bound on an autosaved script. Generous — a feature screenplay is well
# under this — but bounded so a paste accident can't push megabytes per keystroke.
MAX_SCRIPT_CHARS = int(os.environ.get("API_MAX_SCRIPT_CHARS", str(400_000)))

# --- Admin panel --------------------------------------------------------------
# WHO IS AN ADMINISTRATOR. The role lives on the user document (`role: "admin"`)
# so it can be granted and revoked from the panel itself — but that is a
# chicken-and-egg problem on a fresh database: granting the first role requires
# an admin, and there isn't one. Every address listed here is treated as an
# admin REGARDLESS of what its document says. That is the bootstrap, and also
# the way back in if the last admin is demoted by mistake.
#
# ⚠ IT IS A FLOOR, NOT A LIST. Being absent from here does not make you an
# ordinary user — the document's `role` still counts. Comma-separated.
ADMIN_EMAILS = frozenset(
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
)

# The activity log — registrations, sign-ins, and every admin action. Follows
# the USER STORE (`API_USER_STORE`) for the same reason drafts do: accounts and
# the record of what those accounts did belong in the same place, and that is
# one switch rather than two.
EVENTS_COLLECTION = os.environ.get("API_EVENTS_COLLECTION", "events")
LOCAL_EVENTS_PATH = os.environ.get("API_LOCAL_EVENTS_PATH", ".local_events.json")
# How long an event is kept. Enforced by a Mongo TTL index, so expiry happens in
# the database and needs no cleanup job. Set 0 to keep events for ever.
EVENT_RETENTION_DAYS = int(os.environ.get("API_EVENT_RETENTION_DAYS", "180"))
# The local JSON mirror is a dev convenience, not a database — it is rewritten
# whole on every append, so it is capped rather than allowed to grow unbounded.
MAX_LOCAL_EVENTS = int(os.environ.get("API_MAX_LOCAL_EVENTS", "5000"))
# Page size ceiling for the admin user/event tables. A guard on one accidental
# `?limit=100000` against a remote database, not a product decision.
ADMIN_MAX_PAGE = int(os.environ.get("API_ADMIN_MAX_PAGE", "200"))

# --- Feature flags ------------------------------------------------------------
# What each account may SEE and USE — the registry the sidebar, the entitlements
# endpoint and every `require_feature` guard read from. Follows the USER STORE
# for the same reason drafts and events do: one switch, one place.
FEATURES_COLLECTION = os.environ.get("API_FEATURES_COLLECTION", "features")
LOCAL_FEATURES_PATH = os.environ.get("API_LOCAL_FEATURES_PATH", ".local_features.json")
# ⚠ THIS IS READ ON NEARLY EVERY REQUEST AND CHANGED A FEW TIMES A WEEK, so it
# is cached in-process and an admin write bumps the cache immediately. The TTL
# only matters for the OTHER processes in a multi-worker deployment, which is
# what it is sized for: a flag flipped in the panel lands everywhere within it.
FEATURE_CACHE_TTL_S = float(os.environ.get("API_FEATURE_CACHE_TTL_S", "20"))

# --- Billing tiers ------------------------------------------------------------
# ⚠ "TIERS", NEVER "PLANS". `server/plans.py`, the `/plans` route and
# `JobKind.PLAN` have meant *Plan & Script* — the content-planning workflow —
# since long before there was any billing in this app. A collection called
# `plans` here would be the single most confusing name in the codebase.
TIERS_COLLECTION = os.environ.get("API_TIERS_COLLECTION", "tiers")
LOCAL_TIERS_PATH = os.environ.get("API_LOCAL_TIERS_PATH", ".local_tiers.json")
# Same reasoning as the feature cache: read constantly, written a few times a
# month. See FEATURE_CACHE_TTL_S.
TIER_CACHE_TTL_S = float(os.environ.get("API_TIER_CACHE_TTL_S", "60"))
# ⚠ DISPLAY ONLY. Prices are stored in MINOR UNITS (cents, paise) as integers,
# so nothing in the money path is ever a float. This says which currency those
# integers are in; it does NOT convert anything.
BILLING_CURRENCY = os.environ.get("API_BILLING_CURRENCY", "USD")

# --- Offers and subscriptions -------------------------------------------------
# Discounts (a site-wide sale, or a redeemable coupon) and the record of who is
# paying for what. Same store as everything else the panel owns.
OFFERS_COLLECTION = os.environ.get("API_OFFERS_COLLECTION", "offers")
LOCAL_OFFERS_PATH = os.environ.get("API_LOCAL_OFFERS_PATH", ".local_offers.json")
SUBSCRIPTIONS_COLLECTION = os.environ.get("API_SUBSCRIPTIONS_COLLECTION", "subscriptions")
LOCAL_SUBSCRIPTIONS_PATH = os.environ.get(
    "API_LOCAL_SUBSCRIPTIONS_PATH", ".local_subscriptions.json"
)
OFFER_CACHE_TTL_S = float(os.environ.get("API_OFFER_CACHE_TTL_S", "30"))

# --- Usage counters -----------------------------------------------------------
# What each account has used this calendar month, so a tier's `limits` can be
# enforced. ⚠ NOT CACHED — a counter that is read from a cache is a counter that
# lets somebody over their limit for the length of the TTL.
USAGE_COLLECTION = os.environ.get("API_USAGE_COLLECTION", "usage_counters")
LOCAL_USAGE_PATH = os.environ.get("API_LOCAL_USAGE_PATH", ".local_usage.json")

# True while running on the insecure dev JWT secret (set in security.py).
JWT_SECRET_IS_DEV = False
