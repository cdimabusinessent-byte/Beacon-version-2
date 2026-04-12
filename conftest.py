import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("BEACON_ENV_FILE", str(PROJECT_ROOT / ".env.localtest"))
