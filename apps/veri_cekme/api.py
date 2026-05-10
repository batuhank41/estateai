from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Union
import sys
import os

# Allow importing local files
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from valuation_engine import run_valuation_hybrid
from fsbo_scraper import scrape_fsbo

app = FastAPI(title="EstateAI Backend Engine", description="FastAPI service for scraping and valuation.")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"Validation error: {exc.errors()}")
    print(f"Request body: {await request.body()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": str(await request.body())},
    )

class ValuationRequest(BaseModel):
    address_text: Optional[str] = "İstanbul"
    rooms: Optional[str] = "2+1"
    area_m2: Optional[float] = 100.0
    prop_type: Optional[str] = "daire"
    company_id: Optional[Union[str, int]] = "default"
    lat: Optional[float] = None
    lng: Optional[float] = None
    # Advanced filters
    building_age: Optional[str] = None
    floor_level: Optional[str] = None
    heating_type: Optional[str] = None
    listing_date: Optional[str] = None
    owner_type: Optional[str] = None
    min_price: Optional[str] = None
    max_price: Optional[str] = None
    site_status: Optional[str] = None
    furnished_status: Optional[str] = None
    loan_status: Optional[str] = None
    has_elevator: Optional[bool] = False
    has_parking: Optional[bool] = False
    has_garden: Optional[bool] = False
    has_pool: Optional[bool] = False
    has_security: Optional[bool] = False

class ScannerRequest(BaseModel):
    city: Optional[str] = "İstanbul"
    district: Optional[str] = "Kadıköy"
    prop_type: Optional[str] = "daire"
    listing_type: Optional[str] = "satılık"
    neighborhood: Optional[str] = ""
    company_id: Optional[Union[str, int]] = "default"
    # New filters for scanner
    min_price: Optional[str] = None
    max_price: Optional[str] = None
    room_count: Optional[str] = None
    building_age: Optional[str] = None
    floor_level: Optional[str] = None
    heating_type: Optional[str] = None
    site_status: Optional[str] = None
    furnished_status: Optional[str] = None

@app.get("/")
def read_root():
    return {"status": "ok", "message": "EstateAI FastAPI Engine is running."}

@app.post("/valuation")
async def run_valuation(req: ValuationRequest):
    try:
        # Calls the hybrid engine as per radar.txt
        print(f"Valuation request received: {req}", flush=True)
        result = run_valuation_hybrid(
            req.address_text, req.rooms, req.area_m2, req.prop_type, req.company_id, 
            lat=req.lat, lon=req.lng,
            building_age=req.building_age,
            floor_level=req.floor_level,
            heating_type=req.heating_type,
            listing_date=req.listing_date,
            owner_type=req.owner_type,
            min_price=req.min_price,
            max_price=req.max_price,
            site_status=req.site_status,
            furnished_status=req.furnished_status,
            loan_status=req.loan_status,
            has_elevator=req.has_elevator,
            has_parking=req.has_parking,
            has_garden=req.has_garden,
            has_pool=req.has_pool,
            has_security=req.has_security
        )
        return result
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})

@app.post("/scanner")
async def run_scanner(req: ScannerRequest):
    try:
        # Calls existing engine
        print(f"Scanner request received: {req}", flush=True)
        results = scrape_fsbo(
            req.district, req.prop_type, req.listing_type, 
            city=req.city, neighborhood=req.neighborhood, company_id=req.company_id,
            min_price=req.min_price, max_price=req.max_price,
            room_count=req.room_count, building_age=req.building_age,
            floor_level=req.floor_level, heating_type=req.heating_type,
            site_status=req.site_status, furnished_status=req.furnished_status
        )
        return results
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})

if __name__ == "__main__":
    # VDS ortamında sorunsuz çalışması için host "0.0.0.0"
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
