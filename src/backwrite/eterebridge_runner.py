"""
EtereBridge integration module.

Runs EtereBridge's CSV processing pipeline (language detection, bill-code
generation, market standardisation, user-input stamping) and returns a
pandas DataFrame suitable for the Run Sheet tab.

Requires /home/scrib/dev/EtereBridge to be present with its config.ini.
If EtereBridge is unavailable, run_eterebridge_pipeline() returns None and
the caller falls back to the built-in transformer logic.
"""

import csv
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd

_EB_DIR = str(Path("/home/scrib/dev/EtereBridge").resolve())

# Add EtereBridge source dir to path so its modules can be imported.
# config_manager.py resolves config.ini relative to its own __file__, so it
# works correctly regardless of our working directory.
if _EB_DIR not in sys.path:
    sys.path.insert(0, _EB_DIR)

try:
    from config_manager import config_manager as _eb_config  # type: ignore[import]
    from file_processor import FileProcessor, transform_month_column  # type: ignore[import]
    from monetary_utils import standardize_monetary_columns  # type: ignore[import]
    from time_utils import transform_times  # type: ignore[import]

    _eb_app_config   = _eb_config.get_config()
    _file_processor  = FileProcessor(_eb_app_config)
    _AVAILABLE       = True
except Exception as _exc:
    _AVAILABLE = False
    logging.warning("[EtereBridge] Not available — will fall back to built-in pipeline: %s", _exc)


def is_available() -> bool:
    return _AVAILABLE


def get_language_options() -> list:
    """Return the list of valid language codes from EtereBridge config."""
    if not _AVAILABLE:
        return ["E", "C", "M", "V", "T", "K", "J", "SA", "Hm"]
    return list(_eb_app_config.language_options)


def get_language_details(csv_bytes: bytes) -> list:
    """
    Run language detection and return per-unique-description results.

    Returns [{"description": str, "lang": str, "count": int}, ...] sorted by
    (lang, description).  Returns [] if EtereBridge is unavailable.
    """
    if not _AVAILABLE:
        return []

    with tempfile.NamedTemporaryFile(
        suffix=".csv", prefix="eterebridge_lang_", delete=False
    ) as tmp:
        tmp.write(csv_bytes)
        tmp_path = tmp.name

    try:
        df = _file_processor.load_and_clean_data(tmp_path)
        _detected_counts, row_languages = _file_processor.detect_languages(df)

        unique: dict = {}
        for idx, desc in df["rowdescription"].items():
            if not isinstance(desc, str):
                desc = str(desc) if desc is not None else ""
            lang = row_languages.get(idx, "E")
            if desc not in unique:
                unique[desc] = {"lang": lang, "count": 0}
            unique[desc]["count"] += 1

        return sorted(
            [
                {"description": d, "lang": info["lang"], "count": info["count"]}
                for d, info in unique.items()
            ],
            key=lambda x: (x["lang"], x["description"]),
        )
    except Exception as exc:
        logging.warning("[EtereBridge] Language details failed: %s", exc)
        return []
    finally:
        os.unlink(tmp_path)


