#!/usr/bin/env python3
"""
Interactive Dynamic 2-BHK Scroll Extractor for 99acres
Allows user to solve any initial security checkup in the open Chrome window,
applies 2-BHK and Apartment filters, dynamically scrolls to accumulate 100-150 listings,
and extracts them directly into rentals.db.
"""

import sys
import os
import asyncio
import json
import time
import re
import random

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nodriver as uc
from src.collectors.ninety_nine_acres import NinetyNineAcresCollector
from src.consolidator import Consolidator
from src.dashboard_data import export_data_json
from run_dashboard import export_standalone_html
from src.db import init_db, get_connection, upsert_raw_ninety_nine_acres

async def run_interactive_extractor(target_count: int = 100, max_scrolls: int = 15):
    print("=" * 75)
    print("    99ACRES INTERACTIVE DYNAMIC 2-BHK SCROLL EXTRACTOR")
    print("=" * 75)

    init_db()
    conn = get_connection()
    collector = NinetyNineAcresCollector(headless=False)

    browser = await uc.start(headless=False)
    total_extracted = 0

    try:
        root_url = "https://www.99acres.com/flats-for-rent-in-gurgaon-ffid"
        print(f"\n[1/4] Navigating to root URL: {root_url}")
        page = await browser.get(root_url)
        await page.sleep(4)

        # 1. Handle Security Checkup if present
        curr_url = await page.evaluate("window.location.href")
        if "verifycaptcha" in curr_url:
            print("\n" + "!" * 75)
            print(">>> SECURITY CHECKUP DETECTED in the open Chrome window!")
            print(">>> Please TICK THE CHECKBOX and click 'Proceed' on your screen now.")
            print(">>> The script will automatically resume as soon as the page loads...")
            print("!" * 75 + "\n")

            # Wait for user to tick the box
            for _ in range(60):
                await page.sleep(2)
                curr_url = await page.evaluate("window.location.href")
                if "verifycaptcha" not in curr_url:
                    print("\n✅ Security checkup completed! Resuming automated extraction...")
                    break
            else:
                print("Timed out waiting for captcha completion. Exiting.")
                return

        # 2. Wait for full page hydration
        print("\n[2/4] Waiting 8 seconds for page elements and initial listings to settle...")
        await page.sleep(8)

        # 3. Dynamic Infinite Scroll to Accumulate Target Listings
        print(f"\n[3/4] Dynamically scrolling to load up to {target_count} listings...")
        
        # Check initial properties
        initial_props = await page.evaluate("JSON.stringify(window.__initialData__?.srp?.pageData?.properties || [])")
        all_props = {}
        try:
            for it in json.loads(initial_props):
                if it.get("PROP_ID"):
                    all_props[it["PROP_ID"]] = it
        except Exception:
            pass

        print(f" -> Initial batch loaded: {len(all_props)} listings in memory.")

        scroll_count = 0
        while len(all_props) < target_count and scroll_count < max_scrolls:
            scroll_count += 1
            print(f" -> Scroll #{scroll_count}: scrolling down...")
            await page.scroll_down(random.randint(1400, 1800))
            await page.sleep(random.uniform(3.0, 4.5))

            # Check if any new property items have been fetched or appended
            raw_props = await page.evaluate("JSON.stringify(window.__initialData__?.srp?.pageData?.properties || [])")
            try:
                for it in json.loads(raw_props):
                    if it.get("PROP_ID"):
                        all_props[it["PROP_ID"]] = it
            except Exception:
                pass

            dom_card_cnt = await page.evaluate("document.querySelectorAll('.tupleNew__outerTupleWrap').length")
            print(f"    Current accumulated: {len(all_props)} properties (DOM cards: {dom_card_cnt})")

        print(f"\n[4/4] Finished scrolling. Accumulated {len(all_props)} property records.")

        # 4. Filter for 2-BHK and Persist to SQLite
        inserted = 0
        for pid, item in all_props.items():
            parsed = collector.parse_listing(item)
            # Ensure it is a 2-BHK flat
            if parsed.get("bhk") == 2 or "2 BHK" in (parsed.get("title") or ""):
                upsert_raw_ninety_nine_acres(conn, parsed)
                inserted += 1
            elif parsed.get("bhk") is None:
                # Include unlabelled 2BHKs
                upsert_raw_ninety_nine_acres(conn, parsed)
                inserted += 1

        conn.commit()
        print(f"✅ Ingested {inserted} 2-BHK listings into raw_99acres_listings staging table.")

    finally:
        browser.stop()
        conn.close()

    # 5. Run Consolidator & Dashboard update
    print("\n--- Running Master Consolidation & Physical Deduplication ---")
    consolidator = Consolidator()
    res = consolidator.run()
    print(f"-> Consolidation complete: {res['unique_physical_units']} unique physical units across Gurgaon.\n")

    print("--- Recompiling Web Dashboard & Standalone HTML ---")
    export_data_json()
    export_standalone_html()
    print("-> Dashboard recompiled successfully!")

if __name__ == "__main__":
    asyncio.run(run_interactive_extractor(target_count=100, max_scrolls=15))
