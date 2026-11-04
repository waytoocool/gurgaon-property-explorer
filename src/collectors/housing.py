import json
import asyncio
import os
import re
from typing import Optional, Dict, Any, List
import nodriver as uc

from ..db import get_connection, upsert_raw_housing
from ..normalizers.filters import normalize_furnishing

class HousingCollector:
    """
    Automated Multi-Page Collector for Housing.com using nodriver.
    Bypasses Akamai Bot Manager via browser warmup and extracts SSR window.__INITIAL_STATE__.
    """
    DEFAULT_URL = "https://housing.com/rent/flats-for-rent-in-dlf-phase-4-gurgaon-P3a7ctrhlqp0cuwe2"
    CITYWIDE_URL = "https://housing.com/rent/flats-for-rent-in-gurgaon-haryana-P1od1w26jrfqap1jl"
    CITYWIDE_2BHK_URL = "https://housing.com/rent/2bhk-flats-for-rent-in-gurgaon-haryana-C4P1od1w26jrfqap1jl"
    CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    PROFILE_DIR = "/tmp/housing_uc_profile"
    CATALOG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "config", "housing_sectors.json"))

    @staticmethod
    def _normalize_sector_name(name: str) -> str:
        s = name.lower().strip()
        s = re.sub(r"[_/-]", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s

    @classmethod
    def _get_key_variants(cls, name: str) -> List[str]:
        norm = cls._normalize_sector_name(name)
        variants = [norm]
        clean = re.sub(r"[^a-z0-9]", "", norm)
        if clean:
            variants.append(clean)
        
        # If digits only, e.g. "57" -> "sector 57", "sector57"
        if norm.isdigit():
            variants.append(f"sector {norm}")
            variants.append(f"sector{norm}")
        
        # "sec 57" -> "sector 57"
        if norm.startswith("sec "):
            variants.append(norm.replace("sec ", "sector "))
            variants.append(norm.replace("sec ", "sector"))
            
        # "dlf phase 4" -> "dlf 4", "dlf4"
        if "phase" in norm:
            variants.append(norm.replace("phase", "").strip())
            variants.append(re.sub(r"[^a-z0-9]", "", norm.replace("phase", "")))
            
        # "ext" -> "extension"
        if "ext" in norm and "extension" not in norm:
            variants.append(norm.replace("ext", "extension"))
            variants.append(re.sub(r"[^a-z0-9]", "", norm.replace("ext", "extension")))

        return list(dict.fromkeys(variants))

    @classmethod
    def load_catalog(cls) -> Dict[str, Any]:
        """Loads the sector-to-polygon-hash catalog from housing_sectors.json."""
        if not os.path.exists(cls.CATALOG_PATH):
            return {}
        try:
            with open(cls.CATALOG_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as e:
            print(f"[Housing] Warning: Failed to load housing sectors catalog: {e}")
            return {}

    @classmethod
    def save_catalog(cls, catalog: Dict[str, Any]) -> None:
        """Persists the sector-to-polygon-hash catalog to housing_sectors.json."""
        os.makedirs(os.path.dirname(cls.CATALOG_PATH), exist_ok=True)
        try:
            with open(cls.CATALOG_PATH, "w", encoding="utf-8") as f:
                json.dump(catalog, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Housing] Warning: Failed to save housing sectors catalog: {e}")

    @classmethod
    def get_sector_url(cls, sector_name: str) -> Optional[str]:
        """
        Resolves canonical Housing.com search URL with P<hash> for any sector name or alias.
        Supports 'Sector 57', 'sector_57', '57', 'dlf_phase_4', 'Sushant Lok 1', etc.
        """
        if not sector_name:
            return None
        catalog = cls.load_catalog()
        if not catalog:
            return None

        # 1. Exact catalog key match
        if sector_name in catalog:
            return catalog[sector_name].get("url")

        # 2. Normalized alias match
        variants = cls._get_key_variants(sector_name)
        for cat_name, cat_entry in catalog.items():
            cat_variants = cls._get_key_variants(cat_name)
            if any(v in cat_variants for v in variants):
                return cat_entry.get("url")

        return None

    @classmethod
    def update_catalog_with_links(cls, links_dict: Dict[str, str]) -> int:
        """
        Self-learning expansion: extracts sector polygon hashes from newly discovered DOM links
        and merges them into config/housing_sectors.json.
        """
        if not links_dict:
            return 0
        catalog = cls.load_catalog()
        added_count = 0

        for raw_text, href in links_dict.items():
            m = re.search(r"/rent/flats-for-rent-in-(.*?)-(P[a-z0-9]+)(?:\?|$)", href)
            if not m:
                continue
            slug, p_hash = m.group(1), m.group(2)

            # Derive clean sector name
            clean_name = raw_text.strip()
            clean_name = re.sub(r"(?i)^flats\s+for\s+rent\s+in\s+", "", clean_name)
            clean_name = re.sub(r"(?i)\s+gurgaon.*$", "", clean_name)
            clean_name = re.sub(r"(?i)\s+gurugram.*$", "", clean_name)

            if not clean_name or len(clean_name) > 50:
                s = slug.replace("-gurgaon", "").replace("-gurugram", "").replace("-haryana", "")
                parts = s.split("-")
                clean_name = " ".join([p.upper() if p.lower() in ["dlf", "imt"] else p.capitalize() for p in parts])

            full_url = f"https://housing.com/rent/flats-for-rent-in-{slug}-{p_hash}"

            # Only add if not present
            if clean_name not in catalog:
                catalog[clean_name] = {
                    "name": clean_name,
                    "hash": p_hash,
                    "url": full_url
                }
                added_count += 1

        if added_count > 0:
            cls.save_catalog(catalog)
            print(f"[Housing] Self-learning catalog: Added {added_count} new sector(s) to housing_sectors.json.")

        return added_count

    @staticmethod
    def to_bhk_url(url: str, bhk: Optional[int] = None) -> str:
        """
        Converts standard Housing.com rent URL to dynamic BHK-specific URL.
        Housing requires both:
          1. Path prefix: /{bhk}bhk-flats-for-rent-in-
          2. Category configuration code in polygon hash: -C{code}P (C1=1RK, C2=1BHK, C4=2BHK, C8=3BHK)
        Without the -C{code}P code, Housing issues a 301 redirect stripping the BHK filter.
        """
        if not bhk:
            return url
            
        # 1. Update path prefix
        if re.search(r"/\d+bhk-flats-for-rent-in-", url):
            url = re.sub(r"/\d+bhk-flats-for-rent-in-", f"/{bhk}bhk-flats-for-rent-in-", url)
        elif "/flats-for-rent-in-" in url:
            url = url.replace("/flats-for-rent-in-", f"/{bhk}bhk-flats-for-rent-in-")

        # 2. Update category code (C2=1BHK, C4=2BHK, C8=3BHK)
        c_map = {1: 2, 2: 4, 3: 8}
        if bhk in c_map:
            c_code = c_map[bhk]
            if re.search(r"-C\d+P", url):
                url = re.sub(r"-C\d+P", f"-C{c_code}P", url)
            elif "-P" in url:
                url = url.replace("-P", f"-C{c_code}P")

        return url

    def __init__(
        self,
        target_url: str = DEFAULT_URL,
        browser_executable_path: str = CHROME_PATH,
        user_data_dir: str = PROFILE_DIR,
        headless: bool = False,
        bhk: Optional[int] = None
    ):
        if bhk:
            self.target_url = self.to_bhk_url(target_url, bhk=bhk)
        else:
            self.target_url = target_url
        self.browser_executable_path = browser_executable_path
        self.user_data_dir = user_data_dir
        self.headless = headless

    def _build_page_url(self, page_no: int) -> str:
        if page_no <= 1:
            return self.target_url
        separator = "&" if "?" in self.target_url else "?"
        return f"{self.target_url}{separator}page={page_no}"

    def parse_listing(self, p_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        """Maps Housing.com SSR entity into standardized DB schema."""
        title = item.get("title")
        dp = item.get("displayPrice", {})
        rent = item.get("price", 0)
        
        deposit = 0
        if dp and dp.get("deposit"):
            try:
                deposit = int(str(dp.get("deposit")).replace(",", "").strip())
            except Exception:
                deposit = 0

        maint = dp.get("maintenance", 0) if dp else 0
        brokerage = dp.get("brokerage", 0) if dp else 0

        # Areas
        built_sqft = 0
        if item.get("builtUpArea") and isinstance(item.get("builtUpArea"), dict):
            built_sqft = item.get("builtUpArea").get("value", 0)

        f_config = {
            f.get("label"): f.get("description")
            for f in item.get("featureAndConfig", [])
            if isinstance(f, dict)
        }

        carpet_sqft = 0
        if "Carpet area" in f_config:
            try:
                carpet_sqft = int(f_config["Carpet area"].replace("sq.ft", "").replace(",", "").strip())
            except Exception:
                carpet_sqft = 0

        if not built_sqft and "Build Up" in f_config:
            try:
                built_sqft = int(f_config["Build Up"].replace("sq.ft", "").replace(",", "").strip())
            except Exception:
                built_sqft = 0

        if not built_sqft:
            built_sqft = carpet_sqft

        furnishing = normalize_furnishing(f_config.get("Furnishing"))

        bathrooms = None
        if "Bathrooms" in f_config:
            try:
                bathrooms = int(f_config["Bathrooms"])
            except Exception:
                bathrooms = None

        balconies = None
        if "Balcony" in f_config:
            try:
                balconies = int(f_config["Balcony"])
            except Exception:
                balconies = None

        bhk = None
        if title:
            m = re.search(r"(\d+(?:\.\d+)?)\s*BHK", title, re.IGNORECASE)
            if m:
                try:
                    bhk = int(float(m.group(1)))
                except Exception:
                    bhk = None
            elif "1 RK" in title:
                bhk = 1

        coords = item.get("coords") or [None, None]
        lat = float(coords[0]) if coords[0] else None
        lon = float(coords[1]) if coords[1] else None

        society = item.get("entityProjectName") or item.get("project")
        if not society:
            society = "Independent Builder Floor / House"

        locality = item.get("address", {}).get("address", "DLF Phase 4, Gurgaon")
        is_broker = item.get("isBrokerageChargeable", True)
        sellers = item.get("sellers", [])
        seller_name = sellers[0].get("name") if sellers else None
        url_suffix = item.get("url", "")
        listing_url = f"https://housing.com{url_suffix}" if url_suffix else "https://housing.com"

        posted_date = item.get("postedDate", "")[:10] if item.get("postedDate") else None
        last_updated_date = item.get("updatedAt", "")[:10] if item.get("updatedAt") else None

        return {
            "portal_id": str(p_id),
            "title": title,
            "society": society,
            "locality": locality,
            "bhk": bhk,
            "bhk_desc": f"{bhk} BHK" if bhk else None,
            "rent": rent,
            "deposit": deposit,
            "maintenance_amount": maint,
            "brokerage_amount": brokerage,
            "built_up_sqft": built_sqft,
            "carpet_sqft": carpet_sqft,
            "furnishing": furnishing,
            "bathrooms": bathrooms,
            "balconies": balconies,
            "floor_desc": f_config.get("Floor number"),
            "facing": f_config.get("Main entrance facing"),
            "latitude": lat,
            "longitude": lon,
            "is_broker": is_broker,
            "seller_name": seller_name,
            "listing_url": listing_url,
            "posted_date": posted_date,
            "last_updated_date": last_updated_date,
            "photos_count": item.get("imageCount", 0) or 0,
            "raw_json": json.dumps(item)
        }

    async def scrape_async(
        self,
        pages: int = 2,
        start_page: int = 1,
        delay_per_page: float = 5.0,
        db_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Runs headless nodriver session and stages data directly to SQLite."""
        os.makedirs(self.user_data_dir, exist_ok=True)
        # Clean up any stale Chrome singleton lock symlinks from previous killed runs
        for lock_name in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
            lock_path = os.path.join(self.user_data_dir, lock_name)
            if os.path.islink(lock_path) or os.path.exists(lock_path):
                try:
                    os.unlink(lock_path)
                except Exception:
                    pass

        print(f"[Housing] Starting browser for {self.target_url} ({pages} pages, starting at page {start_page})...")
        
        config = uc.Config()
        config.browser_executable_path = self.browser_executable_path
        config.user_data_dir = self.user_data_dir
        config.headless = self.headless
        config.sandbox = False
        browser = await uc.start(config)
        
        conn = get_connection(db_path)
        total_processed = 0
        pages_completed = 0

        try:
            # 1. Warm-up navigation on homepage
            print("[Housing] Performing warmup navigation on homepage...")
            warmup_page = await browser.get("https://housing.com/")
            await warmup_page.sleep(3)
            await warmup_page.scroll_down(250)
            await warmup_page.sleep(2)

            # 2. Iterate pages
            for page_num in range(start_page, start_page + pages):
                page_url = self._build_page_url(page_num)
                print(f"[Housing] Fetching Page {page_num}: {page_url}")
                page = await browser.get(page_url)
                await page.sleep(delay_per_page)

                # Wait for window.__INITIAL_STATE__.searchResults.data to populate
                sr_data = {}
                for attempt in range(6):
                    raw_state = await page.evaluate("JSON.stringify(window.__INITIAL_STATE__ || null)")
                    if raw_state:
                        try:
                            state = json.loads(raw_state)
                            sr_data = state.get("searchResults", {}).get("data", {})
                            if sr_data:
                                break
                        except Exception:
                            pass
                    await page.sleep(1.5)

                if not sr_data:
                    print(f"[Housing] Page {page_num}: No search results found in window.__INITIAL_STATE__.")
                    continue

                print(f"[Housing] Page {page_num}: Received {len(sr_data)} raw entities.")

                # Self-learning catalog harvest: extract any linked Gurgaon sectors on page
                try:
                    raw_links = await page.evaluate("""
                        (() => {
                            const links = Array.from(document.querySelectorAll('a[href*="/rent/flats-for-rent-in-"]'));
                            const map = {};
                            for (const a of links) {
                                const href = a.getAttribute('href') || '';
                                const text = (a.innerText || '').trim();
                                if (href.includes('-P') && (href.includes('gurgaon') || href.includes('gurugram'))) {
                                    map[text] = href;
                                }
                            }
                            return JSON.stringify(map);
                        })()
                    """)
                    if raw_links:
                        links_map = json.loads(raw_links)
                        if links_map:
                            self.update_catalog_with_links(links_map)
                except Exception:
                    pass

                for p_id, item in sr_data.items():
                    parsed = self.parse_listing(p_id, item)
                    upsert_raw_housing(conn, parsed)
                    total_processed += 1

                conn.commit()
                pages_completed += 1

        finally:
            try:
                if hasattr(browser, "aclose"):
                    await browser.aclose()
            except Exception:
                pass
            try:
                browser.stop()
            except Exception:
                pass
            conn.close()

        return {
            "portal": "Housing",
            "pages_completed": pages_completed,
            "total_processed": total_processed
        }

    def scrape(
        self,
        pages: int = 2,
        start_page: int = 1,
        delay_per_page: float = 5.0,
        db_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Synchronous wrapper for scrape_async."""
        return asyncio.run(
            self.scrape_async(pages=pages, start_page=start_page, delay_per_page=delay_per_page, db_path=db_path)
        )
