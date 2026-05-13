import os
import time
import httpx
from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from dotenv import load_dotenv
from app.core.logger import logger
from app.core.analyzer import get_strongest_currency, get_weakest_currency, calculate_average
from app.infrastructure.database import engine, Base, get_db
from app.core import models
from app.core.security import verify_credentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from datetime import datetime, timedelta
#TODO persistent user setting, switching base currency, historical rates, github test coverage
# Load environment variables from .env file
load_dotenv()
API_KEY = os.getenv("EXCHANGERATE_API_KEY")

# Create database tables upon startup
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Currency Analyzer",
    description="REST API for currency rate analysis (STIN 2026)",
    version="0.1.0"
)

# --- IN-MEMORY CACHE ---
API_CACHE = {}
CACHE_TTL_SECONDS = 600

class SettingsUpdate(BaseModel):
    """
    Data Transfer Object (DTO) for updating user settings.
    """
    base_currency: str
    selected_currencies: str
    chart_currency: str

# --- MOCK CLIENT ---
class MockExchangeRateClient:
    def get_latest_rates(self, base: str = "EUR", symbols: str = "USD,CZK,GBP"):
        """
        Returns mocked data corresponding to the DSP documentation.
        """
        return {
            "success": True,
            "timestamp": 1700000000,
            "base": base,
            "date": "2026-04-27",
            "rates": {"USD": 1.08, "CZK": 24.5, "GBP": 0.85}
        }

# --- REAL CLIENT ---
class ExchangeRateClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        
    def get_rates(self, base: str = "EUR", symbols: str = "USD,CZK,GBP", date: str = None):
        """
        Fetches currency rates from the external API (Live or Historical).
        """
        if not self.api_key:
            raise ValueError("API key is missing in the .env file!")
            
        if date:
            # Historical data
            url = f"http://api.exchangerate.host/historical?access_key={self.api_key}&date={date}&base={base}&symbols={symbols}"
        else:
            # Live data
            url = f"http://api.exchangerate.host/live?access_key={self.api_key}&base={base}&symbols={symbols}"
        
        with httpx.Client() as client:
            response = client.get(url)
            response.raise_for_status()
            return response.json()
import json

def get_snapshot_from_db_or_api(date_str: str, db: Session) -> dict:
    existing = db.query(models.DailySnapshot).filter(
        models.DailySnapshot.date == date_str
    ).first()
    
    if existing:
        logger.info(f"DB HIT: Rates for {date_str} loaded from SQLite")
        return json.loads(existing.rates_json)
    
    logger.info(f"DB MISS: Fetching {date_str} from API")
    try:
        raw = exchange_client.get_rates(base="USD", symbols="EUR,CZK,GBP,CAD,CHF,JPY,PLN", date=date_str)
    except httpx.HTTPStatusError as e:
        logger.warning(f"API returned {e.response.status_code} for {date_str}, skipping.")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error fetching {date_str}: {e}")
        return {}
    
    if not raw or not raw.get("success"):
        logger.warning(f"API returned no usable data for {date_str}")
        return {}

    rates = raw.get("rates") or raw.get("quotes", {})
    clean = {}
    for k, v in rates.items():
        clean_key = k[3:] if (len(k) == 6 and k.startswith("USD")) else k
        clean[clean_key] = v
    clean["USD"] = 1.0
    snapshot = models.DailySnapshot(date=date_str, rates_json=json.dumps(clean))
    db.add(snapshot)
    db.commit()
    logger.info(f"Saved {date_str} to SQLite ({len(clean)} currencies)")
    return clean
# --- DEPENDENCY INJECTION ---
exchange_client = ExchangeRateClient(api_key=API_KEY)
# exchange_client = MockExchangeRateClient() 

# --- REST ENDPOINTS ---
@app.get("/")
def read_root():
    """
    Root endpoint to verify the API is running.
    """
    return {"status": "ok", "message": "Currency Analyzer API is running!"}

