from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app
from app.core import security

# Create a TestClient instance to simulate HTTP requests
client = TestClient(app)

# Use the dynamically loaded credentials to avoid hardcoded secrets in tests
TEST_USER = security.ADMIN_USERNAME
TEST_PASS = security.ADMIN_PASSWORD
VALID_AUTH = (TEST_USER, TEST_PASS)

def test_read_root():
    """
    Test the root endpoint for a basic status check (No auth required).
    """
    response = client.get("/")
    assert response.status_code == 200
    assert "status" in response.json()

def test_analyze_unauthorized():
    """
    Test that the analyze endpoint rejects requests without basic auth completely.
    """
    response = client.get("/api/v1/analyze")
    assert response.status_code == 403  

def test_analyze_wrong_credentials():
    """
    Test that the analyze endpoint rejects requests with invalid basic auth.
    """
    response = client.get("/api/v1/analyze", auth=("wrong_user", "bad_password"))
    assert response.status_code == 403  

# OPRAVA: Místo get_latest_rates použijeme get_rates
@patch('app.main.exchange_client.get_rates')
def test_analyze_rates_authorized(mock_get_rates):
    """
    Test the /analyze endpoint with valid auth and mocked external API data.
    """
    mock_get_rates.return_value = {
        "success": True,
        "base": "USD",
        "date": "2026-04-27",
        "rates": {
            "USD": 1.0,
            "CZK": 24.5,
            "EUR": 0.92
        }
    }
    
    response = client.get("/api/v1/analyze", auth=VALID_AUTH)
    assert response.status_code == 200
    
    # Pass the authentication tuple to the test client
    response = client.get("/api/v1/analyze", auth=VALID_AUTH)
    
    assert response.status_code == 200
    data = response.json()
    assert "strongest_currency" in data
    assert "weakest_currency" in data

def test_get_settings_authorized():
    """
    Test reading user settings from the database.
    """
    response = client.get("/api/v1/settings", auth=VALID_AUTH)
    assert response.status_code == 200
    data = response.json()
    assert "base_currency" in data
    assert "selected_currencies" in data

def test_update_settings_authorized():
    """
    Test updating user settings in the database.
    """
    payload = {
        "base_currency": "EUR",
        "selected_currencies": "JPY,CAD",
        "chart_currency": "CZK" 
    }
    # tvůj auth a client.put...
    response = client.put("/api/v1/settings", json=payload, auth=VALID_AUTH)
    assert response.status_code == 200

def test_get_trends_authorized():
    """
    Test the historical trends endpoint ensures the loop and DB fallback works.
    """
    # Zkusíme stáhnout data jen pro 2 dny, ať to netrvá dlouho
    response = client.get("/api/v1/trends?days=2", auth=VALID_AUTH)
    
    assert response.status_code == 200
    data = response.json()
    
    assert "labels" in data
    assert "datasets" in data
    assert type(data["labels"]) is list