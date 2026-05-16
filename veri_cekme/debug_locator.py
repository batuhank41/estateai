
import sys
import os
from pathlib import Path

# Add veri_cekme to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from smart_locator import SmartLocator

def test():
    locator = SmartLocator()
    
    city = "İstanbul"
    district = "Üsküdar"
    neighborhood = ""
    
    url = locator.get_url(city, district, neighborhood)
    print(f"Result for {city}/{district}: {url}")

if __name__ == "__main__":
    test()
