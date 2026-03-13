# ──────────────────────────────────────────────────────────────────────────────
#  count_importer.py  —  Multi-Format Count Sheet Importer  v5.0.0
#
#  SUPPORTED FORMATS
#  ─────────────────────────────────────────────────────────────────────────────
#  FMT_A  CSV / XLSX — no header block, two rows per item, Seq+Desc key,
#                      no location column (single-location concessions style)
#                      Columns: Seq | Item Description | Inv Count | Pack Type
#                               | Price | UOM | Last Inventory Qty | Total Price
#
#  FMT_B  CSV / XLSX — 4-row header block, "Grouped by: Classification" header,
#                      location explicit in column 0 with optional "->SubClass",
#                      two rows per item, no Seq, no Last Inventory Qty
#                      Columns: Location | Item Description | UOM | Pack Type
#                               | Price | Inv Count | Total Price
#
#  FMT_C  XLSX       — 4-row header block, "Grouped by: Classification >> …"
#                      header (adds Category / DC / Mfg breadcrumb),
#                      location in col 0 as "Loc->Sub >> Cat >> DC >> Mfg",
#                      two rows per item, has Seq + Last Inventory Qty
#                      Columns: Location | Item Description | UOM | Pack Type
#                               | Price | Last Inventory Qty | Seq | Inv Count
#                               | Total Price
#
#  FMT_D  XLSX       — 4-row header block, "Grouped by: Classification" header,
#                      SINGLE row per item, case/each values slash-delimited
#                      inside cells: Price="$x/$y"  Qty="x UOM/y EA"
#                      Columns: Location | Seq | Item Description | Price
#                               | Last Inventory Qty | Inv Count | Total Price
#                               | UOM | Pack Type
#
#  FMT_E  PDF        — image-based, no extractable text → OCR required (future)
#
#  AUTO-DETECTOR CONFIDENCE SCORING
#  ─────────────────────────────────────────────────────────────────────────────
#  Samples file structure, scores each format 0-100, picks highest.
#  If best score < 40 → UNKNOWN, flagged for user review.
#  If score 40-70 → UNCERTAIN, auto-selects but warns user.
#  If score > 70  → CONFIDENT, proceeds silently.
#
#  CLASSIFICATION
#  ─────────────────────────────────────────────────────────────────────────────
#  Concessions (FMT_A): CHARGEABLE / NON-CHARGEABLE / LIQUOR  (3-bucket)
#  Catering (FMT_B/C/D): native "->SubClass" label kept verbatim
#                         (Beer, Liquor, Wine, Mixers, Pantry, Dry Storage …)
#
#  MATH GROUND TRUTH
#  ─────────────────────────────────────────────────────────────────────────────
#  each_qty * each_price = Total Price  (always)
#  If Total > 0 and price > 0:  qty = Total / price  (overrides raw qty)
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple, Any

import pandas as pd

__version__ = "5.0.0"


# ──────────────────────────────────────────────────────────────────────────────
#  FORMAT IDENTIFIERS
# ──────────────────────────────────────────────────────────────────────────────

FMT_A       = "fmt_a"   # concessions two-row, Seq key, no location col
FMT_B       = "fmt_b"   # catering two-row, location col, no Seq
FMT_C       = "fmt_c"   # catering two-row, location col, has Seq + breadcrumb
FMT_D       = "fmt_d"   # catering single-row slash-delimited
FMT_E       = "fmt_e"   # image PDF (future OCR)
FMT_UNKNOWN = "unknown"

FORMAT_LABELS = {
    FMT_A:       "Concessions CSV/XLSX (two-row, Seq key)",
    FMT_B:       "Catering CSV/XLSX (two-row, location column)",
    FMT_C:       "Catering XLSX (two-row, location + breadcrumb + Seq)",
    FMT_D:       "Catering XLSX (single-row, slash-delimited)",
    FMT_E:       "Image PDF (OCR required — not yet supported)",
    FMT_UNKNOWN: "Unknown format",
}


# ──────────────────────────────────────────────────────────────────────────────
#  COMMON CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

LIQUOR_PREFIXES = {'BRB', 'CRD', 'GIN', 'RUM', 'SCT', 'TEQ', 'VOD', 'WSK'}

NON_CHARGEABLE_KEYWORDS = {
    'BIB', 'SYRUP', 'BAG IN BOX',
    'LID', 'STRAW', 'SLEEVE',
    'MUSTARD', 'KETCHUP', 'RELISH', 'JALAPENO', 'PEPPER', 'CHILI',
    'SAUCE CHEESE', 'CHEESE SAUCE', 'CHEESE CHDR', 'CHEESE SHARP',
    'BUN', 'ROLL',
    'CHIP CORN', 'CHIP TORTILLA WHT',
    'GLOVE', 'HAIRNET', 'SOAP', 'SANITIZER',
    'LINER TRASH', 'TRASH BAG', 'TRASH LINER',
    'PAN COATING', 'DRYWAX', 'WRAP SAND',
    'NAPKIN', 'FORK', 'KNIFE', 'SPOON',
    'CUP PORTION', 'PORTION CUP',
    'FOIL', 'PLASTIC WRAP',
    'TEA BAG', 'COFFEE', 'CREAMER', 'SUGAR',
}

CASE_UOMS = {'CASE', 'CS', 'CTN', 'CA', 'CT', 'BOX', 'BX'}
EACH_UOMS = {'EACH', 'EA', 'EA.', 'E', '1', 'BAG', 'BG'}

# Sub-classifications that map to non-chargeable in catering
NC_SUBCLASSES = {
    'dry storage', 'paper', 'pantry', 'mixers', 'area storage (na)',
    'storage closet', 'stairwell', 'elevator storage', 'cages',
    'ice box cages', 'reach-in coolers', 'catering cooler',
}

