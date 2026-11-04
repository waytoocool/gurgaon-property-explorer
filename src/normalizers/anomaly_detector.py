import os
import json
import re
from typing import Dict, Any, Optional, Tuple
from shapely.geometry import Point, shape

from .filters import is_single_room_flatmate

EXTERNAL_LOCALITIES = [
    "chattarpur", "chhatarpur", "alwar", "alvar", "kapashera",
    "aya nagar", "aayanagar", "sohna", "sohna rural", "raghopur",
    "bhiwadi", "faridabad", "noida", "gls arawali", "paras quartier"
]

def are_neighbor_subsectors(sec1: str, sec2: str) -> bool:
    """Checks if two sector numbers are adjacent sub-sectors (e.g. 9 and 9A, 12 and 12A, 22 and 22B)."""
    s1 = re.sub(r"[^0-9a-z]", "", sec1.lower())
    s2 = re.sub(r"[^0-9a-z]", "", sec2.lower())
    m1 = re.match(r"^(\d+)([a-z]?)$", s1)
    m2 = re.match(r"^(\d+)([a-z]?)$", s2)
    if m1 and m2:
        return m1.group(1) == m2.group(1)
    return False

class Tier1AnomalyDetector:
    """
    Automated Tier-1 Heuristic Classifier for Gurgaon Rental Listings.
    Classifies raw listings and consolidated physical units into canonical anomaly tags:
      - DATA_ENTRY_ERROR   : Shorthand/placeholder rents (<= ₹1,000), rates < ₹5/sq.ft, or extreme area typos.
      - FLATMATE_AD        : Single room, bed-sharing, or commercial dormitory ads.
      - MIS_PINNED_LOCATION: Text vs GPS contradictions (external cities or sectors > 2.5km from actual polygon).
      - LOW_RISE_WALKUP    : Independent builder floors, standalone houses, or buildings <= 5 floors (DTCP cap).
      - None               : Clean, standard property conforming to expected parameters.
    """

    def __init__(self, geojson_path: Optional[str] = None):
        if not geojson_path:
            # Default to config/gurgaon_sectors.geojson relative to repo root
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            geojson_path = os.path.join(base_dir, "config", "gurgaon_sectors.geojson")

        self.polygons = {}
        if os.path.exists(geojson_path):
            try:
                with open(geojson_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for feat in data.get("features", []):
                    name = feat["properties"].get("sector_name") or feat["properties"].get("name")
                    if name:
                        clean_name = name.strip().lower()
                        poly_geom = shape(feat["geometry"])
                        self.polygons[clean_name] = poly_geom
                        m = re.search(r"(\d+[a-z]?)", clean_name)
                        if m:
                            num = m.group(1)
                            self.polygons[f"sector {num}"] = poly_geom
                            self.polygons[num] = poly_geom
            except Exception:
                pass

    def detect(self, item: Dict[str, Any]) -> Tuple[Optional[str], str]:
        """
        Classifies an individual listing or consolidated physical unit.
        
        Args:
            item: Dictionary containing listing attributes.
            
        Returns:
            Tuple of (anomaly_tag, reason_string)
        """
        rent = item.get("min_rent") or item.get("rent") or 0
        area = item.get("built_up_sqft") or 0
        rate = item.get("price_per_sqft") or (rent / area if area and rent else 0)
        bhk = item.get("bhk")
        total_floors = item.get("total_floors")
        prop_type = item.get("property_type") or ""
        society = str(item.get("society") or "")
        locality = str(item.get("locality") or "")
        title = str(item.get("title") or "")
        desc = str(item.get("description") or "")
        actual_sec = str(item.get("actual_sector") or "")
        lat = item.get("latitude")
        lon = item.get("longitude")

        # ---------------------------------------------------------
        # 1. DATA ENTRY ERROR (Highest Precedence)
        # ---------------------------------------------------------
        if rent <= 0:
            return "DATA_ENTRY_ERROR", f"Zero or negative rent (rent={rent})"
        if rent <= 1000:
            return "DATA_ENTRY_ERROR", f"Shorthand or placeholder rent (<= ₹1000, rent={rent})"
        if area > 0 and rate < 5.0:
            return "DATA_ENTRY_ERROR", f"Implausibly low rate (< ₹5/sqft, rate=₹{rate:.1f}/sqft)"
        # Unrealistic single unit area in multi-story high-rise
        if prop_type != "Independent Builder Floor / Standalone" and (total_floors or 0) >= 6:
            if bhk == 1 and area > 1000:
                return "DATA_ENTRY_ERROR", f"1 BHK multi-story unit area overstatement ({area} sqft)"
        if "1 rk" in title.lower() and area > 1500:
            return "DATA_ENTRY_ERROR", f"1 RK with {area} sqft plot size entered"

        # ---------------------------------------------------------
        # 2. FLATMATE / ROOM AD (Second Precedence)
        # ---------------------------------------------------------
        if is_single_room_flatmate(title, desc):
            return "FLATMATE_AD", "Single room or shared occupancy ad detected"

        # ---------------------------------------------------------
        # 3. MIS-PINNED LOCATION (Third Precedence)
        # ---------------------------------------------------------
        text_corpus = f"{society} {locality} {title}".lower()

        # 3a. External Municipality Check
        for ext in EXTERNAL_LOCALITIES:
            if re.search(r'\b' + re.escape(ext) + r'\b', text_corpus):
                if "manesar" in ext and "manesar" in actual_sec.lower():
                    continue
                return "MIS_PINNED_LOCATION", f"External locality '{ext}' found in text but pinned in '{actual_sec}'"

        # 3b. Manesar text pinned in non-Manesar sector
        if "manesar" in text_corpus and "manesar" not in actual_sec.lower():
            return "MIS_PINNED_LOCATION", f"Manesar mentioned in text but pinned in '{actual_sec}'"

        # 3c. Explicit Sector Contradiction
        text_sectors = re.findall(r'(?:sector|sec)[\s-]*(\d+[a-z]?)', text_corpus)
        if text_sectors:
            m_act = re.search(r'(?:sector|sec)[\s-]*(\d+[a-z]?)', actual_sec.lower())
            act_num = m_act.group(1) if m_act else None
            
            has_match_or_neighbor = False
            for ts in text_sectors:
                if act_num and (ts == act_num or are_neighbor_subsectors(ts, act_num)):
                    has_match_or_neighbor = True
                    break

            if act_num and not has_match_or_neighbor:
                first_ts = text_sectors[0]
                target_poly = self.polygons.get(f"sector {first_ts}") or self.polygons.get(first_ts)
                if target_poly and lat and lon:
                    pt = Point(lon, lat)
                    dist_km = target_poly.distance(pt) * 111.0
                    if dist_km > 2.5:
                        return "MIS_PINNED_LOCATION", f"Text mentions Sector {first_ts} but pinned {dist_km:.1f}km away in {actual_sec}"
                elif not target_poly:
                    return "MIS_PINNED_LOCATION", f"Text mentions Sector {first_ts} but resolved to {actual_sec}"

        # 3d. Coordinates outside municipal hub
        if lat and lon and actual_sec.startswith("Outside Hub"):
            return "MIS_PINNED_LOCATION", f"Coordinates {lat:.4f}, {lon:.4f} are outside municipal hub boundary ({actual_sec})"

        # ---------------------------------------------------------
        # 4. LOW-RISE WALKUP (Fourth Precedence)
        # ---------------------------------------------------------
        if prop_type == "Independent Builder Floor / Standalone":
            return "LOW_RISE_WALKUP", "Independent Builder Floor / Standalone property"
        if total_floors is not None and total_floors <= 5:
            return "LOW_RISE_WALKUP", f"Low-rise walkup building (total_floors={total_floors} <= 5)"
        if any(kw in title.lower() for kw in ["builder floor", "independent floor", "kothi", "duplex", "house for rent"]):
            return "LOW_RISE_WALKUP", "Title indicates independent builder floor / house"

        # ---------------------------------------------------------
        # 5. Clean / Standard Property
        # ---------------------------------------------------------
        return None, "Standard property"
