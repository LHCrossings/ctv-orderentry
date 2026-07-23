"""One-time / idempotent setup for the long-form PI rotation store.

Creates `chat.pi_lf_rotation` in the shared Etere SQL DB. Each row = one
long-form PI spot token (`PI-LF-NNNN` / `WLPI-LF-NNNN`) that has already been
used in the CURRENT rotation cycle. The Marketplace assigner draws random spots
WITHOUT replacement until every active spot is used, then clears this table to
start a fresh cycle. Safe to re-run from any environment — it only creates the
table if missing and never touches existing rows.

    uv run python scripts/setup_pi_lf_rotation_table.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # project root → import like the other scripts

from browser_automation.etere_direct_client import connect

_DDL = [
    "IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'chat') EXEC('CREATE SCHEMA chat')",
    """IF OBJECT_ID('chat.pi_lf_rotation', 'U') IS NULL
       CREATE TABLE chat.pi_lf_rotation (
           pi_token   NVARCHAR(30) NOT NULL PRIMARY KEY,   -- 'PI-LF-0002' / 'WLPI-LF-0008'
           used_at    DATETIME     NOT NULL CONSTRAINT DF_pi_lf_rotation_used DEFAULT GETDATE(),
           used_by    NVARCHAR(100) NULL
       )""",
]


def main():
    with connect() as conn:
        cur = conn.cursor()
        for stmt in _DDL:
            cur.execute(stmt)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM chat.pi_lf_rotation")
        print(f"chat.pi_lf_rotation ready — {cur.fetchone()[0]} token(s) used in the current cycle.")


if __name__ == "__main__":
    main()
