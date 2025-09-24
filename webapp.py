# webapp.py
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import sleeper_trades
import logging
from datetime import datetime

LOG = logging.getLogger("sleeper_web")
LOG.setLevel(logging.INFO)

app = FastAPI(title="Sleeper Trades Viewer")

# Mount static if present (avoid crash if directory missing)
if Path("static").exists() and Path("static").is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

def format_date(value):
    """
    Safely format a date for Jinja2.
    Accepts datetime objects or ISO date strings.
    Returns YYYY-MM-DD or the original value if parsing fails.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        try:
            # Parse ISO string
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return value
    return value

# Add filter to Jinja2 environment
templates.env.filters["date_ymd"] = format_date

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Show the search form (and optionally instructions)."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/trades", response_class=HTMLResponse)
async def trades(
    request: Request,
    username: str = Query(..., description="Sleeper username (required)"),
    season: int = Query(None, description="Season (e.g. 2025). Defaults to current league season."),
    rounds: str = Query(None, description="Comma-separated round numbers (e.g. 1,2,3). Defaults 1-18.")
):
    """
    Query trades for a username. Example:
    /trades?username=alice&season=2025&rounds=1,2,3
    """
    # parse rounds into tuple of ints (or None)
    round_list = None
    if rounds:
        try:
            round_list = tuple(int(r.strip()) for r in rounds.split(",") if r.strip().isdigit())
        except Exception:
            round_list = None

    # determine season if missing
    if season is None:
        try:
            season = await sleeper_trades.get_current_season()
        except Exception:
            season = None

    context = {"request": request, "username": username, "season": season, "rounds": rounds}

    try:
        # gather_trades expects rounds as a tuple (or None)
        trades = await sleeper_trades.gather_trades(username, season=season, rounds=round_list)
        context["trades"] = trades
        context["error"] = None
    except ValueError as ve:
        context["trades"] = []
        context["error"] = str(ve)
    except Exception as e:
        LOG.exception("Error fetching trades")
        context["trades"] = []
        context["error"] = "An error occurred while fetching trades. See logs."

    return templates.TemplateResponse("index.html", context)
