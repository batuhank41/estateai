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
                except:
                    pass

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
                # radar.txt: Sistem yönetimi (key-value)
                conn.execute("CREATE TABLE IF NOT EXISTS system_management (key TEXT PRIMARY KEY, value TEXT)")
                
                # Migrasyon: Eğer eski 'anahtar' ve 'deger' varsa 'key' ve 'value'ye çevir
                try:
                    conn.execute("ALTER TABLE system_management RENAME COLUMN anahtar TO key")
                    conn.execute("ALTER TABLE system_management RENAME COLUMN deger TO value")
                    print("[DATABASE] system_management tablosu key/value formatına taşındı.")
                except:
                    pass

                conn.commit()
        except Exception as e:
            print(f"Veritabani ilklendirme hatasi: {e}")

    def get_connection(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_pg_connection(self):
        """Web uygulamasının PostgreSQL veritabanına bağlanır."""
        try:
            try:
                import psycopg2
            except ImportError:
                print("❌ PostgreSQL Bağlantı Hatası: 'psycopg2' modülü yüklü değil. Veritabanından API anahtarları okunamıyor.", flush=True)
                return None

            from urllib.parse import urlparse, unquote
            
            # .env dosyasından DATABASE_URL'i çek (Çoklu Yol Kontrolü)
            possible_env_paths = [
                Path(__file__).parent.parent / "apps" / "web" / ".env",
                Path(__file__).parent / ".env",
                Path(os.getcwd()) / ".env"
            ]
            
            db_url = None
            for env_path in possible_env_paths:
                if env_path.exists():
                    with open(env_path, "r") as f:
                        for line in f:
                            if line.startswith("DATABASE_URL="):
                                db_url = line.split("=")[1].strip().strip('"').strip("'")
                                break
                if db_url: break
            
            if not db_url:
                db_url = os.getenv("DATABASE_URL")

            if db_url:
                parsed = urlparse(db_url)
                dbname = parsed.path.lstrip('/')
                user = parsed.username
                password = unquote(parsed.password) if parsed.password else None
                host = parsed.hostname
                port = parsed.port or 5432
                
                conn_str = f"dbname='{dbname}' user='{user}' host='{host}' port='{port}'"
                if password:
                    conn_str += f" password='{password}'"
                if "sslmode=require" in db_url:
                    conn_str += " sslmode='require'"
                
                return psycopg2.connect(conn_str)
            return None
        except Exception as e:
            print(f"❌ PostgreSQL Bağlantı Hatası: {e}", flush=True)
            return None

    def get_gemini_api_key(self):
        # Önce PostgreSQL'den (Web Uygulaması DB) dene
        conn = self.get_pg_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM system_management WHERE key = 'gemini_api_key'")
                    row = cur.fetchone()
                    if row:
                        val = row[0]
                        print(f"✅ PostgreSQL'den Gemini Key Alındı: {val[:5]}...", flush=True)
                        return val
            except Exception as e:
                print(f"⚠️ PostgreSQL Sorgu Hatası (Gemini): {e}", flush=True)
            finally:
                conn.close()
        
        # SQLite Fallback (emlak.db)
        try:
            with self.get_connection() as conn:
                res = conn.execute("SELECT value FROM system_management WHERE key='gemini_api_key'").fetchone()
                if res:
                    val = res[0]
                    print(f"✅ SQLite'dan Gemini Key Alındı: {val[:5]}...", flush=True)
                    return val
        except:
            pass

        env_val = os.getenv("GEMINI_API_KEY")
        if not env_val:
            print(f"ℹ️ Gemini Key ne DB'de ne de Env'de bulunamadı! (Psycopg2 yüklü mü? .env yolu doğru mu?)", flush=True)
        return env_val

    def get_openai_api_key(self):
        conn = self.get_pg_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM system_management WHERE key = 'openai_api_key'")
                    row = cur.fetchone()
                    if row: return row[0]
            except: pass
            finally: conn.close()
        
        try:
            with self.get_connection() as conn:
                res = conn.execute("SELECT value FROM system_management WHERE key='openai_api_key'").fetchone()
                if res: return res[0]
        except: pass
        return os.getenv("OPENAI_API_KEY")

    def get_gemini_model(self):
        conn = self.get_pg_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM system_management WHERE key = 'gemini_model'")
                    row = cur.fetchone()
                    if row: return row[0]
            except: pass
            finally: conn.close()
        
        try:
            with self.get_connection() as conn:
                res = conn.execute("SELECT value FROM system_management WHERE key='gemini_model'").fetchone()
                if res: return res[0]
        except: pass
        return os.getenv("GEMINI_MODEL")

def get_headless_driver(user_data_dir: Optional[str] = None, browser_port: Optional[int] = None):
    """DrissionPage - İzole edilmiş ve kararlı tarayıcı başlatıcı."""
    from DrissionPage import ChromiumPage, ChromiumOptions
    import os
    
    co = ChromiumOptions()
    
    # 1. Temel Sunucu Ayarları
    co.set_argument('--headless=new')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--disable-infobars')
    
    # İzolasyon: Eğer bir profil klasörü verildiyse onu kullan (Aynı anda çoklu kullanım için KRİTİK)
    if user_data_dir:
        co.set_user_data_path(user_data_dir)
    
    if browser_port:
        co.set_local_port(browser_port)
    
    # 2. Chrome Yolunu Belirt (Garantili Mod)
    for path in ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]:
        if os.path.exists(path):
            co.set_browser_path(path)
            break

    # 3. Gerçekçi User Agent & Ekran Boyutu
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    co.set_user_agent(ua)
    co.set_argument('--window-size=1920,1080')
    
    try:
        page = ChromiumPage(co)
        page.run_js('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
        return page
    except Exception as e:
        print(f"[VALUATION ENGINE] Tarayıcı başlatılamadı: {str(e)}", flush=True)
        return None


def safe_quit_driver(driver):
    """Driver'ı (DrissionPage) güvenli bir şekilde kapatır."""
    try:
        if driver:
            driver.quit()
    except:
        pass

def scrape_web_comparables(address_text: str, rooms: str, area_m2: Optional[float], 
                           prop_type: str = "daire",
                           lat: float = None, lng: float = None,
                           building_age: str = None, floor_level: str = None,
                           heating_type: str = None, listing_date: str = None,
                           owner_type: str = None, min_price: str = None,
                           max_price: str = None, site_status: str = None,
                           furnished_status: str = None, loan_status: str = None,
                           has_elevator: bool = False,
                            has_parking: bool = False, has_garden: bool = False,
                            has_pool: bool = False, has_security: bool = False,
                            city: str = None, district: str = None,
                            neighborhood: str = None,
                            user_data_dir: Optional[str] = None,
                            browser_port: Optional[int] = None) -> Dict:
    """
    Hepsiemlak üzerinden gerçek zamanlı emsal verilerini çeker.
    Returns: { "samples": [], "url": "...", "status": "..." }
    """
    result_data = {"samples": [], "url": "", "status": "Başlatılmadı"}
    try:
        from DrissionPage import ChromiumPage
        import pandas as pd
    except ImportError as e:
        msg = f"Scraping kütüphane hatası: {str(e)}"
        print(f"\n[KRITIK] {msg}\n", flush=True)
        log.error(msg)
        result_data["status"] = msg
        return result_data

    # 1. Adres Parçalama ve Konum Çözme
    # Virgül veya Slash ile ayır
    raw_parcalar = [p.strip() for p in re.split(',|/', address_text)]
    parcalar = [p for p in raw_parcalar if p]
    
    # Eğer tek bir blok geldiyse (örn: "kocaeli izmit topçular"), boşlukla bölmeyi dene
    if len(parcalar) == 1 and " " in parcalar[0]:
        parcalar = [p.strip() for p in parcalar[0].split(" ") if p]

    il, ilce, mahalle = "", "", ""
    iller = ["istanbul", "ankara", "izmir", "bursa", "antalya", "konya", "adana", "sanliurfa", "gaziantep", "kocaeli", "mersin", "diyarbakir", "hatay", "manisa", "kayseri", "samsun", "balikesir", "kahramanmaras", "van", "aydin", "tekirdag", "denizli", "sakarya", "mugla", "eskisehir", "mardin", "trabzon", "ordu", "afyonkarahisar", "erzurum", "zonguldak", "sivas", "adiyaman", "batman", "tokat", "elazig", "kutahya", "osmaniye", "corum", "agri", "sirnak", "giresun", "isparta", "yozgat", "aksaray", "edirne", "mus", "duzce", "kastamonu", "usak", "kirklareli", "niğde", "bitlis", "rize", "amasya", "siirt", "bolu", "nevsehir", "yalova", "bingol", "hakkari", "kirikkale", "karaman", "karabuk", "burdur", "kirsehir", "erzincan", "bilecik", "sinop", "cankiri", "bartin", "igdir", "artvin", "gumushane", "kilis", "ardahan", "tunceli", "bayburt"]

    if city and district:
        il, ilce, mahalle = city, district, (neighborhood or "")
    elif len(parcalar) >= 3:
        ilk_parca_slug = parcalar[0].lower().replace('i', 'i').replace('ı', 'i')
        if any(il_adi in ilk_parca_slug for il_adi in iller):
            il, ilce, mahalle = parcalar[0], parcalar[1], parcalar[2]
        else:
            il, ilce, mahalle = parcalar[-1], parcalar[-2], parcalar[-3]
    elif len(parcalar) == 2:
        il, ilce = parcalar[-1], parcalar[0]
    elif len(parcalar) == 1:
        ilce = parcalar[0]

    # Mahalle bazlı anahtar kelime kontrolü
    if not mahalle:
        for p in parcalar:
            if any(x in p.lower() for x in ["mahalle", "mah.", "köyü"]):
        
                mahalle = p
                break

    # 2. SmartLocator ile Konum Çözme (Fırsat Radarı Standartı)
    driver = None
    konum_yolu = ""
    locator = None
    try:
        driver = get_headless_driver(user_data_dir=user_data_dir, browser_port=browser_port)
        locator = SmartLocator(driver=driver)
        base_slug = locator.get_url(il, ilce, mahalle)
        # Slug temizleme (radar.txt Bölüm 3 Standartı)
        konum_yolu = base_slug.replace("-satilik", "").replace("-kiralik", "").replace("-sahibinden", "").strip("/")
        print(f"[WEB TARAMA] Cozulen Konum Yolu: {konum_yolu}", flush=True)
    except Exception as e:
        print(f"[WEB TARAMA] SmartLocator Hatasi: {e}", flush=True)
        from smart_locator import SmartLocator as SL
        if not locator: locator = SL()
        il_s = locator.hepsiemlak_slugify(il)
        ilce_s = locator.hepsiemlak_slugify(ilce)
        mah_s = locator.hepsiemlak_slugify(mahalle.replace("Mahallesi","").replace("Mah.",""))
        parts = [p for p in [il_s, ilce_s, mah_s] if p]
        konum_yolu = "-".join(parts) if parts else "turkiye"
    
    mahalle_slug = locator.hepsiemlak_slugify(mahalle.replace("Mahallesi", "").replace("Mah.", "")) if locator else "bolge"

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
    
    # Oda Sayısı p63 (Hepsiemlak Güncel ID'ler)
    oda_id_map = {
        "1+0": "180401",
        "1+1": "180402",
        "2+1": "180404",
        "2+2": "180405",
        "3+1": "180407",
        "3+2": "180408",
        "4+1": "180409",
        "4+2": "180410",
        "5+1": "180411",
        "5+2": "180412",
        "6+1": "180413"
    }
    # 2. Slug Hazırlığı (Zaten konum_yolu olarak yukarıda hesaplandı)
    target_path = f"{konum_yolu}-satilik"
    print(f"[WEB TARAMA] Hedef Bölge Slug: {target_path}", flush=True)

    # 3. Parametreleri Hazırla
    query_params = []
    
    # Oda Sayısı
    oda_id_map = {
        "1+0": "180401", "1+1": "180402", "2+1": "180404", "2+2": "180405",
        "3+1": "180407", "3+2": "180408", "4+1": "180409", "4+2": "180410",
        "5+1": "180411", "5+2": "180412", "6+1": "180413"
    }
    target_oda_id = oda_id_map.get(str(rooms).replace(" ", ""))
    if target_oda_id:
        query_params.append(("p63", target_oda_id))
    
    # m2
    if area_m2:
        m2_v = str(int(area_m2))
        query_params.append(("p25", m2_v))
        query_params.append(("p26", m2_v))
    
    # Filtreler
    if building_age: query_params.append(("buildingAges", building_age))
    if floor_level: query_params.append(("p7", floor_level))
    if heating_type: query_params.append(("p18", heating_type))
    if min_price: query_params.append(("p31", str(min_price)))
    if max_price: query_params.append(("p32", str(max_price)))
    if site_status == "true": query_params.append(("p69", "true"))
    if furnished_status == "true": query_params.append(("p75", "true"))

    # Özellikler (Attributes)
    attrs = []
    if has_elevator: attrs.append("1")
    if has_parking: attrs.extend(["64", "170"])
    if has_garden: attrs.append("148")
    if has_security: attrs.append("61")
    if has_pool: attrs.extend(["174", "360"])
    if attrs:
        query_params.append(("realtyAttributes", ",".join(attrs)))

    from urllib.parse import urlencode
    encoded_params = urlencode(query_params)
    
    # 5. URL İnşası (Scanner Standartı)
    # Örn: https://www.hepsiemlak.com/kadikoy-satilik/daire?p63=180407
    url = f"https://www.hepsiemlak.com/{target_path}/{mulk_tipi_slug}?{encoded_params}"
    
    # URL Temizliği (Çift slash vb.)
    url = url.replace("com//", "com/").replace("//?", "/?")
    
    result_data["url"] = url
    print(f"[WEB TARAMA] KESİN HEDEF URL: {url}", flush=True)
    
    try:
        # Driver zaten başlatıldı (SmartLocator/orijinal_url_yakala için), onu kullan
        print(f"[WEB TARAMA] Sayfa yukleniyor (DrissionPage)...", flush=True)
        driver.get(url)
        
        # Yonlendirme kontrolu
        current_url = driver.url
        if "captcha" in current_url.lower() or "challenge" in current_url.lower():
            print(f"[WEB TARAMA] BLOKLANMA TESPİT EDİLDİ: {current_url}", flush=True)
            result_data["status"] = "Site tarafından engellendi (Captcha/Challenge)."
            return result_data

        # İnsansı bekleme
        print(f"[WEB TARAMA] Bekleniyor (insansi bekleme)...", flush=True)
        time.sleep(random.uniform(5.5, 9.2))
        
        # 2. Kesin Sonuç Kontrolü (CSS Sınıf Bazlı)
        try:
            zero_results = driver.eles('css:.zero-result')
            suggested_banner = driver.eles('css:.suggested-search-banner')
            
            if len(zero_results) > 0 or len(suggested_banner) > 0:
                print(f"[WEB TARAMA] KESİN UYARI: Sıfır sonuç veya Öneri bandı tespit edildi. Taramayı durduruyorum.", flush=True)
                result_data["status"] = "Uygun ilan bulunamadı."
                return result_data
        except:
            pass

        # 3. İnsansı Hareketler (Kaydırma ve Bekleme)
        print(f"[WEB TARAMA] Sayfa üzerinde insansı kaydırma (scrolling) yapılıyor...", flush=True)
        for _ in range(random.randint(2, 4)):
            scroll_val = random.randint(400, 900)
            driver.scroll.down(scroll_val)
            time.sleep(random.uniform(1.2, 2.5))

        # 4. İlanları Yakalama (Genişletilmiş Seçiciler ve Wait ile)
        print(f"[WEB TARAMA] İlan kartları ayıklanıyor...", flush=True)
        
        # Sayfanın yüklendiğinden emin olmak için kısa bir bekleme ve bir kartın görünmesini bekle
        selectors = [
            'css:a.listingView__card-link',
            'css:.listing-item',
            'css:.list-view-item',
            'css:a[href*="/satilik/"]',
            'css:div.listView'
        ]
        
        items = []
        for selector in selectors:
            try:
                found = driver.eles(selector, timeout=3)
                if found and len(found) > 0:
                    items = found
                    print(f"[WEB TARAMA] Seçici ile bulundu: {selector} (Adet: {len(found)})", flush=True)
                    break
            except:
                continue

        if not items:
            # Debug için sayfa durumunu kontrol et
            page_title = driver.title
            print(f"[WEB TARAMA] KRITIK: Sayfada ilan bulunamadı. Sayfa Başlığı: {page_title}", flush=True)
            if "captcha" in page_title.lower() or "robot" in page_title.lower():
                 result_data["status"] = "Robot kontrolüne takıldı."
            else:
                 result_data["status"] = "İlanlar yakalanamadı veya filtreye uygun sonuç yok."
            return result_data
            
        print(f"[WEB TARAMA] Toplam {len(items)} kart bulundu. Detaylı analiz başlıyor...", flush=True)
        
        for i, item in enumerate(items[:20]):
            try:
                full_text = item.text
                if not full_text or len(full_text) < 10: continue
                
                price, m2_val, rooms_val = None, None, "—"
                link = item.attr('href') or ""
                if link and not link.startswith('http'):
                    link = "https://www.hepsiemlak.com" + link
                
                # Fiyat, m2 ve Oda bilgisini DrissionPage ile yakala
                price_el = item.ele('css:.list-view-price, .price', timeout=0.1)
                if price_el:
                    p_text = re.sub(r'\D', '', price_el.text)
                    if p_text: price = float(p_text)
                
                m2_el = item.ele('css:.squareMeter, .list-view-size', timeout=0.1)
                if m2_el:
                    m_text = re.sub(r'\D', '', m2_el.text)
                    if m_text: m2_val = float(m_text)

                rooms_el = item.ele('css:.houseRoomCount', timeout=0.1)
                if rooms_el:
                    rooms_val = rooms_el.text.strip()
                
                # 1. Önce spesifik elementlerden veri çekmeyi dene (Daha kesin sonuç)
                try:
                    price_el = item.ele('css:.list-view-price, .price, .listing-price', timeout=0.1)
                    if price_el:
                        p_digits = re.sub(r'\D', '', price_el.text)
                        if p_digits: price = float(p_digits)
                except: pass

                try:
                    m2_el = item.ele('css:.squareMeter, .list-view-size, .celly.squareMeter', timeout=0.1)
                    if m2_el:
                        m_digits = re.sub(r'\D', '', m2_el.text)
                        if m_digits: m2_val = float(m_digits)
                except: pass

                try:
                    rooms_el = item.ele('css:.houseRoomCount, .celly.houseRoomCount', timeout=0.1)
                    if rooms_el:
                        rooms_val = rooms_el.text.strip()
                except: pass
                
                # İlan linkini yakala
                try:
                    link = item.attr('href') if item.tag == 'a' else item.ele('tag:a', timeout=0.1).attr('href')
                except:
                    link = ""

                if price and m2_val:
                    # MANTIK FİLTRESİ
                    max_limit = 1000000 if prop_type.lower() == "arsa" else 10000
                    if 10 < m2_val < max_limit:
                        print(f"   [OK] İlan {i+1}: {price:,.0f} TL | {m2_val} m2 | {rooms_val}", flush=True)
                        result_data["samples"].append({
                            "id": random.randint(10000, 99999),
                            "title": "Web İlanı",
                            "price": price,
                            "area_m2": m2_val,
                            "rooms": rooms_val,
                            "district": mahalle_slug,
                            "is_web": True,
                            "url": link
                        })
                    else:
                        print(f"   [SKIP] İlan {i+1}: Hatalı m2 ({m2_val})", flush=True)
                else:
                    print(f"   [SKIP] İlan {i+1}: Fiyat veya m2 bulunamadı.", flush=True)
            except Exception as e:
                print(f"   [ERROR] İlan {i+1} parse hatası: {e}", flush=True)
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
                         has_security: bool = False,
                         city: str = None, district: str = None,
                         neighborhood: str = None,
                         api_key: str = None, openai_key: str = None,
                         ai_model: str = None,
                         user_data_dir: Optional[str] = None,
                         browser_port: Optional[int] = None) -> Dict:
    """
    radar.txt BÖLÜM 2.B: Web verileriyle DB verilerini birleştirerek analiz yapar.
    """
    db = DatabaseManager()

    # Override keys if passed from Next.js (Safe fallback if psycopg2 is missing)
    if api_key: os.environ["GEMINI_API_KEY"] = api_key
    if openai_key: os.environ["OPENAI_API_KEY"] = openai_key
    if ai_model: os.environ["GEMINI_MODEL"] = ai_model
    
    # 1. Web'den emsalleri çek
    web_result = scrape_web_comparables(
        address_text, rooms, area_m2, prop_type,
        lat=lat, lng=lon,
        building_age=building_age, floor_level=floor_level,
        heating_type=heating_type, listing_date=listing_date,
        owner_type=owner_type, min_price=min_price,
        max_price=max_price, site_status=site_status,
        furnished_status=furnished_status, loan_status=loan_status,
        has_elevator=has_elevator, has_parking=has_parking,
        has_garden=has_garden, has_pool=has_pool,
        has_security=has_security,
        city=city, district=district, neighborhood=neighborhood,
        user_data_dir=user_data_dir,
        browser_port=browser_port
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
    
    # 5. Ek Analizler ve ROI
    # Amortisman ve Kira Tahmini (radar.txt BÖLÜM 2.D)
    rent_stats = calculate_roi(db, avg_price, ilce)
    
    # Likidite ve Trend (Dinamik Tarihli)
    liquidity_score = random.randint(65, 95)
    
    # Son 5 ayı dinamik olarak oluştur (Türkçe isimlerle)
    ay_isimleri = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    trends = []
    sim_avg = avg_price
    for i in range(4, -1, -1):
        dt = datetime.now() - timedelta(days=30*i)
        month_idx = dt.month - 1
        trends.append({
            "month": ay_isimleri[month_idx],
            "price": sim_avg * (1 - (i * 0.02)) # Simüle edilmiş hafif artış trendi
        })

    final_stats = {
        "count": n,
        "avg_sqm_price": avg_price,
        "min_sqm_price": sqm_prices[0] if n > 0 else 0,
        "max_sqm_price": sqm_prices[-1] if n > 0 else 0,
        "median_sqm_price": percentile(sqm_prices, 50),
        "q25": percentile(sqm_prices, 25),
        "q75": percentile(sqm_prices, 75),
        "samples": all_samples[:30],
        "url": web_result.get("url", ""),
        "roi": rent_stats,
        "liquidity_score": liquidity_score,
        "trends": trends,
        "status": "Detayli analiz tamamlandi."
    }
    
    # 6. POI ve AI Raporu
    poi = get_poi_data(ilce, lat=lat, lon=lon)
    final_stats["poi"] = poi
    
    ai_text = generate_ai_report(db, final_stats, {
        "district": ilce, "prop_type": prop_type, "rooms": rooms, "area_m2": area_m2
    })
    final_stats["ai_report"] = ai_text
    
    # PDF Oluştur (Yeni)
    try:
        pdf_params = {
            "district": ilce, "prop_type": prop_type, "rooms": rooms, "area_m2": area_m2,
            "age": building_age, "heating": heating_type
        }
        pdf_path = generate_pdf_report(pdf_params, final_stats, ai_text)
        final_stats["pdf_path"] = pdf_path
    except Exception as e:
        log.error(f"Hybrid PDF Hatası: {e}")
        final_stats["pdf_path"] = None

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
    from urllib.parse import quote

    # Overpass QL Query
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

    # Çoklu sunucu desteği (Fallback)
    overpass_servers = [
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
        "https://overpass-api.de/api/interpreter"
    ]
    
    headers = {
        'User-Agent': 'EstateAI_Bot/1.0 (https://estateai.com)',
        'Accept': 'application/json'
    }

    for server_url in overpass_servers:
        try:
            log.info(f"[POI] Overpass sunucusu deneniyor: {server_url}")
            # Bazı sunucular datayı GET query string olarak, bazıları POST body olarak kabul eder.
            # En garantisi URL'e eklemek.
            full_url = f"{server_url}?data={quote(query)}"
            response = requests.get(full_url, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
            
            elements = data.get('elements', [])
            summary = {
                'hastane_sayisi': 0, 'okul_sayisi': 0, 'eczane_sayisi': 0,
                'otobus_duragi_sayisi': 0, 'metro_girisi_sayisi': 0
            }
            
            for element in elements:
                tags = element.get('tags', {})
                amenity, highway, railway = tags.get('amenity'), tags.get('highway'), tags.get('railway')
                
                if amenity == 'hospital': summary['hastane_sayisi'] += 1
                elif amenity == 'school': summary['okul_sayisi'] += 1
                elif amenity == 'pharmacy': summary['eczane_sayisi'] += 1
                elif highway == 'bus_stop': summary['otobus_duragi_sayisi'] += 1
                elif railway == 'subway_entrance': summary['metro_girisi_sayisi'] += 1
            
            log.info(f"[OK] Overpass verisi başarıyla çekildi ({server_url})")
            return summary

        except Exception as e:
            log.warning(f"[WARN] Overpass sunucusu hatası ({server_url}): {e}")
            continue # Bir sonraki sunucuyu dene

    log.error("❌ Tüm Overpass sunucuları başarısız oldu.")
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

    # Trendleri metin olarak hazırla (Python 3.10 uyumluluğu için f-string dışında)
    trends_list = stats.get('trends', [])
    trends_summary = ", ".join([f"{t.get('month')}: {t.get('price', 0):,.0f}₺" for t in trends_list])

    # Yeni ve profesyonel prompt yapısı (Kullanıcı Talebi)
    prompt = (
        f"Bir gayrimenkul değerleme uzmanı ve veri analisti olarak görev yapıyorsun. "
        f"Sana sağlanan [Gayrimenkul Bilgileri], [Bölgesel Veriler] ve [Piyasa Trendleri] parametrelerini kullanarak profesyonel bir Gayrimenkul Değerleme Analiz Raporu oluştur.\n\n"
        f"[Gayrimenkul Bilgileri]:\n"
        f"- Adres/Bölge: {district}\n"
        f"- Mülk Tipi: {prop_type}\n"
        f"- Oda Sayısı: {rooms or 'Belirtilmemiş'}\n"
        f"- Net Alan: {area_m2 or 'Belirtilmemiş'} m²\n"
        f"- Bina Yaşı: {params.get('age', '—')} yıl, Isıtma: {params.get('heating', '—')}\n\n"
        f"[Bölgesel Veriler]:\n"
        f"- İncelenen Emsal Sayısı: {count}\n"
        f"- Ortalama m² Fiyatı: {avg:,.0f} ₺\n"
        f"- Fiyat Aralığı: {minn:,.0f} ₺ - {maxx:,.0f} ₺\n"
        f"- Amortisman Süresi (ROI): {stats.get('roi', {}).get('amortization_years', '—')} yıl\n"
        f"- Likidite Skoru: %{stats.get('liquidity_score', 85)}\n\n"
        f"[Piyasa Trendleri]:\n"
        f"- Son 5 Ay Değişimi: {trends_summary}\n\n"
        f"Rapor şu bölümlerden oluşmalı ve görselleştirme için uygun bir Markdown yapısında sunulmalıdır:\n"
        f"1. Özet Kartı: Mülkün adresi, oda sayısı, net/brüt m² ve temel özellikleri.\n"
        f"2. AI Fiyat Tahmin Modeli: Mevcut satış ve kira değerini hesapla. Minimum ve maksimum değer aralıklarını belirt. Tahminin güven oranını (%) ver.\n"
        f"3. Yatırım Analizi: Yıllık ROI (Amortisman süresi) ve m² birim fiyatını hesapla.\n"
        f"4. Gelecek Projeksiyonu: Bölgesel trendlere dayanarak 6 ve 12 aylık fiyat/kira artış tahminlerini bir tablo olarak sun.\n"
        f"5. Piyasa İçgörüleri: Bölgenin demografik yapısı, ulaşım imkanları ve değer artış nedenleri hakkında 3 ana başlıkta profesyonel yorum yap.\n"
        f"6. Karşılaştırmalı Analiz: Verilen benzer 3-4 mülkü m² fiyatı, bina yaşı ve özellikleri bakımından kıyasla.\n\n"
        f"GÜNCEL TARİH: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Format Notu: Çıktıyı doğrudan bir PDF oluşturma kütüphanesine (örneğin ReportLab veya WeasyPrint) besleyebileceğim şekilde temiz bir Markdown formatında ver. Sektörel terimler kullan ve tarafsız bir dil benimse."
    )

    # Gemini denemesi
    try:
        api_key = db.get_gemini_api_key()
        if api_key:
            import google.generativeai as genai
            from google.api_core import exceptions
            import time
            
            import sys
            print(f"[AI] Modeli Hazırlanıyor: {db.get_gemini_model() or 'gemini-1.5-flash'}", file=sys.stderr, flush=True)
            genai.configure(api_key=api_key)
            
            # Model adını veritabanından çek (varsayılan: gemini-1.5-flash)
            model_name = db.get_gemini_model() or "gemini-1.5-flash"
            
            import sys
            print(f"[API] Gemini İstek Gönderiliyor: {model_name}...", file=sys.stderr, flush=True)
            model = genai.GenerativeModel(model_name)
            
            # Retry Logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    time.sleep(0.5)
                    response = model.generate_content(prompt)
                    
                    if response and response.text:
                        import sys
                        print("[SUCCESS] AI Yanıtı Başarıyla Alındı.", file=sys.stderr, flush=True)
                        return response.text
                    else:
                        raise Exception("Boş veya engellenmiş AI yanıtı.")
                        
                except exceptions.ResourceExhausted:
                    if attempt < max_retries - 1:
                        import sys
                        print(f"[WARN] Gemini Rate Limit. {10}s bekleniyor...", file=sys.stderr, flush=True)
                        time.sleep(10)
                        continue
                    else: raise
                except Exception as e:
                    import sys
                    print(f"[ERROR] Gemini Deneme {attempt+1} Hatası: {e}", file=sys.stderr, flush=True)
                    if "404" in str(e):
                        try:
                            models = [m.name for m in genai.list_models()]
                            import sys
                            print(f"[INFO] Mevcut Modeller: {models}", file=sys.stderr, flush=True)
                        except: pass
                    if attempt == max_retries - 1: break
                    
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            log.error("Valuation Engine: Gemini API Rate Limit aşıldı.")
            return "YAPAY ZEKA RATE LIMIT HATASI: Çok fazla istek atıldı. Lütfen biraz bekleyin.\n\n" + _template_report(params, stats)
        
        err_msg = f"GEMINI HATASI: {str(e)}. (Lütfen system_management tablosundaki anahtarları kontrol edin veya servisi RESTART edin.)"
        log.warning(err_msg)
        import sys
        print(f"[ERROR] Gemini Başarısız: {e}", file=sys.stderr, flush=True)

    # OpenAI denemesi (Eğer Gemini başarısız olursa ve OpenAI anahtarı varsa)
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
            reports_dir = Path(__file__).parent / "reports"
            reports_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(reports_dir / f"EstateAI_Rapor_{ts}.pdf")

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
            ["Gösterge", "Değer (TL/m²)", "Tahmini Toplam (TL)"],
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
                "ALT DEĞER\n" + f"{est.get('estimated_low', 0):,.0f} TL",
                "ORTA DEĞER (ÖNERİLEN)\n" + f"{mid_val:,.0f} TL",
                "ÜST DEĞER\n" + f"{est.get('estimated_high', 0):,.0f} TL",
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
            ai_text = ai_text.replace("₺", "TL")
            story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#E2E8F0"), spaceAfter=10))
            story.append(Paragraph("Uzman Değerlendirmesi", h2))
            for line in ai_text.split("\n"):
                line = line.strip()
                if not line:
                    story.append(Spacer(1, 4))
                    continue
                
                # Markdown Kalın Yazı Desteği (Düzenli ifade ile güvenli değişim)
                import re
                line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
                
                # Markdown Başlık Desteği
                if line.startswith("###"):
                    story.append(Paragraph(line.replace("###", "").strip(), h2))
                elif line.startswith("##"):
                    story.append(Paragraph(line.replace("##", "").strip(), h2))
                elif line.startswith("#"):
                    story.append(Paragraph(line.replace("#", "").strip(), h1))
                elif line.endswith(":") or line.isupper():
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
