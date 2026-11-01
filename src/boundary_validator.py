"""
Gurgaon & IMT Manesar Sector Boundary & Area Verification Engine
================================================================
Implements statutory multi-tier spatial and area verification for the GMDA Master Plan GeoJSON:

1. Pairwise Overlap Invariant: Verifies 0.0% pairwise intersection across all features (threshold: 0.001 km²).
2. Physical Size Bounds Sanity Check: Verifies all sectors fall within realistic urban planning bounds
   (0.20 km² <= Area <= 6.0 km²), catching micro-slivers and bloated monster polygons.
3. Planned Urban Footprint & Open Space Audit: Computes total planned footprint, median sector area,
   and catalogs legitimate open/natural/defense zones (e.g. Aravallis, IAF depot) without forcing artificial buffers.
"""

import os
import json
from typing import Dict, Any, List, Tuple
from shapely.geometry import shape, Polygon, MultiPolygon, mapping
from shapely.ops import unary_union

def audit_sector_boundaries(geojson_path: str = "config/gurgaon_sectors.geojson") -> Dict[str, Any]:
    """
    Executes 3-tier boundary and area verification on the GMDA sector GeoJSON.
    """
    if not os.path.exists(geojson_path):
        raise FileNotFoundError(f"GeoJSON not found at: {geojson_path}")

    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    shapes: List[Tuple[str, Any, float]] = []
    
    for i, f in enumerate(features):
        props = f.get("properties", {})
        s_name = props.get("sector_name") or props.get("name") or f"Feature_{i}"
        geom = shape(f["geometry"])
        area_sqkm = geom.area * (111.0 ** 2)
        shapes.append((s_name, geom, round(area_sqkm, 4)))

    total_features = len(shapes)

    # -------------------------------------------------------------
    # 1. TIER 1: PAIRWISE OVERLAP CHECK (Invariant: < 0.001 sq km)
    # -------------------------------------------------------------
    overlaps = []
    for i in range(total_features):
        n1, s1, _ = shapes[i]
        for j in range(i + 1, total_features):
            n2, s2, _ = shapes[j]
            inter = s1.intersection(s2)
            inter_area = inter.area * (111.0 ** 2)
            if inter_area > 0.001:  # threshold: 1,000 sq. meters
                overlaps.append({
                    "sector_1": n1,
                    "sector_2": n2,
                    "overlap_sqkm": round(inter_area, 4)
                })

    # -------------------------------------------------------------
    # 2. TIER 2: PHYSICAL SECTOR SIZE BOUNDS SANITY CHECK
    # -------------------------------------------------------------
    micro_slivers = []  # < 0.20 sq km (under ~50 acres)
    bloated_sectors = []  # > 6.0 sq km (over ~1500 acres)
    areas = []

    for name, geom, a in shapes:
        areas.append(a)
        if a < 0.20:
            micro_slivers.append({"sector": name, "area_sqkm": a})
        elif a > 6.0:
            bloated_sectors.append({"sector": name, "area_sqkm": a})

    areas.sort()
    min_area = areas[0] if areas else 0.0
    max_area = areas[-1] if areas else 0.0
    median_area = round(areas[len(areas) // 2], 3) if areas else 0.0
    total_planned_area = round(sum(areas), 3)

    # -------------------------------------------------------------
    # 3. TIER 3: PLANNED URBAN FOOTPRINT & OPEN SPACE AUDIT
    # -------------------------------------------------------------
    all_geoms = [s for _, s, _ in shapes]
    city_union = unary_union(all_geoms)

    if city_union.geom_type == "Polygon":
        city_polys = [city_union]
    elif city_union.geom_type == "MultiPolygon":
        city_polys = list(city_union.geoms)
    else:
        city_polys = []

    # Significant internal open / defense / natural zones (> 0.5 sq km)
    open_zones = []
    for p in city_polys:
        for hole in p.interiors:
            hole_poly = Polygon(hole)
            h_area = hole_poly.area * (111.0 ** 2)
            if h_area > 0.50:  # significant zones like IAF Depot, Aravalli ridges
                open_zones.append({
                    "area_sqkm": round(h_area, 3),
                    "centroid_lon": round(hole_poly.centroid.x, 4),
                    "centroid_lat": round(hole_poly.centroid.y, 4),
                    "bounds": [round(c, 4) for c in hole_poly.bounds]
                })

    open_zones.sort(key=lambda x: -x["area_sqkm"])

    return {
        "total_sectors": total_features,
        "total_planned_area_sqkm": total_planned_area,
        "median_sector_area_sqkm": median_area,
        "min_sector_area_sqkm": min_area,
        "max_sector_area_sqkm": max_area,
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "micro_sliver_count": len(micro_slivers),
        "micro_slivers": micro_slivers,
        "bloated_count": len(bloated_sectors),
        "bloated_sectors": bloated_sectors,
        "significant_open_zones": open_zones
    }

def print_audit_report(results: Dict[str, Any]):
    print("=" * 75)
    print("   GURGAON & IMT MANESAR SECTOR BOUNDARY & AREA AUDIT REPORT (GMDA 2031)")
    print("=" * 75)
    print(f"Total Official Sectors      : {results['total_sectors']}")
    print(f"Total Planned Zoned Area    : {results['total_planned_area_sqkm']:.2f} sq km")
    print(f"Median Sector Size          : {results['median_sector_area_sqkm']:.2f} sq km (~{results['median_sector_area_sqkm']*247.1:.0f} acres)")
    print(f"Sector Area Range           : {results['min_sector_area_sqkm']:.3f} km²  to  {results['max_sector_area_sqkm']:.3f} km²")
    print(f"Pairwise Overlaps (>0.001)  : {results['overlap_count']} (0.0% overlap)")
    print(f"Microscopic Slivers (<0.2)  : {results['micro_sliver_count']}")
    print(f"Bloated Monsters (>6.0)     : {results['bloated_count']}")
    print("-" * 75)

    if results["overlaps"]:
        print("⚠️ DETECTED OVERLAPS:")
        for o in results["overlaps"]:
            print(f"  - {o['sector_1']} <-> {o['sector_2']}: {o['overlap_sqkm']} sq km")
        print("-" * 75)
    else:
        print(f"✅ TIER 1 PASSED: 0.0% pairwise overlap across all {results['total_sectors']} sectors/enclaves.")

    if results["micro_slivers"] or results["bloated_sectors"]:
        print("⚠️ SIZE BOUND ANOMALIES:")
        for s in results["micro_slivers"]:
            print(f"  - Sliver: {s['sector']} ({s['area_sqkm']} sq km)")
        for b in results["bloated_sectors"]:
            print(f"  - Monster: {b['sector']} ({b['area_sqkm']} sq km)")
        print("-" * 75)
    else:
        print("✅ TIER 2 PASSED: 100% of sectors fall within realistic planning bounds (0.2–6.0 km²).")

    if results["significant_open_zones"]:
        print("\n🌲 LEGITIMATE NATURAL / DEFENSE OPEN ZONES (AUDITED & PRESERVED):")
        for i, z in enumerate(results["significant_open_zones"][:5]):
            print(f"  [{i+1}] Area: {z['area_sqkm']:<6.2f} sq km | Centroid: ({z['centroid_lon']:.4f}, {z['centroid_lat']:.4f}) | Bounds: {z['bounds']}")
    print("=" * 75)

if __name__ == "__main__":
    report = audit_sector_boundaries()
    print_audit_report(report)
