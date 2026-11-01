#!/usr/bin/env python3
"""
CLI Entrypoint for Gurgaon Rental Data Analysis.
Wraps the modular src.analyzer.MarketAnalyzer engine.
"""
import argparse
from src.analyzer import MarketAnalyzer

def main():
    parser = argparse.ArgumentParser(description="Gurgaon Rental Market Intelligence Analyzer")
    parser.add_argument("--source", choices=["consolidated", "raw"], default="consolidated", help="Source table to analyze (default: consolidated)")
    parser.add_argument("--all", action="store_true", help="Include independent builder floors and standalone properties (Default: Named societies/complexes only)")
    parser.add_argument("--high-rise", action="store_true", help="Filter strictly to High-Rise Multi-Story Apartments (total_floors > 5)")
    parser.add_argument("--min-floors", type=int, default=None, help="Filter properties with total_floors >= N (e.g. 6 for > 5 floors)")
    parser.add_argument("--max-age-days", type=int, default=30, help="Filter listings active/updated within the last N days (default: 30 days)")
    parser.add_argument("--all-time", action="store_true", help="Disable the 30-day recency filter and include all historical listings")
    parser.add_argument("--posted-after", type=str, default=None, help="Filter listings posted/updated on or after YYYY-MM-DD")
    parser.add_argument("--sector", type=str, default=None, help="Filter listings strictly to a specific official HUDA Sector (e.g. 'Sector 43', 'Sector 28')")
    parser.add_argument("--min-photos", type=int, default=2, help="Filter listings with at least N photos (default: 2)")
    parser.add_argument("--all-photos", action="store_true", help="Include listings with zero or single static photos")
    parser.add_argument("--audit-outliers", action="store_true", help="Run automated Sector Variance & Outlier Diagnostic report")
    parser.add_argument("--variance-threshold", type=float, default=40.0, help="Variance discount threshold percentage below sector baseline (default: 40.0%%)")
    args = parser.parse_args()

    analyzer = MarketAnalyzer()

    if args.audit_outliers:
        analyzer.print_outlier_audit(
            threshold_percent=args.variance_threshold,
            source=args.source,
            sector=args.sector
        )
        return

    min_flrs = args.min_floors
    if args.high_rise:
        min_flrs = 6

    max_age = None if args.all_time else args.max_age_days
    min_photos = None if args.all_photos else args.min_photos

    analyzer.print_analysis(
        source=args.source,
        gated_only=not args.all,
        min_floors=min_flrs,
        max_age_days=max_age,
        posted_after=args.posted_after,
        sector=args.sector,
        min_photos=min_photos
    )

if __name__ == "__main__":
    main()
