def get_strongest_currency(rates: dict) -> str:
    """
    Returns the currency code with the highest nominal value.
    """
    if not rates:
        return None
    return max(rates, key=rates.get)

def get_weakest_currency(rates: dict) -> str:
    """
    Returns the currency code with the lowest nominal value.
    """
    if not rates:
        return None
    return min(rates, key=rates.get)

def calculate_average(rates: dict) -> float:
    """
    Calculates the arithmetic average of the given currency rates.
    """
    if not rates:
        return 0.0
    total = sum(rates.values())
    return total / len(rates)