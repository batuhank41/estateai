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
from datetime import datetime, timedelta, timezone
TR_TZ = timezone(timedelta(hours=3))
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions
from smart_locator import SmartLocator
import os as _os

def _get_proxy_config() -> dict:
    """Residential Proxy yapılandırmasını env var'dan okur.
    VDS'te: export RESIDENTIAL_PROXY_URL="http://user:pass@host:port"
    
    """
    proxy_url = _os.environ.get("RESIDENTIAL_PROXY_URL", "").strip()
    
    # Varsayılan şablon değerlerini (placeholder) kontrol et ve engelle
    is_placeholder = any(x in proxy_url.lower() for x in [
        "username", "password", "example.com", "your_",
        "kullanici", "kullanıcı", "sifre", "şifre", "saglayici", "sağlayıcı"
    ])
    
    if proxy_url and not is_placeholder:
        import re as _re
        import random as _random

        # Dinamik IP rotasyonu: "session-XXXX" veya Proxynet "_session-XXXX" formatını destekle
        session_pattern = r'(_?session-)[a-zA-Z0-9]+'
        if _re.search(session_pattern, proxy_url):
            prefix_match = _re.search(session_pattern, proxy_url)
            prefix = prefix_match.group(1)  # "session-" veya "_session-"
            random_session = f"{prefix}{_random.randint(100000, 99999999)}"
            proxy_url = _re.sub(session_pattern, random_session, proxy_url)
            safe_url = _re.sub(r':([^:@]+)@', ':****@', proxy_url)
            print(f"[PROXY] Fırsat Radarı: Dinamik IP Rotasyonu Aktif (Yeni Oturum: {random_session}). Proxy: {safe_url}", file=sys.stderr, flush=True)
        else:
            safe_url = _re.sub(r':([^:@]+)@', ':****@', proxy_url)
            print(f"[PROXY] Fırsat Radarı: Residential Proxy aktif. Proxy: {safe_url}", file=sys.stderr, flush=True)

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
                    print(f"[PROXY TEST] Fırsat Radarı: Proxy doğrulandı ({test_url} → HTTP {r.status_code}).", file=sys.stderr, flush=True)
                    return {"url": proxy_url, "dict": {"http": proxy_url, "https": proxy_url}, "active": True}
            except Exception:
                continue

        # Tüm test URL'leri başarısız oldu
        safe_err_url = _re.sub(r':([^:@]+)@', ':****@', proxy_url)
        print(f"[PROXY TEST] Fırsat Radarı UYARI: Tüm proxy doğrulama testleri başarısız ({safe_err_url}). "
              f"Proxy yine de kullanılacak (bağlantı belki sadece tarayıcı tünelinden çalışıyor).", file=sys.stderr, flush=True)
        # Proxynet gibi browser-only proxy'ler requests testini geçemez ama DrissionPage'de çalışır.
        # Bu yüzden testi geçemese bile active=True dönüyoruz.
        return {"url": proxy_url, "dict": {"http": proxy_url, "https": proxy_url}, "active": True}

    return {"url": "", "dict": {}, "active": False}


