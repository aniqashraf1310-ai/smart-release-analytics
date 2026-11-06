"""
Create the 20 demo releases by hand (the API also does this on start-up
when the database is empty).

    python packages/api/seed_demo.py          # only if there are no releases yet
    python packages/api/seed_demo.py --force  # add them even if releases exist
Requires a trained model (npm run train) and a migrated database.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import risk  # noqa: E402
from app.db import pool  # noqa: E402
from app.demo_seed import seed  # noqa: E402

logging.basicConfig(level=logging.INFO)
pool.open(wait=True, timeout=15)
risk.load_model()
if risk.state.engine is None:
    sys.exit(risk.state.error)
try:
    n = seed(force="--force" in sys.argv)
finally:
    pool.close()
print(f"created {n} demo releases" if n else "releases already exist - nothing created (use --force)")
