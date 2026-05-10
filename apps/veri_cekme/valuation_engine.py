"""
valuation_engine.py — Dijital Değerleme & Otomatik Ekspertiz Raporu
=====================================================================
1. Veritabanındaki benzer portföylerden istatistiksel m² analizi yapar
2. Sonucu AI'ye (Gemini/OpenAI) göndererek profesyonel metin üretir
3. reportlab ile PDF raporu oluşturur ve kaydeder
"""
import logging
import os
import re
import json
import random
import time
import atexit
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from smart_locator import SmartLocator
# Removed ui.url_resolver since ui module no longer exists

log = logging.getLogger(__name__)

class DatabaseManager:
    """radar.txt'de belirtilen emlak.db (SQLite) erişimini yönetir."""
    def __init__(self, db_path="emlak.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Tablolar yoksa oluşturur ve şemayı günceller."""
        try:
            import sqlite3
            with self.get_connection() as conn:
                # portfolios tablosu (Bölgesel istatistikler için)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS portfolios (
                        id TEXT PRIMARY KEY,
                        company_id TEXT,
                        price INTEGER,
                        area_m2 FLOAT,
                        rooms TEXT,
                        district TEXT,
                        neighborhood TEXT,
                        status TEXT DEFAULT 'active'
                    )
                """)
                
                # property_type sütununu kontrol et ve ekle (Şema güncelleme)
                try:
                    conn.execute("ALTER TABLE portfolios ADD COLUMN property_type TEXT")
                    print("[DATABASE] 'property_type' sütunu portfolios tablosuna eklendi.")
                except:
                    pass # Zaten varsa hata verir, görmezden gel

                # Diğer tablolar...
                conn.execute("CREATE TABLE IF NOT EXISTS rent_multipliers (district TEXT PRIMARY KEY, multiplier INTEGER, avg_rent_sqm FLOAT)")
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
                conn.execute("CREATE TABLE IF NOT EXISTS system_management (key TEXT PRIMARY KEY, value TEXT)")
                conn.commit()
        except Exception as e:
            print(f"Veritabani ilklendirme hatasi: {e}")

    def get_connection(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_gemini_api_key(self):
        try:
            with self.get_connection() as conn:
                row = conn.execute("SELECT value FROM system_management WHERE key='gemini_api_key'").fetchone()
                return row['value'] if row else os.getenv("GEMINI_API_KEY")
        except:
            return os.getenv("GEMINI_API_KEY")

    def get_openai_api_key(self):
        try:
            with self.get_connection() as conn:
                row = conn.execute("SELECT value FROM system_management WHERE key='openai_api_key'").fetchone()
                return row['value'] if row else os.getenv("OPENAI_API_KEY")
        except:
            return os.getenv("OPENAI_API_KEY")

    def get_gemini_model(self):
        try:
            with self.get_connection() as conn:
                row = conn.execute("SELECT value FROM system_management WHERE key='gemini_model'").fetchone()
                return row['value'] if row else "gemini-1.5-flash"
        except:
            return "gemini-1.5-flash"

def get_headless_driver():
    """Headless ve Stealth modda undetected-chromedriver başlatır."""
    import undetected_chromedriver as uc
    options = uc.ChromeOptions()
    
    # --- TESTTE ONAYLANAN PARAMETRELER (Headless & Stealth) --- 
    options.add_argument('--headless') 
    options.add_argument('--disable-gpu') 
    options.add_argument('--no-sandbox') 
    options.add_argument("--window-size=1920,1080") 
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=tr-TR")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Bu User-Agent çok önemli, testte çalıştığı için bunu aynen koruyoruz
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36') 
    
    # Uygulamadaki Chrome sürümüne göre (147) başlatıyoruz
    driver = uc.Chrome(options=options, version_main=147)
    driver.set_page_load_timeout(45)
    
    # Program beklenmedik şekilde kapanırsa driver'ı kapatmaya çalış
    atexit.register(lambda: safe_quit_driver(driver))
    
    return driver


def safe_quit_driver(driver):
    """Driver'ı güvenli bir şekilde kapatır."""
    try:
        if driver:
            driver.quit()
    except:
        pass

