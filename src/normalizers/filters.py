import re
from typing import Optional, Tuple

FLATMATE_KEYWORDS = [
    "room available", "room in a 3bhk", "room in a 2bhk", "room in 3bhk", 
    "room in 2bhk", "single room", "pre-occupied", "already occupied",
    "vacant room", "room for rent in", "flatmate", "flatmates", "sublet", "pg/co-living"
]

FLATMATE_REGEX_PATTERNS = [
    # Commercial multi-unit / dormitory room blocks (e.g. "total 15 units of 1rks", "34 single rooms available")
    re.compile(r"\b\d+\s+(?:units?|rooms?)\s+of\s+1rks?\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+single\s+rooms?\b", re.IGNORECASE),
    # Bed / room sharing in hostels or shared flats
    re.compile(r"\b(?:twin|triple|double|room|bed)\s+sharing\b", re.IGNORECASE),
    re.compile(r"\bsharing\s+(?:room|basis|bed)\b", re.IGNORECASE),
    re.compile(r"\bper\s+bed\b", re.IGNORECASE),
    # Standalone PG / Paying Guest / Hostel (strict word boundaries)
    re.compile(r"\b(?:boys?|girls?|student|gents?|ladies)?\s*\bpg\b", re.IGNORECASE),
    re.compile(r"\bpaying\s+guest\b", re.IGNORECASE),
    re.compile(r"\bhostel\b", re.IGNORECASE),
    # Fractional room occupancy in multi-BHK flat
    re.compile(r"\b(?:one|\d+)\s+room\s+out\s+of\b", re.IGNORECASE),
    re.compile(r"\boccupied\s+and\s+(?:one|\d+)\s+(?:vacant|available|room)\b", re.IGNORECASE)
]

def is_single_room_flatmate(title: Optional[str], desc: Optional[str]) -> bool:
    """Detects partial room / flatmate ads masquerading as full flats."""
    text = f"{title or ''} {desc or ''}".lower()
    if any(kw in text for kw in FLATMATE_KEYWORDS):
        return True
    return any(bool(pat.search(text)) for pat in FLATMATE_REGEX_PATTERNS)

def parse_housing_floor_desc(floor_desc: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """
    Parses Housing.com floor descriptions such as:
    'Floor 11 of 18', '3 of 4', 'Ground of 4', 'Lower Basement of 14', '2nd of 4 floors'
    Returns: (floor_number, total_floors)
    """
    if not floor_desc or not isinstance(floor_desc, str):
        return None, None
        
    s = floor_desc.strip().lower()
    
    # Pattern: '<floor> of <total>'
    m = re.search(r'(?:floor\s+)?([a-z0-9\-]+)\s+of\s+(\d+)', s)
    if not m:
        # Check single number if only floor or total is present
        m_single = re.search(r'(\d+)', s)
        if m_single:
            return int(m_single.group(1)), None
        return None, None
        
    raw_flr = m.group(1).strip()
    raw_tot = int(m.group(2).strip())
    
    flr: Optional[int] = None
    if "ground" in raw_flr or raw_flr == "g":
        flr = 0
    elif "lower basement" in raw_flr:
        flr = -2
    elif "basement" in raw_flr:
        flr = -1
    else:
        # Match digits (e.g., '1st' -> 1, '11' -> 11)
        flr_digits = re.search(r'(-?\d+)', raw_flr)
        if flr_digits:
            flr = int(flr_digits.group(1))
            
    return flr, raw_tot

def normalize_furnishing(val: Optional[str]) -> str:
    """Standardizes furnishing into 3-tier enum."""
    if not val:
        return "Semi-Furnished"
    v = val.strip().lower()
    if any(k in v for k in ["full", "fully"]):
        return "Fully Furnished"
    elif any(k in v for k in ["none", "unfurnished", "un-furnished", "bare"]):
        return "Unfurnished"
    return "Semi-Furnished"