# Sub-classifications that map to liquor in catering
LIQUOR_SUBCLASSES = {
    'liquor', 'sidelines liquor', 'suites liquor', 'wine', 'bar cooler',
}


# ──────────────────────────────────────────────────────────────────────────────
#  DATA CLASSES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CountRecord:
    item_description: str
    pack_type:        str
    item_key:         str           # DESCRIPTION||PACK_TYPE  (uppercase, double-pipe)

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

    # Totals from file
    total_price_case: float
    total_price_each: float
    total_price:      float

    # Location / classification
    location_name:    str           # explicit string (catering) or "Location N" (concessions)
    sub_class:        str           # "->Beer", "CHARGEABLE", "NON-CHARGEABLE", "LIQUOR" …
    is_chargeable:    bool

    # Optional concessions fields
    seq:              str  = ""
    location_num:     int  = 0

    # Quality
    verified:         bool = True
    source_fmt:       str  = ""


@dataclass
class DetectionResult:
    fmt:        str
    confidence: int          # 0-100
    label:      str
    notes:      List[str] = field(default_factory=list)

    @property
    def is_confident(self):   return self.confidence > 70
    @property
    def is_uncertain(self):   return 40 <= self.confidence <= 70
    @property
    def is_unknown(self):     return self.confidence < 40


@dataclass
class ParseResult:
    records:        List[CountRecord]
    fmt:            str
    confidence:     int
    location_count: int
    item_count:     int
    row_count:      int
    skipped_rows:   int
    warnings:       List[str]
    math_errors:    List[str]
    grand_total:    float


# ──────────────────────────────────────────────────────────────────────────────
#  VALUE CLEANERS
# ──────────────────────────────────────────────────────────────────────────────

def _qty(val) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    m = re.match(r'^\s*(-?\d+\.?\d*)', str(val).strip())
    return float(m.group(1)) if m else 0.0


def _price(val) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    s = re.sub(r'[,$\s]', '', str(val).split('/')[0])
    try:
        return float(s)
    except ValueError:
        return 0.0


def _price2(val) -> float:
    """Second price from slash-delimited cell e.g. '$82.27/$0.59'"""
    s = str(val or '')
    parts = s.split('/')
    if len(parts) < 2:
        return _price(val)
    return _price(parts[1])


def _qty2(val) -> float:
    """Second qty from slash-delimited cell e.g. '1.00 Case/2.00 EA'"""
    s = str(val or '')
    parts = s.split('/')
    if len(parts) < 2:
        return _qty(val)
    return _qty(parts[1])


def _uom1(val) -> str:
    s = str(val or '').split('/')[0].strip()
    # strip leading digits+spaces to get just the UOM word
    m = re.search(r'[A-Za-z]+', s)
    return m.group(0).upper() if m else s.upper()


def _uom2(val) -> str:
    parts = str(val or '').split('/')
    s = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    m = re.search(r'[A-Za-z]+', s)
    return m.group(0).upper() if m else s.upper()


def _norm_uom(uom: str) -> str:
    u = uom.strip().upper()
    if u in CASE_UOMS: return 'CASE'
    if u in EACH_UOMS: return 'EACH'
    return u


def _item_key(desc: str, pack: str) -> str:
    d = str(desc or '').strip().upper()
    p = str(pack or '').strip().upper()
    return f"{d}||{p}" if p else f"{d}||CASE"


# ──────────────────────────────────────────────────────────────────────────────
#  MATH VERIFIER
# ──────────────────────────────────────────────────────────────────────────────

def _verify(qty: float, price: float, total: float,
            label: str) -> Tuple[float, bool, str]:
    if price <= 0:
        return qty, True, ''
    if total > 0:
        calc = round(qty * price, 2)
        if abs(calc - total) > 0.02:
            corrected = round(total / price, 4)
            return corrected, False, (
                f"{label}: {qty}×${price}=${calc} ≠ file ${total} → using {corrected}"
            )
    return qty, True, ''


# ──────────────────────────────────────────────────────────────────────────────
#  CONCESSIONS CLASSIFIER  (FMT_A only)
# ──────────────────────────────────────────────────────────────────────────────

def _concessions_classify(desc: str) -> str:
    d = desc.strip().upper()
    if d[:3] in LIQUOR_PREFIXES:
        return 'LIQUOR'
    for kw in NON_CHARGEABLE_KEYWORDS:
        if kw in d:
            return 'NON-CHARGEABLE'
    return 'CHARGEABLE'


# ──────────────────────────────────────────────────────────────────────────────
#  FILE LOADER  — returns raw rows as list-of-lists
# ──────────────────────────────────────────────────────────────────────────────

def _load_rows(content: bytes, filename: str) -> Tuple[List[List], str]:
    """Returns (rows, ext). rows are raw values."""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if ext in ('xlsx', 'xls'):
        import openpyxl
        wb  = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws  = wb.active
        return [list(r) for r in ws.iter_rows(values_only=True)], ext

    if ext == 'csv':
        # Try multiple encodings
        for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                text = content.decode(enc)
                df   = pd.read_csv(io.StringIO(text), dtype=str,
                                   keep_default_na=False, header=None)
                return df.values.tolist(), ext
            except Exception:
                continue
        return [], ext

    if ext == 'pdf':
        return [], 'pdf'

    # Fallback — try CSV
    try:
        text = content.decode('utf-8-sig', errors='replace')
        df   = pd.read_csv(io.StringIO(text), dtype=str,
                           keep_default_na=False, header=None)
        return df.values.tolist(), 'csv'
    except Exception:
        return [], ext