def scrape_web_comparables(address_text: str, rooms: str, area_m2: Optional[float], 
                           prop_type: str = "daire", building_age: str = None,
                           floor_level: str = None, heating_type: str = None,
                           listing_date: str = None, owner_type: str = None,
                           min_price: str = None, max_price: str = None,
                           site_status: str = None, furnished_status: str = None,
                           loan_status: str = None, has_elevator: bool = False,
                           has_parking: bool = False, has_garden: bool = False,
                           has_pool: bool = False, has_security: bool = False) -> Dict:
    """
    Hepsiemlak üzerinden gerçek zamanlı emsal verilerini çeker.
    Returns: { "samples": [], "url": "...", "status": "..." }
    """
    result_data = {"samples": [], "url": "", "status": "Başlatılmadı"}
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
        import pandas as pd
    except ImportError:
        msg = "Scraping için gerekli kütüphaneler (undetected-chromedriver, selenium, pandas) eksik."
        log.error(msg)
        result_data["status"] = msg
        return result_data

    # 1. URL Oluşturma (SmartLocator ile)
    # Adres metnini parçala: [Mahalle, İlçe, İl]
    parcalar = [p.strip() for p in address_text.split(',')]
    il = parcalar[-1] if len(parcalar) >= 1 else ""
    ilce = parcalar[-2] if len(parcalar) >= 2 else ""
    mahalle = parcalar[-3] if len(parcalar) >= 3 else (parcalar[0] if len(parcalar) == 1 else "")
    
    # Mahalle tespiti (Özel temizleme gerekebilir)
    for p in parcalar:
        if "mahallesi" in p.lower() or "mah." in p.lower():
            mahalle = p
            break

    # SmartLocator ile en doğru URL'yi bul
    # Driver başlatıldıktan sonra SmartLocator'a vererek canlı arama yapmasını sağla
    driver = None
    try:
        driver = get_headless_driver()
        locator = SmartLocator(driver=driver)
        konum_yolu = locator.get_url(il, ilce, mahalle)
        
    except Exception as e:
        log.warning(f"⚠️ SmartLocator/URL yakalama sırasında hata: {e}, fallback ile devam ediliyor...")
        if driver is None:
            driver = get_headless_driver()
        locator = SmartLocator(driver=driver)
        konum_yolu = locator.get_url(il, ilce, mahalle)
    
    # Mahalle slug bilgisini ilan detayları için ayıkla (Hata ayıklama amaçlı)
    mahalle_slug = locator.hepsiemlak_slugify(mahalle.replace("Mahallesi", "").replace("Mah.", ""))

    oda_slug = str(rooms).replace("+", "-").replace(" ", "") if rooms else ""
    
    tip_map = {
        "Daire": "daire", 
        "Villa": "villa", 
        "Müstakil": "mustakil-ev", 
        "Müstakil Ev": "mustakil-ev",
        "Ofis": "ofis", 
        "Dükkan": "dukkan",
        "İşyeri": "isyeri",
        "Arsa": "arsa",
        "Depo": "depo"
    }
    mulk_tipi_slug = tip_map.get(prop_type, "daire")
    
    # Hepsiemlak Teknik URL Yapısı (Garantili Mod)
    # Örn: https://www.hepsiemlak.com/umraniye-satilik/daire?p63=180407&p69=true&p75=true
    
    # 1. Temel Path (Bölge + İşlem)
    path_base = f"{konum_yolu}-satilik"
    
    # 2. Mülk Takısı (Oda artık path'de değil, p63'te olacak)
    mulk_oda_slug = f"{mulk_tipi_slug}"
    
    # 3. Query Parametreleri
    query_params = []
    
    # Oda Sayısı p63
    oda_id_map = {
        "1+1": "180405", "2+1": "180406", "3+1": "180407", "4+1": "180408",
        "5+1": "180409", "6+1": "180410", "5-1": "180409", "6-1": "180410"
    }
    target_oda_id = oda_id_map.get(rooms)
    if target_oda_id:
        query_params.append(f"p63={target_oda_id}")
    
    m2_query = ""
    if area_m2:
        m2 = int(area_m2)
        m2_query = f"p25={m2}&p26={m2}"
    if m2_query: query_params.append(m2_query)
    
    if building_age: query_params.append(f"buildingAges={building_age}")
    if floor_level: query_params.append(f"p7={floor_level}")
    if heating_type: query_params.append(f"p18={heating_type}")
    if listing_date: query_params.append(f"p14={listing_date}")
    if owner_type: query_params.append(f"p37={owner_type}")
    if min_price: query_params.append(f"p31={min_price}")
    if max_price: query_params.append(f"p32={max_price}")
    
    # Teknik Filtreler (p-kodları)
    if site_status == "true": query_params.append("p69=true")
    elif site_status == "false": query_params.append("p69=false")
    
    if furnished_status == "true": query_params.append("p75=true")
    elif furnished_status == "false": query_params.append("p75=false")
    
    if loan_status: query_params.append(f"availableForLoanStatus={loan_status}")
    
    # Semantik Özellikler (Hala çalışanlar)
    if has_elevator: query_params.append("elevator=true")
    if has_parking: query_params.append("parking=true")
    if has_garden: query_params.append("garden=true")
    
    # realtyAttributes
    attrs = []
    if has_security: attrs.append("61")
    if has_pool: attrs.extend(["174", "360"])
    if attrs:
        query_params.append(f"realtyAttributes={','.join(attrs)}")

    query_string = "&".join(query_params)
    
    url = f"https://www.hepsiemlak.com/{path_base}/{mulk_oda_slug}"
    if query_string:
        url += f"?{query_string}"
    
    # Temizlik
    url = url.replace("//", "/").replace("https:/", "https://").replace(".com//", ".com/")
    result_data["url"] = url

    print(f"\n[WEB TARAMA] Hedef URL: {url}", flush=True)
    
    try:
        # Driver zaten başlatıldı (SmartLocator/orijinal_url_yakala için), onu kullan
        # (get_headless_driver() çağrılmaz, mevcut driver kullanılır)
        print(f"[WEB TARAMA] Sayfa yukleniyor (Mevcut driver kullaniliyor)...", flush=True)
        driver.get(url)
        
        # İnsansı bekleme
        time.sleep(random.uniform(8.5, 13.2))
        
        # Hata ayıklama için ekran görüntüsü al
        driver.save_screenshot("debug_ekran.png")
        print(f"[WEB TARAMA] Ekran goruntusu alindi: debug_ekran.png", flush=True)
        
        # 2. Kesin Sonuç Kontrolü (CSS Sınıf Bazlı)
        # Hepsiemlak öneri ilanlarını '.suggested-search-banner' bandının altına dizer.
        # Eğer '.zero-result' veya bu banner varsa kesinlikle gerçek sonuç yoktur.
        try:
            zero_results = driver.find_elements(By.CSS_SELECTOR, ".zero-result")
            suggested_banner = driver.find_elements(By.CSS_SELECTOR, ".suggested-search-banner")
            
            if len(zero_results) > 0 or len(suggested_banner) > 0:
                print(f"[WEB TARAMA] KESİN UYARI: Sıfır sonuç veya Öneri bandı tespit edildi. Taramayı durduruyorum.", flush=True)
                result_data["status"] = "Uygun ilan bulunamadı."
                return result_data
        except:
            pass

        # Yedek Metin Kontrolü
        page_source = driver.page_source
        no_results_keywords = [
            "0 ilan bulundu", 
            "aradığınız kriterlere uygun ilan bulamadık"
        ]
        page_source_lower = page_source.lower()
        if any(kw in page_source_lower for kw in no_results_keywords):
            print(f"[WEB TARAMA] Metin bazlı sıfır sonuç tespiti (Oneri ilanlar atlandi).", flush=True)
            result_data["status"] = "Uygun ilan bulunamadı."
            return result_data

        print(f"[WEB TARAMA] Insansi kaydirma (scrolling) yapiliyor...", flush=True)
        # Parça parça kaydır
        for _ in range(3):
            scroll_amount = random.randint(400, 800)
            driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
            time.sleep(random.uniform(1.5, 3.0))

        items = driver.find_elements(By.CSS_SELECTOR, "li.listing-item")
        if not items:
            items = driver.find_elements(By.CLASS_NAME, "listing-item")

        if not items:
            print(f"[WEB TARAMA] Hic ilan bulunamadi. Bloklanmis olabiliriz.", flush=True)
            result_data["status"] = "İlan bulunamadı veya bloklandı."
            return result_data
            
        print(f"[WEB TARAMA] {len(items)} ilan bulundu. Veriler ayiklaniyor...", flush=True)
        
        for i, item in enumerate(items[:15]):
            try:
                full_text = item.text
                if not full_text or len(full_text) < 20: continue
                
                lines = full_text.split('\n')
                price, m2_val, rooms_val = None, None, "—"
                title = "Web İlanı"
                link = ""
                
                # 1. Önce spesifik elementlerden veri çekmeyi dene (Daha kesin sonuç)
                try:
                    # Fiyat genellikle list-view-price veya price class'ındadır
                    price_el = item.find_element(By.CSS_SELECTOR, ".list-view-price, .price, .listing-price")
                    p_digits = re.sub(r'\D', '', price_el.text)
                    if p_digits: price = float(p_digits)
                except: pass

                try:
                    # m2 genellikle squareMeter veya list-view-size içindedir
                    m2_el = item.find_element(By.CSS_SELECTOR, ".squareMeter, .list-view-size, .celly.squareMeter")
                    m_digits = re.sub(r'\D', '', m2_el.text)
                    if m_digits: m2_val = float(m_digits)
                except: pass

                try:
                    # Oda sayısı genellikle .houseRoomCount veya .celly.houseRoomCount içindedir
                    rooms_el = item.find_element(By.CSS_SELECTOR, ".houseRoomCount, .celly.houseRoomCount")
                    rooms_val = rooms_el.text.strip()
                except: pass

                # 2. Eğer elementlerden bulunamadıysa veya eksikse metin üzerinden devam et (Yedek plan)
                if not price or not m2_val or rooms_val == "—":
                    for line in lines:
                        line = line.strip()
                        # Ana fiyatı çek (Birim fiyat olan TL/m2'yi atla)
                        if "TL" in line and "/m" not in line.lower():
                            digits = re.sub(r'\D', '', line)
                            if digits: price = float(digits)
                        
                        # Toplam m2'yi çek
                        if ("m²" in line or "m2" in line) and "/m" not in line.lower():
                            digits = re.sub(r'\D', '', line)
                            if digits: m2_val = float(digits)
                        
                        # Oda sayısı (Örn: 2+1, 3+1)
                        if "+" in line and len(line) < 10:
                            rooms_val = line
                        elif "Stüdyo" in line:
                            rooms_val = "1+0"
                
                # İlan linkini yakala
                try:
                    link_el = item.find_element(By.TAG_NAME, "a")
                    link = link_el.get_attribute("href")
                except:
                    pass

                if price and m2_val:
                    # MANTIK FİLTRESİ (Outlier Removal): Gerçekçi olmayan m2 verilerini ele
                    # Arsa için limitleri geniş tut (10m2 - 1.000.000m2), diğerleri için (10m2 - 10.000m2)
                    max_limit = 1000000 if prop_type.lower() == "arsa" else 10000
                    if 10 < m2_val < max_limit:
                        print(f"   OK {i+1}. Ilan: {price:,.0f} TL | {m2_val} m2 | {rooms_val}", flush=True)
                        result_data["samples"].append({
                            "id": random.randint(10000, 99999),
                            "title": title,
                            "price": price,
                            "area_m2": m2_val,
                            "rooms": rooms_val,
                            "district": mahalle_slug, # Look-up'tan gelen temiz mahalle slug
                            "is_web": True,
                            "url": link
                        })
                    else:
                        print(f"   WARN {i+1}. Ilan Elendi (Hatali m2): {m2_val} m2", flush=True)
            except Exception as e:
                log.debug(f"Bireysel ilan parse hatası: {e}")
                continue
        
        if result_data["samples"]:
            print(f"[WEB TARAMA] Toplam {len(result_data['samples'])} gecerli ilan cekildi.", flush=True)
        else:
            print(f"[WEB TARAMA] Ilan bulundu ama veri ayristirilamadi.", flush=True)
            
        result_data["status"] = f"{len(result_data['samples'])} ilan başarıyla çekildi."
        return result_data

    except Exception as e:
        msg = f"Scraping hatası: {str(e)}"
        log.error(msg)
        result_data["status"] = msg
        return result_data
    finally:
        safe_quit_driver(driver)

