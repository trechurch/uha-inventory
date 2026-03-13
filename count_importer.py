# ──────────────────────────────────────────────────────────────────────────────
#  count_importer.py  —  PAC / myOrders Count Sheet Parser
#  Format: export(24) style — two rows per item (Case row + Each row)
#
#  PARSING RULES (derived from real count sheets):
#
#  ROW PAIRING
#    Every item appears exactly twice — same Seq, same Description.
#    Row 1: Case  — qty, price, total for the case unit
#    Row 2: Each  — qty, price, total for the each unit
#    Key: Item Description || Pack Type (uppercase, double-pipe)
#
#  MATH (ground truth — always holds)
#    each_qty  * each_price  = Total Price  (each row)
#    case_qty  * case_price  = Total Price  (case row)
#    If Total Price > 0 and price > 0:  qty = Total Price / price  (safer than raw qty)
#
#  SECTION CLASSIFICATION (by first item description in section)
#    LIQUOR        — description starts with 3-char spirit prefix (BRB CRD GIN …)
#    NON-CHARGEABLE — description matches any NON_CHARGEABLE_KEYWORDS keyword
#    CHARGEABLE    — everything else
#
#  LOCATION BOUNDARY STATE MACHINE
#    CHARGEABLE    → seq reset → NON-CHARGEABLE  (same location)
#    NON-CHARGEABLE → seq reset → classify next section:
#                       LIQUOR      → same location
#                       CHARGEABLE  → NEW LOCATION
#    LIQUOR        → seq reset → always NEW LOCATION
# ──────────────────────────────────────────────────────────────────────────────

import re
import io
import uuid
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple

import pandas as pd

__version__ = "4.0.0"


# ──────────────────────────────────────────────────────────────────────────────
#  CLASSIFICATION CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

LIQUOR_PREFIXES = {'BRB', 'CRD', 'GIN', 'RUM', 'SCT', 'TEQ', 'VOD', 'WSK'}

NON_CHARGEABLE_KEYWORDS = {
    # BIB / beverage supply
    'BIB', 'SYRUP', 'BAG IN BOX',
    # Beverage non-chargeable
    'LID', 'STRAW', 'SLEEVE',
    # Food ingredients & condiments
    'MUSTARD', 'KETCHUP', 'RELISH', 'JALAPENO', 'PEPPER', 'CHILI',
    'SAUCE CHEESE', 'CHEESE SAUCE', 'CHEESE CHDR', 'CHEESE SHARP',
    'BUN', 'ROLL',
    'CHIP CORN', 'CHIP TORTILLA WHT',
    # Food prep & sanitation
    'GLOVE', 'HAIRNET', 'SOAP', 'SANITIZER',
    'LINER TRASH', 'TRASH BAG', 'TRASH LINER',
    'PAN COATING', 'DRYWAX', 'WRAP SAND',
    'NAPKIN', 'FORK', 'KNIFE', 'SPOON',
    'CUP PORTION', 'PORTION CUP',
    # Paper / packaging
    'FOIL', 'PLASTIC WRAP',
    # Tea / coffee
    'TEA BAG', 'COFFEE', 'CREAMER', 'SUGAR',
}

# UOM strings that mean CASE
CASE_UOMS = {'CASE', 'CS', 'CTN', 'CA', 'CT', 'BOX', 'BX'}

# UOM strings that mean EACH
EACH_UOMS = {'EACH', 'EA', 'EA.', 'E', '1', 'BAG', 'BG'}


# ──────────────────────────────────────────────────────────────────────────────
#  DATA CLASSES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CountRecord:
    """One fully-resolved item from the count sheet."""
    seq:              str
    item_description: str
    pack_type:        str
    item_key:         str          # DESCRIPTION||PACK_TYPE

    # Case values
    case_uom:         str
    last_qty_case:    float
    count_qty_case:   float
    price_case:       float

    # Each values
    each_uom:         str
    last_qty_each:    float
    count_qty_each:   float
    price_each:       float

    # Totals from file (ground truth)
    total_price_case: float
    total_price_each: float

    # Classification
    location_num:     int
    classification:   str          # CHARGEABLE | NON-CHARGEABLE | LIQUOR
    is_chargeable:    bool

    # Derived
    total_price:      float = 0.0  # case total + each total
    verified:         bool  = True  # math check passed


