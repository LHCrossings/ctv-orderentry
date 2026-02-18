"""
TCAA Order Browser Automation (Refactored)

Uses etere_client.py for ALL Etere interactions.
This file contains ONLY:
- PDF parsing orchestration
- Business logic (ROS definitions, bonus line prompting)
- Data transformation
- Calls to etere_client methods

NO Etere browser code lives here - it's all in etere_client.py.
"""

from dataclasses import dataclass
from typing import Optional, Tuple
from pathlib import Path
import sys

# Add src to path for imports
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from browser_automation.etere_client import EtereClient
from browser_automation.ros_definitions import ROS_SCHEDULES
from browser_automation.language_utils import get_language_block_prefixes, extract_language_from_program
from src.domain.enums import BillingType
from parsers.tcaa_parser import (
    parse_tcaa_pdf,
    TCAAEstimate,
    TCAALine,
    format_time_for_description,
)


# ============================================================================
# DOMAIN MODELS (User Input)
# ============================================================================

@dataclass(frozen=True)
class BonusLineInput:
    """User input for a bonus line."""
    days: str
    time: str
    language: str
    hindi_punjabi_both: Optional[str] = None  # For South Asian disambiguation


# ============================================================================
# ROS (Run of Schedule) DEFINITIONS - Now using universal definitions
# ============================================================================

# Import universal ROS schedules (shared across all agencies)
# Defined in browser_automation/ros_definitions.py
# ROS_SCHEDULES already imported above

# Alias for backward compatibility with existing code
ROS_OPTIONS = ROS_SCHEDULES


# ============================================================================
# LANGUAGE BLOCK MAPPING - Now using universal utilities
# ============================================================================

# Import universal language utilities (shared across all agencies)
# Defined in browser_automation/language_utils.py
# get_language_block_prefixes already imported above


# ============================================================================
# PRESENTATION LAYER - User Input Gathering
# ============================================================================

