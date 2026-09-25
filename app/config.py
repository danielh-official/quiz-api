import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://quiz:secret@localhost:5433/quiz")
APP_URL = os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
JWT_SIGNING_KEY = os.environ.get("JWT_SIGNING_KEY", "")
STORAGE_ENCRYPTION_KEY = os.environ.get("STORAGE_ENCRYPTION_KEY", "")

# GitHub logins allowed to use the API (case-insensitive). Empty = nobody.
ALLOWED_USERS = {login.strip().lower() for login in os.environ.get("ALLOWED_USERS", "").split(",") if login.strip()}
