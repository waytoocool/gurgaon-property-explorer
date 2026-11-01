# AI Agent Technical Reference Manual (`agents.md`)

> **Target Audience**: AI Agents (Antigravity, Claude, Copilot, etc.) and human developers working on the Gurgaon Rental Aggregator & Market Intelligence Engine.
> **Role**: Authoritative single source of truth for architectural principles, database schemas, deduplication logic, operational workflows, and hard system invariants.

---

## 1. Project Mission & System Boundaries

### 1.1. Core Mission
The system is a high-precision, personal-use rental aggregator and market intelligence engine for Gurgaon, India. It programmatically extracts live listings from **NoBroker**, **Housing.com**, and **99acres** (with future architectural support for MagicBricks), stages raw data, executes 5-point physical unit deduplication, and computes rental rate benchmarks across high-rise condominiums, independent builder floors, and plotted societies.

### 1.2. Scope Boundary & Inviolable Defers
* **In-Scope**:
  * Multi-page data extraction (REST APIs and browser-assisted SSR).
  * 4-table ELT database persistence with complete raw JSON audit payloads.
  * Canonical normalization (area, rent, furnishing, dates, building height).
  * 5-point physical apartment unit fingerprint deduplication.
  * CLI analytics, rate matrix calculations, and tabular reporting.
  * Lightweight production Leaflet/JS web dashboard ([`run_dashboard.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/run_dashboard.py), [`dashboard.html`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/dashboard.html), `web/`).
* **Strictly Deferred (Out-of-Scope for Current Engine)**:
  * **Heavy Frontend App Frameworks**: Complex Streamlit/React web server architectures and remote cloud deployments are deferred in favor of the lightweight client-side Leaflet UI.
  * **Automated Alert Dispatch**: WhatsApp/Telegram bots or email alert dispatchers are deferred.
  * **Image Binary Storage & OpenCV pHash**: No local image binary caching, CDN image downloading, or OpenCV pHash deduplication in the scraping pipeline.

---

## 2. Inviolable Architectural Principles & Decisions Registry

Any agent modifying or extending this codebase **MUST** uphold these principles and decisions without exception:

### 2.1. Inviolable Core Principles
1. **Zero Data Loss Ingestion Principle**:
   * *Rule*: Extraction and consolidation pipelines **MUST ingest and persist 100% of discovered physical units**.
   * *Rationale*: Discarding builder floors, standalone houses, or older listings at scrape/write time causes irreversible data loss and forces costly web re-scraping.
   * *Enforcement*: `raw_nobroker_listings`, `raw_housing_listings`, and `raw_99acres_listings` store every single crawled payload and its full raw JSON. `consolidated_listings` stores every unique physical unit. **ALL FILTERING LIVES AT QUERY TIME** in [`src/analyzer.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/analyzer.py).
2. **"Show Data As-Is" Policy (Decision 3.5)**:
   * *Rule*: **NEVER hardcode manual Python society reclassifications, whitelists, or blacklists.**
   * *Rationale*: Brokers frequently misclassify properties on portals (e.g. tagging commercial studios like "Premium Stay" or plotted colonies like "Maruti Vihar" as apartments). Portals perform zero physical verification. Manually hardcoding dictionary overrides is unscalable, brittle, and introduces subjective bias.
   * *Enforcement*: Metadata (society name, building type, floor count) is displayed faithfully as reported by brokers and portals.
3. **Objective High-Rise Identification via Building Height (`total_floors >= 6`) (Decision 3.6)**:
   * *Rule*: Programmatically identify high-rise apartment complexes using `total_floors >= 6` (> 5 floors).
   * *Legal Grounding*: In Haryana / Gurgaon, DTCP/HUDA building bye-laws legally cap plotted residential layouts and builder floors at **Stilt + 4 floors (or G+4)**. Any building with **6 or more floors** is physically, legally, and structurally guaranteed to be a multi-story condominium tower.
   * *Enforcement*: Extracted from `totalFloor` on NoBroker and regex-parsed from `floor_desc` on Housing. Query flag `--high-rise` filters for `total_floors >= 6`.
4. **Listing Freshness Standard (30-Day Default Filter) (Decision 2.7)**:
   * *Rule*: Analytical reports apply a **default 30-day recency filter** (`max_age_days = 30`), with `--all-time` available to override.
   * *Market Reality*: In prime Gurgaon sectors, attractive rentals lease in 7–14 days. Listings older or unrefreshed for $> 30$ days are predominantly ghost inventory, already rented units, or broker lead-generation bait. Raw audits revealed NoBroker creation dates dating back to `2017-03-11`.
   * *Enforcement*: Tables store `posted_date` (earliest publish date) and `last_updated_date` (latest refresh/bump date). Query engine defaults to `max_age_days = 30`.
5. **5-Point Physical Unit Fingerprint (Decision 3.2)**:
   * *Rule*: Deduplication across portals is defined strictly as **merging listings representing the exact same physical apartment**.
   * *5-Point Fingerprint*:
     1. *Society / Spatial Proximity*: Same canonical society name OR geolocation coordinates $\le 50$ meters (with hard distance caps rejecting any units $> 500$m apart, or $> 50$m apart for builder floors).
     2. *Exact Floor Number Match (`floor == floor`)*: Flats on different floors are **NEVER** merged.
     3. *Exact BHK Match (`bhk == bhk`)*: Unit bedroom counts must be identical.
     4. *Built-up Area Tolerance*: Built-up square footage must match within $\pm 5\%$.
     5. *Bathroom Count*: Bathrooms must match when reported (100% coverage across portals).
     * *Secondary Non-Conflicting Check (Gate Facing)*: Facing is sparse (NoBroker reports only 31%). If both report a facing, they must not conflict (e.g. North vs South rejected). If either is blank, the match proceeds.
   * *Anti-Pattern Warning*: Never revert to spatial-grid prototype clustering (`CONS_GRID_...`) which conflated market aggregation with deduplication and merged different flats sharing the same floor plan.

### 2.2. Scope & Storage Architecture Decisions
* **Decision 1.1 (Scope Boundary)**: Focus strictly on Data Extraction, Local ELT Persistence, Canonical Cleaning, Deduplication, CLI Analytics, and lightweight production Leaflet web visualization. Heavy server frameworks (Streamlit/React) and automated alerts (WhatsApp/Telegram) are formally deferred.
* **Decision 1.2 (4-Table ELT Storage)**:
  * `raw_nobroker_listings`: Stores parsed key columns + full 101-field raw JSON.
  * `raw_housing_listings`: Stores parsed key columns + full 78-field raw JSON.
  * `raw_99acres_listings`: Stores parsed key columns + full raw JSON.
  * `consolidated_listings`: Master deduplicated physical units and society benchmarks.
  * *Reasoning*: Zero re-scrapes when transformation rules change; 100% auditability and lineage back to source records.
* **Decision 1.3 (Image Storage & Serving)**: No local image binary downloads, CDN caching, or pHash computation in the scraping pipeline. Direct CDN URLs are referenced on-demand by the Leaflet dashboard.

### 2.3. Extraction & Canonical Normalization Decisions
* **Decision 2.1 (Data Sourcing Protocols)**:
  * *NoBroker*: Direct unauthenticated REST API (`GET /api/v3/multi/property/RENT/filter`) with Base64 payload encoding. Sub-second execution (~500ms).
  * *Housing.com*: `nodriver` with warm-up navigation extracting pre-rendered SSR JSON state `window.__INITIAL_STATE__.searchResults.data`. Bypasses Akamai Bot Manager.
  * *99acres*: `nodriver` with warm-up navigation extracting pre-rendered SSR JSON state `window.__initialData__.srp.pageData.properties`. Governed by **5 Strict Anti-Bot Safeguards**:
    1. *Hard Ceiling on Pagination*: Max 2 pages per search feed (`min(pages, 2)`) to strictly prevent triggering the Page 4 captcha trap.
    2. *Automated Circuit Breaker*: Immediate process abort on challenge detection (`verifycaptcha`, `Access Denied`, `Robot`) to safeguard the host IP address.
    3. *Randomized Human Jitter & Behavioral Simulation*: Non-repeating randomized delays (5.0–8.0s) paired with natural human scroll simulation (`page.scroll_down()`).
    4. *Strict Tab Lifecycle Cleanliness*: Explicit tab termination (`await page.close()`) after each page to prevent DevTools session drops.
    5. *Micro-Batch Architecture*: Extraction executed in small horizontal sector batches (`sweep_99acres.py --batch N`) with inter-target cooldowns.
* **Decision 2.2 (Rent & Maintenance Cost Model)**:
  * Store `rent`, `deposit`, and `maintenance_amount`.
  * Always compute: $\text{total\_monthly\_cost} = \text{rent} + \text{maintenance\_amount}$ and $\text{rate\_per\_sqft} = \frac{\text{total\_monthly\_cost}}{\text{built\_up\_sqft}}$.
* **Decision 2.3 (Area Standardization)**: Standardize on **Built-up Area** (`built_up_sqft`) as primary metric across portals. Store `carpet_sqft` as secondary field when reported by Housing or 99acres.
* **Decision 2.4 (Furnishing Category Mapping)**: Normalize portal strings via [`normalize_furnishing`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/normalizers/filters.py#L72-L81) into a 3-tier enum: `Fully Furnished`, `Semi-Furnished`, `Unfurnished`.
* **Decision 2.5 (Contact Details Handling)**: Store `owner_name`, `seller_name`, `has_direct_owner`, and direct `best_listing_url`. Do not attempt phone number extraction (portals mask numbers behind logged-in forms; clicking the saved URL unlocks contact inside user's app).
* **Decision 2.6 (Omitted Non-Essential Fields)**: Omit Tenant Preference (`tenantTypeDesc`, `leaseType`) and Lock-in Period (`lockInPeriod`) from the consolidated table to keep schemas lean and fast.
* **Decision 2.8 (Photo Verification Filter Standard - 2-Photo Minimum)**:
  * *Rule*: Analytical reporting and production dashboard export enforce a **default minimum photo filter** (`photos_count >= 2`), excluding broker lead-generation bait, phantom listings, and static single-image placeholders (`534_notxt.jpg` or single business card photos).
  * *Pipeline Persistence*: `photos_count` is parsed at raw collection time (`len(photos)` on NoBroker, `imageCount` on Housing, `len(PROPERTY_IMAGES)` on 99acres), persisted into `raw_nobroker_listings`, `raw_housing_listings`, `raw_99acres_listings`, and carried forward to `consolidated_listings`.
  * *Auditability*: Override with `--all-photos` across CLI tools or toggle in the web dashboard. Zero data loss is maintained in raw ingestion tables.

### 2.4. Society Normalization & Classification Decisions
* **Decision 3.1 (High-Precision Two-Step Automated Society Resolution)**:
  1. *Step 1 (Category & Pure Sector Tagger)*:
     - If text contains generic builder keywords (`standalone`, `independent`, `builder floor`, `apartment`), tag as `Independent Builder Floor / House`.
     - If text is purely a sector/locality identifier (regex `(sector|sec)[\s-]*\d+[a-z]?(\s+gurgaon|\s+gurugram)?` like `"sector 84"`, `"sec 23A"`), tag as `Independent Builder Floor / House` (eliminates pseudo-societies created by brokers leaving society blank).
  2. *Step 2 (4-Tier Guarded Fuzzy Society Matching)*: For named complexes, run rapidfuzz matching ($\ge 85\%$) enforcing:
     - *Number Invariant*: Distinct numbers (e.g. `Sector 41` vs `Sector 84`, `Phase 2` vs `Phase 4`) are strictly prevented from merging.
     - *Developer Brand Guard*: Multi-token projects (e.g. `"DLF The Camellias"`, `"Signature Global Sarvam"`) can never collapse into 1-word builder brands (`"DLF"`, `"Signature"`).
     - *Location Suffix Stripping*: Strips trailing `, Sector XX` and `, Gurgaon` prior to comparison so core project identities match cleanly.
     - *Distinctive Token Requirement*: Shared tokens must not consist solely of generic address terms (`sector`, `gurgaon`, `phase`).
* **Decision 3.3 (Shared Flatmate & Single-Room Keyword Sanitization)**:
  * Automated keyword scan on `title` and `description` via [`is_single_room_flatmate`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/normalizers/filters.py#L27-L33) catching single-room / flatmate ads:
    `["room available", "room in a 3bhk", "room in a 2bhk", "room in 3bhk", "room in 2bhk", "single room", "pre-occupied", "flatmate", "sublet", "pg/co-living"]` and dormitory regex patterns.
  * Tags unit with `is_flatmate_listing = 1` and excludes from whole-flat market averages.
  * *Sanity Price Floor Rejected*: Heuristic price floors (e.g. `< ₹25k` for 3BHK) rejected per user direction to avoid discarding genuine budget rentals.
* **Decision 3.4 (Consolidated Schema Classification Fields)**:
  * `property_type TEXT`: `'Society / Project (As Reported)'` vs `'Independent Builder Floor / Standalone'`.
  * `is_gated_society BOOLEAN`: `1` for named projects, `0` for generic builder floors.
  * `total_floors INTEGER`: Total building floors extracted from portal data.
  * `is_flatmate_listing BOOLEAN`: `1` for single-room ads, `0` otherwise.
  * `description TEXT`: Stored directly in consolidated table for fast search without raw joins.
* **Decision 3.7 (Sector Variance Detection & Agent Individual Review Workflow)**:
  * *Rule*: Properties discounted by $> 40\%$ relative to the sector baseline median rate (₹/sq.ft) trigger an audit list (`python analyze_data.py --audit-outliers`).
  * *Mandatory Agent Individual Review*: **No automated code alters or assigns anomaly classifications automatically.** An AI or human agent must manually inspect each flagged outlier one by one individually, evaluate the underlying raw remarks and geographic reality, and manually update `consolidated_listings`.
  * *Workflow*:
    1. *Source*: Ingest 100% of discovered units unconditionally (Zero Data Loss).
    2. *Consolidation*: 5-point physical unit fingerprint deduplication with spatial HUDA sector tagging.
    3. *Audit by Price Variance*: Engine flags listings with $> 40\%$ discount against the sector median baseline.
    4. *Agent Individual Review*: Agent manually reviews each flagged listing one by one individually to diagnose the true root cause (`MIS_PINNED_LOCATION`, `FLATMATE_AD`, `LOW_RISE_WALKUP`, `GENUINE_BUDGET_DEAL`).
    5. *Audit Column Persistence*: Agent updates `audit_anomaly_tag` (and updates `actual_sector` or `is_flatmate_listing` if mis-pinned or a flatmate ad). Downstream clean benchmark calculations exclude confirmed mis-pinned or rogue flatmate ads.
* **Decision 3.8 (Statutory GMDA Master Plan 2031 Geographic Standardization)**:
  * *Rule*: Map and standardize all listings strictly to official **HUDA & IMT Manesar Sector numbers** (`Sector 43`, `Sector 28`, `Sector 53`, `IMT Manesar Sector 1`, etc.) as `actual_sector` using **official GMDA OneMap Gurugram Master Plan 2031 vector polygons (Layer 67)** supplemented by **official GMDA Colony Boundaries (Layer 68)** persisted locally in [`config/gurgaon_sectors.geojson`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/config/gurgaon_sectors.geojson).
  * *Coverage*: 169 official planned sectors & municipal enclaves across Gurugram and Manesar:
    * 146 HUDA sectors 1–115 and subsectors like 3A, 9A, 9B, 10A, 11A, 12A, 23A, 25A, 26A, 36A/B, 37A-D, 52A, 63A, 67A, 70A, 72A, 75A, 79A/B, 82A, 88A/B, 89A/B, 95A/B, 99A, 102A, 110A.
    * 19 IMT Manesar sectors M1–M15 (including M1A-D, M3A).
    * 4 GMDA Layer 68 Municipal Enclaves: `Palam Vihar Extension`, `Ashok Vihar (Phases 1-3)`, `Sheetla Colony`, and the statutory `Ammunition Depot (IAF 54 ASP Defense Zone)`.
  * *Containment Protocol*: Unit GPS coordinates are resolved via `shapely` Point-in-Polygon containment (`poly.contains(Point(lon, lat))`). Because official GMDA municipal boundaries share statutory 60m and 30m master road dividers, this achieves **0.0% spatial overlap ambiguity**. Properties situated on road medians or dividers are cleanly assigned to their nearest sector boundary polygon within a 1.5 km buffer (`poly.distance(pt)`).
* **Decision 3.9 (Native Basemap Transit Integration & Zero Vector Clutter)**:
  * *Rule*: Rely strictly on OpenStreetMap Standard's native, multi-scale vector/raster rendering for transit networks (Delhi Metro Yellow Line, Gurgaon Rapid Metro, tracks, and stations). Manual overlay polylines and static circle markers (`METRO_LAYER_GROUP`) are strictly removed to eliminate visual clutter and ensure pristine map legibility.
* **Decision 3.10 (Basemap Layer Architecture & Zero Watermarks)**:
  * *Rule*: Basemap tiles must be 100% keyless, unwatermarked, and reliably available. OpenStreetMap Standard (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`) is the default active basemap, natively providing progressive zoom detailing (landmarks, malls, arterial roads, metro stations).
  * *Secondary Layers*: Provide Esri World Streets, Esri Topo & Landmarks, and Esri Satellite Imagery as 1-click alternative basemaps. Third-party tile providers enforcing API keys (e.g. CARTO's "API KEY REQUIRED" watermark) are strictly prohibited.
* **Decision 3.11 (Statutory Multi-Tier Sector Boundary & Area Verification Engine)**:
  * *Rule*: Verify geometric and area integrity across all official sectors through automated multi-tier checks codified in [`src/boundary_validator.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/boundary_validator.py):
    1. *Tier 1: Pairwise Overlap Invariant*: $\text{Area}(S_i \cap S_j) < 0.001\text{ km}^2$ for all $i \neq j$ (enforcing 0.0% overlap across all 169 sectors/enclaves).
    2. *Tier 2: Physical Size Bounds Sanity Check*: $0.20\text{ km}^2 \le \text{Area} \le 6.00\text{ km}^2$, guaranteeing zero micro-slivers and zero bloated monster voids.
    3. *Tier 3: Planned Footprint & Legitimate Open Zone Audit*: Tracks total planned urban footprint ($341.95\text{ km}^2$) and median sector size ($1.80\text{ km}^2$).
  * *Execution*: Run `python -m src.boundary_validator` offline anytime the sector GeoJSON is updated or audited.

---

## 3. Repository Architecture & File Inventory

```
Property Explorer/
├── analyze_data.py          # CLI entrypoint for querying, reporting, and outlier auditing
├── run_pipeline.py          # Master CLI orchestrator (Scrape -> Consolidate -> Export -> Report)
├── run_dashboard.py         # Production Leaflet web dashboard server & standalone exporter
├── dashboard.html           # Standalone single-file portable offline dashboard
├── rentals.db               # SQLite 3 database (ELT architecture, WAL mode)
├── agents.md                # This manual (Authoritative agent reference)
├── config/
│   ├── sectors.yaml         # Sector registry & scraper seeds (DLF Phase 4, Phase 5, Sector 56/57)
│   ├── gurgaon_sectors.geojson # 169 official GMDA polygons (146 HUDA + 19 IMT Manesar + 4 Enclaves)
│   └── gurgaon_transit.geojson # Transit reference vectors
├── web/
│   ├── index.html           # Production Leaflet map interface & filter controls
│   ├── dashboard.js         # Reactive client-side filtering engine & drawer renderer
│   ├── style.css            # Custom dashboard typography & animations
│   └── data.json            # Compiled dataset (169 polygons + verified listings)
└── src/
    ├── __init__.py
    ├── db.py                # Database connection pooling, schemas & idempotent upserts
    ├── boundary_validator.py # Statutory multi-tier overlap and size bounds verification engine (offline audit)
    ├── consolidator.py      # Master 5-point deduplication & shapely PIP sector engine
    ├── analyzer.py          # Modular calculation engine for market reports & outlier audits
    ├── dashboard_data.py    # Zero-lag GeoJSON & listings compiler for web dashboard
    ├── collectors/
    │   ├── __init__.py
    │   ├── nobroker.py      # Direct unauthenticated REST API collector
    │   ├── housing.py       # nodriver headless browser SSR extractor
    │   └── ninety_nine_acres.py # nodriver browser SSR extractor with circuit breakers
    └── normalizers/
        ├── __init__.py
        ├── filters.py       # Shared utility: Flatmate regex scanner, floor parser, furnishing enum
        └── society.py       # Rapidfuzz Token-Set Ratio society normalizer (>= 85%)
```

### Module Responsibilities:
* [`analyze_data.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/analyze_data.py): Lightweight CLI wrapper around [`src.analyzer.MarketAnalyzer`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/analyzer.py). Accepts `--source`, `--all`, `--high-rise`, `--min-floors`, `--max-age-days`, `--all-time`, `--posted-after`, `--sector`, `--min-photos`, `--all-photos`, `--audit-outliers`, and `--variance-threshold`.
* [`run_pipeline.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/run_pipeline.py): End-to-end master pipeline runner executing `NoBrokerCollector` $\rightarrow$ `HousingCollector` $\rightarrow$ `NinetyNineAcresCollector` $\rightarrow$ `Consolidator` $\rightarrow$ `Dashboard Exporter` $\rightarrow$ `MarketAnalyzer`.
* [`run_dashboard.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/run_dashboard.py): Production Leaflet web dashboard launcher (`http://localhost:8050`) with auto-browser launch and `--export-html` standalone single-file offline compilation.
* [`src/dashboard_data.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/dashboard_data.py): Compiles official HUDA sector polygons and clean verified listings into `web/data.json`.
* [`src/db.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/db.py): Manages `rentals.db` connection lifecycle, WAL mode, schema definitions, and idempotent `ON CONFLICT DO UPDATE` upserts for all tables.
* [`src/boundary_validator.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/boundary_validator.py): Offline statutory verification script verifying 0.0% overlap, size sanity bounds, and open space footprint across GMDA sector polygons.
* [`src/consolidator.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/consolidator.py): Master transformation engine. Reads raw staging tables, standardizes metadata, groups units into candidate clusters, applies the 5-point fingerprint, resolves HUDA sector polygons via PIP, tags data-entry errors, and writes master physical units to `consolidated_listings`.
* [`src/analyzer.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/analyzer.py): Modular `MarketAnalyzer` querying `rentals.db`, computing BHK rate matrices, floor ranges, property type splits, and sector variance outlier audits.
* [`src/collectors/nobroker.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/collectors/nobroker.py): Sub-second REST collector (`GET /api/v3/multi/property/RENT/filter`) with Base64 payload encoding, dynamic pagination (`pageNo=1..N`), and date parsing.
* [`src/collectors/housing.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/collectors/housing.py): `nodriver`-powered browser collector bypassing Akamai bot detection via homepage warm-up, scrolling, and extracting SSR state `window.__INITIAL_STATE__.searchResults.data`.
* [`src/collectors/ninety_nine_acres.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/collectors/ninety_nine_acres.py): `nodriver`-powered browser collector extracting SSR pre-rendered state `window.__initialData__.srp.pageData.properties` governed by 5 strict anti-bot safeguards.
* [`src/normalizers/filters.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/normalizers/filters.py): Shared utility functions: regex scanner detecting rogue single-room/flatmate ads, regex floor parser (`"Floor 11 of 18"` $\rightarrow$ `floor=11, total_floors=18`), and 3-tier furnishing mapping.
* [`src/normalizers/society.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/normalizers/society.py): 2-step hybrid pipeline: (1) builder floor keyword classifier, (2) `rapidfuzz` Token-Set Ratio matching ($\ge 85\%$) for named societies.

---

## 4. Database Schemas & Data Model (`rentals.db`)

### 4.1. Table: `raw_nobroker_listings`
* Staging table for raw NoBroker records.
* **Primary Key**: `portal_id TEXT PRIMARY KEY` (NoBroker property ID, e.g. `8a9f...`).
* **Columns**:
  * `portal_id TEXT PRIMARY KEY`
  * `title TEXT`, `society TEXT`, `locality TEXT`
  * `bhk INTEGER`, `bhk_desc TEXT`
  * `rent INTEGER NOT NULL`, `deposit INTEGER`
  * `maintenance_amount INTEGER DEFAULT 0`, `maintenance_included BOOLEAN`
  * `built_up_sqft INTEGER`, `furnishing TEXT`
  * `bathrooms INTEGER`, `balconies INTEGER`
  * `floor INTEGER`, `total_floors INTEGER`, `facing TEXT`
  * `latitude REAL`, `longitude REAL`
  * `owner_name TEXT`, `listing_url TEXT NOT NULL`
  * `posted_date TEXT`, `last_updated_date TEXT`
  * `photos_count INTEGER DEFAULT 0`
  * `raw_json TEXT NOT NULL` (complete 101-field payload)
  * `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

### 4.2. Table: `raw_housing_listings`
* Staging table for raw Housing.com records.
* **Primary Key**: `portal_id TEXT PRIMARY KEY` (Housing listing ID, e.g. `21246845`).
* **Columns**:
  * `portal_id TEXT PRIMARY KEY`
  * `title TEXT`, `society TEXT`, `locality TEXT`
  * `bhk INTEGER`, `bhk_desc TEXT`
  * `rent INTEGER NOT NULL`, `deposit INTEGER`
  * `maintenance_amount INTEGER DEFAULT 0`, `brokerage_amount INTEGER DEFAULT 0`
  * `built_up_sqft INTEGER`, `carpet_sqft INTEGER`
  * `furnishing TEXT`, `bathrooms INTEGER`, `balconies INTEGER`
  * `floor_desc TEXT` (raw unparsed text e.g. `"Floor 11 of 18"`, parsed into integers during consolidation)
  * `facing TEXT`, `latitude REAL`, `longitude REAL`
  * `is_broker BOOLEAN`, `seller_name TEXT`
  * `listing_url TEXT NOT NULL`
  * `posted_date TEXT`, `last_updated_date TEXT`
  * `photos_count INTEGER DEFAULT 0`
  * `raw_json TEXT NOT NULL` (complete 78-field payload)
  * `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

### 4.3. Table: `raw_99acres_listings`
* Staging table for raw 99acres records.
* **Primary Key**: `portal_id TEXT PRIMARY KEY` (99acres property ID, e.g. `L94589832`).
* **Columns**:
  * `portal_id TEXT PRIMARY KEY`
  * `title TEXT`, `society TEXT`, `locality TEXT`
  * `bhk INTEGER`, `bhk_desc TEXT`
  * `rent INTEGER NOT NULL`, `deposit INTEGER`
  * `maintenance_amount INTEGER DEFAULT 0`
  * `built_up_sqft INTEGER`, `carpet_sqft INTEGER`
  * `furnishing TEXT`, `bathrooms INTEGER`, `balconies INTEGER`
  * `floor INTEGER`, `total_floors INTEGER`, `facing TEXT`
  * `latitude REAL`, `longitude REAL`
  * `seller_name TEXT`, `listing_url TEXT NOT NULL`
  * `posted_date TEXT`, `last_updated_date TEXT`
  * `photos_count INTEGER DEFAULT 0`
  * `raw_json TEXT NOT NULL`
  * `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

### 4.4. Table: `consolidated_listings`
* Master table storing deduplicated physical units and society benchmarks.
* **Primary Key**: `uid TEXT PRIMARY KEY` (e.g. `UNIT_NB_8a9f...`, `UNIT_99_L945...`).
* **Columns**:
  * `uid TEXT PRIMARY KEY`: Unique canonical physical unit ID.
  * `dedup_cluster_id TEXT NOT NULL`: Cluster grouping identifier (e.g. `CLUST_00123`).
  * `title TEXT`: Unit descriptive headline.
  * `society TEXT`: Canonical society name or `'Independent Builder Floor / House'`.
  * `locality TEXT`: Locality context reported by portal.
  * `bhk INTEGER`: Standardized bedroom count.
  * `min_rent INTEGER NOT NULL`, `max_rent INTEGER`: Min and max base rent reported across merged portals.
  * `maintenance_amount INTEGER DEFAULT 0`: Monthly maintenance cost.
  * `total_monthly_cost INTEGER NOT NULL`: Computed as `min_rent + maintenance_amount`.
  * `deposit INTEGER`: Security deposit amount.
  * `built_up_sqft INTEGER`: Standardized built-up area in square feet.
  * `carpet_sqft INTEGER`: Usable carpet area when reported.
  * `price_per_sqft REAL`: Total monthly cost per built-up sq.ft.
  * `furnishing TEXT`: 3-tier enum (`Fully Furnished`, `Semi-Furnished`, `Unfurnished`).
  * `bathrooms INTEGER`, `balconies INTEGER`: Room fixture counts.
  * `floor INTEGER`: Specific apartment floor number.
  * `total_floors INTEGER`: Total building floors (vital for `--high-rise` filtering).
  * `latitude REAL`, `longitude REAL`: Geocoded coordinates (or sector polygon centroid fallback).
  * `has_direct_owner BOOLEAN DEFAULT 0`: Flagged `1` if listed directly by owner without brokerage.
  * `owner_name TEXT`: Verified owner or direct seller name.
  * `primary_portal TEXT NOT NULL`: Primary source portal (`'NoBroker'`, `'Housing'`, `'99acres'`).
  * `best_listing_url TEXT NOT NULL`: Preferred listing URL (prioritizing direct owner links or max photos).
  * `nobroker_id TEXT`, `housing_id TEXT`, `acres_id TEXT`: Foreign keys back to raw source staging tables.
  * `duplicate_count INTEGER DEFAULT 1`: Number of raw portal listings merged into this physical unit.
  * `property_type TEXT`: `'Society / Project (As Reported)'` vs `'Independent Builder Floor / Standalone'`.
  * `is_gated_society BOOLEAN DEFAULT 0`: `1` if listed with a named project, `0` for generic builder floors.
  * `is_flatmate_listing BOOLEAN DEFAULT 0`: `1` if single-room / flatmate ad, `0` otherwise.
  * `description TEXT`: Full property description stored directly for zero-join queries.
  * `actual_sector TEXT`: Standardized HUDA Sector (e.g. `'Sector 43'`, `'Sector 28'`) resolved via GMDA Master Plan 2031 vector polygons.
  * `audit_anomaly_tag TEXT`: Anomaly tag from variance audit (e.g. `'DATA_ENTRY_ERROR'`, `'MIS_PINNED_LOCATION'`, `'FLATMATE_AD'`).
  * `photos_count INTEGER DEFAULT 0`: Maximum photos count across constituent raw listings.
  * `posted_date TEXT`: Earliest original publication date across sources (`YYYY-MM-DD`).
  * `last_updated_date TEXT`: Most recent refresh/bump date across sources (`YYYY-MM-DD`).
  * `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

---

## 5. Common CLI Commands & Workflows

### 5.1. Running Market Analysis (`analyze_data.py`)
```bash
# 1. Default: Named societies & projects, active in last 30 days, min 2 photos
python analyze_data.py

# 2. High-Rise Only: Multi-story condominium towers (total_floors >= 6), active in last 30 days
python analyze_data.py --high-rise

# 3. All properties: Include independent builder floors & standalone houses
python analyze_data.py --all

# 4. Filter strictly to a specific HUDA Sector
python analyze_data.py --sector "Sector 43"
python analyze_data.py --sector "Sector 54" --high-rise

# 5. All-time view: Bypass the 30-day freshness filter to audit full historical database
python analyze_data.py --all-time
python analyze_data.py --high-rise --all-time

# 6. Custom floor cutoff: Filter for towers with >= 10 floors
python analyze_data.py --min-floors 10

# 7. Photo filter override: Include listings with zero or single static photos
python analyze_data.py --all-photos

# 8. Specific date window: Listings active on or after Oct 1, 2026
python analyze_data.py --posted-after 2026-10-01

# 9. Outlier Variance Audit: Identify properties discounted > 40% below sector baseline
python analyze_data.py --audit-outliers
python analyze_data.py --audit-outliers --sector "Sector 43" --variance-threshold 35.0
```

### 5.2. Running the Full Pipeline (`run_pipeline.py`)
```bash
# 1. Citywide Full Sweep: Ingest listings across all 169 sectors dynamically using official centroids
python run_pipeline.py --all-sectors --nb-pages 1

# 2. Targeted 2 BHK Central Gurgaon Deep Sweep (Sectors 14-57, DLF phases, Golf Course Road)
python run_pipeline.py --central-only --bhk 2 --nb-pages 3 --delay 0.25

# 3. Custom Sector Sourcing for Specific High-Rise Hubs
python run_pipeline.py --sectors "Sector 27,Sector 28,Sector 43,Sector 53,Sector 54,Sector 56,Sector 57,Sector 47,Sector 48,Sector 50" --bhk 2 --nb-pages 4

# 4. Multi-Portal Extraction with Housing.com and 99acres
python run_pipeline.py --all-sectors --nb-pages 2 --housing-pages 5 --acres-pages 2 --delay 0.3

# 5. Single Sector Extraction for DLF Phase 4 (3 NoBroker + 2 Housing pages)
python run_pipeline.py --sector dlf_phase_4 --nb-pages 3 --housing-pages 2

# 6. Exhaustive Single Sector Extraction for DLF Phase 4
python run_pipeline.py --sector dlf_phase_4 --nb-pages 10 --housing-pages 5

# 7. Run high-rise report immediately upon pipeline completion
python run_pipeline.py --high-rise

# 8. Re-consolidate and update dashboard on existing database without web scraping
python run_pipeline.py --skip-scrape
```

### 5.3. Adding a New Sector in `config/sectors.yaml`
To add a new sector seed for single-sector mode:
```yaml
crawl_seeds:
  dlf_phase_5:
    name: "DLF Phase 5 / Sector 53 & 54 (Golf Course Road)"
    nobroker:
      lat: 28.4418
      lon: 77.0982
      place_name: "DLF Phase 5"
      place_id: "ChIJ77h6-S_mDDkR48rXlV8z0kU"
      radius: 2.5
    housing:
      url: "https://housing.com/rent/flats-for-rent-in-dlf-phase-5-gurgaon-P32yv3x9e9w9d2h6s"
```

### 5.4. Running the Production Web Dashboard (`run_dashboard.py`)
```bash
# 1. Start local Leaflet dashboard server and auto-open default browser (http://localhost:8050)
python run_dashboard.py

# 2. Run on custom port without auto-launching browser
python run_dashboard.py --port 8080 --no-browser

# 3. Export standalone single-file offline portable dashboard (zero dependencies)
python run_dashboard.py --export-html
```

### 5.5. Running Offline Boundary Validation (`src/boundary_validator.py`)
```bash
# Run statutory 3-tier boundary verification on config/gurgaon_sectors.geojson
python -m src.boundary_validator
```

---

## 6. Technical Gotchas & Operational Notes

### 6.1. macOS ARM64 `nodriver` Execution
* **Issue**: On macOS ARM64, `nodriver.start()` can fail to connect to the browser socket if using the default ephemeral temp profile.
* **Fix Applied**: In [`src/collectors/housing.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/collectors/housing.py#L42-L55) and [`src/collectors/ninety_nine_acres.py`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/src/collectors/ninety_nine_acres.py), always supply `user_data_dir="/tmp/housing_uc_profile"` and locate the system Google Chrome binary explicitly via `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.

### 6.2. Date Format Parsing
* **NoBroker**: Provides Unix epoch timestamps in milliseconds (`creationDate`, `lastUpdateDate`). Parsed via `datetime.fromtimestamp(ms / 1000.0).strftime("%Y-%m-%d")`.
* **Housing.com**: Provides ISO 8601 strings (e.g. `2026-10-15T09:30:00.000Z`). Parsed via `datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")`.

### 6.3. Maintenance Cost Normalization
* Many listings report base rent excluding maintenance.
* Always evaluate rental yield and per-sq.ft rate using:
  $$\text{total\_monthly\_cost} = \text{rent} + \text{maintenance\_amount}$$
  $$\text{rate\_per\_sqft} = \frac{\text{total\_monthly\_cost}}{\text{built\_up\_sqft}}$$

### 6.4. Mandatory 3-Artifact Management System
When working in agentic pair programming on this codebase, always maintain the three living project artifacts in the artifact directory (`<appDataDir>/brain/<conversation-id>/`):
1. `live_discussion.md`: User discussion log, comments, feedback, and active transitions.
2. `current_state_analysis.md`: Empirical single source of truth for live database state, scraped sector inventory metrics, rate matrices, and data quality insights. (All architectural principles, system invariants, schemas, and technical decisions live permanently in [`agents.md`](file:///Users/prateekgoyal/Desktop/Prateek/Claude%20Projects/Property%20Explorer/agents.md)).
3. `plan.md`: Phased execution roadmap, milestones, and deliverables.
* **Header Rule**: Always preserve the `> **Objective**: ...` block at line 3 of each artifact.
* **Synchronous 3-Artifact Invariant (Rule 6.4.1)**: Whenever data extraction sweeps, deduplication passes, or database transformations occur, **ALL THREE ARTIFACTS** (`live_discussion.md`, `current_state_analysis.md`, and `plan.md`) **MUST be updated in lockstep during that exact same turn**. It is a strict system violation to update `plan.md` and `live_discussion.md` while leaving `current_state_analysis.md` displaying outdated database volumes or previous sandbox scopes.
* **Immediate Artifact-First Comment Logging Rule**: Every user comment or question (whether made via artifact inline comments or chat) **MUST be immediately logged in full detail in `live_discussion.md`**, along with the complete technical investigation, analysis, code updates, and CLI verification. Chat responses must remain concise and act as pointers to the artifact rather than trapping detailed analyses in ephemeral chat messages.
