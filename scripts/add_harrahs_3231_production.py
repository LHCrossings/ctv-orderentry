"""Add the $1,200 November production charge to Harrah's Northern California (contract 3231).

Lee, 2026-10-31: "The 3231 order needs to have $1200 production for November added to it."

The contract entered 10/30 as 54 airtime lines (11/2/26-4/25/27, $15,360) with no charges. The
production money joins it the way every mixed airtime + production order carries it (oracles:
SacRT 2945 charge 205, Vida 3172, Snoqualmie 3177, Ntooitive 3179, ACM 3223 charge 234):

  * CONTRATTISPESE: one 'Production' row, $1,200.00, on the FIRST PAID NOVEMBER line
    (87358, M-F 7p-9p Chinese News/Talk 11/2-11/29), DATA = that line's flight start 11/2/2026
    (inside broadcast November; the header is Broadcast 316), PRINTDETAIL=1 so the proposals-app
    Production box shows it;
  * header LISTINO / LISTINOORIGINALE / SCONTATO 15,360 -> 16,560 (every production oracle
    header carries lines + charges).

Nothing else moves: no line, no placement, no ContrattiImportiGiornalieri row (the Production
box counts as revenue nowhere; see memory production-charges).

Dry run by default (ROLLBACK); --apply commits and re-verifies from a fresh connection.
A restore .sql is written to logs/ before any write.
"""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_automation.etere_direct_client import connect  # noqa: E402

CT = 3231
CARRIER_LINE = 87358  # first paid November line: M-F 7p-9p Chinese News/Talk, 11/2-11/29
CHARGE_DATE = date(2026, 11, 2)  # the carrier line's flight start
PRODUCTION = 1200.0
AIRTIME_TOTAL = 15360.0
NEW_HEADER_TOTAL = AIRTIME_TOTAL + PRODUCTION
LOGS = Path(__file__).resolve().parents[1] / "logs"


def main(apply: bool):
    conn = connect()
    cur = conn.cursor()

    # ── preconditions: the carrier line is what the docstring says, and no charge exists yet ──
    cur.execute(
        "SELECT ID_CONTRATTITESTATA, DESCRIZIONE, ID_BOOKINGCODE, IMPORTO, N_PASSAGGI, DATA_INIZIO, DATA_FINE, COD_USER "
        "FROM CONTRATTIRIGHE WHERE ID_CONTRATTIRIGHE=%s",
        (CARRIER_LINE,),
    )
    line = cur.fetchone()
    assert line is not None, f"line {CARRIER_LINE} not found"
    ct, desc, booking, importo, n, d0, d1, cod_user = line
    assert ct == CT, f"line {CARRIER_LINE} belongs to contract {ct}, not {CT}"
    assert booking == 2 and float(importo) > 0, f"line {CARRIER_LINE} is not a paid line: {line}"
    assert d0 == datetime(2026, 11, 2) and d1 == datetime(2026, 11, 29), (
        f"unexpected dates on {CARRIER_LINE}: {d0}..{d1}"
    )
    assert cod_user == 7, f"carrier line station {cod_user} is not CVC (7)"
    cur.execute(
        "SELECT MIN(ID_CONTRATTIRIGHE) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA=%s AND ID_BOOKINGCODE=2 AND DATA_INIZIO=%s",
        (CT, CHARGE_DATE),
    )
    assert cur.fetchone()[0] == CARRIER_LINE, "carrier is not the first paid November line"

    cur.execute(
        "SELECT COUNT(*) FROM CONTRATTISPESE WHERE ID_CONTRATTIRIGHE IN "
        "(SELECT ID_CONTRATTIRIGHE FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA=%s)",
        (CT,),
    )
    assert cur.fetchone()[0] == 0, (
        "contract 3231 already carries a charge; refusing to add a second"
    )

    cur.execute(
        "SELECT LISTINO, LISTINOORIGINALE, SCONTATO, CENTROMEDIA FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA=%s",
        (CT,),
    )
    listino, listino_orig, scontato, centromedia = cur.fetchone()
    assert float(listino) == float(listino_orig) == float(scontato) == AIRTIME_TOTAL, (
        f"header totals are not the airtime total: {listino} / {listino_orig} / {scontato}"
    )
    assert centromedia == 316, f"header is not Broadcast (316): {centromedia}"
    cur.execute(
        "SELECT SUM(N_PASSAGGI*IMPORTO) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA=%s", (CT,)
    )
    assert float(cur.fetchone()[0]) == AIRTIME_TOTAL
    print(
        f"[PRE] carrier line {CARRIER_LINE} '{desc}' {n} x ${float(importo):,.2f}, {d0:%m/%d/%Y}-{d1:%m/%d/%Y}, CVC; no charges; header ${float(listino):,.2f}"
    )

    # ── restore file ──
    LOGS.mkdir(exist_ok=True)
    restore = LOGS / f"harrahs-3231-production-restore-{date.today():%Y%m%d}.sql"
    restore.write_text(
        "-- Restore: undo the $1,200 November production charge on Harrah's NoCal 3231 (2026-10-31)\n"
        f"DELETE FROM CONTRATTISPESE WHERE ID_CONTRATTIRIGHE={CARRIER_LINE} AND DESCRIZIONE='Production' AND IMPORTO={PRODUCTION:.2f};\n"
        f"UPDATE CONTRATTITESTATA SET LISTINO={float(listino):.4f}, LISTINOORIGINALE={float(listino_orig):.4f}, "
        f"SCONTATO={float(scontato):.4f} WHERE ID_CONTRATTITESTATA={CT};\n"
    )
    print(f"[RESTORE] {restore}")

    # ── writes ──
    cur.execute(
        "INSERT INTO CONTRATTISPESE (ID_CONTRATTIRIGHE, DESCRIZIONE, DATA, IMPORTO, VALUTA, "
        "ID_FATTURAEMITTENTE, ID_FATTURAAGENZIA, PRINTDETAIL, ID_CPEMITTENTE, ID_CPAGENZIA) "
        "VALUES (%s, 'Production', %s, %s, '', 0, 0, 1, 0, 0)",
        (CARRIER_LINE, CHARGE_DATE, PRODUCTION),
    )
    assert cur.rowcount == 1
    print(
        f"[CHARGE] Production ${PRODUCTION:,.2f} on line {CARRIER_LINE} dated {CHARGE_DATE:%m/%d/%Y}"
    )

    cur.execute(
        "UPDATE CONTRATTITESTATA SET LISTINO=%s, LISTINOORIGINALE=%s, SCONTATO=%s WHERE ID_CONTRATTITESTATA=%s",
        (NEW_HEADER_TOTAL, NEW_HEADER_TOTAL, NEW_HEADER_TOTAL, CT),
    )
    assert cur.rowcount == 1
    print(f"[HEADER] total ${AIRTIME_TOTAL:,.2f} -> ${NEW_HEADER_TOTAL:,.2f}")

    verify(cur, "in-txn")
    if apply:
        conn.commit()
        print("[COMMIT] committed")
    else:
        conn.rollback()
        print("[DRY RUN] rolled back — rerun with --apply")
    conn.close()
    if apply:
        c2 = connect()
        verify(c2.cursor(), "fresh connection")
        c2.close()


