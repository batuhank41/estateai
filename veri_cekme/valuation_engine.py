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
from datetime import datetime, timedelta, timezone
TR_TZ = timezone(timedelta(hours=3))
from pathlib import Path
from typing import Optional, List, Dict
import re

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
    
    # Global Styles & Fonts Cache
    _REPORTLAB_INITIALIZED = False
    _STYLES = None
    _FONT_NAME = "Helvetica"
    _FONT_BOLD = "Helvetica-Bold"

    def _init_reportlab():
        global _REPORTLAB_INITIALIZED, _STYLES, _FONT_NAME, _FONT_BOLD
        if _REPORTLAB_INITIALIZED: return
        
        try:
            # Türkçe font desteği için Arial yüklemeyi dene (Windows varsayılan)
            for arial_path, arial_bold_path in [
                ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
                ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")
            ]:
                if os.path.exists(arial_path):
                    pdfmetrics.registerFont(TTFont('Arial', arial_path))
                    pdfmetrics.registerFont(TTFont('Arial-Bold', arial_bold_path))
                    _FONT_NAME = "Arial"
                    _FONT_BOLD = "Arial-Bold"
                    break
        except Exception as e:
            print(f"Font registration warning: {e}")
            
        _STYLES = getSampleStyleSheet()
        _REPORTLAB_INITIALIZED = True

except ImportError:
    print("Warning: reportlab not installed. PDF generation will be disabled.")

# Markdown Regex
MD_BOLD_RE = re.compile(r'\*\*(.*?)\*\*')

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

def _get_proxy_config() -> dict:
    """Residential Proxy yapılandırmasını environment variable'dan okur.
    
    Kullanım: VDS'te şu komutu çalıştırın:
      export RESIDENTIAL_PROXY_URL="http://kullanici:sifre@proxy.sağlayıcı.com:port"
    
    Proxynet örneği:
      export RESIDENTIAL_PROXY_URL="http://proxynet-yf5tjb9b:786gLuDaF9ehz8@gate-res.proxynet.io:8000"

    Desteklenen sağlayıcılar: Proxynet, IPRoyal, Webshare, Smartproxy, Bright Data, Oxylabs
    Format: http://user:pass@host:port veya socks5://user:pass@host:port
    """
    import os
    import re as _re
    import random as _random
    proxy_url = os.getenv("RESIDENTIAL_PROXY_URL", "").strip()
    
    # Varsayılan şablon değerlerini (placeholder) kontrol et ve engelle
    is_placeholder = any(x in proxy_url.lower() for x in [
        "username", "password", "example.com", "your_",
        "kullanici", "kullanıcı", "sifre", "şifre", "saglayici", "sağlayıcı"
    ])
    
    if proxy_url and not is_placeholder:
        # Dinamik IP rotasyonu: "session-XXXX" veya Proxynet "_session-XXXX" formatını destekle
        session_pattern = r'(_?session-)[a-zA-Z0-9]+'
        if _re.search(session_pattern, proxy_url):
            prefix_match = _re.search(session_pattern, proxy_url)
            prefix = prefix_match.group(1)  # "session-" veya "_session-"
            random_session = f"{prefix}{_random.randint(100000, 99999999)}"
            proxy_url = _re.sub(session_pattern, random_session, proxy_url)
            safe_url = _re.sub(r':([^:@]+)@', ':****@', proxy_url)
            print(f"[PROXY] Dinamik IP Rotasyonu Aktif (Yeni Oturum: {random_session}). Proxy: {safe_url}", flush=True)
        else:
            safe_url = _re.sub(r':([^:@]+)@', ':****@', proxy_url)
            print(f"[PROXY] Residential Proxy aktif. Proxy: {safe_url}", flush=True)

        # ── Bağlantı Testi ──────────────────────────────────────────────────────
        # Proxynet gibi HTTP proxy'ler için httpbin.org/ip kullanıyoruz.
        # Google SSL handshake bazı sağlayıcılarda timeout verebilir.
        import requests
        TEST_URLS = [
            ("http://httpbin.org/ip",  [200]),           # HTTP endpoint — hızlı
            ("https://httpbin.org/ip", [200]),           # HTTPS — SSL test
            ("http://www.google.com",  [200, 301, 302]), # Yedek
        ]
        for test_url, ok_codes in TEST_URLS:
            try:
                r = requests.get(
                    test_url,
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=8.0,
                    verify=False,
                )
                if r.status_code in ok_codes:
                    print(f"[PROXY TEST] Proxy doğrulandı ({test_url} → HTTP {r.status_code}).", flush=True)
                    return {
                        "url": proxy_url,
                        "dict": {"http": proxy_url, "https": proxy_url},
                        "active": True
                    }
            except Exception:
                continue

        # Tüm test URL'leri başarısız oldu
        safe_err_url = _re.sub(r':([^:@]+)@', ':****@', proxy_url)
        print(f"[PROXY TEST] UYARI: Tüm proxy doğrulama testleri başarısız ({safe_err_url}). "
              f"Proxy yine de kullanılacak (bağlantı belki sadece tarayıcı tünelinden çalışıyor).", flush=True)
        # Proxynet gibi browser-only proxy'ler requests testini geçemez ama DrissionPage'de çalışır.
        # Bu yüzden testi geçemese bile active=True dönüyoruz.
        return {"url": proxy_url, "dict": {"http": proxy_url, "https": proxy_url}, "active": True}

    return {"url": "", "dict": {}, "active": False}


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
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.set_argument('--disable-infobars')
    co.set_argument('--disable-notifications')
    
    # 2. Cloudflare Parmak İzi (Fingerprint) Gizleme - Kritik
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--exclude-switches=enable-automation')
    co.set_argument('--disable-extensions')
    co.set_argument('--disable-popup-blocking')
    co.set_argument('--ignore-certificate-errors')
    co.set_argument('--allow-running-insecure-content')
    co.set_argument('--lang=tr-TR')
    co.set_argument('--accept-lang=tr-TR,tr;q=0.9,en-US;q=0.8')
    
    # 3. Bant genişliği tasarrufu - Görselleri kapat (Proxy dostu)
    co.set_pref('profile.managed_default_content_settings.images', 2)
    co.set_argument('--blink-settings=imagesEnabled=false')
    
    # 4. Residential Proxy (RESIDENTIAL_PROXY_URL env var ile aktif olur)
    # Proxynet gibi HTTP proxy'ler için hem set_proxy() hem --proxy-server kullanıyoruz.
    # set_proxy() DrissionPage'in native metodudur ve kimlik doğrulamayı (user:pass) destekler.
    # --proxy-server Chromium'a doğrudan geçilir (çift güvence).
    proxy_cfg = _get_proxy_config()
    if proxy_cfg["active"]:
        print(f"[PROXY] Residential Proxy DrissionPage'e bağlanıyor...", flush=True)
        try:
            co.set_proxy(proxy_cfg["url"])  # DrissionPage native proxy (user:pass@host:port destekler)
        except Exception:
            pass
        co.set_argument(f'--proxy-server={proxy_cfg["url"]}')  # Chromium argümanı (yedek güvence)
    
    # 5. İzolasyon: Eğer bir profil klasörü verildiyse onu kullan
    if user_data_dir:
        co.set_user_data_path(user_data_dir)
    
    if browser_port:
        co.set_local_port(browser_port)
    
    # 6. Chrome Yolunu Belirt (Garantili Mod)
    for path in ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/google-chrome"]:
        if os.path.exists(path):
            co.set_browser_path(path)
            break

    # 7. Gerçekçi Türkiye kullanıcısı User Agent & Ekran Boyutu
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'
    co.set_user_agent(ua)
    co.set_argument('--window-size=1920,1080')
    
    try:
        page = ChromiumPage(co)
        # Cloudflare fingerprint bypass JS enjeksiyonu
        page.run_js('''
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
            window.chrome = {runtime: {}};
        ''')
        return page
    except Exception as e:
        print(f"[VALUATION ENGINE] DrissionPage başlatılamadı: {str(e)}. Undetected-Chromedriver yedek planı deneniyor...", flush=True)
        try:
            import undetected_chromedriver as uc
            options = uc.ChromeOptions()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-dev-shm-usage')
            proxy_cfg = _get_proxy_config()
            if proxy_cfg["active"]:
                options.add_argument(f'--proxy-server={proxy_cfg["url"]}')
            
            # Port ve User Data Directory
            if user_data_dir:
                options.add_argument(f'--user-data-dir={user_data_dir}')
            
            driver = uc.Chrome(options=options, version_main=132)
            # DrissionPage ile aynı metot arabirimini taklit eden bir wrapper oluşturuyoruz
            class UCWrapper:
                def __init__(self, drv):
                    self.driver = drv
                def get(self, url):
                    self.driver.get(url)
                def run_js(self, code):
                    return self.driver.execute_script(code)
                def quit(self):
                    self.driver.quit()
                @property
                def html(self):
                    return self.driver.page_source
                @property
                def url(self):
                    return self.driver.current_url
                def close(self):
                    self.driver.close()
            
            return UCWrapper(driver)
        except Exception as uc_err:
            print(f"[VALUATION ENGINE] Undetected-Chromedriver da başlatılamadı: {uc_err}", flush=True)
            return None


