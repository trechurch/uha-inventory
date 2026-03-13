"""
Inventory Importer - Canonical Version
Key format: "ITEM NAME||PACKTYPE" (uppercase, double-pipe)
Supports: Type B vendor invoice CSVs (B1 and B2 subtypes)
PAC inventory PDFs handled via count_importer.py (separate)

v4.0.0 — Added:
  - fuzzy_match_description(): difflib-based candidate lookup (stdlib only)
  - score_import_row(): 0–100 confidence scoring per row
  - analyze_import() now returns 'fuzzy_matches' bucket and
    'confidence' on every item in new_items / updates / fuzzy_matches
  - existing_descriptions loaded once per analyze run (no N+1 DB calls)
"""
import difflib
import pandas as pd
import re
from typing import List, Dict, Tuple, Optional
from datetime import datetime


# -----------------------------------------------------------------------
# NORMALIZERS  (from Production Power Query)
# -----------------------------------------------------------------------
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
    if not raw or pd.isna(raw):
        return 'CASE'
    s = str(raw).strip().upper()
    # Keep only valid chars
    s = re.sub(r'[^A-Z0-9/\s\-X.]', '', s)
    # Replace /EACH and /1 endings
    s = re.sub(r'/EACH$', '/EA', s)
    s = re.sub(r'/1$', '/EA', s)
    # Normalise suffix tokens
    parts = re.split(r'([^A-Z0-9])', s)
    normed = []
    for p in parts:
        normed.append(PACK_NORM.get(p, p))
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
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = re.sub(r'[$,\s]', '', str(value))
    try:
        return float(s)
    except ValueError:
        return None


def split_gl_field(gl_string: str) -> Tuple[str, str]:
    """
    'Produce 411085' -> ('Produce', '411085')
    '411085'         -> ('', '411085')
    """
    if not gl_string or pd.isna(gl_string):
        return ('', '')
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


# -----------------------------------------------------------------------
# HEADER DETECTION
# -----------------------------------------------------------------------
HEADER_REQUIRED = ['ITEM', 'DESC', 'PRODUCT']
HEADER_PACK     = ['PACK', 'UOM']
HEADER_PRICE    = ['PRICE', 'COST', 'INVOICED']

def _is_header_row(row) -> bool:
    vals = [str(v).upper() for v in row if pd.notna(v)]
    joined = ' '.join(vals)
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


# -----------------------------------------------------------------------
# COLUMN NORMALISER
# -----------------------------------------------------------------------
COL_MAP = {
    'ITEM DESCRIPTION': 'description', 'ITEM DESC': 'description',
    'DESCRIPTION': 'description', 'ITEM': 'description',
    'DESC': 'description', 'PRODUCT': 'description',
    'PACK TYPE': 'pack_type', 'PACK': 'pack_type',
    'UOM': 'pack_type',
    'INVOICED PRICE': 'cost', 'CONFIRMED PRICE': 'cost',
    'CURRENT PRICE': 'cost', 'COST': 'cost', 'PRICE': 'cost',
    'INVOICED QUANTITY': 'quantity', 'CONFIRMED QUANTITY': 'quantity',
    'QUANTITY': 'quantity',
    'GL CODE': 'gl_field', 'GL': 'gl_field', 'ACCOUNT': 'gl_field',
    'VENDOR': 'vendor', 'VENDORS': 'vendor',
    'ITEM NUMBER': 'item_number',
    'MOG': 'mog', 'BRAND': 'brand', 'MFG': 'brand',
    'GTIN': 'gtin',
    'STATUS': 'status', 'CONFIRMATION STATUS': 'status',
    'CATEGORY': 'category',
    'DELIVERY DATE': 'delivery_date',
}

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for col in df.columns:
        key = str(col).strip().upper()
        if key in COL_MAP:
            rename[col] = COL_MAP[key]
    return df.rename(columns=rename)


# -----------------------------------------------------------------------
# FUZZY MATCHING
# -----------------------------------------------------------------------
FUZZY_THRESHOLD = 0.82   # minimum SequenceMatcher ratio to suggest a match

def fuzzy_match_description(
    description: str,
    existing_descriptions: Dict[str, str],
    threshold: float = FUZZY_THRESHOLD,
    n: int = 3
) -> List[Dict]:
    """
    Find the closest existing item descriptions using difflib.
    Uses stdlib only — no extra dependencies.

    Args:
        description:           Incoming item description (will be uppercased).
        existing_descriptions: Dict of {UPPER_DESCRIPTION: item_key}
                               from db.get_all_descriptions().
        threshold:             Minimum similarity ratio (0.0–1.0).
                               Default 0.82 catches vendor typos / spacing
                               differences without over-matching.
        n:                     Max number of candidates to return.

    Returns:
        List of dicts sorted by score descending:
        [{'description': str, 'key': str, 'score': int (0–100)}, ...]
        Empty list if no match above threshold.
    """
    if not description or not existing_descriptions:
        return []
    query = str(description).strip().upper()
    candidates = []
    for existing_desc, existing_key in existing_descriptions.items():
        ratio = difflib.SequenceMatcher(
            None, query, existing_desc, autojunk=False
        ).ratio()
        if ratio >= threshold:
            candidates.append({
                "description": existing_desc,
                "key":         existing_key,
                "score":       round(ratio * 100),
            })
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:n]


