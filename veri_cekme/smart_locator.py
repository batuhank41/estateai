import logging
import re
import json
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

class SmartLocator:
    """
    Akıllı Konum Çözücü:
    1. JSON (MASTER_SOZLUK) üzerinden O(1) arama yapar.
    2. JSON'da yoksa (ve driver varsa) Live API/Search üzerinden doğrular.
    3. En son katmanda hiyerarşik tahmin (V10) yürütür.
    """

    def __init__(self, driver=None):
        self.driver = driver
        self.sozluk = self._load_master_sozluk()
        import sys
        print("[INFO] SmartLocator V2.1 (Enhanced Priority) Initialized.", file=sys.stderr, flush=True)

    def _load_master_sozluk(self) -> dict:
        """Master JSON dosyasını hafızaya yükler."""
        path = Path(__file__).parent / "hepsiemlak_master_sozluk.json"
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                log.info(f"[OK] SmartLocator: {len(data)} lokasyon yüklendi.")
                return data
            else:
                log.warning(f"[WARN] SmartLocator: Sözlük bulunamadı: {path}")
        except Exception as e:
            log.error(f"[ERROR] SmartLocator Sözlük yükleme hatası: {e}")
        return {}

    def hepsiemlak_slugify(self, text: str) -> str:
        """Metni Hepsiemlak standartlarına (radar.txt Bölüm 3) çevirir."""
        if not text:
            return ""
        # Türkçeye özgü i-I dönüşümü
        text = text.replace('İ', 'i').replace('I', 'i').lower()
        # radar.txt: ç->c, ğ->g, ı->i, ş->s, ö->o, ü->u, space->-
        tr_map = str.maketrans("çğşıöü ", "cgsiou-")
        text = text.translate(tr_map)
        text = re.sub(r'[^a-z0-9-]', '', text)
        return re.sub(r'-+', '-', text).strip('-')

    def get_url(self, il: str, ilce: str, mahalle: str) -> str:
        """
        Giriş: il, ilce, mahalle
        Çıktı: Temizlenmiş hiyerarşik bölge slug'ı (radar.txt Bölüm 2 & 4'e uygun)
        """
        # 0. Slugify inputs first
        il_s = self.hepsiemlak_slugify(il)
        ilce_s = self.hepsiemlak_slugify(ilce)
        mah_clean = mahalle.replace("Mahallesi", "").replace("Mah.", "").strip()
        mah_s = self.hepsiemlak_slugify(mah_clean)

        # 1. Sözlükten kontrol et (Öncelikli)
        slug_from_json = self._get_from_json(il_s, ilce_s, mah_s)
        if slug_from_json:
            # Güvenlik Kontrolü: Mahalle istenmişse sonucun mahalleyi (tire-duyarsız) içerdiğinden emin ol
            # yaman-evler vs yamanevler eşleşmesini sağlamak için tireleri kaldırıp bakıyoruz
            clean_mah_s = mah_s.replace("-", "")
            clean_json_slug = slug_from_json.replace("-", "")
            
            if mah_s and clean_mah_s not in clean_json_slug:
                log.info(f"[INFO] SmartLocator: Sözlük eşleşmesi ({slug_from_json}) mahalleyi içermiyor, hiyerarşik veya API aramasına geçiliyor.")
                slug_from_json = None
            else:
                # Sözlükteki '-satilik' takısını temizle
                clean_slug = slug_from_json.replace("-satilik", "").replace("-kiralik", "")
                
                # Şehir adı temizleme (Opsiyonel: radar.txt Bölüm 3)
                if il_s and clean_slug.startswith(f"{il_s}-"):
                    clean_slug = clean_slug[len(il_s)+1:]

                log.info(f"[INFO] SmartLocator: Sözlükten eşleşme bulundu: {clean_slug}")
                return clean_slug

        # 1.5. Canlı API / Arama üzerinden kontrol et (Driver varsa)
        if self.driver:
            slug_from_api = self._get_from_api(il, ilce, mahalle)
            if slug_from_api:
                clean_slug = slug_from_api.replace("-satilik", "").replace("-kiralik", "")
                log.info(f"[INFO] SmartLocator: Canlı aramadan eşleşme bulundu: {clean_slug}")
                return clean_slug

        # 2. Sözlükte yoksa hiyerarşik yapı kur
        raw_parts = [p for p in [il_s, ilce_s, mah_s] if p]
        
        # Tekrar edenleri temizle (Örn: istanbul-istanbul -> istanbul)
        parts = []
        for p in raw_parts:
            if p not in parts:
                parts.append(p)
        
        if not parts:
            return "turkiye"
            
        final_slug = "-".join(parts)
        log.info(f"[INFO] SmartLocator: Sözlükte yok, tahmin üretildi: {final_slug}")
        return final_slug

    def _get_from_json(self, il: str, ilce: str, mahalle: str) -> Optional[str]:
        """JSON sözlük üzerinden hiyerarşik arama. Daha spesifik olandan genele gider."""
        res = None
        import sys
        print(f"[SEARCH] SmartLocator Search: il={il}, ilce={ilce}, mahalle={mahalle}", file=sys.stderr, flush=True)
        
        # Mahalle varyasyonları
        mah_variants = [mahalle]
        if mahalle:
            if not mahalle.endswith("mahallesi"):
                mah_variants.append(f"{mahalle}-mahallesi")
            if not mahalle.endswith("mah"):
                mah_variants.append(f"{mahalle}-mah")

        # 1. Tam Hiyerarşi (En Spesifik)
        if il and ilce and mahalle:
            for mv in mah_variants:
                full_path = f"{il}-{ilce}-{mv}"
                print(f"Checking key: {full_path}", file=sys.stderr, flush=True)
                if full_path in self.sozluk:
                    res = self.sozluk[full_path]
                    break

        # 2. İlçe-Mahalle
        if not res and ilce and mahalle:
            for mv in mah_variants:
                mid_path = f"{ilce}-{mv}"
                print(f"Checking key: {mid_path}", file=sys.stderr, flush=True)
                if mid_path in self.sozluk:
                    res = self.sozluk[mid_path]
                    break

        # 3. Sadece Mahalle (Agresif Varyasyon Denemesi)
        # Mahalle önceliği İlçe'den yüksektir çünkü daha spesifik bir arama istenmiştir.
        if not res and mahalle:
            # radar.txt: Bazı mahalleler ayrık yazılabiliyor (yamanevler -> yaman-evler)
            final_variants = set(mah_variants)
            if "-" not in mahalle:
                prefixes = ["yaman", "yeni", "eski", "orta", "yukari", "asagi", "buyuk", "kucuk", "karli"]
                for p in prefixes:
                    if mahalle.startswith(p) and len(mahalle) > len(p):
                        final_variants.add(f"{p}-{mahalle[len(p):]}")
            if "-" in mahalle:
                final_variants.add(mahalle.replace("-", ""))

            for mv in final_variants:
                print(f"Checking key: {mv}", file=sys.stderr, flush=True)
                if mv in self.sozluk:
                    res = self.sozluk[mv]
                    break

        # 4. İl-İlçe
        if not res and il and ilce:
            region_paths = [f"{il}-{ilce}", f"{il}-{ilce}-merkez", f"{il}-{ilce}-merkezi"]
            for rp in region_paths:
                print(f"Checking key: {rp}", file=sys.stderr, flush=True)
                if rp in self.sozluk:
                    res = self.sozluk[rp]
                    break

        # 5. Sadece İlçe
        if not res and ilce:
            ilce_variants = [ilce, f"{ilce}-merkez", f"{ilce}-merkezi"]
            for iv in ilce_variants:
                print(f"Checking key: {iv}", file=sys.stderr, flush=True)
                if iv in self.sozluk:
                    res = self.sozluk[iv]
                    break

        # 6. Sadece İl
        if not res and il:
            print(f"Checking key: {il}", file=sys.stderr, flush=True)
            if il in self.sozluk:
                res = self.sozluk[il]

        if not res:
            print(f"[ERROR] SmartLocator: Sözlükte hiçbir anahtar bulunamadı.", file=sys.stderr, flush=True)
            return None

        # GÜVENLİK KONTROLÜ - KALDIRILDI (Sözlüğe güveniliyor)
        return res.strip("/")

    def _get_from_api(self, il: str, ilce: str, mahalle: str) -> Optional[str]:
        """
        Hepsiemlak sitesi üzerinden canlı arama yaparak doğru slug'ı bulur.
        DrissionPage uyumlu Sniper V3 mantığı.
        """
        if not self.driver:
            return None

        try:
            import time
            import random

            search_query = f"{il} {ilce} {mahalle}".strip()
            log.info(f"[INFO] SmartLocator (DrissionPage): Canlı arama başlatılıyor: {search_query}")

            self.driver.get("https://www.hepsiemlak.com/")
            time.sleep(2)
            
            # 1. BOT / ENGEL KONTROLÜ
            title = self.driver.title.lower()
            if "blocked" in title or "cloudflare" in title or "robot" in title:
                log.warning(f"[WARN] SmartLocator: Hepsiemlak bot korumasına takıldı (Title: {title})")
                return None

            # 2. SAHA TEMİZLİĞİ - Pop-up ve overlay'leri sil (Daha agresif)
            self.driver.run_js("""
                var selectors = [
                    '._dn_onsite-overlay', '.dengage-popup', '[id*="onsite_popup"]', 
                    '.cookie-policy', '.banner-container', '.modal-backdrop',
                    '#dengage_push-accept-button', '#dengage_push-refuse-button'
                ];
                selectors.forEach(s => {
                    document.querySelectorAll(s).forEach(el => el.remove());
                });
                document.body.classList.remove('modal-open');
                document.body.style.overflow = 'auto';
            """)
            time.sleep(1)

            # 3. Arama kutusunu bul (Çoklu Selector Denemesi)
            search_input = None
            selectors_to_try = [
                '#search-input', 
                '.smart-search-input', 
                'input[placeholder*="Konum"]',
                'input[placeholder*="Ara"]'
            ]
            
            for selector in selectors_to_try:
                search_input = self.driver.ele(selector, timeout=5)
                if search_input:
                    log.info(f"[OK] SmartLocator: Arama kutusu bulundu ({selector})")
                    break
            
            if not search_input:
                log.warning("[WARN] SmartLocator: Arama kutusu bulunamadı. Sayfa içeriği bot koruması veya farklı bir layout olabilir.")
                # Hata anında sayfa başlığını ve bir kısmını loglayalım
                log.debug(f"[DEBUG] Page Title: {self.driver.title}")
                return None
                
            # Elementin görünür/tıklanabilir olduğundan emin ol
            try:
                search_input.scroll.to_see()
            except: pass
            
            search_input.click()
            time.sleep(0.5)

            # 4. Yazım ve Enter - İnsansı Taklit
            search_input.clear()
            for char in search_query:
                search_input.input(char)
                time.sleep(random.uniform(0.05, 0.15))

            time.sleep(1)
            search_input.input('\n') # Enter gönder

            # 5. YÖNLENDİRMEYİ BEKLE
            log.info(f"[WAIT] SmartLocator: Yönlendirme bekleniyor...")
            time.sleep(6) # Biraz daha uzun bekle

            current_url = self.driver.url

            if "hepsiemlak.com/" in current_url:
                # Slug'ı ayıkla
                slug = current_url.split("hepsiemlak.com/")[-1].split("?")[0].strip('/')
                # Eğer hala ana sayfadaysak veya geçersiz bir slug ise bekle
                if not slug or slug == "" or "turkiye" in slug:
                    time.sleep(3)
                    current_url = self.driver.url
                    slug = current_url.split("hepsiemlak.com/")[-1].split("?")[0].strip('/')

                if slug and len(slug) > 3:
                    # İl doğrulaması (Daha esnek: ilçe veya mahalle ile başlıyorsa da kabul et)
                    il_s = self.hepsiemlak_slugify(il)
                    ilce_s = self.hepsiemlak_slugify(ilce)
                    mah_s = self.hepsiemlak_slugify(mahalle)
                    
                    is_valid = False
                    if il_s and slug.startswith(il_s): is_valid = True
                    elif ilce_s and slug.startswith(ilce_s): is_valid = True
                    elif mah_s and slug.startswith(mah_s): is_valid = True
                    
                    if not is_valid:
                        log.warning(f"[WARN] SmartLocator API: Konum uyumsuzluğu! İstenen: {il_s}/{ilce_s}, URL: {slug}")
                        return None
                    log.info(f"[OK] SmartLocator: Canlı aramadan slug bulundu: {slug}")
                    return slug

        except Exception as e:
            log.warning(f"[WARN] SmartLocator: Canlı arama sırasında hata: {e}")

        return None

    def _fallback_predict(self, il: str, ilce: str, mahalle: str) -> str:
        """Fallback tahmin mekanizması (radar.txt Bölüm 4)."""
        parts = [p for p in [il, ilce, mahalle] if p]
        if parts:
            return "-".join(parts)
        return "turkiye"
