import json
import statistics
from datetime import datetime, date
from collections import defaultdict
from typing import Optional, Dict, Any, List

from .db import get_connection
from .normalizers.filters import is_single_room_flatmate
from .normalizers.society import canonical_society_name, classify_property_as_reported
from .normalizers.anomaly_detector import Tier1AnomalyDetector

class MarketAnalyzer:
    """
    Formalized Modular Market Intelligence Analyzer for Gurgaon Rentals.
    Computes BHK-wise rate matrices, building height ranges, structural archetypes,
    and recency / listing age filters.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.anomaly_detector = Tier1AnomalyDetector()

    def load_data(
        self,
        source: str = "consolidated",
        gated_only: bool = True,
        min_floors: Optional[int] = None,
        max_age_days: Optional[int] = None,
        posted_after: Optional[str] = None,
        sector: Optional[str] = None,
        min_photos: Optional[int] = 2
    ) -> Dict[str, Any]:
        """Loads and filters rental listings according to analytical criteria."""
        conn = get_connection(self.db_path)
        cur = conn.cursor()

        if source == "raw":
            cur.execute("""
            SELECT portal_id, title, society, bhk, rent, maintenance_amount, built_up_sqft, 
                   (rent + maintenance_amount) as total_cost,
                   ROUND((rent + maintenance_amount) * 1.0 / NULLIF(built_up_sqft, 0), 2) as rate,
                   total_floors, posted_date, last_updated_date, raw_json, 
                   NULL as is_flatmate_listing, NULL as property_type, NULL as is_gated_society, NULL as description,
                   NULL as actual_sector, NULL as audit_anomaly_tag, photos_count
            FROM raw_nobroker_listings
            UNION ALL
            SELECT portal_id, title, society, bhk, rent, maintenance_amount, built_up_sqft,
                   (rent + maintenance_amount) as total_cost,
                   ROUND((rent + maintenance_amount) * 1.0 / NULLIF(built_up_sqft, 0), 2) as rate,
                   NULL as total_floors, posted_date, last_updated_date, raw_json, 
                   NULL as is_flatmate_listing, NULL as property_type, NULL as is_gated_society, NULL as description,
                   NULL as actual_sector, NULL as audit_anomaly_tag, photos_count
            FROM raw_housing_listings
            UNION ALL
            SELECT portal_id, title, society, bhk, rent, maintenance_amount, built_up_sqft,
                   (rent + maintenance_amount) as total_cost,
                   ROUND((rent + maintenance_amount) * 1.0 / NULLIF(built_up_sqft, 0), 2) as rate,
                   total_floors, posted_date, last_updated_date, raw_json, 
                   NULL as is_flatmate_listing, NULL as property_type, NULL as is_gated_society, NULL as description,
                   NULL as actual_sector, NULL as audit_anomaly_tag, photos_count
            FROM raw_99acres_listings
            """)
        else:
            cur.execute("""
            SELECT uid as portal_id, title, society, bhk, min_rent as rent, maintenance_amount, 
                   built_up_sqft, total_monthly_cost as total_cost, price_per_sqft as rate,
                   total_floors, posted_date, last_updated_date, '' as raw_json, 
                   is_flatmate_listing, property_type, is_gated_society, description,
                   actual_sector, audit_anomaly_tag, photos_count
            FROM consolidated_listings
            WHERE total_monthly_cost > 0
            """)

        rows = cur.fetchall()
        clean_records = []
        filtered_out_rooms = 0
        filtered_out_non_gated = 0
        filtered_out_low_floors = 0
        filtered_out_stale = 0
        filtered_out_anomalies = 0
        filtered_out_sector = 0
        filtered_out_no_photos = 0

        today = date.today()

        for r in rows:
            desc = r["description"] or ""
            tf = r["total_floors"]
            if r["raw_json"]:
                try:
                    j = json.loads(r["raw_json"])
                    desc = j.get("ownerDescription") or j.get("description") or ""
                    if isinstance(desc, dict):
                        desc = desc.get("overviewDescription", "")
                except Exception:
                    pass

            # Filter out audited anomalous entries (mis-pinned remote locations, data entry typos)
            anomaly_tag = r["audit_anomaly_tag"] if "audit_anomaly_tag" in r.keys() else None
            if anomaly_tag in ("MIS_PINNED_LOCATION", "DATA_ENTRY_ERROR", "FLATMATE_AD"):
                filtered_out_anomalies += 1
                continue

            # Filter by HUDA sector if specified
            actual_sec = r["actual_sector"] if "actual_sector" in r.keys() else "Unknown"
            if sector and actual_sec != sector:
                filtered_out_sector += 1
                continue

            is_flatmate = r["is_flatmate_listing"]
            if is_flatmate is None:
                is_flatmate = 1 if is_single_room_flatmate(r["title"], desc) else 0

            if is_flatmate == 1:
                filtered_out_rooms += 1
                continue

            # Filter by minimum photos (excluding no photo or single static image)
            p_cnt = r["photos_count"] if "photos_count" in r.keys() else None
            if p_cnt is None and r["raw_json"]:
                try:
                    j = json.loads(r["raw_json"])
                    p_cnt = len(j.get("photos") or []) if "photos" in j else (j.get("imageCount", 0) or 0)
                except Exception:
                    p_cnt = 0
            if min_photos is not None and (p_cnt or 0) < min_photos:
                filtered_out_no_photos += 1
                continue

            clean_soc = canonical_society_name(r["society"])
            if r["property_type"]:
                ptype = r["property_type"]
                is_gated = r["is_gated_society"]
            else:
                ptype, is_gated = classify_property_as_reported(clean_soc)

            # Filter for gated / named societies only
            if gated_only and not is_gated and not min_floors:
                filtered_out_non_gated += 1
                continue

            # Filter by minimum total floors (high-rise)
            if min_floors:
                if tf is None or tf < min_floors:
                    filtered_out_low_floors += 1
                    continue

            # Filter by posted_after date (YYYY-MM-DD)
            p_date = r["posted_date"]
            u_date = r["last_updated_date"]

            if posted_after:
                ref_date = p_date or u_date
                if not ref_date or ref_date < posted_after:
                    filtered_out_stale += 1
                    continue

            # Filter by max_age_days
            if max_age_days is not None:
                ref_date_str = u_date or p_date
                if ref_date_str:
                    try:
                        d_obj = datetime.strptime(ref_date_str[:10], "%Y-%m-%d").date()
                        age_days = (today - d_obj).days
                        if age_days > max_age_days:
                            filtered_out_stale += 1
                            continue
                    except Exception:
                        pass

            clean_records.append({
                "portal_id": r["portal_id"],
                "title": r["title"],
                "society": clean_soc,
                "property_type": ptype,
                "is_gated": is_gated,
                "total_floors": tf,
                "bhk": r["bhk"],
                "total_cost": r["total_cost"],
                "built_up_sqft": r["built_up_sqft"],
                "rate": r["rate"],
                "posted_date": p_date,
                "last_updated_date": u_date,
                "actual_sector": actual_sec
            })

        conn.close()
        return {
            "records": clean_records,
            "filtered_rooms": filtered_out_rooms,
            "filtered_non_gated": filtered_out_non_gated,
            "filtered_low_floors": filtered_out_low_floors,
            "filtered_stale": filtered_out_stale,
            "filtered_anomalies": filtered_out_anomalies,
            "filtered_sector": filtered_out_sector,
            "filtered_no_photos": filtered_out_no_photos
        }

    def aggregate_societies(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Computes society-level BHK-wise pricing, floor ranges, and latest refresh dates."""
        soc_groups = defaultdict(list)
        for r in records:
            soc_groups[r["society"]].append(r)

        results = []
        for soc, items in soc_groups.items():
            cnt = len(items)
            rents = [i["total_cost"] for i in items if i["total_cost"] and i["total_cost"] > 0]
            avg_rent = int(statistics.mean(rents)) if rents else 0
            rates = [i["rate"] for i in items if i["rate"] and i["rate"] > 0]
            avg_rate = round(statistics.mean(rates), 2) if rates else 0
            ptype = items[0]["property_type"]
            is_gated = items[0]["is_gated"]

            # Floors summary
            soc_tfs = [i["total_floors"] for i in items if i["total_floors"] is not None]
            if soc_tfs:
                min_f, max_f = min(soc_tfs), max(soc_tfs)
                flr_str = f"G+{max_f}" if min_f == max_f else f"{min_f}-{max_f}"
            else:
                flr_str = "—"

            # Recency summary
            u_dates = [i["last_updated_date"] for i in items if i.get("last_updated_date")]
            latest_ref = max(u_dates) if u_dates else "—"

            bhk_rates = {}
            for b in [1, 2, 3, 4]:
                if b == 4:
                    b_rates = [i["rate"] for i in items if i["bhk"] and i["bhk"] >= 4 and i["rate"] and i["rate"] > 0]
                else:
                    b_rates = [i["rate"] for i in items if i["bhk"] == b and i["rate"] and i["rate"] > 0]
                bhk_rates[b] = round(statistics.mean(b_rates), 1) if b_rates else None

            results.append({
                "society": soc,
                "type": ptype,
                "is_gated": is_gated,
                "floors": flr_str,
                "latest_update": latest_ref,
                "count": cnt,
                "bhk1": bhk_rates[1],
                "bhk2": bhk_rates[2],
                "bhk3": bhk_rates[3],
                "bhk4": bhk_rates[4],
                "avg_rent": avg_rent,
                "avg_rate": avg_rate
            })

        results.sort(key=lambda x: (x["count"], x["avg_rate"]), reverse=True)
        return results

    def aggregate_property_types(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Computes property type distribution and averages."""
        ptypes = defaultdict(list)
        for r in records:
            ptypes[r["property_type"]].append(r)

        results = []
        for pt, items in sorted(ptypes.items(), key=lambda x: len(x[1]), reverse=True):
            avg_r = int(statistics.mean([i["total_cost"] for i in items if i["total_cost"]]))
            rates = [i["rate"] for i in items if i["rate"] and i["rate"] > 0]
            avg_rt = round(statistics.mean(rates), 2) if rates else 0
            pct = round(len(items) * 100 / len(records), 1) if records else 0.0
            results.append({
                "property_type": pt,
                "count": len(items),
                "percentage": pct,
                "avg_rent": avg_r,
                "avg_rate": avg_rt
            })
        return results

    def aggregate_sectors(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Computes official HUDA sector distribution and benchmarks."""
        secs = defaultdict(list)
        for r in records:
            sec_name = r.get("actual_sector") or "Unknown"
            secs[sec_name].append(r)

        results = []
        for sec, items in sorted(secs.items(), key=lambda x: len(x[1]), reverse=True):
            avg_r = int(statistics.mean([i["total_cost"] for i in items if i["total_cost"]]))
            rates = [i["rate"] for i in items if i["rate"] and i["rate"] > 0]
            avg_rt = round(statistics.mean(rates), 2) if rates else 0
            pct = round(len(items) * 100 / len(records), 1) if records else 0.0
            results.append({
                "sector": sec,
                "count": len(items),
                "percentage": pct,
                "avg_rent": avg_r,
                "avg_rate": avg_rt
            })
        return results

    def print_analysis(
        self,
        source: str = "consolidated",
        gated_only: bool = True,
        min_floors: Optional[int] = None,
        max_age_days: Optional[int] = None,
        posted_after: Optional[str] = None,
        sector: Optional[str] = None,
        min_photos: Optional[int] = 2
    ) -> None:
        """Formats and prints the standard CLI report."""
        mode_parts = []
        if sector:
            mode_parts.append(f"Sector: {sector}")
        if min_floors:
            mode_parts.append(f"HIGH-RISE APARTMENTS ONLY (Total Floors >= {min_floors})")
        elif gated_only:
            mode_parts.append("NAMED SOCIETIES & COMPLEXES ONLY")
        else:
            mode_parts.append("ALL PROPERTIES (INCL. BUILDER FLOORS)")

        if max_age_days is not None:
            mode_parts.append(f"Active in Last {max_age_days} Days")
        if posted_after:
            mode_parts.append(f"Posted After {posted_after}")
        if min_photos is not None and min_photos > 0:
            mode_parts.append(f"Photos >= {min_photos}")
        else:
            mode_parts.append("All Photos")

        mode_str = " | ".join(mode_parts)

        print("==========================================================================")
        print(f"       GURGAON RENTAL DATA ANALYSIS ({mode_str} | Source: {source.upper()})")
        print("==========================================================================\n")

        data = self.load_data(source, gated_only, min_floors, max_age_days, posted_after, sector, min_photos)
        records = data["records"]

        filter_notes = []
        if min_floors:
            filter_notes.append(f"{data['filtered_low_floors']} units with < {min_floors} floors")
        elif gated_only:
            filter_notes.append(f"{data['filtered_non_gated']} independent builder floors / standalone")
        if data["filtered_rooms"]:
            filter_notes.append(f"{data['filtered_rooms']} flatmate ads")
        if data["filtered_stale"]:
            filter_notes.append(f"{data['filtered_stale']} stale/older listings")
        if data["filtered_anomalies"]:
            filter_notes.append(f"{data['filtered_anomalies']} audited anomalous listings (mis-pinned/rogue)")
        if data["filtered_sector"]:
            filter_notes.append(f"{data['filtered_sector']} listings in other sectors")
        if data.get("filtered_no_photos"):
            filter_notes.append(f"{data['filtered_no_photos']} listings with < {min_photos} photos")

        notes_str = f" (Filtered out {', '.join(filter_notes)})" if filter_notes else ""
        print(f"Total Units Analyzed: {len(records)}{notes_str}\n")

        societies = self.aggregate_societies(records)

        print("1. SOCIETY & COMPLEX INVENTORY (BHK-WISE RATE MATRIX)")
        print(f"{'Society Name':<32} | {'Floors':<8} | {'Latest':<10} | {'Count':<5} | {'1 BHK':<7} | {'2 BHK':<7} | {'3 BHK':<7} | {'4+ BHK':<7} | {'Avg Rent':<10} | {'Avg ₹/sqft'}")
        print("-" * 148)

        for r in societies:
            b1 = f"₹{r['bhk1']}" if r['bhk1'] else "—"
            b2 = f"₹{r['bhk2']}" if r['bhk2'] else "—"
            b3 = f"₹{r['bhk3']}" if r['bhk3'] else "—"
            b4 = f"₹{r['bhk4']}" if r['bhk4'] else "—"
            print(f"{r['society']:<32} | {r['floors']:<8} | {r['latest_update']:<10} | {r['count']:<5} | {b1:<7} | {b2:<7} | {b3:<7} | {b4:<7} | ₹{r['avg_rent']:<9,} | ₹{r['avg_rate']}")

        print("\n2. PROPERTY TYPE SPLIT")
        ptypes = self.aggregate_property_types(records)
        for pt in ptypes:
            print(f"   * {pt['property_type']:<30}: {pt['count']:>2} units ({pt['percentage']}%) | Avg Rent: ₹{pt['avg_rent']:,}/mo | Avg Rate: ₹{pt['avg_rate']}/sq.ft")

        print("\n3. OFFICIAL HUDA SECTOR DISTRIBUTION")
        secs = self.aggregate_sectors(records)
        for s in secs:
            print(f"   * {s['sector']:<25}: {s['count']:>2} units ({s['percentage']}%) | Avg Rent: ₹{s['avg_rent']:,}/mo | Avg Rate: ₹{s['avg_rate']}/sq.ft")

        print("\n==========================================================================")

    def audit_outliers(
        self,
        threshold_percent: float = 40.0,
        source: str = "consolidated",
        sector: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Sector Variance & Outlier Diagnostic Engine:
        Identifies properties priced >= threshold_percent below the sector typology baseline (price_per_sqft)
        using dynamic dual-tier segmentation (High-Rise >= 6 floors vs Low-Rise Builder Floors <= 4 floors).
        Diagnoses the underlying cause:
        - [FLATMATE AD]: Single-room or shared flatmate ad masquerading as a full flat.
        - [MIS-PINNED LOCATION]: Property from another city/township (Sohna, Alwar, Bhiwadi, Delhi, Manesar) mis-pinned into this sector.
        - [DATA ENTRY ERROR]: Zero rent, placeholder token rent, or super-area corruption.
        - [LOW-RISE WALK-UP]: Low-rise builder floor or plotted layout (<= 4 floors) without elevator.
        - [GENUINE BUDGET DEAL]: True full-unit distressed or low-rate opportunity in high-rise condominium.
        """
        conn = get_connection(self.db_path)
        cur = conn.cursor()

        if source == "raw":
            cur.execute("""
            SELECT portal_id, title, society, locality, NULL as actual_sector, bhk, rent, maintenance_amount, built_up_sqft,
                   (rent + maintenance_amount) as total_cost,
                   ROUND((rent + maintenance_amount) * 1.0 / NULLIF(built_up_sqft, 0), 2) as rate,
                   total_floors, floor, listing_url as best_listing_url, posted_date, last_updated_date,
                   raw_json, NULL as is_flatmate_listing, NULL as description, NULL as audit_anomaly_tag,
                   NULL as property_type, latitude, longitude
            FROM raw_nobroker_listings WHERE built_up_sqft > 0
            UNION ALL
            SELECT portal_id, title, society, locality, NULL as actual_sector, bhk, rent, maintenance_amount, built_up_sqft,
                   (rent + maintenance_amount) as total_cost,
                   ROUND((rent + maintenance_amount) * 1.0 / NULLIF(built_up_sqft, 0), 2) as rate,
                   NULL as total_floors, NULL as floor, listing_url as best_listing_url, posted_date, last_updated_date,
                   raw_json, NULL as is_flatmate_listing, NULL as description, NULL as audit_anomaly_tag,
                   NULL as property_type, latitude, longitude
            FROM raw_housing_listings WHERE built_up_sqft > 0
            UNION ALL
            SELECT portal_id, title, society, locality, NULL as actual_sector, bhk, rent, maintenance_amount, built_up_sqft,
                   (rent + maintenance_amount) as total_cost,
                   ROUND((rent + maintenance_amount) * 1.0 / NULLIF(built_up_sqft, 0), 2) as rate,
                   total_floors, floor, listing_url as best_listing_url, posted_date, last_updated_date,
                   raw_json, NULL as is_flatmate_listing, NULL as description, NULL as audit_anomaly_tag,
                   NULL as property_type, latitude, longitude
            FROM raw_99acres_listings WHERE built_up_sqft > 0
            """)
        else:
            cur.execute("""
            SELECT uid as portal_id, title, society, locality, actual_sector, bhk, min_rent as rent, maintenance_amount,
                   built_up_sqft, total_monthly_cost as total_cost, price_per_sqft as rate,
                   total_floors, floor, best_listing_url, posted_date, last_updated_date,
                   '' as raw_json, is_flatmate_listing, description, audit_anomaly_tag,
                   property_type, latitude, longitude
            FROM consolidated_listings
            WHERE built_up_sqft > 0
            """)

        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        # Normalize threshold if passed as decimal (e.g. 0.40 -> 40.0)
        if 0 < threshold_percent <= 1.0:
            threshold_percent *= 100.0

        # Compute dual-tier sector medians (High-Rise >= 6 floors vs Low-Rise <= 4 floors)
        sector_hr_rates = defaultdict(list)
        sector_lr_rates = defaultdict(list)
        sector_all_rates = defaultdict(list)
        city_hr_rates = []
        city_lr_rates = []
        city_all_rates = []

        for r in rows:
            rate = r["rate"]
            if not rate or rate <= 0:
                continue
            sec = r.get("actual_sector") or "Unknown"
            tf = r.get("total_floors")
            is_hr = (tf is not None and tf >= 6)

            sector_all_rates[sec].append(rate)
            city_all_rates.append(rate)
            if is_hr:
                sector_hr_rates[sec].append(rate)
                city_hr_rates.append(rate)
            else:
                sector_lr_rates[sec].append(rate)
                city_lr_rates.append(rate)

        default_city_hr = statistics.median(city_hr_rates) if city_hr_rates else 35.0
        default_city_lr = statistics.median(city_lr_rates) if city_lr_rates else 25.0

        MISLOCATED_KEYWORDS = [
            "sohna", "alwar", "alvar", "bhiwadi", "raghopur", "sector-109", "sector 109",
            "sector-108", "sector 108", "faridabad", "manesar", "dharuhera", "neemrana", "pataudi",
            "kapashera", "aaya nagar", "chhawla"
        ]

        outliers = []
        for r in rows:
            rate = r["rate"]
            sec = r.get("actual_sector") or "Unknown"
            if sector and sec != sector:
                continue

            tf = r.get("total_floors")
            is_hr = (tf is not None and tf >= 6)

            # Determine baseline for this listing's sector & typology
            if is_hr:
                if len(sector_hr_rates.get(sec, [])) >= 3:
                    baseline_rate = statistics.median(sector_hr_rates[sec])
                elif len(sector_all_rates.get(sec, [])) >= 3:
                    baseline_rate = statistics.median(sector_all_rates[sec])
                else:
                    baseline_rate = default_city_hr
                typology_str = "High-Rise"
            else:
                if len(sector_lr_rates.get(sec, [])) >= 3:
                    baseline_rate = statistics.median(sector_lr_rates[sec])
                elif len(sector_all_rates.get(sec, [])) >= 3:
                    baseline_rate = statistics.median(sector_all_rates[sec])
                else:
                    baseline_rate = default_city_lr
                typology_str = "Low-Rise"

            cutoff_rate = round(baseline_rate * (1.0 - threshold_percent / 100.0), 2)
            existing_tag = r.get("audit_anomaly_tag")

            # Outlier condition: discount exceeds threshold OR has known anomaly tag OR zero/token rent
            is_discount_outlier = (rate is not None and rate <= cutoff_rate)
            is_zero_or_token = (r["rent"] is not None and (r["rent"] <= 0 or (r["rent"] <= 1000 and (r["built_up_sqft"] or 0) >= 400)))
            is_flagged_anomaly = bool(existing_tag in ("DATA_ENTRY_ERROR", "MIS_PINNED_LOCATION", "FLATMATE_AD"))

            if not (is_discount_outlier or is_zero_or_token or is_flagged_anomaly):
                continue

            desc = r["description"] or ""
            if r["raw_json"]:
                try:
                    j = json.loads(r["raw_json"])
                    desc = j.get("ownerDescription") or j.get("description") or ""
                    if isinstance(desc, dict):
                        desc = desc.get("overviewDescription", "")
                except Exception:
                    pass

            r_cand = dict(r)
            r_cand["description"] = desc

            detected_tag = existing_tag
            detected_reason = ""
            if not detected_tag or detected_tag == "LOW_RISE_WALKUP":
                d_tag, d_reason = self.anomaly_detector.detect(r_cand)
                if d_tag:
                    detected_tag = d_tag
                    detected_reason = d_reason

            flr = r["floor"]

            # Diagnosis assignment
            if detected_tag == "DATA_ENTRY_ERROR" or is_zero_or_token:
                diag = "[DATA ENTRY ERROR]"
                reason = detected_reason or "Zero rent, token placeholder rent, or corrupted pricing denominator"
            elif detected_tag == "FLATMATE_AD" or bool(r.get("is_flatmate_listing")):
                diag = "[FLATMATE AD]"
                reason = detected_reason or "Partial room or flatmate ad masquerading as a full flat"
            elif detected_tag == "MIS_PINNED_LOCATION":
                diag = "[MIS-PINNED LOCATION]"
                reason = detected_reason or "Property located in distant area/sector mis-pinned into this sector"
            elif detected_tag == "LOW_RISE_WALKUP" or not is_hr:
                diag = "[LOW-RISE WALK-UP]"
                reason = detected_reason or f"Older low-rise walk-up ({'G+' + str(tf) if tf else 'low-rise'}); lack of elevator/amenities discounts rent"
            else:
                diag = "[GENUINE BUDGET DEAL]"
                reason = "Verified high-rise full-flat unit priced significantly below typology benchmark"

            discount_pct = round(((baseline_rate - (rate or 0)) / baseline_rate) * 100.0, 1) if baseline_rate else 0.0

            outliers.append({
                "portal_id": r["portal_id"],
                "society": r["society"] or "Independent",
                "locality": r["locality"] or "Gurgaon",
                "sector": sec,
                "typology": typology_str,
                "bhk": r["bhk"],
                "rent": r["rent"],
                "built_up_sqft": r["built_up_sqft"],
                "rate": rate or 0.0,
                "baseline_rate": round(baseline_rate, 2),
                "discount_pct": discount_pct,
                "total_floors": tf,
                "floor": flr,
                "diagnosis": diag,
                "reason": reason,
                "url": r["best_listing_url"]
            })

        outliers.sort(key=lambda x: (x["rate"], -x["discount_pct"]))
        return {
            "city_highrise_median": round(default_city_hr, 2),
            "city_lowrise_median": round(default_city_lr, 2),
            "threshold_percent": threshold_percent,
            "total_outliers": len(outliers),
            "outliers": outliers
        }

    def print_outlier_audit(
        self,
        threshold_percent: float = 40.0,
        source: str = "consolidated",
        sector: Optional[str] = None
    ) -> None:
        report = self.audit_outliers(threshold_percent=threshold_percent, source=source, sector=sector)
        hr_med = report["city_highrise_median"]
        lr_med = report["city_lowrise_median"]
        outliers = report["outliers"]

        sec_title = f" | Sector: {sector}" if sector else ""
        print("=" * 155)
        print(f"             DYNAMIC DUAL-TIER SECTOR VARIANCE & OUTLIER REPORT (Source: {source.upper()}{sec_title})")
        print(f"             City High-Rise Median: ₹{hr_med:.2f}/sq.ft | City Low-Rise Median: ₹{lr_med:.2f}/sq.ft")
        print(f"             Outlier Threshold: > {threshold_percent:.0f}% Discount relative to Sector Typology Baseline")
        print(f"             Total Outliers Detected: {len(outliers)}")
        print("=" * 155 + "\n")

        diag_counts = defaultdict(int)
        for o in outliers:
            diag_counts[o["diagnosis"]] += 1

        print("1. TIER-1 AUTOMATED ROOT-CAUSE SUMMARY:")
        for diag in ["[GENUINE BUDGET DEAL]", "[LOW-RISE WALK-UP]", "[MIS-PINNED LOCATION]", "[DATA ENTRY ERROR]", "[FLATMATE AD]"]:
            cnt = diag_counts.get(diag, 0)
            if cnt > 0:
                print(f"   * {diag:<24}: {cnt:3d} listings")

        print("\n2. ANOMALOUS LISTINGS AUDIT MATRIX:")
        print(f"{'Society / Complex':<24} | {'Sector':<14} | {'Typology':<9} | {'BHK':<4} | {'Rent':<8} | {'Sqft':<5} | {'₹/sqft':<7} | {'Disc%':<7} | {'Diagnosis':<22} | {'Root Cause & Details'}")
        print("-" * 155)

        for o in outliers:
            bhk_s = f"{o['bhk']} BHK" if o['bhk'] else "—"
            rent_s = f"₹{o['rent']:,}" if o['rent'] else "₹0"
            sqft_s = f"{o['built_up_sqft']}"
            rate_s = f"₹{o['rate']:.2f}"
            disc_s = f"-{abs(o['discount_pct']):.1f}%" if o['discount_pct'] >= 0 else f"+{abs(o['discount_pct']):.1f}%"
            sec_s = o['sector'][:14]
            print(f"{o['society'][:24]:<24} | {sec_s:<14} | {o['typology']:<9} | {bhk_s:<4} | {rent_s:<8} | {sqft_s:<5} | {rate_s:<7} | {disc_s:<7} | {o['diagnosis']:<22} | {o['reason']}")

        print("\n" + "=" * 155)
