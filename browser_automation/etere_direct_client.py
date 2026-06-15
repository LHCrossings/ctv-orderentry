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

import os
import re
from datetime import date, datetime
from math import ceil
from typing import Optional

try:
    import pyodbc  # noqa: F401 — only needed on Windows for DB access
except ImportError:
    pyodbc = None

try:
    import pymssql as _pymssql
except ImportError:
    _pymssql = None
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_dotenv(_env_path)  # always loads from project root regardless of CWD
except ImportError:
    pass  # python-dotenv not installed; connect() falls back to Windows Auth

# ── Connection ──────────────────────────────────────────────────────────────────

# Override via ETERE_DB_SERVER / ETERE_WEB_URL in credentials.env
DB_SERVER     = os.getenv("ETERE_DB_SERVER", "100.85.38.72")
DB_DATABASE   = "Etere_crossing"
DB_DRIVER     = "{SQL Server}"
ETERE_WEB_URL = os.getenv("ETERE_WEB_URL", "http://100.102.206.113")


def connect():
    """Return a live DB connection (pymssql on Linux, pyodbc on Windows).

    SQL auth credentials are read from .env:
        ETERE_DB_USER     — SQL Server login name
        ETERE_DB_PASSWORD — SQL Server login password

    Falls back to Windows Authentication via pyodbc if no credentials set.
    """
    user = os.getenv("ETERE_DB_USER")
    password = os.getenv("ETERE_DB_PASSWORD")

    if user and password and _pymssql is not None:
        return _pymssql.connect(
            server=DB_SERVER,
            user=user,
            password=password,
            database=DB_DATABASE,
        )

    if pyodbc is not None:
        if user and password:
            return pyodbc.connect(
                f"DRIVER={DB_DRIVER};SERVER={DB_SERVER};"
                f"DATABASE={DB_DATABASE};UID={user};PWD={password};"
            )
        return pyodbc.connect(
            f"DRIVER={DB_DRIVER};SERVER={DB_SERVER};"
            f"DATABASE={DB_DATABASE};Trusted_Connection=yes;"
        )

    raise RuntimeError("No DB driver available — install pymssql or pyodbc")


def etere_web_login(retries: int = 3, retry_delay: float = 12.0):
    """Log into the Etere web UI headlessly using requests.

    Reads credentials from credentials.env (same as Selenium login).
    Returns a live requests.Session for use with Etere web API calls (report fetcher, EDI).
    The session carries all cookies and will reuse them across calls.

    When the license seat limit is hit, automatically retries up to `retries` times
    with `retry_delay` seconds between attempts (orphaned sessions expire quickly).

    Raises RuntimeError if login fails or no cookies are returned.
    """
    try:
        import requests as _req
    except ImportError:
        raise RuntimeError("requests package not installed — run: pip install requests")

    import os
    import sys
    import time as _time
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from credential_loader import load_credentials

    username, password = load_credentials()
    login_url = f"{ETERE_WEB_URL}/index/login"

    for attempt in range(1 + retries):
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

        # Parse login response — Etere returns JSON: {"IsOk": true/false, "Message": "..."}
        try:
            login_result = resp.json()
        except Exception:
            login_result = {}

        if login_result.get("IsOk", False):
            break

        msg = login_result.get("Message", "Login failed (unknown reason)")

        # Retry only for license-exhaustion errors — all others fail immediately
        is_license_error = "license" in msg.lower() or "exceeded" in msg.lower()
        if not is_license_error or attempt >= retries:
            raise RuntimeError(f"Etere login failed: {msg}")

        print(
            f"[LOGIN] License seats exhausted (attempt {attempt + 1}/{1 + retries}) — "
            f"retrying in {retry_delay:.0f}s..."
        )
        _time.sleep(retry_delay)

    if not dict(session.cookies):
        raise RuntimeError(
            "Etere login returned no cookies — credentials may be wrong "
            "or the login endpoint has changed."
        )

    print(f"[LOGIN] OK - Logged into Etere as {username} ({len(dict(session.cookies))} cookie(s))")
    return session


