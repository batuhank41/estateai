# 🏙️ EstateAI - Intelligent Real Estate Valuation & Opportunity Radar

![EstateAI Banner](https://img.shields.io/badge/EstateAI-Advanced_Valuation_System-orange?style=for-the-badge&logo=homeassistant)
![React](https://img.shields.io/badge/React-Next.js-blue?style=flat-square&logo=react)
![Python](https://img.shields.io/badge/Python-Backend-green?style=flat-square&logo=python)
![Gemini AI](https://img.shields.io/badge/AI-Gemini_2.5_Flash-purple?style=flat-square&logo=google)

**EstateAI**, Türkiye gayrimenkul piyasasına (özellikle arsa, konut ve ticari mülklere) yönelik geliştirilmiş, yapay zeka destekli, kurumsal düzeyde bir gayrimenkul değerleme ve yatırım analizi platformudur.

Bu proje, bir Bilgisayar Mühendisi gözüyle karmaşık mimarileri, veri güvenliğini, fault-tolerant (hata tolere edebilir) sistem tasarımını ve LLM (Büyük Dil Modeli) entegrasyonlarını harmanlayan kapsamlı bir bitirme / vizyon projesidir.

---

## 🚀 Projenin Amacı ve Vizyonu
Gayrimenkul değerleme süreçleri geleneksel olarak yavaş, eksik verilere dayalı ve manuel süreçlerdir. **EstateAI**, resmi TKGM (Tapu Kadastro) verilerini, canlı piyasa emsallerini (Big Data) ve yapay zeka analizlerini saniyeler içinde birleştirerek kullanıcıya SPK lisanslı bir değerleme uzmanı kalitesinde, yatırım getirisi (ROI) ve amortisman hesaplamaları içeren detaylı raporlar sunar.

Ayrıca projede yer alan **Fırsat Radarı**, piyasaya yeni düşen veya fiyatı kırılan kelepir ilanları anında tespit etmek için özel algoritmalarla donatılmıştır.

---

## 🛠️ Temel Özellikler & Mühendislik Çözümleri

### 1. 🛡️ Çift Motorlu Anti-Bot Scraping Mimarisi (Dual-Engine Proxy)
Türkiye'nin büyük emlak portallarındaki agresif Cloudflare ve DataDome korumalarını aşmak için **ScrapingBee** ve **ZenRows** API'leri üzerine kurulu "Fallback" (yedekli) bir web tarama mimarisi geliştirdim.
- **Stealth Proxy & JS Rendering:** Hedef sunucuya tam bir tarayıcı (browser) parmak izi bırakılarak güvenlik duvarları (CAPTCHA vb.) %100 oranında atlatılır.
- Sistem Motor 1'de hata alırsa anında Motor 2'ye geçiş (failover) yapar.

### 2. 🗺️ PostGIS ve TKGM Entegrasyonu (Smart Locator)
- Kullanıcı haritada bir yere arama yaptığında veya tıkladığında, arka planda **Nominatim (OpenStreetMap)** ile Reverse Geocoding yapılır.
- Nokta atışı **TKGM WMS** servisi üzerinden Ada/Parsel ve Nitelik (Arsa, Tarla, Kargir Ev vb.) verileri çekilir.
- **Konum Şerefiyesi Algoritması:** Parselin denize, yola, hastaneye veya merkeze uzaklığı hesaplanarak değerleme çarpanları (Multiplier) dinamik olarak oluşturulur.

### 3. 🧠 LLM (Gemini 2.5 Flash) ile Akıllı Değerleme Modeli
- Klasik if-else tabanlı raporlama yerine, toplanan tüm ham veriler (Emsal KAKS, Parsel Alanı, Bölge Ortalama Fiyatı) özel bir Prompt Engineering mimarisiyle Google Gemini modeline beslenir.
- Yapay zeka bu verileri işleyerek; **Ham Toprak Payı**, **Kemik Değer**, **İmar Gücü Primi** ve **Likidite Skoru** gibi finansal metrikleri hesaplar ve Markdown formatında zengin bir rapor sunar.

### 4. 🌙 Gece Kuşu Algoritması (Midnight Shift Logic)
- **Fırsat Radarı**, "Sadece bugünün ilanlarını getir" mantığıyla çalışır. Ancak gece 00:00 ile 06:00 arasında çalıştırıldığında tarih değiştiği için ilanları kaçırma riskine (Edge Case) karşı özel bir `timedelta` (Gece Kuşu) mantığı geliştirilmiş ve zaman farkından doğan veri kaybı önlenmiştir.

### 5. 🔍 Robust String Matching (Kelime Sınırı Algoritmaları)
- Türkiye'deki mahalle isimlerinin ("Orhan" vs "Orhaniye", "Fatih" vs "Fatihi") birbiriyle karışmasını önlemek amacıyla, boşlukları silinen veriler üzerinde Regex ve index bazlı (Word Boundary) çok hassas bir eşleşme algoritması (False-Positive önleyici) yazdım.

---

## 💻 Kullanılan Teknolojiler (Tech Stack)

* **Frontend:** React, Next.js, TailwindCSS, TypeScript, Leaflet (Interactive Maps)
* **Backend (Data & Engine):** Python, BeautifulSoup4, Requests, Regex
* **AI & Machine Learning:** Google Generative AI (Gemini SDK)
* **Veri & GIS:** TKGM API, Nominatim, PostGIS Logic
* **Network / Proxy:** ZenRows, ScrapingBee (Stealth/Premium Proxies)

---
> **Not:** Bu depo (repository), güvenlik ve gizlilik politikaları (API Key, Özel Emsal Datası) gereği projenin sadece mimari vizyonunu ve seçili güvenli komponentlerini (Frontend Map UI vb.) barındırmaktadır. Arka plan servisleri ve veritabanı gizli tutulmuştur.