@dataclass
class ParseResult:
    records:       List[CountRecord]
    location_count: int
    section_count:  int
    row_count:      int
    skipped_rows:   int
    warnings:       List[str]
    math_errors:    List[str]


# ──────────────────────────────────────────────────────────────────────────────
#  CLASSIFICATION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _classify(description: str) -> str:
    desc = description.strip().upper()
    if desc[:3] in LIQUOR_PREFIXES:
        return 'LIQUOR'
    for kw in NON_CHARGEABLE_KEYWORDS:
        if kw in desc:
            return 'NON-CHARGEABLE'
    return 'CHARGEABLE'


def _is_case_uom(uom: str) -> bool:
    return uom.strip().upper() in CASE_UOMS


def _normalize_uom(uom: str) -> str:
    u = uom.strip().upper()
    if u in CASE_UOMS:
        return 'CASE'
    if u in EACH_UOMS:
        return 'EACH'
    return u


# ──────────────────────────────────────────────────────────────────────────────
#  VALUE CLEANERS
# ──────────────────────────────────────────────────────────────────────────────

def _clean_qty(val) -> float:
    """Parse '8.00 Case', '0.75', '1.5 CS' → float."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    s = str(val).strip()
    # Extract leading number
    m = re.match(r'^\s*(-?\d+\.?\d*)', s)
    return float(m.group(1)) if m else 0.0


def _clean_price(val) -> float:
    """Parse '$16.56', '16.56', '' → float."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    s = str(val).strip().replace('$', '').replace(',', '')
    try:
        return float(s)
    except ValueError:
        return 0.0


def _build_key(description: str, pack_type: str) -> str:
    desc = str(description or '').strip().upper()
    pack = str(pack_type or '').strip().upper()
    return f"{desc}||{pack}" if pack else f"{desc}||CASE"


# ──────────────────────────────────────────────────────────────────────────────
#  MATH VERIFIER
# ──────────────────────────────────────────────────────────────────────────────

def _verify_math(qty: float, price: float, total: float,
                 label: str) -> Tuple[float, bool, str]:
    """
    Ground truth: qty * price = total.
    If total > 0 and price > 0, recalculate qty from total (more reliable).
    Returns (verified_qty, passed, warning_msg).
    """
    if price <= 0:
        return qty, True, ''
    if total > 0:
        calc_qty  = round(total / price, 4)
        calc_total = round(qty * price, 2)
        if abs(calc_total - total) > 0.02:
            # Trust total/price over raw qty
            return calc_qty, False, (
                f"{label}: raw qty={qty} × price=${price} = ${calc_total} "
                f"≠ file total ${total} → using {calc_qty}"
            )
        return qty, True, ''
    return qty, True, ''


# ──────────────────────────────────────────────────────────────────────────────
#  CORE PARSER
# ──────────────────────────────────────────────────────────────────────────────

