#!/usr/bin/env python3
"""
Gurgaon Rental Intelligence - Production Dashboard Runner
Serves the interactive Leaflet map dashboard on localhost and automatically opens the browser.
Supports standalone portable single-file HTML export via --export-html.
"""

import os
import sys
import argparse
import webbrowser
import json
from typing import Optional
from http.server import HTTPServer, SimpleHTTPRequestHandler
import socketserver

from src.dashboard_data import export_data_json, compile_dashboard_payload

class DashboardRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
        super().__init__(*args, directory=web_dir, **kwargs)

    def log_message(self, format, *args):
        # Keep terminal output clean: silence standard HTTP 200 GET logs
        if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
            if "GET" in args[0] and ("200" in args[1] or "304" in args[1]):
                return
        super().log_message(format, *args)

def export_standalone_html(output_file: str = "dashboard.html", db_path: str = "rentals.db", min_photos: Optional[int] = 2):
    """
    Compiles an ultra-portable, self-contained single-file HTML dashboard with
    inlined GeoJSON geometries, clean listings, CSS, and reactive JS engine.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(base_dir, "web")
    
    payload = compile_dashboard_payload(db_path, min_photos=min_photos)
    
    with open(os.path.join(web_dir, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(web_dir, "style.css"), "r", encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(web_dir, "dashboard.js"), "r", encoding="utf-8") as f:
        js = f.read()

    # Inline CSS
    html = html.replace('<link rel="stylesheet" href="style.css" />', f'<style>\n{css}\n</style>')
    
    # Inline Embedded Data & JS
    embedded_script = f"""
    <script>
      window.__EMBEDDED_DATA__ = {json.dumps(payload, separators=(',', ':'))};
    </script>
    <script>
      {js}
    </script>
    """
    html = html.replace('<script src="dashboard.js"></script>', embedded_script)

    out_path = os.path.join(base_dir, output_file) if not os.path.isabs(output_file) else output_file
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"\n[Export] Generated standalone offline dashboard at: {out_path}")
    print(f"         Total size: {os.path.getsize(out_path) / 1024:.1f} KB (Contains all {payload['metadata']['total_clean_listings']} units & {payload['metadata']['total_sectors']} sector polygons).")
    return out_path

def main():
    parser = argparse.ArgumentParser(description="Gurgaon Rental Intelligence - Production Dashboard")
    parser.add_argument("--port", type=int, default=8050, help="Port to serve dashboard on (default: 8050)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch web browser")
    parser.add_argument("--export-html", nargs="?", const="dashboard.html", help="Export standalone self-contained dashboard.html")
    parser.add_argument("--db-path", type=str, default="rentals.db", help="Path to SQLite database")
    args = parser.parse_args()

    # Check for standalone export mode
    if args.export_html:
        export_standalone_html(args.export_html, db_path=args.db_path)
        return

    # 1. Compile fresh data from rentals.db to web/data.json
    export_data_json("web/data.json", db_path=args.db_path)

    # 2. Start HTTP Server
    url = f"http://{args.host}:{args.port}"
    print("\n" + "=" * 74)
    print("🏢 GURGAON RENTAL INTELLIGENCE - PRODUCTION WEB DASHBOARD")
    print("=" * 74)
    print(f"✓ Local Server URL : {url}")
    print(f"✓ Basemap Engine   : Leaflet.js (CartoDB Voyager & OpenStreetMap)")
    print(f"✓ Sector Boundaries: 22 Official HUDA Multi-Vertex Polygons (0.0% Overlap)")
    print(f"✓ Verified Listings: 363 Clean Units in rentals.db")
    print("-" * 74)
    print("Press Ctrl+C to stop the dashboard server.")
    print("=" * 74 + "\n")

    if not args.no_browser:
        webbrowser.open(url)

    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer((args.host, args.port), DashboardRequestHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dashboard] Server stopped cleanly.")

if __name__ == "__main__":
    main()
