"""
EtereDirectClient — writes contracts and lines directly to Etere's SQL Server
via stored procedures, bypassing Selenium browser automation entirely.

Must run on Windows (or a machine that can use Windows Auth against the SQL
Server).  Accepts a live pyodbc.Connection so the caller controls connection
lifecycle.

Usage (Windows, from the project root):
    import pyodbc
    from browser_automation.etere_direct_client import EtereDirectClient, connect

    with connect() as conn:
        client = EtereDirectClient(conn, owner="Charmaine Lane")
        client.set_master_market("NYC")
        contract_id = client.create_contract_header(
            code="RPM TVC 10907 SF",
            description="Thunder Valley Casino Est 10907 SFO",
            customer_id=68,
            agency_id=AGENCY_IDS["RPM"],
            media_center_id=MEDIA_CENTER_IDS["RPM"],
        )
        client.add_contract_line(
            market="SFO",
            days="M-F",
            time_range="06:00-07:00",
            description="M-F Mandarin News 6a-7a",
            rate=120.0,
            total_spots=4,
            spots_per_week=2,
            max_daily_run=1,
            date_from=date(2026, 6, 1),
            date_to=date(2026, 6, 7),
        )
"""

from __future__ import annotations

import re
from datetime import date, datetime
from math import ceil
from typing import Optional

import os

import pyodbc  # noqa: F401 — caller imports this module for type hints
try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env from project root (or CWD) if present
except ImportError:
    pass  # python-dotenv not installed; connect() falls back to Windows Auth

# ── Connection ──────────────────────────────────────────────────────────────────

DB_SERVER   = "etere-sql-server.tail98be.ts.net"
DB_DATABASE = "Etere_crossing"
DB_DRIVER   = "{SQL Server}"
ETERE_WEB_URL = "http://100.102.206.113"


def connect() -> pyodbc.Connection:
    """Return a new pyodbc connection.

    Credentials are read from environment variables (set in .env):
        ETERE_DB_USER     — SQL Server login name
        ETERE_DB_PASSWORD — SQL Server login password

    If neither is set, falls back to Windows Authentication (Trusted_Connection).
    """
    user = os.getenv("ETERE_DB_USER")
    password = os.getenv("ETERE_DB_PASSWORD")

    if user and password:
        return pyodbc.connect(
            f"DRIVER={DB_DRIVER};"
            f"SERVER={DB_SERVER};"
            f"DATABASE={DB_DATABASE};"
            f"UID={user};"
            f"PWD={password};"
        )
    return pyodbc.connect(
        f"DRIVER={DB_DRIVER};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        "Trusted_Connection=yes;"
    )


def etere_web_login() -> dict:
    """Log into the Etere web UI headlessly using requests.

    Reads credentials from credentials.env (same as Selenium login).
    Returns a cookies dict ready to pass to EtereDirectClient.set_session_cookies().

    Raises RuntimeError if login fails or no cookies are returned.
    """
    try:
        import requests as _req
    except ImportError:
        raise RuntimeError("requests package not installed — run: pip install requests")

    import sys
    import os
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from credential_loader import load_credentials

    username, password = load_credentials()
    login_url = f"{ETERE_WEB_URL}/index/login"

    session = _req.Session()

    # GET the login page first — picks up any pre-auth cookies
    resp = session.get(login_url, timeout=15)
    resp.raise_for_status()

    # POST credentials — AJAX form, field names are Login.UserName / Login.Password
    resp = session.post(
        login_url,
        data={
            "Login.UserName": username,
            "Login.Password": password,
            "Login.Domain":   "ctvetere.local",
        },
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": login_url,
        },
        timeout=15,
        allow_redirects=True,
    )
    resp.raise_for_status()

    # Navigate to the main app pages to accumulate all session cookies
    # (Etere sets additional cookies when loading the sales module)
    for warmup_path in ("/etere/etere.html", "/sales/index"):
        try:
            session.get(f"{ETERE_WEB_URL}{warmup_path}", timeout=15)
        except Exception:
            pass  # best-effort; don't fail if a page 404s

    cookies = dict(session.cookies)
    if not cookies:
        raise RuntimeError(
            "Etere login returned no cookies — credentials may be wrong "
            "or the login endpoint has changed."
        )

    print(f"[LOGIN] OK - Logged into Etere as {username} ({len(cookies)} cookie(s))")
    return cookies


