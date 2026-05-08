from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sys
import os

# Allow importing local files
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from valuation_engine import scrape_web_comparables
from fsbo_scraper import scrape_fsbo

app = FastAPI(title="EstateAI Backend Engine", description="FastAPI service for scraping and valuation.")

class ValuationRequest(BaseModel):
    address_text: str
    rooms: str
    area_m2: float = None
    prop_type: str

class ScannerRequest(BaseModel):
    city: str
    district: str
    prop_type: str
    listing_type: str = "satılık"

@app.get("/")
def read_root():
    return {"status": "ok", "message": "EstateAI FastAPI Engine is running."}

@app.post("/valuation")
def run_valuation(req: ValuationRequest):
    try:
        # Calls existing valuation engine which works completely headless
        result = scrape_web_comparables(req.address_text, req.rooms, req.area_m2, req.prop_type)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scanner")
def run_scanner(req: ScannerRequest):
    try:
        # Calls existing cloudscraper engine which works completely headless
        results = scrape_fsbo(req.district, req.prop_type, req.listing_type)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # VDS ortamında sorunsuz çalışması için host "0.0.0.0"
    uvicorn.run(app, host="0.0.0.0", port=8000)
