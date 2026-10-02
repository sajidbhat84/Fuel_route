# Fuel Route Planner API (Django 6.1)

Given a start and finish inside the USA, returns the driving route (GeoJSON + an HTML map), the
cost-optimal fuel stops along it, and the total fuel cost for a 500-mile-range, 10 mpg vehicle.

## Run it
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py runserver
```
* API:  `GET/POST http://localhost:8000/api/route/`
* Map UI: `http://localhost:8000/` (or the `map_url` returned by the API)
* Tests: `python manage.py test routing`
* Postman: import `postman/fuel-route-planner.postman_collection.json`

## API
`GET /api/route/?start=New York, NY&finish=Los Angeles, CA`  or  `POST` JSON `{"start": "...", "finish": "..."}`

| field | notes |
|---|---|
| `start`, `finish` | `"City, ST"`, `"City, State"`, `"lat,lon"`, `{"lat","lon"}`, or a street address |
| `starting_fuel_percent` | optional, default 100 |
| `corridor_miles` | optional, max distance a station may be from the route (default 5) |

Response: `summary` (distance, `total_fuel_cost`, gallons), `fuel_stops[]` (station, price, gallons,
cost, route mile), `route` (GeoJSON FeatureCollection: route line, start, finish, numbered stops),
`map_url`, `meta` (API-call counts and timing). Errors: 400 bad input, 404 location not found / not
in USA, 422 no station within range, 502 routing service failure.

## How it works / design choices
* **Routing API: OSRM public server (free, no key). Exactly ONE call per new route**; results are
  cached by start/finish, so repeats make zero calls. Swap `OSRM_URL` in settings to self-host.
* **Geocoding without API calls:** `"City, ST"` inputs resolve from a bundled city index. Only street
  addresses fall back to Nominatim (1 call each; reported in `meta.geocoding_api_calls`).
* **Stations:** the supplied CSV has no coordinates, so `manage.py build_stations` (one-time,
  offline) drops Canadian rows, dedupes by OPIS ID, and attaches lat/lon from GeoNames/ZIP data.
  Result is `routing/data/stations.csv` (6,622 US stations), loaded into memory once per process.
* **Speed:** stations within the corridor are found with a KD-tree over the route vertices;
  planning takes a few ms. Request time is dominated by the single OSRM call.
* **Optimisation:** the classic gas-station greedy (buy just enough to reach a cheaper station
  within range, otherwise fill up and move to the cheapest reachable one). It is provably optimal
  for this model, and `OptimiserTests` verifies it against an independent brute-force DP.

## Assumptions (worth stating in the Loom)
1. Vehicle **starts with a full tank, which is not billed**; only fuel bought at stops is charged.
   A trip under 500 mi therefore costs $0 (use `starting_fuel_percent` to change this).
2. Duplicate OPIS IDs carry several prices; the **lowest** listed price is used.
3. Canadian rows in the file are ignored.
4. Stations are located at **city/ZIP centroid**, not the exact exit, so "miles off route" is
   approximate. For production, geocode the `Address` column once (e.g. Census batch geocoder) and
   replace `stations.csv`; nothing else changes.
5. Detour fuel is not charged, and the greedy can produce small top-ups at nearby stations. Adding
   a per-stop penalty is a natural next step.
6. Not verified here: a live call to the OSRM demo server (my sandbox blocks it). Routing is covered
   by mocked tests; please run one real request before recording.

## Layout
`routing/services/` -> `locations.py` (input resolution), `osrm.py` (routing client),
`planner.py` (corridor search + optimiser), `pipeline.py` (request orchestration);
`views.py` (API + map page); `management/commands/build_stations.py` (data prep).

## Loom outline (5 min)
1 min: Postman NY->LA request, point at `summary`, `fuel_stops`, `meta`. 1 min: open `map_url`.
1 min: repeat request (cache, `routing_api_calls: 0`) and the error cases. 2 min: walk
`pipeline.py` -> `planner.py` (KD-tree, greedy) and the assumptions above.
