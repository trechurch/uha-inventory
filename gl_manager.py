"""
GL Code Manager — Unified Version
Combines:
 - Advanced matching (token, weighted, exclusion)
 - Review queue support
 - CSV/TXT/directory loaders
 - Filename GL parsing
 - DB loading
 - Export
"""

import re
from typing import List, Dict, Tuple, Optional
from difflib import SequenceMatcher
import pandas as pd
from pathlib import Path

from importer import detect_encoding


__version__ = "3.4.0"


class GLCodeManager:
    """Unified GL Manager with advanced matching + full file ingestion."""

    def __init__(self, database):
        self.db = database

        # GL mappings: {gl_code: {"name": str, "examples": [str]}}
        self.gl_mappings: Dict[str, Dict] = {}

        # User-configurable matching settings (used by new UI)
        self.settings = {
            "use_token_matching": True,
            "use_weighted_match": True,
            "use_exclusion": True,
            "min_confidence": 0.7,
        }

        # Load existing GL mappings from DB
        self.load_gl_mappings_from_db()

    # ────────────────────────────────────────────────────────────────
    # Normalization & Matching
    # ────────────────────────────────────────────────────────────────

    def _normalize(self, text: str) -> str:
        """Normalize text for matching."""
        t = str(text).upper().strip()

        if self.settings.get("use_exclusion", True):
            # Remove common unit noise
            t = re.sub(r"\b(CASE|CS|OZ|LB|PK|PACK|EA|KG|GAL)\b", "", t)

        return t

    def token_match_score(self, str1: str, str2: str) -> float:
        """Word overlap score."""
        s1 = set(re.findall(r"\w+", self._normalize(str1)))
        s2 = set(re.findall(r"\w+", self._normalize(str2)))
        if not s1 or not s2:
            return 0.0
        return len(s1.intersection(s2)) / max(len(s1), len(s2))

    def similarity_score(self, str1: str, str2: str) -> float:
        """Fuzzy similarity score."""
        return SequenceMatcher(None, self._normalize(str1), self._normalize(str2)).ratio()

    # ────────────────────────────────────────────────────────────────
    # GL Parsing
    # ────────────────────────────────────────────────────────────────

    def parse_gl_code(self, gl_string: str) -> Tuple[str, str]:
        """Parse 'Category 411048' → ('Category', '411048')."""
        if not gl_string or pd.isna(gl_string):
            return ("", "")

        gl_string = str(gl_string).strip()
        match = re.search(r"^(.*?)\s+(\d{6})$", gl_string)
        if match:
            return (match.group(1).strip(), match.group(2))
        return (gl_string, "")

    # ────────────────────────────────────────────────────────────────
    # GL Mapping Management
    # ────────────────────────────────────────────────────────────────

    def add_gl_mapping(self, gl_code: str, gl_name: str, example_description: str):
        """Add mapping + example."""
        if gl_code not in self.gl_mappings:
            self.gl_mappings[gl_code] = {"name": gl_name, "examples": []}

        if example_description and example_description not in self.gl_mappings[gl_code]["examples"]:
            self.gl_mappings[gl_code]["examples"].append(example_description)

    def load_gl_mappings_from_db(self):
        """Load GL mappings from existing items."""
        try:
            for item in self.db.get_all_items():
                gl_code = item.get("gl_code")
                gl_name = item.get("gl_name")
                desc = item.get("description")
                if gl_code and desc:
                    self.add_gl_mapping(gl_code, gl_name or "", desc)
        except Exception:
            pass

    # ────────────────────────────────────────────────────────────────
    # File Loaders (CSV, TXT, Directory)
    # ────────────────────────────────────────────────────────────────

    def load_gl_mappings_from_file(self, filepath: str) -> bool:
        """Load GL mappings from CSV."""
        try:
            with open(filepath, "rb") as f:
                raw = f.read()
            enc = detect_encoding(raw)
            df = pd.read_csv(filepath, encoding=enc, encoding_errors="replace")

            desc_col = None
            gl_col = None

            for col in df.columns:
                c = col.lower().strip()
                if "description" in c or "item" in c:
                    desc_col = col
                if "gl code" in c or "gl_code" in c:
                    gl_col = col

            if not desc_col or not gl_col:
                return False

            for _, row in df.iterrows():
                desc = row[desc_col]
                gl_string = row[gl_col]
                if pd.isna(desc) or pd.isna(gl_string):
                    continue

                gl_name, gl_code = self.parse_gl_code(gl_string)
                if gl_code:
                    self.add_gl_mapping(gl_code, gl_name, str(desc))

            return True
        except Exception as e:
            print(f"Error loading GL mappings from {filepath}: {e}")
            return False

    def load_gl_mappings_from_directory(self, directory: str) -> int:
        """Load all CSV files in a directory."""
        p = Path(directory)
        if not p.exists():
            return 0

        count = 0
        for file in p.glob("*.csv"):
            if self.load_gl_mappings_from_file(str(file)):
                count += 1
        return count

    def load_gl_from_filename(self, filepath: str) -> Tuple[str, str]:
        """Parse 'Beer_411034.txt' → ('Beer', '411034')."""
        stem = Path(filepath).stem
        match = re.search(r"^(.*?)_?(\d{6})$", stem)
        if match:
            return (match.group(1).replace("_", " ").strip(), match.group(2))
        return ("", "")

    def load_gl_txt_files_from_directory(self, directory: str) -> int:
        """Load TXT files named 'Category_411034.txt'."""
        p = Path(directory)
        if not p.exists():
            return 0

        count = 0
        for file in p.glob("*.txt"):
            category, gl_code = self.load_gl_from_filename(str(file))
            if not gl_code:
                continue

            try:
                with open(file, "rb") as f:
                    raw = f.read()
                enc = detect_encoding(raw)

                with open(file, "r", encoding=enc, errors="replace") as f:
                    for line in f:
                        desc = line.strip()
                        if desc:
                            self.add_gl_mapping(gl_code, category, desc)
                count += 1
            except Exception as e:
                print(f"Error reading {file}: {e}")

        return count

    # ────────────────────────────────────────────────────────────────
    # Matching Engine (Advanced)
    # ────────────────────────────────────────────────────────────────

    def find_best_gl_match(self, description: str) -> Optional[Dict]:
        """Return best match, even below threshold (for review queue)."""
        if not description or not self.gl_mappings:
            return None

        best = None
        best_score = 0.0

        for gl_code, info in self.gl_mappings.items():
            for example in info["examples"]:
                fuzzy = self.similarity_score(description, example)
                token = self.token_match_score(description, example) if self.settings["use_token_matching"] else 0.0

                if self.settings["use_weighted_match"]:
                    score = (fuzzy * 0.4) + (token * 0.6)
                else:
                    score = max(fuzzy, token)

                if score > best_score:
                    best_score = score
                    best = {
                        "gl_code": gl_code,
                        "gl_name": info["name"],
                        "confidence": score,
                        "matched_example": example,
                    }

        # Return match even if below min_confidence (review queue)
        if best and best["confidence"] >= 0.4:
            return best
        return None

    # ────────────────────────────────────────────────────────────────
    # Auto‑Assignment
    # ────────────────────────────────────────────────────────────────

    def assign_gl_codes_to_items(self) -> Dict:
        """Auto-assign GL codes using current settings."""
        results = {"assigned": 0, "skipped": 0, "failed": 0, "assignments": []}

        for item in self.db.get_all_items():
            if item.get("gl_code"):
                results["skipped"] += 1
                continue

            desc = item.get("description")
            if not desc:
                results["failed"] += 1
                continue

            match = self.find_best_gl_match(desc)

            if match and match["confidence"] >= self.settings["min_confidence"]:
                self.db.update_item(
                    item["key"],
                    {"gl_code": match["gl_code"], "gl_name": match["gl_name"]},
                    changed_by="auto_gl_assignment",
                    change_reason=f"Auto-assigned (confidence: {match['confidence']:.2%})",
                )
                results["assigned"] += 1
                results["assignments"].append(match)
            else:
                results["failed"] += 1

        return results

    # ────────────────────────────────────────────────────────────────
    # Summary & Export
    # ────────────────────────────────────────────────────────────────

    def get_gl_summary(self) -> List[Dict]:
        return [
            {
                "gl_code": gl_code,
                "gl_name": info["name"],
                "example_count": len(info["examples"]),
            }
            for gl_code, info in sorted(self.gl_mappings.items())
        ]

    def export_gl_mappings(self, filepath: str):
        rows = []
        for gl_code, info in self.gl_mappings.items():
            for example in info["examples"]:
                rows.append(
                    {
                        "GL Code": gl_code,
                        "GL Name": info["name"],
                        "Example Description": example,
                    }
                )
        pd.DataFrame(rows).to_csv(filepath, index=False)
        return True
