#!/usr/bin/env python3
"""
Citywide 99acres Rental Ingestion & Market Aggregator Sweep
Extracts high-density rental inventory across all sectors and prime corridors in Gurgaon,
runs 5-point physical unit deduplication, and updates the production Leaflet dashboard.
"""

import sys
import os
import argparse
import time
from typing import Optional, List, Dict

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.collectors.ninety_nine_acres import NinetyNineAcresCollector
from src.consolidator import Consolidator
from src.dashboard_data import export_data_json
from run_dashboard import export_standalone_html
from src.analyzer import MarketAnalyzer
from src.db import init_db

def run_citywide_acres_sweep(
    deep: bool = False,
    batch: Optional[int] = None,
    custom_sectors: Optional[str] = None,
    delay_per_page: float = 4.0,
    min_photos: int = 2
):
    print("=" * 75)
    print("       99ACRES GURGAON INGESTION & CONSOLIDATION SWEEP (HARDENED)")
    print("=" * 75)

    init_db()

    # Pre-defined safe micro-batches (2 pages each, max 4 pages total per run)
    micro_batches = {
        1: [
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-43-gurgaon-ffid", "pages": 2, "name": "Sector 43 (Golf Course / Central)"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-56-gurgaon-ffid", "pages": 2, "name": "Sector 56 (Golf Course Ext. Hub)"},
        ],
        2: [
            {"url": "https://www.99acres.com/flats-for-rent-in-golf-course-road-gurgaon-ffid", "pages": 2, "name": "Golf Course Road Prime"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-54-gurgaon-ffid", "pages": 2, "name": "Sector 54 (Suncity / High-Rise)"},
        ],
        3: [
            {"url": "https://www.99acres.com/flats-for-rent-in-sohna-road-gurgaon-ffid", "pages": 2, "name": "Sohna Road Corridor"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-48-gurgaon-ffid", "pages": 2, "name": "Sector 48 (Central Park / Tatvam)"},
        ],
        4: [
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-65-gurgaon-ffid", "pages": 2, "name": "Sector 65 (M3M Golfestate / Emaar)"},
            {"url": "https://www.99acres.com/flats-for-rent-in-golf-course-extension-road-gurgaon-ffid", "pages": 2, "name": "Golf Course Ext Rd"},
        ],
        5: [
            {"url": "https://www.99acres.com/2-bhk-flats-for-rent-in-gurgaon-ffid", "pages": 2, "name": "Citywide 2-BHK High Priority"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-70-gurgaon-ffid", "pages": 2, "name": "Sector 70 (SPR High-Rise Hub)"},
        ]
    }

    if custom_sectors:
        sec_names = [s.strip() for s in custom_sectors.split(",")]
        targets = []
        for s in sec_names:
            slug = NinetyNineAcresCollector.to_sector_slug(s)
            targets.append({
                "url": f"https://www.99acres.com/flats-for-rent-in-{slug}-gurgaon-ffid",
                "pages": 2,
                "name": s
            })
        print(f"[Mode]: CUSTOM SECTOR BATCH ({len(targets)} sectors: {sec_names})")
    elif batch:
        targets = micro_batches.get(batch, micro_batches[1])
        print(f"[Mode]: SAFE MICRO-BATCH #{batch} ({len(targets)} targets, max 2 pages each)")
    elif deep:
        print("[Mode]: DEEP SWEEP (Broad coverage across citywide feeds, corridors, and top sectors)")
        targets = [
            {"url": "https://www.99acres.com/flats-for-rent-in-gurgaon-ffid", "pages": 2, "name": "Citywide All Rentals"},
            {"url": "https://www.99acres.com/2-bhk-flats-for-rent-in-gurgaon-ffid", "pages": 2, "name": "Citywide 2-BHK Inventory"},
            {"url": "https://www.99acres.com/3-bhk-flats-for-rent-in-gurgaon-ffid", "pages": 2, "name": "Citywide 3-BHK Inventory"},
            {"url": "https://www.99acres.com/flats-for-rent-in-golf-course-road-gurgaon-ffid", "pages": 2, "name": "Golf Course Road"},
            {"url": "https://www.99acres.com/flats-for-rent-in-golf-course-extension-road-gurgaon-ffid", "pages": 2, "name": "Golf Course Ext Rd"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sohna-road-gurgaon-ffid", "pages": 2, "name": "Sohna Road"},
            {"url": "https://www.99acres.com/flats-for-rent-in-dwarka-expressway-gurgaon-ffid", "pages": 2, "name": "Dwarka Expressway"},
            {"url": "https://www.99acres.com/flats-for-rent-in-southern-peripheral-road-gurgaon-ffid", "pages": 2, "name": "SPR Corridor"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-43-gurgaon-ffid", "pages": 2, "name": "Sector 43"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-56-gurgaon-ffid", "pages": 2, "name": "Sector 56"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-54-gurgaon-ffid", "pages": 2, "name": "Sector 54"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-48-gurgaon-ffid", "pages": 2, "name": "Sector 48"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-65-gurgaon-ffid", "pages": 2, "name": "Sector 65"},
            {"url": "https://www.99acres.com/flats-for-rent-in-sector-70-gurgaon-ffid", "pages": 2, "name": "Sector 70"},
        ]
    else:
        print("[Mode]: STANDARD MICRO-BATCH #1 (Sector 43 & Sector 56, 2 pages each)")
        targets = micro_batches[1]

    total_target_pages = sum(t["pages"] for t in targets)
    print(f"[Targets]: {len(targets)} search feeds | Total Pages: {total_target_pages}")
    print(f"[Delay]: {delay_per_page}s per page | Anti-Bot: nodriver warm-up on 99acres.com\n")

    # 1. Execute Multi-Target Extraction in Single Browser Instance
    collector = NinetyNineAcresCollector(headless=False)
    t0 = time.time()
    res = collector.scrape_targets(targets=targets, delay_per_page=delay_per_page)
    t_elapsed = time.time() - t0

    print("\n" + "-" * 75)
    print(f"-> Extraction Complete: {res['total_processed']} listings ingested across {res['pages_completed']} pages in {t_elapsed:.1f}s.")
    print("-" * 75)

    # 2. Run Consolidation & Deduplication
    print("\n--- Running 5-Point Deduplication & Statutory Sector Assignment ---")
    consolidator = Consolidator()
    cons_res = consolidator.run()
    print(f"-> Consolidation Complete: {cons_res['unique_physical_units']} unique physical units ({cons_res['multi_portal_duplicates']} duplicates merged).\n")

    # 3. Update Dashboard Datasets
    print("--- Updating Production Leaflet Dashboard & Standalone HTML ---")
    export_data_json(min_photos=min_photos)
    export_standalone_html(min_photos=min_photos)
    print("-> Dashboard datasets recompiled successfully.\n")

    # 4. Print Summary Analytics
    print("--- Quick Market Summary (High-Rise Condominiums, Active 30 Days) ---")
    analyzer = MarketAnalyzer()
    analyzer.print_analysis(
        source="consolidated",
        gated_only=False,
        min_floors=6,
        max_age_days=30,
        min_photos=min_photos
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Citywide 99acres Ingestion Sweep for Gurgaon")
    parser.add_argument("--batch", type=int, default=None, help="Run a specific safe micro-batch (1: Sec 43+56, 2: Golf Course+Sec 54, 3: Sohna+Sec 48, 4: Sec 65+Ext, 5: 2BHK+Sec 70)")
    parser.add_argument("--sectors", type=str, default=None, help="Comma-separated custom sector names to crawl (e.g. 'Sector 43,Sector 56')")
    parser.add_argument("--deep", action="store_true", help="Run a broad multi-target sweep (14 distinct feeds)")
    parser.add_argument("--delay", type=float, default=4.0, help="Polite delay per page in seconds (default: 4.0s)")
    parser.add_argument("--min-photos", type=int, default=2, help="Minimum photos filter for dashboard export (default: 2)")

    args = parser.parse_args()
    run_citywide_acres_sweep(
        deep=args.deep,
        batch=args.batch,
        custom_sectors=args.sectors,
        delay_per_page=args.delay,
        min_photos=args.min_photos
    )