class CountSheetParser:
    """
    Parses export(24)-style count CSVs.
    Columns: Seq | Pack Type | Last Inventory Qty | Inv Count |
             Item Description | UOM | Price | Total Price
    """

    REQUIRED_COLS = {
        'seq', 'pack type', 'last inventory qty', 'inv count',
        'item description', 'uom', 'price', 'total price',
    }

    def parse(self, content: bytes, filename: str = '') -> ParseResult:
        warnings   = []
        math_errors = []

        # ── Load DataFrame ────────────────────────────────────────────────────
        try:
            df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
        except Exception as e:
            return ParseResult([], 0, 0, 0, 0, [f"CSV read error: {e}"], [])

        # Normalize column names
        df.columns = [c.strip().lower() for c in df.columns]

        missing = self.REQUIRED_COLS - set(df.columns)
        if missing:
            return ParseResult([], 0, 0, 0, 0,
                               [f"Missing columns: {', '.join(sorted(missing))}"], [])

        df = df.reset_index(drop=True)
        total_rows = len(df)

        # ── Group raw rows into (seq, description) pairs ──────────────────────
        # Each item appears exactly twice: case row then each row (same seq + desc)
        groups: Dict[Tuple[str,str,str], List[dict]] = {}
        order:  List[Tuple[str,str,str]] = []
        skipped = 0
        row_index = 0   # tracks position for state machine

        raw_items = []  # list of (row_index_of_case_row, case_row_dict, each_row_dict)

        i = 0
        rows = df.to_dict('records')
        while i < len(rows):
            r = rows[i]
            seq  = str(r.get('seq', '')).strip()
            desc = str(r.get('item description', '')).strip()
            uom  = str(r.get('uom', '')).strip()

            if not seq or not desc:
                skipped += 1
                i += 1
                continue

            # Peek at next row
            if i + 1 < len(rows):
                r2   = rows[i + 1]
                seq2 = str(r2.get('seq', '')).strip()
                desc2 = str(r2.get('item description', '')).strip()

                if seq2 == seq and desc2 == desc:
                    # Pair found — determine which is case and which is each
                    uom2 = str(r2.get('uom', '')).strip()
                    if _is_case_uom(uom):
                        case_row, each_row = r, r2
                    elif _is_case_uom(uom2):
                        case_row, each_row = r2, r
                    else:
                        # Neither is clearly a case — treat first as case
                        case_row, each_row = r, r2
                        warnings.append(
                            f"Seq {seq} '{desc}': neither UOM looks like a case "
                            f"('{uom}' / '{uom2}') — assuming first row is case"
                        )
                    raw_items.append((i, case_row, each_row))
                    i += 2
                    continue

            # Orphan row — single entry, no pair
            warnings.append(f"Seq {seq} '{desc}': orphan row (no matching pair) — skipped")
            skipped += 1
            i += 1

        # ── State machine — classify sections and assign locations ─────────────
        #
        # State: current classification within a location
        # Transitions on seq reset (seq resets to a value ≤ prev seq):
        #   CHARGEABLE     → NON-CHARGEABLE  (same location)
        #   NON-CHARGEABLE → LIQUOR          (same location, if next section = LIQUOR)
        #   NON-CHARGEABLE → CHARGEABLE      (NEW LOCATION)
        #   LIQUOR         → anything        (NEW LOCATION)

        records      = []
        location_num = 1
        section_count = 0
        current_section = 'CHARGEABLE'
        prev_seq_num    = -1
        section_first_desc = None
        section_decided = False

        for idx, (row_idx, case_row, each_row) in enumerate(raw_items):
            seq_str  = str(case_row.get('seq', '')).strip()
            desc     = str(case_row.get('item description', '')).strip()
            pack     = str(case_row.get('pack type', '')).strip()

            try:
                seq_num = int(float(seq_str))
            except ValueError:
                seq_num = 0

            # ── Detect seq reset ───────────────────────────────────────────
            is_reset = (seq_num <= prev_seq_num) and (prev_seq_num > 0)

            if is_reset:
                # Determine what the next section is by looking at this item
                next_class = _classify(desc)
                section_count += 1

                if current_section == 'CHARGEABLE':
                    if next_class == 'NON-CHARGEABLE':
                        current_section = 'NON-CHARGEABLE'   # same location
                    else:
                        location_num   += 1                   # new location
                        current_section = 'CHARGEABLE'

                elif current_section == 'NON-CHARGEABLE':
                    if next_class == 'LIQUOR':
                        current_section = 'LIQUOR'            # same location
                    else:
                        location_num   += 1                   # new location
                        current_section = 'CHARGEABLE'

                elif current_section == 'LIQUOR':
                    location_num   += 1                       # always new location
                    current_section = 'CHARGEABLE'

                section_first_desc = desc
                section_decided    = True
            else:
                if prev_seq_num < 0:
                    # Very first item
                    current_section    = _classify(desc)
                    section_first_desc = desc
                    section_count      = 1

            prev_seq_num = seq_num

            # ── Parse values ───────────────────────────────────────────────
            case_uom  = _normalize_uom(str(case_row.get('uom', '')))
            each_uom  = _normalize_uom(str(each_row.get('uom', '')))

            last_case = _clean_qty(case_row.get('last inventory qty'))
            cnt_case  = _clean_qty(case_row.get('inv count'))
            pr_case   = _clean_price(case_row.get('price'))
            tot_case  = _clean_price(case_row.get('total price'))

            last_each = _clean_qty(each_row.get('last inventory qty'))
            cnt_each  = _clean_qty(each_row.get('inv count'))
            pr_each   = _clean_price(each_row.get('price'))
            tot_each  = _clean_price(each_row.get('total price'))

            # ── Math verification ──────────────────────────────────────────
            label = f"Loc{location_num} Seq{seq_str} '{desc}'"
            cnt_case_v, case_ok, case_warn = _verify_math(
                cnt_case, pr_case, tot_case, f"{label} CASE"
            )
            cnt_each_v, each_ok, each_warn = _verify_math(
                cnt_each, pr_each, tot_each, f"{label} EACH"
            )
            if case_warn: math_errors.append(case_warn)
            if each_warn: math_errors.append(each_warn)

            total_price = round(tot_case + tot_each, 2)

            record = CountRecord(
                seq              = seq_str,
                item_description = desc,
                pack_type        = pack,
                item_key         = _build_key(desc, pack),
                case_uom         = case_uom,
                last_qty_case    = last_case,
                count_qty_case   = cnt_case_v,
                price_case       = pr_case,
                each_uom         = each_uom,
                last_qty_each    = last_each,
                count_qty_each   = cnt_each_v,
                price_each       = pr_each,
                total_price_case = tot_case,
                total_price_each = tot_each,
                location_num     = location_num,
                classification   = current_section,
                is_chargeable    = (current_section != 'NON-CHARGEABLE'),
                total_price      = total_price,
                verified         = case_ok and each_ok,
            )
            records.append(record)

        return ParseResult(
            records        = records,
            location_count = location_num,
            section_count  = section_count,
            row_count      = total_rows,
            skipped_rows   = skipped,
            warnings       = warnings,
            math_errors    = math_errors,
        )