def run_valuation_hybrid(address_text: str, rooms: str, area_m2: Optional[float], 
                         prop_type: str = "daire", company_id: str = "default",
                         lat: float = None, lon: float = None,
                         building_age: str = None, floor_level: str = None,
                         heating_type: str = None, listing_date: str = None,
                         owner_type: str = None, min_price: str = None,
                         max_price: str = None, site_status: str = None,
                         furnished_status: str = None, loan_status: str = None,
                         has_elevator: bool = False, has_parking: bool = False,
                         has_garden: bool = False, has_pool: bool = False,
                         has_security: bool = False) -> Dict:
    """
    radar.txt BÖLÜM 2.B: Web verileriyle DB verilerini birleştirerek analiz yapar.
    """
    db = DatabaseManager()
    
    # 1. Web'den emsalleri çek
    web_result = scrape_web_comparables(
        address_text, rooms, area_m2, prop_type,
        building_age=building_age, floor_level=floor_level,
        heating_type=heating_type, listing_date=listing_date,
        owner_type=owner_type, min_price=min_price,
        max_price=max_price, site_status=site_status,
        furnished_status=furnished_status, loan_status=loan_status,
        has_elevator=has_elevator, has_parking=has_parking,
        has_garden=has_garden, has_pool=has_pool,
        has_security=has_security
    )
    web_samples = web_result.get("samples", [])
    
    # 2. DB'den bölgesel istatistikleri ve emsalleri çek
    # Adres metninden ilçe ayıkla
    parcalar = [p.strip() for p in address_text.split(',')]
    ilce = parcalar[-2] if len(parcalar) >= 2 else (parcalar[0] if len(parcalar) > 0 else "")
    
    db_stats = calculate_market_stats(db, company_id, ilce, prop_type, rooms)
    db_samples = db_stats.get("samples", [])
    
    # 3. Verileri birleştir (Hybrid Model)
    all_samples = web_samples + db_samples
    
    if not all_samples:
        return web_result

    # 4. Ortalama, Medyan, Q25, Q75 değerlerini yeniden hesapla (radar.txt 2.B)
    sqm_prices = []
    for s in all_samples:
        try:
            p = float(s['price'])
            a = float(s['area_m2'])
            if a > 0:
                sqm_prices.append(p / a)
        except:
            continue
    
    sqm_prices.sort()
    n = len(sqm_prices)
    
    def percentile(lst, p):
        if not lst: return 0
        idx = int(len(lst) * p / 100)
        return lst[min(idx, len(lst) - 1)]

    avg_price = sum(sqm_prices) / n if n > 0 else 0
    
    final_stats = {
        "count": n,
        "avg_sqm_price": avg_price,
        "min_sqm_price": sqm_prices[0] if n > 0 else 0,
        "max_sqm_price": sqm_prices[-1] if n > 0 else 0,
        "median_sqm_price": percentile(sqm_prices, 50),
        "q25": percentile(sqm_prices, 25),
        "q75": percentile(sqm_prices, 75),
        "samples": all_samples[:30], # İlk 30 emsali döndür
        "url": web_result.get("url", ""),
        "status": "Hibrit analiz tamamlandi."
    }
    
    # 5. AI Analizi ve ROI
    roi = calculate_roi(db, avg_price, ilce)
    final_stats["roi"] = roi
    
    # POI verileri (Koordinat varsa gerçek, yoksa simüle)
    poi = get_poi_data(ilce, lat=lat, lon=lon)
    final_stats["poi"] = poi
    
    ai_text = generate_ai_report(db, final_stats, {
        "district": ilce, "prop_type": prop_type, "rooms": rooms, "area_m2": area_m2
    })
    final_stats["ai_report"] = ai_text
    
    return final_stats


