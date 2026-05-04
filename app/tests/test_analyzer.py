import pytest
from app.core.analyzer import get_strongest_currency, get_weakest_currency, calculate_average

# Testovací data (odpovídají struktuře z API)
mock_rates = {
    "USD": 1.08,
    "CZK": 24.5,
    "GBP": 0.85
}

def test_strongest_currency():
    # Podle DSP: nejsilnější měna = nejvyšší nominální hodnota [cite: 44, 45]
    assert get_strongest_currency(mock_rates) == "CZK"

def test_weakest_currency():
    # Nejslabší měna = nejnižší nominální hodnota [cite: 45]
    assert get_weakest_currency(mock_rates) == "GBP"

def test_calculate_average():
    # Aritmetický průměr [cite: 48]
    # (1.08 + 24.5 + 0.85) / 3 = 8.81
    assert calculate_average(mock_rates) == pytest.approx(8.81, 0.01)

def test_empty_rates():
    # Test hraničních hodnot (Boundary Value Analysis z 3. přednášky)
    assert get_strongest_currency({}) is None
    assert calculate_average({}) == 0.0