# -----------------------------------------------------------------------
# CONFIDENCE SCORING
# -----------------------------------------------------------------------
def score_import_row(
    key: str,
    item_data: Dict,
    match_type: str,          # 'exact', 'fuzzy', 'new'
    fuzzy_score: int = 0,     # 0–100, only used when match_type='fuzzy'
) -> int:
    """
    Returns an integer confidence score (0–100) for an import row.

    Scoring logic:
      exact match  → starts at 100
      fuzzy match  → starts at fuzzy_score (capped 60–90)
      new item     → starts at 40

    Bonuses (applied to all types):
      +5   has a valid cost
      +5   has a GL code
      +5   has a vendor name
      +3   has an item number

    Penalties:
      -10  description appears suspiciously short (< 4 chars)
      -10  key contains '???' or placeholder text
    """
    if match_type == 'exact':
        base = 100
    elif match_type == 'fuzzy':
        base = max(60, min(90, fuzzy_score))
    else:  # 'new'
        base = 40

    bonus = 0
    if item_data.get("cost") and float(item_data.get("cost") or 0) > 0:
        bonus += 5
    if item_data.get("gl_code"):
        bonus += 5
    if item_data.get("vendor"):
        bonus += 5
    if item_data.get("item_number"):
        bonus += 3

    penalty = 0
    desc = str(item_data.get("description") or "")
    if len(desc.strip()) < 4:
        penalty += 10
    if "???" in key or "UNKNOWN" in key:
        penalty += 10

    return max(0, min(100, base + bonus - penalty))


