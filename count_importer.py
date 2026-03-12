
# ──────────────────────────────────────────────────────────────────────────────
#  count_importer.py  —  On-Hand / Cost-Center Count Import
#  Handles the three export formats from the myOrders count system:
#    • CSV  — Separated, single location, sorted by Seq
#    • XLSX — Separated, all locations, column[0] = Classification
#    • PDF  — Combined (slash-delimited), all locations, section headers
#
#  "Separated" means 2 rows per item (CS row + EA row).
#  "Combined"  means 1 row per item with "0.00 CS/16.00 Each" style values.
#
#  All three produce the same normalized CountRecord list, which is then
#  diff'd against the database to generate a variance report before any
#  writes are committed.
# ──────────────────────────────────────────────────────────────────────────────

import re
import io
import uuid
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import pandas as pd

from importer import normalize_pack_type, detect_encoding

# ── end of imports ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

# UOM tokens that identify a CASE-level row in separated format
CASE_UOMS = {'case', 'cs', 'cases', 'cse', 'ctn'}

# UOM tokens that identify an EACH-level row in separated format
EACH_UOMS = {'each', 'ea', 'bx', 'box', 'sleeve', 'slv', '1', 'bag'}

# Column aliases used in count export files
COUNT_COL_MAP = {
    'SEQ':                  'seq',
    'PACK TYPE':            'pack_type',
    'LAST INVENTORY QTY':   'last_qty',
    'INV COUNT':            'inv_count',
    'ITEM DESCRIPTION':     'description',
    'UOM':                  'uom',
    'PRICE':                'price',
    'TOTAL PRICE':          'total_price',
    'GROUPED BY: CLASSIFICATION': 'location',
    # XLSX has location as an unnamed first column
}

# Phrases that indicate a header / skip row in the count file
SKIP_PHRASES = [
    'property of compass group', 'printed by', 'page',
    'seq', 'pack type', 'last inventory', 'inv count',
    'grouped by', 'track market->non', 'non-chargeable',
]

# Default variance flagging thresholds
DEFAULT_FLAG_EACH  = 24    # flag if |variance_each| > this many units
DEFAULT_FLAG_VALUE = 50.0  # flag if |variance $| > this amount

# ── end of constants ─────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  DATA CLASSES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CountRecord:
    """One normalized item from a count export file."""
    location:       str
    seq:            str
    item_description: str
    pack_type:      str
    item_key:       str          # "DESCRIPTION||PACK_TYPE"
    last_qty_case:  float = 0.0
    last_qty_each:  float = 0.0
    count_qty_case: float = 0.0
    count_qty_each: float = 0.0
    price_case:     float = 0.0
    price_each:     float = 0.0
    total_price:    float = 0.0
    uom_case:       str   = "CS"
    uom_each:       str   = "EA"
    is_chargeable:  bool  = True


@dataclass
class VarianceRecord:
    """One item's count vs. database comparison."""
    record:          CountRecord
    db_qty:          float        # current quantity_on_hand in DB
    db_price_each:   float        # current price_each in DB
    new_qty:         float        # what we'll write (count_qty_each)
    variance_each:   float        # new_qty - db_qty
    variance_value:  float        # variance_each * effective_price
    in_db:           bool         # False = item not found in DB
    is_flagged:      bool         = False
    flag_reason:     str          = ""


@dataclass
class CountImportMeta:
    """Metadata for a count import session."""
    import_id:    str   = field(default_factory=lambda: str(uuid.uuid4())[:12])
    source_file:  str   = ""
    file_format:  str   = ""      # csv | xlsx | pdf
    data_layout:  str   = ""      # separated | combined
    count_type:   str   = "complete"   # complete | partial
    count_date:   str   = ""
    cost_center:  str   = "UHA TDECU Stadium"
    imported_by:  str   = "user"
    file_hash:    str   = ""

# ── end of data classes ───────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  SCALAR + PARSE HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _scalar(val):
    """Return plain Python scalar, never a pandas Series."""
    if isinstance(val, pd.Series):
        val = val.iloc[0] if not val.empty else None
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    return val


