import os
import json
import sqlite3
import yaml
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

def compile_dashboard_payload(db_path: str = "rentals.db", min_photos: Optional[int] = 2) -> Dict[str, Any]:
    """
    Compiles official HUDA sector multi-vertex polygons and verified physical listing records
    into an integrated, zero-lag dataset for the production Leaflet dashboard.
    Filters out listings with < 2 photos (no image or single static image) by default.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    geo_path = os.path.join(base_dir, "config", "gurgaon_sectors.geojson")
    yaml_path = os.path.join(base_dir, "config", "sectors.yaml")
    full_db_path = os.path.join(base_dir, db_path) if not os.path.isabs(db_path) else db_path
    transit_path = os.path.join(base_dir, "config", "gurgaon_transit.geojson")

    # 1. Load GeoJSON Polygons
    if not os.path.exists(geo_path):
        raise FileNotFoundError(f"Missing sector GeoJSON at {geo_path}")
    with open(geo_path, "r", encoding="utf-8") as f:
        geo_data = json.load(f)

    # 1b. Load Transit & Landmarks GeoJSON
    transit_data = None
    if os.path.exists(transit_path):
        with open(transit_path, "r", encoding="utf-8") as f:
            transit_data = json.load(f)

    # 2. Load Sector Aliases from YAML
    aliases_map = {}
    if os.path.exists(yaml_path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            for s_name, s_info in cfg.get("huda_sectors", {}).items():
                aliases_map[s_name] = s_info.get("aliases", [])

    # 3. Query Clean Listings from SQLite
    conn = sqlite3.connect(full_db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = """
        SELECT uid, title, society, locality, bhk, min_rent, max_rent, 
               maintenance_amount, total_monthly_cost, built_up_sqft, carpet_sqft,
               price_per_sqft, furnishing, bathrooms, balconies, floor, total_floors,
               latitude, longitude, has_direct_owner, owner_name, primary_portal,
               best_listing_url, nobroker_id, housing_id, acres_id,
               property_type, is_gated_society, posted_date, 
               last_updated_date, actual_sector, audit_anomaly_tag, photos_count
        FROM consolidated_listings
        WHERE (audit_anomaly_tag IS NULL 
           OR audit_anomaly_tag NOT IN ('MIS_PINNED_LOCATION', 'DATA_ENTRY_ERROR', 'FLATMATE_AD'))
          AND (is_flatmate_listing IS NULL OR is_flatmate_listing = 0)
    """
    params = []
    if min_photos is not None and min_photos > 0:
        query += " AND photos_count >= ?"
        params.append(min_photos)

    query += " ORDER BY total_monthly_cost ASC"
    cur.execute(query, params)
    listings = [dict(r) for r in cur.fetchall()]
    conn.close()

    # 4. Enrich GeoJSON Features with Aliases and Centroids
    for feat in geo_data.get("features", []):
        props = feat.get("properties", {})
        sec_name = props.get("sector_name")
        props["aliases"] = aliases_map.get(sec_name, [])
        # Compute bounding box & center for fast Leaflet zooming
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        all_pts = []
        if geom.get("type") == "Polygon":
            all_pts = coords[0] if coords else []
        elif geom.get("type") == "MultiPolygon":
            for poly in coords:
                if poly:
                    all_pts.extend(poly[0])
        
        if all_pts:
            lons = [p[0] for p in all_pts]
            lats = [p[1] for p in all_pts]
            props["min_lon"] = min(lons)
            props["max_lon"] = max(lons)
            props["min_lat"] = min(lats)
            props["max_lat"] = max(lats)
            props["center_lon"] = sum(lons) / len(lons)
            props["center_lat"] = sum(lats) / len(lats)

    payload = {
        "metadata": {
            "title": "Gurgaon Rental Explorer Intelligence Dataset",
            "total_clean_listings": len(listings),
            "total_sectors": len(geo_data.get("features", [])),
            "generated_at": datetime.now(timezone.utc).isoformat()
        },
        "sectors": geo_data,
        "listings": listings,
        "transit": transit_data
    }
    return payload

def export_data_json(output_path: str = "web/data.json", db_path: str = "rentals.db", min_photos: Optional[int] = 2) -> str:
    """Exports compiled dashboard payload to a static JSON file (default min_photos=2)."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_output = os.path.join(base_dir, output_path) if not os.path.isabs(output_path) else output_path
    os.makedirs(os.path.dirname(full_output), exist_ok=True)

    payload = compile_dashboard_payload(db_path, min_photos=min_photos)
    with open(full_output, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(',', ':'))
    
    print(f"[DashboardData] Exported {payload['metadata']['total_clean_listings']} listings across {payload['metadata']['total_sectors']} sectors (min_photos={min_photos}) to {full_output}")
    return full_output

if __name__ == "__main__":
    export_data_json()
