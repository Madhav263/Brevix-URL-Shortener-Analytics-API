import os
import string
import random
import sqlite3
import httpx
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# --- RENDER PATH FIX ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "urls.db")

def get_db_conn():
    conn = sqlite3.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_conn() as conn:
        # Table for URLs
        conn.execute("""
            CREATE TABLE IF NOT EXISTS urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_url TEXT NOT NULL,
                short_code TEXT UNIQUE NOT NULL,
                clicks INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Table for Detailed Analytics
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_code TEXT,
                click_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                country TEXT,
                browser TEXT,
                FOREIGN KEY (short_code) REFERENCES urls (short_code)
            )
        """)
    print("Database initialized with full analytics support.")

init_db()

# Helper to generate unique short tokens
def generate_short_code(length=7):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def serve_frontend(request: Request):
    # Pass the actual base URL of the deployment to the frontend
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "base_url": str(request.base_url).rstrip('/')
    })

@app.post("/shorten")
async def shorten_url(original_url: str = Form(...)):
    if not original_url.startswith(("http://", "https://")):
        original_url = "https://" + original_url
    
    with get_db_conn() as conn:
        # Check if URL already exists to save space
        existing = conn.execute("SELECT short_code FROM urls WHERE original_url = ?", (original_url,)).fetchone()
        if existing:
            return {"short_url": existing["short_code"]}
        
        short_code = generate_short_code()
        try:
            conn.execute("INSERT INTO urls (original_url, short_code) VALUES (?, ?)", (original_url, short_code))
            conn.commit()
        except sqlite3.IntegrityError:
            short_code = generate_short_code() # Retry once on collision
            conn.execute("INSERT INTO urls (original_url, short_code) VALUES (?, ?)", (original_url, short_code))
            conn.commit()
    
    return {"short_url": short_code}

@app.get("/{short_code}")
async def redirect_url(short_code: str, request: Request):
    with get_db_conn() as conn:
        url_data = conn.execute("SELECT original_url FROM urls WHERE short_code = ?", (short_code,)).fetchone()
        if not url_data:
            raise HTTPException(status_code=404, detail="Brevix Link not found")
        
        # --- Advanced Telemetry ---
        user_agent = request.headers.get("user-agent", "Unknown")
        client_ip = request.headers.get("x-forwarded-for", request.client.host)
        
        country = "International"
        try:
            # Async call to get country from IP
            async with httpx.AsyncClient() as client:
                res = await client.get(f"https://ipapi.co/{client_ip.split(',')[0]}/country_name/", timeout=1.5)
                if res.status_code == 200:
                    country = res.text
        except:
            pass

        # Update stats
        conn.execute("UPDATE urls SET clicks = clicks + 1 WHERE short_code = ?", (short_code,))
        conn.execute("INSERT INTO analytics (short_code, country, browser) VALUES (?, ?, ?)", 
                     (short_code, country, user_agent[:50]))
        conn.commit()
        
        return RedirectResponse(url=url_data["original_url"])

@app.get("/api/analytics/{short_code}")
async def get_analytics(short_code: str):
    with get_db_conn() as conn:
        url_info = conn.execute("SELECT original_url, clicks, created_at FROM urls WHERE short_code = ?", (short_code,)).fetchone()
        if not url_info:
            return {"error": "Link not found in Brevix database"}
        
        # Get country distribution
        geo_data = conn.execute("""
            SELECT country, COUNT(*) as count 
            FROM analytics 
            WHERE short_code = ? 
            GROUP BY country 
            ORDER BY count DESC
        """, (short_code,)).fetchall()
        
        return {
            "original_url": url_info["original_url"],
            "total_clicks": url_info["clicks"],
            "created_at": url_info["created_at"],
            "geo_distribution": {row["country"]: row["count"] for row in geo_data}
        }