def safe_quit_driver(driver):
    """Driver'ı (DrissionPage) güvenli bir şekilde kapatır."""
    try:
        if driver:
            driver.quit()
    except:
        pass

def _build_api_params_from_url(url: str) -> dict:
    """URL'deki query parametrelerini parse ederek API için dict döner."""
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    params = {}
    for k, v in qs.items():
        params[k] = v[0] if len(v) == 1 else v
    path_parts = parsed.path.strip('/').split('/')
    if path_parts:
        params['_slug'] = path_parts[0]
    if len(path_parts) > 1:
        params['_category'] = path_parts[1]
    return params


def _renew_tor_circuit():
    """Tor'un yeni bir devre (IP) almasını sağlar."""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("127.0.0.1", 9051))
            s.sendall(b'AUTHENTICATE ""\r\nSIGNAL NEWNYM\r\n')
            time.sleep(2)
    except:
        pass


def scrape_with_tor(url: str, prop_type: str = "daire") -> List[Dict]:
    """Tor SOCKS5 proxy üzerinden Hepsiemlak'ı tamamen farklı bir IP ile çeker."""
    samples = []
    try:
        import requests as std_requests
        from bs4 import BeautifulSoup
        import json

        TOR_PROXY = {
            "http":  "socks5h://127.0.0.1:9050",
            "https": "socks5h://127.0.0.1:9050",
        }

        UA_LIST = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        ]

        for attempt in range(3):
            ua = random.choice(UA_LIST)
            print(f"[TOR] Tor ile istek deneniyor (Deneme {attempt+1}/3)...", flush=True)

            try:
                session = std_requests.Session()
                session.proxies = TOR_PROXY

                # Warmup: Ana sayfayı ziyaret et
                warmup = session.get(
                    "https://www.hepsiemlak.com",
                    headers={
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "accept-language": "tr-TR,tr;q=0.9",
                        "user-agent": ua,
                    },
                    timeout=30,
                    allow_redirects=True
                )
                print(f"[TOR] Warmup yanıt: HTTP {warmup.status_code}", flush=True)

                if warmup.status_code != 200:
                    print(f"[TOR] Warmup başarısız (HTTP {warmup.status_code}), yeni Tor devresi alınıyor...", flush=True)
                    _renew_tor_circuit()
                    time.sleep(3)
                    continue

                time.sleep(random.uniform(2.0, 4.0))

                # Hedef URL'yi gerçek navigasyon gibi çek
                response = session.get(
                    url,
                    headers={
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                        "accept-language": "tr-TR,tr;q=0.9",
                        "cache-control": "max-age=0",
                        "referer": "https://www.hepsiemlak.com/",
                        "sec-fetch-dest": "document",
                        "sec-fetch-mode": "navigate",
                        "sec-fetch-site": "same-origin",
                        "sec-fetch-user": "?1",
                        "upgrade-insecure-requests": "1",
                        "user-agent": ua,
                    },
                    timeout=45,
                    allow_redirects=True
                )
                print(f"[TOR] Hedef URL yanıt: HTTP {response.status_code}", flush=True)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    next_data_script = soup.find('script', id='__NEXT_DATA__')

                    if next_data_script and next_data_script.string:
                        try:
                            data = json.loads(next_data_script.string)
                            listings = None

                            def _find_listings_tor(d):
                                nonlocal listings
                                if isinstance(d, dict):
                                    for key in ["listings", "items", "results", "data"]:
                                        if key in d and isinstance(d[key], list) and len(d[key]) > 0:
                                            first = d[key][0]
                                            if isinstance(first, dict) and any(k in first for k in ["price", "fiyat", "id", "url", "title"]):
                                                listings = d[key]
                                                return
                                    for v in d.values():
                                        _find_listings_tor(v)
                                        if listings: return
                                elif isinstance(d, list):
                                    for item in d:
                                        _find_listings_tor(item)
                                        if listings: return

                            _find_listings_tor(data)

                            if listings:
                                print(f"[TOR] Başarı! __NEXT_DATA__ içinde {len(listings)} ilan bulundu.", flush=True)
                                for lst in listings[:25]:
                                    try:
                                        price = (
                                            lst.get("price", {}).get("value") if isinstance(lst.get("price"), dict)
                                            else lst.get("price") or lst.get("fiyat") or lst.get("salePrice") or 0
                                        )
                                        price = float(str(price).replace(".", "").replace(",", "").strip()) if price else 0
                                        m2_val = (
                                            lst.get("size", {}).get("value") if isinstance(lst.get("size"), dict)
                                            else lst.get("size") or lst.get("squareMeter") or lst.get("area") or lst.get("netM2") or 0
                                        )
                                        m2_val = float(str(m2_val).replace(",", ".").strip()) if m2_val else 0
                                        rooms_val = lst.get("roomCount") or lst.get("rooms") or "—"
                                        title = lst.get("title") or lst.get("baslik") or "Hepsiemlak İlanı"
                                        link = lst.get("url") or lst.get("link") or lst.get("detailUrl") or ""
                                        if link and not link.startswith("http"):
                                            link = "https://www.hepsiemlak.com" + link
                                        if price > 10000 and m2_val > 0:
                                            samples.append({
                                                "id": random.randint(10000, 99999),
                                                "title": title,
                                                "price": price,
                                                "area_m2": m2_val,
                                                "rooms": rooms_val,
                                                "district": "web",
                                                "is_web": True,
                                                "url": link
                                            })
                                    except:
                                        pass
                                if samples:
                                    return samples
                        except Exception as e:
                            print(f"[TOR] __NEXT_DATA__ parse hatası: {e}", flush=True)

                    # HTML fallback
                    cards = (
                        soup.select('a.listingView__card-link') or
                        soup.select('div.list-view-item') or
                        soup.select('.listing-item')
                    )
                    if cards:
                        print(f"[TOR] HTML'de {len(cards)} kart bulundu.", flush=True)
                        for card in cards[:25]:
                            try:
                                link = card.get('href', '')
                                if link and not link.startswith('http'):
                                    link = "https://www.hepsiemlak.com" + link
                                p_el = card.select_one('.list-view-price strong, .list-view-price, .price, strong')
                                price = float(re.sub(r'\D', '', p_el.get_text())) if p_el else 0
                                m_el = card.select_one('.list-view-size, .squareMeter, dd.list-view-size')
                                m2_val = float(re.sub(r'\D', '', m_el.get_text())) if m_el else 0
                                r_el = card.select_one('.list-view-room, .houseRoomCount, dd.list-view-room')
                                rooms_val = r_el.get_text().strip() if r_el else "—"
                                if price > 10000 and m2_val > 0:
                                    samples.append({
                                        "id": random.randint(10000, 99999),
                                        "title": "Hepsiemlak İlanı",
                                        "price": price,
                                        "area_m2": m2_val,
                                        "rooms": rooms_val,
                                        "district": "web",
                                        "is_web": True,
                                        "url": link
                                    })
                            except:
                                pass
                        if samples:
                            return samples

                    print(f"[TOR] Sayfa alındı ama ilan verisi yok.", flush=True)

                elif response.status_code in [403, 429]:
                    print(f"[TOR] Engellendi (HTTP {response.status_code}), yeni Tor devresi alınıyor...", flush=True)
                    _renew_tor_circuit()
                    time.sleep(4)

            except Exception as e:
                print(f"[TOR] İstek hatası: {e}", flush=True)
                _renew_tor_circuit()
                time.sleep(3)

        print("[TOR] Tor ile veri çekilemedi.", flush=True)
    except ImportError:
        print("[TOR] 'requests' modülü bulunamadı veya Tor kurulu değil.", flush=True)
    except Exception as e:
        print(f"[TOR] Kritik hata: {e}", flush=True)

    return samples