# ──────────────────────────────────────────────────────────────────────────────
#  AGGREGATOR
#  Collapses duplicate item keys across locations into per-item totals
#  and per-location breakdowns.
# ──────────────────────────────────────────────────────────────────────────────

def aggregate(records: List[CountRecord]) -> Dict:
    """
    Returns:
      by_key:      {item_key: {total_each, total_case, total_value, locations: [...]}}
      by_location: {location_num: [CountRecord]}
      summary:     {location_num: {chargeable_value, non_chargeable_value, liquor_value, total}}
    """
    by_key: Dict[str, dict] = {}
    by_location: Dict[int, List[CountRecord]] = {}
    summary: Dict[int, dict] = {}

    for r in records:
        # by_key
        if r.item_key not in by_key:
            by_key[r.item_key] = {
                'item_description': r.item_description,
                'pack_type':        r.pack_type,
                'price_each':       r.price_each,
                'price_case':       r.price_case,
                'total_each':       0.0,
                'total_case':       0.0,
                'total_value':      0.0,
                'locations':        [],
            }
        entry = by_key[r.item_key]
        entry['total_each']  += r.count_qty_each
        entry['total_case']  += r.count_qty_case
        entry['total_value'] += r.total_price
        entry['locations'].append(r.location_num)

        # by_location
        by_location.setdefault(r.location_num, []).append(r)

        # summary
        if r.location_num not in summary:
            summary[r.location_num] = {
                'chargeable_value':     0.0,
                'non_chargeable_value': 0.0,
                'liquor_value':         0.0,
                'total':                0.0,
            }
        s = summary[r.location_num]
        s['total'] += r.total_price
        if r.classification == 'CHARGEABLE':
            s['chargeable_value'] += r.total_price
        elif r.classification == 'NON-CHARGEABLE':
            s['non_chargeable_value'] += r.total_price
        elif r.classification == 'LIQUOR':
            s['liquor_value'] += r.total_price

    return {
        'by_key':       by_key,
        'by_location':  by_location,
        'summary':      summary,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  DB WRITER
# ──────────────────────────────────────────────────────────────────────────────

def commit_count(
    records:    List[CountRecord],
    db,
    count_date: str,
    imported_by: str,
    count_type:  str = 'complete',
    cost_center: str = 'default',
) -> Dict:
    """
    Write count records to the database.
    Returns result summary dict.
    """
    import_id = f"CNT-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    results = {
        'import_id':     import_id,
        'items_updated': 0,
        'items_created': 0,
        'items_skipped': 0,
        'errors':        [],
        'total_value':   0.0,
    }

    variance_rows = []

    for r in records:
        try:
            db_item = db.get_item(r.item_key)
            prev_qty = float(db_item['quantity_on_hand']) if db_item else 0.0
            new_qty  = r.count_qty_each  # canonical qty stored as each units

            if db_item:
                ok = db.update_quantity_from_count(
                    r.item_key, new_qty, import_id, changed_by=imported_by
                )
                if ok:
                    results['items_updated'] += 1
                else:
                    results['errors'].append(f"Update failed: {r.item_key}")
                    results['items_skipped'] += 1
            else:
                # Create minimal record from count data
                new_item = {
                    'key':               r.item_key,
                    'description':       r.item_description,
                    'pack_type':         r.pack_type,
                    'cost':              r.price_each,
                    'quantity_on_hand':  new_qty,
                    'is_chargeable':     r.is_chargeable,
                    'cost_center':       cost_center,
                    'status_tag':        '📦 Count Import',
                }
                db.add_item(new_item, changed_by=imported_by)
                results['items_created'] += 1

            results['total_value'] += r.total_price

            # Variance detail row
            variance_rows.append({
                'import_id':        import_id,
                'location':         f"Location {r.location_num}",
                'seq':              r.seq,
                'item_key':         r.item_key,
                'item_description': r.item_description,
                'pack_type':        r.pack_type,
                'prev_qty_each':    prev_qty,
                'new_qty_each':     new_qty,
                'count_qty_case':   r.count_qty_case,
                'count_qty_each':   r.count_qty_each,
                'price_each':       r.price_each,
                'variance_each':    new_qty - prev_qty,
                'variance_value':   round((new_qty - prev_qty) * r.price_each, 2),
                'is_flagged':       False,
                'flag_reason':      '',
            })

        except Exception as e:
            results['errors'].append(f"{r.item_key}: {e}")
            results['items_skipped'] += 1

    # Bulk write variance detail
    if variance_rows:
        try:
            db.save_count_variance_records(variance_rows)
        except Exception as e:
            results['errors'].append(f"Variance detail write failed: {e}")

    # Log the import
    try:
        db.log_count_import(
            import_id        = import_id,
            source_file      = '',
            file_format      = 'csv',
            data_layout      = 'export24',
            count_type       = count_type,
            count_date       = count_date,
            cost_center      = cost_center,
            imported_by      = imported_by,
            total_items      = len(records),
            items_changed    = results['items_updated'] + results['items_created'],
            items_flagged    = 0,
            total_prev_value = 0.0,
            total_new_value  = results['total_value'],
            variance_value   = results['total_value'],
        )
    except Exception as e:
        results['errors'].append(f"Import log failed: {e}")

    return results


