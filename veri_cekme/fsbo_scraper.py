"""
fsbo_scraper.py — Hepsiemlak Üzerinden Sahibinden İlan Tarayıcı (Fırsat Radarı)
=============================================================================
Strateji (radar.txt'ye uygun):
  1. SmartLocator ile il/ilçe/mahalle → Hepsiemlak URL'sine çevir
  2. URL'ye -sahibinden ekleyerek sahibinden ilanlarını filtrele
  3. sortField=UPDATED_DATE&sortDirection=DESC ile en yeni ilanları getir
  4. DrissionPage kullanarak headless modda tarama yap
  5. '.list-view-date' sınıfındaki metni okuyarak "bugün", "yeni" veya günün tarihini içermeyenleri ele
"""
import logging
import re
import json
import time
import sys
import random
from datetime import datetime
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions
from smart_locator import SmartLocator

log = logging.getLogger(__name__)

def _get_drission_page():
    """DrissionPage için bot korumalı arka plan tarayıcısı ayarlar."""
    co = ChromiumOptions()
    co.set_argument('--headless=new')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-blink-features=AutomationControlled')
    # User agent ekleyelim
    co.set_user_agent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    page = ChromiumPage(co)
    return page

def _is_today(date_text: str) -> bool:
    """İlan tarihinin bugün olup olmadığını radar.txt kriterlerine göre kontrol et."""
    if not date_text:
        return False
    
    # Türkçe karakterler için güvenli küçük harf çevrimi
    date_text = date_text.lower().replace('İ', 'i').replace('I', 'ı').strip()
    
    tr_aylar = {
        1: "ocak", 2: "şubat", 3: "mart", 4: "nisan", 5: "mayıs", 6: "haziran",
        7: "temmuz", 8: "ağustos", 9: "eylül", 10: "ekim", 11: "kasım", 12: "aralık"
    }
    now = datetime.now()
    
    # 1. Klasik Anahtar Kelimeler
    keywords = ["bugün", "yeni", "saat", "dakika", "sn"]
    
    if now.hour < 6:
        keywords.append("dün")

    if any(x in date_text for x in keywords):
        return True
    
    # 3. Tarihsel Eşleşme (Örn: "10 Mayıs", "10/05/2026", "10.05.2026")
    gunun_tarihi_tr = f"{now.day:02d} {tr_aylar[now.month]}"
    gunun_tarihi_kisa = f"{now.day} {tr_aylar[now.month]}"
    gunun_tarihi_iso = now.strftime("%d/%m/%Y")
    gunun_tarihi_nokta = now.strftime("%d.%m.%Y")
    
    date_matches = [gunun_tarihi_tr, gunun_tarihi_kisa, gunun_tarihi_iso, gunun_tarihi_nokta]
    if any(x in date_text for x in date_matches):
        return True
    
    return False