# ──────────────────────────────────────────────────────────────────────────────
#  FORMAT DETECTOR
# ──────────────────────────────────────────────────────────────────────────────

class FormatDetector:

    def detect(self, content: bytes, filename: str) -> DetectionResult:
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if ext == 'pdf':
            return DetectionResult(FMT_E, 85, FORMAT_LABELS[FMT_E],
                                   ["PDF detected — OCR not yet implemented"])

        rows, _ = _load_rows(content, filename)
        if not rows:
            return DetectionResult(FMT_UNKNOWN, 0, FORMAT_LABELS[FMT_UNKNOWN],
                                   ["Could not read file"])

        scores = {
            FMT_A: self._score_a(rows),
            FMT_B: self._score_b(rows),
            FMT_C: self._score_c(rows),
            FMT_D: self._score_d(rows),
        }

        best_fmt = max(scores, key=lambda k: scores[k])
        best_score = scores[best_fmt]

        notes = [f"Scores: " + ", ".join(f"{k}={v}" for k, v in scores.items())]

        if best_score < 40:
            return DetectionResult(FMT_UNKNOWN, best_score,
                                   FORMAT_LABELS[FMT_UNKNOWN], notes)

        return DetectionResult(best_fmt, best_score,
                               FORMAT_LABELS[best_fmt], notes)

    def _cell(self, row, i) -> str:
        try:
            v = row[i]
            return str(v).strip() if v is not None else ''
        except IndexError:
            return ''

    def _has_compass_header(self, rows) -> bool:
        """4-row Compass header block: row0 has 'Property Of Compass Group',
           row1 has cost center name, row4 has 'Grouped by:'"""
        for r in rows[:3]:
            joined = ' '.join(str(c or '') for c in r)
            if 'Compass Group' in joined or 'Property Of' in joined:
                return True
        return False

    def _grouped_by_row(self, rows) -> Optional[int]:
        """Return index of the 'Grouped by:' header row, or None."""
        for i, r in enumerate(rows[:10]):
            joined = ' '.join(str(c or '') for c in r)
            if 'Grouped by' in joined:
                return i
        return None

    def _score_a(self, rows) -> int:
        """Concessions style: no Compass header, has Seq col, two rows per item."""
        score = 0
        if self._has_compass_header(rows):
            return 0   # definitely not FMT_A

        # Look for header row with Seq
        for r in rows[:5]:
            joined = ' '.join(str(c or '').lower() for c in r)
            if 'seq' in joined and 'item description' in joined:
                score += 50
                if 'inv count' in joined:
                    score += 20
                if 'total price' in joined:
                    score += 15
                if 'last inventory' in joined:
                    score += 15
                break

        return min(score, 100)

    def _score_b(self, rows) -> int:
        """Catering two-row, location col, no Seq, no Last Inv Qty."""
        if not self._has_compass_header(rows):
            return 0
        gb = self._grouped_by_row(rows)
        if gb is None:
            return 0

        header_row = rows[gb]
        joined = ' '.join(str(c or '').lower() for c in header_row)

        score = 30  # has compass header + grouped by
        if '>>' not in joined:
            score += 20   # no breadcrumb = B not C
        if 'seq' not in joined:
            score += 20
        if 'last inventory' not in joined:
            score += 15

        # Check data rows — should have location->subclass pattern
        data_rows = rows[gb+1:gb+10]
        has_arrow = any('->' in str(r[0] or '') for r in data_rows if r)
        if has_arrow:
            score += 15

        # Check for slash-delimited cells (would be D not B)
        has_slash = any(
            '/' in str(r[i] or '') and '$' in str(r[i] or '')
            for r in data_rows if r
            for i in range(1, min(5, len(r)))
        )
        if has_slash:
            score -= 30

        return min(score, 100)

    def _score_c(self, rows) -> int:
        """Catering two-row, location col, has Seq + breadcrumb >>."""
        if not self._has_compass_header(rows):
            return 0
        gb = self._grouped_by_row(rows)
        if gb is None:
            return 0

        header_row = rows[gb]
        joined = ' '.join(str(c or '').lower() for c in header_row)

        score = 30
        if '>>' in joined:
            score += 30   # breadcrumb header = C signature
        if 'seq' in joined:
            score += 20
        if 'last inventory' in joined:
            score += 20

        return min(score, 100)

    def _score_d(self, rows) -> int:
        """Catering single-row slash-delimited."""
        if not self._has_compass_header(rows):
            return 0
        gb = self._grouped_by_row(rows)
        if gb is None:
            return 0

        score = 20
        data_rows = rows[gb+1:gb+15]

        slash_price_count = sum(
            1 for r in data_rows if r and len(r) > 3
            and '/' in str(r[3] or '') and '$' in str(r[3] or '')
        )
        if slash_price_count >= 3:
            score += 50

        slash_qty_count = sum(
            1 for r in data_rows if r and len(r) > 4
            and '/' in str(r[4] or '')
        )
        if slash_qty_count >= 3:
            score += 30

        return min(score, 100)


# ──────────────────────────────────────────────────────────────────────────────
#  PARSER A  — Concessions two-row, Seq key, no location column
# ──────────────────────────────────────────────────────────────────────────────

