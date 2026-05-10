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
    # radar.txt: "saat", "dakika", "yeni", "bugün" ifadeleri günceldir
    keywords = ["bugün", "yeni", "saat", "dakika", "sn"]
    
    # 2. Eğer sabahın çok erken saatlerindeysek (00:00 - 06:00 arası),
    # gece geç saatte girilen "Dün" ilanlarını da kaçırmayalım.
    if now.hour < 6:
        keywords.append("dün")

    if any(x in date_text for x in keywords):
        return True
    
    # 3. Tarihsel Eşleşme (Örn: "10 Mayıs", "10/05/2026")
    gunun_tarihi_tr = f"{now.day:02d} {tr_aylar[now.month]}"
    gunun_tarihi_kisa = f"{now.day} {tr_aylar[now.month]}"
    gunun_tarihi_iso = now.strftime("%d/%m/%Y")
    
    date_matches = [gunun_tarihi_tr, gunun_tarihi_kisa, gunun_tarihi_iso]
    if any(x in date_text for x in date_matches):
        return True
    
    return False

def scrape_fsbo(district: str, prop_type: str = "daire", listing_type: str = "satilik",
                max_results: int = 20, city: str = "", neighborhood: str = "",
                today_only: bool = True, company_id: str = "default",
                min_price: str = None, max_price: str = None, 
                room_count: str = None, building_age: str = None,
                floor_level: str = None, heating_type: str = None,
                site_status: str = None, furnished_status: str = None) -> list:
    """
    Hepsiemlak'tan DrissionPage kullanarak sahibinden ilanlarını çek (Fırsat Radarı).
    Gelişmiş filtreleme desteği eklendi (Fiyat, Oda, Yaş, Kat vb.).
    """
    results = []
    
    # 1. URL İnşası
    locator = SmartLocator()
    base_slug = locator.get_url(city, district, neighborhood)
    
    # Metin temizleme (radar.txt'ye uygun)
    islem_tipi = "satilik" if "sat" in listing_type.lower() else "kiralik"
    
    # base_slug içindeki takıları temizle
    clean_slug = base_slug.replace("-satilik", "").replace("-kiralik", "").replace("-sahibinden", "")
    
    # radar.txt Standart Yapı: {lokasyon}-{islem}-sahibinden
    target_slug = f"{clean_slug}-{islem_tipi}-sahibinden"
    
    # Query Parametreleri
    from urllib.parse import urlencode
    query_params = {
        "sortField": "UPDATED_DATE",
        "sortDirection": "DESC",
        "p33": "1" # KRİTİK: Hepsiemlak "Sahibinden" filtresini garantiye al (Slug yönlense bile bu çalışır)
    }
    
    # Fiyat Parametreleri
    if min_price: query_params["p31"] = re.sub(r'[^0-9]', '', str(min_price))
    if max_price: query_params["p32"] = re.sub(r'[^0-9]', '', str(max_price))
    
    # Bina Yaşı Parametreleri (p63)
    age_map = {
        "0": "180404", "1-5": "180407", "6-10": "180408", 
        "11-15": "180409", "16-20": "180410", "21-25": "180411", "26-30": "180412"
    }
    if building_age in age_map:
        query_params["p63"] = age_map[building_age]
    
    # Kat Seviyesi (p7)
    floor_map = {
        "bahce": "101207", "giris": "101201", "ara": "101202", "enust": "101212"
    }
    if floor_level in floor_map:
        query_params["p7"] = floor_map[floor_level]
        
    # Isınma (p10)
    heating_map = {
        "kombi": "101304", "merkezi": "101301", "yerden": "101303"
    }
    if heating_type in heating_map:
        query_params["p10"] = heating_map[heating_type]

    # Site & Furnished (p69 & p75)
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
        log.info(f"[FIRSAT RADARI] Sayfa yukleniyor: {url}")
        page.get(url)
        
        # Bekleme ve yüklenme kontrolü
        page.wait.load_start()
        time.sleep(random.uniform(4.0, 6.0))
        
        current_url = page.url
        if current_url != url:
            print(f"[FIRSAT RADARI] Yönlendirme Tespit Edildi: {current_url}", file=sys.stderr, flush=True)
        
        # 2. Kesin Sonuç Kontrolü (CSS Sınıf ve Metin Bazlı)
        # radar.txt: Gerçek sonuç yoksa öneri alanına girilmez.
        
        # Sınıf bazlı kontrol (En kesin yöntem)
        has_zero_result = page.ele('css:.zero-result')
        has_suggested_banner = page.ele('css:.suggested-search-banner')
        
        if has_zero_result or has_suggested_banner:
            print("[FIRSAT RADARI] KESİN UYARI: Sıfır sonuç veya Öneri bandı tespit edildi. Taramayı durduruyorum.", file=sys.stderr, flush=True)
            return []

        # Metin bazlı yedek kontrol
        no_results_keywords = ["0 ilan bulundu", "aradığınız kriterlere uygun ilan bulamadık"]
        page_content = page.html.lower()
        if any(kw in page_content for kw in no_results_keywords):
            print("[FIRSAT RADARI] Metin bazlı sıfır sonuç tespiti (Öneri ilanlar atlanıyor).", file=sys.stderr, flush=True)
            return []
        
        # Sayfayı kaydır (Yavaş ve kademeli)
        for _ in range(3):
            page.scroll.down(1000)
            time.sleep(1)
        
        # İlan kartlarını bul (Genişletilmiş Seçiciler)
        items = page.eles('css:.listing-item') or \
                page.eles('css:.listingView__card-link') or \
                page.eles('css:.listing-card') or \
                page.eles('css:[class*="card"]')

        log.info(f"[FIRSAT RADARI] Sayfada toplam {len(items)} ilan kartı tespit edildi.")
        print(f"[FIRSAT RADARI] Sayfada {len(items)} ilan kartı bulundu. Kriterlere göre inceleniyor...", file=sys.stderr, flush=True)

        match_count = 0

        for item in items:
            try:
                # 1. Tarih bilgisini al (Agresif Yaklaşım)
                date_el = item.ele('css:.list-view-date') or \
                          item.ele('css:.listingView__card-date') or \
                          item.ele('css:.listing-card--date') or \
                          item.ele('css:.date') or \
                          item.ele('tag:time')
                
                date_text = date_el.text if date_el else ""
                
                # Eğer selector ile bulunamadıysa, tüm kart metninde "Bugün/Saat/Dakika" ara
                if not date_text:
                    full_text = item.text.lower()
                    if any(x in full_text for x in ["bugün", "saat", "dakika", "sn", "yeni"]):
                        date_text = "Bugün (Metinden Tespit)"
                
                # radar.txt: "bugün", "yeni" veya günün tarihini içermeyen ilanları anında eler.
                if today_only and not _is_today(date_text):
                    if date_text:
                        log.debug(f"[FIRSAT RADARI] Tarih Kriterine Takıldı: {date_text}")
                    continue
                
                if not date_text:
                    log.debug("[FIRSAT RADARI] Tarih bulunamadı, ilan atlanıyor.")
                    continue

                # 2. Fiyat
                price_el = item.ele('css:.list-view-price') or item.ele('css:.listingView__card-price') or item.ele('tag:strong')
                price_raw = price_el.text if price_el else "—"
                # Rakamları ayıkla (nokta ve virgül karmaşasını çöz)
                price_clean = re.sub(r'[^0-9]', '', price_raw)
                try:
                    if price_clean:
                        price_text = "{:,.0f}".format(float(price_clean)).replace(",", ".") + " TL"
                    else:
                        price_text = price_raw
                except:
                    price_text = price_raw
                
                # 3. Başlık
                # Eski: .list-view-header | Yeni: .listingView__card-title veya h3
                title_el = item.ele('css:.list-view-header') or item.ele('css:.listingView__card-title') or item.ele('tag:h3')
                title = title_el.text if title_el else "Sahibinden İlan"
                
                # 4. Link
                # Item'ın kendisi a tagı olabilir (yeni layout) veya içinde a tagı olabilir
                if item.tag == 'a':
                    href = item.attr('href')
                else:
                    link_el = item.ele('tag:a')
                    href = link_el.attr('href') if link_el else ""
                
                if href and not href.startswith('http'):
                    href = "https://www.hepsiemlak.com" + href

                # 5. Lokasyon
                # Eski: .list-view-location | Yeni: .listingView__card-location veya address
                loc_el = item.ele('css:.list-view-location') or item.ele('css:.listingView__card-location') or item.ele('tag:address')
                location = loc_el.text if loc_el else (district or city)

                results.append({
                    "title": title,
                    "source_url": href or url,
                    "location": location,
                    "price_text": price_text,
                    "raw_snippet": f"{title} · {location} · {date_text}".strip(" ·"),
                    "source_site": "hepsiemlak",
                    "date": date_text,
                })

                # radar.txt: Yeni bulunan ilanlar 'emlak.db' veritabanına kaydedilir.
                try:
                    import sqlite3
                    db_path = Path(__file__).parent / "emlak.db"
                    with sqlite3.connect(str(db_path)) as conn:
                        # Tablo yoksa oluştur
                        conn.execute("""
                            CREATE TABLE IF NOT EXISTS scanned_listings (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                company_id TEXT,
                                ilan_id TEXT UNIQUE,
                                baslik TEXT,
                                fiyat TEXT,
                                tarih_temiz TEXT,
                                bolge_url TEXT,
                                ilan_link TEXT,
                                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        # Mükerrer kontrolü (URL üzerinden)
                        ilan_id = href.split("/")[-1].split("?")[0] if href else str(random.random())
                        conn.execute("""
                            INSERT OR IGNORE INTO scanned_listings 
                            (company_id, ilan_id, baslik, fiyat, tarih_temiz, bolge_url, ilan_link)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (company_id, ilan_id, title, price_text, date_text, location, href))
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

    # Eğer hiç sonuç çıkmadıysa (bloklanma vs), demo verisi döndürme (radar.txt'de demo istenmiyor, gerçek veri isteniyor)
    if not results:
        print(f"[FIRSAT RADARI] UYARI: Hiç ilan bulunamadı veya kriterlere uyan (Bugün) ilan yok.", file=sys.stderr, flush=True)
        log.warning("[FIRSAT RADARI] Hic ilan bulunamadi.")
    else:
        print(f"[FIRSAT RADARI] BAŞARILI: {len(results)} yeni ilan kriterlere uygun bulundu.", file=sys.stderr, flush=True)
    
    return results

if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    test_res = scrape_fsbo("Kocaeli", "daire")