def _get_drission_page(user_data_dir: str = None, browser_port: int = None):
    """DrissionPage için bot korumalı arka plan tarayıcısı ayarlar."""
    co = ChromiumOptions()
    if user_data_dir:
        co.set_user_data_path(user_data_dir)
    
    if browser_port:
        co.set_local_port(browser_port)

    # Xvfb bağımsızlığı için Headless modu aktifleştirildi
    co.set_argument('--headless=new')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--disable-infobars')
    co.set_argument('--exclude-switches=enable-automation')
    co.set_argument('--disable-extensions')
    co.set_argument('--ignore-certificate-errors')
    co.set_argument('--lang=tr-TR')
    co.set_argument('--accept-lang=tr-TR,tr;q=0.9,en-US;q=0.8')

    # 3. Bant genişliği tasarrufu - Görselleri kapat (Proxy dostu)
    co.set_pref('profile.managed_default_content_settings.images', 2)
    co.set_argument('--blink-settings=imagesEnabled=false')
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')

    # Residential Proxy (RESIDENTIAL_PROXY_URL ile aktif)
    # Proxynet gibi HTTP proxy'ler için hem set_proxy() hem --proxy-server kullanıyoruz.
    # set_proxy() DrissionPage'in native metodudur ve kimlik doğrulamayı (user:pass) destekler.
    # --proxy-server Chromium'a doğrudan geçilir (çift güvence).
    proxy_cfg = _get_proxy_config()
    if proxy_cfg["active"]:
        print(f"[PROXY] Fırsat Radarı: Residential Proxy DrissionPage'e bağlanıyor...", file=sys.stderr, flush=True)
        try:
            co.set_proxy(proxy_cfg["url"])  # DrissionPage native proxy (user:pass@host:port destekler)
        except Exception:
            pass
        co.set_argument(f'--proxy-server={proxy_cfg["url"]}')  # Chromium argümanı (yedek güvence)
    
    for chrome_path in ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/google-chrome"]:
        if _os.path.exists(chrome_path):
            co.set_browser_path(chrome_path)
            break

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
        print(f"[FSBO SCRAPER] DrissionPage başlatılamadı: {e}. Undetected-Chromedriver yedek planı deneniyor...", file=sys.stderr, flush=True)
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
            if user_data_dir:
                options.add_argument(f'--user-data-dir={user_data_dir}')
            
            driver = uc.Chrome(options=options, version_main=132)
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
            print(f"[FSBO SCRAPER] Undetected-Chromedriver başlatılamadı: {uc_err}", file=sys.stderr, flush=True)
            return None



def _is_today(date_text: str) -> bool:
    """İlan tarihinin bugün olup olmadığını radar.txt kriterlerine göre kontrol et."""
    if not date_text:
        return False
    
    date_text = date_text.lower().replace('İ', 'i').replace('I', 'ı').strip()
    
    tr_aylar = {
        1: "ocak", 2: "şubat", 3: "mart", 4: "nisan", 5: "mayıs", 6: "haziran",
        7: "temmuz", 8: "ağustos", 9: "eylül", 10: "ekim", 11: "kasım", 12: "aralık"
    }
    now = datetime.now(TR_TZ)
    
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


