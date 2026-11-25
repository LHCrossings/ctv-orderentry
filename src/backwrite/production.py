"""Production backwrite: the Sales Confirmation workbook for production charges.

An airtime backwrite never carries production (Lee 2026-11-25), so production
gets its own workbook, same three tabs. One charge = one line on the Sales
Confirmation (1 x the amount) and one Run Sheet row. Lee's rules:

* the row is dated the 20th of the month in which the charge bills;
* Type ``PRD``, Program ``BILLING LINE``, no times, no length;
* Bill Code ``<bill code> PRODUCTION``, Estimate ``PRODUCTION CHARGES``;
* Revenue Type is always ``Branded Content``;
* Agency / Non-Agency comes from the contract's agency in Etere (ANAGRAF),
  never the legacy word "Direct";
* billing type (Broadcast / Calendar) comes from the contract, and a contract
  with none set refuses.

Charges are read from ``CONTRATTISPESE`` when the contract has them; a contract
entered without its production (RWNY 3204) takes charges typed on the page.
Nothing here writes to Etere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from .transformer import CsvHeader, SpotRow, generate_excel

ESTIMATE_LABEL = "PRODUCTION CHARGES"
REVENUE_TYPE = "Branded Content"
BILL_DAY = 20

# COD_USER -> the market code the Run Sheet carries.
MARKETS = {
    1: "NYC",
    2: "CMP",
    3: "HOU",
    4: "SFO",
    5: "SEA",
    6: "LAX",
    7: "CVC",
    8: "WDC",
    9: "MMT",
    10: "DAL",
}


def bill_date(d: date) -> date:
    """The 20th of the month ``d`` falls in."""
    return date(d.year, d.month, BILL_DAY)


@dataclass
class ProductionCharge:
    bill_date: date
    description: str
    amount: float
    market: str
    line_id: int = 0  # the Etere line the charge rides on; 0 = typed by hand


def _d(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def load_contract(ref: str, today: Optional[date] = None) -> dict:
    """Everything the page shows for one contract (Etere ID or contract code).

    Raises ``ValueError`` when the contract is not found or carries no billing type.
    """
    from browser_automation.etere_direct_client import connect

    today = today or date.today()
    ref = str(ref or "").strip()
    if not ref:
        raise ValueError("Enter a contract number or code.")
    with connect() as conn:
        cur = conn.cursor()
        where = "ct.ID_CONTRATTITESTATA = %s" if ref.isdigit() else "RTRIM(ct.COD_CONTRATTO) = %s"
        cur.execute(
            f"""
            SELECT ct.ID_CONTRATTITESTATA, RTRIM(ct.COD_CONTRATTO), RTRIM(ISNULL(ct.DESCRIZIONE, '')),
                   ct.CENTROMEDIA, ISNULL(ct.P_AGENZIA, 0), ISNULL(ct.AGENZIA, 0),
                   RTRIM(ISNULL(ag.RAG_SOCIAL, '')), RTRIM(ISNULL(cl.RAG_SOCIAL, '')),
                   LTRIM(RTRIM(CASE WHEN ae.Nome IS NOT NULL AND ae.Nome != ''
                        THEN ae.Nome + ' ' + ISNULL(ae.RAG_SOCIAL, '')
                        ELSE ISNULL(ae.RAG_SOCIAL, '') END)),
                   LTRIM(RTRIM(ISNULL(bt.VIA, ''))), LTRIM(RTRIM(ISNULL(bt.CITTA, ''))),
                   LTRIM(RTRIM(ISNULL(bt.PROVINCIA, ''))), LTRIM(RTRIM(ISNULL(bt.CAP, ''))),
                   LTRIM(RTRIM(ISNULL(bt.TELEFONO, ''))), LTRIM(RTRIM(ISNULL(bt.FAX, ''))),
                   LTRIM(RTRIM(ISNULL(bt.E_MAIL, ''))),
                   LTRIM(RTRIM(ISNULL(bt.Nome, '') + ' ' + ISNULL(bt.NomeDue, '')))
            FROM CONTRATTITESTATA ct
            LEFT JOIN ANAGRAF ag ON ag.ID_ANAGRAF = NULLIF(ct.AGENZIA, 0)
            LEFT JOIN ANAGRAF cl ON cl.ID_ANAGRAF = ct.COMMITTENTE
            LEFT JOIN ANAGRAF ae ON ae.ID_ANAGRAF = ct.AGENTE1
            LEFT JOIN ANAGRAF bt ON bt.ID_ANAGRAF = COALESCE(NULLIF(ct.AGENZIA, 0), ct.COMMITTENTE)
            WHERE {where}
            ORDER BY ct.ID_CONTRATTITESTATA DESC
            """,
            (int(ref) if ref.isdigit() else ref,),
        )
        r = cur.fetchone()
        if not r:
            raise ValueError(f"Contract {ref!r} not found in Etere.")
        cid = int(r[0])
        centromedia = int(r[3] or 0)
        billing = {316: "Broadcast", 317: "Calendar"}.get(centromedia)
        if not billing:
            raise ValueError(
                f"Contract {r[1]} (ID {cid}) has no billing type set in Etere. "
                "Set Broadcast or Calendar on the contract, then retry."
            )
        p_ag = float(r[4] or 0)
        has_agency = int(r[5] or 0) > 0

        # Markets in the order Etere lists the lines. A typed charge defaults to the
        # FIRST one (Lee 2026-11-25: one market -> that market; several -> the first).
        cur.execute(
            "SELECT COD_USER, MIN(ID_CONTRATTIRIGHE) FROM CONTRATTIRIGHE "
            "WHERE ID_CONTRATTITESTATA = %s GROUP BY COD_USER ORDER BY 2",
            (cid,),
        )
        markets = [m for m in (MARKETS.get(int(x[0] or 0), "") for x in cur.fetchall()) if m]

        cur.execute(
            """
            SELECT s.ID_CONTRATTISPESE, cr.ID_CONTRATTIRIGHE, RTRIM(ISNULL(s.DESCRIZIONE, '')),
                   COALESCE(s.DATA, cr.DATA_INIZIO), s.IMPORTO, cr.COD_USER,
                   RTRIM(ISNULL(cr.DESCRIZIONE, ''))
            FROM CONTRATTISPESE s
            JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE = s.ID_CONTRATTIRIGHE
            WHERE cr.ID_CONTRATTITESTATA = %s
            ORDER BY COALESCE(s.DATA, cr.DATA_INIZIO), s.ID_CONTRATTISPESE
            """,
            (cid,),
        )
        charges = []
        for c in cur.fetchall():
            cd = _d(c[3]) or today
            charges.append(
                {
                    "charge_id": int(c[0]),
                    "line_id": int(c[1]),
                    "description": c[2] or "Production",
                    "etere_date": cd.isoformat(),
                    "bill_date": bill_date(cd).isoformat(),
                    "amount": float(c[4] or 0),
                    "market": MARKETS.get(int(c[5] or 0), ""),
                    "line_description": c[6],
                }
            )

    return {
        "contract_id": cid,
        "code": r[1] or "",
        "description": r[2] or "",
        "billing_type": billing,
        "agency_flag": "Agency" if has_agency else "Non-Agency",
        "agency_fee": p_ag / 100 if p_ag > 1 else p_ag,
        "agency": r[6] if has_agency else "",
        "client": r[7] or "",
        "sales_person": r[8] or "",
        "contact": {
            "address": r[9],
            "city": r[10],
            "state": r[11],
            "zip": r[12],
            "phone": r[13],
            "fax": r[14],
            "email_1": r[15],
            "contact_person": r[16],
        },
        "markets": markets,
        "charges": charges,
        "default_bill_date": bill_date(today).isoformat(),
        "estimate": ESTIMATE_LABEL,
        "revenue_type": REVENUE_TYPE,
    }


def output_name(code: str, first_bill: date) -> str:
    """``RWNY 2610`` billed in November -> ``RWNY 2611 PRODUCTION.xlsx``.

    A code that ends in a yymm within a year of the billing month takes the
    BILLING month; any other trailing number (an estimate, 4828) is kept.
    """
    base = code.strip()
    m = re.search(r"(?<!\d)(\d{2})(\d{2})$", base)
    if m and 1 <= int(m.group(2)) <= 12:
        months = (
            (2000 + int(m.group(1)) - first_bill.year) * 12 + int(m.group(2)) - first_bill.month
        )
        if abs(months) <= 12:
            base = base[: m.start()] + f"{first_bill.year % 100:02d}{first_bill.month:02d}"
    return re.sub(r'[\\/:*?"<>|]', "", f"{base} PRODUCTION").strip() + ".xlsx"


def build_workbook(
    contract: dict,
    charges: List[ProductionCharge],
    amounts_are_gross: Optional[bool] = None,
    contact: Optional[dict] = None,
    validation_out: Optional[dict] = None,
) -> bytes:
    """The production workbook for ``contract`` (a ``load_contract`` dict).

    On an AGENCY contract the IO decides whether the production amount is gross
    (commission comes off it) or already net (Lee 2026-11-25), so the caller must
    say which: ``amounts_are_gross`` True or False. A non-agency amount is net.
    """
    is_agency = contract["agency_flag"] == "Agency"
    fee = float(contract.get("agency_fee") or 0.0)
    if is_agency and fee > 0 and amounts_are_gross is None:
        raise ValueError(
            "This is an agency contract: say whether the production amount is gross "
            "(commission comes off) or already net."
        )
    if not (is_agency and amounts_are_gross):
        fee = 0.0
    if not charges:
        raise ValueError("No production charges to backwrite.")
    for i, c in enumerate(charges, 1):
        if not c.description.strip():
            raise ValueError(f"Charge {i} has no description.")
        if not c.amount or c.amount <= 0:
            raise ValueError(f"Charge {i} ({c.description}) has no amount.")
        if c.market not in MARKETS.values():
            raise ValueError(f"Charge {i} ({c.description}) has no market.")

    header = CsvHeader(
        agency=contract.get("agency", ""),
        client=contract.get("client", ""),
        contract_code=contract.get("code", ""),
        description=contract.get("description", ""),
        order_date="",
        address="",
        city="",
        ae=contract.get("sales_person", ""),
    )
    rows = [
        SpotRow(
            contract_code=header.contract_code,
            client=header.client,
            line_id=i,
            priority=4,
            duration_s=0,
            flight_start=c.bill_date,
            time_from="00:00",
            time_to="00:00",
            gross_rate=float(c.amount),
            days_pattern="",
            market=c.market,
            air_date=c.bill_date,
            air_time="",
            copy_code="",
            row_description=c.description.strip(),
        )
        for i, c in enumerate(charges, 1)
    ]
    user_inputs = {
        "sales_person": contract.get("sales_person", ""),
        "billing_type": contract["billing_type"],
        "revenue_type": REVENUE_TYPE,
        "agency_flag": contract["agency_flag"],
        "agency_fee": fee,
        "estimate": ESTIMATE_LABEL,
        "estimate_run": "",
        "contract": str(contract.get("contract_id", "")),
        "affidavit": "Y",
        "lang": "",  # left blank (Lee 2026-11-25)
        "revision": "",
    }
    merged = dict(contract.get("contact") or {})
    merged.update({k: v for k, v in (contact or {}).items() if v is not None})
    user_inputs.update({k: str(v or "") for k, v in merged.items()})
    return generate_excel(header, rows, user_inputs, validation_out=validation_out, production=True)