def etere_web_logout(session) -> None:
    """Log out of the Etere web UI, releasing the license seat.

    Must be called after every headless session — orphaned sessions consume
    license slots until IIS session timeout.
    """
    try:
        logout_url = f"{ETERE_WEB_URL}/index/logout"
        session.get(logout_url, timeout=10, allow_redirects=True)
        print("[LOGOUT] Logged out of Etere.")
    except Exception as exc:
        print(f"[LOGOUT] Warning: could not log out cleanly: {exc}")


# ── Constants ───────────────────────────────────────────────────────────────────

FRAMES_PER_SECOND = 29.97   # NTSC drop-frame rate — used for time-of-day (start/end/separation)

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
    "BRENTAN": 439,
}

# Media center (CENTROMEDIA) IDs
MEDIA_CENTER_IDS: dict[str, int] = {
    "RPM":      316,
    "HL":       316,
    "IMPRENTA": 316,
    "IMPACT":     0,
    "SAGENT":   316,
}

def _build_newtype(
    is_bonus: bool = False,
    is_billboard: bool = False,
    is_bookend: bool = False,
    is_added_value: bool = False,
    is_barter: bool = False,
    is_trade: bool = False,
) -> str:
    """Build semicolon-delimited NEWTYPE string: specific type first, COMS second."""
    if is_trade:
        specific = "TRD"
    elif is_bonus:
        specific = "BNS"
    elif is_added_value:
        specific = "AV"
    elif is_billboard:
        specific = "BB"
    elif is_bookend:
        specific = "BOOK"
    elif is_barter:
        specific = "BART"
    else:
        specific = "COM"
    return f"{specific};COMS"

# Default Nielsen target ID (Adults 35-64)
DEFAULT_NIELSEN_ID   = 728
DEFAULT_NIELSEN_CODE = "0001"


# ── Frame-conversion helpers ────────────────────────────────────────────────────

def _to_frames(h: int, m: int = 0, s: int = 0) -> int:
    """Convert HH:MM:SS to Etere frame count (29.97 fps)."""
    return round((h * 3600 + m * 60 + s) * FRAMES_PER_SECOND)


