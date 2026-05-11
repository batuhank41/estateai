"""
fsbo_scraper.py — Hepsiemlak Üzerinden Sahibinden İlan Tarayıcı (Fırsat Radarı)
=============================================================================
Strateji (radar.txt'ye uygun):
  1. SmartLocator ile il/ilçe/mahalle → Hepsiemlak URL'sine çevir
  2. URL'ye -sahibinden ekleyerek sahibinden ilanlarını filtrele
  3. sortField=UPDATED_DATE&sortDirection=DESC ile en yeni ilanları getir
  4. DrissionPage kullanarak headless modda tarama yap (Human-like behavior)
  5. '.list-view-date' sınıfındaki metni okuyarak "bugün", "yeni" kontrolü yap
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
    # Headless modda bot tespitini zorlaştırmak için ayarlar
    co.set_argument('--headless=new')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--disable-infobars')
    
    # Gerçekçi bir User Agent
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    co.set_user_agent(ua)
    
    page = ChromiumPage(co)
    # Navigator.webdriver özelliğini gizle
    page.run_js('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
    return page

def _is_today(date_text: str) -> bool:
    """İlan tarihinin bugün olup olmadığını radar.txt kriterlerine göre kontrol et."""
    if not date_text:
        return False
    
    date_text = date_text.lower().replace('İ', 'i').replace('I', 'ı').strip()
    
    tr_aylar = {
        1: "ocak", 2: "şubat", 3: "mart", 4: "nisan", 5: "mayıs", 6: "haziran",
        7: "temmuz", 8: "ağustos", 9: "eylül", 10: "ekim", 11: "kasım", 12: "aralık"
    }
    now = datetime.now()
    
    # 1. Klasik Anahtar Kelimeler
    keywords = ["bugün", "yeni", "saat", "dakika", "sn", "saniye"]
    if now.hour < 6: # Gece yarısından sonra düne bakabiliriz
        keywords.append("dün")

    if any(x in date_text for x in keywords):
        return True
    
    # 3. Tarihsel Eşleşme
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
    Daha insansı (human-like) davranışlar eklenmiştir.
    """
    results = []
    
    # 1. URL İnşası
    locator = SmartLocator()
    base_slug = locator.get_url(city, district, neighborhood)
    islem_tipi = "satilik" if "sat" in listing_type.lower() else "kiralik"
    
    # Slug temizleme ve inşası
    clean_slug = base_slug.replace("-satilik", "").replace("-kiralik", "").replace("-sahibinden", "")
    target_slug = f"{clean_slug}-{islem_tipi}-sahibinden"
    
    from urllib.parse import urlencode
    query_params = {
        "sortField": "UPDATED_DATE",
        "sortDirection": "DESC",
        "p33": "1" # Sahibinden filtresi
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

    encoded_params = urlencode(query_params)
    url = f"https://www.hepsiemlak.com/{target_slug}?{encoded_params}"
    
    print(f"\n[WEB TARAMA] Hedef URL: {url}", file=sys.stderr, flush=True)

    page = None
    try:
        page = _get_drission_page()
        page.get(url)
        
        # İnsansı bekleme ve yükleme
        print("[WEB TARAMA] Sayfa yukleniyor...", file=sys.stderr, flush=True)
        page.wait.load_start()
        time.sleep(random.uniform(4.0, 7.0))
        
        # 2. İnsansı Kaydırma (Kademeli ve Rastgele)
        print("[WEB TARAMA] Insansi kaydirma (scrolling) yapiliyor...", file=sys.stderr, flush=True)
        for _ in range(random.randint(3, 5)):
            scroll_amount = random.randint(300, 700)
            page.scroll.down(scroll_amount)
            time.sleep(random.uniform(0.7, 1.8))
        
        # İlan kartlarını bul
        # .listingView__card-link en güncel ve kapsayıcı olanı
        items = page.eles('css:a.listingView__card-link', timeout=15)

        if not items or len(items) == 0:
            # Alternatif: Sayfayı yenileyip daha uzun bekle
            print("[WEB TARAMA] İlan kartları henüz görünmedi, sayfa tazeleniyor...", file=sys.stderr, flush=True)
            page.refresh()
            time.sleep(8)
            items = page.eles('css:a.listingView__card-link', timeout=15)

        print(f"[WEB TARAMA] Sayfada {len(items)} ilan kartı bulundu. Analiz ediliyor...", file=sys.stderr, flush=True)

        seen_urls = set()
        for item in items:
            try:
                href = item.attr('href')
                if not href: continue
                
                if not href.startswith('http'):
                    href = "https://www.hepsiemlak.com" + href
                
                clean_url = href.split("?")[0]
                if clean_url in seen_urls: continue
                
                # 1. Tarih (time.list-view-date)
                date_el = item.ele('css:time.list-view-date', timeout=0.5) or \
                          item.ele('css:.list-view-date', timeout=0.2) or \
                          item.ele('css:time', timeout=0.2)
                
                date_text = date_el.text if date_el else ""
                
                # Tarih filtresi
                if today_only and not _is_today(date_text):
                    # "bugün" kelimesini içermiyorsa ama "yeni" vb varsa kabul edebiliriz
                    if not any(x in item.text.lower() for x in ["bugün", "yeni", "dakika"]):
                        continue
                
                # 2. Fiyat
                price_el = item.ele('css:.list-view-price strong', timeout=0.5) or \
                           item.ele('css:.list-view-price', timeout=0.2) or \
                           item.ele('css:.price', timeout=0.2)
                
                price_raw = price_el.text if price_el else "—"
                price_clean = re.sub(r'[^0-9]', '', price_raw)
                
                # 3. Başlık
                title_el = item.ele('tag:h3', timeout=0.5) or \
                           item.ele('css:.list-view-header', timeout=0.2)
                title = title_el.text if title_el else "Sahibinden İlan"

                # 4. Lokasyon
                loc_el = item.ele('css:address.list-view-location', timeout=0.5) or \
                         item.ele('css:.list-view-location', timeout=0.2)
                location = loc_el.text if loc_el else district

                results.append({
                    "title": title,
                    "source_url": href,
                    "location": location,
                    "price_text": f"{price_clean} TL" if price_clean else price_raw,
                    "raw_snippet": f"{title} · {location} · {date_text}".strip(" ·"),
                    "source_site": "hepsiemlak",
                    "date": date_text or "Bugün",
                })
                seen_urls.add(clean_url)

                if len(results) >= max_results:
                    break
            except Exception:
                continue

    except Exception as e:
        print(f"[WEB TARAMA] Kritik Hata: {e}", file=sys.stderr, flush=True)
    finally:
        if page:
            try: page.quit()
            except: pass

    if not results:
        print(f"[WEB TARAMA] UYARI: Hiç ilan bulunamadı. Bloklanmış olabiliriz veya bugün yeni ilan yok.", file=sys.stderr, flush=True)
    else:
        print(f"[WEB TARAMA] BAŞARILI: {len(results)} yeni ilan bulundu.", file=sys.stderr, flush=True)
    
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    scrape_fsbo("Fatih", "daire")