def _to_float(val) -> float:
    """Convert a value to float, stripping $, commas, whitespace."""
    val = _scalar(val)
    if val is None:
        return 0.0
    s = re.sub(r'[$,\s]', '', str(val))
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _parse_combined_qty(text: str) -> Tuple[float, str, float, str]:
    """
    Parse "0.00 CS/16.00 Each" or "14.00 CS/0.00 EA" into
    (case_qty, case_uom, each_qty, each_uom).
    Returns (0,0,0,0) on failure.
    """
    if not text:
        return 0.0, "CS", 0.0, "EA"
    text = str(text).strip()
    # Pattern: number uom / number uom
    m = re.match(
        r'([\d.]+)\s*([A-Za-z]+)\s*/\s*([\d.]+)\s*([A-Za-z]+)',
        text
    )
    if m:
        return (
            float(m.group(1)), m.group(2).upper(),
            float(m.group(3)), m.group(4).upper(),
        )
    # Fallback: single number
    m2 = re.match(r'([\d.]+)', text)
    if m2:
        return float(m2.group(1)), "CS", 0.0, "EA"
    return 0.0, "CS", 0.0, "EA"


def _parse_combined_price(text: str) -> Tuple[float, float]:
    """
    Parse "$101.63/$0.10" into (case_price, each_price).
    """
    if not text:
        return 0.0, 0.0
    text = str(text).strip()
    parts = text.split('/')
    if len(parts) >= 2:
        return _to_float(parts[0]), _to_float(parts[1])
    return _to_float(text), 0.0


def _build_key(description: str, pack_type: str) -> str:
    """Canonical key: 'DESCRIPTION||PACK_TYPE' — pack_type normalized to match DB."""
    desc = str(description or '').strip().upper()
    pack = normalize_pack_type(pack_type)   # CS→CASE, EA→EACH, etc.
    if not desc:
        return ''
    return f"{desc}||{pack}" if pack else f"{desc}||CASE"


def _is_skip_line(text: str) -> bool:
    """True if this line is a page header, column header, or metadata row."""
    low = str(text or '').strip().lower()
    if not low:
        return True
    return any(p in low for p in SKIP_PHRASES)


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]

# ── end of scalar + parse helpers ────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  FORMAT DETECTION
# ──────────────────────────────────────────────────────────────────────────────

def detect_format(filename: str, content_bytes: bytes) -> Dict:
    """
    Returns {
        'ext':          'csv' | 'xlsx' | 'pdf',
        'layout':       'separated' | 'combined',
        'has_location': bool,
        'description':  str   (human-readable summary)
    }
    """
    ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''

    if ext == 'pdf':
        return {
            'ext': 'pdf', 'layout': 'combined', 'has_location': True,
            'description': 'PDF — Combined (slash-delimited), all locations',
        }

    # For CSV / XLSX, peek at the content to detect layout and location column
    layout       = 'separated'
    has_location = False

    try:
        if ext == 'csv':
            enc = detect_encoding(content_bytes)
            df = pd.read_csv(io.BytesIO(content_bytes), encoding=enc,
                             encoding_errors='replace', dtype=str, nrows=5)
            # Check first column for non-numeric location strings
            has_location = bool(re.search(r'[A-Za-z]', str(df.columns[0])) and
                                'seq' not in str(df.columns[0]).lower())
            # Check if qty values contain "/" (combined)
            sample = df.to_string()
            if re.search(r'\d+\.\d+\s+\w+/\d+\.\d+\s+\w+', sample):
                layout = 'combined'

        elif ext in ('xlsx', 'xls'):
            df = pd.read_excel(io.BytesIO(content_bytes), header=None,
                               dtype=str, nrows=8)
            # XLSX: look for a classification column (col[0] has location names)
            col0_vals = df.iloc[:, 0].dropna().tolist()
            for v in col0_vals:
                if isinstance(v, str) and len(v) > 3 and re.search(r'[A-Za-z]', v):
                    has_location = True
                    break
            # Check layout from qty column values
            flat = ' '.join(str(c) for c in df.values.flatten() if pd.notna(c))
            if re.search(r'\d+\.\d+\s+\w+/\d+\.\d+\s+\w+', flat):
                layout = 'combined'

    except Exception:
        pass

    loc_str = 'all locations' if has_location else 'single location'
    desc    = f"{ext.upper()} — {layout.capitalize()}, {loc_str}"
    return {
        'ext':          ext,
        'layout':       layout,
        'has_location': has_location,
        'description':  desc,
    }