class ParserA:

    def parse(self, content: bytes, filename: str) -> ParseResult:
        rows, _ = _load_rows(content, filename)
        warnings    = []
        math_errors = []

        # Find header row
        header_idx = None
        for i, r in enumerate(rows[:10]):
            joined = ' '.join(str(c or '').lower() for c in r)
            if 'seq' in joined and 'item description' in joined:
                header_idx = i
                break
        if header_idx is None:
            return ParseResult([], FMT_A, 0, 0, 0, len(rows), len(rows),
                               ["Could not find header row"], [], 0.0)

        header = [str(c or '').strip().lower() for c in rows[header_idx]]
        col = {h: i for i, h in enumerate(header)}

        def gc(row, name):
            i = col.get(name)
            return row[i] if i is not None and i < len(row) else None

        data_rows = rows[header_idx + 1:]

        # Pair rows by Seq + Description
        raw_pairs = []
        i = 0
        while i < len(data_rows):
            r = data_rows[i]
            seq  = str(gc(r, 'seq') or '').strip()
            desc = str(gc(r, 'item description') or '').strip()
            if not seq or not desc:
                i += 1
                continue
            if i + 1 < len(data_rows):
                r2   = data_rows[i + 1]
                seq2 = str(gc(r2, 'seq') or '').strip()
                desc2 = str(gc(r2, 'item description') or '').strip()
                if seq2 == seq and desc2 == desc:
                    uom1 = str(gc(r,  'uom') or '').strip().upper()
                    uom2 = str(gc(r2, 'uom') or '').strip().upper()
                    if uom1 in CASE_UOMS or (uom2 not in CASE_UOMS and uom1 not in EACH_UOMS):
                        raw_pairs.append((seq, desc, r, r2))
                    else:
                        raw_pairs.append((seq, desc, r2, r))
                    i += 2
                    continue
            warnings.append(f"Seq {seq} '{desc}': orphan row — skipped")
            i += 1

        # State machine — same as before
        records         = []
        location_num    = 1
        current_section = None
        prev_seq_num    = -1

        for seq_str, desc, case_row, each_row in raw_pairs:
            try:
                seq_num = int(float(seq_str))
            except ValueError:
                seq_num = 0

            is_reset = (seq_num <= prev_seq_num) and (prev_seq_num > 0)
            next_class = _concessions_classify(desc)

            if is_reset:
                if current_section == 'CHARGEABLE':
                    if next_class == 'NON-CHARGEABLE':
                        current_section = 'NON-CHARGEABLE'
                    else:
                        location_num += 1
                        current_section = 'CHARGEABLE'
                elif current_section == 'NON-CHARGEABLE':
                    if next_class == 'LIQUOR':
                        current_section = 'LIQUOR'
                    else:
                        location_num += 1
                        current_section = 'CHARGEABLE'
                elif current_section == 'LIQUOR':
                    location_num += 1
                    current_section = 'CHARGEABLE'
                else:
                    current_section = next_class
            else:
                if current_section is None:
                    current_section = next_class

            prev_seq_num = seq_num

            pack     = str(gc(case_row, 'pack type') or '').strip()
            case_uom = _norm_uom(str(gc(case_row, 'uom') or ''))
            each_uom = _norm_uom(str(gc(each_row, 'uom') or ''))

            last_case = _qty(gc(case_row, 'last inventory qty'))
            cnt_case  = _qty(gc(case_row, 'inv count'))
            pr_case   = _price(gc(case_row, 'price'))
            tot_case  = _price(gc(case_row, 'total price'))

            last_each = _qty(gc(each_row, 'last inventory qty'))
            cnt_each  = _qty(gc(each_row, 'inv count'))
            pr_each   = _price(gc(each_row, 'price'))
            tot_each  = _price(gc(each_row, 'total price'))

            label = f"Loc{location_num} Seq{seq_str} '{desc}'"
            cnt_case, ok1, w1 = _verify(cnt_case, pr_case, tot_case, f"{label} CASE")
            cnt_each, ok2, w2 = _verify(cnt_each, pr_each, tot_each, f"{label} EACH")
            if w1: math_errors.append(w1)
            if w2: math_errors.append(w2)

            records.append(CountRecord(
                item_description = desc,
                pack_type        = pack,
                item_key         = _item_key(desc, pack),
                case_uom         = case_uom,
                last_qty_case    = last_case,
                count_qty_case   = cnt_case,
                price_case       = pr_case,
                each_uom         = each_uom,
                last_qty_each    = last_each,
                count_qty_each   = cnt_each,
                price_each       = pr_each,
                total_price_case = tot_case,
                total_price_each = tot_each,
                total_price      = round(tot_case + tot_each, 2),
                location_name    = f"Location {location_num}",
                location_num     = location_num,
                sub_class        = current_section,
                is_chargeable    = (current_section != 'NON-CHARGEABLE'),
                seq              = seq_str,
                verified         = ok1 and ok2,
                source_fmt       = FMT_A,
            ))

        locs  = len(set(r.location_num for r in records))
        total = sum(r.total_price for r in records)
        return ParseResult(
            records=records, fmt=FMT_A, confidence=90,
            location_count=locs, item_count=len(records),
            row_count=len(rows), skipped_rows=len(rows) - len(raw_pairs)*2 - 1,
            warnings=warnings, math_errors=math_errors, grand_total=round(total, 2),
        )


# ──────────────────────────────────────────────────────────────────────────────
#  BASE CATERING PARSER  — shared logic for FMT_B, C, D
# ──────────────────────────────────────────────────────────────────────────────