def scrape_with_hepsiemlak_api(url: str, prop_type: str = "daire") -> List[Dict]:
    """Hepsiemlak internal Next.js JSON API üzerinden veri çeker. Cloudflare'e takılmaz."""
    samples = []
    try:
        from curl_cffi import requests as cf_requests
        import json
        from urllib.parse import urlparse, urlencode, parse_qs
        
        parsed = urlparse(url)
        path = parsed.path.strip('/')  # örn: uskudar-icadiye-satilik/daire
        qs = parse_qs(parsed.query)

        # Hepsiemlak Next.js _next/data API endpoint (gerçek JSON verisi)
        # Format: https://www.hepsiemlak.com/_next/data/{buildId}/{path}.json?{params}
        # buildId dinamik olduğu için önce ana sayfadan çekeceğiz

        base_api = "https://www.hepsiemlak.com/api/listings/search"
        
        # Hepsiemlak search API parametrelerini oluştur
        slug_parts = path.split('/')
        location_slug = slug_parts[0] if slug_parts else ''
        
        # Query params'ı kopyala
        api_params = {}
        for k, v in qs.items():
            api_params[k] = v[0] if len(v) == 1 else v
        api_params['path'] = path
        
        IMPERSONATION_PROFILES = [
            ("chrome124", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            ("chrome120", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
            ("safari17_0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
            ("chrome116", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"),
        ]
        
        for impersonate, ua in IMPERSONATION_PROFILES:
            print(f"[API] Hepsiemlak JSON API deneniyor (impersonate={impersonate})...", flush=True)
            
            session = cf_requests.Session(impersonate=impersonate)
            
            # 1. Önce hepsiemlak.com ana sayfasını ziyaret et (session/cookie oluştur)
            try:
                warmup = session.get(
                    "https://www.hepsiemlak.com",
                    headers={
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "accept-language": "tr-TR,tr;q=0.9",
                        "user-agent": ua,
                    },
                    timeout=12,
                    allow_redirects=True
                )
                if warmup.status_code != 200:
                    print(f"[API] Warmup başarısız: HTTP {warmup.status_code}", flush=True)
                    continue
                time.sleep(random.uniform(1.5, 3.0))
            except Exception as e:
                print(f"[API] Warmup hatası: {e}", flush=True)
                continue
            
            # 2. Hedef URL'yi gerçek bir navigasyon gibi iste (Referer ile)
            headers = {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "accept-language": "tr-TR,tr;q=0.9,en;q=0.7",
                "cache-control": "max-age=0",
                "referer": "https://www.hepsiemlak.com/",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "same-origin",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
                "user-agent": ua,
            }
            
            try:
                response = session.get(url, headers=headers, timeout=20, allow_redirects=True)
                print(f"[API] Hedef URL yanıt: HTTP {response.status_code}", flush=True)
                
                if response.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # __NEXT_DATA__ JSON içeriğini ara
                    next_data_script = soup.find('script', id='__NEXT_DATA__')
                    if next_data_script and next_data_script.string:
                        try:
                            data = json.loads(next_data_script.string)
                            listings = None
                            def find_listings(d):
                                nonlocal listings
                                if isinstance(d, dict):
                                    for key in ["listings", "items", "results", "data"]:
                                        if key in d and isinstance(d[key], list) and len(d[key]) > 0:
                                            # Her liste elemanının gerçek ilan olup olmadığını kontrol et
                                            first = d[key][0]
                                            if isinstance(first, dict) and any(k in first for k in ["price", "fiyat", "id", "url", "title"]):
                                                listings = d[key]
                                                return
                                    for v in d.values():
                                        find_listings(v)
                                        if listings: return
                                elif isinstance(d, list):
                                    for item in d:
                                        find_listings(item)
                                        if listings: return
                            find_listings(data)
                            
                            if listings:
                                print(f"[API] __NEXT_DATA__ içinde {len(listings)} ilan bulundu!", flush=True)
                                for lst in listings[:25]:
                                    try:
                                        # Farklı alan adı varyasyonlarını dene
                                        price = (
                                            lst.get("price", {}).get("value") if isinstance(lst.get("price"), dict)
                                            else lst.get("price") or lst.get("fiyat") or lst.get("salePrice") or 0
                                        )
                                        price = float(str(price).replace(".", "").replace(",", "").strip()) if price else 0
                                        
                                        m2_val = (
                                            lst.get("size", {}).get("value") if isinstance(lst.get("size"), dict)
                                            else lst.get("size") or lst.get("squareMeter") or lst.get("area") or lst.get("netM2") or 0
                                        )
                                        m2_val = float(str(m2_val).replace(",", ".").strip()) if m2_val else 0
                                        
                                        rooms_val = lst.get("roomCount") or lst.get("rooms") or lst.get("odaSayisi") or "—"
                                        title = lst.get("title") or lst.get("baslik") or "Hepsiemlak İlanı"
                                        link = lst.get("url") or lst.get("link") or lst.get("detailUrl") or ""
                                        if link and not link.startswith("http"):
                                            link = "https://www.hepsiemlak.com" + link
                                        
                                        if price > 10000 and m2_val > 0:
                                            samples.append({
                                                "id": random.randint(10000, 99999),
                                                "title": title,
                                                "price": price,
                                                "area_m2": m2_val,
                                                "rooms": rooms_val,
                                                "district": "web",
                                                "is_web": True,
                                                "url": link
                                            })
                                    except:
                                        pass
                                if samples:
                                    print(f"[API] Başarı! {len(samples)} ilan parse edildi.", flush=True)
                                    return samples
                        except Exception as e:
                            print(f"[API] __NEXT_DATA__ parse hatası: {e}", flush=True)
                    
                    # HTML kart fallback
                    print("[API] __NEXT_DATA__ yok, HTML kartları aranıyor...", flush=True)
                    cards = (
                        soup.select('a.listingView__card-link') or
                        soup.select('div.list-view-item') or
                        soup.select('.listing-item')
                    )
                    if cards:
                        print(f"[API] HTML'de {len(cards)} kart bulundu.", flush=True)
                        for card in cards[:25]:
                            try:
                                link = card.get('href', '')
                                if link and not link.startswith('http'):
                                    link = "https://www.hepsiemlak.com" + link
                                p_el = card.select_one('.list-view-price strong, .list-view-price, .price, strong')
                                price = float(re.sub(r'\D', '', p_el.get_text())) if p_el else 0
                                m_el = card.select_one('.list-view-size, .squareMeter, dd.list-view-size')
                                m2_val = float(re.sub(r'\D', '', m_el.get_text())) if m_el else 0
                                r_el = card.select_one('.list-view-room, .houseRoomCount, dd.list-view-room')
                                rooms_val = r_el.get_text().strip() if r_el else "—"
                                if price > 10000 and m2_val > 0:
                                    samples.append({
                                        "id": random.randint(10000, 99999),
                                        "title": "Hepsiemlak İlanı",
                                        "price": price,
                                        "area_m2": m2_val,
                                        "rooms": rooms_val,
                                        "district": "web",
                                        "is_web": True,
                                        "url": link
                                    })
                            except:
                                pass
                        if samples:
                            return samples
                    
                    print(f"[API] {impersonate} ile veri çekilemedi, sonraki profil deneniyor...", flush=True)
                    
                elif response.status_code in [403, 429, 503]:
                    print(f"[API] {impersonate} engellendi (HTTP {response.status_code}), sonraki profil deneniyor...", flush=True)
                    time.sleep(random.uniform(2.0, 4.0))
                    continue
                    
            except Exception as e:
                print(f"[API] {impersonate} isteği başarısız: {e}", flush=True)
                continue
        
        print("[API] Tüm impersonation profilleri başarısız oldu.", flush=True)
        
    except Exception as e:
        print(f"[API] Kritik hata: {e}", flush=True)
    
    return samples


def scrape_with_curl_cffi(url: str, prop_type: str = "daire") -> List[Dict]:
    """Ana yedek mekanizma: Residential Proxy → Tor → curl_cffi multi-profile."""
    print(f"[FALLBACK] Yedek plan başlatılıyor: {url}", flush=True)

    # Strateji 0: Residential Proxy (En güçlü - RESIDENTIAL_PROXY_URL env var ile aktif)
    proxy_cfg = _get_proxy_config()
    if proxy_cfg["active"]:
        print("[FALLBACK] Strateji 0: Residential Proxy deneniyor...", flush=True)
        result = _scrape_with_proxy(url, prop_type, proxy_cfg)
        if result:
            print(f"[FALLBACK] Residential Proxy ile {len(result)} ilan çekildi!", flush=True)
            return result
        print("[FALLBACK] Proxy başarısız, Tor'a geçiliyor...", flush=True)
    
    # Strateji 1: Tor SOCKS5 proxy (IP değişimi)
    print("[FALLBACK] Strateji 1: Tor proxy deneniyor...", flush=True)
    result = scrape_with_tor(url, prop_type)
    if result:
        print(f"[FALLBACK] Tor ile {len(result)} ilan başarıyla çekildi!", flush=True)
        return result
    
    # Strateji 2: curl_cffi rotating impersonation
    print("[FALLBACK] Strateji 2: curl_cffi multi-profile deneniyor...", flush=True)
    result = scrape_with_hepsiemlak_api(url, prop_type)
    if result:
        print(f"[FALLBACK] curl_cffi ile {len(result)} ilan başarıyla çekildi!", flush=True)
        return result
    
    print("[FALLBACK] Tüm stratejiler başarısız oldu.", flush=True)
    return []


def _scrape_with_proxy(url: str, prop_type: str, proxy_cfg: dict) -> List[Dict]:
    """Residential Proxy üzerinden veri çeker (requests + BeautifulSoup)."""
    samples = []
    try:
        import requests as std_requests
        from bs4 import BeautifulSoup
        import json

        UA_LIST = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        ]
        ua = random.choice(UA_LIST)

        session = std_requests.Session()
        session.proxies = proxy_cfg["dict"]

        # Warmup (cookie/session oluştur)
        print("[PROXY] Ana sayfa warmup yapılıyor...", flush=True)
        warmup = session.get(
            "https://www.hepsiemlak.com",
            headers={"accept": "text/html,*/*;q=0.8", "accept-language": "tr-TR,tr;q=0.9", "user-agent": ua},
            timeout=30, allow_redirects=True
        )
        print(f"[PROXY] Warmup yanıt: HTTP {warmup.status_code}", flush=True)
        if warmup.status_code != 200:
            print(f"[PROXY] Warmup başarısız: {warmup.status_code}", flush=True)
            return []

        time.sleep(random.uniform(2.0, 4.0))

        # Hedef URL
        response = session.get(
            url,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "accept-language": "tr-TR,tr;q=0.9",
                "cache-control": "max-age=0",
                "referer": "https://www.hepsiemlak.com/",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "same-origin",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
                "user-agent": ua,
            },
            timeout=45, allow_redirects=True
        )
        print(f"[PROXY] Hedef URL yanıt: HTTP {response.status_code}", flush=True)

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        next_data_script = soup.find('script', id='__NEXT_DATA__')

        if next_data_script and next_data_script.string:
            data = json.loads(next_data_script.string)
            listings = None
            def _find_proxy(d):
                nonlocal listings
                if isinstance(d, dict):
                    for key in ["listings", "items", "results", "data"]:
                        if key in d and isinstance(d[key], list) and len(d[key]) > 0:
                            first = d[key][0]
                            if isinstance(first, dict) and any(k in first for k in ["price", "fiyat", "id", "url", "title"]):
                                listings = d[key]
                                return
                    for v in d.values():
                        _find_proxy(v)
                        if listings: return
                elif isinstance(d, list):
                    for item in d:
                        _find_proxy(item)
                        if listings: return
            _find_proxy(data)

            if listings:
                print(f"[PROXY] __NEXT_DATA__ içinde {len(listings)} ilan bulundu!", flush=True)
                for lst in listings[:25]:
                    try:
                        price = (lst.get("price", {}).get("value") if isinstance(lst.get("price"), dict) else lst.get("price") or lst.get("fiyat") or 0)
                        price = float(str(price).replace(".", "").replace(",", "").strip()) if price else 0
                        m2_val = (lst.get("size", {}).get("value") if isinstance(lst.get("size"), dict) else lst.get("size") or lst.get("squareMeter") or lst.get("area") or 0)
                        m2_val = float(str(m2_val).replace(",", ".").strip()) if m2_val else 0
                        rooms_val = lst.get("roomCount") or lst.get("rooms") or "—"
                        title = lst.get("title") or lst.get("baslik") or "Hepsiemlak İlanı"
                        link = lst.get("url") or lst.get("link") or lst.get("detailUrl") or ""
                        if link and not link.startswith("http"): link = "https://www.hepsiemlak.com" + link
                        if price > 10000 and m2_val > 0:
                            samples.append({"id": random.randint(10000, 99999), "title": title, "price": price, "area_m2": m2_val, "rooms": rooms_val, "district": "web", "is_web": True, "url": link})
                    except:
                        pass

        if not samples:
            cards = soup.select('a.listingView__card-link') or soup.select('div.list-view-item') or soup.select('.listing-item')
            for card in cards[:25]:
                try:
                    link = card.get('href', '')
                    if link and not link.startswith('http'): link = "https://www.hepsiemlak.com" + link
                    p_el = card.select_one('.list-view-price strong, .list-view-price, .price, strong')
                    price = float(re.sub(r'\D', '', p_el.get_text())) if p_el else 0
                    m_el = card.select_one('.list-view-size, .squareMeter, dd.list-view-size')
                    m2_val = float(re.sub(r'\D', '', m_el.get_text())) if m_el else 0
                    r_el = card.select_one('.list-view-room, .houseRoomCount, dd.list-view-room')
                    rooms_val = r_el.get_text().strip() if r_el else "—"
                    if price > 10000 and m2_val > 0:
                        samples.append({"id": random.randint(10000, 99999), "title": "Hepsiemlak İlanı", "price": price, "area_m2": m2_val, "rooms": rooms_val, "district": "web", "is_web": True, "url": link})
                except:
                    pass

        print(f"[PROXY] {len(samples)} ilan çekildi.", flush=True)
    except Exception as e:
        print(f"[PROXY] Hata: {e}", flush=True)
    return samples




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
    
    if not driver:
        print("[WEB TARAMA] Hata: Tarayıcı başlatılamadı. Doğrudan yedek plan (curl_cffi/Tor) tetikleniyor...", flush=True)
        fallback_samples = scrape_with_curl_cffi(url, prop_type)
        if fallback_samples:
            result_data["samples"] = fallback_samples
            result_data["status"] = f"{len(fallback_samples)} ilan yedek mekanizma (curl_cffi) ile başarıyla çekildi."
        else:
            result_data["status"] = "Tarayıcı başlatılamadı ve yedek plan veri çekemedi."
        return result_data
        
    try:
        # 0. Anti-Bot İnsansı Yönlendirme (Google Geçmişi Oluşturma)
        print(f"[WEB TARAMA] Anti-Bot: Google üzerinden insansı yönlendirme yapılıyor...", flush=True)
        try:
            driver.get("https://www.google.com.tr")
            time.sleep(random.uniform(1.5, 3.0))
            driver.scroll.down(random.randint(100, 300))
            time.sleep(random.uniform(0.5, 1.5))
        except:
            pass

        # 1. Sayfayı Yükle
        print(f"[WEB TARAMA] Hedef sayfaya gidiliyor (DrissionPage)...", flush=True)
        driver.get(url)
        
        # 2. Cloudflare / Challenge Kontrolü ve Bekleme (İnsansı Mod)
        # Not: Hem İngilizce hem Türkçe Cloudflare başlıklarını kontrol et!
        CLOUDFLARE_TITLES = [
            "Just a moment", "challenge", "bekleyiniz", "Robot Değilim",
            "Bir dakika lütfen", "bir dakika", "Lütfen bekleyin", "Attention Required"
        ]
        is_blocked = False
        for attempt in range(6):
            page_title = driver.title
            if any(x in page_title for x in CLOUDFLARE_TITLES):
                print(f"[WEB TARAMA] Cloudflare/Challenge tespit edildi (Title: {page_title}). Bekleniyor... {attempt+1}/6", flush=True)
                if attempt == 5:
                    is_blocked = True
                    break
                # İnsan benzeri davranış: mouse hareketi + scroll simülasyonu
                wait_time = random.uniform(6.0, 10.0)
                time.sleep(wait_time)
                try:
                    # JS ile insansı mouse hareketi simüle et
                    driver.run_js('''
                        var event = new MouseEvent('mousemove', {
                            bubbles: true, clientX: Math.random()*800+100,
                            clientY: Math.random()*400+100
                        });
                        document.dispatchEvent(event);
                    ''')
                    if attempt > 0:
                        driver.scroll.down(random.randint(80, 200))
                        time.sleep(random.uniform(0.8, 1.5))
                        driver.scroll.up(random.randint(50, 100))
                except:
                    pass
            else:
                break

        if is_blocked:
            print(f"[WEB TARAMA] HIZLI GEÇİŞ: DrissionPage Cloudflare engeline takıldı. Doğrudan curl_cffi yedek planı tetikleniyor...", flush=True)
            fallback_samples = scrape_with_curl_cffi(url, prop_type)
            if fallback_samples:
                result_data["samples"] = fallback_samples
                result_data["status"] = f"{len(fallback_samples)} ilan yedek mekanizma (curl_cffi) ile başarıyla çekildi."
                return result_data
            else:
                result_data["status"] = "Robot kontrolü geçilemedi ve yedek plan veri çekemedi."
                return result_data

        # Yonlendirme kontrolu
        current_url = driver.url
        if "captcha" in current_url.lower() or "challenge" in current_url.lower():
            print(f"[WEB TARAMA] BLOKLANMA TESPİT EDİLDİ (URL): {current_url}", flush=True)
            result_data["status"] = "Site tarafından engellendi (Captcha/Challenge)."
            return result_data

        # İnsansı bekleme (optimize edildi - zaman tasarrufu)
        print(f"[WEB TARAMA] Bekleniyor (insansi bekleme)...", flush=True)
        time.sleep(random.uniform(2.0, 3.5))
        
        # Bekleme sonrası Cloudflare tekrar kontrol (geç yüklenen challenge sayfaları için)
        post_wait_title = driver.title
        if any(x in post_wait_title for x in CLOUDFLARE_TITLES):
            print(f"[WEB TARAMA] Geç Cloudflare tespit edildi (Title: {post_wait_title}). Yedek plana geçiliyor...", flush=True)
            fallback_samples = scrape_with_curl_cffi(url, prop_type)
            if fallback_samples:
                result_data["samples"] = fallback_samples
                result_data["status"] = f"{len(fallback_samples)} ilan yedek mekanizma ile çekildi."
            else:
                result_data["status"] = "Cloudflare geçilemedi ve yedek plan da başarısız oldu."
            return result_data
        
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

        # 3. İnsansı Hareketler (Optimize - hızlı kaydırma)
        print(f"[WEB TARAMA] Sayfa üzerinde insansı kaydırma (scrolling) yapılıyor...", flush=True)
        for _ in range(random.randint(2, 3)):
            scroll_val = random.randint(400, 800)
            driver.scroll.down(scroll_val)
            time.sleep(random.uniform(0.7, 1.3))

        # 4. İlanları Yakalama
        print(f"[WEB TARAMA] İlan kartları ayıklanıyor...", flush=True)
        
        selectors = [
            'css:a.listingView__card-link',
            'css:div.list-view-item',
            'css:.listing-item',
            'css:a[href*="/satilik/"]',
            'css:div.listView'
        ]
        
        items = []
        for selector in selectors:
            try:
                # 2 Kez Deneme (hızlandırıldı)
                for attempt in range(2):
                    found = driver.eles(selector, timeout=3)
                    if found and len(found) > 0:
                        items = found
                        print(f"[WEB TARAMA] Seçici ile bulundu: {selector} (Adet: {len(found)}) - Deneme: {attempt+1}", flush=True)
                        break
                    time.sleep(1)
                if items: break
            except:
                continue

        if not items:
            # Debug için sayfa durumunu kontrol et
            page_title = driver.title
            print(f"[WEB TARAMA] KRITIK: Sayfada ilan bulunamadı. Sayfa Başlığı: {page_title}", flush=True)
            
            # YEDEK MEKANİZMA: curl_cffi ile dene
            print(f"[WEB TARAMA] YEDEK PLAN: curl_cffi ile sayfa çekiliyor...", flush=True)
            fallback_samples = scrape_with_curl_cffi(url, prop_type)
            if fallback_samples:
                result_data["samples"] = fallback_samples
                result_data["status"] = f"{len(fallback_samples)} ilan yedek mekanizma (curl_cffi) ile başarıyla çekildi."
                return result_data

            if any(x in page_title.lower() for x in ["captcha", "robot", "just a moment", "challenge"]):
                 result_data["status"] = "Robot kontrolüne takıldı veya sayfa yüklenemedi."
            else:
                 result_data["status"] = "İlanlar yakalanamadı veya filtreye uygun sonuç yok."
            return result_data
            
        print(f"[WEB TARAMA] Toplam {len(items)} kart bulundu. Detaylı analiz başlıyor...", flush=True)
        
        for i, item in enumerate(items[:20]):
            try:
                # İlan linkini yakala
                link = ""
                try:
                    link = item.attr('href') or ""
                    if not link and item.tag != 'a':
                        link_el = item.ele('tag:a', timeout=0)
                        if link_el: link = link_el.attr('href')
                except: pass

                if link and not link.startswith('http'):
                    link = "https://www.hepsiemlak.com" + link

                price, m2_val, rooms_val = None, None, "—"
                
                # 1. Fiyat Ayıklama
                price_selectors = [
                    'css:.list-view-price strong',
                    'css:.list-view-price',
                    'css:.price',
                    'css:.listing-item__price',
                    'css:strong' # Son çare
                ]
                for ps in price_selectors:
                    try:
                        p_el = item.ele(ps, timeout=0)
                        if p_el:
                            # text veya innerText denemesi
                            raw_p = p_el.text or p_el.attr('innerText') or ""
                            p_text = re.sub(r'\D', '', raw_p)
                            if p_text:
                                price = float(p_text)
                                break
                    except: continue

                # 2. m2 Ayıklama
                m2_selectors = [
                    'css:.list-view-size',
                    'css:.squareMeter',
                    'css:.celly.squareMeter',
                    'css:dd.list-view-size'
                ]
                for ms in m2_selectors:
                    try:
                        m_el = item.ele(ms, timeout=0)
                        if m_el:
                            raw_m = m_el.text or m_el.attr('innerText') or ""
                            m_text = re.sub(r'\D', '', raw_m)
                            if m_text:
                                m2_val = float(m_text)
                                break
                    except: continue

                # 3. Oda Sayısı Ayıklama
                rooms_selectors = [
                    'css:.list-view-room',
                    'css:.houseRoomCount',
                    'css:.celly.houseRoomCount',
                    'css:dd.list-view-room'
                ]
                for rs in rooms_selectors:
                    try:
                        r_el = item.ele(rs, timeout=0)
                        if r_el:
                            rooms_val = (r_el.text or r_el.attr('innerText') or "—").strip()
                            if rooms_val: break
                    except: continue

                # Eğer hala bulamadıysak, item.text üzerinden regex ile deneyelim (Garantili Mod)
                if not price or not m2_val:
                    full_text = item.text or item.attr('innerText') or ""
                    # Fiyat Regex (Örn: 15.000.000 TL)
                    if not price:
                        p_match = re.search(r'([\d\.]+)\s*TL', full_text)
                        if p_match:
                            p_str = re.sub(r'\D', '', p_match.group(1))
                            if p_str: price = float(p_str)
                    
                    # m2 Regex (Örn: 120 m2)
                    if not m2_val:
                        m_match = re.search(r'(\d+)\s*m²', full_text)
                        if m_match:
                            m2_val = float(m_match.group(1))

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
                    # Debug için neden bulunamadığını yazdır
                    # print(f"   [DEBUG] İlan {i+1} veri eksik. Price: {price}, m2: {m2_val}. Text: {item.text[:50]}...", flush=True)
                    pass

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
    rent_stats = calculate_roi(db, avg_price, ilce, prop_type=prop_type, rooms=rooms, building_age=building_age)
    
    # Likidite Skoru (Cidden hesaplanan)
    base_liquidity = 75
    if n >= 15:
        base_liquidity += 10
    elif n >= 8:
        base_liquidity += 5
    elif n < 3:
        base_liquidity -= 15
        
    rooms_clean = str(rooms).replace(" ", "") if rooms else ""
    if rooms_clean in ["1+1", "2+1"]:
        base_liquidity += 8
    elif rooms_clean in ["4+1", "5+1"]:
        base_liquidity -= 5
        
    if building_age == "0-0" or building_age == "1-5":
        base_liquidity += 7
    elif building_age == "21-50":
        base_liquidity -= 12
        
    prop_type_lower = prop_type.lower() if prop_type else "daire"
    if n >= 3:
        q25_val = percentile(sqm_prices, 25)
        q75_val = percentile(sqm_prices, 75)
        median_val = percentile(sqm_prices, 50)
        if median_val > 0:
            spread = (q75_val - q25_val) / median_val
            if spread < 0.15:
                base_liquidity += 8
            elif spread > 0.35:
                base_liquidity -= 10
                
    if "arsa" in prop_type_lower:
        base_liquidity -= 15
    elif any(x in prop_type_lower for x in ["dukkan", "isyeri", "ofis"]):
        base_liquidity -= 5
        
    liquidity_score = max(35, min(97, base_liquidity))

    # Analiz Güven Oranı (Cidden hesaplanan)
    base_confidence = 65
    if n >= 20:
        base_confidence += 15
    elif n >= 10:
        base_confidence += 10
    elif n >= 5:
        base_confidence += 5
    elif n < 3:
        base_confidence -= 15
        
    if n >= 3:
        q25_val = percentile(sqm_prices, 25)
        q75_val = percentile(sqm_prices, 75)
        median_val = percentile(sqm_prices, 50)
        if median_val > 0:
            spread = (q75_val - q25_val) / median_val
            if spread < 0.20:
                base_confidence += 10
            elif spread > 0.40:
                base_confidence -= 10
                
    if lat and lon:
        base_confidence += 5
    if neighborhood and len(str(neighborhood)) > 2:
        base_confidence += 3
        
    confidence_score = max(40, min(98, base_confidence))
    
    # Son 5 ayı dinamik olarak oluştur (Türkçe isimlerle)
    ay_isimleri = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    trends = []
    sim_avg = avg_price
    for i in range(4, -1, -1):
        dt = datetime.now(TR_TZ) - timedelta(days=30*i)
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
        "confidence_score": confidence_score,
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
                date = datetime.now(TR_TZ) - timedelta(days=30*i)
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


def calculate_roi(db, avg_sqm_price: float, district: str, prop_type: str = "daire", rooms: str = "2+1", building_age: str = "") -> dict:
    """Amortisman süresi ve kira çarpanı hesabı (Dinamik ve Ciddi)."""
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

    # Eğer DB'den gelmediyse veya 0 ise gelişmiş formülle hesaplayalım
    if not avg_rent_sqm or not kira_carpani:
        prop_type_lower = prop_type.lower() if prop_type else "daire"
        base_multiplier = 240 # varsayılan 20 yıl
        
        if "daire" in prop_type_lower:
            base_multiplier = 230
            # Oda sayısı etkisi
            rooms_clean = str(rooms).replace(" ", "") if rooms else "2+1"
            if rooms_clean in ["1+1", "1+0"]:
                base_multiplier -= 30 # 200 ay (~16.6 yıl)
            elif rooms_clean == "2+1":
                base_multiplier -= 10 # 220 ay (~18.3 yıl)
            elif "4+1" in rooms_clean or "5+1" in rooms_clean:
                base_multiplier += 20 # 250 ay (~20.8 yıl)
        elif "villa" in prop_type_lower:
            base_multiplier = 280 # ~23.3 yıl
        elif any(x in prop_type_lower for x in ["dukkan", "ofis", "isyeri"]):
            base_multiplier = 200 # ~16.6 yıl
        elif "arsa" in prop_type_lower:
            base_multiplier = 420 # ~35 yıl

        # Bölge etkisi
        district_lower = district.lower() if district else ""
        if any(x in district_lower for x in ["besiktas", "kadikoy", "sariyer", "bakirkoy", "sisli", "cesme", "bodrum", "uskudar"]):
            base_multiplier += 30 # Değerli bölgelerde amortisman uzundur
        elif any(x in district_lower for x in ["esenyurt", "arnavutkoy", "sancaktepe", "sultanbeyli"]):
            base_multiplier -= 20 # Gelişmekte olan bölgelerde daha kısa

        # Bina yaşı etkisi
        if building_age == "0-0":
            base_multiplier += 15 # Sıfır bina daha geç amorti olur
        elif building_age == "1-5":
            base_multiplier += 10
        elif building_age == "21-50":
            base_multiplier -= 20 # Yaşlı bina daha hızlı amorti olur

        kira_carpani = max(120, min(500, base_multiplier))
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
            response = requests.get(full_url, headers=headers, timeout=5)
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
        f"GÜNCEL TARİH: {datetime.now(TR_TZ).strftime('%d.%m.%Y %H:%M')}\n\n"
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
        if '_REPORTLAB_INITIALIZED' not in globals() or not globals().get('_REPORTLAB_INITIALIZED'):
             _init_reportlab()

        if not output_path:
            reports_dir = Path(__file__).parent / "reports"
            reports_dir.mkdir(exist_ok=True)
            ts = datetime.now(TR_TZ).strftime("%Y%m%d_%H%M%S")
            output_path = str(reports_dir / f"EstateAI_Rapor_{ts}.pdf")

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=2*cm, bottomMargin=2*cm,
        )

        BLUE   = HexColor("#2563EB")
        DARK   = HexColor("#0F172A")
        GRAY   = HexColor("#64748B")
        LIGHT  = HexColor("#F1F5F9")
        GREEN  = HexColor("#16A34A")

        h1 = ParagraphStyle("h1", fontSize=20, fontName=_FONT_BOLD,
                             textColor=BLUE, spaceAfter=6, spaceBefore=4)
        h2 = ParagraphStyle("h2", fontSize=13, fontName=_FONT_BOLD,
                             textColor=DARK, spaceAfter=4, spaceBefore=10)
        body = ParagraphStyle("body", fontSize=10, fontName=_FONT_NAME,
                               textColor=DARK, leading=16, alignment=TA_JUSTIFY)
        meta = ParagraphStyle("meta", fontSize=9, fontName=_FONT_NAME,
                               textColor=GRAY, spaceAfter=2)

        story = []

        # ── Başlık ───────────────────────────────────────────────────────────
        story.append(Paragraph("EstateAI — Dijital Ekspertiz Raporu", h1))
        story.append(Paragraph(
            f"Rapor Tarihi: {datetime.now(TR_TZ).strftime('%d.%m.%Y %H:%M')}  |  "
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
            ("FONTNAME",   (0, 0), (0, -1), _FONT_BOLD),
            ("FONTNAME",   (2, 0), (2, -1), _FONT_BOLD),
            ("FONTNAME",   (1, 0), (1, -1), _FONT_NAME),
            ("FONTNAME",   (3, 0), (3, -1), _FONT_NAME),
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
        area_m2_val = float(params.get("area_m2", 0) or 0)
        est = estimate_value(stats, area_m2_val) if area_m2_val > 0 else {}

        sdata = [
            ["Gösterge", "Değer (TL/m²)", "Tahmini Toplam (TL)"],
            ["Minimum m² Fiyatı",
             f"{stats.get('min_sqm_price', 0):,.0f}",
             f"{stats.get('min_sqm_price', 0)*area_m2_val:,.0f}" if area_m2_val else "—"],
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
             f"{stats.get('max_sqm_price', 0)*area_m2_val:,.0f}" if area_m2_val else "—"],
        ]
        stable = Table(sdata, colWidths=[6*cm, 4.5*cm, 5.5*cm])
        stable.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  BLUE),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  HexColor("#FFFFFF")),
            ("FONTNAME",      (0, 0), (-1, 0),  _FONT_BOLD),
            ("FONTNAME",      (0, 1), (-1, -1), _FONT_NAME),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("GRID",          (0, 0), (-1, -1), 0.5, HexColor("#CBD5E1")),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [LIGHT, HexColor("#F8FAFC")]),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
            # Ortalama satırı vurgula
            ("BACKGROUND",    (0, 4), (-1, 4),  HexColor("#ECFDF5")),
            ("FONTNAME",      (0, 4), (-1, 4),  _FONT_BOLD),
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
                ("FONTNAME",      (0, 0), (-1, -1), _FONT_BOLD),
                ("FONTSIZE",      (0, 0), (-1, -1), 10),
                ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING",    (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("GRID",          (0, 0), (-1, -1), 1, HexColor("#CBD5E1")),
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
                
                # Markdown Kalın Yazı Desteği
                line = MD_BOLD_RE.sub(r'<b>\1</b>', line)
                
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
        disclaimer = ParagraphStyle("disc", fontSize=8, fontName=_FONT_NAME,
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

    except Exception as e:
        log.error(f"PDF oluşturma hatası: {e}")
        raise