@app.get("/api/v1/rates")
def get_rates(username: str = Depends(verify_credentials)):
    """
    Retrieves the latest raw currency rates.
    """
    try:
        data = exchange_client.get_latest_rates()
        logger.info(f"User '{username}' successfully fetched raw rates.")
        return data
    except Exception as e:
        logger.error(f"Error fetching rates for user '{username}': {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error communicating with API: {str(e)}")
        
@app.get("/api/v1/analyze")
def analyze_rates(
    date: str = None,
    db: Session = Depends(get_db), 
    username: str = Depends(verify_credentials)
):
    """
    Calculates the strongest, weakest currency and the average.
    Supports historical dates (YYYY-MM-DD) and handles API base currency limitations via cross-rates.
    """
    settings = db.query(models.UserSettings).filter(models.UserSettings.id == 1).first()
    
    base_curr = settings.base_currency if settings else "EUR"
    symbols = settings.selected_currencies if settings else "USD,CZK,GBP"
    
    cache_date = date if date else "live"
    cache_key = f"{base_curr}_{symbols}_{cache_date}"
    current_time = time.time()

    if date:
        try:
            # Pokusíme se datum převést. Pokud má špatný formát, spadne to do except bloku.
            parsed_date = datetime.strptime(date, "%Y-%m-%d").date()
            # Kontrola budoucnosti
            if parsed_date > datetime.today().date():
                raise HTTPException(status_code=400, detail="Nelze stahovat kurzy z budoucnosti!")
        except ValueError as e:
            if "budoucnosti" in str(e):
                raise HTTPException(status_code=400, detail=str(e))
            raise HTTPException(status_code=400, detail="Neplatný formát data. Použijte YYYY-MM-DD.")

    if cache_key in API_CACHE and (current_time - API_CACHE[cache_key]["timestamp"]) < CACHE_TTL_SECONDS:
        rates_data = API_CACHE[cache_key]["data"]
        logger.info(f"CACHE HIT: Returning data from memory for key {cache_key}")
    else:
        # TRIK: Vždy si musíme stáhnout i kurz naší požadované Base měny, abychom mohli dělat matematiku
        request_symbols = symbols
        if base_curr not in request_symbols:
            request_symbols += f",{base_curr}"
            
        rates_data = exchange_client.get_rates(base_curr, request_symbols, date)
        
        if rates_data and (rates_data.get("success") or "quotes" in rates_data):
            API_CACHE[cache_key] = {
                "timestamp": current_time,
                "data": rates_data
            }
            logger.info(f"API CALL: New data from internet cached for key {cache_key}")
    # --- PŘÍPRAVA A NORMALIZACE DAT ---
    raw_rates = rates_data.get("rates") or rates_data.get("quotes", {})
    # Defaultujeme na USD, protože free API vrací vždy kurzy k USD
    api_base = rates_data.get("base") or rates_data.get("source") or "USD"
    
    clean_rates = {}
    for key, rate in raw_rates.items():
        if len(key) == 6 and key.startswith("USD"):
            clean_key = key[3:]
        elif key == api_base:
            clean_key = key  # nezkracuj base měnu samotnou (USD → USD, ne "")
        else:
            clean_key = key.replace(api_base, "") if key.startswith(api_base) else key
        
        if clean_key:  # nikdy neukládej prázdný string
            clean_rates[clean_key] = rate
    clean_rates[api_base] = 1.0
    # --- CROSS-RATE MATEMATIKA ---
    if api_base != base_curr:
        conversion_rate = clean_rates.get(base_curr)
        if conversion_rate:
            recalculated_rates = {}
            for sym, rate in clean_rates.items():
                recalculated_rates[sym] = rate / conversion_rate
            
            clean_rates = recalculated_rates
            rates_data["base"] = base_curr
            logger.info(f"Cross-rate calculation: Base converted from {api_base} to {base_curr}")
            
    # --- FILTROVÁNÍ NA ZÁKLADĚ ČISTÝCH KLÍČŮ ---
    actual_base = rates_data.get("base") or base_curr
    requested_symbols = [s.strip().upper() for s in symbols.split(",")]
    filtered_rates = {}
    
    for sym in requested_symbols:
        if sym in clean_rates:
            filtered_rates[sym] = clean_rates[sym]
            
    rates_to_analyze = filtered_rates if filtered_rates else clean_rates
    
    logger.info(f"User '{username}' ran analysis for base '{actual_base}' and symbols '{symbols}'")
    return {
        "base_currency": actual_base,
        "date": rates_data.get("date") or rates_data.get("timestamp"),
        "strongest_currency": get_strongest_currency(rates_to_analyze),
        "weakest_currency": get_weakest_currency(rates_to_analyze),
        "average_rate": calculate_average(rates_to_analyze),
        "analyzed_rates": rates_to_analyze,
        "settings_used": {
        "base": base_curr,
        "symbols": symbols,
        "chart_currency": settings.chart_currency if settings else "CZK"  # NOVÉ
    }
    }

