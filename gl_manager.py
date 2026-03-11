"""
GL Code Manager - Automatic GL code assignment based on description matching
"""
import re
from typing import List, Dict, Tuple, Optional
from difflib import SequenceMatcher
import pandas as pd
from pathlib import Path


class GLCodeManager:
    """Manages GL code assignments and auto-matching"""
    
    def __init__(self, database):
        """Initialize with database connection"""
        self.db = database
        self.gl_mappings = {}  # {gl_code: {name, examples: []}}
        self.load_gl_mappings_from_db()
    
    def parse_gl_code(self, gl_string: str) -> Tuple[str, str]:
        """
        Parse GL code string into name and code
        
        Example: "Dairy/Milk 411048" -> ("Dairy/Milk", "411048")
        """
        if not gl_string or pd.isna(gl_string):
            return ("", "")
        
        gl_string = str(gl_string).strip()
        
        # Match pattern: text followed by 6-digit code
        match = re.search(r'^(.*?)\s+(\d{6})$', gl_string)
        if match:
            return (match.group(1).strip(), match.group(2))
        
        # If no match, return as-is
        return (gl_string, "")
    
    def add_gl_mapping(self, gl_code: str, gl_name: str, example_description: str):
        """Add a GL code mapping with an example item description"""
        if gl_code not in self.gl_mappings:
            self.gl_mappings[gl_code] = {
                'name': gl_name,
                'examples': []
            }
        
        # Add example if not already present
        if example_description and example_description not in self.gl_mappings[gl_code]['examples']:
            self.gl_mappings[gl_code]['examples'].append(example_description)
    
    def load_gl_mappings_from_file(self, filepath: str):
        """
        Load GL mappings from CSV file
        
        Expected format: Item Description, GL Code columns
        """
        try:
            df = pd.read_csv(filepath, encoding='utf-8-sig')
            
            # Find description and GL code columns
            desc_col = None
            gl_col = None
            
            for col in df.columns:
                col_lower = col.lower().strip()
                if 'description' in col_lower or 'item' in col_lower:
                    desc_col = col
                if 'gl code' in col_lower or 'gl_code' in col_lower:
                    gl_col = col
            
            if not desc_col or not gl_col:
                return False
            
            # Process each row
            for _, row in df.iterrows():
                description = row[desc_col]
                gl_string = row[gl_col]
                
                if pd.isna(description) or pd.isna(gl_string):
                    continue
                
                gl_name, gl_code = self.parse_gl_code(gl_string)
                if gl_code:
                    self.add_gl_mapping(gl_code, gl_name, str(description))
            
            return True
        except Exception as e:
            print(f"Error loading GL mappings from {filepath}: {e}")
            return False
    
    def load_gl_mappings_from_directory(self, directory: str):
        """Load all GL mapping files from a directory"""
        dir_path = Path(directory)
        if not dir_path.exists():
            return 0
        
        count = 0
        for file_path in dir_path.glob("*.csv"):
            if self.load_gl_mappings_from_file(str(file_path)):
                count += 1
        
        return count
    
    def load_gl_mappings_from_db(self):
        """Load GL mappings from items already in database"""
        try:
            items = self.db.get_all_items()
            for item in items:
                gl_code = item.get('gl_code')
                gl_name = item.get('gl_name')
                description = item.get('description')
                
                if gl_code and description:
                    self.add_gl_mapping(gl_code, gl_name or "", description)
        except:
            pass
    
    def similarity_score(self, str1: str, str2: str) -> float:
        """Calculate similarity score between two strings (0-1)"""
        if not str1 or not str2:
            return 0.0
        
        # Normalize strings
        s1 = str(str1).upper().strip()
        s2 = str(str2).upper().strip()
        
        # Use SequenceMatcher for fuzzy matching
        return SequenceMatcher(None, s1, s2).ratio()
    
    def find_best_gl_match(self, description: str, min_confidence: float = 0.6) -> Optional[Dict]:
        """
        Find best GL code match for a description
        
        Returns: {
            'gl_code': str,
            'gl_name': str,
            'confidence': float,
            'matched_example': str
        } or None
        """
        if not description or not self.gl_mappings:
            return None
        
        best_match = None
        best_score = 0.0
        matched_example = ""
        
        # Check against all GL code examples
        for gl_code, gl_info in self.gl_mappings.items():
            for example in gl_info['examples']:
                score = self.similarity_score(description, example)
                
                if score > best_score:
                    best_score = score
                    best_match = {
                        'gl_code': gl_code,
                        'gl_name': gl_info['name'],
                        'confidence': score,
                        'matched_example': example
                    }
                    matched_example = example
        
        # Return match if confidence is high enough
        if best_match and best_match['confidence'] >= min_confidence:
            return best_match
        
        return None
    
    def assign_gl_codes_to_items(self, min_confidence: float = 0.7) -> Dict:
        """
        Auto-assign GL codes to all items in database that don't have one
        
        Returns summary of assignments
        """
        results = {
            'assigned': 0,
            'skipped': 0,
            'failed': 0,
            'assignments': []
        }
        
        items = self.db.get_all_items()
        
        for item in items:
            # Skip if already has GL code
            if item.get('gl_code'):
                results['skipped'] += 1
                continue
            
            description = item.get('description')
            if not description:
                results['failed'] += 1
                continue
            
            # Find best match
            match = self.find_best_gl_match(description, min_confidence)
            
            if match:
                # Update item with GL code
                self.db.update_item(
                    item['key'],
                    {
                        'gl_code': match['gl_code'],
                        'gl_name': match['gl_name']
                    },
                    changed_by='auto_gl_assignment',
                    change_reason=f"Auto-assigned (confidence: {match['confidence']:.2%})"
                )
                
                results['assigned'] += 1
                results['assignments'].append({
                    'key': item['key'],
                    'description': description,
                    'gl_code': match['gl_code'],
                    'gl_name': match['gl_name'],
                    'confidence': match['confidence'],
                    'matched_to': match['matched_example']
                })
            else:
                results['failed'] += 1
        
        return results
    
    def get_gl_summary(self) -> List[Dict]:
        """Get summary of all GL codes and example counts"""
        summary = []
        for gl_code, info in sorted(self.gl_mappings.items()):
            summary.append({
                'gl_code': gl_code,
                'gl_name': info['name'],
                'example_count': len(info['examples'])
            })
        return summary
    
    def export_gl_mappings(self, filepath: str):
        """Export GL mappings to CSV for review"""
        rows = []
        for gl_code, info in self.gl_mappings.items():
            for example in info['examples']:
                rows.append({
                    'GL Code': gl_code,
                    'GL Name': info['name'],
                    'Example Description': example
                })
        
        df = pd.DataFrame(rows)
        df.to_csv(filepath, index=False)
        return True


    def load_gl_from_filename(self, filepath: str) -> Tuple[str, str]:
        """
        Parse GL code and category from a filename.
        'Beer_411034.xlsx' -> ('Beer', '411034')
        'Grocery___Store_Room_411039.txt' -> ('Grocery Store Room', '411039')
        """
        from pathlib import Path
        stem = Path(filepath).stem  # filename without extension
        # Last 6 digits = GL code
        match = re.search(r'^(.*?)_?(\d{6})$', stem)
        if match:
            category = match.group(1).replace('_', ' ').strip()
            gl_code = match.group(2)
            return (category, gl_code)
        return ('', '')

    def load_gl_txt_files_from_directory(self, directory: str) -> int:
        """Load GL items from .txt files named 'Category_GLCODE.txt'."""
        from pathlib import Path
        dir_path = Path(directory)
        if not dir_path.exists():
            return 0
        count = 0
        for file_path in dir_path.glob("*.txt"):
            category, gl_code = self.load_gl_from_filename(str(file_path))
            if not gl_code:
                continue
            try:
                with open(file_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                    for line in f:
                        description = line.strip()
                        if description:
                            self.add_gl_mapping(gl_code, category, description)
                count += 1
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
        return count