# ── Constants ───────────────────────────────────────────────────────────────────

FRAMES_PER_SECOND = 29.97  # NTSC broadcast frame rate

# Market code -> Etere Users.cod_user (also CONTRATTITESTATA.COD_USER)
MARKET_USER_IDS: dict[str, int] = {
    "NYC": 1,
    "CMP": 2,
    "HOU": 3,
    "SFO": 4,
    "SEA": 5,
    "LAX": 6,
    "CVC": 7,
    "WDC": 8,
    "MMT": 9,
    "DAL": 10,
}

# Agency (AGENZIA) IDs
AGENCY_IDS: dict[str, int] = {
    "RPM":      67,
    "HL":        8,
    "IMPRENTA": 76,
    "IMPACT":  251,
    "SAGENT":   69,
}

# Media center (CENTROMEDIA) IDs
MEDIA_CENTER_IDS: dict[str, int] = {
    "RPM":      316,
    "HL":       316,
    "IMPRENTA": 316,
    "IMPACT":     0,
    "SAGENT":   316,
}

# NEWTYPE values for paid vs bonus lines
NEWTYPE_PAID  = "BART;COMS;AV;BB;BNS;BOOK;COM;ID;INT;PER;PSA"
NEWTYPE_BONUS = "BNS"

# Default Nielsen target ID (Adults 35-64)
DEFAULT_NIELSEN_ID   = 728
DEFAULT_NIELSEN_CODE = "0001"


# ── Frame-conversion helpers ────────────────────────────────────────────────────

def _to_frames(h: int, m: int = 0, s: int = 0) -> int:
    """Convert HH:MM:SS to Etere frame count (29.97 fps)."""
    return round((h * 3600 + m * 60 + s) * FRAMES_PER_SECOND)


def _seconds_to_frames(seconds: int) -> int:
    return round(seconds * FRAMES_PER_SECOND)


def _minutes_to_frames(minutes: int) -> int:
    return round(minutes * 60 * FRAMES_PER_SECOND)


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' -> (h, m)."""
    h, m = time_str.strip().split(":")
    return int(h), int(m)


def _frames_to_hhmm(frames: int) -> str:
    """Convert Etere SMPTE frame count back to 'HH:MM' string."""
    total_seconds = round(frames / FRAMES_PER_SECOND)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"


def _duration_str_to_seconds(duration: str) -> int:
    """
    Convert Etere duration string to integer seconds.
    Accepts:
        "00:00:30:00"  (HH:MM:SS:FF)  -> 30
        "30"           (bare seconds)  -> 30
    """
    if ":" in duration:
        parts = duration.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(duration)


# ── Day-pattern parser ──────────────────────────────────────────────────────────

_DAY_KEYS = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")  # Mon-Sun

# Tokens recognised as individual days
_TOKEN_MAP: dict[str, str] = {
    "M":   "lun",
    "MO":  "lun",
    "MON": "lun",
    "TU":  "mar",
    "TUE": "mar",
    "W":   "mer",
    "WED": "mer",
    "TH":  "gio",
    "THU": "gio",
    "R":   "gio",
    "F":   "ven",
    "FRI": "ven",
    "SA":  "sab",
    "SAT": "sab",
    "SU":  "dom",
    "SUN": "dom",
}

# Ordered list used for range expansion "M-F", "M-Su", "Sa-Su"
_DAY_ORDER = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
_TOKEN_TO_INDEX: dict[str, int] = {
    "M": 0, "MO": 0, "MON": 0,
    "TU": 1, "TUE": 1,
    "W": 2, "WED": 2,
    "TH": 3, "THU": 3, "R": 3,
    "F": 4, "FRI": 4,
    "SA": 5, "SAT": 5,
    "SU": 6, "SUN": 6,
}


