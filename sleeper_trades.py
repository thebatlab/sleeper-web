# sleeper_trades.py
import asyncio
import httpx
import json
import os
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from async_lru import alru_cache

LOG = logging.getLogger("sleeper_trades")
LOG.setLevel(logging.INFO)

BASE_URL = "https://api.sleeper.app/v1"
CACHE_DIR = Path(os.getenv("SLEEPER_CACHE_DIR", ".sleeper_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
PLAYERS_FILE = CACHE_DIR / "players_nfl.json"

# limit concurrency so we don't hammer the public API
MAX_CONCURRENT = int(os.getenv("SLEEPER_MAX_CONCURRENT", "8"))
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


async def _safe_get(client: httpx.AsyncClient, url: str) -> Optional[Any]:
    """GET a URL and return parsed JSON or None on error."""
    try:
        async with _semaphore:
            resp = await client.get(url, timeout=20.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        LOG.warning("HTTP error for %s: %s", url, e)
    except Exception as e:
        LOG.warning("Error fetching %s: %s", url, e)
    return None


def _iso_from_maybe_ts(val: Any) -> Optional[str]:
    """Convert various date representations to ISO strings (UTC)."""
    if val is None:
        return None
    # numeric epoch ms
    try:
        if isinstance(val, (int, float)):
            # Sleeper often uses milliseconds
            t = float(val) / 1000.0
            return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
        s = str(val)
        # try parse int-like strings
        if s.isdigit():
            t = float(int(s)) / 1000.0
            return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
        # try ISO-like strings
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            # fallback: return original string
            return s
    except Exception:
        return str(val)


@alru_cache(maxsize=1, ttl=86400)
async def get_players() -> Dict[str, Any]:
    """
    Return the players dict (player_id -> player info).
    Uses file cache at .sleeper_cache/players_nfl.json if present; otherwise fetches.
    Cached in-memory for 24h via alru_cache.
    """
    # if file exists, load and return
    if PLAYERS_FILE.exists():
        try:
            with PLAYERS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            LOG.warning("Failed to read players cache: %s", e)

    # otherwise fetch and save
    async with httpx.AsyncClient() as client:
        url = f"{BASE_URL}/players/nfl"
        data = await _safe_get(client, url)
        if not data:
            LOG.warning("Could not fetch players from Sleeper; returning empty dict.")
            return {}
        try:
            with PLAYERS_FILE.open("w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            LOG.warning("Failed to write players cache: %s", e)
        return data


@alru_cache(maxsize=1024, ttl=3600)
async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Return the user object for a username (cached 1 hour)."""
    async with httpx.AsyncClient() as client:
        return await _safe_get(client, f"{BASE_URL}/user/{username}")


async def get_current_season() -> int:
    """Return the current NFL season (tries /state/nfl)."""
    async with httpx.AsyncClient() as client:
        state = await _safe_get(client, f"{BASE_URL}/state/nfl")
        if state:
            # try a few keys
            for k in ("league_season", "season", "year"):
                if k in state:
                    try:
                        return int(state[k])
                    except Exception:
                        pass
    # fallback to current year
    return datetime.utcnow().year


async def _fetch_round_transactions(client: httpx.AsyncClient, league_id: str, r: int) -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/league/{league_id}/transactions/{r}"
    data = await _safe_get(client, url)
    return data or []


async def _fetch_league_transactions(client: httpx.AsyncClient, league_id: str, rounds: Tuple[int, ...]) -> List[Dict[str, Any]]:
    # fetch all requested rounds concurrently
    if not rounds:
        rounds = tuple(range(1, 19))
    tasks = [_fetch_round_transactions(client, league_id, r) for r in rounds]
    results = await asyncio.gather(*tasks)
    # flatten and deduplicate by transaction_id (if present)
    seen = set()
    out = []
    for lst in results:
        for tx in lst:
            tid = tx.get("transaction_id") or json.dumps(tx, sort_keys=True)
            if tid in seen:
                continue
            seen.add(tid)
            out.append(tx)
    return out


async def _fetch_rosters(client: httpx.AsyncClient, league_id: str) -> List[Dict[str, Any]]:
    data = await _safe_get(client, f"{BASE_URL}/league/{league_id}/rosters")
    return data or []


async def _fetch_users(client: httpx.AsyncClient, league_id: str) -> List[Dict[str, Any]]:
    data = await _safe_get(client, f"{BASE_URL}/league/{league_id}/users")
    return data or []


async def _fetch_traded_picks(client: httpx.AsyncClient, league_id: str) -> List[Dict[str, Any]]:
    data = await _safe_get(client, f"{BASE_URL}/league/{league_id}/traded_picks")
    return data or []


def _str(x) -> str:
    return "" if x is None else str(x)


def _roster_ids_for_user(rosters: List[Dict[str, Any]], user_id: str) -> List[str]:
    """Return roster_id strings that belong to given user_id (best-effort)."""
    user_id_s = _str(user_id)
    out = []
    for r in rosters:
        owner = _str(r.get("owner_id") or r.get("user_id") or r.get("user"))
        rid = _str(r.get("roster_id") or r.get("roster"))
        # some APIs use integers, strings, or nested metadata; be flexible
        if owner and owner == user_id_s:
            out.append(rid)
    return out


def _resolve_player_name(pid: str, players: Dict[str, Any]) -> str:
    if not pid:
        return "Unknown"
    p = players.get(pid)
    if p and isinstance(p, dict):
        name = p.get("full_name") or (p.get("first_name", "") + " " + p.get("last_name", ""))
        pos = p.get("position") or ""
        team = p.get("team") or ""
        parts = [name.strip()]
        if pos or team:
            parts.append(f"({pos} {team})".strip())
        return " ".join([part for part in parts if part])
    # fallback: return id
    return pid


def _is_roster_match(value: Any, roster_ids: List[str]) -> bool:
    """Return True if value (owner_id, roster_id, etc.) matches any of roster_ids."""
    if value is None:
        return False
    s = _str(value)
    return s in roster_ids


def _parse_transaction_for_user(tx: Dict[str, Any], user_roster_ids: List[str], players: Dict[str, Any]) -> Dict[str, Any]:
    """
    Best-effort parse to determine assets_gained and assets_lost for user's roster(s).
    Returns dict: {'assets_gained': [...], 'assets_lost': [...]} (string descriptions).
    """
    gained = []
    lost = []

    # Handle explicit traded_picks inside transaction if present
    for field in ("traded_picks", "draft_picks", "picks", "traded_pick"):
        tp = tx.get(field)
        if not tp:
            continue
        # tp might be list or dict
        picks = tp if isinstance(tp, list) else [tp]
        for pick in picks:
            # pick likely contains owner_id / previous_owner_id and season/round
            owner = _str(pick.get("owner_id") or pick.get("owner"))
            prev = _str(pick.get("previous_owner_id") or pick.get("previous_owner"))
            desc = f"{pick.get('season', '')} R{pick.get('round', '?')} pick".strip()
            if owner and owner in user_roster_ids:
                gained.append(desc)
            if prev and prev in user_roster_ids:
                lost.append(desc)

    # Normal player adds/drops mapping (player_id -> roster_id)
    adds = tx.get("adds") or {}
    drops = tx.get("drops") or {}
    # These dicts sometimes map player_id -> roster_id (string/int)
    if isinstance(adds, dict):
        for pid, rid in adds.items():
            if rid is None:
                continue
            if _str(rid) in user_roster_ids:
                gained.append(_resolve_player_name(pid, players))
    if isinstance(drops, dict):
        for pid, rid in drops.items():
            if rid is None:
                continue
            # Drops are often the roster_id that lost the player; if user's roster lost it, it's in lost
            if _str(rid) in user_roster_ids:
                lost.append(_resolve_player_name(pid, players))

    # Some trades list 'players' and a 'roster_ids' list but don't annotate who got what.
    # Best-effort: if user's roster_id appears in roster_ids, include all players as gained for that roster.
    if 'players' in tx and isinstance(tx.get('players'), list):
        tx_roster_ids = [ _str(x) for x in (tx.get('roster_ids') or []) ]
        for rid in user_roster_ids:
            if rid in tx_roster_ids:
                # add players but avoid duplicates
                for pid in tx.get('players', []):
                    name = _resolve_player_name(pid, players) if str(pid).isdigit() else str(pid)
                    if name not in gained:
                        gained.append(name)

    # Final normalization: unique
    gained = list(dict.fromkeys(gained))
    lost = list(dict.fromkeys(lost))

    return {"assets_gained": gained, "assets_lost": lost, "raw": tx}


@alru_cache(maxsize=128, ttl=60)
async def gather_trades(username: str, season: Optional[int] = None, rounds: Optional[Tuple[int, ...]] = None) -> List[Dict[str, Any]]:
    """
    Async, cached aggregator. Returns list of trades (newest-first).
    rounds must be a tuple of ints (hashable); if None defaults to 1..18.
    """
    if season is None:
        season = await get_current_season()

    players = await get_players()

    user = await get_user_by_username(username)
    if not user or "user_id" not in user:
        raise ValueError(f"user '{username}' not found")

    user_id = _str(user["user_id"])

    async with httpx.AsyncClient() as client:
        # fetch leagues for user
        leagues = await _safe_get(client, f"{BASE_URL}/user/{user_id}/leagues/nfl/{season}") or []

        # process each league concurrently
        async def _process_league(league: Dict[str, Any]) -> List[Dict[str, Any]]:
            lid = _str(league.get("league_id"))
            league_name = league.get("name") or lid

            # concurrently fetch transactions (multiple rounds), rosters, users, traded_picks
            txs_task = _fetch_league_transactions(client, lid, rounds or tuple(range(1, 19)))
            rosters_task = _fetch_rosters(client, lid)
            users_task = _fetch_users(client, lid)
            picks_task = _fetch_traded_picks(client, lid)
            txs, rosters, users, picks = await asyncio.gather(txs_task, rosters_task, users_task, picks_task)

            rosters = rosters or []
            users = users or []
            txs = txs or []
            picks = picks or []

            # list roster ids belonging to user in this league
            user_roster_ids = _roster_ids_for_user(rosters, user_id)

            out = []
            # parse transactions
            for tx in txs:
                # skip non-trades (we already filtered by type when fetching), but be safe
                if tx.get("type") not in (None, "trade", "trade_proposal", "trade_transaction"):
                    # skip other types (waiver etc.)
                    continue

                # detect involvement: roster_ids or traded_picks or user_id inside raw JSON
                tx_roster_ids = [ _str(x) for x in (tx.get("roster_ids") or []) ]
                involved = bool(set(user_roster_ids).intersection(set(tx_roster_ids)))
                # fallback: check if user_id appears anywhere in JSON
                if not involved:
                    raw_str = json.dumps(tx)
                    if user_id in raw_str:
                        involved = True

                if not involved:
                    continue

                parsed = _parse_transaction_for_user(tx, user_roster_ids, players)
                # only include if there is some asset change
                if parsed.get("assets_gained") or parsed.get("assets_lost"):
                    entry = {
                        "league_id": lid,
                        "league_name": league_name,
                        "transaction_id": tx.get("transaction_id"),
                        "date": _iso_from_maybe_ts(tx.get("status_updated") or tx.get("created") or tx.get("updated_at")),
                        "assets_gained": parsed.get("assets_gained", []),
                        "assets_lost": parsed.get("assets_lost", []),
                        "raw": parsed.get("raw"),
                    }
                    out.append(entry)

            # parse traded_picks endpoint entries (these are separate)
            for pick in picks:
                # owner_id and previous_owner_id could be roster_ids or user_ids; try both:
                owner = _str(pick.get("owner_id") or pick.get("owner"))
                prev = _str(pick.get("previous_owner_id") or pick.get("previous_owner"))
                # build description
                season_p = pick.get("season") or pick.get("draft_season") or season
                rnd = pick.get("round") or pick.get("draft_round") or pick.get("round_number")
                desc = f"{season_p} R{rnd} pick".strip()
                date = _iso_from_maybe_ts(pick.get("updated_at") or pick.get("created") or pick.get("draft_id"))

                if owner and (owner in user_roster_ids or owner == user_id):
                    out.append({
                        "league_id": lid,
                        "league_name": league_name,
                        "transaction_id": None,
                        "date": date,
                        "assets_gained": [desc],
                        "assets_lost": [],
                        "raw": pick
                    })
                if prev and (prev in user_roster_ids or prev == user_id):
                    out.append({
                        "league_id": lid,
                        "league_name": league_name,
                        "transaction_id": None,
                        "date": date,
                        "assets_gained": [],
                        "assets_lost": [desc],
                        "raw": pick
                    })

            return out

        # gather all leagues
        tasks = [_process_league(league) for league in leagues]
        results = await asyncio.gather(*tasks)

    # flatten results and sort newest-first
    all_trades = [t for chunk in results for t in chunk]
    all_trades.sort(key=lambda x: x.get("date") or "", reverse=True)
    return all_trades


def trades_for_user(username: str, season: Optional[int] = None, rounds: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """
    Sync wrapper for scripts / CLI. rounds can be a list of ints.
    """
    rounds_tuple = tuple(rounds) if rounds else None
    return asyncio.run(gather_trades(username, season=season, rounds=rounds_tuple))
