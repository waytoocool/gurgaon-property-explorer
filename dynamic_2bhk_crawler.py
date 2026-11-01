#!/usr/bin/env python3
"""
Production Dynamic 2-BHK Scroll Extractor for 99acres
Applies verified 2-BHK and Residential Apartment DOM filters,
executes the patient scroll-and-wait loop until 100-150 listings are rendered,
extracts all listings into rentals.db, runs 5-point deduplication, and updates the dashboard.
"""

import sys
import os
import asyncio
import json
import re
import random
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nodriver as uc
from bs4 import BeautifulSoup
from src.collectors.ninety_nine_acres import NinetyNineAcresCollector
from src.consolidator import Consolidator
from src.dashboard_data import export_data_json
from run_dashboard import export_standalone_html
from src.analyzer import MarketAnalyzer
from src.db import init_db, get_connection, upsert_raw_ninety_nine_acres

def parse_eval_result(raw: Any) -> Dict[str, Any]:
    """Robust parser for nodriver evaluate responses (JSON string, list of entries, or dict)."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    if isinstance(raw, list):
        try:
            return {k: (v.get("value") if isinstance(v, dict) else v) for k, v in raw}
        except Exception:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}

# Patch nodriver Browser.start to allow ample macOS cold-start time and prevent duplicate empty windows
orig_browser_start = uc.Browser.start

async def patient_browser_start(self):
    from nodriver.core import browser as browser_mod, util
    import pathlib

    if not self:
        raise RuntimeError("use await Browser.create() to create a new instance")
    if self._process or self._process_pid:
        if self._process.returncode is not None:
            return await self.create(config=self.config)
        raise RuntimeError("ignored! this call has no effect when already running.")

    connect_existing = False
    if self.config.host is not None and self.config.port is not None:
        connect_existing = True
    else:
        self.config.host = "127.0.0.1"
        self.config.port = util.free_port()

    if not connect_existing:
        if not pathlib.Path(self.config.browser_executable_path).exists():
            raise FileNotFoundError("Could not determine browser executable.")

    exe = self.config.browser_executable_path
    params = self.config()

    if not connect_existing:
        self._process = await asyncio.create_subprocess_exec(
            exe, *params,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=True,
        )
        self._process_pid = self._process.pid

    self._http = browser_mod.HTTPApi((self.config.host, self.config.port))
    util.get_registered_instances().add(self)
    await asyncio.sleep(0.5)

    # Allow up to 15 retries (7.5s) for macOS cold start
    for _ in range(15):
        try:
            self.info = browser_mod.ContraDict(await self._http.get("version"), silent=True)
            if self.info:
                break
        except Exception:
            await asyncio.sleep(0.5)

    if not self.info:
        if getattr(self, "_process", None):
            try:
                self._process.kill()
            except Exception:
                pass
        raise Exception("Failed to connect to browser after 15 attempts")

    self.websocket_url = self.info.webSocketDebuggerUrl
    await self.attach()
    await self.update_targets()
    while not self.tabs:
        await asyncio.sleep(0.1)
        await self.update_targets()
    return self

uc.Browser.start = patient_browser_start

async def run_dynamic_2bhk_extraction(
    target_count: int = 120,
    max_scroll_cycles: int = 15,
    headless: bool = False
):
    print("=" * 75)
    print("       99ACRES DYNAMIC 2-BHK HIGH-RISE SCROLL EXTRACTOR")
    print("=" * 75)

    init_db()
    conn = get_connection()

    # Launch browser: exactly one clean window
    print(f"\n[Step 1/5] Launching Chrome (headless={headless})...")
    config = uc.Config()
    config.headless = headless
    config.add_argument('--window-size=1920,1080')
    config.add_argument('--start-maximized')
    browser = await uc.start(config)
    print(" -> Browser connected successfully (single window)!")

    try:
        root_url = "https://www.99acres.com/flats-for-rent-in-gurgaon-ffid"
        print(f" -> Opening root URL: {root_url}")
        page = await browser.get(root_url)

        # Allow user to solve checkup if it appears
        curr_url = await page.evaluate("window.location.href")
        page_title = await page.evaluate("document.title")
        page_text = await page.evaluate("document.body ? document.body.innerText.slice(0, 500) : ''")
        is_blocked = ("verifycaptcha" in curr_url or "Security checkup" in page_title or 
                      "Just a moment" in page_title or "Access Denied" in page_title or 
                      "verifycaptcha" in page_text or "Security checkup" in page_text)

        if is_blocked:
            print("\n" + "!" * 75)
            print(">>> SECURITY CHECKUP / CHALLENGE DETECTED on 99acres!")
            print(">>> Please solve the checkbox on your screen now (script will pause up to 60s)...")
            print("!" * 75 + "\n")
            for _ in range(30):
                await page.sleep(2)
                curr_url = await page.evaluate("window.location.href")
                page_title = await page.evaluate("document.title")
                page_text = await page.evaluate("document.body ? document.body.innerText.slice(0, 500) : ''")
                if not ("verifycaptcha" in curr_url or "Security checkup" in page_title or 
                        "Just a moment" in page_title or "Access Denied" in page_title or 
                        "verifycaptcha" in page_text or "Security checkup" in page_text):
                    print("✅ Security checkup resolved! Proceeding with crawl...")
                    break
            else:
                print("Timed out waiting for checkup. Aborting.")
                return

        # 2. Wait for page hydration & trigger small scroll to activate lazy filter sidebar
        print("\n[Step 2/5] Waiting for page hydration and waking sidebar filters...")
        await page.sleep(6)
        try:
            await page.scroll_down(250)
            await page.sleep(1.5)
            await page.scroll_up(250)
        except Exception:
            pass
        await page.sleep(2)

        # 3. Reactive Wait & Application of 2 BHK and Residential Apartment filters
        print("\n[Step 3/5] Reactively waiting for filter chips to asynchronously render in DOM...")

        # 3a. Poll and click 2 BHK filter (up to 30s)
        print(" -> Polling for 2 BHK chip (up to 30s)...")
        bhk_active = False
        for poll in range(15):
            raw_bhk = await page.evaluate(r'''
                (() => {
                    try {
                        const candidates = Array.from(document.querySelectorAll('div, span, button, [data-label="BEDROOM_CLUSTER"], [datalabel="BEDROOM_CLUSTER"]'));
                        const el = candidates.find(c => {
                            if (!c.innerText) return false;
                            const txt = c.innerText.trim();
                            return /\b2\s*BHK\b/i.test(txt) && !txt.includes('1 BHK') && txt.length < 20;
                        }) || document.querySelector('[id="2"] > span:last-child') || document.querySelector('[id="2"]');

                        if (!el) return JSON.stringify({ found: false });

                        const chip = el.closest('div[id="2"]') || el.closest('[data-label="BEDROOM_CLUSTER"]') || el;
                        const isActive = chip.classList && chip.classList.contains('tags-and-chips__active');
                        if (isActive) return JSON.stringify({ found: true, active: true });

                        el.click();
                        if (chip !== el) chip.click();
                        return JSON.stringify({ found: true, active: false, clicked: true, text: el.innerText.trim() });
                    } catch (err) {
                        return JSON.stringify({ error: err.toString() });
                    }
                })()
            ''')
            bhk_res = parse_eval_result(raw_bhk)
            if bhk_res.get("active"):
                print(f"    ✅ 2 BHK filter is active (detected after {poll*2}s)!")
                bhk_active = True
                break
            elif bhk_res.get("clicked"):
                print(f"    ✅ Found and clicked 2 BHK filter '{bhk_res.get('text')}' (after {poll*2}s)!")
                await page.sleep(4)
                raw_active = await page.evaluate(r'''
                    (() => {
                        const chip = document.querySelector('[id="2"]') || document.querySelector('#\\32 ') || document.querySelector('[data-label="BEDROOM_CLUSTER"]');
                        return JSON.stringify({ active: chip ? chip.classList.contains('tags-and-chips__active') : false });
                    })()
                ''')
                is_active = parse_eval_result(raw_active).get("active", False)
                print(f"    -> 2 BHK active verified: {is_active}")
                bhk_active = True
                break
            await page.sleep(2.0)

        if not bhk_active:
            print("    ⚠️ Warning: 2 BHK chip was not found within 30s.")

        # 3b. Poll and click Residential Apartment filter (up to 30s)
        print(" -> Polling for Residential Apartment chip (up to 30s)...")
        apt_active = False
        for poll in range(15):
            raw_apt = await page.evaluate(r'''
                (() => {
                    try {
                        const candidates = Array.from(document.querySelectorAll('div, span, button, [data-label="PROPTYPE_CLUSTER"], [datalabel="PROPTYPE_CLUSTER"]'));
                        const el = candidates.find(c => {
                            if (!c.innerText) return false;
                            const txt = c.innerText.trim();
                            return /Residential\s+Apartment/i.test(txt) && txt.length < 35;
                        }) || document.querySelector('[id="1"] > span:last-child') || document.querySelector('[id="1"]');

                        if (!el) return JSON.stringify({ found: false });

                        const chip = el.closest('div[id="1"]') || el.closest('[data-label="PROPTYPE_CLUSTER"]') || el;
                        const isActive = chip.classList && chip.classList.contains('tags-and-chips__active');
                        if (isActive) return JSON.stringify({ found: true, active: true });

                        el.click();
                        if (chip !== el) chip.click();
                        return JSON.stringify({ found: true, active: false, clicked: true, text: el.innerText.trim() });
                    } catch (err) {
                        return JSON.stringify({ error: err.toString() });
                    }
                })()
            ''')
            apt_res = parse_eval_result(raw_apt)
            if apt_res.get("active"):
                print(f"    ✅ Residential Apartment filter is active (detected after {poll*2}s)!")
                apt_active = True
                break
            elif apt_res.get("clicked"):
                print(f"    ✅ Found and clicked Residential Apartment filter '{apt_res.get('text')}' (after {poll*2}s)!")
                await page.sleep(4)
                raw_active = await page.evaluate(r'''
                    (() => {
                        const chip = document.querySelector('[id="1"]') || document.querySelector('#\\31 ') || document.querySelector('[data-label="PROPTYPE_CLUSTER"]');
                        return JSON.stringify({ active: chip ? chip.classList.contains('tags-and-chips__active') : false });
                    })()
                ''')
                is_active = parse_eval_result(raw_active).get("active", False)
                print(f"    -> Residential Apartment active verified: {is_active}")
                apt_active = True
                break
            await page.sleep(2.0)

        if not apt_active:
            print("    ⚠️ Warning: Residential Apartment chip was not found within 30s.")

        # 3c. Settle wait for filtered listings to reload
        print(" -> Waiting 6 seconds for 2-BHK filtered cards to refresh...")
        await page.sleep(6)

        # 4. Patient Scroll-and-Wait Loop
        print(f"\n[Step 4/5] Executing patient scroll-and-wait loop (Target: {target_count} listings)...")
        
        initial_card_count = await page.evaluate("document.querySelectorAll('.tupleNew__outerTupleWrap').length")
        print(f" -> Initial 2-BHK batch rendered: {initial_card_count} listings in DOM.")

        scroll_cycle = 0
        current_card_count = initial_card_count

        while current_card_count < target_count and scroll_cycle < max_scroll_cycles:
            scroll_cycle += 1
            prev_count = current_card_count

            print(f"\n [Scroll Cycle {scroll_cycle}/{max_scroll_cycles}] Scrolling to bottom (Current DOM count: {current_card_count})...")
            # Natural human scroll: JS smooth scroll + native CDP scroll down
            await page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
            try:
                await page.scroll_down(800)
            except Exception:
                pass

            # Patient Wait: poll every 2 seconds for up to 20 seconds for background XHR to populate
            batch_loaded = False
            for poll in range(10):
                await page.sleep(2.0)
                current_card_count = await page.evaluate("document.querySelectorAll('.tupleNew__outerTupleWrap').length")
                if current_card_count > prev_count:
                    print(f"    ✅ New listings loaded! Rendered cards increased: {prev_count} -> {current_card_count}")
                    batch_loaded = True
                    break
                else:
                    print(f"    ... waiting for 99acres server response ({poll * 2 + 2}s elapsed, count: {current_card_count})")

            if not batch_loaded:
                print("    ⚠️ No new cards appended in 20 seconds. Retrying a small scroll up and down...")
                try:
                    await page.scroll_up(500)
                    await page.sleep(2.0)
                    await page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
                    await page.scroll_down(800)
                except Exception:
                    pass
                await page.sleep(4.0)
                current_card_count = await page.evaluate("document.querySelectorAll('.tupleNew__outerTupleWrap').length")
                print(f"    ... post-nudge count: {current_card_count}")

        print(f"\n-> Finished scrolling. Total cards rendered in DOM: {current_card_count}")

        # 5. Extract all property cards from the DOM and state
        print("\n[Step 5/5] Extracting property cards and merging with in-memory state...")
        
        # Pull HTML of all rendered tuples
        all_tuples_html = await page.evaluate('''
            (() => {
                const tuples = Array.from(document.querySelectorAll('.tupleNew__outerTupleWrap'));
                return JSON.stringify(tuples.map(t => t.outerHTML));
            })()
        ''')
        
        # Pull in-memory property JSON array if present
        raw_state_props = await page.evaluate('''
            (() => {
                const props = window.__initialData__?.srp?.pageData?.properties || [];
                return JSON.stringify(props);
            })()
        ''')

        # Index state properties by PROP_ID for rich metadata enrichment (GPS coordinates, etc.)
        state_dict = {}
        try:
            for it in json.loads(raw_state_props):
                if it.get("PROP_ID"):
                    pid_str = str(it["PROP_ID"])
                    state_dict[pid_str] = it
                    state_dict[re.sub(r"^[A-Z]+", "", pid_str)] = it
        except Exception:
            pass

        cards_list = json.loads(all_tuples_html)
        print(f" -> Parsing {len(cards_list)} HTML card elements...")

        ingested_count = 0
        now = datetime.now()

        for html_str in cards_list:
            soup = BeautifulSoup(html_str, "html.parser")
            card = soup.select_one(".tupleNew__outerTupleWrap")
            if not card:
                continue

            p_id = card.get("id", "").strip()
            if not p_id:
                continue

            # Look up state JSON
            numeric_id = re.sub(r"^[A-Z]+", "", p_id)
            item_json = state_dict.get(p_id) or state_dict.get(numeric_id) or {}
            
            # Society Name
            soc_el = card.select_one(".tupleNew__locationName")
            society = soc_el.get_text(strip=True) if soc_el else item_json.get("SOCIETY_NAME", "")

            # Title & Link
            title_el = card.select_one("a.tupleNew__propertyHeading")
            title = title_el.get("title", "") if title_el else (card.select_one("h2.tupleNew__propType").get_text(strip=True) if card.select_one("h2.tupleNew__propType") else "")
            listing_url = title_el.get("href", "") if title_el else item_json.get("PROP_DETAILS_URL", "")
            if listing_url and not listing_url.startswith("http"):
                listing_url = f"https://www.99acres.com/{listing_url}"

            # Locality / Sector
            locality = "Gurgaon"
            m_loc = re.search(r"in\s+([^,]+(?:,\s*[^,]+)?),?\s*Gurgaon", title, re.I)
            if m_loc:
                locality = m_loc.group(1).strip()
            elif item_json.get("LOCALITY_WO_CITY"):
                locality = item_json["LOCALITY_WO_CITY"]

            # Rent
            price_el = card.select_one(".tupleNew__priceValWrap span")
            price_str = price_el.get_text(strip=True) if price_el else "0"
            rent = int(re.sub(r"[^\d]", "", price_str)) if re.sub(r"[^\d]", "", price_str) else 0

            # Deposit
            dep_el = card.select_one(".tupleNew__perSqftWrap")
            deposit = 0
            if dep_el and "deposit" in dep_el.get_text().lower():
                m_dep = re.search(r"₹?([\d,]+)", dep_el.get_text())
                if m_dep:
                    deposit = int(m_dep.group(1).replace(",", ""))
            if not deposit:
                deposit = item_json.get("DEPOSIT", 0)

            # Area
            area_el = card.select_one(".tupleNew__totolAreaWrap .tupleNew__area1Type")
            area_str = area_el.get_text(strip=True) if area_el else "0"
            built_up_sqft = int(re.sub(r"[^\d]", "", area_str)) if re.sub(r"[^\d]", "", area_str) else 0

            # Bathrooms
            m_bath = re.search(r"(\d+)\s*Baths?", card.get_text(), re.I)
            bathrooms = int(m_bath.group(1)) if m_bath else item_json.get("BATHROOM_NUM", 2)

            # Photos count
            photos_el = card.select_one(".tupleNew__tupleCOuntTopaz, .tupleNew__imgCount")
            photos_count = int(re.sub(r"[^\d]", "", photos_el.get_text(strip=True))) if (photos_el and re.sub(r"[^\d]", "", photos_el.get_text(strip=True))) else 0

            # Description
            desc_el = card.select_one('[data-label="DESCRIPTION"]')
            desc = desc_el.get_text(strip=True) if desc_el else ""

            # Floor & Total Floors from description or state
            floor = item_json.get("FLOOR_NUM")
            total_floors = item_json.get("TOTAL_FLOOR")
            if floor is None or total_floors is None:
                m_fl = re.search(r"(?:located\s+)?on\s+(\d+|ground)(?:st|nd|rd|th)?(?:\s+floor)?\s+of\s+(\d+)\s+floor", desc, re.I)
                if m_fl:
                    try:
                        floor = 0 if m_fl.group(1).lower() == "ground" else int(m_fl.group(1))
                        total_floors = int(m_fl.group(2))
                    except Exception:
                        pass

            # Furnishing
            furnishing = "Semi-Furnished"
            furn_code = str(item_json.get("FURNISH") or "")
            if furn_code == "1" or "fully furnished" in desc.lower():
                furnishing = "Fully Furnished"
            elif furn_code == "2" or "unfurnished" in desc.lower():
                furnishing = "Unfurnished"
            elif furn_code == "4" or "semi-furnished" in desc.lower():
                furnishing = "Semi-Furnished"

            # Coordinates
            map_details = item_json.get("MAP_DETAILS") or {}
            lat = map_details.get("LATITUDE")
            lon = map_details.get("LONGITUDE")

            # Seller name
            seller_el = card.select_one(".tupleNew__pbL2")
            seller_name = seller_el.get_text(strip=True) if seller_el else item_json.get("CONTACT_NAME")

            # Relative Posted Date
            posted_date = item_json.get("POSTING_DATE")
            if not posted_date:
                posted_el = card.select_one(".tupleNew__pbL1")
                posted_str = posted_el.get_text(strip=True) if posted_el else ""
                m_time = re.search(r"(\d+)\s*([dwmy])\s*ago", posted_str)
                if m_time:
                    qty, unit = int(m_time.group(1)), m_time.group(2)
                    if unit == "d": delta = timedelta(days=qty)
                    elif unit == "w": delta = timedelta(weeks=qty)
                    elif unit == "m": delta = timedelta(days=qty*30)
                    elif unit == "y": delta = timedelta(days=qty*365)
                    posted_date = (now - delta).strftime("%Y-%m-%d")
                elif "today" in posted_str.lower():
                    posted_date = now.strftime("%Y-%m-%d")
                elif "yesterday" in posted_str.lower():
                    posted_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

            # Dynamically parse BHK from title
            bhk_val = 2
            m_bhk = re.search(r"(\d+(?:\.\d+)?)\s*BHK", title, re.I)
            if m_bhk:
                try:
                    bhk_val = int(float(m_bhk.group(1)))
                except Exception:
                    pass
            elif "1 RK" in title:
                bhk_val = 1
            bhk_desc = f"{bhk_val} BHK"

            parsed_record = {
                "portal_id": p_id,
                "title": title or "2 BHK Flat for rent in Gurgaon",
                "society": society,
                "locality": locality,
                "bhk": bhk_val,
                "bhk_desc": bhk_desc,
                "rent": rent,
                "deposit": deposit,
                "maintenance_amount": item_json.get("MAINTENANCE", 0),
                "built_up_sqft": built_up_sqft,
                "carpet_sqft": item_json.get("CARPET_SQFT"),
                "furnishing": furnishing,
                "bathrooms": bathrooms,
                "balconies": item_json.get("BALCONY_NUM"),
                "floor": floor,
                "total_floors": total_floors,
                "facing": item_json.get("FACING"),
                "latitude": lat,
                "longitude": lon,
                "seller_name": seller_name,
                "listing_url": listing_url,
                "posted_date": posted_date,
                "last_updated_date": item_json.get("UPDATE_DATE"),
                "photos_count": photos_count,
                "raw_json": json.dumps({"card_id": p_id, "desc": desc, "state": item_json})
            }

            upsert_raw_ninety_nine_acres(conn, parsed_record)
            ingested_count += 1

        conn.commit()
        print(f"\n✅ Successfully extracted and persisted {ingested_count} 2-BHK listings to raw_99acres_listings!")

    finally:
        try:
            browser.stop()
        except Exception:
            pass
        conn.close()

    # 6. Master Consolidation & Physical Deduplication
    print("\n--- Running Master Consolidation & Physical Deduplication ---")
    consolidator = Consolidator()
    res = consolidator.run()
    print(f"-> Consolidation complete: {res['unique_physical_units']} unique physical units across Gurgaon.\n")

    # 7. Recompile Web Dashboard & Standalone HTML
    print("--- Recompiling Web Dashboard & Standalone HTML ---")
    export_data_json()
    export_standalone_html()
    print("-> Dashboard datasets recompiled successfully!\n")

    # 8. Print High-Rise Summary
    print("--- HIGH-RISE MARKET INTELLIGENCE REPORT ---")
    analyzer = MarketAnalyzer()
    analyzer.print_analysis(
        source="consolidated",
        min_floors=6,
        max_age_days=30,
        min_photos=2
    )

if __name__ == "__main__":
    asyncio.run(run_dynamic_2bhk_extraction(target_count=120, max_scroll_cycles=15))
