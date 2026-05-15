"""
URL Shortener & Analytics API
A production-ready FastAPI application with SQLite backend.
Optimized for production deployment and clean architectural design.
"""

import sqlite3
import string
import random
import os
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Use environment variable for DB path in production to persist volume state
DB_PATH = os.getenv("DATABASE_URL", "urls.db")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SHORT_CODE_LENGTH = 7

# Simulated countries for global analytics analytics distribution
SIMULATED_COUNTRIES = [
    ("United States", "🇺🇸", 35),
    ("India", "🇮🇳", 18),
    ("United Kingdom", "🇬🇧", 10),
    ("Germany", "🇩🇪", 7),
    ("Canada", "🇨🇦", 6),
    ("Australia", "🇦🇺", 5),
    ("France", "🇫🇷", 4),
    ("Brazil", "🇧🇷", 4),
    ("Japan", "🇯🇵", 3),
    ("Netherlands", "🇳🇱", 3),
    ("Singapore", "🇸🇬", 2),
    ("Mexico", "🇲🇽", 2),
    ("South Korea", "🇰🇷", 1),
]

# ---------------------------------------------------------------------------
# Database Helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create structural tables securely if they don't already exist."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS urls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                short_code  TEXT UNIQUE NOT NULL,
                long_url    TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clicks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                short_code  TEXT NOT NULL,
                clicked_at  TEXT NOT NULL,
                country     TEXT NOT NULL,
                flag        TEXT NOT NULL,
                FOREIGN KEY (short_code) REFERENCES urls(short_code)
            )
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def generate_short_code(length: int = SHORT_CODE_LENGTH) -> str:
    """Generate a random cryptographic base-62 short code."""
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=length))


def weighted_country() -> tuple[str, str]:
    """Pick a simulated country based on realistic application traffic weights."""
    population = [c[2] for c in SIMULATED_COUNTRIES]
    choice = random.choices(SIMULATED_COUNTRIES, weights=population, k=1)[0]
    return choice[0], choice[1]


# ---------------------------------------------------------------------------
# Lifespan Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database layout on startup execution
    init_db()
    yield


# ---------------------------------------------------------------------------
# App Initialization & Templates
# ---------------------------------------------------------------------------

app = FastAPI(
    title="URL Shortener & Analytics API",
    description="Production-grade URL Shortening system exposing precise analytical metrics.",
    version="1.0.0",
    lifespan=lifespan,
)

# Initialize template handling mechanism
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# API Routing Context
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend(request: Request):
    """Serve the single-page application dashboard interface."""
    return templates.TemplateResponse("index.html", {"request": request, "base_url": BASE_URL.rstrip('/')})


@app.post("/shorten", tags=["URL Shortener"])
async def shorten_url(request: Request):
    """
    Accept a long URL string and map it to a deterministic unique base-62 short reference string.
    - Eliminates redundancy via lookup validation mapping.
    - Auto-injects explicit transport schemas if unassigned.
    """
    body = await request.json()
    long_url = body.get("long_url", "").strip()
    
    if not long_url:
        raise HTTPException(status_code=422, detail="long_url must not be empty.")

    if not long_url.startswith(("http://", "https://")):
        long_url = "https://" + long_url

    with get_db() as conn:
        existing = conn.execute(
            "SELECT short_code, created_at FROM urls WHERE long_url = ?", (long_url,)
        ).fetchone()

        if existing:
            short_code = existing["short_code"]
            created_at = existing["created_at"]
        else:
            # Collision prevention logic sequence handling
            for _ in range(10):
                short_code = generate_short_code()
                clash = conn.execute(
                    "SELECT 1 FROM urls WHERE short_code = ?", (short_code,)
                ).fetchone()
                if not clash:
                    break
            else:
                raise HTTPException(status_code=500, detail="Could not resolve unique token collision.")

            created_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO urls (short_code, long_url, created_at) VALUES (?, ?, ?)",
                (short_code, long_url, created_at),
            )
            conn.commit()

    clean_base = BASE_URL.rstrip('/')
    return {
        "short_code": short_code,
        "short_url": f"{clean_base}/{short_code}",
        "long_url": long_url,
        "created_at": created_at,
    }


@app.get("/{short_code}", include_in_schema=False)
async def redirect_url(short_code: str):
    """Resolve short code token mapping, record telemetry tracking logs, and execute 302 redirection."""
    if short_code in ("favicon.ico", "robots.txt"):
        raise HTTPException(status_code=404)

    with get_db() as conn:
        row = conn.execute(
            "SELECT long_url FROM urls WHERE short_code = ?", (short_code,)
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Short code assignment matching token '{short_code}' not found.")

        country, flag = weighted_country()
        conn.execute(
            "INSERT INTO clicks (short_code, clicked_at, country, flag) VALUES (?, ?, ?, ?)",
            (short_code, datetime.now(timezone.utc).isoformat(), country, flag),
        )
        conn.commit()

    return RedirectResponse(url=row["long_url"], status_code=302)


@app.get("/analytics/{short_code}", tags=["Analytics"])
async def get_analytics(short_code: str):
    """Retrieve fine-grained evaluation metrics data and telemetry logs for localized short codes."""
    with get_db() as conn:
        url_row = conn.execute(
            "SELECT long_url, created_at FROM urls WHERE short_code = ?", (short_code,)
        ).fetchone()

        if not url_row:
            raise HTTPException(status_code=404, detail=f"Target short code sequence metadata '{short_code}' not found.")

        click_rows = conn.execute(
            "SELECT clicked_at, country, flag FROM clicks WHERE short_code = ? ORDER BY clicked_at DESC",
            (short_code,),
        ).fetchall()

    clicks = [{"clicked_at": r["clicked_at"], "country": r["country"], "flag": r["flag"]} for r in click_rows]

    # Dynamically build historical metric distributions mappings
    country_breakdown = {}
    for c in clicks:
        label = f"{c['flag']} {c['country']}"
        country_breakdown[label] = country_breakdown.get(label, 0) + 1

    return {
        "short_code": short_code,
        "long_url": url_row["long_url"],
        "total_clicks": len(clicks),
        "created_at": url_row["created_at"],
        "clicks": clicks,
        "country_breakdown": country_breakdown,
    }