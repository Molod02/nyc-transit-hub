"""NYC Transit Hub backend.

Polls the MTA GTFS-realtime feeds, caches the parsed result, and serves clean
JSON to the frontend:

    GET /status   -> [{"line": "A", "status": "Good Service", "message": null}, ...]
    GET /alerts   -> [{"id": ..., "routes": [...], "header": ..., "description": ...}, ...]
    GET /feeds    -> entity counts per subway feed (proves live data is flowing)
    GET /arrivals/{station_id} -> next trains at a station (live trip updates)
    GET /stations?q=...        -> search stations by name (needs static GTFS)
    GET /route?origin=...&destination=... -> lines connecting two stations
    GET /health   -> {"ok": true}

Config (environment variables):
    GTFS_DIR      folder with unzipped static GTFS (default backend/data/gtfs)
    MTA_API_KEY   optional; sent as the x-api-key header if set
    CACHE_TTL     seconds to reuse a parsed feed before re-fetching (default 30)
    ALERTS_FEED   feed path for service alerts (default camsys%2Fsubway-alerts)
"""

import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google.transit import gtfs_realtime_pb2

from routing import Index, load_or_build_index

BASE_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/"
API_KEY = os.getenv("MTA_API_KEY")
CACHE_TTL = int(os.getenv("CACHE_TTL", "30"))
ALERTS_FEED = os.getenv("ALERTS_FEED", "camsys%2Fsubway-alerts")

# Subway real-time feeds, grouped the way the MTA publishes them.
SUBWAY_FEEDS: Dict[str, str] = {
    "1-7,S": "nyct%2Fgtfs",
    "A,C,E": "nyct%2Fgtfs-ace",
    "B,D,F,M": "nyct%2Fgtfs-bdfm",
    "G": "nyct%2Fgtfs-g",
    "J,Z": "nyct%2Fgtfs-jz",
    "N,Q,R,W": "nyct%2Fgtfs-nqrw",
    "L": "nyct%2Fgtfs-l",
}

LINES: List[str] = [
    "1", "2", "3", "4", "5", "6", "7",
    "A", "C", "E", "B", "D", "F", "M",
    "G", "J", "Z", "L", "N", "Q", "R", "W", "S",
]


# ---------------------------------------------------------------- fetching

def fetch_feed(path: str) -> "gtfs_realtime_pb2.FeedMessage":
    """Download one GTFS-realtime feed and parse the protobuf."""
    headers = {"x-api-key": API_KEY} if API_KEY else {}
    resp = requests.get(BASE_URL + path, headers=headers, timeout=10)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


_cache: Dict[str, dict] = {}


def cached(name: str, loader: Callable[[], object]):
    """Return a fresh cached value, or reload it. Falls back to stale data
    if the MTA is unreachable so the demo keeps working."""
    entry = _cache.get(name)
    now = time.time()
    if entry and now - entry["ts"] < CACHE_TTL:
        return entry["value"]
    try:
        value = loader()
    except Exception:
        if entry:
            return entry["value"]
        raise
    _cache[name] = {"ts": now, "value": value}
    return value


# ----------------------------------------------------------------- parsing

def _text(translated) -> Optional[str]:
    """Pick the English string from a GTFS-rt TranslatedString."""
    if not translated.translation:
        return None
    for t in translated.translation:
        if t.language in ("en", "en-US"):
            return t.text
    return translated.translation[0].text


def _is_active(alert, now: float) -> bool:
    if not alert.active_period:
        return True
    for p in alert.active_period:
        start = p.start or 0
        end = p.end or float("inf")
        if start <= now <= end:
            return True
    return False


def _normalize_route(route_id: str) -> str:
    """Express variants (6X, 7X) map to their base line."""
    if route_id not in LINES and route_id.endswith("X") and route_id[:-1] in LINES:
        return route_id[:-1]
    return route_id