def parse_day_bits(days: str) -> dict[str, bool]:
    """
    Convert a day-pattern string to a dict of Italian day keys (lun…dom).

    Accepts:
        "M-F", "M-Su", "M-Sa", "Sa-Su"     — range
        "M,W,F", "TU,TH"                    — comma list
        "SAT", "SUN"                         — single day
    Returns:
        {"lun": True, "mar": True, …}
    """
    result = {k: False for k in _DAY_KEYS}
    raw = days.strip().upper()

    # Normalise separators between tokens
    raw = re.sub(r"\s*,\s*", ",", raw)

    # Range:  TOKEN-TOKEN
    range_m = re.fullmatch(r"([A-Z]+)-([A-Z]+)", raw)
    if range_m:
        start = _TOKEN_TO_INDEX.get(range_m.group(1))
        end   = _TOKEN_TO_INDEX.get(range_m.group(2))
        if start is not None and end is not None:
            for i in range(start, end + 1):
                result[_DAY_ORDER[i]] = True
            return result

    # Comma list:  TOKEN,TOKEN,…
    if "," in raw:
        for tok in raw.split(","):
            key = _TOKEN_MAP.get(tok.strip())
            if key:
                result[key] = True
        return result

    # Single token
    key = _TOKEN_MAP.get(raw)
    if key:
        result[key] = True
        return result

    print(f"[DIRECT] Warning: unrecognised day pattern '{days}' — no days set")
    return result


# ── EtereDirectClient ───────────────────────────────────────────────────────────