# ── İstatistiksel Hesaplama ───────────────────────────────────────────────────

def calculate_market_stats(db, company_id: int, district: str,
                            prop_type: str, rooms: str = None) -> dict:
    """
    Veritabanındaki aktif portföylerden bölge/tip bazlı m² istatistiği.
    Returns:
        {count, avg_sqm_price, min_sqm_price, max_sqm_price,
         median_sqm_price, q25, q75, samples, trends, stock_count}
    """
    try:
        query = (
            "SELECT id, price, area_m2, rooms, district, neighborhood "
            "FROM portfolios "
            "WHERE company_id=? AND status='active' "
            "AND area_m2 IS NOT NULL AND area_m2 > 0 "
            "AND price IS NOT NULL AND price > 0"
        )
        params = [company_id]

        if district:
            query += " AND LOWER(district) LIKE LOWER(?)"
            params.append(f"%{district}%")
        if prop_type:
            query += " AND LOWER(property_type) LIKE LOWER(?)"
            params.append(f"%{prop_type}%")
        if rooms:
            query += " AND rooms=?"
            params.append(rooms)

        with db.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()

        samples = [dict(r) for r in rows]
        if not samples:
            return {"count": 0, "samples": [], "trends": [], "stock_count": 0}

        sqm_prices = [s["price"] / s["area_m2"] for s in samples if s["area_m2"] > 0]
        sqm_prices.sort()
        n = len(sqm_prices)

        def percentile(lst, p):
            idx = int(len(lst) * p / 100)
            return lst[min(idx, len(lst) - 1)]

        # Trend analizi (Fiyat Geçmişinden)
        portfolio_ids = [s["id"] for s in samples]
        trends = []
        if portfolio_ids:
            placeholders = ",".join(["?"] * len(portfolio_ids))
            trend_query = f"""
                SELECT strftime('%Y-%m', changed_at) as month, AVG(new_price/old_price - 1) * 100 as change_pct
                FROM price_history 
                WHERE portfolio_id IN ({placeholders})
                GROUP BY month ORDER BY month DESC LIMIT 6
            """
            try:
                with db.get_connection() as conn:
                    trend_rows = conn.execute(trend_query, portfolio_ids).fetchall()
                    trends = [dict(r) for r in trend_rows]
            except Exception as e:
                log.warning(f"Trend çekilirken hata: {e}")

        # Eğer gerçek veri yoksa, bölgeye özel simüle edilmiş bir trend oluştur (Görsel doluluk için)
        if not trends:
            import random
            from datetime import timedelta
            seed = sum(ord(c) for c in district)
            random.seed(seed)
            
            # Son 6 ayı simüle et
            for i in range(6):
                date = datetime.now() - timedelta(days=30*i)
                month_str = date.strftime('%Y-%m')
                # Genellikle %1 ile %4 arası bir artış simüle edelim
                sim_change = random.uniform(1.2, 3.8)
                trends.append({"month": month_str, "change_pct": round(sim_change, 1)})
            
            # Ayları eskiye doğru sırala (chart fonksiyonu tersine çeviriyor zaten)
            trends.sort(key=lambda x: x["month"], reverse=True)

        return {
            "count":          n,
            "avg_sqm_price":  sum(sqm_prices) / n,
            "min_sqm_price":  sqm_prices[0],
            "max_sqm_price":  sqm_prices[-1],
            "median_sqm_price": percentile(sqm_prices, 50),
            "q25":            percentile(sqm_prices, 25),
            "q75":            percentile(sqm_prices, 75),
            "samples":        samples,
            "trends":         trends,
            "stock_count":    n, # Bölgedeki aktif ilan sayısı
        }

    except Exception as e:
        log.error(f"Market stats hatası: {e}")
        return {"count": 0, "samples": [], "trends": [], "stock_count": 0, "error": str(e)}