class _CateringBase:

    def _find_header(self, rows) -> Optional[int]:
        for i, r in enumerate(rows[:10]):
            joined = ' '.join(str(c or '') for c in r)
            if 'Grouped by' in joined:
                return i
        return None

    def _parse_location_cell(self, cell_val) -> Tuple[str, str]:
        """
        Returns (location_name, sub_class).
        'Fertitta Center - Legends Club->Beer' → ('Fertitta Center - Legends Club', 'Beer')
        'Cougar Club (Shasta) >> Cat >> DC >> Mfg' → ('Cougar Club (Shasta)', '')
        'Loc->Sub >> Cat >> DC' → ('Loc', 'Sub')
        """
        raw = str(cell_val or '').strip()

        # Strip >> breadcrumb (keep only the first segment)
        base = raw.split('>>')[0].strip()

        if '->' in base:
            parts = base.split('->', 1)
            return parts[0].strip(), parts[1].strip()

        return base, ''

    def _is_chargeable(self, sub_class: str) -> bool:
        sc = sub_class.lower()
        if sc in LIQUOR_SUBCLASSES:
            return True   # liquor is chargeable
        if sc in NC_SUBCLASSES:
            return False
        return True   # default chargeable

    def _make_record(self, desc, pack, loc_name, sub_class,
                     case_uom, last_case, cnt_case, pr_case, tot_case,
                     each_uom, last_each, cnt_each, pr_each, tot_each,
                     seq, math_errors, fmt) -> CountRecord:
        label = f"'{loc_name}' '{desc}'"
        cnt_case, ok1, w1 = _verify(cnt_case, pr_case, tot_case, f"{label} CASE")
        cnt_each, ok2, w2 = _verify(cnt_each, pr_each, tot_each, f"{label} EACH")
        if w1: math_errors.append(w1)
        if w2: math_errors.append(w2)

        return CountRecord(
            item_description = str(desc or '').strip(),
            pack_type        = str(pack or '').strip(),
            item_key         = _item_key(desc, pack),
            case_uom         = _norm_uom(str(case_uom or '')),
            last_qty_case    = last_case,
            count_qty_case   = cnt_case,
            price_case       = pr_case,
            each_uom         = _norm_uom(str(each_uom or '')),
            last_qty_each    = last_each,
            count_qty_each   = cnt_each,
            price_each       = pr_each,
            total_price_case = tot_case,
            total_price_each = tot_each,
            total_price      = round(tot_case + tot_each, 2),
            location_name    = loc_name,
            sub_class        = sub_class,
            is_chargeable    = self._is_chargeable(sub_class),
            seq              = str(seq or ''),
            verified         = ok1 and ok2,
            source_fmt       = fmt,
        )


# ──────────────────────────────────────────────────────────────────────────────
#  PARSER B  — Catering two-row, location col, no Seq, no Last Inv Qty
#  Columns: Location | Item Description | UOM | Pack Type | Price | Inv Count
#           | Total Price
# ──────────────────────────────────────────────────────────────────────────────

class ParserB(_CateringBase):

    def parse(self, content: bytes, filename: str) -> ParseResult:
        rows, _ = _load_rows(content, filename)
        warnings    = []
        math_errors = []

        hi = self._find_header(rows)
        if hi is None:
            return ParseResult([], FMT_B, 0, 0, 0, len(rows), len(rows),
                               ["Cannot find Grouped-by header"], [], 0.0)

        # Build col map from the header row
        header = [str(c or '').strip().lower() for c in rows[hi]]
        col    = {h: i for i, h in enumerate(header)}

        def gc(row, *names):
            for name in names:
                i = col.get(name)
                if i is not None and i < len(row):
                    return row[i]
            return None

        data = rows[hi + 1:]
        records = []
        i = 0
        skipped = 0

        while i < len(data):
            r = data[i]
            if not r or all(str(c or '').strip() == '' for c in r):
                i += 1
                skipped += 1
                continue

            loc_raw  = gc(r, 'grouped by: classification', 'classification')
            desc     = str(gc(r, 'item description') or '').strip()
            uom1     = str(gc(r, 'uom') or '').strip()
            pack     = str(gc(r, 'pack type') or '').strip()
            price1   = _price(gc(r, 'price'))
            cnt1     = _qty(gc(r, 'inv count'))
            tot1     = _price(gc(r, 'total price'))

            if not desc or str(loc_raw or '').strip() in ('', 'Total'):
                i += 1
                skipped += 1
                continue

            loc_name, sub_class = self._parse_location_cell(loc_raw)

            # Look for matching pair (same desc, same location)
            if i + 1 < len(data):
                r2       = data[i + 1]
                loc_raw2 = gc(r2, 'grouped by: classification', 'classification')
                desc2    = str(gc(r2, 'item description') or '').strip()

                if desc2 == desc and str(loc_raw2 or '').strip() == str(loc_raw or '').strip():
                    uom2   = str(gc(r2, 'uom') or '').strip()
                    price2 = _price(gc(r2, 'price'))
                    cnt2   = _qty(gc(r2, 'inv count'))
                    tot2   = _price(gc(r2, 'total price'))

                    # Determine which is case vs each
                    if uom1.upper() in CASE_UOMS or (uom2.upper() not in CASE_UOMS):
                        case_u, last_c, cnt_c, pr_c, tot_c = uom1, 0.0, cnt1, price1, tot1
                        each_u, last_e, cnt_e, pr_e, tot_e = uom2, 0.0, cnt2, price2, tot2
                    else:
                        case_u, last_c, cnt_c, pr_c, tot_c = uom2, 0.0, cnt2, price2, tot2
                        each_u, last_e, cnt_e, pr_e, tot_e = uom1, 0.0, cnt1, price1, tot1

                    records.append(self._make_record(
                        desc, pack, loc_name, sub_class,
                        case_u, last_c, cnt_c, pr_c, tot_c,
                        each_u, last_e, cnt_e, pr_e, tot_e,
                        '', math_errors, FMT_B,
                    ))
                    i += 2
                    continue

            # Single row — treat as each-only
            records.append(self._make_record(
                desc, pack, loc_name, sub_class,
                uom1, 0.0, 0.0, price1, 0.0,
                'EACH', 0.0, cnt1, price1, tot1,
                '', math_errors, FMT_B,
            ))
            warnings.append(f"'{desc}': single row (no pair) — treated as each-only")
            i += 1

        locs  = len(set(r.location_name for r in records))
        total = sum(r.total_price for r in records)
        return ParseResult(
            records=records, fmt=FMT_B, confidence=85,
            location_count=locs, item_count=len(records),
            row_count=len(rows), skipped_rows=skipped,
            warnings=warnings, math_errors=math_errors, grand_total=round(total, 2),
        )


