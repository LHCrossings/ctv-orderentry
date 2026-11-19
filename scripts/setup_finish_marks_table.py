"""One-time, idempotent setup for the Fill & Finish "done by hand" marks (Ashe 11/19).

Creates the empty table `dbo.CTV_FinishHandMarks` in the shared Etere SQL DB. Safe to
re-run from any environment: it creates the table only when it is not there and never
touches existing rows.

    uv run python scripts/setup_finish_marks_table.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect  # noqa: E402
from src.business_logic.services.finish_marks import DDL, TABLE, table_exists  # noqa: E402


def main():
    with connect() as conn:
        cur = conn.cursor()
        was_there = table_exists(cur)
        cur.execute(DDL)
        conn.commit()
    with connect() as fresh:  # read back from a new connection, never from the writer
        cur = fresh.cursor()
        assert table_exists(cur), f"{TABLE} is not there after the create"
        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
        n = cur.fetchone()[0]
    print(f"{TABLE}: {'already there' if was_there else 'created'}, {n} row(s)")


if __name__ == "__main__":
    main()
