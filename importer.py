# ──────────────────────────────────────────────────────────────────────────────
#  importer.py  —  Inventory Importer  —  Canonical Version
#  Key format : "ITEM NAME||PACKTYPE"  (uppercase, double-pipe)
#  Supports   : Type B vendor invoice CSVs (B1 and B2 subtypes)
#  PAC PDFs   : handled via pac_importer.py (separate module)
# ──────────────────────────────────────────────────────────────────────────────

import pandas as pd
import re
import hashlib
from typing import List, Dict, Tuple, Optional
from datetime import datetime

# ── end of imports ────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  SCALAR HELPER  —  prevents "truth value of Series is ambiguous" errors
# ──────────────────────────────────────────────────────────────────────────────

def _scalar(val):
    """Always return a plain scalar value, never a Series."""
    if isinstance(val, pd.Series):
        val = val.iloc[0] if not val.empty else None
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    return val

# ── end of scalar helper ─────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  FILE HASH HELPER  —  used for duplicate-import detection
# ──────────────────────────────────────────────────────────────────────────────

def file_hash(content: bytes) -> str:
    """Return a short SHA-256 hex digest of raw file bytes."""
    return hashlib.sha256(content).hexdigest()[:16]

# ── end of file hash helper ───────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  NORMALIZERS  —  from Production Power Query logic
# ──────────────────────────────────────────────────────────────────────────────

SKIP_PHRASES = [
    "PROPERTY OF COMPASS GROUP", "PRINTED BY", "BILL TO",
    "SHIP TO", "ITEMS ORDERED", "TOTAL COST ORDERED"
]

PACK_NORM = {
    'SLVS': 'SLEEVE', 'SLV': 'SLEEVE',
    'CASE': 'CASE', 'CSE': 'CASE', 'CS': 'CASE', 'CA': 'CASE',
    'CTN': 'CASE', 'CT': 'CASE',
    'EACH': 'EACH', 'EA': 'EACH', 'E': 'EACH',
}


def normalize_pack_type(raw: str) -> str:
    """fxNormalizePackType — matches production Power Query logic."""
    if raw is None:
        return 'CASE'
    try:
        if pd.isna(raw):
            return 'CASE'
    except (TypeError, ValueError):
        pass
    s = str(raw).strip().upper()
    s = re.sub(r'[^A-Z0-9/\s\-X.]', '', s)
    s = re.sub(r'/EACH$', '/EA', s)
    s = re.sub(r'/1$',    '/EA', s)
    parts  = re.split(r'([^A-Z0-9])', s)
    normed = [PACK_NORM.get(p, p) for p in parts]
    result = ''.join(normed).strip()
    return result if result else 'CASE'


def build_key(item_name: str, pack_type: str) -> Optional[str]:
    """Canonical key: 'ITEM NAME||PACKTYPE' both uppercase."""
    name = str(item_name or '').strip().upper()
    pack = normalize_pack_type(pack_type)
    if not name:
        return None
    return f"{name}||{pack}"


def clean_price(value) -> Optional[float]:
    """Strip $, commas, whitespace; return float or None."""
    value = _scalar(value)
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    s = re.sub(r'[$,\s]', '', str(value))
    try:
        return float(s)
    except ValueError:
        return None


def split_gl_field(gl_string: str) -> Tuple[str, str]:
    """
    'Produce 411085'  ->  ('Produce', '411085')
    '411085'          ->  ('',        '411085')
    """
    if not gl_string:
        return ('', '')
    try:
        if pd.isna(gl_string):
            return ('', '')
    except (TypeError, ValueError):
        pass
    s = str(gl_string).strip()
    m = re.search(r'^(.*?)\s*(\d{6})\s*$', s)
    if m:
        return (m.group(1).strip(), m.group(2))
    if re.fullmatch(r'\d{6}', s):
        return ('', s)
    return (s, '')


def should_skip_row(row_values) -> bool:
    row_str = ' '.join(str(v) for v in row_values if pd.notna(v)).upper()
    return any(p in row_str for p in SKIP_PHRASES)

# ── end of normalizers ────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  HEADER DETECTION
# ──────────────────────────────────────────────────────────────────────────────

