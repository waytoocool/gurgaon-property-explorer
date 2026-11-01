import re
from typing import Optional, List, Tuple
from rapidfuzz import fuzz

GENERIC_BUILDER_TERMS = [
    "standalone", "stand alone", "standlone", "independent house", 
    "builder floor", "independent builder floor"
]

DEVELOPER_BRANDS = {
    "signature global", "signature", "dlf", "m3m", "godrej", "unitech",
    "ireo", "vatika", "puri", "tata", "sobha", "bestech", "emaar",
    "shree vardhman", "pyramid", "ansal", "tulip", "paras", "central park", "gls"
}

GENERIC_LOCATION_TOKENS = {
    "sector", "sec", "gurgaon", "gurugram", "phase", "near", "road", 
    "block", "floor", "flats", "flat", "apartment", "apartments", 
    "society", "tower", "towers", "rwa", "huda", "plot", "plots"
}

def clean_tokens(s: str) -> str:
    """Removes punctuation and normalizes spaces for fuzzy matching."""
    return re.sub(r"[^\w\s]", " ", s or "").lower().strip()

def extract_numbers(s: str) -> set:
    """Extracts numeric and alphanumeric identifiers (e.g. '41', '84', '67a')."""
    return set(re.findall(r"\b\d+[a-z]?\b", (s or "").lower()))

def strip_location_suffix(s: str) -> str:
    """Strips trailing sector/locality and city suffixes for clean society comparison."""
    s = re.sub(r"[\s,]+(sector|sec)[\s-]*\d+[a-z]?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[\s,]+(gurgaon|gurugram|haryana|india|\d{6})", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^\w\s]", " ", s).strip()
    return " ".join(s.split())

def canonical_society_name(s: Optional[str]) -> str:
    """Normalizes society strings and detects generic builder floor entries."""
    if not s:
        return "Independent Builder Floor / House"
    s_clean = s.strip(" .,-\t\n")
    s_l = s_clean.lower()
    
    # Generic builder terms check (substring match for standalone / builder floor)
    if any(k in s_l for k in GENERIC_BUILDER_TERMS):
        return "Independent Builder Floor / House"
    if s_l in ["apartment", "independent", "residential", "dlf phase 4 builder floor"]:
        return "Independent Builder Floor / House"
    
    # Pure sector / locality entries (where broker entered sector instead of society name)
    if re.fullmatch(r"(sector|sec)[\s-]*\d+[a-z]?(\s+gurgaon|\s+gurugram)?", s_l):
        return "Independent Builder Floor / House"
        
    return s_clean

def classify_property_as_reported(society_clean: str) -> Tuple[str, int]:
    """
    Faithfully classifies property based on broker reporting:
    Returns (property_type, is_gated_society)
    """
    if society_clean == "Independent Builder Floor / House":
        return "Independent Builder Floor / Standalone", 0
    return "Society / Project (As Reported)", 1

def fuzzy_match_society(society_name: str, known_societies: List[str], threshold: int = 85) -> Optional[str]:
    """
    High-precision fuzzy society matching:
    Matches a society name against known canonical societies while enforcing:
    1. Number invariant (conflicting sector/phase numbers never merge).
    2. Developer brand guard (multi-word projects cannot collapse into 1-word builder names like DLF or Signature).
    3. Distinctive non-generic token requirement.
    4. Location suffix stripping (e.g. 'Unitech The Palms, Sector 41' -> 'Unitech The Palms').
    """
    if not society_name or society_name == "Independent Builder Floor / House":
        return None
        
    s_clean = strip_location_suffix(society_name).lower()
    s_tokens = s_clean.split()
    s_nums = extract_numbers(society_name)
    
    best_match = None
    best_score = 0
    
    for known in known_societies:
        if known == "Independent Builder Floor / House":
            continue
            
        k_clean = strip_location_suffix(known).lower()
        k_tokens = k_clean.split()
        k_nums = extract_numbers(known)
        
        # 1. Number invariant: numbers must not conflict (e.g. Sector 41 vs Sector 84, Phase 2 vs Phase 4)
        if s_nums and k_nums and not (s_nums & k_nums):
            continue
            
        # 2. Developer brand guard: cannot merge multi-word project into 1-word developer brand
        if len(k_tokens) == 1 and k_clean in DEVELOPER_BRANDS and len(s_tokens) > 1:
            continue
        if len(s_tokens) == 1 and s_clean in DEVELOPER_BRANDS and len(k_tokens) > 1:
            continue
            
        # 3. Non-generic common token requirement
        common = set(s_tokens) & set(k_tokens)
        distinct = common - GENERIC_LOCATION_TOKENS
        if not distinct:
            continue
            
        # 4. Compute similarity with length ratio guard
        len_ratio = min(len(s_clean), len(k_clean)) / max(len(s_clean), len(k_clean))
        sort_score = fuzz.token_sort_ratio(s_clean, k_clean)
        set_score = fuzz.token_set_ratio(s_clean, k_clean)
        
        score = 0
        if sort_score >= threshold:
            score = sort_score
        elif set_score >= 90 and len_ratio >= 0.60:
            score = set_score
            
        if score >= threshold and score > best_score:
            best_score = score
            best_match = known
            
    return best_match