# -----------------------------------------------------------------------
# MAIN IMPORTER CLASS
# -----------------------------------------------------------------------
class InventoryImporter:

    def __init__(self, database):
        self.db = database
        self.errors: List[str] = []

    # -- File reading --------------------------------------------------
    def read_file(self, filepath: str) -> Optional[pd.DataFrame]:
        self.errors = []
        try:
            if filepath.lower().endswith('.csv'):
                df = pd.read_csv(filepath, encoding='utf-8-sig', dtype=str)
            elif filepath.lower().endswith(('.xlsx', '.xls')):
                df = pd.read_excel(filepath, header=None, dtype=str)
                hdr = find_header_row(df)
                df.columns = df.iloc[hdr]
                df = df.iloc[hdr + 1:].reset_index(drop=True)
            else:
                self.errors.append(f"Unsupported file type: {filepath}")
                return None
            df = normalize_columns(df)
            return df
        except Exception as e:
            self.errors.append(f"Read error: {e}")
            return None

    # -- Analysis pass (preview, no DB writes) -------------------------
    def analyze_import(self, df: pd.DataFrame) -> Dict:
        """
        Classifies every row into one of four buckets:
          new_items     — clean new items, no DB match
          updates       — exact key match against existing item
          fuzzy_matches — no exact match but description is ~similar
                          to an existing item; surfaced for user review
          skipped       — metadata / substitution rows
          errors        — rows that could not be processed

        Every item in new_items, updates, and fuzzy_matches carries:
          'confidence'  — integer 0–100 (see score_import_row)

        fuzzy_matches items additionally carry:
          'fuzzy_candidates' — list of closest existing items with scores
        """
        analysis = {
            'total_rows':    len(df),
            'new_items':     [],
            'updates':       [],
            'fuzzy_matches': [],   # ← new bucket
            'skipped':       [],
            'errors':        [],
        }

        # Load all existing descriptions once — prevents N+1 DB calls
        # Falls back gracefully if db doesn't support the method yet
        try:
            existing_descriptions = self.db.get_all_descriptions()
        except Exception:
            existing_descriptions = {}

        for idx, row in df.iterrows():
            row = row.where(pd.notna(row), None)

            # Skip metadata rows
            if should_skip_row(row.values):
                analysis['skipped'].append(idx)
                continue

            # Skip B2 substitution rows
            status = str(row.get('status') or '').upper()
            pack   = str(row.get('pack_type') or '')
            if 'SUBSTITUTION' in status or pack.strip() == '99':
                analysis['skipped'].append(idx)
                continue

            description = row.get('description')
            if not description:
                continue

            pack_raw  = row.get('pack_type') or ''
            pack_norm = normalize_pack_type(pack_raw)
            key       = build_key(description, pack_norm)
            if not key:
                analysis['errors'].append(f"Row {idx+1}: Could not build key")
                continue

            item_data = self._prepare_row(row, key, pack_norm)

            # ── Exact match ──────────────────────────────────────────
            if self.db.item_exists(key):
                current = self.db.get_item(key)
                changes = {
                    f: {'old': current.get(f), 'new': item_data.get(f)}
                    for f in ('cost', 'pack_type', 'vendor', 'gl_code')
                    if item_data.get(f) and str(current.get(f)) != str(item_data.get(f))
                }
                confidence = score_import_row(key, item_data, 'exact')
                analysis['updates'].append({
                    'key':         key,
                    'description': str(description),
                    'changes':     changes,
                    'row_data':    item_data,
                    'confidence':  confidence,
                })
                continue

            # ── No exact match — run fuzzy ───────────────────────────
            desc_upper = str(description).strip().upper()
            fuzzy_candidates = fuzzy_match_description(
                desc_upper, existing_descriptions
            )

            if fuzzy_candidates:
                # Best fuzzy candidate drives the score
                best_score = fuzzy_candidates[0]["score"]
                confidence = score_import_row(
                    key, item_data, 'fuzzy', fuzzy_score=best_score
                )
                analysis['fuzzy_matches'].append({
                    'key':              key,
                    'description':      str(description),
                    'row_data':         item_data,
                    'confidence':       confidence,
                    'fuzzy_candidates': fuzzy_candidates,
                })
            else:
                # Genuinely new item
                confidence = score_import_row(key, item_data, 'new')
                analysis['new_items'].append({
                    'key':         key,
                    'description': str(description),
                    'row_data':    item_data,
                    'confidence':  confidence,
                })

        return analysis

    # -- Execute -------------------------------------------------------
    def execute_import(self, analysis: Dict,
                       changed_by: str = "import",
                       source_document: str = None,
                       doc_date: str = None,
                       include_fuzzy: bool = False) -> Dict:
        """
        Commits approved import rows to the database.

        include_fuzzy=False (default): fuzzy_matches are NOT committed
            automatically — they require explicit user approval first.
        include_fuzzy=True: fuzzy matches are treated as new items
            (use only when the user has reviewed and approved them).
        """
        results = {
            'new_items_added':    0,
            'items_updated':      0,
            'fuzzy_skipped':      len(analysis.get('fuzzy_matches', [])),
            'errors':             [],
        }

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
                    changed_by=changed_by
                )
                if result in ('updated', 'created'):
                    results['items_updated'] += 1
            except Exception as e:
                results['errors'].append(f"{item['key']}: {e}")

        if include_fuzzy:
            results['fuzzy_skipped'] = 0
            for item in analysis.get('fuzzy_matches', []):
                try:
                    if self.db.add_item(item['row_data'], changed_by=changed_by):
                        results['new_items_added'] += 1
                except Exception as e:
                    results['errors'].append(f"{item['key']}: {e}")

        return results

    # -- Full pipeline -------------------------------------------------
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
                include_fuzzy=False   # fuzzy always requires manual review
            )
            return analysis, results
        return analysis, {}

    # -- Row prep ------------------------------------------------------
    def _prepare_row(self, row, key: str, pack_norm: str) -> Dict:
        item = {
            'key':         key,
            'description': str(row.get('description') or '').strip().upper(),
            'pack_type':   pack_norm,
        }

        cost = clean_price(row.get('cost'))
        if cost is not None:
            item['cost'] = cost

        if row.get('vendor'):
            item['vendor'] = str(row['vendor']).strip()
        if row.get('item_number'):
            item['item_number'] = str(row['item_number']).strip()
        if row.get('mog'):
            item['mog'] = str(row['mog']).strip()
        if row.get('brand'):
            item['brand'] = str(row['brand']).strip()
        if row.get('gtin'):
            item['gtin'] = str(row['gtin']).strip()

        # GL field — may be "Produce 411085" combined
        gl_raw = row.get('gl_field') or row.get('gl_code') or ''
        if gl_raw:
            gl_name, gl_code = split_gl_field(str(gl_raw))
            if gl_code:
                item['gl_code'] = gl_code
                item['gl_name'] = gl_name
            elif gl_name:
                item['gl_code'] = gl_name  # bare code stored as-is

        qty = row.get('quantity')
        if qty is not None:
            try:
                item['quantity_on_hand'] = float(
                    re.sub(r'[^\d.]', '', str(qty))
                )
            except (ValueError, TypeError):
                pass

        return item
