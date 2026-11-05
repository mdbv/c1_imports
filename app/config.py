import os
from pathlib import Path


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


BASE_URL = require("BASE_URL").rstrip("/")
SESSION_SECRET = require("SESSION_SECRET")
ENTRA_TENANT_ID = require("ENTRA_TENANT_ID")
ENTRA_CLIENT_ID = require("ENTRA_CLIENT_ID")
ENTRA_CLIENT_SECRET = require("ENTRA_CLIENT_SECRET")
ENTRA_GROUP_ID = require("ENTRA_GROUP_ID")
DEV_USER = os.environ.get("DEV_USER", "").strip()

LOCAL_DIR = Path(os.environ.get("LOCAL_DIR", "/app/files"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "/var/log"))
STATE_DIR = Path(os.environ.get("STATE_DIR", "/app/state"))
LOCK_FILE = Path(os.environ.get("LOCK_FILE", "/var/lock/c1get.lock"))
SYNC_SCRIPT = os.environ.get("SYNC_SCRIPT", "/app/c1get.sh")

HTTPS_ONLY = BASE_URL.startswith("https://")
REDIRECT_URI = f"{BASE_URL}/auth/callback"
AUTHORITY = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/v2.0"
LOGOUT_URL = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/oauth2/v2.0/logout"
