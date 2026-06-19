"""One-time setup for the Daily Programming per-show "exceptions" store.

Creates the `chat.show_profiles` table in the shared Etere SQL DB and seeds it
from the built-in default profiles in show_profiles.py. Idempotent — safe to
re-run; it only creates the table if missing and only seeds if the table is
empty (so it never clobbers exceptions edited via the UI).

Run once per environment after deploying the exceptions feature:
    uv run python scripts/setup_show_profiles_table.py
"""
import json

from browser_automation.etere_direct_client import connect
from src.business_logic.services.show_profiles import default_profiles, to_config

_CREATE_SCHEMA = """
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'chat')
    EXEC('CREATE SCHEMA chat')
"""

_CREATE_TABLE = """
IF OBJECT_ID('chat.show_profiles', 'U') IS NULL
CREATE TABLE chat.show_profiles (
    id          INT IDENTITY(1,1) PRIMARY KEY,
    name        NVARCHAR(100)  NOT NULL,
    enabled     BIT            NOT NULL CONSTRAINT DF_show_profiles_enabled DEFAULT 1,
    code_re     NVARCHAR(200)  NOT NULL,   -- PRIMARY match: regex vs FILMATI.COD_PROGRA
    config      NVARCHAR(MAX)  NOT NULL,   -- JSON: networks, days, window, open_bumper, close_bumper, future elements
    sort_order  INT            NOT NULL CONSTRAINT DF_show_profiles_sort DEFAULT 100,
    updated_at  DATETIME       NOT NULL CONSTRAINT DF_show_profiles_updated DEFAULT GETDATE(),
    updated_by  NVARCHAR(100)  NULL
)
"""


def main():
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(_CREATE_SCHEMA)
        cur.execute(_CREATE_TABLE)
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM chat.show_profiles")
        if cur.fetchone()[0] == 0:
            defaults = default_profiles()
            for i, p in enumerate(defaults):
                cur.execute(
                    "INSERT INTO chat.show_profiles (name, code_re, config, sort_order, updated_by) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (p["name"], p["code_re"], json.dumps(to_config(p)), (i + 1) * 10, "seed"),
                )
            conn.commit()
            print(f"Seeded {len(defaults)} default profile(s).")
        else:
            print("chat.show_profiles already has rows — left as-is (not seeding).")

        cur.execute("SELECT id, name, enabled, code_re, sort_order FROM chat.show_profiles ORDER BY sort_order, id")
        print("Current profiles:")
        for r in cur.fetchall():
            print("  ", dict(zip(("id", "name", "enabled", "code_re", "sort_order"), r)))


if __name__ == "__main__":
    main()
