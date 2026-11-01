import sqlite3
import os
from typing import Optional, Dict, Any

DEFAULT_DB_PATH = os.environ.get(
    "RENTALS_DB_PATH", 
    "/Users/prateekgoyal/Desktop/Prateek/Claude Projects/Property Explorer/rentals.db"
)

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: Optional[str] = None) -> None:
    """Initialize the 3-table SQLite schema in rentals.db."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    # 1. Raw NoBroker listings table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS raw_nobroker_listings (
        portal_id TEXT PRIMARY KEY,
        title TEXT,
        society TEXT,
        locality TEXT,
        bhk INTEGER,
        bhk_desc TEXT,
        rent INTEGER NOT NULL,
        deposit INTEGER,
        maintenance_amount INTEGER DEFAULT 0,
        maintenance_included BOOLEAN,
        built_up_sqft INTEGER,
        furnishing TEXT,
        bathrooms INTEGER,
        balconies INTEGER,
        floor INTEGER,
        total_floors INTEGER,
        facing TEXT,
        latitude REAL,
        longitude REAL,
        owner_name TEXT,
        listing_url TEXT NOT NULL,
        posted_date TEXT,
        last_updated_date TEXT,
        photos_count INTEGER DEFAULT 0,
        raw_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Raw Housing.com listings table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS raw_housing_listings (
        portal_id TEXT PRIMARY KEY,
        title TEXT,
        society TEXT,
        locality TEXT,
        bhk INTEGER,
        bhk_desc TEXT,
        rent INTEGER NOT NULL,
        deposit INTEGER,
        maintenance_amount INTEGER DEFAULT 0,
        brokerage_amount INTEGER DEFAULT 0,
        built_up_sqft INTEGER,
        carpet_sqft INTEGER,
        furnishing TEXT,
        bathrooms INTEGER,
        balconies INTEGER,
        floor_desc TEXT,
        facing TEXT,
        latitude REAL,
        longitude REAL,
        is_broker BOOLEAN,
        seller_name TEXT,
        listing_url TEXT NOT NULL,
        posted_date TEXT,
        last_updated_date TEXT,
        photos_count INTEGER DEFAULT 0,
        raw_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Raw 99acres listings table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS raw_99acres_listings (
        portal_id TEXT PRIMARY KEY,
        title TEXT,
        society TEXT,
        locality TEXT,
        bhk INTEGER,
        bhk_desc TEXT,
        rent INTEGER NOT NULL,
        deposit INTEGER,
        maintenance_amount INTEGER DEFAULT 0,
        built_up_sqft INTEGER,
        carpet_sqft INTEGER,
        furnishing TEXT,
        bathrooms INTEGER,
        balconies INTEGER,
        floor INTEGER,
        total_floors INTEGER,
        facing TEXT,
        latitude REAL,
        longitude REAL,
        seller_name TEXT,
        listing_url TEXT NOT NULL,
        posted_date TEXT,
        last_updated_date TEXT,
        photos_count INTEGER DEFAULT 0,
        raw_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 4. Master Consolidated listings table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS consolidated_listings (
        uid TEXT PRIMARY KEY,
        dedup_cluster_id TEXT NOT NULL,
        title TEXT,
        society TEXT,
        locality TEXT,
        bhk INTEGER,
        min_rent INTEGER NOT NULL,
        max_rent INTEGER,
        maintenance_amount INTEGER DEFAULT 0,
        total_monthly_cost INTEGER NOT NULL,
        deposit INTEGER,
        built_up_sqft INTEGER,
        carpet_sqft INTEGER,
        price_per_sqft REAL,
        furnishing TEXT,
        bathrooms INTEGER,
        balconies INTEGER,
        floor INTEGER,
        latitude REAL,
        longitude REAL,
        has_direct_owner BOOLEAN DEFAULT 0,
        owner_name TEXT,
        primary_portal TEXT NOT NULL,
        best_listing_url TEXT NOT NULL,
        nobroker_id TEXT,
        housing_id TEXT,
        acres_id TEXT,
        duplicate_count INTEGER DEFAULT 1,
        property_type TEXT,
        is_gated_society BOOLEAN DEFAULT 0,
        total_floors INTEGER,
        is_flatmate_listing BOOLEAN DEFAULT 0,
        posted_date TEXT,
        last_updated_date TEXT,
        description TEXT,
        actual_sector TEXT,
        audit_anomaly_tag TEXT,
        photos_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Ensure schema migrations for existing databases
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(consolidated_listings)")
    cols = [r[1] for r in cur.fetchall()]
    if "actual_sector" not in cols:
        cur.execute("ALTER TABLE consolidated_listings ADD COLUMN actual_sector TEXT")
    if "audit_anomaly_tag" not in cols:
        cur.execute("ALTER TABLE consolidated_listings ADD COLUMN audit_anomaly_tag TEXT")
    if "photos_count" not in cols:
        cur.execute("ALTER TABLE consolidated_listings ADD COLUMN photos_count INTEGER DEFAULT 0")
    if "acres_id" not in cols:
        cur.execute("ALTER TABLE consolidated_listings ADD COLUMN acres_id TEXT")

    cur.execute("PRAGMA table_info(raw_nobroker_listings)")
    nb_cols = [r[1] for r in cur.fetchall()]
    if "photos_count" not in nb_cols:
        cur.execute("ALTER TABLE raw_nobroker_listings ADD COLUMN photos_count INTEGER DEFAULT 0")

    cur.execute("PRAGMA table_info(raw_housing_listings)")
    hc_cols = [r[1] for r in cur.fetchall()]
    if "photos_count" not in hc_cols:
        cur.execute("ALTER TABLE raw_housing_listings ADD COLUMN photos_count INTEGER DEFAULT 0")

    conn.commit()
    conn.close()

def upsert_raw_nobroker(conn: sqlite3.Connection, item: Dict[str, Any]) -> None:
    item.setdefault("photos_count", 0)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO raw_nobroker_listings (
        portal_id, title, society, locality, bhk, bhk_desc, rent, deposit,
        maintenance_amount, maintenance_included, built_up_sqft, furnishing,
        bathrooms, balconies, floor, total_floors, facing, latitude, longitude,
        owner_name, listing_url, posted_date, last_updated_date, photos_count, raw_json
    ) VALUES (
        :portal_id, :title, :society, :locality, :bhk, :bhk_desc, :rent, :deposit,
        :maintenance_amount, :maintenance_included, :built_up_sqft, :furnishing,
        :bathrooms, :balconies, :floor, :total_floors, :facing, :latitude, :longitude,
        :owner_name, :listing_url, :posted_date, :last_updated_date, :photos_count, :raw_json
    )
    ON CONFLICT(portal_id) DO UPDATE SET
        title = excluded.title,
        society = excluded.society,
        locality = excluded.locality,
        bhk = excluded.bhk,
        bhk_desc = excluded.bhk_desc,
        rent = excluded.rent,
        deposit = excluded.deposit,
        maintenance_amount = excluded.maintenance_amount,
        maintenance_included = excluded.maintenance_included,
        built_up_sqft = excluded.built_up_sqft,
        furnishing = excluded.furnishing,
        bathrooms = excluded.bathrooms,
        balconies = excluded.balconies,
        floor = excluded.floor,
        total_floors = excluded.total_floors,
        facing = excluded.facing,
        latitude = excluded.latitude,
        longitude = excluded.longitude,
        owner_name = excluded.owner_name,
        listing_url = excluded.listing_url,
        posted_date = excluded.posted_date,
        last_updated_date = excluded.last_updated_date,
        photos_count = excluded.photos_count,
        raw_json = excluded.raw_json
    """, item)