def prompt_for_bonus_lines(estimate: TCAAEstimate) -> dict[int, BonusLineInput]:
    """
    Prompt user for bonus line specifications AND South Asian disambiguation (upfront, before automation).
    
    This gathers ALL user input needed for the entire estimate before any browser automation begins,
    enabling fully unattended processing.
    
    Args:
        estimate: TCAAEstimate object
    
    Returns:
        Dictionary mapping line index to BonusLineInput (for bonus lines and South Asian paid lines)
    """
    bonus_inputs = {}
    
    # Find bonus lines
    bonus_lines = [(idx, line) for idx, line in enumerate(estimate.lines) if line.is_bonus()]
    
    # Find South Asian paid lines (need disambiguation too!)
    south_asian_paid_lines = [
        (idx, line) for idx, line in enumerate(estimate.lines)
        if not line.is_bonus() and "South Asian" in extract_language_from_program(line.program)
    ]
    
    if not bonus_lines and not south_asian_paid_lines:
        return bonus_inputs
    
    print(f"\nEstimate {estimate.estimate_number} requires upfront input")
    print(f"  - {len(bonus_lines)} bonus line(s)")
    print(f"  - {len(south_asian_paid_lines)} South Asian paid line(s) (need block selection)")
    print("\nPlease specify how to enter each:\n")
    
    for line_idx, line in bonus_lines:
        print(f"Bonus Line {line_idx + 1}:")
        print(f"  Program: {line.program}")
        print(f"  Spots: {line.total_spots}")
        print()
        
        # Show ROS options
        print("  ROS (Run of Schedule) Options:")
        for idx, (name, definition) in enumerate(ROS_OPTIONS.items(), 1):
            print(f"    {idx}. {name} ROS - {definition['days']} {definition['time']}")
        print(f"    {len(ROS_OPTIONS) + 1}. Custom Entry")
        print()
        
        # Get choice
        while True:
            choice = input(f"  Select option (1-{len(ROS_OPTIONS) + 1}): ").strip()
            try:
                choice_num = int(choice)
                if 1 <= choice_num <= len(ROS_OPTIONS) + 1:
                    break
            except ValueError:
                pass
            print("  Invalid choice, try again")
        
        # Handle choice
        if choice_num <= len(ROS_OPTIONS):
            # ROS option selected
            ros_name = list(ROS_OPTIONS.keys())[choice_num - 1]
            ros_def = ROS_OPTIONS[ros_name]
            
            days = ros_def['days']
            time = ros_def['time']
            language = ros_def['language']
            
            # Handle South Asian disambiguation
            hindi_punjabi_both = None
            if language == "South Asian":
                print("\n  South Asian block selection:")
                print("    1. Hindi (SA blocks only)")
                print("    2. Punjabi (P blocks only)")
                print("    3. Both (SA and P blocks)")
                sa_choice = input("  Select (1-3): ").strip()
                
                if sa_choice == "1":
                    hindi_punjabi_both = "Hindi"
                elif sa_choice == "2":
                    hindi_punjabi_both = "Punjabi"
                else:
                    hindi_punjabi_both = "Both"
            
            print(f"  ✓ Selected: {ros_name} ROS")
            
        else:
            # Custom entry
            print("\n  Custom Entry:")
            days = input("    Days (e.g., M-F, M-Su, Sa-Su): ").strip()
            time = input("    Time (e.g., 8a-10a, 7p-11p): ").strip()
            language = input("    Language (e.g., Korean, Filipino): ").strip()
            
            # Handle South Asian
            hindi_punjabi_both = None
            if language.lower() == "south asian":
                print("    South Asian block selection:")
                print("      1. Hindi (SA blocks only)")
                print("      2. Punjabi (P blocks only)")
                print("      3. Both (SA and P blocks)")
                sa_choice = input("    Select (1-3): ").strip()
                
                if sa_choice == "1":
                    hindi_punjabi_both = "Hindi"
                elif sa_choice == "2":
                    hindi_punjabi_both = "Punjabi"
                else:
                    hindi_punjabi_both = "Both"
            
            print(f"  ✓ Custom: {days} {time} {language}")
        
        # Store input
        bonus_inputs[line_idx] = BonusLineInput(
            days=days,
            time=time,
            language=language,
            hindi_punjabi_both=hindi_punjabi_both
        )
        print()
    
    # Now gather South Asian paid line block selections
    if south_asian_paid_lines:
        print(f"\n{'='*70}")
        print("SOUTH ASIAN PAID LINES - Block Selection")
        print(f"{'='*70}\n")
        
        for line_idx, line in south_asian_paid_lines:
            print(f"South Asian Paid Line {line_idx + 1}:")
            print(f"  Program: {line.program}")
            print(f"  Days: {line.days}, Time: {line.time}")
            print(f"  Spots: {line.total_spots}")
            print()
            print("  Select programming blocks:")
            print("    1. Hindi (SA blocks only)")
            print("    2. Punjabi (P blocks only)")
            print("    3. Both (SA and P blocks)")
            
            while True:
                choice = input("  Select (1-3): ").strip()
                if choice in ["1", "2", "3"]:
                    break
                print("  Invalid choice, try again")
            
            if choice == "1":
                hindi_punjabi_both = "Hindi"
            elif choice == "2":
                hindi_punjabi_both = "Punjabi"
            else:
                hindi_punjabi_both = "Both"
            
            # Store as BonusLineInput (reusing the structure for paid South Asian)
            # days/time/language will be extracted from line data later
            bonus_inputs[line_idx] = BonusLineInput(
                days="",  # Not used for paid lines
                time="",  # Not used for paid lines
                language="South Asian",
                hindi_punjabi_both=hindi_punjabi_both
            )
            print(f"  ✓ Selected: {hindi_punjabi_both}")
            print()
    
    return bonus_inputs


