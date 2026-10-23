"""The aircheck spot query is built with Python `%` formatting and executed with NO
parameters, so a `%%` inside an interpolated fragment reaches SQL Server verbatim.
Since 22f5e781 (10/16) the upcoming-only filter lived in such a fragment and every
traffic assign with auto-aircheck reported `Incorrect syntax near '86400000'`
(Lee, 3189/3190, 2026-10-23). Pin: the rendered SQL carries exactly one modulo."""

import re
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from web.routes import airchecks  # noqa: E402


def _rendered_sql(**kwargs) -> str:
    captured = {}

    class _Cur:
        def execute(self, sql):
            captured["sql"] = sql

        def fetchall(self):
            return []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self, as_dict=False):
            return _Cur()

    with mock.patch("browser_automation.etere_direct_client.connect", lambda: _Conn()):
        airchecks._fetch_etere_spots(3189, **kwargs)
    return captured["sql"]


@pytest.mark.parametrize("kwargs", [{}, {"first_only": False}])
def test_upcoming_filter_has_a_single_percent_modulo(kwargs):
    sql = _rendered_sql(**kwargs)
    assert "%%" not in sql, "a doubled percent reaches SQL Server verbatim (no params)"
    assert re.search(r"\)\s*%\s*86400000\s*,", sql), sql
    assert "ID_CONTRATTITESTATA = 3189" in sql


def test_history_path_has_no_upcoming_filter():
    sql = _rendered_sql(upcoming_only=False, first_only=False)
    assert "86400000" not in sql and "WHERE 1 = 1" in sql
