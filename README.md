🏙️ EstateAI - Intelligent Real Estate Valuation & Opportunity Radar
EstateAI is an AI-powered, enterprise-grade real estate valuation and investment analysis platform developed for the Turkish real estate market (specifically for land, residential, and commercial properties).

From a computer engineer's perspective, this project is a comprehensive graduation/vision project that blends complex architectures, data security, fault-tolerant system design, and LLM (Large Language Model) integrations.

🚀 Project Purpose and Vision
Real estate valuation processes are traditionally slow, reliant on incomplete data, and manual. EstateAI combines official TKGM (General Directorate of Land Registry and Cadastre) data, live market comparables (Big Data), and AI analyses within seconds to provide users with detailed reports containing return on investment (ROI) and depreciation calculations at the quality of an SPK-licensed valuation expert.

In addition, the Opportunity Radar featured in the project is equipped with special algorithms to instantly detect newly dropped or price-cut bargain listings.

🛠️ Core Features & Engineering Solutions
1. 🛡️ Dual-Engine Anti-Bot Scraping Architecture
To bypass aggressive Cloudflare and DataDome protections on major Turkish real estate portals, I developed a fallback web scraping architecture built on top of ScrapingBee and ZenRows APIs.

Stealth Proxy & JS Rendering: Security walls (such as CAPTCHAs) are 100% bypassed by leaving a full browser fingerprint on the target server.

The system instantly performs a failover to Engine 2 if it encounters an error in Engine 1.

2. 🗺️ PostGIS and TKGM Integration (Smart Locator)
When a user searches or clicks on a location on the map, Reverse Geocoding is performed in the background using Nominatim (OpenStreetMap).

Block/Parcel and Attribute (Land, Field, Masonry House, etc.) data are fetched point-and-click via the TKGM WMS service.

Location Premium Algorithm: Valuation multipliers are generated dynamically by calculating the parcel's distance to the sea, road, hospital, or city center.

3. 🧠 Smart Valuation Model with LLM (Gemini 2.5 Flash)
Instead of classic if-else based reporting, all collected raw data (Comparable FAR/KAKS, Parcel Area, Region Average Price) are fed into the Google Gemini model using a special Prompt Engineering architecture.

Processing this data, the artificial intelligence calculates financial metrics such as Raw Land Share, Bone Value, Zoning Power Premium, and Liquidity Score, and presents a rich report in Markdown format.

4. 🌙 Midnight Shift Logic
The Opportunity Radar works with the logic of "Fetch only today's listings." However, to prevent the risk of missing listings due to the date change when run between 00:00 and 06:00 (Edge Case), a special timedelta (Midnight Shift) logic was developed, preventing data loss caused by time differences.

5. 🔍 Robust String Matching (Word Boundary Algorithms)
To prevent neighborhood names in Turkey ("Orhan" vs "Orhaniye", "Fatih" vs "Fatihi") from mixing up with each other, I wrote a very precise matching algorithm (False-Positive preventer) using Regex and index-based (Word Boundary) on data with spaces removed.

💻 Tech Stack
Frontend: React, Next.js, TailwindCSS, TypeScript, Leaflet (Interactive Maps)

Backend (Data & Engine): Python, BeautifulSoup4, Requests, Regex

AI & Machine Learning: Google Generative AI (Gemini SDK)

Data & GIS: TKGM API, Nominatim, PostGIS Logic

Network / Proxy: ZenRows, ScrapingBee (Stealth/Premium Proxies)

Note: Due to security and privacy policies (API Key, Private Comparable Data), this repository contains only the architectural vision of the project and selected secure components (Frontend Map UI, etc.). Background services and the database are kept private.
