import sqlite3
import os

def init():
    db_path = "veri_cekme/emlak.db"
    print(f"Initializing {db_path}...")
    
    conn = sqlite3.connect(db_path)
    # Create tables
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rent_multipliers (
            district TEXT PRIMARY KEY,
            multiplier INTEGER,
            avg_rent_sqm FLOAT
        )
    """)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_management (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Add some seed data for testing
    conn.execute("INSERT OR IGNORE INTO rent_multipliers (district, multiplier, avg_rent_sqm) VALUES (?, ?, ?)", ("Kadikoy", 240, 500))
    conn.execute("INSERT OR IGNORE INTO system_management (key, value) VALUES (?, ?)", ("gemini_model", "gemini-1.5-flash"))
    
    conn.commit()
    conn.close()
    print("Database initialized successfully.")

if __name__ == "__main__":
    init()