HEADER_REQUIRED = ['ITEM', 'DESC', 'PRODUCT']
HEADER_PACK     = ['PACK', 'UOM']
HEADER_PRICE    = ['PRICE', 'COST', 'INVOICED']


def _is_header_row(row) -> bool:
    vals      = [str(v).upper() for v in row if pd.notna(v)]
    joined    = ' '.join(vals)
    has_item  = any(k in joined for k in HEADER_REQUIRED)
    has_pack  = any(k in joined for k in HEADER_PACK)
    has_price = any(k in joined for k in HEADER_PRICE)
    return has_item and (has_pack or has_price)


def find_header_row(df: pd.DataFrame, max_rows: int = 25) -> int:
    for i, row in df.iterrows():
        if i > max_rows:
            break
        if _is_header_row(row.values):
            return i
    return 0

# ── end of header detection ───────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  COLUMN NORMALIZER  —  maps raw header names to canonical field names
# ──────────────────────────────────────────────────────────────────────────────

COL_MAP = {
    'ITEM DESCRIPTION': 'description', 'ITEM DESC': 'description',
    'DESCRIPTION':      'description', 'ITEM':      'description',
    'DESC':             'description', 'PRODUCT':   'description',
    'PACK TYPE':        'pack_type',   'PACK':      'pack_type',
    'UOM':              'pack_type',
    'INVOICED PRICE':   'cost',  'CONFIRMED PRICE': 'cost',
    'CURRENT PRICE':    'cost',  'COST':            'cost',
    'PRICE':            'cost',
    'INVOICED QUANTITY':   'quantity', 'CONFIRMED QUANTITY': 'quantity',
    'QUANTITY':            'quantity',
    'GL CODE':  'gl_field', 'GL':      'gl_field', 'ACCOUNT': 'gl_field',
    'VENDOR':   'vendor',   'VENDORS': 'vendor',
    'ITEM NUMBER': 'item_number',
    'MOG':   'mog',  'BRAND': 'brand', 'MFG':  'brand',
    'GTIN':  'gtin',
    'STATUS': 'status', 'CONFIRMATION STATUS': 'status',
    'CATEGORY':      'category',
    'DELIVERY DATE': 'delivery_date',
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for col in df.columns:
        key = str(col).strip().upper()
        if key in COL_MAP:
            rename[col] = COL_MAP[key]
    df = df.rename(columns=rename)
    # Drop duplicate columns produced by renaming, keeping first occurrence
    df = df.loc[:, ~df.columns.duplicated()]
    return df

# ── end of column normalizer ──────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
#  INVENTORY IMPORTER CLASS
# ──────────────────────────────────────────────────────────────────────────────

class InventoryImporter:

    def __init__(self, database):
        self.db     = database
        self.errors: List[str] = []

    # ──────────────────────────────────────────────────────────────────────────
    #  FILE READING
    # ──────────────────────────────────────────────────────────────────────────

    def read_file(self, filepath: str) -> Optional[pd.DataFrame]:
        self.errors = []
        try:
            if filepath.lower().endswith('.csv'):
                df = pd.read_csv(filepath, encoding='utf-8-sig', dtype=str)
            elif filepath.lower().endswith(('.xlsx', '.xls')):
                df = pd.read_excel(filepath, header=None, dtype=str)
                hdr        = find_header_row(df)
                df.columns = df.iloc[hdr]
                df         = df.iloc[hdr + 1:].reset_index(drop=True)
            else:
                self.errors.append(f"Unsupported file type: {filepath}")
                return None
            df = normalize_columns(df)
            return df
        except Exception as e:
            self.errors.append(f"Read error: {e}")
            return None

    # ── end of file reading ───────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  ANALYSIS PASS  —  preview changes, no DB writes
    # ──────────────────────────────────────────────────────────────────────────

    def analyze_import(self, df: pd.DataFrame) -> Dict:
        analysis = {
            'total_rows': len(df),
            'new_items':  [],
            'updates':    [],
            'skipped':    [],
            'errors':     [],
        }

        for idx, row in df.iterrows():
            row = row.where(pd.notna(row), None)

            if should_skip_row(row.values):
                analysis['skipped'].append(idx)
                continue

            status = str(_scalar(row.get('status')) or '').upper()
            pack   = str(_scalar(row.get('pack_type')) or '')
            if 'SUBSTITUTION' in status or pack.strip() == '99':
                analysis['skipped'].append(idx)
                continue

            description = _scalar(row.get('description'))
            if not description:
                continue

            pack_raw  = _scalar(row.get('pack_type')) or ''
            pack_norm = normalize_pack_type(pack_raw)
            key       = build_key(description, pack_norm)
            if not key:
                analysis['errors'].append(f"Row {idx+1}: Could not build key")
                continue

            item_data = self._prepare_row(row, key, pack_norm)

            if self.db.item_exists(key):
                current = self.db.get_item(key)
                changes = {
                    f: {'old': current.get(f), 'new': item_data.get(f)}
                    for f in ('cost', 'pack_type', 'vendor', 'gl_code')
                    if item_data.get(f) and str(current.get(f)) != str(item_data.get(f))
                }
                analysis['updates'].append({
                    'key':         key,
                    'description': str(description),
                    'changes':     changes,
                    'row_data':    item_data,
                })
            else:
                analysis['new_items'].append({
                    'key':         key,
                    'description': str(description),
                    'row_data':    item_data,
                })

        return analysis

    # ── end of analysis pass ──────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  EXECUTE  —  write selected items to DB
    # ──────────────────────────────────────────────────────────────────────────

    def execute_import(self, analysis: Dict,
                       changed_by: str = "import",
                       source_document: str = None,
                       doc_date: str = None) -> Dict:
        results = {'new_items_added': 0, 'items_updated': 0, 'errors': []}

        for item in analysis['new_items']:
            try:
                if self.db.add_item(item['row_data'], changed_by=changed_by):
                    results['new_items_added'] += 1
            except Exception as e:
                results['errors'].append(f"{item['key']}: {e}")

        for item in analysis['updates']:
            try:
                result = self.db.upsert_item(
                    item['row_data'],
                    doc_date=doc_date,
                    source_document=source_document,
                    changed_by=changed_by,
                )
                if result in ('updated', 'created'):
                    results['items_updated'] += 1
            except Exception as e:
                results['errors'].append(f"{item['key']}: {e}")

        return results

    # ── end of execute ────────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  FULL PIPELINE  —  read → analyze → (optionally) execute in one call
    # ──────────────────────────────────────────────────────────────────────────

    def import_file(self, filepath: str,
                    changed_by: str = "import",
                    auto_approve: bool = True) -> Tuple[Dict, Dict]:
        df = self.read_file(filepath)
        if df is None:
            return {'errors': self.errors}, {}
        analysis = self.analyze_import(df)
        if auto_approve:
            from pathlib import Path
            doc_date = datetime.now().strftime('%Y-%m-%d')
            results  = self.execute_import(
                analysis,
                changed_by=changed_by,
                source_document=Path(filepath).name,
                doc_date=doc_date,
            )
            return analysis, results
        return analysis, {}

    # ── end of full pipeline ──────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────────────────
    #  ROW PREPARATION  —  maps a raw DataFrame row to a clean item dict
    # ──────────────────────────────────────────────────────────────────────────

    def _prepare_row(self, row, key: str, pack_norm: str) -> Dict:
        item = {
            'key':         key,
            'description': str(_scalar(row.get('description')) or '').strip().upper(),
            'pack_type':   pack_norm,
        }

        cost = clean_price(_scalar(row.get('cost')))
        if cost is not None:
            item['cost'] = cost

        for field in ('vendor', 'item_number', 'mog', 'brand', 'gtin'):
            val = _scalar(row.get(field))
            if val:
                item[field] = str(val).strip()

        # GL field — may be combined e.g. "Produce 411085"
        gl_raw = _scalar(row.get('gl_field')) or _scalar(row.get('gl_code')) or ''
        if gl_raw:
            gl_name, gl_code = split_gl_field(str(gl_raw))
            if gl_code:
                item['gl_code'] = gl_code
                item['gl_name'] = gl_name
            elif gl_name:
                item['gl_code'] = gl_name

        qty = _scalar(row.get('quantity'))
        if qty is not None:
            try:
                item['quantity_on_hand'] = float(re.sub(r'[^\d.]', '', str(qty)))
            except (ValueError, TypeError):
                pass

        return item

    # ── end of row preparation ────────────────────────────────────────────────

# ── end of InventoryImporter class ───────────────────────────────────────────
