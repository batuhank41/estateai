import sys
import json
import os
import traceback
import io

# Force UTF-8 encoding for stdout to prevent UnicodeEncodeError with emojis
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def run_valuation():
    try:
        from valuation_engine import scrape_web_comparables
        address_text = sys.argv[2]
        rooms = sys.argv[3]
        area_m2 = float(sys.argv[4]) if sys.argv[4] != 'None' else None
        prop_type = sys.argv[5]
        
        result = scrape_web_comparables(address_text, rooms, area_m2, prop_type)
        print("===JSON_START===")
        print(json.dumps(result))
        print("===JSON_END===")
    except Exception as e:
        print("===JSON_START===")
        print(json.dumps({"error": str(e), "trace": traceback.format_exc()}))
        print("===JSON_END===")

def run_scanner():
    try:
        from fsbo_scraper import scrape_fsbo
        city = sys.argv[2]
        district = sys.argv[3]
        prop_type = sys.argv[4]
        listing_type = "satılık"
        
        results = scrape_fsbo(district, prop_type, listing_type)
        print("===JSON_START===")
        print(json.dumps(results))
        print("===JSON_END===")
    except Exception as e:
        print("===JSON_START===")
        print(json.dumps({"error": str(e), "trace": traceback.format_exc()}))
        print("===JSON_END===")

if __name__ == "__main__":
    if sys.argv[1] == "valuation":
        run_valuation()
    elif sys.argv[1] == "scanner":
        run_scanner()
