import json
import base64
import time
import requests
from typing import Optional, Dict, Any, List

from ..db import get_connection, upsert_raw_nobroker
from ..normalizers.filters import normalize_furnishing

class NoBrokerCollector:
    """
    Automated Multi-Page REST Ingestion Collector for NoBroker.
    Leverages unauthenticated GET endpoint with Base64 search parameters.
    """
    API_URL = "https://www.nobroker.in/api/v3/multi/property/RENT/filter"

    def __init__(
        self,
        lat: float = 28.4671,
        lon: float = 77.0820,
        place_name: str = "DLF Phase IV",
        place_id: str = "",
        radius: float = 2.5,
        city: str = "gurgaon",
        bhk_type: Optional[str] = None
    ):
        self.lat = lat
        self.lon = lon
        self.place_name = place_name
        self.place_id = place_id
        self.radius = radius
        self.city = city
        self.bhk_type = bhk_type
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.nobroker.in/"
        }

    def _build_search_param(self) -> str:
        payload = [{
            "lat": self.lat,
            "lon": self.lon,
            "placeId": self.place_id,
            "placeName": self.place_name
        }]
        return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

    def fetch_page(self, page_no: int, max_retries: int = 3) -> List[Dict[str, Any]]:
        """Fetches a single page of listings with retry backoff."""
        params = {
            "pageNo": page_no,
            "searchParam": self._build_search_param(),
            "radius": str(self.radius),
            "city": self.city,
            "sharedAccomodation": "0",
            "orderBy": "lastUpdateDate,desc"
        }
        if self.bhk_type:
            params["type"] = self.bhk_type

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.get(self.API_URL, params=params, headers=self.headers, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", [])
                print(f"[NoBroker] Page {page_no} returned HTTP {resp.status_code} (attempt {attempt}/{max_retries})")
            except Exception as e:
                print(f"[NoBroker] Error fetching page {page_no} (attempt {attempt}/{max_retries}): {e}")
            
            if attempt < max_retries:
                time.sleep(1.5 * attempt)

        return []

    def parse_listing(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Maps raw portal payload into standardized DB columns."""
        p_id = item.get("id")
        title = item.get("propertyTitle")
        society = item.get("society")
        locality = item.get("locality")
        bhk_desc = item.get("typeDesc")

        bhk = None
        if bhk_desc:
            if "BHK" in bhk_desc:
                try:
                    bhk = int(bhk_desc.split("BHK")[0].strip())
                except Exception:
                    bhk = None
            elif "RK" in bhk_desc:
                bhk = 1

        furn = normalize_furnishing(item.get("furnishingDesc"))
        detail_url = item.get("detailUrl")
        if detail_url:
            listing_url = f"https://www.nobroker.in{detail_url}" if detail_url.startswith("/") else detail_url
        else:
            listing_url = f"https://www.nobroker.in/property/{p_id}" if p_id else ""
        posted_date = None
        if item.get("creationDate"):
            try:
                posted_date = time.strftime("%Y-%m-%d", time.localtime(item["creationDate"] / 1000))
            except Exception:
                posted_date = None

        last_updated_date = None
        if item.get("lastUpdateDate"):
            try:
                last_updated_date = time.strftime("%Y-%m-%d", time.localtime(item["lastUpdateDate"] / 1000))
            except Exception:
                last_updated_date = None

        return {
            "portal_id": p_id,
            "title": title,
            "society": society,
            "locality": locality,
            "bhk": bhk,
            "bhk_desc": bhk_desc,
            "rent": item.get("rent", 0),
            "deposit": item.get("deposit", 0),
            "maintenance_amount": item.get("maintenanceAmount", 0),
            "maintenance_included": item.get("maintenanceIncluded", False),
            "built_up_sqft": item.get("propertySize", 0),
            "furnishing": furn,
            "bathrooms": item.get("bathroom"),
            "balconies": item.get("balconies"),
            "floor": item.get("floor"),
            "total_floors": item.get("totalFloor"),
            "facing": item.get("facingDesc"),
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
            "owner_name": item.get("ownerName"),
            "listing_url": listing_url,
            "posted_date": posted_date,
            "last_updated_date": last_updated_date,
            "photos_count": len(item.get("photos") or []),
            "raw_json": json.dumps(item)
        }

    def scrape(
        self,
        start_page: int = 1,
        end_page: int = 3,
        delay_seconds: float = 1.0,
        db_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Scrapes multiple pages and directly stages raw data into rentals.db."""
        conn = get_connection(db_path)
        total_processed = 0
        pages_completed = 0

        print(f"[NoBroker] Ingesting {self.place_name} (Pages {start_page} to {end_page})...")
        for page in range(start_page, end_page + 1):
            items = self.fetch_page(page)
            if not items:
                print(f"[NoBroker] Page {page} returned 0 items. Stopping pagination.")
                break

            for item in items:
                parsed = self.parse_listing(item)
                upsert_raw_nobroker(conn, parsed)
                total_processed += 1

            conn.commit()
            pages_completed += 1
            print(f"[NoBroker] Page {page}: Ingested {len(items)} listings.")

            if page < end_page:
                time.sleep(delay_seconds)

        conn.close()
        return {
            "portal": "NoBroker",
            "pages_completed": pages_completed,
            "total_processed": total_processed
        }
