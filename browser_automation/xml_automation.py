"""
XML Order Automation
====================
Entry point for processing AAAA SpotTV XML orders from any agency.

This file mirrors the structure of tcaa_automation.py but accepts
.xml files instead of .pdf files. The parsed data flows into the
identical Etere automation pipeline — zero changes to EtereClient
or the contract creation logic.

Supported agencies (any TVB-compliant traffic system):
    Strata / Freewheel  ← TCAA uses this
    WideOrbit
    Matrix
    Any other AAAA SpotTV XML exporter

Usage (from orchestrator or CLI):
    gather_xml_inputs("path/to/order.xml")  → inputs dict
    process_xml_order(driver, "path/to/order.xml")

Architecture:
    xml_automation.py           ← YOU ARE HERE (input gathering + orchestration)
    parsers/aaaa_xml_parser.py  ← pure parsing, no side effects
    tcaa_automation.py          ← create_tcaa_contract() is reused directly
    etere_client.py             ← all browser interactions
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import sys

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from browser_automation.etere_client import EtereClient
from browser_automation.parsers.aaaa_xml_parser import parse_aaaa_xml, print_parse_summary
from browser_automation.tcaa_automation import (
    create_tcaa_contract,
    prompt_for_bonus_lines,
)
from parsers.tcaa_parser import TCAAEstimate
from src.data_access.repositories.customer_repository import CustomerRepository
from src.business_logic.services.customer_matching_service import CustomerMatchingService
from src.domain.enums import OrderType

# Path to customer database (relative to project root)
_DB_PATH = Path(__file__).parent.parent / "data" / "customers.db"


# ============================================================================
# KNOWN MARKETS (for prompting when XML cannot detect market)
# ============================================================================

_MARKET_CHOICES = {
    "1": ("SEA", "Seattle"),
    "2": ("LAX", "Los Angeles"),
    "3": ("SFO", "San Francisco"),
    "4": ("NYC", "New York"),
    "5": ("HOU", "Houston"),
    "6": ("CMP", "Chicago/Minneapolis"),
    "7": ("WDC", "Washington DC"),
    "8": ("CVC", "Central Valley/Sacramento"),
    "9": ("DAL", "Dallas (Asian Channel)"),
}


# ============================================================================
# INPUT GATHERING (upfront, before browser opens)
# ============================================================================

@dataclass(frozen=True)
class XmlOrderInputs:
    """All user inputs gathered before unattended processing begins."""
    xml_path: str
    estimates: list[TCAAEstimate]
    agency: str                            # Agency/buyer name (for notes only)
    client: str                            # Confirmed client/advertiser name
    client_id: str                         # Etere customer ID for the client
    market: str                            # Confirmed market code
    bonus_inputs: dict[str, dict]          # estimate_number → bonus_inputs dict
    separation_intervals: tuple[int, int, int]
    order_code: Optional[str]
    description: Optional[str]


def gather_xml_inputs(xml_path: str) -> Optional[XmlOrderInputs]:
    """
    Parse XML and gather all user inputs upfront.

    Called by the orchestrator before the browser session opens.
    Returns None if user cancels.

    Args:
        xml_path: Path to the AAAA SpotTV XML file

    Returns:
        XmlOrderInputs with everything needed for unattended processing,
        or None if cancelled.
    """
    print(f"\n{'='*70}")
    print(f"XML ORDER PROCESSING")
    print(f"{'='*70}\n")

    # ── Parse XML ──
    print("Parsing XML file...")
    try:
        estimates = parse_aaaa_xml(xml_path)
    except (ValueError, FileNotFoundError) as e:
        print(f"\n✗ Cannot parse XML: {e}")
        return None

    print_parse_summary(estimates)

    # ── Open customer DB ──
    repo = CustomerRepository(_DB_PATH) if _DB_PATH.exists() else None

    # ── Confirm agency (name only — for notes) ──
    agency = _confirm_agency(estimates)

    # ── Confirm client + look up Etere customer ID ──
    client, client_id = _confirm_client(estimates, repo)

    # ── Confirm market ──
    market = _confirm_market(estimates)
    if not market:
        print("\n[CANCELLED] No market selected")
        return None

    # ── Optional: custom contract code / description ──
    print("\n" + "="*70)
    print("CONTRACT DETAILS (press Enter to use defaults)")
    print("="*70)

    order_code  = input("Custom contract code [leave blank for auto]: ").strip() or None
    description = input("Custom description   [leave blank for auto]: ").strip() or None

    # ── Bonus line inputs ──
    all_bonus_inputs = _gather_bonus_inputs(estimates)

    # ── Separation intervals ──
    from browser_automation.separation_utils import confirm_separation_intervals
    separation_intervals = confirm_separation_intervals(
        detected_separation=None,  # XML doesn't specify separation
        order_type="XML",
        estimate_number=f"All {len(estimates)} estimate(s)"
    )

    print(f"\n✓ Ready to process {len(estimates)} estimate(s) unattended")

    return XmlOrderInputs(
        xml_path=xml_path,
        estimates=estimates,
        agency=agency,
        client=client,
        client_id=client_id,
        market=market,
        bonus_inputs=all_bonus_inputs,
        separation_intervals=separation_intervals,
        order_code=order_code,
        description=description,
    )


def _confirm_agency(estimates: list[TCAAEstimate]) -> str:
    """
    Confirm the agency/buyer name (for notes only — no Etere ID needed).

    Reads Buyer/@buyingCompanyName from parsed estimates.
    If blank or 'N/A', requires user entry.
    """
    detected_buyers = {est.buyer for est in estimates if est.buyer and est.buyer != "N/A"}
    detected = next(iter(detected_buyers)) if len(detected_buyers) == 1 else ""

    print("\n" + "="*70)
    print("AGENCY")
    print("="*70)

    if detected:
        print(f"\nDetected agency: {detected}")
        confirm = input("Use this? (Y/n): ").strip().lower()
        if confirm in ("", "y", "yes"):
            return detected

    while True:
        agency = input("Agency name: ").strip()
        if agency:
            return agency
        print("  Agency name cannot be blank")


def _confirm_client(
    estimates: list[TCAAEstimate],
    repo: Optional[CustomerRepository],
) -> tuple[str, str]:
    """
    Confirm the client/advertiser and look up their Etere customer ID.

    1. Reads Advertiser/@name from parsed estimates
    2. Fuzzy-matches against customers.db (OrderType.XML)
    3. If found: confirms with user and returns (name, customer_id)
    4. If not found: prompts for customer ID and saves to DB for next time

    Returns:
        (client_name, etere_customer_id)
    """
    detected_clients = {est.client for est in estimates if est.client and est.client != "Unknown"}
    detected = next(iter(detected_clients)) if len(detected_clients) == 1 else ""

    print("\n" + "="*70)
    print("CLIENT / ADVERTISER")
    print("="*70)

    # Try DB fuzzy match on detected name
    if repo and detected:
        customer = repo.find_by_fuzzy_match(detected, OrderType.XML)
        if customer:
            print(f"\nDetected client: {customer.customer_name}  (Etere ID: {customer.customer_id})")
            confirm = input("Use this? (Y/n): ").strip().lower()
            if confirm in ("", "y", "yes"):
                return (customer.customer_name, customer.customer_id)

    if detected:
        print(f"\nDetected from XML: {detected}")
    client_name = input("Client name [Enter to use above]: ").strip() or detected
    while not client_name:
        client_name = input("Client name: ").strip()

    # Look up / prompt for Etere customer ID
    if repo:
        service = CustomerMatchingService(repo)
        client_id = service.find_customer(client_name, OrderType.XML, prompt_if_not_found=True)
        return (client_name, client_id or "")
    else:
        client_id = input(f"Etere customer ID for '{client_name}': ").strip()
        return (client_name, client_id)


def _confirm_market(estimates: list[TCAAEstimate]) -> Optional[str]:
    """
    Confirm the market code, prompting if the XML didn't specify one.

    If all estimates agree on a detected market, offer it as the default.
    If market is UNKNOWN, show the full list and require selection.
    """
    # Check what the parser detected
    detected_markets = {est.market for est in estimates}

    if len(detected_markets) == 1:
        detected = next(iter(detected_markets))
    else:
        detected = "UNKNOWN"

    print("\n" + "="*70)
    print("MARKET CONFIRMATION")
    print("="*70)

    if detected != "UNKNOWN":
        # Found a market — offer it as default
        market_names = {code: name for _, (code, name) in _MARKET_CHOICES.items()}
        market_name = market_names.get(detected, detected)

        print(f"\nDetected market: {detected} ({market_name})")
        confirm = input(f"Use {detected}? (Y/n): ").strip().lower()

        if confirm in ("", "y", "yes"):
            return detected
        # Fall through to manual selection

    # Manual market selection
    print("\nSelect market:")
    for key, (code, name) in _MARKET_CHOICES.items():
        print(f"  {key}. {code} — {name}")

    while True:
        choice = input("\nMarket (1-9): ").strip()
        if choice in _MARKET_CHOICES:
            code, name = _MARKET_CHOICES[choice]
            print(f"✓ Market: {code} ({name})")
            return code
        print("  Invalid choice, try again")


def _gather_bonus_inputs(
    estimates: list[TCAAEstimate],
) -> dict[str, dict]:
    """
    Gather bonus line inputs for all estimates, with batch mode if structures match.

    Returns dict mapping estimate_number → bonus_inputs dict
    (same structure as all_bonus_inputs in process_tcaa_order).
    """
    all_bonus_inputs: dict[str, dict] = {}

    # Check if any estimate has bonus lines or South Asian paid lines
    any_bonus = any(
        any(line.is_bonus() for line in est.lines)
        for est in estimates
    )

    if not any_bonus:
        # No bonus lines — return empty inputs for all estimates
        for est in estimates:
            all_bonus_inputs[est.estimate_number] = {}
        print("\n✓ No bonus lines detected — skipping bonus input gathering")
        return all_bonus_inputs

    print(f"\n{'='*70}")
    print("BONUS LINE CONFIGURATION")
    print(f"{'='*70}\n")

    # Check if all estimates have identical bonus patterns
    from browser_automation.language_utils import extract_language_from_program
    bonus_patterns = []
    for est in estimates:
        n_bonus = sum(1 for line in est.lines if line.is_bonus())
        n_sa    = sum(1 for line in est.lines
                     if not line.is_bonus()
                     and "South Asian" in extract_language_from_program(line.program))
        bonus_patterns.append((n_bonus, n_sa))

    all_identical = len(set(bonus_patterns)) == 1

    if len(estimates) > 1 and all_identical and bonus_patterns[0][0] > 0:
        print(f"✓ All {len(estimates)} estimates have identical bonus structure")
        print("  1. Apply same setup to ALL estimates (recommended)")
        print("  2. Configure each estimate individually")
        choice = input("\nSelect option (1-2) [default: 1]: ").strip() or "1"

        if choice == "1":
            template = prompt_for_bonus_lines(estimates[0])
            for est in estimates:
                all_bonus_inputs[est.estimate_number] = template
            print(f"\n✓ Configuration applied to all {len(estimates)} estimates")
            return all_bonus_inputs

    # Individual configuration
    for est in estimates:
        inputs = prompt_for_bonus_lines(est)
        all_bonus_inputs[est.estimate_number] = inputs

    return all_bonus_inputs


# ============================================================================
# MAIN PROCESSING FUNCTION
# ============================================================================

def process_xml_order(
    driver,
    xml_path: str,
    pre_gathered_inputs: Optional[XmlOrderInputs] = None,
) -> bool:
    """
    Process an AAAA SpotTV XML order — create contracts in Etere.

    This is the main entry point called by the orchestrator after
    the browser session is open and logged in.

    The flow is identical to process_tcaa_order():
    1. Parse XML (or use pre-gathered inputs)
    2. Gather user inputs if not already gathered
    3. For each estimate: call create_tcaa_contract()

    Args:
        driver:                Selenium WebDriver (already logged in)
        xml_path:              Path to the AAAA SpotTV XML file
        pre_gathered_inputs:   Already-gathered inputs (from gather_xml_inputs),
                               or None to gather interactively now.

    Returns:
        True if all contracts created successfully
    """
    print(f"\n{'='*70}")
    print(f"PROCESSING XML ORDER: {Path(xml_path).name}")
    print(f"{'='*70}\n")

    # ── Get inputs (pre-gathered or gather now) ──
    if pre_gathered_inputs:
        inputs = pre_gathered_inputs
        estimates = inputs.estimates
    else:
        inputs = gather_xml_inputs(xml_path)
        if not inputs:
            return False
        estimates = inputs.estimates

    etere = EtereClient(driver)

    # ── Create each contract ──
    success_count = 0

    for estimate in estimates:
        # Inject confirmed agency, client, and market into the estimate
        estimate_confirmed = _inject_confirmed(estimate, inputs.agency, inputs.client, inputs.market)

        bonus_inputs = inputs.bonus_inputs.get(estimate.estimate_number, {})

        print(f"\n{'='*60}")
        print(f"Creating contract for estimate {estimate.estimate_number}")
        print(f"  Agency: {inputs.agency}")
        print(f"  Client: {inputs.client}  (ID: {inputs.client_id})")
        print(f"  Market: {inputs.market}")
        print(f"  Flight: {estimate.flight_start} – {estimate.flight_end}")
        print(f"  Lines:  {len(estimate.lines)}")
        print(f"{'='*60}")

        success = create_tcaa_contract(
            etere=etere,
            estimate=estimate_confirmed,
            bonus_inputs=bonus_inputs,
            separation_intervals=inputs.separation_intervals,
            order_code=inputs.order_code,
            description=inputs.description,
        )

        if success:
            success_count += 1
            print(f"\n✓ Estimate {estimate.estimate_number} completed")
        else:
            print(f"\n✗ Estimate {estimate.estimate_number} FAILED")
            cont = input("\nContinue with remaining? (y/n): ").strip().lower()
            if cont != "y":
                break

    print(f"\n{'='*70}")
    print(f"XML ORDER PROCESSING COMPLETE")
    print(f"{'='*70}")
    print(f"Successfully created: {success_count}/{len(estimates)} contracts")

    return success_count == len(estimates)


def _inject_confirmed(
    estimate: TCAAEstimate,
    agency: str,
    client: str,
    market: str,
) -> TCAAEstimate:
    """Return a copy of the estimate with confirmed agency, client, and market."""
    from dataclasses import replace
    return replace(estimate, buyer=agency, client=client, market=market)


# ============================================================================
# ORCHESTRATOR REGISTRATION HELPER
# ============================================================================

def gather_xml_inputs_from_path(xml_path: str) -> Optional[XmlOrderInputs]:
    """
    Adapter function matching the _INPUT_GATHERERS signature in orchestrator.py.

    The orchestrator calls:  getattr(module, fn_name)(str(order.pdf_path))
    So this function takes a single path argument and returns inputs.

    To register in orchestrator.py, add to _INPUT_GATHERERS:
        OrderType.XML: (
            "browser_automation.xml_automation",
            "gather_xml_inputs_from_path",
            "XML (AAAA SpotTV)"
        ),
    """
    return gather_xml_inputs(xml_path)


# ============================================================================
# STANDALONE TEST
# ============================================================================

if __name__ == "__main__":
    xml_file = sys.argv[1] if len(sys.argv) > 1 else "CRTV-TV_XML.xml"

    print("Testing XML input gathering (no browser)...\n")
    inputs = gather_xml_inputs(xml_file)

    if inputs:
        print(f"\n✓ Inputs gathered successfully:")
        print(f"  Agency:      {inputs.agency}")
        print(f"  Client:      {inputs.client}  (ID: {inputs.client_id})")
        print(f"  Market:      {inputs.market}")
        print(f"  Estimates:   {len(inputs.estimates)}")
        print(f"  Separation:  {inputs.separation_intervals}")
        print(f"  Order code:  {inputs.order_code or '(auto)'}")
        print(f"\nReady for browser automation.")
    else:
        print("\n✗ Input gathering cancelled or failed")