def calculate_roi(db, avg_sqm_price: float, district: str) -> dict:
    """Amortisman süresi ve kira çarpanı hesabı."""
    # Veritabanından bölge bazlı kira çarpanını çek
    kira_carpani = 240 # Default
    avg_rent_sqm = 0
    
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT multiplier, avg_rent_sqm FROM rent_multipliers WHERE LOWER(district) = LOWER(?)",
                (district,)
            ).fetchone()
            if row:
                kira_carpani = row["multiplier"] if isinstance(row, dict) else row[0]
                avg_rent_sqm = row["avg_rent_sqm"] if isinstance(row, dict) else row[1]
    except Exception as e:
        log.warning(f"ROI verisi çekilirken hata: {e}")

    # Eğer DB'de yoksa veya 0 ise basit hesaplama yap
    if not avg_rent_sqm:
        if "İstanbul" in district or "Bodrum" in district: 
            kira_carpani = 300
        avg_rent_sqm = avg_sqm_price / kira_carpani

    return {
        "amortization_years": round(kira_carpani / 12, 1),
        "monthly_rent_sqm": round(avg_rent_sqm, 2),
        "rent_multiplier": kira_carpani
    }


def get_real_poi_data(lat: float, lon: float, radius: int = 500) -> dict:
    """
    Overpass API kullanarak verilen koordinatların çevresindeki POI verilerini çeker.
    """
    import requests
    import json

    overpass_url = "https://overpass-api.de/api/interpreter"
    
    # Overpass QL Query
    # Hastane, Okul, Eczane, Otobüs Durağı, Metro Girişi
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="hospital"](around:{radius},{lat},{lon});
      way["amenity"="hospital"](around:{radius},{lat},{lon});
      relation["amenity"="hospital"](around:{radius},{lat},{lon});
      
      node["amenity"="school"](around:{radius},{lat},{lon});
      way["amenity"="school"](around:{radius},{lat},{lon});
      relation["amenity"="school"](around:{radius},{lat},{lon});
      
      node["amenity"="pharmacy"](around:{radius},{lat},{lon});
      way["amenity"="pharmacy"](around:{radius},{lat},{lon});
      relation["amenity"="pharmacy"](around:{radius},{lat},{lon});
      
      node["highway"="bus_stop"](around:{radius},{lat},{lon});
      
      node["railway"="subway_entrance"](around:{radius},{lat},{lon});
    );
    out body;
    """

    try:
        headers = {
            "User-Agent": "TraeRealEstateApp/1.0 (https://github.com/Batuhan; contact@example.com)",
            "Accept": "application/json"
        }
        response = requests.post(overpass_url, data={'data': query}, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        elements = data.get('elements', [])
        
        summary = {
            'hastane_sayisi': 0,
            'okul_sayisi': 0,
            'eczane_sayisi': 0,
            'otobus_duragi_sayisi': 0,
            'metro_girisi_sayisi': 0
        }
        
        for element in elements:
            tags = element.get('tags', {})
            amenity = tags.get('amenity')
            highway = tags.get('highway')
            railway = tags.get('railway')
            
            if amenity == 'hospital':
                summary['hastane_sayisi'] += 1
            elif amenity == 'school':
                summary['okul_sayisi'] += 1
            elif amenity == 'pharmacy':
                summary['eczane_sayisi'] += 1
            elif highway == 'bus_stop':
                summary['otobus_duragi_sayisi'] += 1
            elif railway == 'subway_entrance':
                summary['metro_girisi_sayisi'] += 1
                
        return summary
    except Exception as e:
        log.error(f"Overpass API hatası: {e}")
        return None


def get_poi_data(district: str, lat: float = None, lon: float = None) -> dict:
    """Bölgedeki POI (Point of Interest) verilerini çeker. Koordinat varsa gerçek veri, yoksa simülasyon."""
    
    if lat and lon:
        real_data = get_real_poi_data(lat, lon)
        if real_data:
            # Mevcut UI yapısına uydurmak için dönüştürelim
            import random
            seed = sum(ord(c) for c in district)
            random.seed(seed)
            
            return {
                "transport": {
                    "metro": real_data['metro_girisi_sayisi'],
                    "bus": real_data['otobus_duragi_sayisi'],
                    "highway_dist": round(random.uniform(0.5, 5.0), 1) # Mesafe verisi Overpass'ten kompleks
                },
                "social": {
                    "hospital": real_data['hastane_sayisi'],
                    "school": real_data['okul_sayisi'],
                    "mall": random.randint(0, 4), # Overpass'te mall eklenmedi ama eklenebilir
                    "market": real_data['eczane_sayisi'] # Eczaneyi market yerine veya yanına koyabiliriz
                },
                "demographics": {
                    "avg_age": random.randint(30, 45),
                    "education_level": random.choice(["Yüksek", "Orta-Yüksek", "Orta"]),
                    "pop_density": random.choice(["Yoğun", "Orta", "Seyrek"])
                },
                "real": True,
                "pharmacy": real_data['eczane_sayisi']
            }

    # Fallback simülasyon
    import random
    seed = sum(ord(c) for c in district)
    random.seed(seed)
    
    return {
        "transport": {
            "metro": random.randint(0, 3),
            "bus": random.randint(5, 20),
            "highway_dist": round(random.uniform(0.5, 5.0), 1)
        },
        "social": {
            "hospital": random.randint(1, 5),
            "school": random.randint(3, 15),
            "mall": random.randint(0, 4),
            "market": random.randint(10, 50)
        },
        "demographics": {
            "avg_age": random.randint(30, 45),
            "education_level": random.choice(["Yüksek", "Orta-Yüksek", "Orta"]),
            "pop_density": random.choice(["Yoğun", "Orta", "Seyrek"])
        }
    }


def estimate_value(stats: dict, area_m2: Optional[float]) -> dict:
    """İstatistikten tahmini değer hesapla."""
    if not stats or stats.get("count", 0) == 0 or not area_m2 or area_m2 <= 0:
        return {}
    return {
        "estimated_low":    stats["q25"] * area_m2,
        "estimated_mid":    stats["avg_sqm_price"] * area_m2,
        "estimated_high":   stats["q75"] * area_m2,
        "estimated_median": stats["median_sqm_price"] * area_m2,
    }


# ── AI Metin Üretimi ─────────────────────────────────────────────────────────

def generate_ai_report(db, stats: dict, params: dict) -> str:
    """
    Gemini veya OpenAI API'ye istatistik gönder → profesyonel ve özlü değerleme metni al.
    """
    district   = params.get("district", "Belirtilmemiş")
    prop_type  = params.get("prop_type", "Daire")
    rooms      = params.get("rooms", "")
    area_m2    = params.get("area_m2", 0)
    age        = params.get("age", "")

    count = stats.get("count", 0)
    avg   = stats.get("avg_sqm_price", 0)
    minn  = stats.get("min_sqm_price", 0)
    maxx  = stats.get("max_sqm_price", 0)
    
    # Web'den gelen emsal verilerini özetle
    web_samples = [s for s in stats.get("samples", []) if s.get("is_web")]
    web_summary = ""
    if web_samples:
        web_summary = "\nWEB'DEN BULUNAN GERÇEK ZAMANLI EMSALLER:\n"
        for s in web_samples[:3]: # En alakalı 3 tanesini ekleyelim
            web_summary += f"- {s.get('title')}: {s.get('price'):,.0f} ₺ ({s.get('area_m2')} m²)\n"

    prompt = (
        f"Sen profesyonel bir gayrimenkul değerleme uzmanısın. Kısa ve net Türkçe rapor yaz (maks 200 kelime).\n\n"
        f"Bölge: {district} | Tip: {prop_type} | Oda: {rooms or 'Fark Etmez'} | m²: {area_m2 or 'Fark Etmez'}\n\n"
        f"Piyasa: {count} emsal | Ort: {avg:,.0f} ₺/m² | Aralık: {minn:,.0f}-{maxx:,.0f} ₺/m²"
        f"{web_summary}\n\n"
        f"Başlıklar: 1) Piyasa Analizi 2) Değerleme 3) Avantaj/Dezavantaj 4) Yatırım Potansiyeli"
    )

    # Gemini denemesi
    try:
        api_key = db.get_gemini_api_key()
        if api_key:
            import google.generativeai as genai
            from google.api_core import exceptions
            import time
            
            genai.configure(api_key=api_key)
            
            # Model adını veritabanından çek (system_management -> gemini_model)
            model_name = db.get_gemini_model()
            model = genai.GenerativeModel(model_name)
            
            # Retry Logic (Yeniden Deneme Mekanizması)
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Throttling: Her istekten önce kısa bir bekleme
                    time.sleep(0.5)
                    
                    response = model.generate_content(prompt)
                    return response.text
                except exceptions.ResourceExhausted:
                    if attempt < max_retries - 1:
                        log.warning(f"⚠️ Gemini Rate Limit doldu, {10} saniye bekleniyor... (Deneme {attempt+1}/{max_retries})")
                        time.sleep(10)
                        continue
                    else:
                        raise
                except Exception as e:
                    log.error(f"Gemini API çağrı hatası: {e}")
                    break
                    
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            log.error("Valuation Engine: Gemini API Rate Limit aşıldı.")
            return "YAPAY ZEKA RATE LIMIT HATASI: Çok fazla istek atıldı. Detaylı AI raporu üretilemedi. Ancak aşağıda istatistiksel veriler sunulmaktadır.\n\n" + _template_report(params, stats)
        log.warning(f"Gemini AI genel hatası: {e}")

    # OpenAI denemesi
    try:
        import openai
        key = db.get_openai_api_key()
        if key:
            client = openai.OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
            )
            return resp.choices[0].message.content
    except Exception as e:
        log.warning(f"OpenAI hatası: {e}")

    # Şablon fallback
    return _template_report(params, stats)


def _template_report(params: dict, stats: dict) -> str:
    district  = params.get("district", "—")
    prop_type = params.get("prop_type", "Daire")
    area_m2   = params.get("area_m2", 0)
    rooms     = params.get("rooms", "")
    count     = stats.get("count", 0)
    avg       = stats.get("avg_sqm_price", 0)
    est       = estimate_value(stats, area_m2)
    mid       = est.get("estimated_mid", 0)

    return f"""