# ──────────────────────────────────────────────────────────────────────────────
#  PARSER C  — Catering two-row, location col + breadcrumb, has Seq + Last Qty
#  Columns: Location>>… | Item Description | UOM | Pack Type | Price
#           | Last Inventory Qty | Seq | Inv Count | Total Price
# ──────────────────────────────────────────────────────────────────────────────

class ParserC(_CateringBase):

    def parse(self, content: bytes, filename: str) -> ParseResult:
        rows, _ = _load_rows(content, filename)
        warnings    = []
        math_errors = []

        hi = self._find_header(rows)
        if hi is None:
            return ParseResult([], FMT_C, 0, 0, 0, len(rows), len(rows),
                               ["Cannot find Grouped-by header"], [], 0.0)

        header = [str(c or '').strip().lower() for c in rows[hi]]
        col    = {h: i for i, h in enumerate(header)}

        def gc(row, *names):
            for name in names:
                i = col.get(name)
                if i is not None and i < len(row):
                    return row[i]
            return None

        data    = rows[hi + 1:]
        records = []
        i       = 0
        skipped = 0

        while i < len(data):
            r = data[i]
            if not r or all(str(c or '').strip() == '' for c in r):
                i += 1; skipped += 1; continue

            loc_raw = r[0]
            desc    = str(gc(r, 'item description') or '').strip()
            if not desc or str(loc_raw or '').strip() in ('', 'Total'):
                i += 1; skipped += 1; continue

            loc_name, sub_class = self._parse_location_cell(loc_raw)

            uom1     = str(gc(r, 'uom') or '').strip()
            pack     = str(gc(r, 'pack type') or '').strip()
            price1   = _price(gc(r, 'price'))
            last1    = _qty(gc(r, 'last inventory qty'))
            seq_val  = str(gc(r, 'seq') or '').strip()
            cnt1     = _qty(gc(r, 'inv count'))
            tot1     = _price(gc(r, 'total price'))

            # Look for pair
            if i + 1 < len(data):
                r2      = data[i + 1]
                desc2   = str(gc(r2, 'item description') or '').strip()
                loc_r2  = str(r2[0] or '').strip() if r2 else ''

                if desc2 == desc and loc_r2 == str(loc_raw or '').strip():
                    uom2   = str(gc(r2, 'uom') or '').strip()
                    price2 = _price(gc(r2, 'price'))
                    last2  = _qty(gc(r2, 'last inventory qty'))
                    cnt2   = _qty(gc(r2, 'inv count'))
                    tot2   = _price(gc(r2, 'total price'))

                    if uom1.upper() in CASE_UOMS or (uom2.upper() not in CASE_UOMS):
                        case_u, last_c, cnt_c, pr_c, tot_c = uom1, last1, cnt1, price1, tot1
                        each_u, last_e, cnt_e, pr_e, tot_e = uom2, last2, cnt2, price2, tot2
                    else:
                        case_u, last_c, cnt_c, pr_c, tot_c = uom2, last2, cnt2, price2, tot2
                        each_u, last_e, cnt_e, pr_e, tot_e = uom1, last1, cnt1, price1, tot1

                    records.append(self._make_record(
                        desc, pack, loc_name, sub_class,
                        case_u, last_c, cnt_c, pr_c, tot_c,
                        each_u, last_e, cnt_e, pr_e, tot_e,
                        seq_val, math_errors, FMT_C,
                    ))
                    i += 2
                    continue

            # Single row fallback
            records.append(self._make_record(
                desc, pack, loc_name, sub_class,
                uom1, last1, 0.0, price1, 0.0,
                'EACH', 0.0, cnt1, price1, tot1,
                seq_val, math_errors, FMT_C,
            ))
            warnings.append(f"'{desc}': single row — treated as each-only")
            i += 1

        locs  = len(set(r.location_name for r in records))
        total = sum(r.total_price for r in records)
        return ParseResult(
            records=records, fmt=FMT_C, confidence=85,
            location_count=locs, item_count=len(records),
            row_count=len(rows), skipped_rows=skipped,
            warnings=warnings, math_errors=math_errors, grand_total=round(total, 2),
        )


# ──────────────────────────────────────────────────────────────────────────────
#  PARSER D  — Catering single-row, slash-delimited values
#  Columns: Location | Seq | Item Description | Price($x/$y)
#           | Last Inventory Qty(x UOM/y EA) | Inv Count(x UOM/y EA)
#           | Total Price | UOM(case/EA) | Pack Type
# ──────────────────────────────────────────────────────────────────────────────

