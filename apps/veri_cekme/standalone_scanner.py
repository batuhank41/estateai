"""
standalone_scanner.py — FastAPI gerektirmeden çalışan tek dosya
İlk argüman: district, ikinci: prop_type, üçüncü: listing_type (opsiyonel)
Çıktı: JSON listesi (stdout'a basılır)
"""
import json
import sys
import os
import logging

# Yerel modüllere erişim için
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)

from fsbo_scraper import scrape_fsbo

if __name__ == "__main__":
    district = sys.argv[1] if len(sys.argv) > 1 else "Kocaeli"
    print(f"\n--- STANDALONE SCANNER START: {district} ---", file=sys.stderr, flush=True)
    prop_type = sys.argv[2] if len(sys.argv) > 2 else "daire"
    listing_type = sys.argv[3] if len(sys.argv) > 3 else "satilik"
    city = sys.argv[4] if len(sys.argv) > 4 else ""
    neighborhood = sys.argv[5] if len(sys.argv) > 5 else ""
    min_price = sys.argv[6] if len(sys.argv) > 6 else None
    max_price = sys.argv[7] if len(sys.argv) > 7 else None
    room_count = sys.argv[8] if len(sys.argv) > 8 else None

    try:
        results = scrape_fsbo(
            district, prop_type, listing_type, 
            city=city, neighborhood=neighborhood,
            min_price=min_price, max_price=max_price, room_count=room_count
        )
        sys.stdout.buffer.write(json.dumps(results, ensure_ascii=True).encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
