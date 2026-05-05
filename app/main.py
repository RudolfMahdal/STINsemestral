import os
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

class SettingsUpdate(BaseModel):
    """
    Data Transfer Object (DTO) for updating user settings.
    """
    base_currency: str
    selected_currencies: str

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
        
    def get_latest_rates(self, base: str = "EUR", symbols: str = "USD,CZK,GBP"):
        """
        Fetches live currency rates from the external API.
        """
        if not self.api_key:
            raise ValueError("API key is missing in the .env file!")
            
        url = f"http://api.exchangerate.host/live?access_key={self.api_key}&base={base}&symbols={symbols}"
        
        with httpx.Client() as client:
            response = client.get(url)
            response.raise_for_status() # Raises an exception for 4xx/5xx errors
            return response.json()

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
    db: Session = Depends(get_db), 
    username: str = Depends(verify_credentials)
):
    """
    Calculates the strongest, weakest currency and the average
    based on the user's saved settings in the database.
    Ensures external API data is properly filtered.
    """
    settings = db.query(models.UserSettings).filter(models.UserSettings.id == 1).first()
    
    base_curr = settings.base_currency if settings else "EUR"
    symbols = settings.selected_currencies if settings else "USD,CZK,GBP"
    
    data = exchange_client.get_latest_rates(base=base_curr, symbols=symbols)
    
    all_rates = data.get("rates") or data.get("quotes", {})
    actual_base = data.get("base") or data.get("source") or base_curr
    
    requested_symbols = [s.strip().upper() for s in symbols.split(",")]
    filtered_rates = {}
    
    for sym in requested_symbols:
        # Match exact symbol (e.g., 'JPY') or prefixed symbol (e.g., 'USDJPY')
        if sym in all_rates:
            filtered_rates[sym] = all_rates[sym]
        elif f"{actual_base}{sym}" in all_rates:
            filtered_rates[f"{actual_base}{sym}"] = all_rates[f"{actual_base}{sym}"]
            
    # Fallback to all rates if filtering fails completely
    rates_to_analyze = filtered_rates if filtered_rates else all_rates
    
    logger.info(f"User '{username}' ran analysis for base '{actual_base}' and symbols '{symbols}'")
    return {
        "base_currency": actual_base,
        "date": data.get("date") or data.get("timestamp"),
        "strongest_currency": get_strongest_currency(rates_to_analyze),
        "weakest_currency": get_weakest_currency(rates_to_analyze),
        "average_rate": calculate_average(rates_to_analyze),
        "analyzed_rates": rates_to_analyze,
        "settings_used": {
            "base": base_curr,
            "symbols": symbols
        }
    }

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
    
    db.commit()
    db.refresh(settings)
    logger.info(f"User '{username}' updated settings to base: {new_settings.base_currency}, symbols: {new_settings.selected_currencies}")
    return {"message": "Settings updated successfully", "settings": settings}