"""
fsbo_scraper.py — FSBO (Sahibinden Satılık) İlan Avcısı
========================================================
Strateji:
  1. Google arama: '"sahibinden satılık" {ilçe} {tip} site:hepsiemlak.com OR site:zingat.com'
  2. Sonuçları parse et → opportunities tablosuna kaydet
  3. Bot koruması nedeniyle sahibinden.com doğrudan değil, Google/Hepsiemlak üzerinden
  4. requests kütüphanesi eksikse veya rate-limit'e takılırsa demo mod devreye girer

QThread tabanlı çalışır — UI thread'i bloklamaz.
"""
import logging
import time
import re
from datetime import datetime

log = logging.getLogger(__name__)

PROPERTY_TYPES = ["daire", "villa", "arsa", "dükkan", "ofis", "müstakil ev"]
SOURCE_SITES = ["hepsiemlak.com", "zingat.com", "emlakjet.com"]


# ── Yardımcı: URL oluştur ────────────────────────────────────────────────────

def _build_google_url(district: str, prop_type: str, listing_type: str = "satılık") -> str:
    """Google arama URL'si üret."""
    query = f'sahibinden {listing_type} {prop_type} {district} site:hepsiemlak.com OR site:zingat.com'
    from urllib.parse import quote_plus
    return f"https://www.google.com/search?q={quote_plus(query)}&num=20&hl=tr"


def _build_hepsiemlak_url(district: str, prop_type: str) -> str:
    """Hepsiemlak için doğrudan URL (sahibinden ilanları için filtre)."""
    from smart_locator import SmartLocator
    locator = SmartLocator()
    location_data = locator.resolve_location(district)
    konum_yolu = location_data.get("url", district.lower().replace(" ", "-"))
    
    tip_map = {
        "daire": "daire",
        "villa": "villa",
        "arsa": "arsa",
        "dükkan": "isyeri",
        "ofis": "isyeri"
    }
    mulk_tipi_slug = tip_map.get(prop_type.lower(), "daire")
    
    if "satilik" in konum_yolu:
        if "daire" in konum_yolu and mulk_tipi_slug != "daire":
            konum_yolu = konum_yolu.replace("daire", mulk_tipi_slug)
        url = f"https://www.hepsiemlak.com/{konum_yolu}?kimden=sahibinden"
    else:
        url = f"https://www.hepsiemlak.com/{konum_yolu}-satilik-{mulk_tipi_slug}?kimden=sahibinden"
        
    return url.replace("https://www.hepsiemlak.com//", "https://www.hepsiemlak.com/")


# ── Ana Scraper Fonksiyonu ────────────────────────────────────────────────────

def scrape_fsbo(district: str, prop_type: str, listing_type: str = "satılık",
                max_results: int = 15) -> list:
    """
    FSBO ilanlarını tara ve liste döndür.
    Her eleman: {title, source_url, location, price_text, raw_snippet, source_site}
    """
    results = []

    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        from valuation_engine import get_headless_driver
        import time
        driver = get_headless_driver()
        
        try:
            # ── 1. Hepsiemlak Denemesi ─────────────────────────────────────────
            try:
                url = _build_hepsiemlak_url(district, prop_type)
                driver.get(url)
                time.sleep(3) # Dinamik içeriğin yüklenmesini bekle
                soup = BeautifulSoup(driver.page_source, "html.parser")

                cards = soup.select(".list-view-content, .card-ilan, [class*='listing']")
                for card in cards[:max_results]:
                    title_el = card.select_one("h3, h2, [class*='title']")
                    price_el  = card.select_one("[class*='price'], [class*='fiyat']")
                    loc_el    = card.select_one("[class*='location'], [class*='konum']")
                    link_el   = card.select_one("a[href]")

                    title = title_el.get_text(strip=True) if title_el else f"{district} {prop_type}"
                    price = price_el.get_text(strip=True) if price_el else "—"
                    loc   = loc_el.get_text(strip=True) if loc_el else district
                    href  = link_el["href"] if link_el else url
                    if href and not href.startswith("http"):
                        href = "https://www.hepsiemlak.com" + href

                    snippet = card.get_text(" ", strip=True)[:200]

                    if title and len(title) > 3:
                        results.append({
                            "title": title,
                            "source_url": href,
                            "location": loc or district,
                            "price_text": price,
                            "raw_snippet": snippet,
                            "source_site": "hepsiemlak",
                        })

                log.info(f"Hepsiemlak: {len(results)} ilan bulundu.")

            except Exception as e:
                log.warning(f"Hepsiemlak tarama hatası: {e}")

            time.sleep(1.5)  # Rate-limit önlemi

            # ── 2. Google Snippet Denemesi ─────────────────────────────────────
            if len(results) < 5:
                try:
                    gurl = _build_google_url(district, prop_type, listing_type)
                    driver.get(gurl)
                    time.sleep(2)
                    soup2 = BeautifulSoup(driver.page_source, "html.parser")

                    for g in soup2.select("div.g")[:max_results]:
                        a = g.select_one("a[href]")
                        h3 = g.select_one("h3")
                        desc = g.select_one(".VwiC3b, .s3v9rd, [class*='snippet']")

                        if not a or not h3:
                            continue
                        href = a.get("href", "")
                        if href.startswith("/url?q="):
                            href = href[7:].split("&")[0]
                        title = h3.get_text(strip=True)
                        snippet = desc.get_text(strip=True) if desc else ""

                        # Fiyat regex
                        price_match = re.search(r"[\d\.]+\s*(?:TL|₺|bin)", snippet, re.IGNORECASE)
                        price_text = price_match.group() if price_match else "—"

                        site = next((s for s in SOURCE_SITES if s in href), "google")

                        results.append({
                            "title": title,
                            "source_url": href,
                            "location": district,
                            "price_text": price_text,
                            "raw_snippet": snippet[:200],
                            "source_site": site,
                        })

                    log.info(f"Google: {len(results)} toplam ilan.")

                except Exception as e:
                    log.warning(f"Google tarama hatası: {e}")
        finally:
            driver.quit()

    except ImportError:
        log.warning("requests veya beautifulsoup4 kurulu değil — demo mod aktif.")

    # ── Demo/Fallback: Hiç sonuç bulunamazsa örnek veri ──────────────────────
    if not results:
        results = _demo_results(district, prop_type)

    return results[:max_results]


def _demo_results(district: str, prop_type: str) -> list:
    """
    Tarama başarısız olursa kullanıcıya nasıl göründüğünü gösteren
    örnek (demo) kayıtlar döndür.
    """
    base_prices = [1_850_000, 2_200_000, 975_000, 3_100_000, 1_450_000]
    sizes = ["2+1", "3+1", "4+1", "1+1", "3+1"]
    return [
        {
            "title": f"[DEMO] {district} {size} {prop_type} — Sahibinden",
            "source_url": "https://www.hepsiemlak.com",
            "location": district,
            "price_text": f"{price:,.0f} ₺".replace(",", "."),
            "raw_snippet": f"[Bu demo verisidir. Gerçek tarama için requests ve beautifulsoup4 kurulu olmalıdır.] {district} bölgesinde sahibinden {size} {prop_type}.",
            "source_site": "demo",
        }
        for price, size in zip(base_prices, sizes)
    ]



