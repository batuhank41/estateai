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

    def _load_master_sozluk(self) -> dict:
        """Master JSON dosyasını hafızaya yükler."""
        path = Path(__file__).parent / "hepsiemlak_master_sozluk.json"
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                log.info(f"✅ SmartLocator: {len(data)} lokasyon yüklendi.")
                return data
            else:
                log.warning(f"⚠️ SmartLocator: Sözlük bulunamadı: {path}")
        except Exception as e:
            log.error(f"❌ SmartLocator Sözlük yükleme hatası: {e}")
        return {}

    def hepsiemlak_slugify(self, text: str) -> str:
        """Metni Hepsiemlak standartlarına (radar.txt Bölüm 3) çevirir."""
        if not text:
            return ""
        text = str(text).lower()
        # radar.txt: ç->c, ğ->g, ı->i, ş->s, ö->o, ü->u, space->-
        tr_map = str.maketrans("çğışıöü ", "cgisiou-")
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
            # Sözlükteki '-satilik' takısını temizle çünkü engine kendisi ekleyecek
            clean_slug = slug_from_json.replace("-satilik", "").replace("-kiralik", "")
            log.info(f"📍 SmartLocator: Sözlükten eşleşme bulundu: {clean_slug}")
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
        log.info(f"📍 SmartLocator: Sözlükte yok, tahmin üretildi: {final_slug}")
        return final_slug

    def _get_from_json(self, il: str, ilce: str, mahalle: str) -> Optional[str]:
        """JSON sözlük üzerinden hiyerarşik arama."""
        # Tam Hiyerarşi
        if il and ilce and mahalle:
            full_path = f"{il}-{ilce}-{mahalle}"
            if full_path in self.sozluk:
                return self.sozluk[full_path].strip('/')

        # İlçe-Mahalle
        if ilce and mahalle:
            mid_path = f"{ilce}-{mahalle}"
            if mid_path in self.sozluk:
                return self.sozluk[mid_path].strip('/')

        # İl-İlçe (Mahalle bulunamadıysa bölge bazlı)
        if il and ilce:
            region_path = f"{il}-{ilce}"
            if region_path in self.sozluk:
                return self.sozluk[region_path].strip('/')

        # Sadece İlçe (İl-İlçe bulunamadıysa)
        if ilce and ilce in self.sozluk:
            return self.sozluk[ilce].strip('/')

        # Sadece Mahalle (Boş değilse kontrol et)
        if mahalle and mahalle in self.sozluk:
            resmi_slug = self.sozluk[mahalle]
            if not ilce or ilce in resmi_slug:
                return resmi_slug.strip('/')

        return None

    def _get_from_api(self, il: str, ilce: str, mahalle: str) -> Optional[str]:
        """
        Hepsiemlak sitesi üzerinden canlı arama yaparak doğru slug'ı bulur.
        Redirect Sniper V3 mantığı: Saha Temizliği + İnsansı Taklit + Yönlendirme Yakalama
        """
        if not self.driver:
            return None

        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            import time
            import random

            search_query = f"{il} {ilce} {mahalle}".strip()
            log.info(f"🔍 SmartLocator: Canlı API/Arama başlatılıyor: {search_query}")

            self.driver.get("https://www.hepsiemlak.com/")
            wait = WebDriverWait(self.driver, 20)

            # 1. RADİKAL SAHA TEMİZLİĞİ - Pop-up ve overlay'leri sil
            self.driver.execute_script("""
                var overlays = document.querySelectorAll('._dn_onsite-overlay, .dengage-popup, [id*="onsite_popup"], .cookie-policy, .banner-container');
                overlays.forEach(function(el) { el.remove(); });
                document.body.classList.remove('modal-open');
                document.body.style.overflow = 'auto';
            """)
            time.sleep(2)

            # 2. JAVASCRIPT İLE ZORBA TIKLAMA - Element Click Intercepted hatasını bypass et
            search_input = wait.until(EC.presence_of_element_located((By.ID, "search-input")))
            self.driver.execute_script("arguments[0].click();", search_input)

            # 3. YAZIM VE ENTER - Harf harf yazarak gerçek kullanıcı taklidi
            search_input.send_keys(Keys.CONTROL + "a")
            search_input.send_keys(Keys.DELETE)

            for char in search_query:
                search_input.send_keys(char)
                time.sleep(0.1)

            time.sleep(1)
            search_input.send_keys(Keys.ENTER)

            # 4. YÖNLENDİRMEYİ BEKLE
            log.info(f"⌛ SmartLocator: Yönlendirme bekleniyor...")
            time.sleep(6)

            current_url = self.driver.current_url

            if "hepsiemlak.com/" in current_url:
                slug = current_url.split("hepsiemlak.com/")[-1].split("?")[0].strip('/')
                if slug and slug != "turkiye-satilik" and len(slug) > 3:
                    log.info(f"✅ SmartLocator: Canlı aramadan slug bulundu: {slug}")
                    return slug

        except Exception as e:
            log.warning(f"⚠️ SmartLocator: Canlı arama sırasında hata: {e}")

        return None

    def _fallback_predict(self, il: str, ilce: str, mahalle: str) -> str:
        """Fallback tahmin mekanizması (radar.txt Bölüm 4)."""
        parts = [p for p in [il, ilce, mahalle] if p]
        if parts:
            return "-".join(parts)
        return "turkiye"
