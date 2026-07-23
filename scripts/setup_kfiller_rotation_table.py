"""One-time / idempotent setup for the Korean-filler (K-FILLER) rotation store.

Korean dramas air as 3 physical pieces (A/B/C) but their program hour has more
PRGS slots (typically 5), so the blank slots are padded with K-FILLER spots. The
Daily Programming filler picker now auto-fills those blanks with random K-FILLERs
drawn WITHOUT replacement — each row here = one K-FILLER code already used in the
current cycle; once every active filler is used the table is cleared and a fresh
cycle begins. Keyed on COD_PROGRA (the filler's code). Safe to re-run.

    uv run python scripts/setup_kfiller_rotation_table.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # project root → import like the other scripts

from browser_automation.etere_direct_client import connect

_DDL = [
    "IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'chat') EXEC('CREATE SCHEMA chat')",
    """IF OBJECT_ID('chat.kfiller_rotation', 'U') IS NULL
       CREATE TABLE chat.kfiller_rotation (
           kf_code    NVARCHAR(60) NOT NULL PRIMARY KEY,   -- 'K-FILLER25-027'
           used_at    DATETIME     NOT NULL CONSTRAINT DF_kfiller_rotation_used DEFAULT GETDATE(),
           used_by    NVARCHAR(100) NULL
       )""",
]


def main():
    with connect() as conn:
        cur = conn.cursor()
        for stmt in _DDL:
            cur.execute(stmt)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM chat.kfiller_rotation")
        print(f"chat.kfiller_rotation ready — {cur.fetchone()[0]} filler(s) used in the current cycle.")


if __name__ == "__main__":
    main()