def upsert_raw_housing(conn: sqlite3.Connection, item: Dict[str, Any]) -> None:
    item.setdefault("photos_count", 0)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO raw_housing_listings (
        portal_id, title, society, locality, bhk, bhk_desc, rent, deposit,
        maintenance_amount, brokerage_amount, built_up_sqft, carpet_sqft,
        furnishing, bathrooms, balconies, floor_desc, facing, latitude, longitude,
        is_broker, seller_name, listing_url, posted_date, last_updated_date, photos_count, raw_json
    ) VALUES (
        :portal_id, :title, :society, :locality, :bhk, :bhk_desc, :rent, :deposit,
        :maintenance_amount, :brokerage_amount, :built_up_sqft, :carpet_sqft,
        :furnishing, :bathrooms, :balconies, :floor_desc, :facing, :latitude, :longitude,
        :is_broker, :seller_name, :listing_url, :posted_date, :last_updated_date, :photos_count, :raw_json
    )
    ON CONFLICT(portal_id) DO UPDATE SET
        title = excluded.title,
        society = excluded.society,
        locality = excluded.locality,
        bhk = excluded.bhk,
        bhk_desc = excluded.bhk_desc,
        rent = excluded.rent,
        deposit = excluded.deposit,
        maintenance_amount = excluded.maintenance_amount,
        brokerage_amount = excluded.brokerage_amount,
        built_up_sqft = excluded.built_up_sqft,
        carpet_sqft = excluded.carpet_sqft,
        furnishing = excluded.furnishing,
        bathrooms = excluded.bathrooms,
        balconies = excluded.balconies,
        floor_desc = excluded.floor_desc,
        facing = excluded.facing,
        latitude = excluded.latitude,
        longitude = excluded.longitude,
        is_broker = excluded.is_broker,
        seller_name = excluded.seller_name,
        listing_url = excluded.listing_url,
        posted_date = excluded.posted_date,
        last_updated_date = excluded.last_updated_date,
        photos_count = excluded.photos_count,
        raw_json = excluded.raw_json
    """, item)

def upsert_raw_ninety_nine_acres(conn: sqlite3.Connection, item: Dict[str, Any]) -> None:
    item.setdefault("photos_count", 0)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO raw_99acres_listings (
        portal_id, title, society, locality, bhk, bhk_desc, rent, deposit,
        maintenance_amount, built_up_sqft, carpet_sqft, furnishing,
        bathrooms, balconies, floor, total_floors, facing, latitude, longitude,
        seller_name, listing_url, posted_date, last_updated_date, photos_count, raw_json
    ) VALUES (
        :portal_id, :title, :society, :locality, :bhk, :bhk_desc, :rent, :deposit,
        :maintenance_amount, :built_up_sqft, :carpet_sqft, :furnishing,
        :bathrooms, :balconies, :floor, :total_floors, :facing, :latitude, :longitude,
        :seller_name, :listing_url, :posted_date, :last_updated_date, :photos_count, :raw_json
    )
    ON CONFLICT(portal_id) DO UPDATE SET
        title = excluded.title,
        society = excluded.society,
        locality = excluded.locality,
        bhk = excluded.bhk,
        bhk_desc = excluded.bhk_desc,
        rent = excluded.rent,
        deposit = excluded.deposit,
        maintenance_amount = excluded.maintenance_amount,
        built_up_sqft = excluded.built_up_sqft,
        carpet_sqft = excluded.carpet_sqft,
        furnishing = excluded.furnishing,
        bathrooms = excluded.bathrooms,
        balconies = excluded.balconies,
        floor = excluded.floor,
        total_floors = excluded.total_floors,
        facing = excluded.facing,
        latitude = excluded.latitude,
        longitude = excluded.longitude,
        seller_name = excluded.seller_name,
        listing_url = excluded.listing_url,
        posted_date = excluded.posted_date,
        last_updated_date = excluded.last_updated_date,
        photos_count = excluded.photos_count,
        raw_json = excluded.raw_json
    """, item)

