
import sys
import os
from pathlib import Path

# veri_cekme dizinini path'e ekle
sys.path.append(str(Path(__file__).parent))

from valuation_engine import scrape_web_comparables

def test_url_generation():
    test_cases = [
        {
            "name": "Ümraniye Satılık Daire 3+1 Asansörlü",
            "address": "İstanbul Ümraniye",
            "rooms": "3+1",
            "area": 100,
            "prop_type": "Daire",
            "features": {"has_elevator": True}
        },
        {
            "name": "Üsküdar Satılık Daire 2+1 Eşyalı Site İçinde",
            "address": "Üsküdar",
            "rooms": "2+1",
            "area": 85,
            "prop_type": "Daire",
            "features": {"site_status": "true", "furnished_status": "true"}
        },
        {
            "name": "Kadıköy Göztepe 3+1 Otoparklı Havuzlu",
            "address": "İstanbul Kadıköy Göztepe",
            "rooms": "3+1",
            "area": 120,
            "prop_type": "Daire",
            "features": {"has_parking": True, "has_pool": True}
        },
        {
            "name": "Ankara Çankaya 4+1 Yeni Bina",
            "address": "Ankara Çankaya",
            "rooms": "4+1",
            "area": 150,
            "prop_type": "Daire",
            "features": {"building_age": "0"}
        },
        {
            "name": "İzmir Bornova m2 Fark Etmez",
            "address": "İzmir Bornova",
            "rooms": "1+1",
            "area": 0,
            "prop_type": "Daire",
            "features": {}
        }
    ]

    print("\n" + "="*50)
    print("HEPSIEMLAK URL GENERATION TEST REPORT")
    print("="*50 + "\n")

    for case in test_cases:
        print(f"TEST: {case['name']}")
        print(f"INPUTS: {case['address']}, {case['rooms']}, {case['area']}m2, {case['features']}")
        
        # scrape_web_comparables fonksiyonunu sadece URL üretmesi için simüle ediyoruz
        # (Gerçek tarama yapmaması için içine bir bayrak ekleyebilirdik ama şu an sadece URL'yi print eden kısmı alacağız)
        # Not: valuation_engine.py içinde print(f"[WEB TARAMA] Hedef URL: {url}") satırı var.
        
        # Gerçek fonksiyonu çağırıyoruz ama tarama kısmına gelmeden hata verebilir (driver olmadığı için)
        # Bu yüzden sadece URL üretme mantığını test etmek için mock'layabiliriz.
        
        from valuation_engine import SmartLocator
        locator = SmartLocator()
        
        # Adresten il-ilce-mah ayıkla (valuation_engine içindeki mantık)
        parts = case['address'].split()
        il = parts[0] if len(parts) > 0 else ""
        ilce = parts[1] if len(parts) > 1 else ""
        mahalle = " ".join(parts[2:]) if len(parts) > 2 else ""
        
        konum_yolu = locator.get_url(il, ilce, mahalle)
        
        # valuation_engine içindeki URL birleştirme mantığını buraya kopyalayalım (Hızlı test için)
        tip_map = {"Daire": "daire", "Villa": "villa", "Müstakil Ev": "mustakil-ev"}
        mulk_tipi_slug = tip_map.get(case['prop_type'], "daire")
        oda_slug = case['rooms'].replace("+", "-")
        
        path_base = f"{konum_yolu}-satilik"
        mulk_oda_slug = f"{mulk_tipi_slug}-{oda_slug}"
        
        query_params = []
        if case['area'] > 0:
            query_params.append(f"p25={case['area']}&p26={case['area']}")
            
        if case['features'].get('building_age'):
            query_params.append(f"buildingAges={case['features']['building_age']}")
            
        if case['features'].get('has_elevator'):
            query_params.append("elevator=true")
            
        if case['features'].get('has_parking'):
            query_params.append("parking=true")
            
        if case['features'].get('has_pool'):
            query_params.append("realtyAttributes=174,360")
            
        if case['features'].get('site_status') == "true":
            query_params.append("withinHousingEstate=true")
            
        if case['features'].get('furnished_status') == "true":
            query_params.append("furnished=true")
            
        query_string = "&".join(query_params)
        url = f"https://www.hepsiemlak.com/{path_base}/{mulk_oda_slug}"
        if query_string:
            url += f"?{query_string}"
            
        url = url.replace("//", "/").replace("https:/", "https://").replace(".com//", ".com/")
        
        print(f"GENERATED URL: {url}")
        print("-" * 30 + "\n")

if __name__ == "__main__":
    test_url_generation()