def parse_alerts(feed, now: Optional[float] = None) -> List[dict]:
    now = now if now is not None else time.time()
    alerts = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        if not _is_active(alert, now):
            continue
        routes = sorted({
            _normalize_route(ie.route_id)
            for ie in alert.informed_entity
            if ie.route_id
        })
        alerts.append({
            "id": entity.id,
            "routes": routes,
            "header": _text(alert.header_text),
            "description": _text(alert.description_text),
        })
    return alerts


def build_status(alerts: List[dict]) -> List[dict]:
    statuses = {
        line: {"line": line, "status": "Good Service", "message": None}
        for line in LINES
    }
    for alert in alerts:
        for route in alert["routes"]:
            if route in statuses and statuses[route]["message"] is None:
                statuses[route]["status"] = "Service Alert"
                statuses[route]["message"] = alert["header"]
    return [statuses[line] for line in LINES]


# --------------------------------------------------------------------- API

app = FastAPI(title="NYC Transit Hub API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])


def get_alerts() -> List[dict]:
    try:
        return cached("alerts", lambda: parse_alerts(fetch_feed(ALERTS_FEED)))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load MTA alerts: {exc}")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status():
    return build_status(get_alerts())


@app.get("/alerts")
def alerts():
    return get_alerts()


@app.get("/feeds")
def feeds():
    """Entity count and timestamp for each subway feed, to prove live data."""
    out = {}
    for name, path in SUBWAY_FEEDS.items():
        try:
            feed = cached(f"feed:{path}", lambda p=path: fetch_feed(p))
            out[name] = {
                "entities": len(feed.entity),
                "feed_timestamp": feed.header.timestamp,
            }
        except Exception as exc:
            out[name] = {"error": str(exc)}
    return out


# ------------------------------------------------------- arrivals (live)

def parse_arrivals(feed_list, station_id: str, now: Optional[float] = None,
                   limit: int = 10) -> List[dict]:
    """Next trains at a station from GTFS-rt trip updates.

    Platform stop ids are the station id plus N or S (e.g. A27 -> A27N, A27S).
    """
    now = now if now is not None else time.time()
    platforms = {station_id, station_id + "N", station_id + "S"}
    out = []
    for feed in feed_list:
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            for stu in tu.stop_time_update:
                if stu.stop_id not in platforms:
                    continue
                t = stu.arrival.time or stu.departure.time
                if not t or t < now - 30:
                    continue
                direction = {"N": "Uptown / Northbound", "S": "Downtown / Southbound"}.get(
                    stu.stop_id[-1], "")
                out.append({
                    "line": tu.trip.route_id,
                    "direction": direction,
                    "minutes": max(0, int((t - now) // 60)),
                    "arrival_time": int(t),
                })
    out.sort(key=lambda a: a["arrival_time"])
    return out[:limit]


@app.get("/arrivals/{station_id}")
def arrivals(station_id: str):
    loaded = []
    for path in SUBWAY_FEEDS.values():
        try:
            loaded.append(cached(f"feed:{path}", lambda p=path: fetch_feed(p)))
        except Exception:
            continue  # one broken feed should not hide the others
    if not loaded:
        raise HTTPException(status_code=502, detail="Could not load any MTA feeds")
    return parse_arrivals(loaded, station_id)


# ------------------------------------------------- stations and routing

DATA_DIR = Path(__file__).resolve().parent / "data"
GTFS_DIR = Path(os.getenv("GTFS_DIR", DATA_DIR / "gtfs"))
_index: Optional[Index] = None


def get_index() -> Index:
    global _index
    if _index is None:
        try:
            _index = load_or_build_index(GTFS_DIR, DATA_DIR / "index.json")
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Static GTFS data missing ({exc}). Unzip the MTA subway "
                       f"GTFS into {GTFS_DIR} (see README).",
            )
    return _index


@app.get("/stations")
def stations(q: str):
    return get_index().search(q)


@app.get("/route")
def route(origin: str, destination: str):
    index = get_index()
    try:
        o, d = index.resolve(origin), index.resolve(destination)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown station: {exc.args[0]}")
    return {
        "origin": index._describe(o),
        "destination": index._describe(d),
        "options": index.plan(o, d),
    }


# Serve the simple frontend from the same server (must come last).
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
