"""
Order Scanner - Scan directories for order PDFs.

Responsible for discovering and organizing order files from the filesystem.
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add src to path
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from business_logic.services.order_detection_service import detect_from_filename
from business_logic.services.pdf_order_detector import PDFOrderDetector
from domain.entities import Order
from domain.enums import OrderStatus, OrderType

# Per-file scan cache. Classifying a PDF means OCR'ing scanned pages and (for
# WorldLink) a vision read — ~seconds per file, re-run on every scan, and the
# web UI re-scans on every poll. Cache the classification keyed by file
# size+mtime so repeat scans are instant; a changed/new file misses and is
# re-detected. Bump the version to invalidate every entry after detection logic
# changes.
_SCAN_CACHE_VERSION = 2   # v2: BDR Type3 fingerprint now text-validated (RWNY misdetect fix)
_SCAN_CACHE_NAME = ".scan_cache.json"


def _file_sig(path: Path) -> str:
    """Cheap change-detector for a file: size + mtime (nanoseconds)."""
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _ai_fallback_enabled() -> bool:
    """Opt-in: when CTV_AI_FALLBACK is truthy, unrecognized orders route to the
    Claude AI extractor instead of being skipped. Off by default — unchanged
    behavior unless explicitly enabled."""
    return os.environ.get("CTV_AI_FALLBACK", "").strip().lower() in ("1", "true", "yes", "on")


def _charmaine_ai_enabled() -> bool:
    """Opt-in: when CTV_CHARMAINE_AI is truthy, route SINGLE-contract Charmaine
    orders to the Claude AI extractor instead of the Charmaine parser. The
    Charmaine parser stays in place as the safety net and the multi-contract
    path. Off by default."""
    return os.environ.get("CTV_CHARMAINE_AI", "").strip().lower() in ("1", "true", "yes", "on")


def _detect_xlsx_content(file_path: Path) -> OrderType:
    """
    Peek inside an XLSX file (first 10 rows) to detect order type by content.
    Used when filename-based detection returns UNKNOWN.
    """
    try:
        try:
            import openpyxl
        except ImportError:
            print(f"[WARN] openpyxl not installed — cannot detect XLSX order type for {file_path.name}. Run: pip install openpyxl")
            return OrderType.UNKNOWN
        wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(max_row=10):
            for cell in row:
                v = str(cell.value or "").upper()
                if "IMPRENTA" in v:
                    wb.close()
                    return OrderType.IMPRENTA
                if "PROSIO" in v:
                    wb.close()
                    return OrderType.PROSIO
                if "POLARIS" in v:
                    wb.close()
                    return OrderType.POLARIS
                if "SIERRA DONOR" in v:
                    wb.close()
                    return OrderType.SIERRADONOR
                if "3OLIVESMEDIA" in v:
                    wb.close()
                    return OrderType.THREEOLIVES
                if "AMERICAN COMMUNITY MEDIA" in v:
                    wb.close()
                    return OrderType.ACM
                # T&T Public Relations — cells may still carry the "Brentan
                # Media" template branding, so match either token.
                if "BRENTAN" in v or "T&T" in v:
                    wb.close()
                    return OrderType.TT
                # Crispin LLC media proposal (Bay Area AQMD) — the agency cell
                # carries "Crispin"; distinguishes it from the Allison & Partners
                # BAAQMD order which is a different Etere customer.
                if "CRISPIN" in v:
                    wb.close()
                    return OrderType.CRISPIN
        wb.close()
    except Exception as e:
        print(f"[WARN] Could not read {file_path.name}: {e}")
    return OrderType.UNKNOWN


class OrderScanner:
    """
    Scans directories for order PDF files.

    Discovers PDF files in the incoming directory and creates
    Order entities with detected types and customer information.
    """

    def __init__(
        self,
        detection_service: PDFOrderDetector,
        incoming_dir: Path
    ):
        """
        Initialize the order scanner.

        Args:
            detection_service: Service for detecting order types from PDF files
            incoming_dir: Directory to scan for orders
        """
        self._detection_service = detection_service
        self._incoming_dir = incoming_dir

    def _cache_file(self) -> Path:
        # Stored in incoming's parent so it never shows up in the scan listing.
        return self._incoming_dir.parent / _SCAN_CACHE_NAME

    def _load_scan_cache(self) -> dict:
        try:
            data = json.loads(self._cache_file().read_text(encoding="utf-8"))
            if data.get("version") == _SCAN_CACHE_VERSION:
                return data.get("entries", {})
        except Exception:
            pass  # missing/corrupt/old-version → start fresh
        return {}

    def _save_scan_cache(self, entries: dict) -> None:
        try:
            self._cache_file().write_text(
                json.dumps({"version": _SCAN_CACHE_VERSION, "entries": entries}),
                encoding="utf-8",
            )
        except Exception:
            pass  # caching is best-effort

    def scan_for_orders(self) -> list[Order]:
        """
        Scan the incoming directory for order PDFs.

        Automatically detects and splits multi-order PDFs (e.g., TCAA).

        Returns:
            List of Order entities, possibly multiple per PDF
        """
        print(f"[SCAN] Looking in: {self._incoming_dir.resolve()}")
        if not self._incoming_dir.exists():
            print(f"[SCAN] Directory does not exist: {self._incoming_dir.resolve()}")
            return []

        # Use iterdir() instead of glob() — glob() silently fails on Windows
        # for filenames containing special characters like &.
        _all = [f for f in self._incoming_dir.iterdir() if f.is_file()]
        if _all:
            print(f"[SCAN] Files found: {[f.name for f in _all]}")
        else:
            print("[SCAN] Directory is empty")

        orders = []

        pdf_files = sorted(f for f in _all if f.suffix.lower() == ".pdf")
        xml_files = sorted(f for f in _all if f.suffix.lower() == ".xml")

        # Per-file classification cache (see _SCAN_CACHE_VERSION). The AI-fallback
        # routing flags are part of the key so toggling them re-detects.
        _scan_cache = self._load_scan_cache()
        _fresh_cache: dict = {}
        _ai = _ai_fallback_enabled()
        _charm = _charmaine_ai_enabled()

        # Classify one PDF → (Order|None, fresh-cache-entry|None). No shared
        # mutable state is touched here (each detection call opens its own
        # file/vision handles and each WorldLink scan writes its own sidecar),
        # so this is safe to run concurrently across files below.
        def _classify_pdf(pdf_path: Path):
            try:
                # Fast path: unchanged file already classified → skip all OCR/vision.
                _sig = _file_sig(pdf_path)
                _hit = _scan_cache.get(pdf_path.name)
                if (_hit and _hit.get("sig") == _sig
                        and _hit.get("ai") == _ai and _hit.get("charm") == _charm):
                    order = Order(
                        pdf_path=pdf_path,
                        order_type=OrderType[_hit["order_type"]],
                        customer_name=_hit["customer_name"],
                        status=OrderStatus.PENDING,
                        estimate_number=_hit["estimate_number"],
                    )
                    return order, _hit

                # Check if this PDF contains multiple orders
                order_type, count = self._detection_service.detect_multi_order_pdf(pdf_path)

                # Opt-in AI fallback: route unrecognized PDFs to Claude extraction
                # instead of failing downstream. Off unless CTV_AI_FALLBACK is set.
                if order_type == OrderType.UNKNOWN and _ai_fallback_enabled():
                    order_type, count = OrderType.AI_FALLBACK, 1

                # Opt-in: route SINGLE-contract Charmaine orders to AI extraction.
                # Multi-contract Charmaine (count > 1) stays on the Charmaine parser
                # so a multi-estimate PDF is never silently merged into one contract.
                elif order_type == OrderType.CHARMAINE and count == 1 and _charmaine_ai_enabled():
                    order_type = OrderType.AI_FALLBACK

                if count > 1:
                    # Multi-order PDF — create ONE order; gather step handles estimate selection
                    print(f"\n[SCAN] {pdf_path.name}: Detected {count} estimates")

                    split_orders = self._detection_service.split_multi_order_pdf(pdf_path, order_type)

                    customer_name = "Unknown"
                    try:
                        first_text = split_orders[0].get('text', '') if split_orders else ''
                        customer_name = self._detection_service.extract_customer_name_from_text(
                            first_text, order_type
                        ) or "Unknown"
                    except Exception:
                        pass

                    order = Order(
                        pdf_path=pdf_path,
                        order_type=order_type,
                        customer_name=f"{customer_name} ({count} estimates)",
                        status=OrderStatus.PENDING,
                        estimate_number=None,
                    )
                else:
                    # Single order PDF
                    # FIXED: Also extract estimate number for single-order TCAA PDFs

                    # Extract customer name
                    customer_name = "Unknown"
                    try:
                        customer_name = self._detection_service.extract_customer_name(pdf_path, order_type)
                        if not customer_name:
                            customer_name = "Unknown"
                    except Exception:
                        pass

                    # Extract estimate number (especially important for TCAA)
                    estimate_number = None
                    if order_type == OrderType.TCAA:
                        try:
                            # Use split_multi_order_pdf to get estimate even for single orders
                            split_data = self._detection_service.split_multi_order_pdf(pdf_path, order_type)
                            if split_data and len(split_data) > 0:
                                estimate_number = split_data[0].get('estimate', 'Unknown')
                        except Exception:
                            pass

                    # Create order entity
                    order = Order(
                        pdf_path=pdf_path,
                        order_type=order_type,
                        customer_name=customer_name,
                        status=OrderStatus.PENDING,
                        estimate_number=estimate_number
                    )

                # Record the freshly-computed classification for next scan.
                _ot = order.order_type
                fresh = {
                    "sig": _sig,
                    "ai": _ai,
                    "charm": _charm,
                    "order_type": _ot.name if hasattr(_ot, "name") else str(_ot),
                    "customer_name": order.customer_name,
                    "estimate_number": order.estimate_number,
                }
                return order, fresh

            except Exception as e:
                # Log error but continue scanning
                print(f"Warning: Failed to process {pdf_path.name}: {e}")
                return None, None

        # Fan out across files. Classification is I/O-bound (vision API for
        # scanned WorldLink, OCR subprocess for other scans), so a small thread
        # pool turns a sequential first-drop scan (~N × per-file latency) into
        # roughly the slowest single file. Cache hits return instantly and cost
        # a pool slot only briefly. Results are reassembled in original file
        # order so the listing and scan cache are identical to the serial path.
        if pdf_files:
            _max_workers = min(8, len(pdf_files))
            with ThreadPoolExecutor(max_workers=_max_workers) as _ex:
                _results = list(_ex.map(_classify_pdf, pdf_files))
            for pdf_path, (order, fresh) in zip(pdf_files, _results):
                if order is not None:
                    orders.append(order)
                if fresh is not None:
                    _fresh_cache[pdf_path.name] = fresh

        # Find all AAAA SpotTV XML files
        for xml_path in xml_files:
            try:
                import xml.etree.ElementTree as ET
                tree = ET.parse(xml_path)
                root = ET.ElementTree(tree.getroot()).getroot()
                # Extract advertiser name from XML for display
                ns = "http://www.AAAA.org/schemas/spotTVCableProposal"
                advertiser = root.find(f".//{{{ns}}}Advertiser")
                customer_name = advertiser.get("name", "Unknown") if advertiser is not None else "Unknown"

                order = Order(
                    pdf_path=xml_path,
                    order_type=OrderType.XML,
                    customer_name=customer_name,
                    status=OrderStatus.PENDING,
                    estimate_number=None,
                )
                orders.append(order)
            except Exception as e:
                print(f"Warning: Failed to process {xml_path.name}: {e}")
                continue

        # Find JPG / PNG / XLSX files (detected by filename, not content)
        # Include both lowercase and uppercase extensions for Linux compatibility.
        # Deduplicate via set — case-insensitive filesystems (WSL2/NTFS) return
        # the same file for both *.xlsx and *.XLSX globs.
        # Skip ~$ Excel temp/lock files.
        _img_exts = {".jpg", ".jpeg", ".png", ".xlsx", ".xlsm"}
        image_xlsx_files = sorted(
            f for f in _all
            if f.suffix.lower() in _img_exts and not f.name.startswith("~$")
        )

        for file_path in image_xlsx_files:
            try:
                order_type = detect_from_filename(file_path.name)

                # For XLSX/XLSM files not identified by filename, peek inside for agency markers
                if order_type == OrderType.UNKNOWN and file_path.suffix.lower() in {".xlsx", ".xlsm"}:
                    order_type = _detect_xlsx_content(file_path)

                if order_type == OrderType.UNKNOWN and _ai_fallback_enabled():
                    order_type = OrderType.AI_FALLBACK
                elif order_type == OrderType.UNKNOWN:
                    continue

                # Extract customer name hint
                name_upper = file_path.stem.upper()
                if "LEXUS" in name_upper:
                    customer_name = "Lexus"
                elif order_type == OrderType.IMPRENTA:
                    # Extract client from filename: "Imprenta_<Client>_2026" → <Client>
                    _parts = [
                        p for p in file_path.stem.replace('_', ' ').split()
                        if p.lower() != 'imprenta' and not (len(p) == 4 and p.isdigit())
                    ]
                    customer_name = ' '.join(_parts) if _parts else "Unknown"
                elif order_type == OrderType.PROSIO:
                    customer_name = "AQMD"
                elif order_type == OrderType.EQC:
                    customer_name = "EQC"   # Emerald Queen Casino (agency TH Media)
                else:
                    customer_name = "Unknown"

                order = Order(
                    pdf_path=file_path,
                    order_type=order_type,
                    customer_name=customer_name,
                    status=OrderStatus.PENDING,
                    estimate_number=None,
                )
                orders.append(order)

            except Exception as e:
                print(f"Warning: Failed to process {file_path.name}: {e}")
                continue

        # Persist the PDF classification cache (only current files are kept, so
        # entries for deleted/processed files drop out automatically).
        self._save_scan_cache(_fresh_cache)

        return orders

    def get_pending_orders(self) -> list[Order]:
        """
        Get all pending orders (alias for scan_for_orders).

        Returns:
            List of pending orders
        """
        return self.scan_for_orders()

    def count_pending_orders(self) -> int:
        """
        Count the number of pending orders without creating Order objects.

        Returns:
            Number of PDF files in incoming directory
        """
        if not self._incoming_dir.exists():
            return 0

        _count_exts = {".pdf", ".xml", ".jpg", ".jpeg", ".png", ".xlsx", ".xlsm"}
        return sum(
            1 for f in self._incoming_dir.iterdir()
            if f.is_file() and f.suffix.lower() in _count_exts
        )