# ──────────────────────────────────────────────────────────────────────────────
#  STREAMLIT PAGE  (called from app.py)
# ──────────────────────────────────────────────────────────────────────────────

def render_count_import_page(db, get_changed_by_fn):
    import streamlit as st

    st.title("📋 Count Import")
    st.caption("Upload a PAC / myOrders count sheet CSV.")

    # ── Upload ────────────────────────────────────────────────────────────────
    uploaded = st.file_uploader(
        "Drop count sheet CSV here",
        type=["csv"],
        label_visibility="collapsed",
    )
    if not uploaded:
        return

    content = uploaded.read()

    # ── Parse ─────────────────────────────────────────────────────────────────
    parser = CountSheetParser()
    with st.spinner("Parsing..."):
        result = parser.parse(content, uploaded.name)

    if not result.records:
        for w in result.warnings:
            st.error(w)
        return

    # ── Parse summary ─────────────────────────────────────────────────────────
    st.success(
        f"✅ Parsed **{len(result.records)}** items across "
        f"**{result.location_count}** location(s) "
        f"({result.skipped_rows} rows skipped)"
    )

    if result.warnings:
        with st.expander(f"⚠️ {len(result.warnings)} warning(s)"):
            for w in result.warnings:
                st.caption(w)

    if result.math_errors:
        with st.expander(f"🔢 {len(result.math_errors)} math correction(s)"):
            for e in result.math_errors:
                st.caption(e)

    # ── Aggregation & preview ─────────────────────────────────────────────────
    agg = aggregate(result.records)

    st.markdown("---")
    st.subheader("📍 Location Summary")

    summary_rows = []
    for loc_num, s in sorted(agg['summary'].items()):
        summary_rows.append({
            'Location':        f"Location {loc_num}",
            'Items':           len(agg['by_location'][loc_num]),
            'Chargeable $':    f"${s['chargeable_value']:,.2f}",
            'Non-Chargeable $':f"${s['non_chargeable_value']:,.2f}",
            'Liquor $':        f"${s['liquor_value']:,.2f}",
            'Total $':         f"${s['total']:,.2f}",
        })
    st.dataframe(
        __import__('pandas').DataFrame(summary_rows),
        use_container_width=True, hide_index=True
    )

    # Grand total
    grand_total = sum(s['total'] for s in agg['summary'].values())
    st.metric("Grand Total Inventory Value", f"${grand_total:,.2f}")

    # ── Per-location drill-down ───────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🔍 Item Detail")

    loc_options = [f"Location {n}" for n in sorted(agg['by_location'].keys())]
    selected_loc = st.selectbox("View location", ["All"] + loc_options)

    if selected_loc == "All":
        view_records = result.records
    else:
        loc_num = int(selected_loc.split()[-1])
        view_records = agg['by_location'][loc_num]

    class_filter = st.multiselect(
        "Filter by classification",
        ["CHARGEABLE", "NON-CHARGEABLE", "LIQUOR"],
        default=["CHARGEABLE", "NON-CHARGEABLE", "LIQUOR"],
    )
    view_records = [r for r in view_records if r.classification in class_filter]

    import pandas as pd
    detail_df = pd.DataFrame([{
        'Loc':          r.location_num,
        'Seq':          r.seq,
        'Description':  r.item_description,
        'Pack Type':    r.pack_type,
        'Class':        r.classification,
        'Last Case':    r.last_qty_case,
        'Count Case':   r.count_qty_case,
        'Case Price':   f"${r.price_case:.2f}",
        'Last Each':    r.last_qty_each,
        'Count Each':   r.count_qty_each,
        'Each Price':   f"${r.price_each:.2f}",
        'Total $':      f"${r.total_price:.2f}",
        '✓':            '✅' if r.verified else '⚠️',
    } for r in view_records])

    st.caption(f"{len(detail_df)} items")
    st.dataframe(detail_df, use_container_width=True, hide_index=True)

    # ── Commit ────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("✅ Commit Count")

    col1, col2 = st.columns(2)
    with col1:
        count_date = st.date_input("Count date", value=__import__('datetime').date.today())
    with col2:
        count_type = st.radio("Count type", ["complete", "partial"], horizontal=True)

    confirmed = st.checkbox(
        f"Commit {len(result.records)} items from {result.location_count} location(s) "
        f"— total value ${grand_total:,.2f}"
    )

    if st.button("✅ Commit", type="primary", disabled=not confirmed):
        with st.spinner("Writing to database..."):
            res = commit_count(
                records     = result.records,
                db          = db,
                count_date  = str(count_date),
                imported_by = get_changed_by_fn(),
                count_type  = count_type,
            )
        st.success(
            f"Done — {res['items_updated']} updated, "
            f"{res['items_created']} created, "
            f"{res['items_skipped']} skipped"
        )
        if res['errors']:
            with st.expander(f"⚠️ {len(res['errors'])} error(s)"):
                for e in res['errors']:
                    st.caption(e)
