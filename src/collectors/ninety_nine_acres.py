import asyncio
import json
import os
import re
import time
import random
from typing import Dict, Any, Optional, List
import nodriver as uc

from ..db import get_connection, upsert_raw_ninety_nine_acres
from ..normalizers.filters import normalize_furnishing

class NinetyNineAcresCollector:
    """
    Automated browser-assisted SSR extractor for 99acres.com rentals.
    Bypasses Radware / ShieldSquare bot protection by warming up on the homepage
    and retrieving pre-rendered data from window.__initialData__.srp.pageData.properties.
    """
    CITYWIDE_URL = "https://www.99acres.com/flats-for-rent-in-gurgaon-ffid"
    CITYWIDE_2BHK_URL = "https://www.99acres.com/2-bhk-flats-for-rent-in-gurgaon-ffid"

    def __init__(
        self,
        target_url: Optional[str] = None,
        sector_name: Optional[str] = None,
        bhk: Optional[int] = None,
        headless: bool = False
    ):
        self.bhk = bhk
        self.headless = headless

        if target_url:
            self.target_url = target_url
        elif sector_name:
            slug = self.to_sector_slug(sector_name)
            if bhk:
                self.target_url = f"https://www.99acres.com/{bhk}-bhk-flats-for-rent-in-{slug}-gurgaon-ffid"
            else:
                self.target_url = f"https://www.99acres.com/flats-for-rent-in-{slug}-gurgaon-ffid"
        else:
            if bhk:
                self.target_url = f"https://www.99acres.com/{bhk}-bhk-flats-for-rent-in-gurgaon-ffid"
            else:
                self.target_url = self.CITYWIDE_URL

    @staticmethod
    def to_sector_slug(sector_name: str) -> str:
        """Converts sector names like 'Sector 45', 'DLF Phase 4' to 99acres URL slugs."""
        s = sector_name.strip().lower()
        s = re.sub(r"[^\w\s-]", "", s)
        s = re.sub(r"[\s_]+", "-", s)
        return s

    def _build_page_url(self, page_num: int) -> str:
        """Generates paginated 99acres SRP URLs."""
        if page_num <= 1:
            return self.target_url
        # If url already has -page-N, replace it
        if "-page-" in self.target_url:
            return re.sub(r"-page-\d+", f"-page-{page_num}", self.target_url)
        # Otherwise append -page-{page_num} before or after -ffid
        if "-ffid" in self.target_url:
            return self.target_url.replace("-ffid", f"-ffid-page-{page_num}")
        return f"{self.target_url}-page-{page_num}"

    def parse_listing(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Maps a 99acres property tuple into the canonical schema."""
        p_id = str(item.get("PROP_ID") or "")
        title = item.get("PROP_HEADING") or ""
        society = item.get("SOCIETY_NAME") or item.get("BUILDING_NAME") or item.get("PROP_NAME") or ""
        locality = item.get("LOCALITY_WO_CITY") or item.get("LOCALITY") or "Gurgaon"

        # BHK
        bhk = None
        b_raw = item.get("BEDROOM_NUM")
        if b_raw is not None:
            try:
                bhk = int(float(b_raw))
            except Exception:
                pass
        if bhk is None and title:
            m = re.search(r"(\d+(?:\.\d+)?)\s*BHK", title, re.IGNORECASE)
            if m:
                try:
                    bhk = int(float(m.group(1)))
                except Exception:
                    pass

        # Rent parsing
        price_raw = str(item.get("PRICE") or item.get("FORMATTED_PRICE") or item.get("MIN_PRICE") or "0")
        price_clean = re.sub(r"[^\d.]", "", price_raw)
        try:
            rent = int(float(price_clean))
        except Exception:
            rent = 0

        # Area parsing
        area_val = 0
        sb_sqft = item.get("SUPERBUILTUP_SQFT")
        if sb_sqft:
            try:
                area_val = int(float(sb_sqft))
            except Exception:
                pass
        if not area_val:
            area_raw = str(item.get("AREA") or item.get("LOCALIZED_AREA") or "")
            m_area = re.search(r"(\d+(?:,\d+)?(?:\.\d+)?)\s*(?:sqft|sq\.?\s*ft)", area_raw, re.IGNORECASE)
            if m_area:
                try:
                    area_val = int(float(m_area.group(1).replace(",", "")))
                except Exception:
                    pass

        # Carpet area
        carpet_val = None
        c_raw = item.get("LOCALIZED_CARPET_AREA")
        if c_raw:
            try:
                carpet_val = int(float(re.sub(r"[^\d.]", "", str(c_raw))))
            except Exception:
                pass

        # Deposit
        deposit = 0
        d_raw = str(item.get("DEPOSIT") or item.get("BOOKING_AMOUNT") or "")
        if d_raw:
            d_clean = re.sub(r"[^\d.]", "", d_raw)
            try:
                deposit = int(float(d_clean))
            except Exception:
                pass

        # Maintenance
        maint = 0
        m_raw = str(item.get("MAINTENANCE") or "")
        if m_raw:
            m_clean = re.sub(r"[^\d.]", "", m_raw)
            try:
                maint = int(float(m_clean))
            except Exception:
                pass

        # Furnishing
        furn_code = str(item.get("FURNISH") or "")
        furnishing = "Semi-Furnished"
        if furn_code == "1":
            furnishing = "Fully Furnished"
        elif furn_code == "2":
            furnishing = "Unfurnished"
        elif furn_code == "4":
            furnishing = "Semi-Furnished"
        else:
            tags = item.get("tags") or []
            tag_labels = [t.get("label", "").upper() for t in tags if isinstance(t, dict)]
            if "FURNISHED" in tag_labels or "FULLY FURNISHED" in tag_labels:
                furnishing = "Fully Furnished"
            elif "UNFURNISHED" in tag_labels:
                furnishing = "Unfurnished"

        # Bathrooms & Balconies
        bathrooms = None
        if item.get("BATHROOM_NUM") is not None:
            try:
                bathrooms = int(item["BATHROOM_NUM"])
            except Exception:
                pass

        balconies = None
        if item.get("BALCONY_NUM") is not None:
            try:
                balconies = int(item["BALCONY_NUM"])
            except Exception:
                pass

        # Floor & Total Floors
        floor = None
        if item.get("FLOOR_NUM") is not None:
            try:
                floor = int(item["FLOOR_NUM"])
            except Exception:
                pass

        total_floors = None
        if item.get("TOTAL_FLOOR") is not None:
            try:
                total_floors = int(item["TOTAL_FLOOR"])
            except Exception:
                pass

        # Coordinates
        map_details = item.get("MAP_DETAILS") or {}
        lat = None
        lon = None
        if isinstance(map_details, dict):
            lat_raw = map_details.get("LATITUDE")
            lon_raw = map_details.get("LONGITUDE")
            if lat_raw and lon_raw:
                try:
                    lat = float(lat_raw)
                    lon = float(lon_raw)
                except Exception:
                    pass

        # Listing URL
        pd_url = item.get("PROP_DETAILS_URL") or item.get("PD_URL") or ""
        if pd_url:
            listing_url = pd_url if pd_url.startswith("http") else f"https://www.99acres.com/{pd_url}"
        else:
            listing_url = self.target_url

        # Dates
        posted_date = None
        p_epoch = item.get("POSTING_DATE__U")
        if p_epoch:
            try:
                posted_date = time.strftime("%Y-%m-%d", time.localtime(p_epoch / 1000))
            except Exception:
                pass

        last_updated_date = None
        u_epoch = item.get("UPDATE_DATE__U")
        if u_epoch:
            try:
                last_updated_date = time.strftime("%Y-%m-%d", time.localtime(u_epoch / 1000))
            except Exception:
                pass

        # Photos count
        images = item.get("PROPERTY_IMAGES") or item.get("THUMBNAIL_IMAGES") or []
        photos_count = len(images)

        seller_name = item.get("CONTACT_NAME") or item.get("CONTACT_COMPANY_NAME")

        return {
            "portal_id": p_id,
            "title": title,
            "society": society,
            "locality": locality,
            "bhk": bhk,
            "bhk_desc": f"{bhk} BHK" if bhk else None,
            "rent": rent,
            "deposit": deposit,
            "maintenance_amount": maint,
            "built_up_sqft": area_val,
            "carpet_sqft": carpet_val,
            "furnishing": furnishing,
            "bathrooms": bathrooms,
            "balconies": balconies,
            "floor": floor,
            "total_floors": total_floors,
            "facing": item.get("FACING"),
            "latitude": lat,
            "longitude": lon,
            "seller_name": seller_name,
            "listing_url": listing_url,
            "posted_date": posted_date,
            "last_updated_date": last_updated_date,
            "photos_count": photos_count,
            "raw_json": json.dumps(item)
        }

    async def scrape_async(
        self,
        pages: int = 2,
        start_page: int = 1,
        delay_per_page: float = 4.0,
        db_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes browser warmup and extracts property pages via nodriver.
        Enforces a hard ceiling of max 2 pages per feed and strict circuit-breaker protection.
        """
        # Hard Ceiling: Never exceed 2 pages per search URL to stay below bot threshold
        pages = min(pages, 2)

        config = uc.Config()
        config.headless = self.headless
        browser = await uc.start(config)

        conn = get_connection(db_path)
        total_processed = 0
        pages_completed = 0
        circuit_broken = False

        try:
            # 1. Warm-up navigation on homepage with human-like pause
            print("[99acres] Warming up session on homepage...")
            warmup_page = await browser.get("https://www.99acres.com/")
            await warmup_page.sleep(random.uniform(3.0, 4.5))

            # 2. Iterate requested pages (Max 2 pages)
            for page_num in range(start_page, start_page + pages):
                if circuit_broken:
                    break

                page_url = self._build_page_url(page_num)
                print(f"[99acres] Fetching Page {page_num}: {page_url}")
                page = None
                try:
                    page = await browser.get(page_url)
                    
                    # Human jitter: initial load wait
                    await page.sleep(random.uniform(2.5, 3.8))

                    # Simulate natural human scroll behavior
                    try:
                        await page.scroll_down(random.randint(180, 320))
                    except Exception:
                        pass

                    # Additional jitter for dynamic SSR hydration
                    await page.sleep(random.uniform(2.0, 3.5))

                    # Circuit Breaker: check for bot challenge or redirect
                    curr_url = await page.evaluate("window.location.href")
                    title = await page.evaluate("document.title")
                    if "verifycaptcha" in curr_url or "Access Denied" in title or "Robot" in title:
                        print(f"[99acres] [CIRCUIT BREAKER] Bot challenge detected at {curr_url} (Title: {title}). Aborting session immediately to protect IP!")
                        circuit_broken = True
                        break

                    # Retrieve properties from window.__initialData__.srp.pageData.properties
                    prop_list = []
                    for attempt in range(6):
                        raw_props = await page.evaluate(
                            "JSON.stringify(window.__initialData__?.srp?.pageData?.properties || [])"
                        )
                        if raw_props:
                            try:
                                prop_list = json.loads(raw_props)
                                if prop_list:
                                    break
                            except Exception:
                                pass
                        await page.sleep(1.5)

                    if not prop_list:
                        print(f"[99acres] Page {page_num}: No properties found in __initialData__.")
                        continue

                    print(f"[99acres] Page {page_num}: Received {len(prop_list)} listings.")

                    for item in prop_list:
                        if not isinstance(item, dict) or not item.get("PROP_ID"):
                            continue
                        parsed = self.parse_listing(item)
                        upsert_raw_ninety_nine_acres(conn, parsed)
                        total_processed += 1

                    pages_completed += 1
                    conn.commit()
                except Exception as e:
                    print(f"[99acres] Error on Page {page_num}: {e}")
                finally:
                    if page:
                        try:
                            await page.close()
                        except Exception:
                            pass

        finally:
            try:
                browser.stop()
            except Exception:
                pass
            conn.close()

        print(f"[99acres] Extraction complete: {total_processed} listings ingested across {pages_completed} pages.")
        return {
            "total_processed": total_processed,
            "pages_completed": pages_completed,
            "circuit_broken": circuit_broken
        }

    def scrape(
        self,
        pages: int = 2,
        start_page: int = 1,
        delay_per_page: float = 4.0,
        db_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Synchronous wrapper for scrape_async."""
        return asyncio.run(
            self.scrape_async(
                pages=pages,
                start_page=start_page,
                delay_per_page=delay_per_page,
                db_path=db_path
            )
        )

    async def scrape_targets_async(
        self,
        targets: List[Dict[str, Any]],
        delay_per_page: float = 3.5,
        db_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Scrapes multiple target SRPs across Gurgaon in a single browser session.
        Applies strict max-2-page ceiling per target, human scroll simulation, randomized jitter,
        inter-target cooldowns, and instant circuit-breaker abort.
        """
        config = uc.Config()
        config.headless = self.headless
        browser = await uc.start(config)

        conn = get_connection(db_path)
        total_processed = 0
        pages_completed = 0
        circuit_broken = False

        try:
            print("[99acres] Warming up session on homepage...")
            warmup_page = await browser.get("https://www.99acres.com/")
            await warmup_page.sleep(random.uniform(3.0, 4.5))

            for t_idx, target in enumerate(targets):
                if circuit_broken:
                    break

                # Inter-target cooldown (pause between different sector searches)
                if t_idx > 0:
                    cooldown = random.uniform(4.0, 7.0)
                    print(f"[99acres] Inter-target pause ({cooldown:.1f}s) to maintain natural browsing pattern...")
                    await asyncio.sleep(cooldown)

                base_url = target["url"]
                # Hard ceiling: max 2 pages per target feed
                target_pages = min(target.get("pages", 2), 2)
                start_p = target.get("start_page", 1)
                name = target.get("name", base_url)
                print(f"\n[99acres] >>> Target [{t_idx + 1}/{len(targets)}]: {name} (Max {target_pages} pages) -> {base_url}")

                for page_num in range(start_p, start_p + target_pages):
                    if circuit_broken:
                        break

                    if page_num <= 1:
                        page_url = base_url
                    elif "-page-" in base_url:
                        page_url = re.sub(r"-page-\d+", f"-page-{page_num}", base_url)
                    elif "-ffid" in base_url:
                        page_url = base_url.replace("-ffid", f"-ffid-page-{page_num}")
                    else:
                        page_url = f"{base_url}-page-{page_num}"

                    print(f"[99acres] [{name}] Fetching Page {page_num}: {page_url}")
                    page = None
                    try:
                        page = await browser.get(page_url)
                        
                        # Human jitter: initial load wait
                        await page.sleep(random.uniform(2.5, 3.8))

                        # Simulate natural human scroll behavior
                        try:
                            await page.scroll_down(random.randint(180, 320))
                        except Exception:
                            pass

                        # Additional jitter for dynamic SSR hydration
                        await page.sleep(random.uniform(2.0, 3.5))

                        # Circuit Breaker: check for bot challenge or redirect
                        curr_url = await page.evaluate("window.location.href")
                        title = await page.evaluate("document.title")
                        if "verifycaptcha" in curr_url or "Access Denied" in title or "Robot" in title:
                            print(f"[99acres] [CIRCUIT BREAKER] Challenge detected at {curr_url} (Title: {title}). Aborting entire run immediately to protect IP!")
                            circuit_broken = True
                            break

                        prop_list = []
                        for attempt in range(6):
                            raw_props = await page.evaluate(
                                "JSON.stringify(window.__initialData__?.srp?.pageData?.properties || [])"
                            )
                            if raw_props:
                                try:
                                    prop_list = json.loads(raw_props)
                                    if prop_list:
                                        break
                                except Exception:
                                    pass
                            await page.sleep(1.5)

                        if not prop_list:
                            print(f"[99acres] [{name}] Page {page_num}: No properties found in __initialData__.")
                            continue

                        print(f"[99acres] [{name}] Page {page_num}: Received {len(prop_list)} listings.")

                        for item in prop_list:
                            if not isinstance(item, dict) or not item.get("PROP_ID"):
                                continue
                            parsed = self.parse_listing(item)
                            upsert_raw_ninety_nine_acres(conn, parsed)
                            total_processed += 1

                        pages_completed += 1
                        conn.commit()
                    except Exception as e:
                        print(f"[99acres] [{name}] Error on Page {page_num}: {e}")
                    finally:
                        if page:
                            try:
                                await page.close()
                            except Exception:
                                pass

        finally:
            try:
                browser.stop()
            except Exception:
                pass
            conn.close()

        print(f"\n[99acres] Multi-target extraction complete: {total_processed} listings ingested across {pages_completed} pages.")
        return {
            "total_processed": total_processed,
            "pages_completed": pages_completed,
            "circuit_broken": circuit_broken
        }

    def scrape_targets(
        self,
        targets: List[Dict[str, Any]],
        delay_per_page: float = 3.5,
        db_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Synchronous wrapper for scrape_targets_async."""
        return asyncio.run(
            self.scrape_targets_async(
                targets=targets,
                delay_per_page=delay_per_page,
                db_path=db_path
            )
        )
