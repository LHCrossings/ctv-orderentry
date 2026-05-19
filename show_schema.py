import sqlite3
from pathlib import Path

conn = sqlite3.connect(Path(__file__).resolve().parent / "data" / "customers.db")
result = conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='customers'"
).fetchone()
print(result[0])
