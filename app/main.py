import json
import os
import time
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.analyzer import calculate_average, get_strongest_currency, get_weakest_currency
from app.core.logger import logger
from app.core import models
from app.core.security import verify_credentials
from app.infrastructure.database import Base, engine, get_db

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()
API_KEY = os.getenv("EXCHANGERATE_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "")

Base.metadata.create_all(bind=engine)

# --- STARTUP LOG: show where data is stored/loaded from ---
_db_url = DATABASE_URL or "sqlite:///./app.db"
if _db_url.startswith("sqlite"):
    # Extract path from sqlite:///./app.db  →  app.db
    _db_file = _db_url.replace("sqlite:///", "").replace("sqlite://", "")
    logger.info("DATABASE: SQLite local (%s)", os.path.abspath(_db_file))
else:
    # Hide credentials, show only host/dbname after @
    logger.info("DATABASE: PostgreSQL / Neon cloud (%s)", _db_url.split("@")[-1])

if API_KEY:
    logger.info("EXCHANGE API: exchangerate.host (live, key set)")
else:
    logger.warning("EXCHANGE API: no API key — MockExchangeRateClient will be used")

# Human-readable DB label reused in runtime logs
_DB_LABEL = "Neon/PostgreSQL" if not _db_url.startswith("sqlite") else f"SQLite ({os.path.abspath(_db_file)})"

app = FastAPI(
    title="Currency Analyzer",
    description="REST API for currency rate analysis (STIN 2026)",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_API_CACHE: dict = {}
_CACHE_TTL = 600  # seconds

# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    base_currency: str
    selected_currencies: str
    chart_currency: str

# ---------------------------------------------------------------------------
# Exchange-rate clients
# ---------------------------------------------------------------------------

class MockExchangeRateClient:
    """Returns static data matching the DSP example response (tests / dev)."""

    def get_rates(self, base: str = "EUR", symbols: str = "USD,CZK,GBP", date: str = None) -> dict:
        return {
            "success": True,
            "timestamp": 1700000000,
            "base": base,
            "date": "2026-04-27",
            "rates": {"USD": 1.08, "CZK": 24.5, "GBP": 0.85},
        }


class ExchangeRateClient:
    """Fetches live or historical rates from exchangerate.host."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def get_rates(self, base: str = "EUR", symbols: str = "USD,CZK,GBP", date: str = None) -> dict:
        if not self.api_key:
            raise ValueError("EXCHANGERATE_API_KEY is missing from .env")

        if date:
            url = (
                f"http://api.exchangerate.host/historical"
                f"?access_key={self.api_key}&date={date}&base={base}&symbols={symbols}"
            )
        else:
            url = (
                f"http://api.exchangerate.host/live"
                f"?access_key={self.api_key}&base={base}&symbols={symbols}"
            )

        with httpx.Client(timeout=10) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.json()


# Swap to MockExchangeRateClient() for local dev without a real API key
exchange_client: ExchangeRateClient = ExchangeRateClient(api_key=API_KEY)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_snapshot(date_str: str, db: Session) -> dict:
    """
    Returns USD-anchored rates for *date_str*.
    Checks SQLite first; fetches from the API and persists on cache miss.
    Returns an empty dict if the API fails (caller must handle it).
    """
    existing = db.query(models.DailySnapshot).filter_by(date=date_str).first()
    if existing:
        logger.info("[DB HIT] %s loaded from %s", date_str, _DB_LABEL)
        return json.loads(existing.rates_json)

    logger.info("[DB MISS] %s not in %s — fetching from exchangerate.host", date_str, _DB_LABEL)
    try:
        raw = exchange_client.get_rates(
            base="USD", symbols="EUR,CZK,GBP,CAD,CHF,JPY,PLN", date=date_str
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("API %s for %s — skipping", exc.response.status_code, date_str)
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error fetching %s: %s", date_str, exc)
        return {}

    if not raw or not raw.get("success"):
        logger.warning("API returned no usable data for %s", date_str)
        return {}

    raw_rates = raw.get("rates") or raw.get("quotes", {})
    clean: dict = {}
    for k, v in raw_rates.items():
        key = k[3:] if (len(k) == 6 and k.startswith("USD")) else k
        if key:
            clean[key] = v
    clean["USD"] = 1.0

    db.add(models.DailySnapshot(date=date_str, rates_json=json.dumps(clean)))
    db.commit()
    logger.info("[DB WRITE] %s saved to %s (%d currencies)", date_str, _DB_LABEL, len(clean))
    return clean


def _normalize_rates(rates_data: dict) -> tuple[dict, str]:
    """
    Strips API-specific key prefixes (e.g. 'USDCZK' → 'CZK') and returns
    (clean_rates, api_base).
    """
    api_base = rates_data.get("base") or rates_data.get("source") or "USD"
    raw = rates_data.get("rates") or rates_data.get("quotes", {})

    clean: dict = {}
    for key, rate in raw.items():
        if len(key) == 6 and key.startswith("USD"):
            clean_key = key[3:]
        elif key == api_base:
            clean_key = key
        else:
            clean_key = key.replace(api_base, "") if key.startswith(api_base) else key
        if clean_key:
            clean[clean_key] = rate

    clean[api_base] = 1.0
    return clean, api_base


def _apply_cross_rates(clean_rates: dict, api_base: str, target_base: str) -> dict:
    """
    Recalculates all rates relative to *target_base* when *api_base* differs.
    """
    if api_base == target_base:
        return clean_rates
    conversion = clean_rates.get(target_base)
    if not conversion:
        logger.warning("Cannot convert base from %s to %s — rate missing", api_base, target_base)
        return clean_rates
    recalculated = {sym: rate / conversion for sym, rate in clean_rates.items()}
    logger.info("[CROSS-RATE] recalculated from %s base to %s", api_base, target_base)
    return recalculated

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Currency Analyzer API is running!"}


@app.get("/api/v1/rates")
def get_rates(username: str = Depends(verify_credentials)):
    """Returns the latest raw rates from the external API."""
    try:
        data = exchange_client.get_rates()
        logger.info("User '%s' fetched raw rates", username)
        return data
    except Exception as exc:
        logger.error("Error fetching rates for '%s': %s", username, exc)
        raise HTTPException(status_code=500, detail=f"API error: {exc}") from exc


@app.get("/api/v1/analyze")
def analyze_rates(
    date: str = None,
    db: Session = Depends(get_db),
    username: str = Depends(verify_credentials),
):
    """
    Returns strongest/weakest currency and arithmetic mean.
    Supports historical dates (YYYY-MM-DD).
    Handles free-tier base-currency restriction via cross-rate math.
    """
    settings = db.query(models.UserSettings).filter_by(id=1).first()
    base_curr = settings.base_currency if settings else "EUR"
    symbols = settings.selected_currencies if settings else "USD,CZK,GBP"

    # Validate date parameter
    if date:
        try:
            parsed = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        if parsed > datetime.today().date():
            raise HTTPException(status_code=400, detail="Cannot fetch rates for a future date.")

    cache_key = f"{base_curr}_{symbols}_{date or 'live'}"
    now = time.time()

    if cache_key in _API_CACHE and (now - _API_CACHE[cache_key]["ts"]) < _CACHE_TTL:
        rates_data = _API_CACHE[cache_key]["data"]
        logger.info("[MEM CACHE HIT] returning in-memory data for key: %s", cache_key)
    else:
        request_symbols = symbols if base_curr in symbols else f"{symbols},{base_curr}"
        try:
            rates_data = exchange_client.get_rates(base_curr, request_symbols, date)
        except httpx.HTTPStatusError as exc:
            logger.error("API HTTP error %s: %s", exc.response.status_code, exc)
            raise HTTPException(status_code=502, detail=f"External API error: {exc.response.status_code}") from exc
        except Exception as exc:
            logger.error("API call failed: %s", exc)
            raise HTTPException(status_code=503, detail=f"Could not reach exchange rate API: {exc}") from exc

        if not rates_data:
            raise HTTPException(status_code=503, detail="External API returned no data.")

        if rates_data.get("success") or "quotes" in rates_data:
            _API_CACHE[cache_key] = {"ts": now, "data": rates_data}
            logger.info("[MEM CACHE WRITE] data from exchangerate.host cached for key: %s", cache_key)

    clean_rates, api_base = _normalize_rates(rates_data)
    clean_rates = _apply_cross_rates(clean_rates, api_base, base_curr)

    if api_base != base_curr:
        rates_data["base"] = base_curr

    requested = [s.strip().upper() for s in symbols.split(",")]
    filtered = {sym: clean_rates[sym] for sym in requested if sym in clean_rates}
    rates_to_analyze = filtered or clean_rates

    logger.info("User '%s' analyzed base='%s' symbols='%s'", username, base_curr, symbols)
    return {
        "base_currency": rates_data.get("base") or base_curr,
        "date": rates_data.get("date") or rates_data.get("timestamp"),
        "strongest_currency": get_strongest_currency(rates_to_analyze),
        "weakest_currency": get_weakest_currency(rates_to_analyze),
        "average_rate": calculate_average(rates_to_analyze),
        "analyzed_rates": rates_to_analyze,
        "settings_used": {
            "base": base_curr,
            "symbols": symbols,
            "chart_currency": settings.chart_currency if settings else "CZK",
        },
    }


@app.get("/api/v1/trends")
def get_trends(
    days: int = 7,
    db: Session = Depends(get_db),
    username: str = Depends(verify_credentials),
):
    """Returns a daily rate time-series for the configured chart currency."""
    settings = db.query(models.UserSettings).filter_by(id=1).first()
    base_curr = settings.base_currency if settings else "EUR"
    chart_curr = settings.chart_currency if settings else "CZK"

    labels: list[str] = []
    values: list[float | None] = []

    for i in range(days - 1, -1, -1):
        date_str = (datetime.today() - timedelta(days=i)).strftime("%Y-%m-%d")
        anchor = _get_snapshot(date_str, db)
        if not anchor:
            continue

        base_rate = anchor.get(base_curr)
        if not base_rate:
            continue

        labels.append(date_str)
        if chart_curr == base_curr:
            values.append(1.0)
        elif chart_curr in anchor:
            values.append(anchor[chart_curr] / base_rate)
        else:
            values.append(None)

    valid = [v for v in values if v is not None]
    period_average = sum(valid) / len(valid) if valid else None

    return {
        "labels": labels,
        "datasets": {chart_curr: values},
        "period_average": period_average,
    }


@app.get("/api/v1/settings")
def get_user_settings(
    db: Session = Depends(get_db),
    username: str = Depends(verify_credentials),
):
    """Returns current settings; creates defaults on first run."""
    settings = db.query(models.UserSettings).filter_by(id=1).first()
    if not settings:
        settings = models.UserSettings(id=1, base_currency="EUR", selected_currencies="USD,CZK,GBP")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@app.put("/api/v1/settings")
def update_user_settings(
    new_settings: SettingsUpdate,
    db: Session = Depends(get_db),
    username: str = Depends(verify_credentials),
):
    """Persists updated currency preferences."""
    settings = db.query(models.UserSettings).filter_by(id=1).first()
    if not settings:
        settings = models.UserSettings(id=1)
        db.add(settings)

    settings.base_currency = new_settings.base_currency
    settings.selected_currencies = new_settings.selected_currencies
    settings.chart_currency = new_settings.chart_currency

    db.commit()
    db.refresh(settings)
    logger.info("User '%s' updated settings: %s", username, new_settings)
    return {"message": "Settings updated successfully", "settings": settings}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/dashboard")
def get_dashboard():
    return RedirectResponse(url="/static/index.html")