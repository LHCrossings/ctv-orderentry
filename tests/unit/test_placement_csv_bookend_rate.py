"""A bookend line's placed rows bill HALF the line rate each.

Imprenta PGE WS 26Q4 (contract 3185, 2026-10-20): entry stores a bookend line
as N pairs at the pair rate (16 x $117.64) and Etere places two rows per pair
(32 rows). The placement CSV billed every row at $117.64, so the run sheet read
$7,528.96 against the order's $3,764.48 - exactly double. Etere's own daily
revenue is N x IMPORTO; Lee's billed PGE tabs carry each aired half at
IMPORTO/2 (WSQ3 $58.82, TSQ3 $29.41).
"""

from decimal import Decimal

from backwrite.eterebridge_runner import placed_row_rate


def test_bookend_row_bills_half_the_pair_rate():
    assert placed_row_rate(Decimal("117.6400"), True, True) == Decimal("58.82")


def test_plain_rotation_line_keeps_its_rate():
    assert placed_row_rate(Decimal("58.8200"), False, False) == Decimal("58.8200")


def test_top_only_billboard_and_bottom_only_keep_their_rate():
    assert placed_row_rate(Decimal("40"), True, False) == Decimal("40")
    assert placed_row_rate(Decimal("40"), False, True) == Decimal("40")


def test_bonus_bookend_stays_zero():
    assert placed_row_rate(Decimal("0"), True, True) == 0
    assert placed_row_rate(None, True, True) == 0


def test_sql_reads_both_bookend_flags():
    import inspect

    from backwrite import eterebridge_runner as m

    src = inspect.getsource(m.build_placement_csv_from_db)
    assert "CONTROLLACAPOFILA" in src and "CONTROLLAFINEFILA" in src
    assert "placed_row_rate(" in src
