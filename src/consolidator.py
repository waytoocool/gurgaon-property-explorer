import os
import json
import math
import re
import yaml
from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict
from shapely.geometry import shape, Point

from .db import get_connection, upsert_consolidated
from .normalizers.filters import is_single_room_flatmate, parse_housing_floor_desc
from .normalizers.society import canonical_society_name, classify_property_as_reported, fuzzy_match_society
from .normalizers.anomaly_detector import Tier1AnomalyDetector

def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance between two GPS points in meters."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

def is_facing_conflicting(facing1: Optional[str], facing2: Optional[str]) -> bool:
    """Non-conflicting facing check. Returns True only if both report explicit opposite/different directions."""
    if not facing1 or not facing2:
        return False
    f1 = facing1.strip().lower()
    f2 = facing2.strip().lower()
    if f1 in ["don't know", "unknown", "none", ""] or f2 in ["don't know", "unknown", "none", ""]:
        return False
    return f1 != f2

class Consolidator:
    """
    Consolidation and Deduplication Engine.
    Executes the strict 5-Point Physical Unit Fingerprint:
    1. Society / Coordinates (<= 50m)
    2. Exact Floor Match (floor == floor)
    3. BHK Match
    4. Built-up Area (+- 5%)
    5. Bathroom Count Match
    + Non-conflicting gate facing check.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.sector_polygons = self._load_sector_polygons()
        self.anomaly_detector = Tier1AnomalyDetector()

    def _load_sector_polygons(self) -> Dict[str, Any]:
        """Loads official HUDA sector multi-vertex polygons from config/gurgaon_sectors.geojson."""
        geo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "gurgaon_sectors.geojson")
        if os.path.exists(geo_path):
            try:
                with open(geo_path, "r", encoding="utf-8") as f:
                    gj = json.load(f)
                polys = {}
                for feat in gj.get("features", []):
                    s_name = feat["properties"]["sector_name"]
                    polys[s_name] = shape(feat["geometry"])
                return polys
            except Exception as e:
                print(f"[Consolidator] Warning: could not load sector polygons from {geo_path}: {e}")
        return {}

    def resolve_actual_sector(self, lat: Optional[float], lon: Optional[float], text_context: str = "") -> str:
        """
        Geographically maps GPS coordinates to official HUDA Sector numbers using shapely Point-in-Polygon.
        Eliminates bounding box overlaps (0.0% overlap). Resolves edge properties on road medians
        to the nearest official sector within 1.5 km.
        Falls back to explicit HUDA sector parsed from text_context (title/locality) if coordinates are missing.
        """
        if lat and lon and self.sector_polygons:
            pt = Point(lon, lat)

            # 1. Exact Point-in-Polygon containment
            exact_matches = [name for name, poly in self.sector_polygons.items() if poly.contains(pt)]
            if exact_matches:
                if len(exact_matches) == 1:
                    return exact_matches[0]
                # If on shared boundary edge, break tie with closest centroid
                return min(exact_matches, key=lambda n: self.sector_polygons[n].centroid.distance(pt))

            # 2. Road Median / Adjacent Buffer resolution: find closest polygon
            closest_dist_deg, closest_s = min((poly.distance(pt), name) for name, poly in self.sector_polygons.items())
            dist_m = closest_dist_deg * 111000.0

            if dist_m <= 1500.0:
                return closest_s

        # 3. Fallback: Parse explicit sector from text_context if coordinates are missing or unresolved
        if text_context and self.sector_polygons:
            m = re.search(r"\b(?:Sector|Sec)[\s-]*(\d+[A-Za-z]?)\b", text_context, re.IGNORECASE)
            if m:
                cand = f"Sector {m.group(1).upper()}"
                if cand in self.sector_polygons:
                    return cand

        return "Unknown"

    def _normalize_nobroker_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        raw_json_str = row["raw_json"] or "{}"
        photos_count = row.get("photos_count")
        desc = ""
        try:
            raw = json.loads(raw_json_str)
            if photos_count is None:
                photos = raw.get("photos") or []
                photos_count = len(photos)
            desc = raw.get("ownerDescription") or raw.get("description") or ""
            if isinstance(desc, dict):
                desc = desc.get("overviewDescription", "")
        except Exception:
            if photos_count is None:
                photos_count = 0
            desc = ""

        title = row["title"] or ""
        flatmate = 1 if is_single_room_flatmate(title, desc) else 0
        soc_clean = canonical_society_name(row["society"])
        ptype, is_gated = classify_property_as_reported(soc_clean)

        bhk = row["bhk"]
        if bhk is None and title:
            m = re.search(r"(\d+)\+?\s*BHK", title, re.IGNORECASE)
            if m:
                try:
                    bhk = int(m.group(1))
                except Exception:
                    pass

        return {
            "portal": "NoBroker",
            "portal_id": row["portal_id"],
            "title": title,
            "society": soc_clean,
            "raw_society": row["society"],
            "locality": row["locality"],
            "bhk": bhk,
            "rent": row["rent"],
            "maintenance_amount": row["maintenance_amount"] or 0,
            "deposit": row["deposit"] or 0,
            "built_up_sqft": row["built_up_sqft"] or 0,
            "carpet_sqft": None,
            "furnishing": row["furnishing"],
            "bathrooms": row["bathrooms"],
            "balconies": row["balconies"],
            "floor": row["floor"],
            "total_floors": row["total_floors"],
            "facing": row["facing"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "owner_name": row["owner_name"],
            "has_direct_owner": bool(row["owner_name"]),
            "listing_url": row["listing_url"],
            "posted_date": row["posted_date"] if "posted_date" in row.keys() else None,
            "last_updated_date": row["last_updated_date"] if "last_updated_date" in row.keys() else None,
            "property_type": ptype,
            "is_gated_society": is_gated,
            "is_flatmate_listing": flatmate,
            "photos_count": photos_count,
            "description": desc
        }

    def _normalize_housing_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        raw_json_str = row["raw_json"] or "{}"
        photos_count = row.get("photos_count")
        desc = ""
        try:
            raw = json.loads(raw_json_str)
            if photos_count is None:
                photos_count = raw.get("imageCount", 0) or 0
            desc = raw.get("description") or ""
            if isinstance(desc, dict):
                desc = desc.get("overviewDescription", "")
        except Exception:
            if photos_count is None:
                photos_count = 0
            desc = ""

        title = row["title"] or ""
        flatmate = 1 if is_single_room_flatmate(title, desc) else 0
        soc_clean = canonical_society_name(row["society"])
        ptype, is_gated = classify_property_as_reported(soc_clean)

        flr, tot_flrs = parse_housing_floor_desc(row["floor_desc"])

        bhk = row["bhk"]
        if title:
            m = re.search(r"(\d+(?:\.\d+)?)\s*BHK", title, re.IGNORECASE)
            if m:
                try:
                    bhk = int(float(m.group(1)))
                except Exception:
                    pass
            elif "1 RK" in title:
                bhk = 1

        return {
            "portal": "Housing",
            "portal_id": row["portal_id"],
            "title": title,
            "society": soc_clean,
            "raw_society": row["society"],
            "locality": row["locality"],
            "bhk": bhk,
            "rent": row["rent"],
            "maintenance_amount": row["maintenance_amount"] or 0,
            "deposit": row["deposit"] or 0,
            "built_up_sqft": row["built_up_sqft"] or 0,
            "carpet_sqft": row["carpet_sqft"],
            "furnishing": row["furnishing"],
            "bathrooms": row["bathrooms"],
            "balconies": row["balconies"],
            "floor": flr,
            "total_floors": tot_flrs,
            "facing": row["facing"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "owner_name": row["seller_name"] if not row["is_broker"] else None,
            "has_direct_owner": not row["is_broker"],
            "listing_url": row["listing_url"],
            "posted_date": row["posted_date"] if "posted_date" in row.keys() else None,
            "last_updated_date": row["last_updated_date"] if "last_updated_date" in row.keys() else None,
            "property_type": ptype,
            "is_gated_society": is_gated,
            "is_flatmate_listing": flatmate,
            "photos_count": photos_count,
            "description": desc
        }

    def _normalize_ninety_nine_acres_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        raw_json_str = row["raw_json"] or "{}"
        photos_count = row.get("photos_count")
        desc = ""
        try:
            raw = json.loads(raw_json_str)
            if photos_count is None:
                images = raw.get("PROPERTY_IMAGES") or raw.get("THUMBNAIL_IMAGES") or []
                photos_count = len(images)
            desc = raw.get("DESCRIPTION") or ""
        except Exception:
            if photos_count is None:
                photos_count = 0
            desc = ""

        title = row["title"] or ""
        flatmate = 1 if is_single_room_flatmate(title, desc) else 0
        soc_clean = canonical_society_name(row["society"])
        ptype, is_gated = classify_property_as_reported(soc_clean)

        bhk = row["bhk"]
        if bhk is None and title:
            m = re.search(r"(\d+(?:\.\d+)?)\s*BHK", title, re.IGNORECASE)
            if m:
                try:
                    bhk = int(float(m.group(1)))
                except Exception:
                    pass
            elif "1 RK" in title:
                bhk = 1

        return {
            "portal": "99acres",
            "portal_id": row["portal_id"],
            "title": title,
            "society": soc_clean,
            "raw_society": row["society"],
            "locality": row["locality"],
            "bhk": bhk,
            "rent": row["rent"],
            "maintenance_amount": row["maintenance_amount"] or 0,
            "deposit": row["deposit"] or 0,
            "built_up_sqft": row["built_up_sqft"] or 0,
            "carpet_sqft": row["carpet_sqft"],
            "furnishing": row["furnishing"],
            "bathrooms": row["bathrooms"],
            "balconies": row["balconies"],
            "floor": row["floor"],
            "total_floors": row["total_floors"],
            "facing": row["facing"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "owner_name": row["seller_name"],
            "has_direct_owner": False,
            "listing_url": row["listing_url"],
            "posted_date": row["posted_date"] if "posted_date" in row.keys() else None,
            "last_updated_date": row["last_updated_date"] if "last_updated_date" in row.keys() else None,
            "property_type": ptype,
            "is_gated_society": is_gated,
            "is_flatmate_listing": flatmate,
            "photos_count": photos_count,
            "description": desc
        }

    def _matches_5_point_fingerprint(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Evaluates whether two listings represent the exact same physical apartment."""
        # 0. Distance Calculation & Hard Distance Caps
        dist = None
        if a["latitude"] and a["longitude"] and b["latitude"] and b["longitude"]:
            dist = haversine_distance_meters(a["latitude"], a["longitude"], b["latitude"], b["longitude"])

        # Hard Cap 1: Properties > 500m apart are NEVER the same physical apartment
        if dist is not None and dist > 500.0:
            return False

        # Hard Cap 2: Independent builder floors > 50m apart are NEVER the same plot
        if (a["society"] == "Independent Builder Floor / House" or b["society"] == "Independent Builder Floor / House"):
            if dist is not None and dist > 50.0:
                return False

        # 1. Society Name OR Spatial Proximity Match
        soc_match = False
        if a["society"] and b["society"] and a["society"] != "Independent Builder Floor / House" and b["society"] != "Independent Builder Floor / House":
            if a["society"].lower() == b["society"].lower() or fuzzy_match_society(a["society"], [b["society"]], threshold=85):
                soc_match = True

        coord_match = (dist is not None and dist <= 50.0)

        if not (soc_match or coord_match):
            return False

        # 2. BHK match (both must specify and be identical)
        if a["bhk"] is None or b["bhk"] is None:
            return False
        if a["bhk"] != b["bhk"]:
            return False

        # 3. Exact Floor Match (Both must specify floor and they must be identical)
        if a["floor"] is None or b["floor"] is None:
            return False
        if a["floor"] != b["floor"]:
            return False

        # 4. Built-up Area Match (+- 5%)
        sqft_a = a["built_up_sqft"]
        sqft_b = b["built_up_sqft"]
        if sqft_a and sqft_b:
            diff_ratio = abs(sqft_a - sqft_b) / max(sqft_a, sqft_b)
            if diff_ratio > 0.05:
                return False

        # 5. Bathroom Count Match
        if a["bathrooms"] is not None and b["bathrooms"] is not None:
            if a["bathrooms"] != b["bathrooms"]:
                return False

        # Secondary Check: Non-conflicting gate facing
        if is_facing_conflicting(a["facing"], b["facing"]):
            return False

        return True

    def run(self) -> Dict[str, Any]:
        """Runs the complete deduplication and consolidation pipeline."""
        conn = get_connection(self.db_path)
        cur = conn.cursor()

        # Load raw items
        cur.execute("SELECT * FROM raw_nobroker_listings")
        nb_raw = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM raw_housing_listings")
        hc_raw = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM raw_99acres_listings")
        acres_raw = [dict(r) for r in cur.fetchall()]

        normalized_items: List[Dict[str, Any]] = []
        for r in nb_raw:
            normalized_items.append(self._normalize_nobroker_row(r))
        for r in hc_raw:
            normalized_items.append(self._normalize_housing_row(r))
        for r in acres_raw:
            normalized_items.append(self._normalize_ninety_nine_acres_row(r))

        print(f"[Consolidator] Ingested {len(nb_raw)} NoBroker + {len(hc_raw)} Housing + {len(acres_raw)} 99acres raw listings.")

        # Resolve Society Name Variations via Token-Set Matching
        all_socs = [item["society"] for item in normalized_items if item["society"] != "Independent Builder Floor / House"]
        unique_socs = sorted(set(all_socs), key=lambda s: len(s))
        canonicals: List[str] = []
        soc_mapping: Dict[str, str] = {}
        for s in unique_socs:
            match = fuzzy_match_society(s, canonicals, threshold=85)
            if match:
                soc_mapping[s] = match
            else:
                canonicals.append(s)
                soc_mapping[s] = s

        for item in normalized_items:
            if item["society"] in soc_mapping:
                item["society"] = soc_mapping[item["society"]]

        # Cluster units via 5-point fingerprint
        clusters: List[List[Dict[str, Any]]] = []
        
        for item in normalized_items:
            matched_cluster = None
            for cl in clusters:
                # 1. Compare core physical attributes against cluster representative
                if not self._matches_5_point_fingerprint(cl[0], item):
                    continue

                # 2. Strict Cluster-Wide Built-up Area Invariant (min/max bound check)
                # Guarantees that EVERY pair in the cluster matches within +-5% tolerance,
                # eliminating order-dependent cluster drift.
                sqft_item = item.get("built_up_sqft") or 0
                if sqft_item > 0:
                    cluster_sqfts = [x["built_up_sqft"] for x in cl if x.get("built_up_sqft") and x["built_up_sqft"] > 0]
                    if cluster_sqfts:
                        min_sqft = min(cluster_sqfts)
                        max_sqft = max(cluster_sqfts)
                        if (abs(sqft_item - min_sqft) / max(sqft_item, min_sqft)) > 0.05:
                            continue
                        if (abs(sqft_item - max_sqft) / max(sqft_item, max_sqft)) > 0.05:
                            continue

                # 3. Transitive Facing Check: candidate must not conflict with ANY member of cluster
                facing_conflict = False
                for existing in cl:
                    if is_facing_conflicting(existing["facing"], item["facing"]):
                        facing_conflict = True
                        break
                if not facing_conflict:
                    matched_cluster = cl
                    break
            
            if matched_cluster is not None:
                matched_cluster.append(item)
            else:
                clusters.append([item])

        print(f"[Consolidator] Identified {len(clusters)} unique physical units across Gurgaon dataset.")

        # Preserve any existing manual audit anomaly tags and manual sector overrides across all constituent portal IDs
        saved_audits_by_uid = {}
        saved_audits_by_portal = {}

        # 1. Load from persistent config/audited_listings.json if present
        audit_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "audited_listings.json")
        if os.path.exists(audit_file):
            try:
                with open(audit_file, "r", encoding="utf-8") as f:
                    file_audits = json.load(f)
                for d in file_audits:
                    tag = d.get("audit_anomaly_tag")
                    sec = d.get("actual_sector")
                    if d.get("uid"):
                        saved_audits_by_uid[d["uid"]] = d
                    if d.get("nobroker_id"):
                        saved_audits_by_portal[("NoBroker", str(d["nobroker_id"]))] = (tag, sec)
                    if d.get("housing_id"):
                        saved_audits_by_portal[("Housing", str(d["housing_id"]))] = (tag, sec)
                    if d.get("acres_id"):
                        saved_audits_by_portal[("99acres", str(d["acres_id"]))] = (tag, sec)
            except Exception:
                pass

        # 2. Layer any in-database audits currently present
        try:
            cur.execute("""
                SELECT uid, nobroker_id, housing_id, acres_id, audit_anomaly_tag, actual_sector 
                FROM consolidated_listings 
                WHERE audit_anomaly_tag IS NOT NULL
            """)
            for r in cur.fetchall():
                d = dict(r)
                tag = d.get("audit_anomaly_tag")
                sec = d.get("actual_sector")
                if d.get("uid"):
                    saved_audits_by_uid[d["uid"]] = d
                if d.get("nobroker_id"):
                    saved_audits_by_portal[("NoBroker", str(d["nobroker_id"]))] = (tag, sec)
                if d.get("housing_id"):
                    saved_audits_by_portal[("Housing", str(d["housing_id"]))] = (tag, sec)
                if d.get("acres_id"):
                    saved_audits_by_portal[("99acres", str(d["acres_id"]))] = (tag, sec)
        except Exception:
            pass

        # Clear and rebuild consolidated_listings
        cur.execute("DELETE FROM consolidated_listings")
        
        multi_portal_dups = 0
        for idx, cl in enumerate(clusters, start=1):
            is_dup = len(cl) > 1
            if is_dup:
                multi_portal_dups += 1

            nb_entry = next((i for i in cl if i["portal"] == "NoBroker"), None)
            hc_entry = next((i for i in cl if i["portal"] == "Housing"), None)
            acres_entry = next((i for i in cl if i["portal"] == "99acres"), None)
            primary = nb_entry if (nb_entry and nb_entry["has_direct_owner"]) else max(cl, key=lambda x: x.get("photos_count") or 0)

            valid_rents = [i["rent"] for i in cl if i["rent"] and i["rent"] > 0]
            min_rent = min(valid_rents) if valid_rents else primary["rent"]
            max_rent = max(valid_rents) if valid_rents else primary["rent"]

            valid_maints = [i["maintenance_amount"] for i in cl if i["maintenance_amount"] and i["maintenance_amount"] > 0]
            maint = min(valid_maints) if valid_maints else primary["maintenance_amount"]
            total_cost = min_rent + maint

            sqft = primary["built_up_sqft"]
            if not sqft or sqft <= 0:
                for i in cl:
                    if i.get("built_up_sqft") and i["built_up_sqft"] > 0:
                        sqft = i["built_up_sqft"]
                        break
            price_sqft = round(total_cost / sqft, 2) if sqft and sqft > 0 else 0.0

            total_flrs = primary["total_floors"]
            if total_flrs is None:
                for i in cl:
                    if i["total_floors"] is not None:
                        total_flrs = i["total_floors"]
                        break

            uid = f"UNIT_{primary['portal'][:2]}_{primary['portal_id']}"
            dedup_id = f"CLUST_{idx:05d}"

            # Dates: earliest posted date and latest refreshed/bumped date
            posted_dates = [i["posted_date"] for i in cl if i.get("posted_date")]
            earliest_posted = min(posted_dates) if posted_dates else None
            updated_dates = [i["last_updated_date"] for i in cl if i.get("last_updated_date")]
            latest_updated = max(updated_dates) if updated_dates else None

            # Spatial sector resolution and manual audit preservation
            text_ctx = f"{primary.get('locality', '')} {primary.get('title', '')} {primary.get('raw_society', '')}"
            actual_sec = self.resolve_actual_sector(primary["latitude"], primary["longitude"], text_context=text_ctx)
            if actual_sec in self.sector_polygons and (not primary.get("latitude") or not primary.get("longitude")):
                poly = self.sector_polygons[actual_sec]
                primary["latitude"] = round(poly.centroid.y, 6)
                primary["longitude"] = round(poly.centroid.x, 6)

            anomaly_tag = None
            # 1. Match by primary UID
            if uid in saved_audits_by_uid:
                anomaly_tag = saved_audits_by_uid[uid].get("audit_anomaly_tag")
                if saved_audits_by_uid[uid].get("actual_sector"):
                    actual_sec = saved_audits_by_uid[uid]["actual_sector"]

            # 2. Match by any constituent portal ID (robust if primary portal changes)
            if not anomaly_tag:
                for member in cl:
                    key = (member.get("portal"), str(member.get("portal_id")))
                    if key in saved_audits_by_portal:
                        saved_tag, saved_sec = saved_audits_by_portal[key]
                        anomaly_tag = saved_tag
                        if saved_sec:
                            actual_sec = saved_sec
                        break

            # 3. Tier-1 Automated Heuristic Engine (Applied if no manual audit tag exists)
            if not anomaly_tag:
                cand_item = {
                    "min_rent": min_rent,
                    "built_up_sqft": sqft,
                    "price_per_sqft": price_sqft,
                    "bhk": primary.get("bhk"),
                    "total_floors": total_flrs,
                    "property_type": primary.get("property_type"),
                    "society": primary.get("society"),
                    "locality": primary.get("locality"),
                    "title": primary.get("title"),
                    "description": primary.get("description"),
                    "actual_sector": actual_sec,
                    "latitude": primary.get("latitude"),
                    "longitude": primary.get("longitude")
                }
                detected_tag, reason = self.anomaly_detector.detect(cand_item)
                if detected_tag:
                    anomaly_tag = detected_tag

            # Cluster-wide flatmate flag
            is_flat = 1 if (anomaly_tag == "FLATMATE_AD" or any(i.get("is_flatmate_listing") for i in cl)) else 0
            if is_flat and not anomaly_tag:
                anomaly_tag = "FLATMATE_AD"

            upsert_consolidated(conn, {
                "uid": uid,
                "dedup_cluster_id": dedup_id,
                "title": primary["title"],
                "society": primary["society"],
                "locality": primary["locality"],
                "bhk": primary["bhk"],
                "min_rent": min_rent,
                "max_rent": max_rent,
                "maintenance_amount": maint,
                "total_monthly_cost": total_cost,
                "deposit": primary["deposit"],
                "built_up_sqft": sqft,
                "carpet_sqft": primary["carpet_sqft"],
                "price_per_sqft": price_sqft,
                "furnishing": primary["furnishing"],
                "bathrooms": primary["bathrooms"],
                "balconies": primary["balconies"],
                "floor": primary["floor"],
                "latitude": primary["latitude"],
                "longitude": primary["longitude"],
                "has_direct_owner": 1 if primary["has_direct_owner"] else 0,
                "owner_name": primary["owner_name"],
                "primary_portal": primary["portal"],
                "best_listing_url": primary["listing_url"],
                "nobroker_id": nb_entry["portal_id"] if nb_entry else None,
                "housing_id": hc_entry["portal_id"] if hc_entry else None,
                "acres_id": acres_entry["portal_id"] if acres_entry else None,
                "duplicate_count": len(cl),
                "property_type": primary["property_type"],
                "is_gated_society": primary["is_gated_society"],
                "total_floors": total_flrs,
                "is_flatmate_listing": is_flat,
                "posted_date": earliest_posted,
                "last_updated_date": latest_updated,
                "description": primary["description"],
                "actual_sector": actual_sec,
                "audit_anomaly_tag": anomaly_tag,
                "photos_count": max((i.get("photos_count") or 0) for i in cl)
            })

        conn.commit()
        conn.close()

        print(f"[Consolidator] Upserted {len(clusters)} physical units ({multi_portal_dups} multi-portal duplicates merged).")
        return {
            "total_raw_listings": len(normalized_items),
            "unique_physical_units": len(clusters),
            "multi_portal_duplicates": multi_portal_dups
        }
