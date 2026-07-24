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
# "firestore" (default) persists jobs in Firestore; "memory" keeps them in
# process only (handy for local dev with no Firestore access).
JOB_STORE = os.environ.get("API_JOB_STORE", "firestore").lower()
FIRESTORE_COLLECTION = os.environ.get("API_FIRESTORE_COLLECTION", "character_jobs")
# When JOB_STORE == "memory", jobs are ALSO mirrored to this JSON file so a
# backend restart (e.g. uvicorn --reload picking up a code change) doesn't wipe
# saved storyboards. Set empty to disable and keep jobs purely in RAM.
LOCAL_JOBS_PATH = os.environ.get("API_LOCAL_JOBS_PATH", ".local_jobs.json")

# --- Worker ------------------------------------------------------------------
# How many pipeline jobs may run concurrently. Each job makes several Gemini
# calls (and optionally polls Meshy for up to 30 min), so keep this small.
MAX_WORKERS = int(os.environ.get("API_MAX_WORKERS", "2"))

# --- Uploads -----------------------------------------------------------------
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = int(os.environ.get("API_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

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

# True while running on the insecure dev JWT secret (set in security.py).
JWT_SECRET_IS_DEV = False