# ── end of format detection ───────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  SEPARATED FORMAT — merge CS + EA row pairs into one CountRecord
# ──────────────────────────────────────────────────────────────────────────────

def _uom_is_case(uom: str) -> bool:
    return str(uom or '').strip().lower() in CASE_UOMS


def _uom_is_each(uom: str) -> bool:
    tok = str(uom or '').strip().lower()
    return tok in EACH_UOMS or (tok not in CASE_UOMS and tok != '')


def _merge_separated_df(df: pd.DataFrame, location: str = "Unspecified") -> List[CountRecord]:
    """
    Given a DataFrame with 2 rows per item (CS row + EA row),
    group by (seq, description) and merge into one CountRecord per item.
    """
    records: List[CountRecord] = []

    # Normalize column names
    rename = {}
    for col in df.columns:
        key = str(col).strip().upper()
        if key in COUNT_COL_MAP:
            rename[col] = COUNT_COL_MAP[key]
    df = df.rename(columns=rename)

    required = {'description', 'uom'}
    if not required.issubset(set(df.columns)):
        return records

    # Group rows by (seq, description)  — seq may be missing
    group_cols = []
    if 'seq' in df.columns:
        group_cols.append('seq')
    group_cols.append('description')

    seen = {}  # key → partial CountRecord dict

    for _, row in df.iterrows():
        row = row.where(pd.notna(row), None)

        desc = _scalar(row.get('description'))
        if not desc or _is_skip_line(str(desc)):
            continue

        # Skip the grand-total sentinel row emitted by myOrders
        # (the row where the location/classification cell reads "Total")
        loc_cell = str(_scalar(row.get('location')) or '').strip().lower()
        if loc_cell == 'total':
            continue

        seq       = str(_scalar(row.get('seq')) or '').strip()
        pack_type = str(_scalar(row.get('pack_type')) or '').strip()
        uom       = str(_scalar(row.get('uom')) or '').strip()
        price     = _to_float(_scalar(row.get('price')))
        qty_raw   = str(_scalar(row.get('last_qty')) or '0')
        cnt_raw   = str(_scalar(row.get('inv_count')) or '0')
        tot_price = _to_float(_scalar(row.get('total_price')))

        # Extract numeric part of qty (e.g. "96.00 EA" → 96.0)
        qty_num   = _to_float(re.sub(r'[^\d.]', '', qty_raw.split()[0]) if qty_raw.split() else '0')
        cnt_num   = _to_float(re.sub(r'[^\d.]', '', cnt_raw.split()[0]) if cnt_raw.split() else '0')

        group_key = f"{seq}||{str(desc).strip().upper()}"

        if group_key not in seen:
            seen[group_key] = {
                'seq': seq, 'description': str(desc).strip(),
                'pack_type': pack_type, 'location': location,
                'last_qty_case': 0.0, 'last_qty_each': 0.0,
                'count_qty_case': 0.0, 'count_qty_each': 0.0,
                'price_case': 0.0, 'price_each': 0.0,
                'total_price': 0.0,
                'uom_case': 'CS', 'uom_each': 'EA',
            }

        entry = seen[group_key]
        entry['total_price'] += tot_price

        if _uom_is_case(uom):
            entry['last_qty_case']  = qty_num
            entry['count_qty_case'] = cnt_num
            entry['price_case']     = price
            entry['uom_case']       = uom.upper()
            if not entry['pack_type'] and pack_type:
                entry['pack_type'] = pack_type
        elif _uom_is_each(uom):
            entry['last_qty_each']  = qty_num
            entry['count_qty_each'] = cnt_num
            entry['price_each']     = price
            entry['uom_each']       = uom.upper()

    # Convert merged dicts to CountRecord objects
    for data in seen.values():
        key = _build_key(data['description'], data['pack_type'])
        if not key:
            continue
        records.append(CountRecord(
            location        = data['location'],
            seq             = data['seq'],
            item_description= data['description'],
            pack_type       = data['pack_type'],
            item_key        = key,
            last_qty_case   = data['last_qty_case'],
            last_qty_each   = data['last_qty_each'],
            count_qty_case  = data['count_qty_case'],
            count_qty_each  = data['count_qty_each'],
            price_case      = data['price_case'],
            price_each      = data['price_each'],
            total_price     = data['total_price'],
            uom_case        = data['uom_case'],
            uom_each        = data['uom_each'],
        ))

    return records