class ParserD(_CateringBase):

    def parse(self, content: bytes, filename: str) -> ParseResult:
        rows, _ = _load_rows(content, filename)
        warnings    = []
        math_errors = []

        hi = self._find_header(rows)
        if hi is None:
            return ParseResult([], FMT_D, 0, 0, 0, len(rows), len(rows),
                               ["Cannot find Grouped-by header"], [], 0.0)

        header = [str(c or '').strip().lower() for c in rows[hi]]
        col    = {h: i for i, h in enumerate(header)}

        def gc(row, *names):
            for name in names:
                i = col.get(name)
                if i is not None and i < len(row):
                    return row[i]
            return None

        data    = rows[hi + 1:]
        records = []
        skipped = 0

        for r in data:
            if not r or all(str(c or '').strip() == '' for c in r):
                skipped += 1; continue

            loc_raw = r[0]
            desc    = str(gc(r, 'item description') or '').strip()
            if not desc or str(loc_raw or '').strip() in ('', 'Total'):
                skipped += 1; continue

            loc_name, sub_class = self._parse_location_cell(loc_raw)

            seq_val  = str(gc(r, 'seq') or '').strip()
            price_raw = gc(r, 'price')
            last_raw  = gc(r, 'last inventory qty')
            cnt_raw   = gc(r, 'inv count')
            tot_val   = _price(gc(r, 'total price'))
            uom_raw   = gc(r, 'uom')
            pack      = str(gc(r, 'pack type') or '').strip()

            pr_case  = _price(price_raw)
            pr_each  = _price2(price_raw)
            last_case = _qty(last_raw)
            last_each = _qty2(last_raw)
            cnt_case  = _qty(cnt_raw)
            cnt_each  = _qty2(cnt_raw)
            case_uom  = _uom1(uom_raw) if uom_raw else 'CASE'
            each_uom  = _uom2(uom_raw) if uom_raw else 'EACH'

            # Total price is for whichever side had counts
            # Allocate: if case count > 0, tot_case = cnt_case * pr_case
            tot_case = round(cnt_case * pr_case, 2) if cnt_case > 0 else 0.0
            tot_each = round(cnt_each * pr_each, 2) if cnt_each > 0 else 0.0
            # Verify against file total
            calc_total = round(tot_case + tot_each, 2)
            if tot_val > 0 and abs(calc_total - tot_val) > 0.02:
                math_errors.append(
                    f"'{desc}': reconstructed ${calc_total} ≠ file ${tot_val}"
                )

            records.append(self._make_record(
                desc, pack, loc_name, sub_class,
                case_uom, last_case, cnt_case, pr_case, tot_case,
                each_uom, last_each, cnt_each, pr_each, tot_each,
                seq_val, math_errors, FMT_D,
            ))

        locs  = len(set(r.location_name for r in records))
        total = sum(r.total_price for r in records)
        return ParseResult(
            records=records, fmt=FMT_D, confidence=85,
            location_count=locs, item_count=len(records),
            row_count=len(rows), skipped_rows=skipped,
            warnings=warnings, math_errors=math_errors, grand_total=round(total, 2),
        )


# ──────────────────────────────────────────────────────────────────────────────
#  MASTER PARSER  — detects format, routes to correct parser
# ──────────────────────────────────────────────────────────────────────────────

class CountImporter:

    def __init__(self):
        self.detector = FormatDetector()
        self._parsers = {
            FMT_A: ParserA(),
            FMT_B: ParserB(),
            FMT_C: ParserC(),
            FMT_D: ParserD(),
        }

    def detect(self, content: bytes, filename: str) -> DetectionResult:
        return self.detector.detect(content, filename)

    def parse(self, content: bytes, filename: str,
              force_fmt: str = None) -> ParseResult:
        det = self.detector.detect(content, filename)

        fmt = force_fmt or det.fmt
        if fmt not in self._parsers:
            return ParseResult(
                [], fmt, det.confidence, 0, 0, 0, 0,
                [f"No parser available for format: {FORMAT_LABELS.get(fmt, fmt)}"],
                [], 0.0,
            )

        result = self._parsers[fmt].parse(content, filename)
        result.confidence = det.confidence
        return result


# ──────────────────────────────────────────────────────────────────────────────
#  AGGREGATOR
# ──────────────────────────────────────────────────────────────────────────────

def aggregate(records: List[CountRecord]) -> Dict:
    by_location: Dict[str, List[CountRecord]] = {}
    summary:     Dict[str, Dict]              = {}

    for r in records:
        by_location.setdefault(r.location_name, []).append(r)
        s = summary.setdefault(r.location_name, {})
        sc = r.sub_class or 'CHARGEABLE'
        s[sc] = round(s.get(sc, 0.0) + r.total_price, 2)
        s['_total'] = round(s.get('_total', 0.0) + r.total_price, 2)

    return {'by_location': by_location, 'summary': summary}


# ──────────────────────────────────────────────────────────────────────────────
#  DB WRITER
# ──────────────────────────────────────────────────────────────────────────────

