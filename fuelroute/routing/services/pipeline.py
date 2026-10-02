"""Orchestrates one API request: resolve locations -> (cached) route -> plan -> response body."""
import time

from django.conf import settings
from django.core.cache import cache

from . import osrm, planner
from .locations import resolve
from .stations import get_store


def _route_cached(start, finish):
    key = f"route:{start[0]:.3f},{start[1]:.3f}:{finish[0]:.3f},{finish[1]:.3f}"
    route = cache.get(key)
    if route is not None:
        return route, 0
    route = osrm.fetch_route(start, finish)  # <- the single external routing call
    cache.set(key, route, settings.FUEL_PLANNER["ROUTE_CACHE_SECONDS"])
    return route, 1


def _geojson(start, finish, plan, start_in, finish_in):
    feats = [
        {"type": "Feature", "properties": {"kind": "route"}, "geometry": {"type": "LineString", "coordinates": plan["geometry"]}},
        {"type": "Feature", "properties": {"kind": "start", "label": start_in}, "geometry": {"type": "Point", "coordinates": [start[1], start[0]]}},
        {"type": "Feature", "properties": {"kind": "finish", "label": finish_in}, "geometry": {"type": "Point", "coordinates": [finish[1], finish[0]]}},
    ]
    for s in plan["stops"]:
        st = s["station"]
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "fuel_stop", "order": s["order"], "name": st["name"],
                    "city": st["city"], "state": st["state"], "price_per_gallon": s["price_per_gallon"],
                    "gallons_purchased": s["gallons_purchased"], "cost": s["cost"], "route_mile": s["route_mile"],
                },
                "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
            }
        )
    return {"type": "FeatureCollection", "features": feats}


def get_plan(start_in, finish_in, start_fuel_pct=100.0, corridor=None):
    t0 = time.perf_counter()
    cfg = settings.FUEL_PLANNER
    corridor = corridor or cfg["CORRIDOR_MILES"]

    s_lat, s_lon, s_src = resolve(start_in)
    f_lat, f_lon, f_src = resolve(finish_in)
    route, routing_calls = _route_cached((s_lat, s_lon), (f_lat, f_lon))

    plan = planner.build_plan(
        route, get_store(),
        max_range=cfg["MAX_RANGE_MILES"], mpg=cfg["MPG"], corridor=corridor, start_fuel_pct=start_fuel_pct,
    )
    label = lambda v: v if isinstance(v, str) else f"{v.get('lat')},{v.get('lon', v.get('lng'))}"
    start_pt, finish_pt = (s_lat, s_lon), (f_lat, f_lon)

    notes = [
        "Fuel prices come from the supplied file; stations are located by city/ZIP centroid, so "
        "positions are approximate (see README).",
        f"Vehicle: {cfg['MAX_RANGE_MILES']} mile range, {cfg['MPG']} mpg. Only fuel purchased at stops is billed; "
        f"the tank at departure ({start_fuel_pct:g}% full) is treated as already paid for.",
    ]
    if not plan["stops"]:
        notes.append("The starting tank covers the whole trip, so no fuel stop is needed.")

    return {
        "start": {"input": label(start_in), "lat": s_lat, "lon": s_lon, "resolved_via": s_src},
        "finish": {"input": label(finish_in), "lat": f_lat, "lon": f_lon, "resolved_via": f_src},
        "vehicle": {
            "max_range_miles": cfg["MAX_RANGE_MILES"], "mpg": cfg["MPG"],
            "tank_gallons": cfg["MAX_RANGE_MILES"] / cfg["MPG"], "starting_fuel_percent": start_fuel_pct,
        },
        "summary": {
            "distance_miles": plan["distance_miles"],
            "drive_time_hours": round(route["duration_s"] / 3600, 2),
            "trip_gallons_required": plan["trip_gallons_required"],
            "fuel_stops": len(plan["stops"]),
            "total_gallons_purchased": plan["total_gallons_purchased"],
            "total_fuel_cost": plan["total_fuel_cost"],
        },
        "fuel_stops": plan["stops"],
        "route": _geojson(start_pt, finish_pt, plan, label(start_in), label(finish_in)),
        "notes": notes,
        "meta": {
            "routing_api_calls": routing_calls,
            "geocoding_api_calls": sum(1 for x in (s_src, f_src) if x == "nominatim"),
            "stations_considered": plan["candidate_stations_in_corridor"],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