def run_eterebridge_pipeline(
    csv_bytes: bytes,
    user_inputs: dict,
) -> Optional[pd.DataFrame]:
    """
    Run EtereBridge's data processing pipeline on an Etere placement CSV.

    Returns a DataFrame with EtereBridge's 29-column output structure, or
    None if EtereBridge is unavailable or an error occurs.

    Expected user_inputs keys (same as the backwrite web form):
        sales_person, billing_type, revenue_type, agency_flag, agency_fee,
        estimate, contract, affidavit,
        gross_up_rates        (optional dict {str(rounded_gross): str(net_rate)})
        language_corrections  (optional dict {description: lang_code})
    """
    if not _AVAILABLE:
        return None

    # Write CSV to a temp file; EtereBridge's load_and_clean_data requires a path.
    with tempfile.NamedTemporaryFile(
        suffix=".csv", prefix="eterebridge_", delete=False
    ) as tmp:
        tmp.write(csv_bytes)
        tmp_path = tmp.name

    try:
        # 1. Extract bill-code parts from CSV row 2 (agency col 1, venue col 6)
        tb180, tb171 = _extract_header_values(tmp_path)

        # 2. Load and clean: skip 3 header rows, rename Etere columns
        df = _file_processor.load_and_clean_data(tmp_path)

        # 3. Language detection — auto, no stdin prompt
        _detected_counts, row_languages = _file_processor.detect_languages(df)

        # 3a. Apply user corrections over auto-detected languages
        language_corrections = user_inputs.get("language_corrections") or {}
        if language_corrections:
            for idx, desc in df["rowdescription"].items():
                if isinstance(desc, str) and desc in language_corrections:
                    row_languages[idx] = language_corrections[desc]

        # 4. Transformations: bill code, market replacements, gross rate, length
        df = _file_processor.apply_transformations(df, tb180, tb171)
        df = standardize_monetary_columns(df)
        df = transform_times(df)

        # 5. Optional gross-up: replace rounded Etere rates with full-precision values
        agency_fee     = float(user_inputs.get("agency_fee") or 0.15)
        gross_up_rates = user_inputs.get("gross_up_rates") or {}
        if gross_up_rates and user_inputs.get("agency_flag") == "Agency" and (1 - agency_fee) > 0:
            rate_map = {
                float(k): float(v) / (1 - agency_fee)
                for k, v in gross_up_rates.items()
            }
            if rate_map:
                df["Gross Rate"] = df["Gross Rate"].apply(
                    lambda r: rate_map.get(float(r), r)
                )

        # 6. Stamp user inputs onto every row (skips the interactive verify_languages step)
        language_dict = row_languages.to_dict() if not row_languages.empty else {}
        df = _apply_user_inputs(
            df,
            billing_type = user_inputs.get("billing_type", "Broadcast"),
            revenue_type = user_inputs.get("revenue_type", "Internal Ad Sales"),
            agency_flag  = user_inputs.get("agency_flag",  "Agency"),
            sales_person = user_inputs.get("sales_person", ""),
            affidavit    = user_inputs.get("affidavit",    "Y"),
            estimate     = user_inputs.get("estimate",     ""),
            contract     = user_inputs.get("contract",     ""),
            language     = language_dict,
        )

        # 7. Compute Month column (Calendar vs. Broadcast logic)
        df = transform_month_column(df)

        return df

    except Exception as exc:
        logging.error("[EtereBridge] Pipeline error: %s", exc, exc_info=True)
        return None
    finally:
        os.unlink(tmp_path)


def get_language_counts(csv_bytes: bytes) -> dict:
    """
    Run language detection only and return a {lang_code: count} dict.
    Used by the preview endpoint to show detected languages before generation.
    Returns {} if EtereBridge is unavailable.
    """
    if not _AVAILABLE:
        return {}

    with tempfile.NamedTemporaryFile(
        suffix=".csv", prefix="eterebridge_lang_", delete=False
    ) as tmp:
        tmp.write(csv_bytes)
        tmp_path = tmp.name

    try:
        df = _file_processor.load_and_clean_data(tmp_path)
        detected_counts, _ = _file_processor.detect_languages(df)
        return detected_counts
    except Exception as exc:
        logging.warning("[EtereBridge] Language detection failed: %s", exc)
        return {}
    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_header_values(file_path: str) -> tuple[str, str]:
    """
    Read CSV row 2 (zero-indexed row 1) to extract bill-code parts.
    Column 1 = agency/client name, column 6 = venue/site name.
    Matches EtereBridge's extract_header_values() logic exactly.
    """
    try:
        with open(file_path, "r") as f:
            f.readline()           # skip row 1 (column labels)
            second_line = f.readline().strip()
        if not second_line:
            return "", ""
        reader = csv.reader([second_line])
        parts  = next(reader)
        first  = parts[0].strip() if len(parts) > 0 else ""
        second = parts[5].strip() if len(parts) > 5 else ""
        return first, second
    except Exception as exc:
        logging.warning("[EtereBridge] Header extraction failed: %s", exc)
        return "", ""


def _apply_user_inputs(
    df: pd.DataFrame,
    billing_type: str,
    revenue_type: str,
    agency_flag:  str,
    sales_person: str,
    affidavit:    str,
    estimate:     str,
    contract:     str,
    language:     dict,
) -> pd.DataFrame:
    """
    Stamp user-provided metadata onto every row and reorder columns to
    EtereBridge's final_columns order.  Mirrors EtereBridge's apply_user_inputs()
    but without the WorldLink branch and without calling verify_languages().
    """
    df["Billing Type"] = billing_type
    df["Revenue Type"] = revenue_type
    df["Agency?"]      = agency_flag
    df["Sales Person"] = sales_person
    df["Affidavit?"]   = affidavit
    df["Estimate"]     = estimate
    df["Contract"]     = contract
    df["Lang."]        = df.index.map(language).fillna("E")

    df["Type"] = df["Gross Rate"].apply(
        lambda r: "BNS" if (pd.isna(r) or float(r) == 0) else "COM"
    )

    # Ensure every final column exists before reordering
    for col in _eb_app_config.final_columns:
        if col not in df.columns:
            df[col] = None

    return df[_eb_app_config.final_columns]
