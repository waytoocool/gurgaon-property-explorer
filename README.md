# Gurgaon Property Explorer & Rental Intelligence Engine

[![Live Dashboard](https://img.shields.io/badge/Live-Interactive%20Map%20Dashboard-blue?style=for-the-badge&logo=leaflet)](https://waytoocool.github.io/gurgaon-property-explorer/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![SQLite 3](https://img.shields.io/badge/SQLite-WAL%20ELT-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)

> **Live Dashboard**: [https://waytoocool.github.io/gurgaon-property-explorer/](https://waytoocool.github.io/gurgaon-property-explorer/)

High-precision rental aggregator, 5-point physical unit deduplication engine, and interactive geospatial market intelligence platform for Gurgaon, India.

Ingests verified rental listings across **NoBroker**, **Housing.com**, and **99acres**, standardizes geometries using official GMDA Master Plan 2031 sector polygons, and serves zero-lag client-side spatial analytics.

---

## 🌟 Key Features

* **Interactive Leaflet Geospatial UI**:
  * Real-time multi-criteria filtering by **BHK** (1, 2, 3, 4+), **Furnishing** (Fully, Semi, Unfurnished), **Typology** (High-Rise vs Independent Floors), and **Portal Source**.
  * Dynamic pricing slider and instant sector-level aggregation drawer.
  * Direct deep links to original verified listings across portals.
* **169 Official GMDA Master Plan 2031 Polygons**:
  * Comprehensive sector mapping across 146 HUDA sectors, 19 IMT Manesar sectors, and municipal enclaves.
  * 0.0% pairwise spatial overlap ambiguity verified by statutory boundary audits.
* **5-Point Physical Unit Fingerprint**:
  * Deduplicates identical physical apartments across competing portals based on:
    1. Geolocation proximity ($\le 50$m) & canonical society matching.
    2. Exact floor match (`floor == floor`).
    3. Exact bedroom count (`bhk == bhk`).
    4. Built-up area tolerance ($\pm 5\%$).
    5. Bathroom count & non-conflicting gate facing.
* **Zero Data Loss Ingestion (ELT)**:
  * Complete source JSON payloads persisted to dedicated raw staging tables.
* **CLI Market Intelligence Engine**:
  * Dual-tier rate benchmarks (₹/sq.ft) comparing high-rise towers ($\ge 6$ floors) against low-rise builder floors ($\le 4$ floors).
  * Automated price variance outlier auditing (`--audit-outliers`).

---

## 🚀 Quickstart

### 1. Access the Web Dashboard
You can access the live interactive dashboard instantly in your browser:
👉 **[Open Live Dashboard](https://waytoocool.github.io/gurgaon-property-explorer/)**

Or run locally:
```bash
python run_dashboard.py
# Opens http://localhost:8050 automatically
```

Export standalone single-file offline HTML:
```bash
python run_dashboard.py --export-html
```

### 2. Market Intelligence CLI
```bash
# Analyze verified active listings (last 30 days, min 2 photos)
python analyze_data.py

# High-rise condominium towers only (>= 6 floors)
python analyze_data.py --high-rise

# Specific sector analysis
python analyze_data.py --sector "Sector 43"

# Run price variance outlier audit (> 40% discount below sector median)
python analyze_data.py --audit-outliers
```

### 3. Sourcing & Aggregation Pipeline
```bash
# Ingest listings across all 169 sectors
python run_pipeline.py --all-sectors --nb-pages 1

# Deep targeted sweep on central core sectors (DLF Phase 1-5, Golf Course Rd, Sec 14-57)
python run_pipeline.py --central-only --bhk 2 --nb-pages 3

# Re-run deduplication and recompile dashboard without re-scraping
python run_pipeline.py --skip-scrape
```

---

## 📁 Repository Structure

```
├── index.html               # Production standalone dashboard (for GitHub Pages root)
├── dashboard.html           # Compiled portable single-file offline dashboard
├── run_dashboard.py         # Local dashboard server & standalone exporter
├── run_pipeline.py          # Master CLI orchestrator
├── analyze_data.py          # CLI Market Intelligence Analyzer
├── agents.md                # Authoritative Technical Reference Manual
├── config/
│   ├── gurgaon_sectors.geojson # 169 GMDA Master Plan 2031 sector polygons
│   ├── gurgaon_transit.geojson # Transit network vectors
│   └── sectors.yaml         # Sector crawl seeds & aliases
├── web/
│   ├── index.html           # Modular web dashboard
│   ├── dashboard.js         # Reactive client-side filtering engine
│   ├── style.css            # Custom dashboard typography & animations
│   └── data.json            # Compiled dataset (169 polygons + verified listings)
└── src/
    ├── db.py                # SQLite schemas & idempotent upserts
    ├── boundary_validator.py# Multi-tier boundary verification engine
    ├── consolidator.py      # 5-point deduplication & PIP sector engine
    ├── analyzer.py          # Market rate calculation & outlier engine
    ├── dashboard_data.py    # Zero-lag GeoJSON compiler
    ├── collectors/          # REST & headless browser collectors (NoBroker, Housing, 99acres)
    └── normalizers/         # Regex filters & RapidFuzz society normalizer
```

---

## 📜 License
MIT License. Built for personal property research and market intelligence in Gurgaon, India.
