# NYC Transit Hub

Web app showing live NYC subway service status from the MTA's real-time feeds.

## Run it

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Only if the MTA requires a key for your account:
export MTA_API_KEY="your-key"    # Windows: set MTA_API_KEY=your-key

uvicorn main:app --reload
```

Open http://localhost:8000 for the status page.

| Endpoint | Returns |
|---|---|
| `GET /status` | Status for each subway line: `[{"line": "A", "status": "Good Service", "message": null}, ...]` |
| `GET /alerts` | Active service alerts and the lines they affect |
| `GET /feeds` | Entity count per subway feed (quick check that live data is flowing) |
| `GET /arrivals/{station_id}` | Next trains at a station, from live trip updates (e.g. `/arrivals/A27`) |
| `GET /stations?q=times` | Station search by name, with the lines serving each |
| `GET /route?origin=...&destination=...` | Which lines connect two stations and where to transfer (station ids or names) |
| `GET /health` | `{"ok": true}` |

## Route planning setup (one time)

`/stations` and `/route` need the MTA's static schedule data:

1. From the MTA developer data page, download the static subway GTFS zip.
2. Unzip it into `backend/data/gtfs/` (it must contain `stops.txt`, `trips.txt`, `stop_times.txt`).
3. Start the server and call `/stations?q=union`. The first call builds `backend/data/index.json`, which can take a minute. Later calls are instant.

Route planning says which lines connect two stations and where to switch, up to 2 transfers. It does not yet use travel times or stop order. That is a Week 5+ improvement.

## Run the tests (no network needed)

```bash
cd backend
python -m unittest test_routing   # route planning, no packages needed
pytest                            # API tests (needs requirements installed)
```

Database design: see `docs/schema.md`.

## Things to verify first

- Sign up at https://api.mta.info and check the current feed URLs and whether a key is required. The URLs and the alerts feed path in `backend/main.py` came from public documentation, not a live call, so confirm them first.
- If the alerts feed path is different, set `ALERTS_FEED` instead of editing code.
- `CACHE_TTL` (seconds, default 30) controls how often we hit the MTA.

## Layout

```
backend/    FastAPI app, parser, tests
frontend/   Static status page, served by the backend at /
```

## Owners and next steps

Fill in who owns what. Planned next: Firebase login, favorites, alert preferences, interactive map, accessibility (elevator/escalator) data, multilingual support.
