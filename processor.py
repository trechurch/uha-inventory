# processor.py — The Inventory Machinery

from typing import List, Dict, Any
from importer import normalize_pack_type  # reuse canonical pack normalization


def generate_canonical_key(name: str, packtype: str) -> str:
    """Standardized ITEM NAME||PACKTYPE key using importer pack normalization."""
    nm = str(name or "").upper().strip()
    pack = normalize_pack_type(packtype)
    return f"{nm}||{pack}"


def calculate_portion_cost(recipe_cost: float, yield_pct: float, conv_ratio: float) -> float:
    """PCA Math Engine: (Cost / Yield) * Conversion Ratio."""
    try:
        recipe_cost = float(recipe_cost or 0.0)
        yield_pct = float(yield_pct or 0.0)
        conv_ratio = float(conv_ratio or 1.0)
    except (ValueError, TypeError):
        return 0.0
    return (recipe_cost / yield_pct) * conv_ratio if yield_pct > 0 else 0.0


def reconcile_two_row_items(raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Aggregates split inventory items from multi-row legacy PDFs.
    Produces a new list (does not mutate inputs) and coerces numeric qty.
    """
    reconciled: Dict[str, Dict[str, Any]] = {}
    for item in raw_data:
        key = str(item.get("description") or "").strip()
        qty = item.get("qty", 0)
        try:
            qty = float(qty or 0)
        except (ValueError, TypeError):
            qty = 0.0
        if key in reconciled:
            reconciled[key]["qty"] = float(reconciled[key].get("qty", 0)) + qty
        else:
            copy_item = dict(item)  # shallow copy to avoid mutating caller
            copy_item["qty"] = qty
            reconciled[key] = copy_item
    return list(reconciled.values())
