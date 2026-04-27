import os
import httpx
from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
from app.core.analyzer import get_strongest_currency, get_weakest_currency, calculate_average
# Načtení proměnných z .env souboru
load_dotenv()
API_KEY = os.getenv("EXCHANGERATE_API_KEY")

app = FastAPI(title="Currency Analyzer",)

# --- MOCK ---
class MockExchangeRateClient:
    def get_latest_rates(self, base: str = "EUR", symbols: str = "USD,CZK,GBP"):
        return {
            "success": True,
            "timestamp": 1700000000,
            "base": base,
            "date": "2026-04-27",
            "rates": {"USD": 1.08, "CZK": 24.5, "GBP": 0.85}
        }

# --- KLIENT ---
class ExchangeRateClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        
    def get_latest_rates(self, base: str = "EUR", symbols: str = "USD,CZK,GBP"):
        if not self.api_key:
            raise ValueError("API klíč chybí v .env souboru!")
            
        url = f"http://api.exchangerate.host/live?access_key={self.api_key}&base={base}&symbols={symbols}"
        
        with httpx.Client() as client:
            response = client.get(url)
            response.raise_for_status() # Pokud API spadne, vyhodí výjimku
            return response.json()

# --- DEPENDENCY INJECTION ---
exchange_client = ExchangeRateClient(api_key=API_KEY)
# exchange_client = MockExchangeRateClient() 


# --- REST ENDPOINTY ---
@app.get("/")
def read_root():
    return {"status": "ok", "message": "Currency Analyzer API běží!"}

@app.get("/api/v1/rates")
def get_rates():
    try:
        data = exchange_client.get_latest_rates()
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chyba při komunikaci s API: {str(e)}")
    
@app.get("/api/v1/analyze")
def analyze_rates():
    """Vypočítá nejsilnější, nejslabší měnu a průměr."""
    data = exchange_client.get_latest_rates()
    rates = data.get("rates", {})
    
    return {
        "base_currency": data.get("base"),
        "date": data.get("date"),
        "strongest_currency": get_strongest_currency(rates),
        "weakest_currency": get_weakest_currency(rates),
        "average_rate": calculate_average(rates),
        "analyzed_rates": rates
    }