def scrape_with_tor_fsbo(url: str, district: str) -> list:
    """Tor SOCKS5 proxy üzerinden Fırsat Radarı ilanlarını çeker (IP değişimi)."""
    results = []
    try:
        import requests as std_requests
        from bs4 import BeautifulSoup
        import json
        import re as re_mod

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
            print(f"[TOR] Fırsat Radarı Tor denemesi {attempt+1}/3...", file=sys.stderr, flush=True)
            try:
                session = std_requests.Session()
                session.proxies = TOR_PROXY

                warmup = session.get(
                    "https://www.hepsiemlak.com",
                    headers={"accept": "text/html,*/*;q=0.8", "accept-language": "tr-TR,tr;q=0.9", "user-agent": ua},
                    timeout=30, allow_redirects=True
                )
                print(f"[TOR] Warmup: HTTP {warmup.status_code}", file=sys.stderr, flush=True)

                if warmup.status_code != 200:
                    _renew_tor_circuit()
                    time.sleep(3)
                    continue

                time.sleep(random.uniform(2.0, 4.0))

                response = session.get(
                    url,
                    headers={
                        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "accept-language": "tr-TR,tr;q=0.9",
                        "cache-control": "max-age=0",
                        "referer": "https://www.hepsiemlak.com/",
                        "sec-fetch-dest": "document",
                        "sec-fetch-mode": "navigate",
                        "sec-fetch-site": "same-origin",
                        "upgrade-insecure-requests": "1",
                        "user-agent": ua,
                    },
                    timeout=45, allow_redirects=True
                )
                print(f"[TOR] Hedef yanıt: HTTP {response.status_code}", file=sys.stderr, flush=True)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    next_data_script = soup.find('script', id='__NEXT_DATA__')

                    if next_data_script and next_data_script.string:
                        try:
                            data = json.loads(next_data_script.string)
                            listings = None
                            def _find_tor(d):
                                nonlocal listings
                                if isinstance(d, dict):
                                    for key in ["listings", "items", "results", "data"]:
                                        if key in d and isinstance(d[key], list) and len(d[key]) > 0:
                                            first = d[key][0]
                                            if isinstance(first, dict) and any(k in first for k in ["price", "fiyat", "id", "url", "title"]):
                                                listings = d[key]
                                                return
                                    for v in d.values():
                                        _find_tor(v)
                                        if listings: return
                                elif isinstance(d, list):
                                    for item in d:
                                        _find_tor(item)
                                        if listings: return
                            _find_tor(data)

                            if listings:
                                print(f"[TOR] Başarı! {len(listings)} ilan bulundu.", file=sys.stderr, flush=True)
                                for lst in listings[:20]:
                                    try:
                                        href = lst.get("url") or lst.get("link") or lst.get("detailUrl") or ""
                                        if href and not href.startswith("http"):
                                            href = "https://www.hepsiemlak.com" + href
                                        price_raw = (
                                            lst.get("price", {}).get("value") if isinstance(lst.get("price"), dict)
                                            else lst.get("price") or lst.get("fiyat") or 0
                                        )
                                        price = float(str(price_raw).replace(".", "").replace(",", "").strip()) if price_raw else 0
                                        title = lst.get("title") or lst.get("baslik") or "Sahibinden İlan"
                                        date_text = lst.get("date") or lst.get("createdDate") or "Bugün"
                                        results.append({
                                            "title": title,
                                            "source_url": href,
                                            "location": district,
                                            "price_text": f"{int(price)} TL" if price > 0 else "—",
                                            "raw_snippet": f"{title} · {district} · {date_text}".strip(" ·"),
                                            "source_site": "hepsiemlak",
                                            "date": date_text,
                                        })
                                    except:
                                        pass
                                if results:
                                    return results
                        except Exception as e:
                            print(f"[TOR] __NEXT_DATA__ parse hatası: {e}", file=sys.stderr, flush=True)

                    # HTML fallback
                    cards = (soup.select('a.listingView__card-link') or soup.select('div.list-view-item') or soup.select('.listing-item'))
                    if cards:
                        for card in cards[:20]:
                            try:
                                href = card.get('href', '')
                                if href and not href.startswith('http'):
                                    href = "https://www.hepsiemlak.com" + href
                                price_el = card.select_one('.list-view-price strong, .list-view-price, .price')
                                price_raw = re_mod.sub(r'[^0-9]', '', price_el.get_text()) if price_el else ""
                                title_el = card.select_one('h3, .list-view-header')
                                title = title_el.get_text().strip() if title_el else "Sahibinden İlan"
                                date_el = card.select_one('time.list-view-date, .list-view-date, time')
                                date_text = date_el.get_text().strip() if date_el else "Bugün"
                                results.append({
                                    "title": title, "source_url": href, "location": district,
                                    "price_text": f"{price_raw} TL" if price_raw else "—",
                                    "raw_snippet": f"{title} · {district} · {date_text}".strip(" ·"),
                                    "source_site": "hepsiemlak", "date": date_text,
                                })
                            except:
                                continue
                        if results:
                            return results

                elif response.status_code in [403, 429]:
                    _renew_tor_circuit()
                    time.sleep(4)

            except Exception as e:
                print(f"[TOR] Hata: {e}", file=sys.stderr, flush=True)
                _renew_tor_circuit()
                time.sleep(3)

        print("[TOR] Tor ile veri çekilemedi.", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[TOR] Kritik hata: {e}", file=sys.stderr, flush=True)
    return results


def scrape_with_curl_cffi(url: str, district: str) -> list:
    """Ana yedek: Residential Proxy → Tor → curl_cffi multi-profile."""
    print(f"[FALLBACK] Fırsat Radarı yedek plan başlatılıyor...", file=sys.stderr, flush=True)

    # Strateji 0: Residential Proxy (RESIDENTIAL_PROXY_URL env var ile aktif)
    proxy_cfg = _get_proxy_config()
    if proxy_cfg["active"]:
        print("[FALLBACK] Strateji 0: Residential Proxy deneniyor...", file=sys.stderr, flush=True)
        result = _scrape_proxy_fsbo(url, district, proxy_cfg)
        if result:
            print(f"[FALLBACK] Proxy ile {len(result)} ilan çekildi!", file=sys.stderr, flush=True)
            return result
        print("[FALLBACK] Proxy başarısız, Tor'a geçiliyor...", file=sys.stderr, flush=True)

    # Strateji 1: Tor
    print("[FALLBACK] Strateji 1: Tor proxy deneniyor...", file=sys.stderr, flush=True)
    result = scrape_with_tor_fsbo(url, district)
    if result:
        print(f"[FALLBACK] Tor ile {len(result)} ilan çekildi!", file=sys.stderr, flush=True)
        return result

    # Strateji 2: curl_cffi multi-profile
    print("[FALLBACK] Strateji 2: curl_cffi multi-profile deneniyor...", file=sys.stderr, flush=True)
    return _scrape_curl_cffi_multiprofile(url, district)


def _scrape_proxy_fsbo(url: str, district: str, proxy_cfg: dict) -> list:
    """Residential Proxy üzerinden Fırsat Radarı ilanlarını çeker."""
    results = []
    try:
        import requests as std_requests
        from bs4 import BeautifulSoup
        import json, re as re_mod

        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        session = std_requests.Session()
        session.proxies = proxy_cfg["dict"]

        warmup = session.get("https://www.hepsiemlak.com",
            headers={"accept": "text/html,*/*", "accept-language": "tr-TR,tr;q=0.9", "user-agent": ua},
            timeout=30, allow_redirects=True)
        print(f"[PROXY-FSBO] Warmup: HTTP {warmup.status_code}", file=sys.stderr, flush=True)
        if warmup.status_code != 200:
            return []

        time.sleep(random.uniform(2.0, 4.0))

        response = session.get(url, headers={
            "accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "accept-language": "tr-TR,tr;q=0.9",
            "cache-control": "max-age=0",
            "referer": "https://www.hepsiemlak.com/",
            "sec-fetch-dest": "document", "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin", "upgrade-insecure-requests": "1",
            "user-agent": ua,
        }, timeout=45, allow_redirects=True)
        print(f"[PROXY-FSBO] Hedef: HTTP {response.status_code}", file=sys.stderr, flush=True)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            nd = soup.find('script', id='__NEXT_DATA__')
            if nd and nd.string:
                data = json.loads(nd.string)
                listings = None
                def _fp(d):
                    nonlocal listings
                    if isinstance(d, dict):
                        for key in ["listings", "items", "results", "data"]:
                            if key in d and isinstance(d[key], list) and len(d[key]) > 0:
                                first = d[key][0]
                                if isinstance(first, dict) and any(k in first for k in ["price", "url", "title"]):
                                    listings = d[key]; return
                        for v in d.values():
                            _fp(v)
                            if listings: return
                    elif isinstance(d, list):
                        for item in d: _fp(item)
                        if listings: return
                _fp(data)
                if listings:
                    for lst in listings[:20]:
                        try:
                            href = lst.get("url") or lst.get("link") or ""
                            if href and not href.startswith("http"): href = "https://www.hepsiemlak.com" + href
                            price_raw = (lst.get("price", {}).get("value") if isinstance(lst.get("price"), dict) else lst.get("price") or 0)
                            price = float(str(price_raw).replace(".", "").replace(",", "")) if price_raw else 0
                            title = lst.get("title") or "Sahibinden İlan"
                            date_text = lst.get("date") or "Bugün"
                            results.append({"title": title, "source_url": href, "location": district,
                                "price_text": f"{int(price)} TL" if price > 0 else "—",
                                "raw_snippet": f"{title} · {district} · {date_text}".strip(" ·"),
                                "source_site": "hepsiemlak", "date": date_text})
                        except: pass
    except Exception as e:
        print(f"[PROXY-FSBO] Hata: {e}", file=sys.stderr, flush=True)
    return results


def _scrape_curl_cffi_multiprofile(url: str, district: str) -> list:
    """Çoklu impersonation + session warm-up ile Cloudflare'i aşarak sahibinden ilanları çeker."""

    results = []
    try:
        from curl_cffi import requests as cf_requests
        from bs4 import BeautifulSoup
        import json
        import re as re_mod

        print(f"[CURL_CFFI] Fırsat Radarı çoklu strateji taraması başlatılıyor: {url}", file=sys.stderr, flush=True)

        IMPERSONATION_PROFILES = [
            ("chrome124", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            ("chrome120", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
            ("safari17_0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
            ("chrome116", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"),
        ]

        for impersonate, ua in IMPERSONATION_PROFILES:
            print(f"[CURL_CFFI] Deneniyor: impersonate={impersonate}...", file=sys.stderr, flush=True)
            
            session = cf_requests.Session(impersonate=impersonate)
            
            # 1. Ana sayfayı ziyaret et — Cloudflare cookie ve session oluştur
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
                    print(f"[CURL_CFFI] Warmup başarısız ({impersonate}): HTTP {warmup.status_code}", file=sys.stderr, flush=True)
                    continue
                time.sleep(random.uniform(1.5, 3.0))
            except Exception as e:
                print(f"[CURL_CFFI] Warmup hatası ({impersonate}): {e}", file=sys.stderr, flush=True)
                continue

            # 2. Hedef URL'yi same-origin navigasyon gibi çek
            try:
                response = session.get(
                    url,
                    headers={
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
                    },
                    timeout=20,
                    allow_redirects=True
                )
                print(f"[CURL_CFFI] Hedef URL yanıt ({impersonate}): HTTP {response.status_code}", file=sys.stderr, flush=True)

                if response.status_code == 200:
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
                                print(f"[CURL_CFFI] __NEXT_DATA__ içinde {len(listings)} ilan bulundu!", file=sys.stderr, flush=True)
                                for lst in listings[:20]:
                                    try:
                                        href = lst.get("url") or lst.get("link") or lst.get("detailUrl") or ""
                                        if href and not href.startswith("http"):
                                            href = "https://www.hepsiemlak.com" + href
                                        price_raw = (
                                            lst.get("price", {}).get("value") if isinstance(lst.get("price"), dict)
                                            else lst.get("price") or lst.get("fiyat") or lst.get("salePrice") or 0
                                        )
                                        price = float(str(price_raw).replace(".", "").replace(",", "").strip()) if price_raw else 0
                                        title = lst.get("title") or lst.get("baslik") or "Sahibinden İlan"
                                        date_text = lst.get("date") or lst.get("createdDate") or "Bugün"
                                        results.append({
                                            "title": title,
                                            "source_url": href,
                                            "location": district,
                                            "price_text": f"{int(price)} TL" if price > 0 else "—",
                                            "raw_snippet": f"{title} · {district} · {date_text}".strip(" ·"),
                                            "source_site": "hepsiemlak",
                                            "date": date_text,
                                        })
                                    except:
                                        pass
                                if results:
                                    print(f"[CURL_CFFI] Başarı! {len(results)} ilan çekildi.", file=sys.stderr, flush=True)
                                    return results
                        except Exception as e:
                            print(f"[CURL_CFFI] __NEXT_DATA__ parse hatası: {e}", file=sys.stderr, flush=True)

                    # HTML kart fallback
                    cards = (
                        soup.select('a.listingView__card-link') or
                        soup.select('div.list-view-item') or
                        soup.select('.listing-item')
                    )
                    if cards:
                        print(f"[CURL_CFFI] HTML'de {len(cards)} kart bulundu.", file=sys.stderr, flush=True)
                        for card in cards[:20]:
                            try:
                                href = card.get('href', '')
                                if href and not href.startswith('http'):
                                    href = "https://www.hepsiemlak.com" + href
                                price_el = card.select_one('.list-view-price strong, .list-view-price, .price')
                                price_raw = re_mod.sub(r'[^0-9]', '', price_el.get_text()) if price_el else ""
                                title_el = card.select_one('h3, .list-view-header')
                                title = title_el.get_text().strip() if title_el else "Sahibinden İlan"
                                date_el = card.select_one('time.list-view-date, .list-view-date, time')
                                date_text = date_el.get_text().strip() if date_el else "Bugün"
                                results.append({
                                    "title": title,
                                    "source_url": href,
                                    "location": district,
                                    "price_text": f"{price_raw} TL" if price_raw else "—",
                                    "raw_snippet": f"{title} · {district} · {date_text}".strip(" ·"),
                                    "source_site": "hepsiemlak",
                                    "date": date_text,
                                })
                            except:
                                continue
                        if results:
                            return results

                    print(f"[CURL_CFFI] {impersonate} ile veri çekilemedi, sonraki profil deneniyor...", file=sys.stderr, flush=True)

                elif response.status_code in [403, 429, 503]:
                    print(f"[CURL_CFFI] {impersonate} engellendi (HTTP {response.status_code}), sonraki profil deneniyor...", file=sys.stderr, flush=True)
                    time.sleep(random.uniform(2.0, 4.0))
                    continue

            except Exception as e:
                print(f"[CURL_CFFI] {impersonate} isteği başarısız: {e}", file=sys.stderr, flush=True)
                continue

        print("[CURL_CFFI] Tüm profiller başarısız oldu.", file=sys.stderr, flush=True)

    except Exception as e:
        print(f"[CURL_CFFI] Kritik Hata: {e}", file=sys.stderr, flush=True)
    return results



def scrape_fsbo(district: str, prop_type: str = "daire", listing_type: str = "satilik",
                max_results: int = 10, city: str = "", neighborhood: str = "",
                today_only: bool = True, company_id: str = "default",
                min_price: str = None, max_price: str = None, 
                room_count: str = None, building_age: str = None,
                floor_level: str = None, heating_type: str = None,
                site_status: str = None, furnished_status: str = None,
                user_data_dir: str = None,
                browser_port: int = None) -> list:
    """
    Hepsiemlak'tan DrissionPage kullanarak sahibinden ilanlarını çek (Fırsat Radarı).
    Daha insansı (human-like) davranışlar eklenmiştir.
    """
    results = []
    import shutil
    import tempfile
    
    # Her istek için izole bir profil dizini
    # Eğer API'den gelmediyse (manuel çalıştırma) geçici bir tane oluştur
    tmp_dir = user_data_dir or tempfile.mkdtemp(prefix="estateai_browser_")

    
    # 1. URL İnşası
    locator = SmartLocator()
    base_slug = locator.get_url(city, district, neighborhood)
    islem_tipi = "satilik" if "sat" in listing_type.lower() else "kiralik"
    
    # Prop type slug (e.g., "daire", "isyeri", "villa")
    # Hepsiemlak standart slug yapısına göre prop_type'ı ekleyelim
    prop_slug = prop_type.lower()
    if prop_slug == "konut": prop_slug = "" # Konut genel bir kategori

    # Slug temizleme ve inşası
    # Sözlükten gelen base_slug'daki -satilik/-kiralik kısmını atıp kendi yapımızı kuruyoruz
    clean_slug = base_slug.replace("-satilik", "").replace("-kiralik", "").replace("-sahibinden", "")
    
    # Hedef Slug: [location]-[islem]-sahibinden
    # Kullanıcı isteği üzerine kategori (daire vb.) URL'den kaldırıldı
    # Örn: uskudar-satilik-sahibinden
    slug_parts = [clean_slug, islem_tipi, "sahibinden"]
    target_slug = "-".join(slug_parts)
    
    from urllib.parse import urlencode
    query_params = {
        "sortField": "UPDATED_DATE",
        "sortDirection": "DESC",
        "p33": "1" # Sahibinden filtresi
    }
    
    if min_price: query_params["p31"] = re.sub(r'[^0-9]', '', str(min_price))
    if max_price: query_params["p32"] = re.sub(r'[^0-9]', '', str(max_price))
    
    # Oda sayısı (p63)
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
        page = _get_drission_page(user_data_dir=tmp_dir, browser_port=browser_port)
        
        if not page:
            print("[WEB TARAMA] Hata: Tarayıcı başlatılamadı. Doğrudan yedek plan (curl_cffi/Tor) tetikleniyor...", file=sys.stderr, flush=True)
            fallback_results = scrape_with_curl_cffi(url, district)
            return fallback_results if fallback_results else []
            
        # 0. Anti-Bot İnsansı Yönlendirme (Google Geçmişi Oluşturma)
        print(f"[WEB TARAMA] Anti-Bot: Google üzerinden insansı yönlendirme yapılıyor...", file=sys.stderr, flush=True)
        try:
            page.get("https://www.google.com.tr")
            time.sleep(random.uniform(1.5, 3.0))
            page.scroll.down(random.randint(100, 300))
            time.sleep(random.uniform(0.5, 1.5))
        except:
            pass

        # 1. Sayfaya Git
        print(f"[WEB TARAMA] Hedef sayfaya gidiliyor...", file=sys.stderr, flush=True)
        page.get(url)
        
        # 2. Cloudflare / Challenge Kontrolü ve Bekleme (İnsansı Mod)
        # Not: Hem İngilizce hem Türkçe Cloudflare başlıklarını kontrol et!
        CLOUDFLARE_TITLES = [
            "Just a moment", "challenge", "bekleyiniz", "Robot Değilim",
            "Bir dakika lütfen", "bir dakika", "Lütfen bekleyin", "Attention Required"
        ]
        is_blocked = False
        for attempt in range(6):
            page_title = page.title
            if any(x in page_title for x in CLOUDFLARE_TITLES):
                print(f"[WEB TARAMA] Cloudflare/Challenge tespit edildi (Title: {page_title}). Bekleniyor... {attempt+1}/6", file=sys.stderr, flush=True)
                if attempt == 5:
                    is_blocked = True
                    break
                wait_time = random.uniform(6.0, 10.0)
                time.sleep(wait_time)
                try:
                    page.run_js('''
                        var event = new MouseEvent('mousemove', {
                            bubbles: true, clientX: Math.random()*800+100,
                            clientY: Math.random()*400+100
                        });
                        document.dispatchEvent(event);
                    ''')
                    if attempt > 0:
                        page.scroll.down(random.randint(80, 200))
                        time.sleep(random.uniform(0.8, 1.5))
                        page.scroll.up(random.randint(50, 100))
                except:
                    pass
            else:
                break

        if is_blocked:
            print("[WEB TARAMA] HIZLI GEÇİŞ: DrissionPage Cloudflare engeline takıldı. Doğrudan curl_cffi yedek planı tetikleniyor...", file=sys.stderr, flush=True)
            fallback_results = scrape_with_curl_cffi(url, district)
            if fallback_results:
                return fallback_results
            else:
                return []

        # İnsansı bekleme ve yükleme (optimize edildi)
        print("[WEB TARAMA] Sayfa yukleniyor...", file=sys.stderr, flush=True)
        page.wait.load_start()
        time.sleep(random.uniform(2.0, 3.5))
        
        # 2. İnsansı Kaydırma (Optimize - hızlı)
        print("[WEB TARAMA] Insansi kaydirma (scrolling) yapiliyor...", file=sys.stderr, flush=True)
        for _ in range(random.randint(2, 3)):
            scroll_amount = random.randint(300, 600)
            page.scroll.down(scroll_amount)
            time.sleep(random.uniform(0.7, 1.3))
        
        # İlan kartlarını bul
        items = page.eles('css:a.listingView__card-link', timeout=12)

        if not items or len(items) == 0:
            print("[WEB TARAMA] YEDEK PLAN: curl_cffi ile Fırsat Radarı ilanları çekiliyor...", file=sys.stderr, flush=True)
            fallback_results = scrape_with_curl_cffi(url, district)
            if fallback_results:
                return fallback_results

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
                date_el = item.ele('css:time.list-view-date', timeout=0) or \
                          item.ele('css:.list-view-date', timeout=0) or \
                          item.ele('css:time', timeout=0)
                
                date_text = date_el.text if date_el else ""
                
                # Tarih filtresi
                if today_only and not _is_today(date_text):
                    # "bugün" kelimesini içermiyorsa ama "yeni" vb varsa kabul edebiliriz
                    if not any(x in item.text.lower() for x in ["bugün", "yeni", "dakika"]):
                        continue
                
                # 2. Fiyat
                price_el = item.ele('css:.list-view-price strong', timeout=0) or \
                           item.ele('css:.list-view-price', timeout=0) or \
                           item.ele('css:.price', timeout=0)
                
                price_raw = price_el.text if price_el else "—"
                price_clean = re.sub(r'[^0-9]', '', price_raw)
                
                # 3. Başlık
                title_el = item.ele('tag:h3', timeout=0) or \
                           item.ele('css:.list-view-header', timeout=0)
                title = title_el.text if title_el else "Sahibinden İlan"
 
                # 4. Lokasyon
                loc_el = item.ele('css:address.list-view-location', timeout=0) or \
                         item.ele('css:.list-view-location', timeout=0)
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
        # İzole profil dizinini temizle (Sadece içeride oluşturulduysa)
        if not user_data_dir:
            try: shutil.rmtree(tmp_dir)
            except: pass

    if not results:
        print(f"[WEB TARAMA] UYARI: Hiç ilan bulunamadı. Bloklanmış olabiliriz veya bugün yeni ilan yok.", file=sys.stderr, flush=True)
    else:
        print(f"[WEB TARAMA] BAŞARILI: {len(results)} yeni ilan bulundu.", file=sys.stderr, flush=True)
    
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    scrape_fsbo("Fatih", "daire")
