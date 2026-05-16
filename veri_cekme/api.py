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
import asyncio
import uuid
import shutil
import concurrent.futures

# Maksimum eşzamanlı tarayıcı (Worker) sınırı
MAX_CONCURRENT_WORKERS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_WORKERS)
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS)

async def run_in_thread(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(thread_pool, lambda: func(*args, **kwargs))


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
    city: Optional[str] = None
    district: Optional[str] = None
    neighborhood: Optional[str] = None
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
    # API Keys passed from Next.js
    api_key: Optional[str] = None
    openai_key: Optional[str] = None
    ai_model: Optional[str] = None

class ScannerRequest(BaseModel):
    city: Optional[str] = None
    district: Optional[str] = None
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

class GibDraftRequest(BaseModel):
    username: str
    password: str
    invoice_data: dict
    company_id: Optional[str] = "default"

class InvoiceETTNResponse(BaseModel):
    ettn: str

@app.get("/invoice/generate-ettn", response_model=InvoiceETTNResponse)
async def generate_ettn():
    import uuid
    return {"ettn": str(uuid.uuid4())}

@app.post("/invoice/prepare-gib-data")
async def prepare_gib_data(invoice_data: dict):
    # This endpoint can be used to format data specifically for GİB portal if needed
    # For now, it returns the data with a generated ETTN
    import uuid
    invoice_data["ettn"] = str(uuid.uuid4())
    invoice_data["status"] = "PREPARED"
    return invoice_data

@app.get("/")
def read_root():
    return {"status": "ok", "message": "EstateAI FastAPI Engine is running."}

@app.post("/valuation")
async def run_valuation(req: ValuationRequest):
    # Unique profile directory and port for this request
    request_id = str(uuid.uuid4())
    profile_dir = os.path.join(os.getcwd(), "temp_profiles", f"profile_{request_id}")
    os.makedirs(profile_dir, exist_ok=True)
    # 9000-9999 arası rastgele bir port (Çakışmayı önlemek için)
    import random as rnd
    browser_port = rnd.randint(9000, 9999)
    
    async with semaphore:
        try:
            print(f"Valuation request [{request_id}] started (Port: {browser_port}) with profile: {profile_dir}", file=sys.stderr, flush=True)
            
            # Pass the unique profile_dir and port to the engine
            result = await run_in_thread(
                run_valuation_hybrid,
                req.address_text, req.rooms, req.area_m2, req.prop_type, req.company_id, 
                lat=req.lat, lon=req.lng,
                city=req.city,
                district=req.district,
                neighborhood=req.neighborhood,
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
                has_security=req.has_security,
                api_key=req.api_key,
                openai_key=req.openai_key,
                ai_model=req.ai_model,
                user_data_dir=profile_dir,
                browser_port=browser_port
            )
            return result
        except Exception as e:
            import traceback
            print(f"Error in request [{request_id}]: {str(e)}", file=sys.stderr)
            return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})
        finally:
            # Cleanup profile directory after use
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
                print(f"Cleanup done for profile: {profile_dir}", file=sys.stderr)
            except:
                pass

@app.post("/scanner")
async def run_scanner(req: ScannerRequest):
    request_id = str(uuid.uuid4())
    profile_dir = os.path.join(os.getcwd(), "temp_profiles", f"profile_{request_id}")
    os.makedirs(profile_dir, exist_ok=True)
    import random as rnd
    browser_port = rnd.randint(9000, 9999)

    async with semaphore:
        try:
            print(f"Scanner request [{request_id}] started (Port: {browser_port}) with profile: {profile_dir}", file=sys.stderr, flush=True)
            results = await run_in_thread(
                scrape_fsbo,
                req.district, req.prop_type, req.listing_type, 
                city=req.city, neighborhood=req.neighborhood, company_id=req.company_id,
                min_price=req.min_price, max_price=req.max_price,
                room_count=req.room_count, building_age=req.building_age,
                floor_level=req.floor_level, heating_type=req.heating_type,
                site_status=req.site_status, furnished_status=req.furnished_status,
                user_data_dir=profile_dir,
                browser_port=browser_port
            )
            return results
        except Exception as e:
            import traceback
            return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})
        finally:
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
            except:
                pass

@app.post("/invoice/send-to-gib")
async def send_to_gib(req: GibDraftRequest):
    try:
        from gib_automator import create_gib_draft
        print(f"GİB Draft request received for user: {req.username}", flush=True)
        result = create_gib_draft(req.username, req.password, req.invoice_data)
        return result
    except Exception as e:
        import traceback
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})

@app.get("/valuation/download-pdf")
async def download_pdf(path: str):
    from fastapi.responses import FileResponse
    import os
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="PDF dosyası bulunamadı.")
    
    # Güvenlik: Sadece .pdf dosyalarına izin ver
    if not path.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Geçersiz dosya formatı.")
        
    return FileResponse(path, media_type='application/pdf', filename=os.path.basename(path))

if __name__ == "__main__":
    # VDS ortamında sorunsuz çalışması için host "0.0.0.0"
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
