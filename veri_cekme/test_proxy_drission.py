"""
test_proxy_drission.py — Proxynet + DrissionPage Entegrasyon Testi
====================================================================
VDS'te şu şekilde çalıştırın:

  export RESIDENTIAL_PROXY_URL="http://proxynet-yf5tjb9b:786gLuDaF9ehz8@gate-res.proxynet.io:8000"
  python test_proxy_drission.py

Başarı kriteri:
  🎯 BAŞARILI! Duvar Aşıldı. Sayfa Başlığı: Hepsiemlak...
"""
import os
import sys
import re

print("=" * 60)
print("  EstateAI — Proxynet + DrissionPage Entegrasyon Testi")
print("=" * 60)

# ── 1. Env var kontrolü ──────────────────────────────────────
PROXY_URL = os.environ.get("RESIDENTIAL_PROXY_URL", "").strip()

if not PROXY_URL or "example.com" in PROXY_URL or "username" in PROXY_URL:
    print("\n❌ RESIDENTIAL_PROXY_URL ayarlanmamış veya placeholder değer içeriyor!")
    print("   Şu komutu çalıştırın:")
    print('   export RESIDENTIAL_PROXY_URL="http://proxynet-yf5tjb9b:786gLuDaF9ehz8@gate-res.proxynet.io:8000"')
    sys.exit(1)

safe_url = re.sub(r':([^:@]+)@', ':****@', PROXY_URL)
print(f"\n✅ Proxy URL bulundu: {safe_url}")

# ── 2. Basit requests testi (başarısız olabilir — bu normaldir) ──
print("\n[ADIM 1] requests() ile bağlantı testi (başarısız olması normaldir)...")
try:
    import requests
    import urllib3
    urllib3.disable_warnings()
    r = requests.get(
        "http://httpbin.org/ip",
        proxies={"http": PROXY_URL, "https": PROXY_URL},
        timeout=8.0,
        verify=False,
    )
    if r.status_code == 200:
        print(f"  ✅ requests() testi BAŞARILI. Çıkış IP: {r.json().get('origin', '?')}")
    else:
        print(f"  ⚠️  requests() yanıtı: HTTP {r.status_code} (tarayıcıda çalışmaya devam edecek)")
except Exception as e:
    print(f"  ⚠️  requests() testi başarısız: {e}")
    print("  ℹ️  Proxynet sadece tarayıcı tünelinden çalışıyor olabilir — bu normaldir.")

# ── 3. DrissionPage testi ────────────────────────────────────
print("\n[ADIM 2] DrissionPage + Proxynet ile Hepsiemlak'a bağlanılıyor...")
try:
    from DrissionPage import ChromiumPage, ChromiumOptions

    co = ChromiumOptions()
    co.set_argument('--headless=new')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--exclude-switches=enable-automation')
    co.set_argument('--disable-infobars')
    co.set_argument('--lang=tr-TR')
    co.set_argument('--ignore-certificate-errors')

    # Bant genişliği tasarrufu — görselleri kapat (1 GB kota için kritik!)
    co.set_pref('profile.managed_default_content_settings.images', 2)
    co.set_argument('--blink-settings=imagesEnabled=false')

    # ── Proxynet entegrasyonu ──
    try:
        co.set_proxy(PROXY_URL)   # DrissionPage native (kimlik doğrulama destekli)
        print(f"  ✅ set_proxy() çağrıldı: {safe_url}")
    except Exception as e:
        print(f"  ⚠️  set_proxy() desteklenmiyor: {e} — --proxy-server kullanılacak")
    co.set_argument(f'--proxy-server={PROXY_URL}')  # Chromium argümanı (çift güvence)

    # Linux Chrome yolları
    for chrome_path in ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/google-chrome"]:
        if os.path.exists(chrome_path):
            co.set_browser_path(chrome_path)
            print(f"  ✅ Chrome yolu: {chrome_path}")
            break

    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'
    co.set_user_agent(ua)
    co.set_argument('--window-size=1920,1080')

    print("\n  🚀 Tarayıcı başlatılıyor...")
    page = ChromiumPage(co)

    # Cloudflare fingerprint bypass
    page.run_js('''
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
        Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
        window.chrome = {runtime: {}};
    ''')

    print("  🌐 Hepsiemlak'a gidiliyor...")
    page.get("https://www.hepsiemlak.com")

    import time
    # Cloudflare bekleme döngüsü
    CLOUDFLARE_TITLES = ["Just a moment", "challenge", "bekleyiniz", "bir dakika", "Attention Required"]
    for attempt in range(6):
        title = page.title
        if any(x.lower() in title.lower() for x in CLOUDFLARE_TITLES):
            print(f"  ⏳ Cloudflare tespit edildi: '{title}' — Bekleniyor... ({attempt+1}/6)")
            time.sleep(8)
        else:
            break

    title = page.title
    current_url = page.url

    print(f"\n{'='*60}")
    if any(x.lower() in title.lower() for x in CLOUDFLARE_TITLES):
        print(f"❌ BAŞARISIZ — Cloudflare engeli aşılamadı.")
        print(f"   Sayfa Başlığı: {title}")
        print(f"   URL: {current_url}")
        print(f"\n   Olası nedenler:")
        print(f"   • Proxynet kotanız bitmiş olabilir (paneli kontrol edin)")
        print(f"   • Proxy URL'si yanlış girilmiş olabilir")
    else:
        print(f"🎯 BAŞARILI! Duvar Aşıldı.")
        print(f"   Sayfa Başlığı: {title}")
        print(f"   URL: {current_url}")
        print(f"\n   ✅ Sistem ticari düzeyde veri çekmeye hazır!")

    page.quit()

except ImportError:
    print("❌ DrissionPage kurulu değil!")
    print("   pip install DrissionPage")
except Exception as e:
    print(f"❌ DrissionPage hatası: {e}")
    import traceback
    traceback.print_exc()

print("=" * 60)
