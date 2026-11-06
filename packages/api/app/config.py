"""Runtime configuration (environment variables with development defaults)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # repository root

DATABASE_URL = os.getenv("DATABASE_URL",
                         "postgresql://release_app:release_app@localhost:5432/release_mgmt")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me-before-any-real-use")
JWT_TTL_MINUTES = int(os.getenv("JWT_TTL_MINUTES", "480"))
MODEL_PATH = Path(os.getenv("MODEL_PATH", ROOT / "packages" / "ml" / "artifacts" / "latest.json"))
ML_PACKAGE_DIR = Path(os.getenv("ML_PACKAGE_DIR", ROOT / "packages" / "ml"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
SEED_DEMO = os.getenv("SEED_DEMO", "1") != "0"   # create demo releases when the database is empty
