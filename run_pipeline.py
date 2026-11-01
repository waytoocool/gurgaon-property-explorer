#!/usr/bin/env python3
"""
Master Execution Pipeline for Gurgaon Rental Aggregator.
Runs Ingestion (NoBroker + Housing) -> Consolidation -> Market Analytics -> Dashboard Compilation.

Usage:
  # Citywide full Gurgaon extraction across all 151 sectors:
  python run_pipeline.py --all-sectors --nb-pages 1

  # Deep extraction on a single sector:
  python run_pipeline.py --sector dlf_phase_4 --nb-pages 5 --housing-pages 3

  # Re-run consolidation and update dashboard without re-scraping:
  python run_pipeline.py --skip-scrape
"""

import argparse
import yaml
import os
import json
import re
import time
import math
from typing import Dict, Any, Optional, List
from shapely.geometry import shape

from src.db import init_db
from src.collectors.nobroker import NoBrokerCollector
from src.collectors.housing import HousingCollector
from src.collectors.ninety_nine_acres import NinetyNineAcresCollector
from src.consolidator import Consolidator
from src.dashboard_data import export_data_json
from run_dashboard import export_standalone_html

def load_all_geojson_sectors(geojson_path: str = "config/gurgaon_sectors.geojson") -> List[Dict[str, Any]]:
    """Loads all official sector features and computes their geographic centroids."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_dir, geojson_path) if not os.path.isabs(geojson_path) else geojson_path
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"Missing sector GeoJSON at: {full_path}")

    with open(full_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Conversion factor from square degrees to sq km at Gurgaon latitude (28.45 deg N)
    sq_deg_to_sqkm = 111.0 * (111.0 * math.cos(math.radians(28.45)))

    sectors = []
    for feat in data.get("features", []):
        name = feat["properties"].get("sector_name") or feat["properties"].get("name")
        geom = shape(feat["geometry"])
        c = geom.centroid
        sectors.append({
            "name": name,
            "display_name": feat["properties"].get("display_name", name),
            "lat": c.y,
            "lon": c.x,
            "area_sqkm": geom.area * sq_deg_to_sqkm
        })
    return sectors

def load_sector_config(sector_key: str = "dlf_phase_4") -> Dict[str, Any]:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "sectors.yaml")
    data = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    seeds = data.get("crawl_seeds", {}) or data.get("sectors", {})

    cfg = {}
    if sector_key in seeds:
        cfg = dict(seeds[sector_key])
    else:
        # Check normalized key match in seeds
        norm_key = re.sub(r"[^a-z0-9]", "", sector_key.lower())
        for k, v in seeds.items():
            if re.sub(r"[^a-z0-9]", "", k.lower()) == norm_key:
                cfg = dict(v)
                break
            if re.sub(r"[^a-z0-9]", "", v.get("name", "").lower()) == norm_key:
                cfg = dict(v)
                break
            if re.sub(r"[^a-z0-9]", "", v.get("primary_sector", "").lower()) == norm_key:
                cfg = dict(v)
                break

    # If still not found, check official GMDA sector polygons in GeoJSON
    if not cfg:
        norm_key = re.sub(r"[^a-z0-9]", "", sector_key.lower())
        try:
            geojson_sectors = load_all_geojson_sectors()
            for sec in geojson_sectors:
                sec_norm = re.sub(r"[^a-z0-9]", "", sec["name"].lower())
                if sec_norm == norm_key or (norm_key.isdigit() and sec_norm == f"sector{norm_key}"):
                    cfg = {
                        "name": sec["name"],
                        "primary_sector": sec["name"],
                        "nobroker": {
                            "lat": sec["lat"],
                            "lon": sec["lon"],
                            "place_name": sec["name"],
                            "place_id": "",
                            "radius": 2.0
                        },
                        "housing": {
                            "url": HousingCollector.get_sector_url(sec["name"])
                        }
                    }
                    break
        except Exception:
            pass

    if not cfg:
        # Check if Housing catalog has this sector or enclave name
        h_url = HousingCollector.get_sector_url(sector_key)
        if h_url:
            cfg = {
                "name": sector_key,
                "primary_sector": sector_key,
                "nobroker": {
                    "place_name": sector_key,
                    "radius": 2.0
                },
                "housing": {
                    "url": h_url
                }
            }
        else:
            return {}

    # Guarantee housing URL is populated if available in housing_sectors.json catalog
    if "housing" not in cfg or not cfg["housing"].get("url"):
        resolved_h_url = (
            HousingCollector.get_sector_url(cfg.get("primary_sector", "")) or
            HousingCollector.get_sector_url(cfg.get("name", "")) or
            HousingCollector.get_sector_url(sector_key)
        )
        if resolved_h_url:
            cfg.setdefault("housing", {})["url"] = resolved_h_url

    return cfg

def run(
    all_sectors: bool = False,
    custom_sectors: Optional[str] = None,
    bhk: Optional[int] = None,
    sector_key: str = "dlf_phase_4",
    nb_pages: int = 1,
    housing_pages: int = 0,
    housing_start_page: int = 1,
    acres_pages: int = 0,
    delay_seconds: float = 0.3,
    skip_scrape: bool = False,
    min_photos: Optional[int] = 2
):
    print("=" * 75)
    print("       GURGAON RENTAL INTELLIGENCE PIPELINE")
    print("=" * 75)

    # 1. Initialize DB schema
    init_db()

    nb_bhk_type = f"BHK{bhk}" if bhk else None
    if bhk:
        print(f"[Target BHK]: Explicitly filtering and sourcing {bhk} BHK units (NoBroker type={nb_bhk_type})")

    if not skip_scrape:
        if all_sectors or custom_sectors:
            sectors = load_all_geojson_sectors()
            
            if custom_sectors:
                req_names = [s.strip().lower() for s in custom_sectors.split(",")]
                sectors = [s for s in sectors if any(rn == s["name"].lower() or rn in s["name"].lower() for rn in req_names)]
                print(f"\n[Mode]: Custom Sector Sweep ({len(sectors)} Sectors: {[s['name'] for s in sectors]})")
            else:
                print(f"\n[Mode]: Citywide Extraction across all {len(sectors)} Gurgaon Sectors")

            total_sectors = len(sectors)
            
            if nb_pages > 0:
                print(f"[NoBroker]: {nb_pages} page(s) per sector | Delay: {delay_seconds}s per call | BHK: {nb_bhk_type or 'All'}\n")

                total_nb_processed = 0
                t_start = time.time()

                for i, sec in enumerate(sectors, 1):
                    s_name = sec["name"]
                    lat = sec["lat"]
                    lon = sec["lon"]
                    
                    # Skip dedicated military non-residential zones
                    if "Ammunition Depot" in s_name:
                        print(f"[{i:3d}/{total_sectors}] {s_name:<28} -> Skipped (Defense Non-Residential Zone)")
                        continue

                    nb = NoBrokerCollector(
                        lat=lat,
                        lon=lon,
                        place_name=s_name,
                        place_id="",
                        radius=1.5,
                        bhk_type=nb_bhk_type
                    )

                    try:
                        res = nb.scrape(start_page=1, end_page=nb_pages, delay_seconds=delay_seconds)
                        total_nb_processed += res["total_processed"]
                        print(f"[{i:3d}/{total_sectors}] {s_name:<28} -> {res['total_processed']:2d} listings (Centroid: {lat:.4f}, {lon:.4f})")
                    except Exception as e:
                        print(f"[{i:3d}/{total_sectors}] {s_name:<28} -> Error: {e}")

                    time.sleep(delay_seconds)

                t_elapsed = time.time() - t_start
                print(f"\n-> NoBroker Sweep Complete: {total_nb_processed} raw listings processed across {total_sectors} sectors in {t_elapsed:.1f}s.")
            else:
                print("\n[NoBroker]: nb_pages=0, skipping NoBroker crawl.")

            # Optional Housing.com Sweep
            if housing_pages > 0:
                if custom_sectors:
                    print(f"\n[Housing.com]: Ingesting {housing_pages} page(s) across {len(sectors)} custom sector(s)...")
                    total_h_processed = 0
                    for sec in sectors:
                        s_name = sec["name"]
                        h_url = HousingCollector.get_sector_url(s_name)
                        if not h_url:
                            print(f"[{s_name}] -> Skipped Housing (no canonical URL in catalog)")
                            continue
                        target_h_url = HousingCollector.to_bhk_url(h_url, bhk=bhk)
                        h_collector = HousingCollector(target_url=target_h_url, headless=False, bhk=bhk)
                        try:
                            h_result = h_collector.scrape(pages=housing_pages, start_page=housing_start_page, delay_per_page=3.5)
                            total_h_processed += h_result["total_processed"]
                            print(f"[{s_name}] -> {h_result['total_processed']} Housing listings.")
                        except Exception as e:
                            print(f"[{s_name}] -> Housing Error: {e}")
                    print(f"-> Housing Multi-Sector Sweep Complete: {total_h_processed} listings processed.\n")
                else:
                    h_url = HousingCollector.to_bhk_url(HousingCollector.CITYWIDE_URL, bhk=bhk)
                    print(f"\n[Housing.com]: Ingesting {housing_pages} page(s) ({h_url})...")
                    h_collector = HousingCollector(target_url=h_url, headless=False, bhk=bhk)
                    h_result = h_collector.scrape(pages=housing_pages, start_page=housing_start_page, delay_per_page=3.5)
                    print(f"-> Housing Complete: {h_result['total_processed']} listings processed across {h_result['pages_completed']} pages.\n")

            # Optional 99acres Sweep (Citywide fallback notice)
            if acres_pages > 0:
                if custom_sectors:
                    print(f"\n[99acres]: Ingesting {acres_pages} page(s) across {len(sectors)} custom sector(s)...")
                    for sec in sectors:
                        s_name = sec["name"]
                        a_collector = NinetyNineAcresCollector(sector_name=s_name, bhk=bhk)
                        a_result = a_collector.scrape(pages=acres_pages, delay_per_page=3.5)
                        print(f"[{s_name}] -> {a_result['total_processed']} 99acres listings.")
                else:
                    a_url = NinetyNineAcresCollector.CITYWIDE_2BHK_URL if bhk == 2 else NinetyNineAcresCollector.CITYWIDE_URL
                    print(f"\n[99acres]: Ingesting {acres_pages} citywide page(s) ({a_url})...")
                    print("   [Notice]: Multi-sector dynamic indexing for 99acres is planned for dedicated phase; fetching citywide.")
                    a_collector = NinetyNineAcresCollector(target_url=a_url, bhk=bhk)
                    a_result = a_collector.scrape(pages=acres_pages, delay_per_page=3.5)
                    print(f"-> 99acres Complete: {a_result['total_processed']} listings processed across {a_result['pages_completed']} pages.\n")
        else:
            # Single Sector Mode
            sector_cfg = load_sector_config(sector_key)
            sector_name = sector_cfg.get("primary_sector") or sector_cfg.get("name", sector_key)
            print(f"\n[Target Sector Mode]: {sector_name} (key: {sector_key})")

            # Scrape NoBroker
            nb_cfg = sector_cfg.get("nobroker", {})
            nb_collector = NoBrokerCollector(
                lat=nb_cfg.get("lat", 28.4671),
                lon=nb_cfg.get("lon", 77.0820),
                place_name=nb_cfg.get("place_name", sector_name),
                place_id=nb_cfg.get("place_id", ""),
                radius=nb_cfg.get("radius", 2.5),
                bhk_type=nb_bhk_type
            )
            nb_result = nb_collector.scrape(start_page=1, end_page=nb_pages, delay_seconds=delay_seconds)
            print(f"-> NoBroker Complete: {nb_result['total_processed']} listings processed across {nb_result['pages_completed']} pages.\n")

            # Scrape Housing.com (if URL configured or resolved for this sector)
            h_cfg = sector_cfg.get("housing", {})
            h_url = h_cfg.get("url") or HousingCollector.get_sector_url(sector_name) or HousingCollector.get_sector_url(sector_key)
            if h_url and housing_pages > 0:
                target_h_url = HousingCollector.to_bhk_url(h_url, bhk=bhk)
                h_collector = HousingCollector(target_url=target_h_url, headless=False, bhk=bhk)
                h_result = h_collector.scrape(pages=housing_pages, start_page=housing_start_page, delay_per_page=4.0)
                print(f"-> Housing Complete: {h_result['total_processed']} listings processed across {h_result['pages_completed']} pages.\n")
            elif housing_pages > 0:
                print(f"[Housing.com]: No configured or resolved search URL for '{sector_name}' ('{sector_key}'). Skipping Housing scrape.")

            # Scrape 99acres (if requested and configured)
            if acres_pages > 0:
                print(f"\n[99acres]: Ingesting {acres_pages} page(s) for {sector_name}...")
                a_collector = NinetyNineAcresCollector(sector_name=sector_name, bhk=bhk)
                a_result = a_collector.scrape(pages=acres_pages, delay_per_page=3.5)
                print(f"-> 99acres Complete: {a_result['total_processed']} listings processed across {a_result['pages_completed']} pages.\n")

    # 2. Run Consolidator (5-Point Physical Fingerprint & Shapely Point-in-Polygon)
    print("\n--- Running Consolidation & Physical Unit Deduplication ---")
    consolidator = Consolidator()
    cons_res = consolidator.run()
    print(f"-> Consolidation Complete: {cons_res['unique_physical_units']} unique physical units ({cons_res['multi_portal_duplicates']} duplicates merged).\n")

    # 3. Update Dashboard Payload & Standalone HTML
    print("--- Updating Production Leaflet Dashboard & Standalone HTML ---")
    export_data_json(min_photos=min_photos)
    export_standalone_html(min_photos=min_photos)
    print("-> Dashboard datasets recompiled successfully.\n")

    # 4. Completion Summary
    print("=" * 75)
    print("       PIPELINE EXECUTION COMPLETE")
    print("=" * 75)
    print(f"✓ Unique Physical Units in DB : {cons_res['unique_physical_units']}")
    print(f"✓ Multi-Portal Units Merged   : {cons_res['multi_portal_duplicates']}")
    print(f"✓ Total Raw Listings Tracked  : {cons_res['total_raw_listings']}")
    print("-" * 75)
    print("✓ Local Dashboard  : python run_dashboard.py (http://localhost:8050)")
    print("✓ Live Web Map     : https://waytoocool.github.io/gurgaon-property-explorer/")
    print("✓ Standalone File  : dashboard.html (portable zero-dependency dashboard)")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Master Gurgaon Rental Aggregator Pipeline")
    parser.add_argument("--all-sectors", action="store_true", help="Crawl all 169 sectors dynamically using official GeoJSON centroids")
    parser.add_argument("--sectors", type=str, default=None, help="Comma-separated sector names to crawl (e.g. 'Sector 43,Sector 56,Sector 48')")
    parser.add_argument("--bhk", type=int, default=None, help="Filter search to specific BHK count (e.g. --bhk 2 for 2-BHK specific sourcing)")
    parser.add_argument("--sector", default="dlf_phase_4", help="Single sector key defined in config/sectors.yaml")
    parser.add_argument("--nb-pages", type=int, default=None, help="Number of NoBroker pages to fetch per sector (default: 1 for multi-sector, 3 for single)")
    parser.add_argument("--housing-pages", type=int, default=None, help="Number of Housing.com pages to fetch (default: 0 for all-sectors, 2 for single-sector)")
    parser.add_argument("--housing-start-page", type=int, default=1, help="Starting page for Housing.com pagination (default: 1)")
    parser.add_argument("--acres-pages", type=int, default=None, help="Number of 99acres pages to fetch (default: 0)")
    parser.add_argument("--delay", type=float, default=0.25, help="Polite delay between consecutive API requests (default: 0.25s)")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping and rerun consolidation on existing raw data")
    parser.add_argument("--min-photos", type=int, default=2, help="Filter listings with at least N photos for dashboard export (default: 2)")
    parser.add_argument("--all-photos", action="store_true", help="Include listings with zero or single static photos in dashboard export")
    args = parser.parse_args()

    min_photos = None if args.all_photos else args.min_photos

    is_multi_sector = args.all_sectors or bool(args.sectors)
    
    # Resolve page counts without clobbering explicit user values (e.g. --nb-pages 1)
    if args.nb_pages is not None:
        nb_p = args.nb_pages
    else:
        nb_p = 1 if is_multi_sector else 3

    if args.housing_pages is not None:
        h_p = args.housing_pages
    else:
        h_p = 0 if is_multi_sector else 2

    if args.acres_pages is not None:
        a_p = args.acres_pages
    else:
        a_p = 0

    run(
        all_sectors=args.all_sectors,
        custom_sectors=args.sectors,
        bhk=args.bhk,
        sector_key=args.sector,
        nb_pages=nb_p,
        housing_pages=h_p,
        housing_start_page=args.housing_start_page,
        acres_pages=a_p,
        delay_seconds=args.delay,
        skip_scrape=args.skip_scrape,
        min_photos=min_photos
    )