def upsert_consolidated(conn: sqlite3.Connection, item: Dict[str, Any]) -> None:
    cur = conn.cursor()
    # Ensure default fields exist
    item.setdefault("actual_sector", None)
    item.setdefault("audit_anomaly_tag", None)
    item.setdefault("photos_count", 0)
    item.setdefault("acres_id", None)

    cur.execute("""
    INSERT INTO consolidated_listings (
        uid, dedup_cluster_id, title, society, locality, bhk, min_rent, max_rent,
        maintenance_amount, total_monthly_cost, deposit, built_up_sqft, carpet_sqft,
        price_per_sqft, furnishing, bathrooms, balconies, floor, latitude, longitude,
        has_direct_owner, owner_name, primary_portal, best_listing_url,
        nobroker_id, housing_id, acres_id, duplicate_count, property_type, is_gated_society,
        total_floors, is_flatmate_listing, posted_date, last_updated_date, description,
        actual_sector, audit_anomaly_tag, photos_count
    ) VALUES (
        :uid, :dedup_cluster_id, :title, :society, :locality, :bhk, :min_rent, :max_rent,
        :maintenance_amount, :total_monthly_cost, :deposit, :built_up_sqft, :carpet_sqft,
        :price_per_sqft, :furnishing, :bathrooms, :balconies, :floor, :latitude, :longitude,
        :has_direct_owner, :owner_name, :primary_portal, :best_listing_url,
        :nobroker_id, :housing_id, :acres_id, :duplicate_count, :property_type, :is_gated_society,
        :total_floors, :is_flatmate_listing, :posted_date, :last_updated_date, :description,
        :actual_sector, :audit_anomaly_tag, :photos_count
    )
    ON CONFLICT(uid) DO UPDATE SET
        dedup_cluster_id = excluded.dedup_cluster_id,
        title = excluded.title,
        society = excluded.society,
        locality = excluded.locality,
        bhk = excluded.bhk,
        min_rent = excluded.min_rent,
        max_rent = excluded.max_rent,
        maintenance_amount = excluded.maintenance_amount,
        total_monthly_cost = excluded.total_monthly_cost,
        deposit = excluded.deposit,
        built_up_sqft = excluded.built_up_sqft,
        carpet_sqft = excluded.carpet_sqft,
        price_per_sqft = excluded.price_per_sqft,
        furnishing = excluded.furnishing,
        bathrooms = excluded.bathrooms,
        balconies = excluded.balconies,
        floor = excluded.floor,
        latitude = excluded.latitude,
        longitude = excluded.longitude,
        has_direct_owner = excluded.has_direct_owner,
        owner_name = excluded.owner_name,
        primary_portal = excluded.primary_portal,
        best_listing_url = excluded.best_listing_url,
        nobroker_id = excluded.nobroker_id,
        housing_id = excluded.housing_id,
        acres_id = COALESCE(excluded.acres_id, consolidated_listings.acres_id),
        duplicate_count = excluded.duplicate_count,
        property_type = excluded.property_type,
        is_gated_society = excluded.is_gated_society,
        total_floors = excluded.total_floors,
        is_flatmate_listing = excluded.is_flatmate_listing,
        posted_date = excluded.posted_date,
        last_updated_date = excluded.last_updated_date,
        description = excluded.description,
        actual_sector = COALESCE(excluded.actual_sector, consolidated_listings.actual_sector),
        audit_anomaly_tag = COALESCE(consolidated_listings.audit_anomaly_tag, excluded.audit_anomaly_tag),
        photos_count = excluded.photos_count
    """, item)

def update_listing_audit(
    conn: sqlite3.Connection,
    uid: str,
    anomaly_tag: str,
    actual_sector: Optional[str] = None,
    is_flatmate_listing: Optional[int] = None
) -> None:
    """Manually updates a listing's audit anomaly tag and metadata after agent inspection."""
    cur = conn.cursor()
    updates = ["audit_anomaly_tag = ?"]
    params = [anomaly_tag]
    if actual_sector is not None:
        updates.append("actual_sector = ?")
        params.append(actual_sector)
    if is_flatmate_listing is not None:
        updates.append("is_flatmate_listing = ?")
        params.append(is_flatmate_listing)
    params.append(uid)
    cur.execute(f"UPDATE consolidated_listings SET {', '.join(updates)} WHERE uid = ?", params)
    conn.commit()