def _seconds_to_frames(seconds: int) -> int:
    """Convert spot duration in seconds to SMPTE drop-frame frame count.

    Etere stores durations as 29.97df frames. At exact minute marks (MM:00),
    frames 00 and 01 are dropped (invalid in df), so the first valid frame is 02.
    Verified: :15→450, :30→900, :60→1800, 2:00→3598.
    """
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    total_minutes = h * 60 + m
    base = (h * 3600 + m * 60 + s) * 30
    drop = 2 * (total_minutes - total_minutes // 10)
    # At exact minute marks (non-10-min boundary), frame 00 is invalid — use frame 02
    correction = 2 if (s == 0 and total_minutes > 0 and total_minutes % 10 != 0) else 0
    return base - drop + correction


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
        # Handle ':30' (leading colon, bare seconds) → ['', '30']
        if parts[0] == "" and len(parts) == 2:
            return int(parts[1])
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(duration)


# ── Day-pattern parser ──────────────────────────────────────────────────────────

_DAY_KEYS = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")  # Mon-Sun

# Tokens recognised as individual days
_TOKEN_MAP: dict[str, str] = {
    "M":   "lun",
    "MO":  "lun",
    "MON": "lun",
    "T":   "mar",   # Admerasia/Melissa single-char Tuesday
    "TU":  "mar",
    "TUE": "mar",
    "W":   "mer",
    "WED": "mer",
    "TH":  "gio",
    "THU": "gio",
    "R":   "gio",
    "F":   "ven",
    "FRI": "ven",
    "S":   "sab",   # Admerasia/Melissa single-char Saturday
    "SA":  "sab",
    "SAT": "sab",
    "U":   "dom",   # Admerasia/Melissa single-char Sunday
    "SU":  "dom",
    "SUN": "dom",
}

# Ordered list used for range expansion "M-F", "M-Su", "Sa-Su"
_DAY_ORDER = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
_TOKEN_TO_INDEX: dict[str, int] = {
    "M": 0, "MO": 0, "MON": 0,
    "T": 1, "TU": 1, "TUE": 1,   # T = Tuesday (Admerasia)
    "W": 2, "WED": 2,
    "TH": 3, "THU": 3, "R": 3,
    "F": 4, "FRI": 4,
    "S": 5, "SA": 5, "SAT": 5,   # S = Saturday (Admerasia)
    "U": 6, "SU": 6, "SUN": 6,   # U = Sunday (Admerasia)
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

    def __init__(self, conn: pyodbc.Connection, owner: str = "House",
                 autocommit: bool = True):
        self._conn = conn
        # '%s' for pymssql (used when SQL auth creds are set), '?' for pyodbc (Windows Auth)
        self._ph = '%s' if type(conn).__module__.startswith('pymssql') else '?'
        self.owner = owner
        self._autocommit = autocommit
        self._master_market = "NYC"
        self._contract_id: Optional[int] = None
        self._nielsen_id: int = DEFAULT_NIELSEN_ID
        self._nielsen_code: str = DEFAULT_NIELSEN_CODE
        self._default_prenotazione: int = 0  # Priority; refreshed from inifiles on init
        self._load_default_scheduling()

    def _load_default_scheduling(self) -> None:
        """Read prop_defscheduletype from Etere inifiles and cache as _default_prenotazione.

        Etere stores this as a 1-based index: 1=Priority(0), 2=Rotation(1), 3=Opt(2), 4=Fixed(3).
        Falls back to Priority (0) on any error.
        """
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT TOP 1 OBJ_VALUE FROM inifiles "
                f"WHERE OBJ_NAME = {self._ph} ORDER BY LASTMODIFIED DESC",
                ("sales.ini/parameters/prop_defscheduletype",),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                self._default_prenotazione = max(0, int(row[0]) - 1)
                print(f"[DIRECT] Etere default scheduling: prop_defscheduletype={row[0]} → PRENOTAZIONE={self._default_prenotazione}")
        except Exception as e:
            print(f"[DIRECT] Warning: could not read default scheduling type ({e}); defaulting to Priority (0)")

    # ── Market ──────────────────────────────────────────────────────────────────

    def set_master_market(self, market: str) -> None:
        self._master_market = market

    # ── Client defaults from Etere ANAGRAF ──────────────────────────────────────

    def get_client_defaults(self, customer_id: int) -> dict:
        """
        Query ANAGRAF for the client record and return contract header defaults.
        Replicates what the Etere desktop app auto-populates when selecting a client.
        """
        cur = self._conn.cursor()
        cur.execute(
            f"""
            SELECT a.AGENZIA, a.ID_PAGAMENTI, a.CENTROMEDIA, a.AGENTE1,
                   ISNULL(ag.Commissione, 0)  AS agency_pct,
                   ISNULL(ae.COD_CONTO, '')   AS owner,
                   ISNULL(a.Id_Nielsen, 0)    AS nielsen_id,
                   ISNULL(n.NIELSEN, '')      AS nielsen_code
            FROM ANAGRAF a
            LEFT JOIN ANAGRAF ag  ON ag.ID_ANAGRAF  = a.AGENZIA
            LEFT JOIN ANAGRAF ae  ON ae.ID_ANAGRAF  = a.AGENTE1
            LEFT JOIN NIELSEN n   ON n.ID_NIELSEN   = a.Id_Nielsen
            WHERE a.ID_ANAGRAF = {self._ph}
            """,
            (customer_id,),
        )
        row = cur.fetchone()
        if not row:
            return {}
        return {
            "agency_id":       int(row[0] or 0),
            "payment_id":      int(row[1] or 1),
            "media_center_id": int(row[2] or 316),
            "agent_id":        int(row[3] or 11),
            "agency_pct":      float(row[4] or 0.0),
            "owner":           str(row[5] or ""),
            "nielsen_id":      int(row[6] or 0),
            "nielsen_code":    str(row[7] or ""),
        }

    # ── Contract header ─────────────────────────────────────────────────────────

    def create_contract_header(
        self,
        code: str,
        description: str,
        customer_id: int,
        agency_id: Optional[int] = None,
        agency_pct: Optional[float] = None,
        agent_id: Optional[int] = None,
        media_center_id: Optional[int] = None,
        contract_date: Optional[date] = None,
        contract_end_date: Optional[date] = None,
        contract_type: int = 1,
        billing_type: str = "agency",   # "agency" → INVOICEMODE=2/FATTURA=1; "client" → 0/0
        invoice_mode: Optional[int] = None,
        invoice_header: Optional[int] = None,
        vat: int = 1,
        payment_id: Optional[int] = None,
        note: str = "",
        customer_order_ref: str = "",
        owner: Optional[str] = None,
        master_market: str = "NYC",
        allow_rename: bool = False,
        lookup_customer_defaults: bool = False,
    ) -> int:
        """
        Create a contract header via web_sales_savecontractgeneral.
        Stores the returned ID internally for subsequent add_contract_line calls.
        Returns the new contract ID.

        agency_id=None triggers auto-lookup of agency_id, agency_pct, agent_id,
        media_center_id, and payment_id from ANAGRAF, replicating Etere's
        client-select auto-populate behaviour.

        lookup_customer_defaults=True forces the ANAGRAF lookup even when an
        agency_id is supplied. This is the agency-parser pattern: always query
        ANAGRAF for the client and use the agency it returns; the supplied
        agency_id is only a fallback for when the client's ANAGRAF record has no
        agency linked. owner/Nielsen/payment/agent/media-center always come from
        the client's ANAGRAF record.

        billing_type drives invoice_mode/invoice_header defaults:
          "agency"  → INVOICEMODE=2, FATTURAZIONE_PRINCIPALE=1  (Customer share indicating agency %)
          "client"  → INVOICEMODE=0, FATTURAZIONE_PRINCIPALE=0  (Customer / direct)
        """
        # Derive invoice fields from billing_type if not explicitly overridden
        if invoice_mode is None:
            invoice_mode = 2 if billing_type == "agency" else 0
        if invoice_header is None:
            invoice_header = 1 if billing_type == "agency" else 0

        # Auto-populate from ANAGRAF, keyed on the customer (client) ID. Runs when
        # agency_id is omitted, OR when an agency parser opts in via
        # lookup_customer_defaults while supplying a fallback agency_id.
        # The client's ANAGRAF record is the source of truth: owner / Nielsen /
        # payment / agent / media-center always come from it, and the agency it
        # returns WINS. A passed agency_id is only a fallback, used when the
        # client's ANAGRAF record has no agency linked.
        if agency_id is None or lookup_customer_defaults:
            defaults = self.get_client_defaults(customer_id)
            if not defaults:
                raise ValueError(
                    f"Customer ID {customer_id} not found in ANAGRAF. "
                    "Check customers.db for a stale or incorrect customer_id."
                )
            anagraf_agency  = defaults.get("agency_id", 0)
            if anagraf_agency:
                agency_id = anagraf_agency      # ANAGRAF's client→agency link wins
            elif agency_id is None:
                agency_id = 0
            # else: no agency in ANAGRAF — keep the caller-supplied fallback agency_id
            agency_pct      = agency_pct       if agency_pct       is not None else (defaults.get("agency_pct") or 15.0)
            agent_id        = agent_id         if agent_id         is not None else defaults.get("agent_id", 11)
            media_center_id = media_center_id  if media_center_id  is not None else defaults.get("media_center_id", 316)
            payment_id      = payment_id       if payment_id       is not None else defaults.get("payment_id", 1)
            # owner: explicit arg > customer DB override > ANAGRAF AE > instance default
            anagraf_owner   = defaults.get("owner", "") or self.owner
            effective_owner = owner if owner is not None else anagraf_owner
            # Nielsen product code from ANAGRAF.Id_Nielsen — drives all subsequent add_contract_line calls
            if defaults.get("nielsen_id"):
                self._nielsen_id   = defaults["nielsen_id"]
                self._nielsen_code = defaults["nielsen_code"] or DEFAULT_NIELSEN_CODE
        else:
            agency_pct      = agency_pct       if agency_pct       is not None else 15.0
            agent_id        = agent_id         if agent_id         is not None else 11
            media_center_id = media_center_id  if media_center_id  is not None else 316
            payment_id      = payment_id       if payment_id       is not None else 1
            effective_owner = owner if owner is not None else self.owner

        if contract_date is None:
            contract_date = date.today()

        # Enforce Etere's uniqueness rule — duplicate codes are blocked in the UI
        # but bypassed by direct SP calls. Raise (or auto-rename) before touching the DB.
        dup_cur = self._conn.cursor()
        dup_cur.execute(
            f"SELECT COUNT(*) FROM CONTRATTITESTATA WHERE COD_CONTRATTO = {self._ph}",
            (code,)
        )
        if (dup_cur.fetchone() or [0])[0] > 0:
            if not allow_rename:
                raise ValueError(
                    f"Contract code '{code}' already exists in Etere. "
                    "Choose a unique code before entering."
                )
            # Auto-append '*' until a unique code is found
            candidate = code
            while True:
                candidate += "*"
                dup_cur.execute(
                    f"SELECT COUNT(*) FROM CONTRATTITESTATA WHERE COD_CONTRATTO = {self._ph}",
                    (candidate,)
                )
                if (dup_cur.fetchone() or [0])[0] == 0:
                    print(f"[HEADER] Code '{code}' exists — using '{candidate}'")
                    code = candidate
                    break

        user_id = MARKET_USER_IDS.get(master_market, MARKET_USER_IDS["NYC"])

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
            effective_owner,  # @owner
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
            16711680,         # @customercolor — default red (0xFF0000), matches Etere convention
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
        cursor.execute(sql.replace('?', self._ph), params)
        if self._autocommit:
            self._conn.commit()

        # Retrieve the ID the SP just inserted
        cursor.execute(
            f"SELECT ID_CONTRATTITESTATA FROM CONTRATTITESTATA WHERE COD_CONTRATTO = {self._ph}",
            [code]
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
        is_billboard: bool = False,
        is_bottom: bool = False,
        is_added_value: bool = False,
        is_barter: bool = False,
        is_trade: bool = False,
        note: str = "",
        separation_intervals: tuple[int, int, int] = (15, 0, 0),
        contract_id: Optional[int] = None,
        priority: int = 500,
        whitelist_priority: int = 50,
        booking_code: int = 2,
        scheduling_type: Optional[int] = None,
        row_status: int = 0,   # 0=Ready, 2=Change Data (use 2 for revision lines on approved contracts)
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

        # Separation in frames — tuple is (customer, order, event)
        # SP @intevent → INTERVALLO (= order separation)
        # SP @intsrighe → INTERV_CONTRATTO (= event separation)
        intcomm   = _minutes_to_frames(separation_intervals[0])  # customer → @intcomm
        intevent  = _minutes_to_frames(separation_intervals[1])  # order    → @intevent  → INTERVALLO
        intsrighe = _minutes_to_frames(separation_intervals[2])  # event    → @intsrighe → INTERV_CONTRATTO

        newtype = _build_newtype(is_bonus, is_billboard, is_bookend, is_added_value, is_barter, is_trade)

        # Scheduling type (PRENOTAZIONE)
        # Caller may pass scheduling_type to override all auto-detection.
        # Bookend/billboard: always 0 — capofila/finefila + priority=3 control placement
        # BNS or AV (non-bookend): always Rotation (1)
        # Monthly (flight >7 days, spots_per_week=0): always Rotation (1)
        # Time window >2 hours: always Rotation (1)
        # Otherwise: use Etere's configured default (read from inifiles at init)
        if scheduling_type is not None:
            prenotazione = scheduling_type
        else:
            _is_position_locked = is_bookend or is_billboard or is_bottom
            if _is_position_locked:
                prenotazione = 0
            elif is_bonus or is_added_value:
                prenotazione = 1
            else:
                _flight_days = (date_to - date_from).days if date_from and date_to else 0
                _is_monthly = _flight_days > 7 and spots_per_week == 0
                _window_minutes = (end_h * 60 + end_m) - (start_h * 60 + start_m)
                _wide_window = _window_minutes > 120
                prenotazione = 1 if (_is_monthly or _wide_window) else self._default_prenotazione

        # capofila (top of break): bookend or billboard
        # finefila (bottom of break): bookend or bottom
        capofila = is_bookend or is_billboard
        finefila = is_bookend or is_bottom

        # Priority: forced by break position; caller value for all other types
        if is_bookend or is_billboard:
            effective_priority = 3
        elif is_bottom:
            effective_priority = 997
        else:
            effective_priority = priority

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
            capofila,           # @controllocapo  (top of break: bookend or billboard)
            finefila,           # @controllofine  (bottom of break: bookend or bottom)
            effective_priority, # @priorita  (3 forced for bookend/billboard)
            prenotazione,       # @prenotazione  (0=Priority,1=Rotation; derived per rules)
            False,              # @omaggio
            rate,               # @importo
            self._nielsen_code,    # @nielsen
            day_bits["lun"],    # @lun
            day_bits["mar"],    # @mar
            day_bits["mer"],    # @mer
            day_bits["gio"],    # @gio
            day_bits["ven"],    # @ven
            day_bits["sab"],    # @sab
            day_bits["dom"],    # @dom
            True,               # @manualprice
            booking_code,       # @idbooking
            0,                  # @id (new line; SP returns the assigned ID)
            whitelist_priority, # @priwhitelist
            row_status,         # @rowstatus — 0=Ready, 2=Change Data (revision lines on approved contracts)
            intcomm,            # @intcomm
            intsrighe,          # @intsrighe
            intevent,           # @intevent
            self._nielsen_id,   # @idnielsen
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
            note,               # @note
            0,                  # @linkedspotpos
            0,                  # @linkedspotid
        ]

        cursor = self._conn.cursor()
        cursor.execute(sql.replace('?', self._ph), params)
        if self._autocommit:
            self._conn.commit()

        # Retrieve the ID the SP just inserted
        cursor.execute(
            f"SELECT MAX(ID_CONTRATTIRIGHE) FROM CONTRATTIRIGHE WHERE ID_CONTRATTITESTATA = {self._ph}",
            [cid]
        )
        row = cursor.fetchone()
        line_id = row[0] if row else 0

        # Clear SECEVENTTYPE — column default is 'CTM' but Selenium always sends ''.
        # Secondary events are assigned manually by operators inside Etere, never during entry.
        if line_id:
            cursor.execute(
                f"UPDATE CONTRATTIRIGHE SET SECEVENTTYPE={self._ph} WHERE ID_CONTRATTIRIGHE={self._ph}",
                ("", line_id)
            )
            if self._autocommit:
                self._conn.commit()

        # Assign available program blocks to this line
        if line_id:
            self._assign_blocks(
                line_id=line_id,
                user_id=user_id,
                start_frames=start_frames,
                end_frames=end_frames,
                day_bits=day_bits,
                date_from=datefrom_dt,
                date_to=dateto_dt,
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
        Populate CONTRATTIFASCE for a contract line using Etere's own block-loading
        logic (source-confirmed from loadAvailableBlocks() in Etere.Web.Sales.dll).

        Joins: Traffic_Calendar → traffic_scheduleblock → traffic_block → traffic_segment
        Filters: date range, market, time window (ts.Offset + tseg.Offset), day-of-week,
                 Expired=0, Level=0 (published schedules only), visible=1.
        """
        _DAY_BITS_TO_NAME = {
            "dom": "Sunday",
            "lun": "Monday",
            "mar": "Tuesday",
            "mer": "Wednesday",
            "gio": "Thursday",
            "ven": "Friday",
            "sab": "Saturday",
        }
        active_days = [name for key, name in _DAY_BITS_TO_NAME.items() if day_bits.get(key)]
        if not active_days:
            return 0

        day_placeholders = ",".join([self._ph] * len(active_days))

        cursor = self._conn.cursor()

        cursor.execute(
            f"DELETE FROM CONTRATTIFASCE WHERE ID_CONTRATTIRIGHE = {self._ph}", [line_id]
        )
        deleted = cursor.rowcount
        if deleted:
            print(f"[DIRECT]     -> {deleted} existing block(s) cleared")

        # One CONTRATTIFASCE row per distinct schedule-offset the block appears at within
        # the time window — matches Etere's loadAvailableBlocks() behaviour where a block
        # that occupies two schedule slots (different ts.Offset values) gets two rows.
        cursor.execute(f"""
            INSERT INTO CONTRATTIFASCE (ID_CONTRATTIRIGHE, ID_FASCE, PRICELIST, SELECTEDSEGMENTS)
            SELECT {self._ph}, sub.ID_TrafficBlock, '', ''
            FROM (
                SELECT DISTINCT ts.Offset AS ts_offset, tb.ID_TrafficBlock, tb.Name AS block_name
                FROM Traffic_Calendar tc
                JOIN traffic_scheduleblock ts ON tc.id_trafficschedule = ts.id_trafficschedule
                JOIN traffic_block tb ON ts.id_trafficblock = tb.id_trafficblock
                JOIN traffic_segment tseg ON tb.ID_TrafficBlock = tseg.ID_TrafficBlock
                WHERE tc.Date BETWEEN {self._ph} AND {self._ph}
                  AND tb.Cod_User = {self._ph}
                  AND (ts.Offset + tseg.Offset) >= {self._ph}
                  AND (ts.Offset + tseg.Offset) < {self._ph}
                  AND tb.Expired = 0
                  AND tc.Level = 0
                  AND tseg.visible = 1
                  AND DATENAME(WEEKDAY, tc.Date) IN ({day_placeholders})
            ) sub
            ORDER BY sub.block_name
        """, [line_id, date_from, date_to, user_id,
              start_frames, end_frames, *active_days])

        count = cursor.rowcount
        if self._autocommit:
            self._conn.commit()
        if count:
            print(f"[DIRECT]     -> {count} block(s) assigned")
        return count

    def get_all_line_ids(self, contract_id) -> list[int]:
        """
        Return all ID_CONTRATTIRIGHE values for the given contract,
        ordered by ID (i.e. creation order).

        contract_id may be an int or a numeric string — both are the
        ID_CONTRATTITESTATA primary key used in the Etere URL (/sales/contract/NNN).
        """
        cursor = self._conn.cursor()
        cursor.execute(f"""
            SELECT ID_CONTRATTIRIGHE
            FROM   CONTRATTIRIGHE
            WHERE  ID_CONTRATTITESTATA = {self._ph}
            ORDER  BY ID_CONTRATTIRIGHE
        """, [int(contract_id)])
        return [row[0] for row in cursor.fetchall()]

    def assign_blocks_for_existing_line(self, line_id: int) -> int:
        """
        Assign blocks to a line that already exists in CONTRATTIRIGHE.
        Uses ORA_INIZIOF/ORA_FINEF and the DLL-confirmed Traffic_Calendar query.

        Returns the number of blocks assigned, or -1 if the line was not found.
        """
        cursor = self._conn.cursor()
        cursor.execute(f"""
            SELECT ORA_INIZIO, ORA_FINE,
                   ORA_INIZIOF, ORA_FINEF,
                   LUNEDI, MARTEDI, MERCOLEDI, GIOVEDI,
                   VENERDI, SABATO, DOMENICA,
                   DATA_INIZIO, DATA_FINE,
                   COD_USER, ID_CONTRATTITESTATA
            FROM   CONTRATTIRIGHE
            WHERE  ID_CONTRATTIRIGHE = {self._ph}
        """, [line_id])
        row = cursor.fetchone()
        if not row:
            print(f"[DIRECT] assign_blocks_for_existing_line: line {line_id} not found")
            return -1

        day_bits = {
            "lun": bool(row[4]),
            "mar": bool(row[5]),
            "mer": bool(row[6]),
            "gio": bool(row[7]),
            "ven": bool(row[8]),
            "sab": bool(row[9]),
            "dom": bool(row[10]),
        }
        date_from   = row[11]
        date_to     = row[12]
        user_id     = row[13]
        contract_id = row[14]

        # Strip trailing asterisks Etere appends after block operations
        cursor.execute(f"""
            UPDATE CONTRATTIRIGHE
            SET    DESCRIZIONE = RTRIM(REPLACE(DESCRIZIONE, '*', ''))
            WHERE  ID_CONTRATTIRIGHE = {self._ph}
              AND  DESCRIZIONE LIKE {self._ph}
        """, [line_id, '%*%'])
        if self._autocommit:
            self._conn.commit()

        # ORA_INIZIOF/ORA_FINEF are the normalized frame values loadBlock() uses.
        # ORA_FINE equals ORA_INIZIO (only start stored there); ORA_FINEF has
        # the actual end time.
        start_frames = row[2]
        end_frames   = row[3] if row[3] else row[2]
        return self._assign_blocks(
            line_id=line_id,
            user_id=user_id,
            start_frames=start_frames,
            end_frames=end_frames,
            day_bits=day_bits,
            date_from=date_from,
            date_to=date_to,
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
