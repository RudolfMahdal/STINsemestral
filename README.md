# Currency Analyzer API & Dashboard

A REST API and web dashboard for fetching, caching, and analyzing foreign exchange rates. Created as a semester project for the Software Engineering (STIN) course.

## Live Demo
The application is deployed and accessible in the cloud via Render.

* URL: https://stinsemestral.onrender.com/dashboard
* Username: admin
* Password: tajneheslo

## Features Implemented
* **RESTful API:** Built with FastAPI for high performance and automatic documentation.
* **Anchor Currency Architecture (Cross-Rates):** Ingeniously bypasses strict Free-tier API limitations (which block base-currency switching and bulk timeframe queries). The backend fetches USD-anchored data once, stores it in an Anchor Cache, and dynamically calculates cross-rates for any user-selected base currency using custom mathematics.
* **Historical Trends & Charting:** Interactive line graphs (powered by Chart.js) visualizing currency performance over user-defined periods without exhausting API rate limits.
* **Multi-Layer Caching:** Implements both a short-term In-Memory Cache (10-minute TTL) for live data and a persistent Anchor Cache for immutable historical data to drastically reduce external API calls.
* **Persistent User Settings:** SQLite database integration (via SQLAlchemy) to save user preferences across sessions.
* **Security & Error Handling:** Basic Authentication protecting all endpoints, combined with robust frontend error handling (401, 403, 404, 500) that seamlessly updates the UI without native browser popups.

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
EXCHANGERATE_API_KEY=your_api_key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=tajneheslo

4. Run the server:
uvicorn app.main:app --reload

The dashboard will be available at http://127.0.0.1:8000/dashboard

## Testing & Coverage
The project includes a comprehensive test suite. To run the automated unit tests and generate a coverage report locally:
pytest --cov=app app/tests/
Note: Tests are also executed automatically via GitHub Actions upon pushing to the repository.