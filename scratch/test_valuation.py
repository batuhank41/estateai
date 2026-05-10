import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'veri_cekme'))

from valuation_engine import scrape_web_comparables

def test():
    # Test with a known location
    address = "Kadikoy, Istanbul"
    rooms = "2+1"
    area = 100
    
    print(f"Testing valuation for {address}, {rooms}, {area}m2...")
    result = scrape_web_comparables(address, rooms, area, "daire")
    
    print("\nResult Status:", result.get('status'))
    print("URL used:", result.get('url'))
    print("Samples found:", len(result.get('samples', [])))
    
    if result.get('samples'):
        for s in result['samples'][:3]:
            print(f"- {s['title']}: {s['price']} TL")

if __name__ == "__main__":
    test()
