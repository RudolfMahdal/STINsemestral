import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import httpx
from datetime import datetime, timedelta

from app.main import app, _API_CACHE
from app.infrastructure.database import Base, get_db
from app.core.security import verify_credentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool  # <-- 1. PŘIDAT TENTO IMPORT

# --- SETUP TESTOVACÍ DATABÁZE (V RAM PAMĚTI) ---
engine = create_engine(
    "sqlite:///:memory:", 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool  # <-- 2. PŘIDAT TENTO PARAMETR
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Oklameme FastAPI, aby místo reálné DB a ověřování použilo naše testovací
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_credentials] = lambda: "admin"

client = TestClient(app)

@pytest.fixture(autouse=True)
def clear_state():
    """Před každým testem vyčistí databázi a RAM cache, abychom měli čistý stůl."""
    _API_CACHE.clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

# ==========================================
# TESTY ZÁKLADNÍCH ENDPOINTŮ
# ==========================================

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "status" in response.json()

def test_get_dashboard():
    # FastAPI RedirectResponse vrací defaultně 307
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 307

# ==========================================
# TESTY NASTAVENÍ (SETTINGS)
# ==========================================

def test_settings_workflow():
    # 1. Získání výchozího nastavení
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["base_currency"] == "EUR"
    
    # 2. Úprava nastavení
    payload = {
        "base_currency": "USD",
        "selected_currencies": "JPY,CAD",
        "chart_currency": "CAD"
    }
    response_put = client.put("/api/v1/settings", json=payload)
    assert response_put.status_code == 200
    assert response_put.json()["settings"]["base_currency"] == "USD"

# ==========================================
# TESTY EXTERNÍHO API (MOCKING)
# ==========================================

@patch('app.main.exchange_client.get_rates')
def test_get_rates_raw(mock_get_rates):
    mock_get_rates.return_value = {"success": True, "rates": {"CZK": 25.0}}
    response = client.get("/api/v1/rates")
    assert response.status_code == 200
    assert response.json()["rates"]["CZK"] == 25.0

@patch('app.main.exchange_client.get_rates')
def test_analyze_rates_success(mock_get_rates):
    # Simulace odpovědi APILayeru s křížovými kurzy (USDEUR, USDCZK)
    mock_get_rates.return_value = {
        "success": True,
        "source": "USD",
        "quotes": {
            "USDEUR": 0.9,
            "USDCZK": 22.5,
            "USDGBP": 0.8
        }
    }
    
    response = client.get("/api/v1/analyze")
    assert response.status_code == 200
    data = response.json()
    assert data["base_currency"] == "EUR" # Kód by měl přepočítat USD na EUR
    assert "CZK" in data["analyzed_rates"]

def test_analyze_rates_invalid_date():
    response = client.get("/api/v1/analyze?date=spatne_datum")
    assert response.status_code == 400
    assert "Invalid date format" in response.json()["detail"]

def test_analyze_rates_future_date():
    future = (datetime.today() + timedelta(days=10)).strftime("%Y-%m-%d")
    response = client.get(f"/api/v1/analyze?date={future}")
    assert response.status_code == 400
    assert "future date" in response.json()["detail"]

@patch('app.main.exchange_client.get_rates')
def test_analyze_rates_http_error(mock_get_rates):
    # Simulace pádu API (např. vyčerpaný limit 429)
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_get_rates.side_effect = httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_response)
    
    response = client.get("/api/v1/analyze")
    assert response.status_code == 502

@patch('app.main.exchange_client.get_rates')
def test_analyze_rates_empty_data(mock_get_rates):
    mock_get_rates.return_value = {}
    response = client.get("/api/v1/analyze")
    assert response.status_code == 503

# ==========================================
# TESTY HISTORICKÉHO GRAFU A CACHE
# ==========================================

@patch('app.main.exchange_client.get_rates')
def test_trends_endpoint_with_cache(mock_get_rates):
    # Tento test proběhne dvakrát pro kontrolu DB MISS a DB HIT u _get_snapshot
    mock_get_rates.return_value = {
        "success": True,
        "base": "USD",
        "rates": {
            "EUR": 0.9,
            "CZK": 23.0,
            "GBP": 0.8
        }
    }
    
    # První volání - API se musí zavolat (DB MISS) a data se uloží do SQLite
    response1 = client.get("/api/v1/trends?days=2")
    assert response1.status_code == 200
    assert mock_get_rates.call_count > 0
    
    # Vyresetujeme počítadlo volání API, abychom poznali, jestli se znovu zavolalo
    mock_get_rates.reset_mock()
    
    # Druhé volání stejného endpointu - Data by se měla vzít z SQLite (DB HIT)
    response2 = client.get("/api/v1/trends?days=2")
    assert response2.status_code == 200
    assert mock_get_rates.call_count == 0 # API se nesmělo vůbec zavolat!
    
    data = response2.json()
    assert "labels" in data
    assert "CZK" in data["datasets"]
    assert data["period_average"] is not None