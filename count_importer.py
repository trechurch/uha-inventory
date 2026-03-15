# count_importer.py  — Inventory count file processing

import pandas as pd
import streamlit as st
from importer import detect_encoding
from typing import Dict, Optional, Tuple, List, Any
from pathlib import Path


__version__ = "3.0.0"


class CountImporter:
    def __init__(self, database=None):
        """
        database: optional InventoryDatabase instance. If provided, the importer
        will attempt to map descriptions to keys and call update_quantity_from_count().
        """
        self.db = database

    def _identify_columns(self, df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
        """Return (description_column, quantity_column) using normalized heuristics."""
        cols = [str(c).strip() for c in df.columns]
        lower = [c.lower() for c in cols]

        # Preferred exact matches
        desc_candidates = []
        qty_candidates = []
        for i, c in enumerate(lower):
            if c in ("description", "item description", "item"):
                desc_candidates.append(cols[i])
            if c in ("count", "quantity", "qty", "counted"):
                qty_candidates.append(cols[i])

        # Fallback substring matches
        if not desc_candidates:
            for i, c in enumerate(lower):
                if "desc" in c or "item" in c:
                    desc_candidates.append(cols[i])
        if not qty_candidates:
            for i, c in enumerate(lower):
                if "count" in c or "qty" in c or "quantity" in c:
                    qty_candidates.append(cols[i])

        desc_col = desc_candidates[0] if desc_candidates else None
        qty_col = qty_candidates[0] if qty_candidates else None
        return desc_col, qty_col

    def process_count_file(self, file_path: str) -> Dict[str, Any]:
        """
        Parses a count file and returns a summary of findings.

        Returns:
            {
                "success": bool,
                "data": [ {description, count, mapped_key (optional), valid: bool}, ... ],
                "invalid_rows": [ {row_index, reason, raw_row}, ... ],
                "stats": {total_rows, valid_count, invalid_count}
            }
        """
        try:
            p = Path(file_path)
            with open(file_path, "rb") as f:
                raw_bytes = f.read()
            enc = detect_encoding(raw_bytes)

            # Read file (Excel vs CSV)
            if p.suffix.lower() in (".xlsx", ".xls"):
                df = pd.read_excel(file_path, engine="openpyxl", dtype=str)
            else:
                df = pd.read_csv(file_path, encoding=enc, encoding_errors="replace", dtype=str)

            desc_col, qty_col = self._identify_columns(df)
            if not desc_col or not qty_col:
                return {"success": False, "error": "Could not identify required columns."}

            # Optionally load DB descriptions for mapping
            existing_map = {}
            if self.db:
                try:
                    existing_map = self.db.get_all_descriptions() or {}
                except Exception:
                    existing_map = {}

            processed_items: List[Dict[str, Any]] = []
            invalid_rows: List[Dict[str, Any]] = []
            for idx, row in df.iterrows():
                raw_desc = str(row.get(desc_col) or "").strip()
                raw_qty = row.get(qty_col)
                # Coerce numeric
                try:
                    qty = pd.to_numeric(raw_qty, errors="coerce")
                    if pd.isna(qty):
                        raise ValueError("non-numeric")
                    qty = float(qty)
                    valid = True
                except Exception:
                    qty = None
                    valid = False

                mapped_key = None
                if valid and self.db:
                    # Try exact uppercase match first
                    mapped_key = existing_map.get(raw_desc.upper())
                    # If not exact, try fuzzy fallback (importer fuzzy_match_description could be used)
                    # Keep it simple here; UI can surface unmapped items for review
                entry = {
                    "row_index": idx,
                    "description": raw_desc,
                    "count": qty,
                    "mapped_key": mapped_key,
                    "valid": valid,
                }
                if not valid:
                    invalid_rows.append({"row_index": idx, "reason": "invalid_count", "raw_row": row.to_dict()})
                processed_items.append(entry)

            stats = {
                "total_rows": len(df),
                "valid_count": len([r for r in processed_items if r["valid"]]),
                "invalid_count": len(invalid_rows),
            }

            return {"success": True, "data": processed_items, "invalid_rows": invalid_rows, "stats": stats}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