# ── end of separated format merge ────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  COMBINED FORMAT — parse slash-delimited row into CountRecord
# ──────────────────────────────────────────────────────────────────────────────

def _parse_combined_row(row_dict: Dict, location: str) -> Optional[CountRecord]:
    """Convert a single combined-format row dict to a CountRecord."""
    desc = str(row_dict.get('description') or '').strip()
    if not desc or _is_skip_line(desc):
        return None

    seq       = str(row_dict.get('seq') or '').strip()
    pack_type = str(row_dict.get('pack_type') or '').strip()

    last_qc, last_uom_c, last_qe, last_uom_e = _parse_combined_qty(
        row_dict.get('last_qty') or '0')
    cnt_qc,  cnt_uom_c,  cnt_qe,  cnt_uom_e  = _parse_combined_qty(
        row_dict.get('inv_count') or '0')
    price_c, price_e = _parse_combined_price(row_dict.get('price') or '0')
    tot_price        = _to_float(row_dict.get('total_price'))

    key = _build_key(desc, pack_type)
    if not key:
        return None

    return CountRecord(
        location        = location,
        seq             = seq,
        item_description= desc,
        pack_type       = pack_type,
        item_key        = key,
        last_qty_case   = last_qc,
        last_qty_each   = last_qe,
        count_qty_case  = cnt_qc,
        count_qty_each  = cnt_qe,
        price_case      = price_c,
        price_each      = price_e,
        total_price     = tot_price,
        uom_case        = last_uom_c or cnt_uom_c or 'CS',
        uom_each        = last_uom_e or cnt_uom_e or 'EA',
    )

# ── end of combined format ────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  COUNT IMPORTER CLASS
# ──────────────────────────────────────────────────────────────────────────────