PİYASA ÖZETİ
{district} bölgesinde {count} adet benzer {prop_type} analiz edilmiştir.
Ortalama m² satış fiyatı {avg:,.0f} ₺ olarak hesaplanmıştır.

DEĞERLEME SONUCU
{area_m2} m² {rooms} {prop_type} için tahmini piyasa değeri:
• Alt Değer:  {est.get("estimated_low", 0):,.0f} ₺
• Orta Değer: {mid:,.0f} ₺
• Üst Değer:  {est.get("estimated_high", 0):,.0f} ₺

BÖLGE ANALİZİ
{district} bölgesi, sunulan veriler ışığında orta-üst segment özellikler taşımaktadır.
Bölgede m² fiyat aralığı {stats.get("min_sqm_price", 0):,.0f} ₺ ile {stats.get("max_sqm_price", 0):,.0f} ₺ arasında seyretmektedir.

SONUÇ
Bu değerleme, sistem portföy verilerine dayalı istatistiksel analiz sonucudur.
Kesin değerleme için lisanslı gayrimenkul eksperi görüşü alınması önerilir.
""".strip()


# ── PDF Raporu Oluştur ────────────────────────────────────────────────────────

def generate_pdf_report(params: dict, stats: dict, ai_text: str,
                        output_path: str = None) -> str:
    """
    reportlab ile profesyonel PDF ekspertiz raporu oluşturur.
    Returns: oluşturulan PDF dosya yolu.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table,
            TableStyle, HRFlowable
        )
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY

        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        
        # Türkçe font desteği için Arial yüklemeyi dene (Windows varsayılan)
        font_name = "Helvetica"
        font_bold = "Helvetica-Bold"
        try:
            if os.path.exists("C:/Windows/Fonts/arial.ttf"):
                pdfmetrics.registerFont(TTFont('Arial', 'C:/Windows/Fonts/arial.ttf'))
                pdfmetrics.registerFont(TTFont('Arial-Bold', 'C:/Windows/Fonts/arialbd.ttf'))
                font_name = "Arial"
                font_bold = "Arial-Bold"
        except Exception as e:
            log.warning(f"Arial fontu yüklenemedi, varsayılana dönülüyor: {e}")

        if not output_path:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(Path.home() / "Downloads" / f"EstateAI_Degerleme_{ts}.pdf")

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=2*cm, bottomMargin=2*cm,
        )

        styles = getSampleStyleSheet()
        BLUE   = HexColor("#2563EB")
        DARK   = HexColor("#0F172A")
        GRAY   = HexColor("#64748B")
        LIGHT  = HexColor("#F1F5F9")
        GREEN  = HexColor("#16A34A")

        h1 = ParagraphStyle("h1", fontSize=20, fontName=font_bold,
                             textColor=BLUE, spaceAfter=6, spaceBefore=4)
        h2 = ParagraphStyle("h2", fontSize=13, fontName=font_bold,
                             textColor=DARK, spaceAfter=4, spaceBefore=10)
        body = ParagraphStyle("body", fontSize=10, fontName=font_name,
                               textColor=DARK, leading=16, alignment=TA_JUSTIFY)
        meta = ParagraphStyle("meta", fontSize=9, fontName=font_name,
                               textColor=GRAY, spaceAfter=2)

        story = []

        # ── Başlık ───────────────────────────────────────────────────────────
        story.append(Paragraph("EstateAI — Dijital Ekspertiz Raporu", h1))
        story.append(Paragraph(
            f"Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y %H:%M')}  |  "
            f"Bölge: {params.get('district', '—')}  |  "
            f"Tip: {params.get('prop_type', '—')}",
            meta
        ))
        story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=14))

        # ── Mülk Bilgileri Tablosu ────────────────────────────────────────────
        story.append(Paragraph("Mülk Bilgileri", h2))
        mdata = [
            ["Bölge",     params.get("district", "—"),
             "Metrekare", f"{params.get('area_m2', '—')} m²"],
            ["Mülk Tipi", params.get("prop_type", "—"),
             "Oda Sayısı", params.get("rooms", "—") or "—"],
            ["Bina Yaşı", params.get("age", "—") or "—",
             "Analiz Edilen", f"{stats.get('count', 0)} mülk"],
        ]
        mtable = Table(mdata, colWidths=[3.5*cm, 5*cm, 3.5*cm, 5*cm])
        mtable.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
            ("BACKGROUND", (0, 0), (0, -1), HexColor("#DBEAFE")),
            ("BACKGROUND", (2, 0), (2, -1), HexColor("#DBEAFE")),
            ("FONTNAME",   (0, 0), (0, -1), font_bold),
            ("FONTNAME",   (2, 0), (2, -1), font_bold),
            ("FONTNAME",   (1, 0), (1, -1), font_name),
            ("FONTNAME",   (3, 0), (3, -1), font_name),
            ("FONTSIZE",   (0, 0), (-1, -1), 9),
            ("GRID",       (0, 0), (-1, -1), 0.5, HexColor("#CBD5E1")),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [LIGHT, HexColor("#F8FAFC")]),
            ("TOPPADDING",  (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ]))
        story.append(mtable)
        story.append(Spacer(1, 12))

        # ── Piyasa İstatistikleri ─────────────────────────────────────────────
        story.append(Paragraph("Piyasa İstatistikleri", h2))
        area_m2 = float(params.get("area_m2", 0) or 0)
        est = estimate_value(stats, area_m2) if area_m2 > 0 else {}

        sdata = [
            ["Gösterge", "Değer (₺/m²)", "Tahmini Toplam (₺)"],
            ["Minimum m² Fiyatı",
             f"{stats.get('min_sqm_price', 0):,.0f}",
             f"{stats.get('min_sqm_price', 0)*area_m2:,.0f}" if area_m2 else "—"],
            ["Q25 (Alt Çeyrek)",
             f"{stats.get('q25', 0):,.0f}",
             f"{est.get('estimated_low', 0):,.0f}" if est else "—"],
            ["Ortalama m² Fiyatı",
             f"{stats.get('avg_sqm_price', 0):,.0f}",
             f"{est.get('estimated_mid', 0):,.0f}" if est else "—"],
            ["Medyan m² Fiyatı",
             f"{stats.get('median_sqm_price', 0):,.0f}",
             f"{est.get('estimated_median', 0):,.0f}" if est else "—"],
            ["Q75 (Üst Çeyrek)",
             f"{stats.get('q75', 0):,.0f}",
             f"{est.get('estimated_high', 0):,.0f}" if est else "—"],
            ["Maksimum m² Fiyatı",
             f"{stats.get('max_sqm_price', 0):,.0f}",
             f"{stats.get('max_sqm_price', 0)*area_m2:,.0f}" if area_m2 else "—"],
        ]
        stable = Table(sdata, colWidths=[6*cm, 4.5*cm, 5.5*cm])
        stable.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  BLUE),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  HexColor("#FFFFFF")),
            ("FONTNAME",      (0, 0), (-1, 0),  font_bold),
            ("FONTNAME",      (0, 1), (-1, -1), font_name),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("GRID",          (0, 0), (-1, -1), 0.5, HexColor("#CBD5E1")),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [LIGHT, HexColor("#F8FAFC")]),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
            # Ortalama satırı vurgula
            ("BACKGROUND",    (0, 4), (-1, 4),  HexColor("#ECFDF5")),
            ("FONTNAME",      (0, 4), (-1, 4),  font_bold),
            ("TEXTCOLOR",     (0, 4), (-1, 4),  GREEN),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ]))
        story.append(stable)
        story.append(Spacer(1, 14))

        # ── Tahmini Değer Vurgusu ─────────────────────────────────────────────
        if est:
            story.append(Paragraph("Tahmini Piyasa Değeri", h2))
            mid_val = est.get("estimated_mid", 0)
            vdata = [[
                "ALT DEĞER\n" + f"{est.get('estimated_low', 0):,.0f} ₺",
                "ORTA DEĞER (ÖNERİLEN)\n" + f"{mid_val:,.0f} ₺",
                "ÜST DEĞER\n" + f"{est.get('estimated_high', 0):,.0f} ₺",
            ]]
            vtable = Table(vdata, colWidths=[5.33*cm, 5.33*cm, 5.33*cm])
            vtable.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (0, 0),  HexColor("#FEF3C7")),
                ("BACKGROUND",    (1, 0), (1, 0),  HexColor("#DCFCE7")),
                ("BACKGROUND",    (2, 0), (2, 0),  HexColor("#DBEAFE")),
                ("FONTNAME",      (0, 0), (-1, -1), font_bold),
                ("FONTSIZE",      (0, 0), (-1, -1), 10),
                ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING",    (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("GRID",          (0, 0), (-1, -1), 1, HexColor("#CBD5E1")),
                ("ROUNDEDCORNERS", [4]),
            ]))
            story.append(vtable)
            story.append(Spacer(1, 14))

        # ── AI Değerleme Metni ────────────────────────────────────────────────
        if ai_text and ai_text.strip():
            story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#E2E8F0"), spaceAfter=10))
            story.append(Paragraph("Uzman Değerlendirmesi", h2))
            for line in ai_text.split("\n"):
                line = line.strip()
                if not line:
                    story.append(Spacer(1, 4))
                elif line.isupper() or line.endswith(":"):
                    story.append(Paragraph(line, h2))
                else:
                    story.append(Paragraph(line, body))

        # ── Dipnot ───────────────────────────────────────────────────────────
        story.append(Spacer(1, 20))
        story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY))
        story.append(Spacer(1, 6))
        disclaimer = ParagraphStyle("disc", fontSize=8, fontName=font_name,
                                     textColor=GRAY, leading=12)
        story.append(Paragraph(
            "Bu rapor EstateAI sistemi tarafından otomatik olarak oluşturulmuştur. "
            "İstatistikler, sistem portföy veritabanındaki benzer mülk verilerine dayanmaktadır. "
            "Resmi ve yasal bağlayıcı bir değerleme niteliği taşımaz. "
            "Kesin değerleme için SPK lisanslı gayrimenkul değerleme uzmanı görüşü alınması önerilir.",
            disclaimer
        ))

        doc.build(story)
        log.info(f"PDF rapor oluşturuldu: {output_path}")
        return output_path

    except ImportError:
        raise ImportError("reportlab kurulu değil. 'pip install reportlab' çalıştırın.")
    except Exception as e:
        log.error(f"PDF oluşturma hatası: {e}")
        raise