@app.get("/api/v1/trends")
def get_trends(days: int = 7, db: Session = Depends(get_db), username: str = Depends(verify_credentials)):
    settings = db.query(models.UserSettings).filter(models.UserSettings.id == 1).first()
    base_curr = settings.base_currency if settings else "EUR"
    chart_curr = settings.chart_currency if settings else "CZK"  # NOVÉ - jen tato jedna

    final_chart_data = {"labels": [], "datasets": {chart_curr: []}}

    for i in range(days - 1, -1, -1):
        date_str = (datetime.today() - timedelta(days=i)).strftime('%Y-%m-%d')
        anchor_rates = get_snapshot_from_db_or_api(date_str, db)
        
        if not anchor_rates:
            continue

        base_rate = anchor_rates.get(base_curr)
        if not base_rate:
            continue

        final_chart_data["labels"].append(date_str)
        
        if chart_curr == base_curr:
            final_chart_data["datasets"][chart_curr].append(1.0)
        elif chart_curr in anchor_rates:
            cross = round(anchor_rates[chart_curr] / base_rate, 4)
            final_chart_data["datasets"][chart_curr].append(cross)
        else:
            final_chart_data["datasets"][chart_curr].append(None)

    return final_chart_data

@app.get("/api/v1/settings")
def get_user_settings(db: Session = Depends(get_db), username: str = Depends(verify_credentials)):
    """
    Retrieves user settings from the database. 
    If no settings exist (first run), creates default settings.
    """
    settings = db.query(models.UserSettings).filter(models.UserSettings.id == 1).first()
    
    if not settings:
        settings = models.UserSettings(id=1, base_currency="EUR", selected_currencies="USD,CZK,GBP")
        db.add(settings)
        db.commit()
        db.refresh(settings)
        
    return settings

@app.put("/api/v1/settings")
def update_user_settings(new_settings: SettingsUpdate, db: Session = Depends(get_db), username: str = Depends(verify_credentials)):  
    """
    Updates the user's preferred currencies in the persistent database.
    """
    settings = db.query(models.UserSettings).filter(models.UserSettings.id == 1).first()
    if not settings:
        settings = models.UserSettings(id=1)
        db.add(settings)
    
    settings.base_currency = new_settings.base_currency
    settings.selected_currencies = new_settings.selected_currencies
    settings.chart_currency = new_settings.chart_currency  # NOVÉ
    
    db.commit()
    db.refresh(settings)
    return {"message": "Settings updated successfully", "settings": settings}

# --- FRONTEND ---
import os
os.makedirs("app/static", exist_ok=True) # Ensure the folder exists
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/dashboard")
def get_dashboard():
    """
    Redirects to the main frontend dashboard.
    """
    return RedirectResponse(url="/static/index.html")