def prompt_for_south_asian_disambiguation(description: str) -> str:
    """
    Prompt user to choose Hindi/Punjabi/Both for South Asian paid lines.
    
    Args:
        description: Line description for context
    
    Returns:
        "Hindi", "Punjabi", or "Both"
    """
    print(f"\nSouth Asian line: {description}")
    print("  Select programming blocks:")
    print("    1. Hindi (SA blocks only)")
    print("    2. Punjabi (P blocks only)")
    print("    3. Both (SA and P blocks)")
    
    while True:
        choice = input("  Select (1-3): ").strip()
        if choice == "1":
            return "Hindi"
        elif choice == "2":
            return "Punjabi"
        elif choice == "3":
            return "Both"
        else:
            print("  Invalid choice, try again")


# ============================================================================
# MAIN PROCESSING FUNCTIONS
# ============================================================================

def process_tcaa_order(
    driver,
    pdf_path: str,
    estimate_number: Optional[str] = None,
    order_code: Optional[str] = None,
    description: Optional[str] = None
) -> bool:
    """
    Process TCAA order PDF - create contracts in Etere.
    
    Args:
        driver: Selenium WebDriver (already logged in)
        pdf_path: Path to the TCAA PDF
        estimate_number: Optional - process only this estimate
        order_code: Optional pre-gathered custom contract code
        description: Optional pre-gathered custom description
    
    Returns:
        True if all contracts created successfully
    """
    print(f"\n{'='*70}")
    print(f"PROCESSING TCAA ORDER: {pdf_path}")
    print(f"{'='*70}\n")
    
    # Parse PDF
    print("Parsing PDF...")
    all_estimates = parse_tcaa_pdf(pdf_path)
    
    # Detect separation intervals from PDF
    import pdfplumber
    from pathlib import Path
    
    detected_separation = None
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            for page in pdf.pages:
                full_text += page.extract_text() or ""
            
            # Try to detect separation from text
            from separation_utils import detect_separation_from_text
            detected_separation = detect_separation_from_text(full_text)
    except Exception as e:
        print(f"Warning: Could not detect separation from PDF: {e}")
    
    # Filter to selected estimate if specified
    if estimate_number:
        estimates = [est for est in all_estimates if est.estimate_number == estimate_number]
        if not estimates:
            print(f"✗ Estimate {estimate_number} not found in PDF")
            return False
        
        # Check if there are other estimates we could batch process
        if len(all_estimates) > 1:
            print(f"{'='*70}")
            print(f"BATCH PROCESSING OPPORTUNITY")
            print(f"{'='*70}")
            print(f"You selected estimate {estimate_number}, but this PDF contains {len(all_estimates)} total estimates:")
            for est in all_estimates:
                marker = " ← SELECTED" if est.estimate_number == estimate_number else ""
                print(f"  - Estimate {est.estimate_number}{marker}")
            print()
            print("Would you like to process ALL estimates together?")
            print("Benefits:")
            print("  ✓ Same bonus line setup applies to all")
            print("  ✓ Same separation applies to all")
            print("  ✓ Fully unattended after initial setup")
            print("  ✓ Saves time on repetitive inputs")
            print()
            
            batch_all = input("Process all estimates together? (Y/n): ").strip().lower()
            
            if batch_all in ['', 'y', 'yes']:
                print(f"\n✓ Will process all {len(all_estimates)} estimates in batch mode\n")
                estimates = all_estimates  # Process all!
            else:
                print(f"\n✓ Will process only estimate {estimate_number}\n")
        else:
            print(f"Processing estimate {estimate_number} (only estimate in PDF)\n")
    else:
        # No estimate_number specified - this means user selected multiple from CLI
        # Check if they want just the selected ones or all in the PDF
        estimates = all_estimates
        
        print(f"{'='*70}")
        print(f"ESTIMATE SELECTION")
        print(f"{'='*70}")
        print(f"Found {len(estimates)} total estimates in this PDF:")
        for idx, est in enumerate(estimates, 1):
            print(f"  {idx}. Estimate {est.estimate_number}")
        print()
        print(f"Process ALL {len(estimates)} estimates together?")
        print("  ✓ Same bonus line setup applies to all")
        print("  ✓ Same separation applies to all")
        print("  ✓ Fully unattended after initial setup")
        print()
        
        batch_all = input(f"Process all {len(estimates)} estimates? (Y/n): ").strip().lower()
        
        if batch_all not in ['', 'y', 'yes']:
            # User wants to select specific ones
            print(f"\nWhich estimates do you want to process?")
            print(f"Options:")
            print(f"  - Enter numbers separated by commas (e.g., 1,2,4)")
            print(f"  - Enter ranges (e.g., 1-3,5)")
            print(f"  - Enter 'all' to process all")
            print(f"  - Press Enter to cancel")
            
            selection = input("\nYour selection: ").strip().lower()
            
            if not selection:
                print("\n✗ Processing cancelled")
                return False
            
            if selection == 'all':
                print(f"\n✓ Will process all {len(estimates)} estimates\n")
            else:
                # Parse selection (handles "1,2,4" or "1-3,5")
                selected_indices = set()
                try:
                    for part in selection.split(','):
                        part = part.strip()
                        if '-' in part:
                            # Range like "1-3"
                            start, end = part.split('-')
                            selected_indices.update(range(int(start), int(end) + 1))
                        else:
                            # Single number
                            selected_indices.add(int(part))
                    
                    # Convert to 0-indexed and filter valid estimates
                    selected_indices = [i - 1 for i in sorted(selected_indices) if 1 <= i <= len(estimates)]
                    estimates = [estimates[i] for i in selected_indices]
                    
                    if not estimates:
                        print(f"\n✗ No valid estimates selected")
                        return False
                    
                    print(f"\n✓ Will process {len(estimates)} estimate(s): {', '.join(est.estimate_number for est in estimates)}\n")
                    
                except (ValueError, IndexError) as e:
                    print(f"\n✗ Invalid selection: {e}")
                    return False
        else:
            print(f"\n✓ Will process all {len(estimates)} estimates in batch mode\n")
    
    # Gather bonus line inputs upfront
    print(f"{'='*70}")
    print("ANALYZING ESTIMATES FOR BATCH PROCESSING")
    print(f"{'='*70}\n")
    
    # Check if all estimates have identical bonus line structure
    all_bonus_inputs = {}
    
    if len(estimates) > 1:
        # Analyze bonus line patterns across all estimates
        bonus_patterns = []
        for estimate in estimates:
            bonus_lines = [(idx, line) for idx, line in enumerate(estimate.lines) if line.is_bonus()]
            south_asian_paid = [(idx, line) for idx, line in enumerate(estimate.lines) 
                               if not line.is_bonus() and "South Asian" in extract_language_from_program(line.program)]
            bonus_patterns.append((len(bonus_lines), len(south_asian_paid)))
        
        # Check if all patterns are identical
        if len(set(bonus_patterns)) == 1:
            print(f"✓ All {len(estimates)} estimates have identical structure:")
            print(f"  - {bonus_patterns[0][0]} bonus line(s) each")
            print(f"  - {bonus_patterns[0][1]} South Asian paid line(s) each")
            print()
            
            # Offer batch mode
            print("Bonus Line Configuration:")
            print("  1. Apply same setup to ALL estimates (recommended)")
            print("  2. Configure each estimate individually")
            
            batch_bonus = input("\nSelect option (1-2) [default: 1]: ").strip() or "1"
            
            if batch_bonus == "1":
                # Configure once, apply to all
                print(f"\n{'='*70}")
                print("CONFIGURE BONUS LINES (will apply to all estimates)")
                print(f"{'='*70}\n")
                
                template_inputs = prompt_for_bonus_lines(estimates[0])
                
                # Apply to all estimates
                for estimate in estimates:
                    all_bonus_inputs[estimate.estimate_number] = template_inputs
                
                print(f"\n✓ Configuration will be applied to all {len(estimates)} estimates")
            else:
                # Individual configuration
                print(f"\n{'='*70}")
                print("GATHERING BONUS LINE INPUTS (individual mode)")
                print(f"{'='*70}\n")
                
                for estimate in estimates:
                    bonus_inputs = prompt_for_bonus_lines(estimate)
                    all_bonus_inputs[estimate.estimate_number] = bonus_inputs
        else:
            # Different structures, must configure individually
            print(f"✗ Estimates have different structures - individual configuration required")
            print()
            print(f"{'='*70}")
            print("GATHERING BONUS LINE INPUTS")
            print(f"{'='*70}\n")
            
            for estimate in estimates:
                bonus_inputs = prompt_for_bonus_lines(estimate)
                all_bonus_inputs[estimate.estimate_number] = bonus_inputs
    else:
        # Single estimate
        print(f"{'='*70}")
        print("GATHERING BONUS LINE INPUTS")
        print(f"{'='*70}\n")
        
        for estimate in estimates:
            bonus_inputs = prompt_for_bonus_lines(estimate)
            all_bonus_inputs[estimate.estimate_number] = bonus_inputs
    
    # Confirm separation intervals upfront (before browser automation)
    print(f"\n{'='*70}")
    print("CONFIRMING SEPARATION INTERVALS")
    print(f"{'='*70}\n")
    
    from separation_utils import confirm_separation_intervals
    
    # Check if we should offer batch mode for separation
    if len(estimates) > 1:
        print(f"Found {len(estimates)} estimates in this order")
        
        if detected_separation is not None:
            print(f"✓ PDF specifies separation: {detected_separation} minutes")
        else:
            print(f"No separation specified in PDF (will use default 15 minutes)")
        
        print()
        print("Separation Configuration:")
        print("  1. Apply same separation to ALL estimates (recommended)")
        print("  2. Configure each estimate individually")
        
        batch_separation = input("\nSelect option (1-2) [default: 1]: ").strip() or "1"
        
        if batch_separation == "1":
            # Configure once, apply to all
            separation_intervals = confirm_separation_intervals(
                detected_separation=detected_separation,
                order_type="TCAA",
                estimate_number=f"All {len(estimates)} estimates"
            )
            
            print(f"\n✓ Separation {separation_intervals} will apply to all {len(estimates)} estimates")
        else:
            # This shouldn't really happen for TCAA but handle it anyway
            # We'll still use the same separation for simplicity
            separation_intervals = confirm_separation_intervals(
                detected_separation=detected_separation,
                order_type="TCAA",
                estimate_number=estimates[0].estimate_number if len(estimates) == 1 else None
            )
    else:
        # Single estimate - normal flow
        separation_intervals = confirm_separation_intervals(
            detected_separation=detected_separation,
            order_type="TCAA",
            estimate_number=estimates[0].estimate_number
        )
    
    print(f"\n✓ Separation intervals confirmed: {separation_intervals}")
    
    print(f"\n{'='*70}")
    print("STARTING CONTRACT CREATION")
    print(f"{'='*70}\n")
    
    # Create Etere client
    etere = EtereClient(driver)
    
    # Create each contract
    success_count = 0
    for estimate in estimates:
        bonus_inputs = all_bonus_inputs[estimate.estimate_number]
        
        success = create_tcaa_contract(
            etere, 
            estimate, 
            bonus_inputs, 
            separation_intervals,
            order_code=order_code,
            description=description
        )
        
        if success:
            success_count += 1
            print(f"\n✓ Estimate {estimate.estimate_number} completed successfully")
        else:
            print(f"\n✗ Estimate {estimate.estimate_number} FAILED")
            
            # Ask user if they want to continue
            cont = input("\nContinue with remaining contracts? (y/n): ").strip().lower()
            if cont != 'y':
                break
        
        print()
    
    print(f"\n{'='*70}")
    print(f"TCAA ORDER PROCESSING COMPLETE")
    print(f"{'='*70}")
    print(f"Successfully created: {success_count}/{len(estimates)} contracts")
    
    return success_count == len(estimates)