class CountImporter:

    def __init__(self, database):
        self.db     = database
        self.errors: List[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    #  PUBLIC — PARSE FILE
    # ──────────────────────────────────────────────────────────────────────────

    def parse(self, filename: str, content_bytes: bytes,
              default_location: str = "Unspecified") -> Tuple[List[CountRecord], Dict]:
        """
        Main entry point. Detects format and returns:
            (list of CountRecord, format_info dict)
        self.errors is populated with any parse issues.
        """
        self.errors = []
        fmt = detect_format(filename, content_bytes)
        ext = fmt['ext']

        try:
            if ext == 'csv':
                records = self._parse_csv(content_bytes, default_location)
            elif ext in ('xlsx', 'xls'):
                records = self._parse_xlsx(content_bytes)
            elif ext == 'pdf':
                records = self._parse_pdf(content_bytes)
            else:
                self.errors.append(f"Unsupported file type: {ext}")
                return [], fmt
        except Exception as e:
            self.errors.append(f"Parse error: {e}")
            return [], fmt

        fmt['record_count'] = len(records)
        fmt['locations']    = sorted(set(r.location for r in records))
        return records, fmt

    # ── end of public parse ───────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  CSV PARSER  —  Separated, single location, sorted by Seq
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_csv(self, content_bytes: bytes,
                   default_location: str = "Unspecified") -> List[CountRecord]:
        enc = detect_encoding(content_bytes)
        df = pd.read_csv(io.BytesIO(content_bytes), encoding=enc,
                         encoding_errors='replace', dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.where(pd.notna(df), None)
        return _merge_separated_df(df, location=default_location)

    # ── end of CSV parser ─────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  XLSX PARSER  —  Separated, all locations, col[0] = Classification
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_xlsx(self, content_bytes: bytes) -> List[CountRecord]:
        """
        The XLSX from myOrders has a header block (rows 0-3) then:
          Row 4:  'Grouped by: Classification' | 'Seq' | 'Pack Type' | ... (actual headers)
          Row 5+: data rows where col[0] = location name
        """
        raw = pd.read_excel(io.BytesIO(content_bytes), header=None, dtype=str)
        raw = raw.where(pd.notna(raw), None)

        # Find the header row (contains 'Seq' or 'Pack Type')
        header_row = 4  # default for known format
        for i, row in raw.iterrows():
            vals = [str(v).strip().lower() for v in row if v]
            if 'seq' in vals or 'pack type' in vals:
                header_row = i
                break

        df = raw.iloc[header_row + 1:].copy()
        df.columns = [str(v).strip() if v else f"col_{i}"
                      for i, v in enumerate(raw.iloc[header_row])]
        df = df.reset_index(drop=True)
        df = df.where(pd.notna(df), None)

        # The first column is the location / classification
        # Rename it and then split by location
        first_col = df.columns[0]
        df = df.rename(columns={first_col: 'location'})

        # Non-Chargeable rows: filter or tag them
        # (We keep them but mark is_chargeable=False)
        records: List[CountRecord] = []
        locations = df['location'].dropna().unique()

        for loc in locations:
            loc_str = str(loc).strip()
            if not loc_str:
                continue

            # Determine chargeability from location name
            is_chargeable = 'non-chargeable' not in loc_str.lower()

            loc_df = df[df['location'] == loc].copy()
            loc_df = loc_df.drop(columns=['location'])

            loc_records = _merge_separated_df(loc_df, location=loc_str)
            for r in loc_records:
                r.is_chargeable = is_chargeable
            records.extend(loc_records)

        return records

    # ── end of XLSX parser ────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  PDF PARSER  —  Combined slash-delimited, section headers = locations
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_pdf(self, content_bytes: bytes) -> List[CountRecord]:
        """
        Uses pdfplumber to extract tables from the count PDF.
        Section headers (non-table text) become location context.
        Qty/Price cells contain slash-delimited combined values.
        """
        try:
            import pdfplumber
        except ImportError:
            self.errors.append("pdfplumber not installed — PDF parsing unavailable.")
            return []

        records: List[CountRecord] = []
        current_location = "Unspecified"

        # Column name regex for detecting the header row within a table
        header_pattern = re.compile(
            r'(seq|pack type|last inventory|inv count|item description)',
            re.IGNORECASE
        )

        with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
            for page in pdf.pages:
                # ── Extract text lines for section header detection ──────────
                text_lines = (page.extract_text() or '').split('\n')

                # ── Extract tables ───────────────────────────────────────────
                tables = page.extract_tables()

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # Find the header row within this table
                    col_headers = None
                    data_start  = 0
                    for i, row in enumerate(table):
                        row_text = ' '.join(str(c or '') for c in row)
                        if header_pattern.search(row_text):
                            col_headers = [str(c or '').strip().upper() for c in row]
                            data_start  = i + 1
                            break

                    if col_headers is None:
                        # Try to infer headers from position (fallback)
                        continue

                    # Map header positions
                    col_idx = {}
                    for ci, h in enumerate(col_headers):
                        mapped = COUNT_COL_MAP.get(h)
                        if mapped:
                            col_idx[mapped] = ci
                        # Also check partial matches
                        elif 'LAST INVENTORY' in h:
                            col_idx['last_qty'] = ci
                        elif 'INV COUNT' in h:
                            col_idx['inv_count'] = ci
                        elif 'ITEM DESCRIPTION' in h:
                            col_idx['description'] = ci
                        elif h == 'SEQ':
                            col_idx['seq'] = ci
                        elif 'PACK TYPE' in h:
                            col_idx['pack_type'] = ci
                        elif h == 'UOM':
                            col_idx['uom'] = ci
                        elif h == 'PRICE':
                            col_idx['price'] = ci
                        elif 'TOTAL PRICE' in h:
                            col_idx['total_price'] = ci

                    def _cell(row, field):
                        idx = col_idx.get(field)
                        if idx is None or idx >= len(row):
                            return None
                        return str(row[idx] or '').strip()

                    for row in table[data_start:]:
                        if not row or all(not c for c in row):
                            continue

                        # Check if this is a section-header row (location change)
                        row_text = ' '.join(str(c or '') for c in row).strip()
                        if _is_section_header(row_text, col_idx):
                            # Extract location name from first non-empty cell
                            for cell in row:
                                if cell and str(cell).strip():
                                    loc_candidate = str(cell).strip()
                                    if not _is_skip_line(loc_candidate):
                                        current_location = loc_candidate
                                    break
                            continue

                        row_dict = {
                            'seq':         _cell(row, 'seq'),
                            'pack_type':   _cell(row, 'pack_type'),
                            'last_qty':    _cell(row, 'last_qty'),
                            'inv_count':   _cell(row, 'inv_count'),
                            'description': _cell(row, 'description'),
                            'uom':         _cell(row, 'uom'),
                            'price':       _cell(row, 'price'),
                            'total_price': _cell(row, 'total_price'),
                        }

                        rec = _parse_combined_row(row_dict, current_location)
                        if rec:
                            records.append(rec)

                # ── Scan text lines for location headers not inside tables ───
                for line in text_lines:
                    line = line.strip()
                    if not line:
                        continue
                    # Location headers are lines like "Track Market" or
                    # "Ferttita->Stand 101" that aren't data rows
                    if _is_location_line(line):
                        current_location = line

        return records

    # ── end of PDF parser ─────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  VARIANCE CALCULATION  —  compare records vs. database, NO writes
    #  One bulk DB query replaces the previous per-item query loop.
    # ──────────────────────────────────────────────────────────────────────────

    def calculate_variance(
        self,
        records:              List[CountRecord],
        flag_each_threshold:  int   = DEFAULT_FLAG_EACH,
        flag_value_threshold: float = DEFAULT_FLAG_VALUE,
    ) -> List[VarianceRecord]:
        """
        Diff every CountRecord against the current DB qty.
        Fetches ALL required items in a single query, then diffs in memory.
        Returns a list of VarianceRecord objects — no DB writes.
        """
        if not records:
            return []

        # ── Single bulk fetch ────────────────────────────────────────────────
        all_keys = list({r.item_key for r in records})
        db_map   = self.db.get_items_bulk(all_keys)   # {key: item_dict}

        # ── In-memory diff ───────────────────────────────────────────────────
        variance_list: List[VarianceRecord] = []

        for rec in records:
            db_item     = db_map.get(rec.item_key)
            in_db       = db_item is not None
            db_qty      = float(db_item.get("quantity_on_hand") or 0) if in_db else 0.0
            db_price_ea = float(db_item.get("cost") or 0)             if in_db else rec.price_each

            # ── Resolve conv_ratio ───────────────────────────────────────────
            #  Priority:
            #    1. DB conv_ratio if set and > 0
            #    2. Leading integer in pack_type string  ("12/24oz CAN" → 12)
            #    3. Default = 1  — null/zero NEVER silently drops case qty
            conv = None

            if in_db:
                try:
                    db_conv = float(db_item.get("conv_ratio") or 0)
                    if db_conv > 0:
                        conv = db_conv
                except (ValueError, TypeError):
                    pass

            if conv is None:
                pt_match = re.match(r'^(\d+)/', str(rec.pack_type or '').strip())
                if pt_match:
                    extracted = int(pt_match.group(1))
                    if extracted > 0:
                        conv = float(extracted)

            if conv is None:
                conv = 1.0   # absolute fallback — never drop case qty

            new_qty = rec.count_qty_each + (rec.count_qty_case * conv)

            variance_each   = new_qty - db_qty
            eff_price       = rec.price_each if rec.price_each > 0 else db_price_ea
            variance_value  = variance_each * eff_price

            is_flagged  = False
            flag_reason = ""
            if not in_db:
                is_flagged  = True
                flag_reason = "Item not found in database"
            elif abs(variance_each) > flag_each_threshold:
                is_flagged  = True
                flag_reason = f"Unit variance {variance_each:+.1f} exceeds threshold {flag_each_threshold}"
            elif abs(variance_value) > flag_value_threshold:
                is_flagged  = True
                flag_reason = f"Value variance ${variance_value:+.2f} exceeds threshold ${flag_value_threshold:.2f}"

            variance_list.append(VarianceRecord(
                record          = rec,
                db_qty          = db_qty,
                db_price_each   = db_price_ea,
                new_qty         = new_qty,
                variance_each   = variance_each,
                variance_value  = variance_value,
                in_db           = in_db,
                is_flagged      = is_flagged,
                flag_reason     = flag_reason,
            ))

        return variance_list

    # ── end of variance calculation ───────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  EXECUTE IMPORT  —  write qty updates + variance log to DB
    # ──────────────────────────────────────────────────────────────────────────

    def execute_count_import(
        self,
        variance_records: List[VarianceRecord],
        meta:             CountImportMeta,
        add_missing:      bool = False,
    ) -> Dict:
        """
        Writes the count to the database:
        1. Optionally creates new items for records not currently in DB
        2. Updates quantity_on_hand for each matched item
        3. Logs the import in count_imports
        4. Saves all variance detail rows in count_variance_detail

        add_missing=True:  items not found in DB are created from CountRecord
                           data (description, pack_type, cost, location, etc.)
                           and then their qty is set from the count.
        add_missing=False: items not in DB are skipped (original behavior).

        Returns a summary dict.
        """
        results = {
            'import_id':        meta.import_id,
            'items_created':    0,
            'items_updated':    0,
            'items_skipped':    0,
            'items_flagged':    0,
            'errors':           [],
            'total_prev_value': 0.0,
            'total_new_value':  0.0,
            'total_variance_value': 0.0,
        }

        # ── Pre-compute value totals ─────────────────────────────────────────
        for vr in variance_records:
            rec = vr.record
            results['total_prev_value']    += vr.db_qty * (
                vr.db_price_each if vr.db_price_each else rec.price_each)
            results['total_new_value']     += rec.total_price   # authoritative export value
            results['total_variance_value'] += vr.variance_value
            if vr.is_flagged:
                results['items_flagged'] += 1

        # ── Pass 1: create missing items if requested ────────────────────────
        if add_missing:
            missing = [vr for vr in variance_records if not vr.in_db]
            for vr in missing:
                rec = vr.record
                new_item = {
                    'key':              rec.item_key,
                    'description':      rec.item_description.strip().upper(),
                    'pack_type':        rec.pack_type,
                    'cost':             rec.price_each or rec.price_case or 0.0,
                    'quantity_on_hand': vr.new_qty,
                    'is_chargeable':    rec.is_chargeable,
                    'cost_center':      meta.cost_center,
                    'status_tag':       '📦 From Count',
                    'user_notes':       (
                        f"Created from count import {meta.import_id} "
                        f"· location: {rec.location} · {meta.count_date}"
                    ),
                }
                try:
                    ok = self.db.add_item(new_item, changed_by=meta.imported_by)
                    if ok:
                        results['items_created'] += 1
                        # Mark as now in_db so Pass 2 can update qty
                        vr.in_db = True
                    else:
                        results['errors'].append(
                            f"Could not create {rec.item_key} (may already exist)")
                        results['items_skipped'] += 1
                except Exception as e:
                    results['errors'].append(f"Create {rec.item_key}: {e}")
                    results['items_skipped'] += 1

        # ── Pass 2: update qty for all in-DB items ───────────────────────────
        for vr in variance_records:
            rec = vr.record
            if not vr.in_db:
                results['items_skipped'] += 1
                continue
            try:
                ok = self.db.update_quantity_from_count(
                    key        = rec.item_key,
                    new_qty    = vr.new_qty,
                    import_id  = meta.import_id,
                    changed_by = meta.imported_by,
                )
                if ok:
                    results['items_updated'] += 1
                else:
                    results['items_skipped'] += 1
            except Exception as e:
                results['errors'].append(f"{rec.item_key}: {e}")
                results['items_skipped'] += 1

        # ── Pass 3: save import metadata ────────────────────────────────────
        try:
            self.db.log_count_import(
                import_id        = meta.import_id,
                source_file      = meta.source_file,
                file_format      = meta.file_format,
                data_layout      = meta.data_layout,
                count_type       = meta.count_type,
                count_date       = meta.count_date,
                cost_center      = meta.cost_center,
                imported_by      = meta.imported_by,
                total_items      = len(variance_records),
                items_changed    = results['items_updated'],
                items_flagged    = results['items_flagged'],
                total_prev_value = results['total_prev_value'],
                total_new_value  = results['total_new_value'],
                variance_value   = results['total_variance_value'],
            )
        except Exception as e:
            results['errors'].append(f"Import log write failed: {e}")

        # ── Pass 4: save variance detail ─────────────────────────────────────
        try:
            detail_rows = []
            for vr in variance_records:
                rec = vr.record
                detail_rows.append({
                    'import_id':        meta.import_id,
                    'location':         rec.location,
                    'seq':              rec.seq,
                    'item_key':         rec.item_key,
                    'item_description': rec.item_description,
                    'pack_type':        rec.pack_type,
                    'prev_qty_each':    vr.db_qty,
                    'new_qty_each':     vr.new_qty,
                    'count_qty_case':   rec.count_qty_case,
                    'count_qty_each':   rec.count_qty_each,
                    'price_each':       rec.price_each,
                    'variance_each':    vr.variance_each,
                    'variance_value':   vr.variance_value,
                    'is_flagged':       vr.is_flagged,
                    'flag_reason':      vr.flag_reason,
                })
            self.db.save_count_variance_records(detail_rows)
        except Exception as e:
            results['errors'].append(f"Variance detail write failed: {e}")

        return results

    # ── end of execute import ─────────────────────────────────────────────────

# ── end of CountImporter class ────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  PDF HELPER FUNCTIONS  (module-level so they're accessible inside the parser)
# ──────────────────────────────────────────────────────────────────────────────

def _is_section_header(row_text: str, col_idx: Dict) -> bool:
    """
    Returns True if this table row looks like a section header
    (location name row) rather than a data row.
    A header row typically: has a description field that contains an
    arrow pattern like "Ferttita->Stand 101" or is just one non-numeric cell.
    """
    text = row_text.strip()
    if not text:
        return False
    # Location names often contain "->" separators
    if '->' in text:
        return True
    # Section header rows have very few columns populated
    non_empty = [c for c in text.split() if c]
    # If it looks like a plain label with no numbers, likely a header
    has_digits = bool(re.search(r'\d', text))
    if not has_digits and len(non_empty) <= 6:
        return True
    return False


def _is_location_line(line: str) -> bool:
    """
    Returns True if a text line (outside a table) looks like a location header.
    Examples: "Track Market", "Ferttita->Stand 101", "TDECU - Concessions->Cooler"
    """
    line = line.strip()
    if not line or len(line) < 3:
        return False
    if _is_skip_line(line):
        return False
    # Location names don't start with digits
    if line[0].isdigit():
        return False
    # Location names don't contain $ signs
    if '$' in line:
        return False
    # Arrow separator is a strong indicator
    if '->' in line or '→' in line:
        return True
    # Known top-level sections
    known = ('track market', 'ferttita', 'tdecu', 'concessions',
             'softball', 'uha schroeder', 'liquor room', 'warehouse',
             'cooler', 'popcorn room', 'vending')
    low = line.lower()
    if any(k in low for k in known):
        return True
    return False

# ── end of PDF helper functions ───────────────────────────────────────────────
