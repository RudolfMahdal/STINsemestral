from sqlalchemy import Column, Integer, String
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