def commit_count(records: List[CountRecord], db, count_date: str,
                 imported_by: str, count_type: str = 'complete',
                 cost_center: str = 'default') -> Dict:

    import_id = (f"CNT-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                 f"-{uuid.uuid4().hex[:6].upper()}")
    results = {
        'import_id':     import_id,
        'items_updated': 0,
        'items_created': 0,
        'items_skipped': 0,
        'errors':        [],
        'total_value':   0.0,
    }

    for r in records:
        try:
            db_item  = db.get_item(r.item_key)
            prev_qty = float(db_item['quantity_on_hand']) if db_item else 0.0
            new_qty  = r.count_qty_each

            if db_item:
                ok = db.update_quantity_from_count(
                    r.item_key, new_qty, import_id, changed_by=imported_by)
                if ok:
                    results['items_updated'] += 1
                else:
                    results['items_skipped'] += 1
                    results['errors'].append(f"Update failed: {r.item_key}")
            else:
                db.add_item({
                    'key':              r.item_key,
                    'description':      r.item_description,
                    'pack_type':        r.pack_type,
                    'cost':             r.price_each,
                    'quantity_on_hand': new_qty,
                    'is_chargeable':    r.is_chargeable,
                    'cost_center':      cost_center,
                    'status_tag':       '📦 Count Import',
                }, changed_by=imported_by)
                results['items_created'] += 1

            results['total_value'] += r.total_price

        except Exception as e:
            results['errors'].append(f"{r.item_key}: {e}")
            results['items_skipped'] += 1

    try:
        db.log_count_import(
            import_id=import_id, source_file='' ,file_format='multi',
            data_layout=records[0].source_fmt if records else '',
            count_type=count_type, count_date=count_date,
            cost_center=cost_center, imported_by=imported_by,
            total_items=len(records),
            items_changed=results['items_updated'] + results['items_created'],
            items_flagged=0, total_prev_value=0.0,
            total_new_value=results['total_value'],
            variance_value=results['total_value'],
        )
    except Exception as e:
        results['errors'].append(f"Import log failed: {e}")

    return results


# ──────────────────────────────────────────────────────────────────────────────
#  STREAMLIT PAGE
# ──────────────────────────────────────────────────────────────────────────────

def render_count_import_page(db, get_changed_by_fn):
    import streamlit as st

    st.title("📋 Count Import")
    st.caption("Accepts CSV, XLSX, or PDF count sheets — format auto-detected.")

    uploaded = st.file_uploader(
        "Drop count sheet here",
        type=["csv", "xlsx", "xls", "pdf"],
        label_visibility="collapsed",
    )
    if not uploaded:
        return

    content  = uploaded.read()
    importer = CountImporter()

    # ── Detection ──────────────────────────────────────────────────────────────
    det = importer.detect(content, uploaded.name)

    if det.fmt == FMT_E:
        st.error("📄 This appears to be an image-based PDF. OCR support is coming — "
                 "please export as CSV or XLSX for now.")
        return

    if det.is_unknown:
        st.error(f"❓ Could not identify format (confidence {det.confidence}%). "
                 "Please select manually:")
        fmt = st.selectbox("Format", list(FORMAT_LABELS.keys()),
                           format_func=lambda k: FORMAT_LABELS[k])
    else:
        confidence_color = "🟢" if det.is_confident else "🟡"
        st.info(
            f"{confidence_color} Detected: **{det.label}** "
            f"(confidence {det.confidence}%)"
        )
        fmt = det.fmt
        if det.is_uncertain:
            override = st.checkbox("Override detected format")
            if override:
                fmt = st.selectbox("Select format", list(FORMAT_LABELS.keys()),
                                   format_func=lambda k: FORMAT_LABELS[k])

    # ── Parse ─────────────────────────────────────────────────────────────────
    with st.spinner("Parsing…"):
        result = importer.parse(content, uploaded.name, force_fmt=fmt)

    if not result.records:
        for w in result.warnings:
            st.error(w)
        return

    st.success(
        f"✅ **{result.item_count}** items · "
        f"**{result.location_count}** location(s) · "
        f"Grand total **${result.grand_total:,.2f}**"
    )

    if result.warnings:
        with st.expander(f"⚠️ {len(result.warnings)} warning(s)"):
            for w in result.warnings:
                st.caption(w)
    if result.math_errors:
        with st.expander(f"🔢 {len(result.math_errors)} math correction(s)"):
            for e in result.math_errors:
                st.caption(e)

    # ── Location summary ───────────────────────────────────────────────────────
    agg = aggregate(result.records)
    st.markdown("---")
    st.subheader("📍 Location Summary")

    summary_rows = []
    for loc, s in sorted(agg['summary'].items()):
        row = {'Location': loc, 'Items': len(agg['by_location'][loc])}
        for k, v in s.items():
            if k != '_total':
                row[k] = f"${v:,.2f}"
        row['Total'] = f"${s['_total']:,.2f}"
        summary_rows.append(row)

    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True,
                 hide_index=True)
    st.metric("Grand Total", f"${result.grand_total:,.2f}")

    # ── Item detail ───────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🔍 Item Detail")

    loc_options = sorted(agg['by_location'].keys())
    selected    = st.selectbox("Location", ["All"] + loc_options)
    view        = result.records if selected == "All" else agg['by_location'][selected]

    detail_df = pd.DataFrame([{
        'Location':    r.location_name,
        'Sub-Class':   r.sub_class,
        'Seq':         r.seq,
        'Description': r.item_description,
        'Pack Type':   r.pack_type,
        'Last Case':   r.last_qty_case,
        'Count Case':  r.count_qty_case,
        'Case $':      f"${r.price_case:.2f}",
        'Last Each':   r.last_qty_each,
        'Count Each':  r.count_qty_each,
        'Each $':      f"${r.price_each:.2f}",
        'Total $':     f"${r.total_price:.2f}",
        '✓':           '✅' if r.verified else '⚠️',
    } for r in view])

    st.caption(f"{len(detail_df)} items")
    st.dataframe(detail_df, use_container_width=True, hide_index=True)

    # ── Commit ────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("✅ Commit Count")

    col1, col2 = st.columns(2)
    with col1:
        count_date = st.date_input("Count date", value=date.today())
    with col2:
        count_type = st.radio("Type", ["complete", "partial"], horizontal=True)

    confirmed = st.checkbox(
        f"Commit {result.item_count} items · ${result.grand_total:,.2f}"
    )
    if st.button("✅ Commit", type="primary", disabled=not confirmed):
        with st.spinner("Writing to database…"):
            res = commit_count(
                records=result.records, db=db,
                count_date=str(count_date),
                imported_by=get_changed_by_fn(),
                count_type=count_type,
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