def scrape_fsbo(district: str, prop_type: str = "daire", listing_type: str = "satilik",
                max_results: int = 10, city: str = "", neighborhood: str = "",
                today_only: bool = True, company_id: str = "default",
                min_price: str = None, max_price: str = None, 
                room_count: str = None, building_age: str = None,
                floor_level: str = None, heating_type: str = None,
                site_status: str = None, furnished_status: str = None) -> list:
    """
    Hepsiemlak'tan DrissionPage kullanarak sahibinden ilanlarını çek (Fırsat Radarı).
    """
    results = []
    
    # 1. URL İnşası
    locator = SmartLocator()
    base_slug = locator.get_url(city, district, neighborhood)
    islem_tipi = "satilik" if "sat" in listing_type.lower() else "kiralik"
    clean_slug = base_slug.replace("-satilik", "").replace("-kiralik", "").replace("-sahibinden", "")
    target_slug = f"{clean_slug}-{islem_tipi}-sahibinden"
    
    # Query Parametreleri
    from urllib.parse import urlencode
    query_params = {
        "sortField": "UPDATED_DATE",
        "sortDirection": "DESC",
        "p33": "1"
    }
    
    if min_price: query_params["p31"] = re.sub(r'[^0-9]', '', str(min_price))
    if max_price: query_params["p32"] = re.sub(r'[^0-9]', '', str(max_price))
    
    oda_id_map = {
        "1+0": "180401", "1+1": "180402", "2+1": "180404", "2+2": "180405",
        "3+1": "180407", "3+2": "180408", "4+1": "180409", "4+2": "180410",
        "5+1": "180411", "5+2": "180412", "6+1": "180413"
    }
    if room_count in oda_id_map:
        query_params["p63"] = oda_id_map[room_count]

    age_query_map = {
        "0": "0-0", "1-5": "1-5", "6-10": "6-10", "11-15": "11-15",
        "16-20": "16-20", "21-25": "21-25", "26-30": "26-30", "31-35": "31-35",
        "36-40": "36-40", "40+": "40-100"
    }
    if building_age in age_query_map:
        query_params["buildingAges"] = age_query_map[building_age]
    
    floor_map = {"bahce": "101207", "giris": "101201", "ara": "101202", "enust": "101212"}
    if floor_level in floor_map: query_params["p7"] = floor_map[floor_level]
        
    heating_map = {"kombi": "101304", "merkezi": "101301", "yerden": "101303"}
    if heating_type in heating_map: query_params["p10"] = heating_map[heating_type]

    if site_status == "true": query_params["p69"] = "true"
    elif site_status == "false": query_params["p69"] = "false"
    
    if furnished_status == "true": query_params["p75"] = "true"
    elif furnished_status == "false": query_params["p75"] = "false"

    encoded_params = urlencode(query_params)
    url = f"https://www.hepsiemlak.com/{target_slug}?{encoded_params}"
    
    print(f"\n[FIRSAT RADARI] Hedef URL: {url}", file=sys.stderr, flush=True)

    page = None
    try:
        page = _get_drission_page()
        page.get(url)
        page.wait.load_start()
        time.sleep(random.uniform(4.0, 6.0))
        
        current_url = page.url
        if current_url != url:
            print(f"[FIRSAT RADARI] Yönlendirme Tespit Edildi: {current_url}", file=sys.stderr, flush=True)
        
        # 2. Kesin Sonuç Kontrolü
        has_zero_result = page.ele('css:.zero-result', timeout=2)
        if has_zero_result:
            print("[FIRSAT RADARI] KESİN UYARI: Sıfır sonuç tespit edildi.", file=sys.stderr, flush=True)
            return []

        # Sayfayı kaydır
        page.scroll.down(1000)
        time.sleep(1)
        
        # İlan kartlarını bul
        items = page.eles('css:.listingView__card-link, .listing-item, .listing-card, [class*="card"]', timeout=5)

        log.info(f"[FIRSAT RADARI] Sayfada toplam {len(items)} potansiyel ilan kartı tespit edildi.")
        print(f"[FIRSAT RADARI] Sayfada {len(items)} ilan kartı bulundu. İlk 50 ilan inceleniyor...", file=sys.stderr, flush=True)

        match_count = 0
        seen_urls = set()

        for item in items[:50]:
            try:
                # 4. Link (Önce linki alalım ki mükerrer kontrolü yapabilelim)
                if item.tag == 'a':
                    href = item.attr('href')
                else:
                    link_el = item.ele('tag:a', timeout=0.5)
                    href = link_el.attr('href') if link_el else ""
                
                if not href:
                    continue
                
                if not href.startswith('http'):
                    href = "https://www.hepsiemlak.com" + href
                
                # Mükerrer kontrolü
                clean_url = href.split("?")[0]
                if clean_url in seen_urls:
                    continue
                
                # 1. Tarih bilgisini al
                date_el = item.ele('css:time', timeout=0.5) or \
                          item.ele('css:.list-view-date', timeout=0.2) or \
                          item.ele('css:.listingView__card-date', timeout=0.2) or \
                          item.ele('css:.listing-card--date', timeout=0.2) or \
                          item.ele('css:.date', timeout=0.2)
                
                date_text = date_el.text if date_el else ""
                
                if not date_text:
                    full_text = item.text.lower()
                    if any(x in full_text for x in ["bugün", "saat", "dakika", "sn", "yeni"]):
                        date_text = "Bugün (Metinden)"
                
                # Tarih filtresi
                if today_only and not _is_today(date_text):
                    continue
                
                if not date_text:
                    continue

                # 2. Fiyat
                price_el = item.ele('css:.list-view-price', timeout=0.5) or \
                           item.ele('css:.listingView__card-price', timeout=0.2) or \
                           item.ele('css:.price', timeout=0.2) or \
                           item.ele('tag:span[class*="price"]', timeout=0.2)
                
                price_raw = price_el.text if price_el else "—"
                price_clean = re.sub(r'[^0-9]', '', price_raw)
                try:
                    if price_clean:
                        price_text = "{:,.0f}".format(float(price_clean)).replace(",", ".") + " TL"
                    else:
                        price_text = price_raw
                except:
                    price_text = price_raw
                
                # 3. Başlık
                title_el = item.ele('css:.list-view-header', timeout=0.5) or \
                           item.ele('css:.listingView__card-title', timeout=0.2) or \
                           item.ele('css:.title', timeout=0.2) or \
                           item.ele('tag:h3', timeout=0.2)
                title = title_el.text if title_el else "Sahibinden İlan"

                # 5. Lokasyon
                loc_el = item.ele('css:.list-view-location', timeout=0.5) or \
                         item.ele('css:.listingView__card-location', timeout=0.2) or \
                         item.ele('css:.location', timeout=0.2) or \
                         item.ele('tag:address', timeout=0.2)
                location = loc_el.text if loc_el else (district or city)

                results.append({
                    "title": title,
                    "source_url": href,
                    "location": location,
                    "price_text": price_text,
                    "raw_snippet": f"{title} · {location} · {date_text}".strip(" ·"),
                    "source_site": "hepsiemlak",
                    "date": date_text,
                })
                seen_urls.add(clean_url)

                # DB Kayıt
                try:
                    import sqlite3
                    db_path = Path(__file__).parent / "emlak.db"
                    with sqlite3.connect(str(db_path)) as conn:
                        conn.execute("CREATE TABLE IF NOT EXISTS scanned_listings (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT, ilan_id TEXT UNIQUE, baslik TEXT, fiyat TEXT, tarih_temiz TEXT, bolge_url TEXT, ilan_link TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
                        ilan_id = href.split("/")[-1].split("?")[0] if href else str(random.random())
                        conn.execute("INSERT OR IGNORE INTO scanned_listings (company_id, ilan_id, baslik, fiyat, tarih_temiz, bolge_url, ilan_link) VALUES (?, ?, ?, ?, ?, ?, ?)", (company_id, ilan_id, title, price_text, date_text, location, href))
                        conn.commit()
                except Exception as db_err:
                    log.warning(f"DB Kayit Hatasi: {db_err}")

                if len(results) >= max_results:
                    break
            except Exception as e:
                log.debug(f"İlan parse hatası: {e}")
                continue

    except Exception as e:
        log.error(f"[FIRSAT RADARI] Hata: {e}")
    finally:
        if page:
            try:
                page.quit()
            except:
                pass

    if not results:
        print(f"[FIRSAT RADARI] UYARI: Hiç ilan bulunamadı veya kriterlere uyan (Bugün) ilan yok.", file=sys.stderr, flush=True)
    else:
        print(f"[FIRSAT RADARI] BAŞARILI: {len(results)} yeni ilan kriterlere uygun bulundu.", file=sys.stderr, flush=True)
    
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    test_res = scrape_fsbo("Kocaeli", "daire")