def create_tcaa_contract(
    etere: EtereClient,
    estimate: TCAAEstimate,
    bonus_inputs: dict[int, BonusLineInput],
    separation_intervals: Tuple[int, int, int],
    order_code: Optional[str] = None,
    description: Optional[str] = None
) -> bool:
    """
    Create a single TCAA contract in Etere.
    
    This function:
    1. Creates contract header
    2. Adds all lines (paid and bonus)
    
    ALL Etere interactions happen through etere.method_name() calls.
    NO direct Selenium/driver code here.
    
    Args:
        etere: EtereClient instance
        estimate: TCAAEstimate object
        bonus_inputs: User inputs for bonus lines
        separation_intervals: Confirmed separation intervals (customer, event, order)
        order_code: Optional custom contract code (uses default if None)
        description: Optional custom description (uses default if None)
    
    Returns:
        True if successful
    """
    try:
        print(f"\n[TCAA] Creating contract for estimate {estimate.estimate_number}")
        
        # ═══════════════════════════════════════════════════════════════
        # CREATE CONTRACT HEADER
        # ═══════════════════════════════════════════════════════════════
        
        # Master market is already set to NYC by session
        # Individual lines will use market="SEA"
        
        # Use custom code if provided, otherwise default
        if not order_code:
            contract_code = f"TCAA Toyota {estimate.estimate_number}"
        else:
            contract_code = order_code
        
        # Use custom description if provided, otherwise default
        if not description:
            contract_description = f"Toyota SEA Est {estimate.estimate_number}"  # TCAA format
        else:
            contract_description = description
        
        # TCAA Notes format (2 lines):
        # Line 1: Description from IO header (e.g., "MAY26 Asian Cable")
        # Line 2: CPE format "CPE WWDTA/Product/EstimateNumber"
        # Client is always hardcoded as WWDTA (Western Washington Dealers Toyota Assoc)
        notes_line1 = estimate.description if hasattr(estimate, 'description') else "Asian Cable"
        notes_line2 = f"CPE WWDTA/Asian/{estimate.estimate_number}"
        notes_cpe = f"{notes_line1}\n{notes_line2}"
        
        contract_number = etere.create_contract_header(
            customer_id=75,  # TCAA Toyota
            code=contract_code,
            description=contract_description,
            # NO market parameter - master market already set to NYC by session
            contract_start=estimate.flight_start,
            contract_end=estimate.flight_end,
            customer_order_ref=None,  # TCAA doesn't use order numbers - leave blank
            notes=notes_cpe,  # Two-line CPE format
            charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
            invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header()
        )
        
        if not contract_number:
            print(f"[TCAA] ✗ Failed to create contract header")
            return False
        
        print(f"[TCAA] ✓ Contract created: {contract_number}")
        
        # ═══════════════════════════════════════════════════════════════
        # ADD LINES (Paid and Bonus)
        # ═══════════════════════════════════════════════════════════════
        
        line_count = 0
        
        for line_idx, line in enumerate(estimate.lines):
            
            # Determine if this is a bonus line
            if line.is_bonus():
                # BONUS LINE
                bonus_input = bonus_inputs.get(line_idx)
                if not bonus_input:
                    print(f"  WARNING: No input for bonus line {line_idx + 1}, skipping")
                    continue
                
                days = bonus_input.days
                time = bonus_input.time
                language = bonus_input.language
                spot_code = 10  # Bonus
                
                # Format description
                is_ros = False
                for ros_name, ros_def in ROS_OPTIONS.items():
                    if (ros_def['days'] == days and 
                        ros_def['time'] == time and 
                        ros_def['language'] == language):
                        desc = f"BNS {language} ROS"
                        is_ros = True
                        break
                
                if not is_ros:
                    desc = f"BNS {days} {time} {language}"
                
                # Get block prefixes
                block_prefixes = get_language_block_prefixes(
                    language,
                    bonus_input.hindi_punjabi_both
                )
                
            else:
                # PAID LINE
                days = line.days
                time = line.time
                language = extract_language_from_program(line.program)
                spot_code = 2  # Paid Commercial
                
                # Format description
                time_fmt = format_time_for_description(time)
                desc = f"{days} {time_fmt} {language}"
                
                # Get block prefixes (handle South Asian)
                if language == "South Asian":
                    # Get pre-gathered choice from bonus_inputs
                    sa_input = bonus_inputs.get(line_idx)
                    if sa_input and sa_input.hindi_punjabi_both:
                        choice = sa_input.hindi_punjabi_both
                    else:
                        # Fallback to Both if not found (shouldn't happen)
                        choice = "Both"
                    block_prefixes = get_language_block_prefixes(language, choice)
                else:
                    block_prefixes = get_language_block_prefixes(language)
            
            # Consolidate weekly distribution (groups identical consecutive weeks)
            ranges = EtereClient.consolidate_weeks_from_flight(
                line.weekly_spots,
                estimate.flight_start,
                estimate.flight_end
            )
            
            print(f"\n  Line {line_idx + 1}: {desc}")
            print(f"    Splits into {len(ranges)} Etere line(s)")
            
            # Parse time range using etere_client utility
            time_from, time_to = EtereClient.parse_time_range(time)
            
            # Apply Sunday 6-7a rule using etere_client utility
            adjusted_days, adjusted_day_count = EtereClient.check_sunday_6_7a_rule(days, time)
            
            # Create Etere line for each range
            for range_idx, range_data in enumerate(ranges, 1):
                line_count += 1
                
                print(f"    Creating line {line_count}: {range_data['start_date']} - {range_data['end_date']}")
                
                # Get spots per week (handle both int and list)
                spots_per_week = range_data['spots_per_week']
                if isinstance(spots_per_week, list):
                    spots_per_week = spots_per_week[0]
                
                # Add the line using etere_client!
                # max_daily_run is auto-calculated by etere_client from spots_per_week and days
                success = etere.add_contract_line(
                    contract_number=contract_number,
                    market="SEA",
                    start_date=range_data['start_date'],
                    end_date=range_data['end_date'],
                    days=adjusted_days,
                    time_from=time_from,
                    time_to=time_to,
                    description=desc,
                    spot_code=spot_code,
                    duration_seconds=line.duration,
                    total_spots=range_data['spots'],  # Total spots for this date range
                    spots_per_week=spots_per_week,
                    # max_daily_run is auto-calculated - no need to pass it!
                    rate=line.rate,
                    block_prefixes=block_prefixes,
                    separation_intervals=separation_intervals,  # Use confirmed intervals
                    is_bookend=False
                )
                
                if not success:
                    print(f"    ✗ Failed to add line {line_count}")
                    return False
        
        print(f"\n[TCAA] ✓ All {line_count} lines added successfully")
        return True
        
    except Exception as e:
        print(f"\n[TCAA] ✗ Error creating contract: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# TESTING FUNCTION
# ============================================================================

def test_tcaa_automation():
    """Test TCAA automation with a sample PDF."""
    from browser_automation.etere_session import EtereSession
    
    # Prompt for PDF path
    pdf_path = input("Enter path to TCAA PDF: ").strip()
    
    with EtereSession() as session:
        # Set market to SEA (Seattle) for TCAA
        session.set_market("SEA")
        
        # Process the order
        success = process_tcaa_order(session.driver, pdf_path)
        
        if success:
            print("\n✓ All contracts created successfully!")
        else:
            print("\n✗ Some contracts failed - review errors above")


if __name__ == "__main__":
    test_tcaa_automation()
