"""One-time / idempotent setup for the Daily Programming per-show "exceptions" store.

Creates `chat.show_profiles` in the shared Etere SQL DB (and migrates it if it
already exists from an earlier version), then seeds any built-in default profile
whose name isn't already present. Safe to re-run from any environment — it never
updates or deletes existing rows, so it can't clobber exceptions edited via the UI.

    uv run python scripts/setup_show_profiles_table.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # project root → import like the other scripts

from browser_automation.etere_direct_client import connect
from src.business_logic.services.show_profiles import default_profiles, to_config

_DDL = [
    # schema
    "IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'chat') EXEC('CREATE SCHEMA chat')",
    # table (fresh installs): a profile matches by code_re OR label — both nullable
    """IF OBJECT_ID('chat.show_profiles', 'U') IS NULL
       CREATE TABLE chat.show_profiles (
           id          INT IDENTITY(1,1) PRIMARY KEY,
           name        NVARCHAR(100)  NOT NULL,
           enabled     BIT            NOT NULL CONSTRAINT DF_show_profiles_enabled DEFAULT 1,
           code_re     NVARCHAR(200)  NULL,        -- match by file COD_PROGRA regex (e.g. Korean News)
           label       NVARCHAR(100)  NULL,        -- OR match by grid kind tag (e.g. Children)
           config      NVARCHAR(MAX)  NOT NULL,    -- JSON: networks/days/window/bumpers/elements + future kinds
           sort_order  INT            NOT NULL CONSTRAINT DF_show_profiles_sort DEFAULT 100,
           updated_at  DATETIME       NOT NULL CONSTRAINT DF_show_profiles_updated DEFAULT GETDATE(),
           updated_by  NVARCHAR(100)  NULL
       )""",
    # migrate an existing table created before `label` / nullable code_re existed
    "IF COL_LENGTH('chat.show_profiles', 'label') IS NULL ALTER TABLE chat.show_profiles ADD label NVARCHAR(100) NULL",
    """IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('chat.show_profiles')
                 AND name = 'code_re' AND is_nullable = 0)
       ALTER TABLE chat.show_profiles ALTER COLUMN code_re NVARCHAR(200) NULL""",
]


def main():
    with connect() as conn:
        cur = conn.cursor()
        for stmt in _DDL:
            cur.execute(stmt)
        conn.commit()

        cur.execute("SELECT name FROM chat.show_profiles")
        existing = {r[0] for r in cur.fetchall()}
        added = 0
        for i, p in enumerate(default_profiles()):
            if p["name"] in existing:
                continue
            cur.execute(
                "INSERT INTO chat.show_profiles (name, code_re, label, config, sort_order, updated_by) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (p["name"], p.get("code_re"), p.get("label"), json.dumps(to_config(p)),
                 (i + 1) * 10, "seed"),
            )
            added += 1
        conn.commit()
        print(f"Seeded {added} new default profile(s); {len(existing)} already present.")

        cur.execute("SELECT id, name, enabled, code_re, label, sort_order FROM chat.show_profiles ORDER BY sort_order, id")
        print("Current profiles:")
        for r in cur.fetchall():
            print("  ", dict(zip(("id", "name", "enabled", "code_re", "label", "sort_order"), r)))


if __name__ == "__main__":
    main()
