import sys
import os
import logging

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fsbo_scraper import scrape_fsbo

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Test with a known active district
    results = scrape_fsbo(city="Istanbul", district="Kadikoy", today_only=True)
    print(f"\nFound {len(results)} today's sahibinden listings.")
    for r in results[:3]:
        print(f"- {r['title']} | {r['date']} | {r['price_text']}")
