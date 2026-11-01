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

    @staticmethod
    def to_bhk_url(url: str, bhk: Optional[int] = None) -> str:
        """Converts standard Housing.com rent URL to dynamic BHK-specific URL."""
        if not bhk:
            return url
        # If url already has a BHK prefix like /2bhk-flats-for-rent-in-
        if re.search(r"/\d+bhk-flats-for-rent-in-", url):
            url = re.sub(r"/\d+bhk-flats-for-rent-in-", f"/{bhk}bhk-flats-for-rent-in-", url)
        elif "/flats-for-rent-in-" in url:
            url = url.replace("/flats-for-rent-in-", f"/{bhk}bhk-flats-for-rent-in-")
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
            society = "DLF Phase 4 Builder Floor / Independent"

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

                for p_id, item in sr_data.items():
                    parsed = self.parse_listing(p_id, item)
                    upsert_raw_housing(conn, parsed)
                    total_processed += 1

                conn.commit()
                pages_completed += 1

        finally:
            browser.stop()
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
        return uc.loop().run_until_complete(
            self.scrape_async(pages=pages, start_page=start_page, delay_per_page=delay_per_page, db_path=db_path)
        )