class EtereDirectClient:
    """
    Writes Etere contracts and lines via stored procedures over pyodbc.

    Interface mirrors EtereClient (Selenium) so agency automation files can
    be switched by changing one import and the constructor call.
    """

    def __init__(self, conn: pyodbc.Connection, owner: str = "Charmaine Lane",
                 autocommit: bool = True):
        self._conn = conn
        self.owner = owner
        self._autocommit = autocommit
        self._master_market = "NYC"
        self._contract_id: Optional[int] = None
        self._session_cookies: dict = {}   # populated via set_session_cookies()

    def set_session_cookies(self, cookies: dict) -> None:
        """Pass browser session cookies so _assign_blocks_http can authenticate."""
        self._session_cookies = cookies

    # ── Market ──────────────────────────────────────────────────────────────────

    def set_master_market(self, market: str) -> None:
        self._master_market = market

    # ── Contract header ─────────────────────────────────────────────────────────

    def create_contract_header(
        self,
        code: str,
        description: str,
        customer_id: int,
        agency_id: int,
        agency_pct: float = 15.0,
        agent_id: int = 11,
        media_center_id: int = 316,
        contract_date: Optional[date] = None,
        contract_end_date: Optional[date] = None,
        contract_type: int = 2,
        invoice_mode: int = 2,
        invoice_header: int = 1,
        vat: int = 1,
        payment_id: int = 1,
        note: str = "",
        customer_order_ref: str = "",
    ) -> int:
        """
        Create a contract header via web_sales_savecontractgeneral.
        Stores the returned ID internally for subsequent add_contract_line calls.
        Returns the new contract ID.
        """
        if contract_date is None:
            contract_date = date.today()

        user_id = MARKET_USER_IDS.get(self._master_market, 1)

        # Legacy {SQL Server} driver can't bind ? params inside a DECLARE batch.
        # Call the SP directly; retrieve the new ID by querying the table.
        # date objects must be cast to datetime for the legacy driver.
        contract_dt = datetime(contract_date.year, contract_date.month, contract_date.day)
        expire_dt = (
            datetime(contract_end_date.year, contract_end_date.month, contract_end_date.day)
            if contract_end_date else contract_dt
        )

        sql = """
EXEC web_sales_savecontractgeneral
    @idcontract           = ?,
    @idcustomer           = ?,
    @coduser              = ?,
    @contractType         = ?,
    @dateProposal         = ?,
    @codeProposal         = ?,
    @descProposal         = ?,
    @discount             = ?,
    @dateExpireProposal   = ?,
    @idAgent              = ?,
    @percAgentCommission  = ?,
    @note                 = ?,
    @owner                = ?,
    @idAgency             = ?,
    @idFinaluser          = ?,
    @idMediacenter        = ?,
    @vat                  = ?,
    @idPayment            = ?,
    @idAgent2             = ?,
    @percAgentCommission2 = ?,
    @idAgent3             = ?,
    @percAgentCommission3 = ?,
    @idAgent4             = ?,
    @percAgentCommission4 = ?,
    @idAgent5             = ?,
    @percAgentCommission5 = ?,
    @percAgency           = ?,
    @percMediaCenter      = ?,
    @invoicemode          = ?,
    @idbank               = ?,
    @scontoinco           = ?,
    @customercolor        = ?,
    @intestazione         = ?,
    @pagrate              = ?,
    @fattprepaga          = ?,
    @packageorder         = ?,
    @suborder             = ?,
    @suborderid           = ?,
    @approvalref          = ?,
    @customerorderref     = ?,
    @listino              = ?,
    @id                   = ?,
    @idanagraflink        = ?
"""
        params = [
            0,                # @idcontract         (0 = new)
            customer_id,      # @idcustomer
            user_id,          # @coduser
            contract_type,    # @contractType
            contract_dt,      # @dateProposal
            code,             # @codeProposal
            description,      # @descProposal
            0,                # @discount
            expire_dt,        # @dateExpireProposal
            agent_id,         # @idAgent
            0,                # @percAgentCommission
            note or "",       # @note  (NOT NULL)
            self.owner,       # @owner
            agency_id,        # @idAgency
            0,                # @idFinaluser
            media_center_id,  # @idMediacenter
            vat,              # @vat
            payment_id,       # @idPayment
            0,                # @idAgent2
            0,                # @percAgentCommission2
            0,                # @idAgent3
            0,                # @percAgentCommission3
            0,                # @idAgent4
            0,                # @percAgentCommission4
            0,                # @idAgent5
            0,                # @percAgentCommission5
            agency_pct,       # @percAgency
            0,                # @percMediaCenter
            invoice_mode,     # @invoicemode
            0,                # @idbank
            0,                # @scontoinco
            0,                # @customercolor
            invoice_header,   # @intestazione
            False,            # @pagrate
            False,            # @fattprepaga
            False,            # @packageorder
            False,            # @suborder
            0,                # @suborderid
            "",               # @approvalref
            customer_order_ref,  # @customerorderref
            0,                # @listino
            0,                # @id  (INOUT — SP sets it; we retrieve via SELECT)
            0,                # @idanagraflink
        ]

        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        if self._autocommit:
            self._conn.commit()

        # Retrieve the ID the SP just inserted
        cursor.execute(
            "SELECT ID_CONTRATTITESTATA FROM CONTRATTITESTATA "
            "WHERE COD_CONTRATTO = ?", [code]
        )
        row = cursor.fetchone()
        if not row:
            raise RuntimeError(f"Contract '{code}' not found after SP call")

        self._contract_id = row[0]
        print(f"[DIRECT] Created contract #{self._contract_id}: {code}")
        return self._contract_id

    # ── Contract line ────────────────────────────────────────────────────────────

    def add_contract_line(
        self,
        market: str,
        days: str,
        time_range: str,          # "HH:MM-HH:MM"
        description: str,
        rate: float,
        total_spots: int,
        spots_per_week: int,
        max_daily_run: Optional[int] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        duration: str = "00:00:30:00",
        is_bonus: bool = False,
        is_bookend: bool = False,
        separation_intervals: tuple[int, int, int] = (15, 0, 0),
        contract_id: Optional[int] = None,
        # Unused kwargs kept for interface compatibility with EtereClient
        **_kwargs,
    ) -> int:
        """
        Insert a contract line via web_sales_InsertContractLine.
        Returns the new line ID.
        """
        cid = contract_id or self._contract_id
        if not cid:
            raise ValueError("No contract ID — call create_contract_header first")

        if date_from is None or date_to is None:
            raise ValueError("date_from and date_to are required for direct DB entry")

        user_id = MARKET_USER_IDS.get(market, 1)

        # Times — normalize any format ("2PM-3PM", "14:00-15:00") to HH:MM
        from etere_client import EtereClient as _EC
        time_from_norm, time_to_norm = _EC.parse_time_range(time_range)
        start_h, start_m = _parse_hhmm(time_from_norm)
        end_h,   end_m   = _parse_hhmm(time_to_norm)
        start_frames = _to_frames(start_h, start_m)
        end_frames   = _to_frames(end_h, end_m)

        # Duration
        dur_seconds = _duration_str_to_seconds(duration)
        dur_frames  = _seconds_to_frames(dur_seconds)

        # Max daily run (auto-calculate if not supplied)
        if max_daily_run is None:
            day_bits = parse_day_bits(days)
            active_days = sum(day_bits.values())
            max_daily_run = ceil(spots_per_week / active_days) if active_days else spots_per_week
        else:
            day_bits = parse_day_bits(days)

        # Separation in frames
        intcomm  = _minutes_to_frames(separation_intervals[0])
        intevent = _minutes_to_frames(separation_intervals[1])
        intsrighe = _minutes_to_frames(separation_intervals[2])

        newtype = NEWTYPE_BONUS if is_bonus else NEWTYPE_PAID

        # Convert date -> datetime for legacy ODBC driver
        datefrom_dt = datetime(date_from.year, date_from.month, date_from.day)
        dateto_dt   = datetime(date_to.year,   date_to.month,   date_to.day)

        sql = """
EXEC web_sales_InsertContractLine
    @idproposal        = ?,
    @iddetails         = ?,
    @coduser           = ?,
    @datefrom          = ?,
    @dateto            = ?,
    @description       = ?,
    @duration          = ?,
    @starttime         = ?,
    @endtime           = ?,
    @newtype           = ?,
    @percCommission1   = ?,
    @totalschedule     = ?,
    @passaggisettimana = ?,
    @passaggigiorno    = ?,
    @controllocapo     = ?,
    @controllofine     = ?,
    @priorita          = ?,
    @prenotazione      = ?,
    @omaggio           = ?,
    @importo           = ?,
    @nielsen           = ?,
    @lun               = ?,
    @mar               = ?,
    @mer               = ?,
    @gio               = ?,
    @ven               = ?,
    @sab               = ?,
    @dom               = ?,
    @manualprice       = ?,
    @idbooking         = ?,
    @id                = ?,
    @priwhitelist      = ?,
    @rowstatus         = ?,
    @intcomm           = ?,
    @intsrighe         = ?,
    @intevent          = ?,
    @idnielsen         = ?,
    @idfatturadesc     = ?,
    @production        = ?,
    @dubbing           = ?,
    @productionLabel   = ?,
    @dubbingLabel      = ?,
    @uniquetb          = ?,
    @filler            = ?,
    @controlloNielsen  = ?,
    @paidFixed         = ?,
    @joinFiller        = ?,
    @hidefromscheduler = ?,
    @eventLevel        = ?,
    @viewgroup         = ?,
    @rowtype           = ?,
    @ignoraregole      = ?,
    @controllainserisci = ?,
    @controllamiddle   = ?,
    @split             = ?,
    @idpianoconti      = ?,
    @note              = ?,
    @linkedspotpos     = ?,
    @linkedspotid      = ?
"""
        params = [
            cid,                # @idproposal
            0,                  # @iddetails  (0 = new line)
            user_id,            # @coduser    (line's station/market)
            datefrom_dt,        # @datefrom
            dateto_dt,          # @dateto
            description,        # @description
            dur_frames,         # @duration
            start_frames,       # @starttime
            end_frames,         # @endtime
            newtype,            # @newtype
            0,                  # @percCommission1
            total_spots,        # @totalschedule
            spots_per_week,     # @passaggisettimana
            max_daily_run,      # @passaggigiorno
            is_bookend,         # @controllocapo  (top of break)
            is_bookend,         # @controllofine  (bottom of break)
            500,                # @priorita
            1,                  # @prenotazione
            False,              # @omaggio
            rate,               # @importo
            DEFAULT_NIELSEN_CODE,  # @nielsen
            day_bits["lun"],    # @lun
            day_bits["mar"],    # @mar
            day_bits["mer"],    # @mer
            day_bits["gio"],    # @gio
            day_bits["ven"],    # @ven
            day_bits["sab"],    # @sab
            day_bits["dom"],    # @dom
            True,               # @manualprice
            2,                  # @idbooking
            0,                  # @id (new line; SP returns the assigned ID)
            50,                 # @priwhitelist
            1,                  # @rowstatus
            intcomm,            # @intcomm
            intsrighe,          # @intsrighe
            intevent,           # @intevent
            DEFAULT_NIELSEN_ID, # @idnielsen
            0,                  # @idfatturadesc
            0,                  # @production
            0,                  # @dubbing
            "",                 # @productionLabel
            "",                 # @dubbingLabel
            False,              # @uniquetb
            False,              # @filler
            False,              # @controlloNielsen
            False,              # @paidFixed
            False,              # @joinFiller
            False,              # @hidefromscheduler
            0,                  # @eventLevel
            "",                 # @viewgroup
            0,                  # @rowtype
            False,              # @ignoraregole
            False,              # @controllainserisci
            False,              # @controllamiddle
            0,                  # @split
            0,                  # @idpianoconti
            "",                 # @note
            0,                  # @linkedspotpos
            0,                  # @linkedspotid
        ]

        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        if self._autocommit:
            self._conn.commit()

        # Retrieve the ID the SP just inserted
        cursor.execute(
            "SELECT MAX(ID_CONTRATTIRIGHE) FROM CONTRATTIRIGHE "
            "WHERE ID_CONTRATTITESTATA = ?", [cid]
        )
        row = cursor.fetchone()
        line_id = row[0] if row else 0

        # Assign available program blocks to this line
        if line_id:
            self._assign_blocks_http(
                contract_id=cid,
                user_id=user_id,
                start_frames=start_frames,
                end_frames=end_frames,
                day_bits=day_bits,
                date_from=datefrom_dt,
                date_to=dateto_dt,
                line_id=line_id,
            )

        label = "BNS" if is_bonus else "PAID"
        print(f"[DIRECT]   Line #{line_id} [{label}] {days} {time_range} | "
              f"{date_from}-{date_to} | {total_spots} spots @ ${rate:.2f}")
        return line_id

    def _assign_blocks(
        self,
        line_id: int,
        user_id: int,
        start_frames: int,
        end_frames: int,
        day_bits: dict,
        date_from,
        date_to,
    ) -> int:
        """
        Populate CONTRATTIFASCE for a contract line by finding matching
        trafficPalinse blocks (same market, date range, time window, day pattern).

        Algorithm (reverse-engineered from Etere's "Add Blocks Automatically"):
          - For each (id_fascia, Date) in the date range, compute MIN(offset).
          - Include id_fascia if MIN(offset) falls in [lb, ub) on at least one day.
          - lb = start_frames - PREROLL  (Etere stores program starts 9 frames
            before the hour boundary, e.g. 22h stored as 22*107892-9)
          - ub = end_frames - PREROLL    (exclusive upper bound)
          - For point-in-time lines (start == end): ub = lb + FPH (1-hour window)

        SQL Server DATEPART(dw): 1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu, 6=Fri, 7=Sat
        """
        _DAY_BITS_TO_DW = {
            "dom": 1,  # Sunday
            "lun": 2,  # Monday
            "mar": 3,  # Tuesday
            "mer": 4,  # Wednesday
            "gio": 5,  # Thursday
            "ven": 6,  # Friday
            "sab": 7,  # Saturday
        }
        active_dw = [dw for key, dw in _DAY_BITS_TO_DW.items() if day_bits.get(key)]
        if not active_dw:
            return 0

        PREROLL = 9          # frames before hour boundary
        FPH = round(29.97 * 3600)   # 107892 frames per hour
        lb = start_frames - PREROLL
        if start_frames == end_frames:
            ub = lb + FPH    # point-in-time: 1-hour window
        else:
            ub = end_frames - PREROLL

        dw_placeholders = ",".join("?" * len(active_dw))

        cursor = self._conn.cursor()

        # Clear existing blocks so duplicated-line inherited blocks don't persist
        cursor.execute(
            "DELETE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", [line_id]
        )
        deleted = cursor.rowcount
        if deleted:
            print(f"[DIRECT]     -> {deleted} existing block(s) cleared")

        cursor.execute(f"""
            INSERT INTO CONTRATTIFASCE (ID_CONTRATTIRIGHE, ID_FASCE, PRICELIST, SELECTEDSEGMENTS)
            SELECT DISTINCT ?, sub.id_fascia, '', ''
            FROM (
                SELECT tp.id_fascia
                FROM trafficPalinse tp
                WHERE tp.Cod_User = ?
                  AND tp.Date >= ? AND tp.Date <= ?
                  AND DATEPART(dw, tp.Date) IN ({dw_placeholders})
                GROUP BY tp.id_fascia, tp.Date
                HAVING MIN(tp.offset) >= ? AND MIN(tp.offset) < ?
            ) sub
        """, [line_id, user_id, date_from, date_to,
              *active_dw, lb, ub])

        count = cursor.rowcount
        if self._autocommit:
            self._conn.commit()
        if count:
            print(f"[DIRECT]     -> {count} block(s) assigned")
        return count

    def _assign_blocks_http(
        self,
        contract_id: int,
        user_id: int,
        start_frames: int,
        end_frames: int,
        day_bits: dict,
        date_from,
        date_to,
        line_id: int = 0,
    ) -> int:
        """
        Assign program blocks to a contract line via Etere's web API.

        Calls POST /sales/getautomaticcontractlineblockstable to get the list of
        blocks Etere's server would assign (using its own matching logic), then
        writes those blocks to CONTRATTIFASCE directly.

        This hybrid approach is more accurate than the reverse-engineered SQL
        method because Etere's server-side algorithm selects the blocks, while
        our direct DB write avoids a separate "save" HTTP call.

        Returns number of blocks assigned, or -1 on HTTP error.
        Note: the typo "ToFimeProghhmm" (not "ToTimeProghhmm") is intentional —
        it matches the actual Etere API parameter name.
        """
        import json as _json
        import re as _re
        try:
            import requests as _requests
        except ImportError:
            print("[DIRECT]     ! requests not installed — skipping HTTP block assignment")
            return -1

        if not self._session_cookies:
            print("[DIRECT]     ! No session cookies — call set_session_cookies() first")
            return -1

        time_from = _frames_to_hhmm(start_frames)
        time_to   = _frames_to_hhmm(end_frames)

        # Dates to M/D/YYYY (Etere web API format)
        fd = date_from if hasattr(date_from, 'month') else date_from
        td = date_to   if hasattr(date_to,   'month') else date_to
        from_str = f"{fd.month}/{fd.day}/{fd.year}"
        to_str   = f"{td.month}/{td.day}/{td.year}"

        payload = {"f": {
            "IdContract":       contract_id,
            "FromDate":         from_str,
            "ToDate":           to_str,
            "CodUser":          str(user_id),
            "FromTimeProghhmm": time_from,
            "ToFimeProghhmm":   time_to,   # typo is intentional — matches Etere API
            "Mon": day_bits.get("lun", False),
            "Tue": day_bits.get("mar", False),
            "Wed": day_bits.get("mer", False),
            "Thu": day_bits.get("gio", False),
            "Fri": day_bits.get("ven", False),
            "Sat": day_bits.get("sab", False),
            "Sun": day_bits.get("dom", False),
        }}

        url = f"{ETERE_WEB_URL}/sales/getautomaticcontractlineblockstable"
        print(f"[DIRECT]     Payload: {payload}")
        try:
            resp = _requests.post(url, json=payload, cookies=self._session_cookies, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[DIRECT]     X Block assignment HTTP error: {exc}")
            return -1

        # Response is a JSON envelope: {"Result": {...}, "Value": "<html...>"}
        # "Value" is HTML when blocks exist, or [] when none are available.
        try:
            value = resp.json().get("Value", resp.text)
        except Exception:
            value = resp.text

        if not isinstance(value, str):
            # No blocks available for this line (Etere returned Value:[])
            print("[DIRECT]     ! No available blocks for this line")
            return 0

        body = value

        # Parse block IDs from the tableSearchBlocksTable JSON object.
        # Structure: {"Code":"BlocksTable","Header":[...],"Body":[[row cells],...]}
        # Each row's first cell has HiddenParams.idBlock = the block ID.
        block_ids: list[int] = []
        tv = _re.search(r'tableSearchBlocksTable\s*=\s*([{\[])', body)
        if tv:
            try:
                decoder = _json.JSONDecoder()
                obj, _ = decoder.raw_decode(body, tv.start(1))
                rows = obj.get('Body') or [] if isinstance(obj, dict) else obj
                for row in rows:
                    try:
                        bid = row[0]['HiddenParams']['idBlock']
                        block_ids.append(int(bid))
                    except (KeyError, IndexError, TypeError, ValueError):
                        pass
            except Exception:
                pass

        # Deduplicate while preserving order
        seen: set[int] = set()
        block_ids = [bid for bid in block_ids if not (bid in seen or seen.add(bid))]

        count = len(block_ids)

        if not block_ids:
            print("[DIRECT]     ! No blocks found in HTTP response")
            return 0

        # Write to CONTRATTIFASCE: clear stale entries then insert the new set
        if line_id:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = ?", [line_id]
            )
            for bid in block_ids:
                cursor.execute(
                    "INSERT INTO CONTRATTIFASCE "
                    "(ID_CONTRATTIRIGHE, ID_FASCE, PRICELIST, SELECTEDSEGMENTS) "
                    "VALUES (?, ?, '', '')",
                    [line_id, bid],
                )
            if self._autocommit:
                self._conn.commit()
            print(f"[DIRECT]     -> {count} block(s) assigned (HTTP)")
        else:
            print(f"[DIRECT]     ! {count} block(s) found but no line_id — not saved")

        return count

    def get_all_line_ids(self, contract_id) -> list[int]:
        """
        Return all ID_CONTRATTIRIGHE values for the given contract,
        ordered by ID (i.e. creation order).

        contract_id may be an int or a numeric string — both are the
        ID_CONTRATTITESTATA primary key used in the Etere URL (/sales/contract/NNN).
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT ID_CONTRATTIRIGHE
            FROM   CONTRATTIRIGHE
            WHERE  ID_CONTRATTITESTATA = ?
            ORDER  BY ID_CONTRATTIRIGHE
        """, [int(contract_id)])
        return [row[0] for row in cursor.fetchall()]

    def assign_blocks_for_existing_line(self, line_id: int) -> int:
        """
        Assign blocks to a line that already exists in CONTRATTIRIGHE (e.g. created
        via Selenium).  Reads ORA_INIZIO/ORA_FINE, day bits, date range, and
        COD_USER directly from the DB and delegates to _assign_blocks_http().

        Returns the number of blocks assigned, or -1 if the line was not found.
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT ORA_INIZIO, ORA_FINE,
                   LUNEDI, MARTEDI, MERCOLEDI, GIOVEDI,
                   VENERDI, SABATO, DOMENICA,
                   DATA_INIZIO, DATA_FINE,
                   COD_USER, ID_CONTRATTITESTATA
            FROM   CONTRATTIRIGHE
            WHERE  ID_CONTRATTIRIGHE = ?
        """, [line_id])
        row = cursor.fetchone()
        if not row:
            print(f"[DIRECT] assign_blocks_for_existing_line: line {line_id} not found")
            return -1

        start_frames, end_frames = row[0], row[1]
        day_bits = {
            "lun": bool(row[2]),
            "mar": bool(row[3]),
            "mer": bool(row[4]),
            "gio": bool(row[5]),
            "ven": bool(row[6]),
            "sab": bool(row[7]),
            "dom": bool(row[8]),
        }
        date_from   = row[9]
        date_to     = row[10]
        user_id     = row[11]
        contract_id = row[12]

        # Strip trailing asterisks Etere appends after block operations
        cursor.execute("""
            UPDATE CONTRATTIRIGHE
            SET    DESCRIZIONE = RTRIM(REPLACE(DESCRIZIONE, '*', ''))
            WHERE  ID_CONTRATTIRIGHE = ?
              AND  DESCRIZIONE LIKE '%*%'
        """, [line_id])
        if self._autocommit:
            self._conn.commit()

        return self._assign_blocks_http(
            contract_id=contract_id,
            user_id=user_id,
            start_frames=start_frames,
            end_frames=end_frames,
            day_bits=day_bits,
            date_from=date_from,
            date_to=date_to,
            line_id=line_id,
        )

    # ── Utilities (pass-through to shared helpers) ───────────────────────────────

    @staticmethod
    def parse_time_range(time_str: str) -> tuple[str, str]:
        """Delegate to EtereClient.parse_time_range for consistent normalisation."""
        from browser_automation.etere_client import EtereClient
        return EtereClient.parse_time_range(time_str)

    @staticmethod
    def check_sunday_6_7a_rule(days: str, time_str: str) -> tuple[str, int]:
        """Delegate to EtereClient.check_sunday_6_7a_rule."""
        from browser_automation.etere_client import EtereClient
        return EtereClient.check_sunday_6_7a_rule(days, time_str)

    @staticmethod
    def consolidate_weeks(weekly_spots, week_start_dates, flight_end):
        """Delegate to EtereClient.consolidate_weeks."""
        from browser_automation.etere_client import EtereClient
        return EtereClient.consolidate_weeks(weekly_spots, week_start_dates, flight_end)

    @staticmethod
    def consolidate_weeks_from_flight(weekly_spots, flight_start, flight_end):
        """Delegate to EtereClient.consolidate_weeks_from_flight."""
        from browser_automation.etere_client import EtereClient
        return EtereClient.consolidate_weeks_from_flight(
            weekly_spots, flight_start, flight_end
        )
