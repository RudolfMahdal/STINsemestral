from sqlalchemy import Column, String, Float, Integer
from app.infrastructure.database import Base

class UserSettings(Base):
    """
    Database model representing the UserSettings table.
    Stores the user's preferred currencies.
    Since we only have one user, we will typically just use row id=1.
    """
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    base_currency = Column(String, default="EUR")
    selected_currencies = Column(String, default="USD,CZK,GBP")
    chart_currency = Column(String, default="CZK")

class DailySnapshot(Base):
    __tablename__ = "daily_snapshots"
    date = Column(String, primary_key=True)   # "2026-05-10"
    rates_json = Column(String, nullable=False) # '{"CZK": 22.5, "EUR": 0.92}'