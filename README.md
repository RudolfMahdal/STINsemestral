# Currency Analyzer API & Dashboard

A REST API and web dashboard for fetching, caching, and analyzing foreign exchange rates. Created as a semester project for the Software Engineering (STIN) course.

## Live Demo
The application is deployed and accessible in the cloud via Render.

* URL: https://stinsemestral.onrender.com/dashboard
* Username: admin
* Password: tajneheslo

## Features Implemented
* RESTful API built with FastAPI.
* External API Integration (ExchangeRate API).
* In-Memory Caching (10-minute TTL) to prevent external API rate limiting.
* Persistent Database (SQLite/SQLAlchemy) for user settings.
* Basic Authentication protecting all endpoints.
* Interactive Frontend Dashboard (HTML/JS).
* Robust Error Handling (401, 403, 404, 500) without native browser popups.

## Local Installation

1. Clone the repository:
git clone https://github.com/RudolfMahdal/STINsemestral.git
cd STINsemestral

2. Create a virtual environment and install dependencies:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

3. Set up Environment Variables:
Create a .env file in the root directory:
EXCHANGERATE_API_KEY=your_api_key_here
ADMIN_USERNAME=admin
ADMIN_PASSWORD=tajneheslo

4. Run the server:
uvicorn app.main:app --reload

The dashboard will be available at http://127.0.0.1:8000/dashboard

## Testing & Coverage
To run the automated unit tests and generate a coverage report (verifying the >80% requirement):

pytest --cov=app tests/