def verify(cur, label):
    cur.execute(
        "SELECT ID_CONTRATTIRIGHE, DESCRIZIONE, DATA, IMPORTO, CAST(PRINTDETAIL AS int), VALUTA, "
        "ID_FATTURAEMITTENTE, ID_FATTURAAGENZIA, ID_CPEMITTENTE, ID_CPAGENZIA FROM CONTRATTISPESE "
        "WHERE ID_CONTRATTIRIGHE IN (SELECT ID_CONTRATTIRIGHE FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA=%s)",
        (CT,),
    )
    charges = cur.fetchall()
    assert len(charges) == 1, charges
    c = charges[0]
    assert (
        c[0] == CARRIER_LINE
        and c[1] == "Production"
        and c[2] == datetime(2026, 11, 2)
        and float(c[3]) == PRODUCTION
    )
    assert c[4] == 1 and c[5] == "" and c[6:] == (0, 0, 0, 0), c

    cur.execute(
        "SELECT LISTINO, LISTINOORIGINALE, SCONTATO, DATA_INIZIO, DATA_TERMINE FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA=%s",
        (CT,),
    )
    h = cur.fetchone()
    assert float(h[0]) == float(h[1]) == float(h[2]) == NEW_HEADER_TOTAL, h
    assert h[3] == datetime(2026, 11, 2) and h[4] == datetime(2027, 4, 25), h

    cur.execute(
        "SELECT COUNT(*), SUM(N_PASSAGGI), SUM(N_PASSAGGI*IMPORTO) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA=%s",
        (CT,),
    )
    n_lines, spots, money = cur.fetchone()
    assert (n_lines, int(spots), float(money)) == (54, 708, AIRTIME_TOTAL), (n_lines, spots, money)
    cur.execute(
        "SELECT COUNT(*), ISNULL(SUM(IMPORTO),0) FROM ContrattiImportiGiornalieri WHERE ID_ContrattiRighe IN "
        "(SELECT ID_CONTRATTIRIGHE FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA=%s)",
        (CT,),
    )
    cig_n, cig_sum = cur.fetchone()
    assert float(cig_sum) == AIRTIME_TOTAL, cig_sum
    print(
        f"[VERIFY {label}] charge {c[1]} ${float(c[3]):,.2f} on {c[0]} dated {c[2]:%m/%d/%Y} print={c[4]}; "
        f"header ${float(h[0]):,.2f}; lines {n_lines} / {int(spots)} spots / ${float(money):,.2f}; "
        f"CIG {cig_n} rows ${float(cig_sum):,.2f} (unchanged)"
    )


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
