import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "ledgerlens.sqlite3"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Folio/0.1 contact@example.com")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "250"))
for path in (DATA_DIR, RAW_DIR):
    path.mkdir(parents=True, exist_ok=True)
