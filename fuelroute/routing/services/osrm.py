"""Thin client for the free OSRM routing API. ONE call per new route."""
import requests
from django.conf import settings


class RoutingError(RuntimeError):
    pass


def fetch_route(start: tuple[float, float], finish: tuple[float, float]) -> dict:
    """start/finish are (lat, lon). Returns {'coords': [[lon, lat], ...], 'miles': float, 'duration_s': float}."""
    cfg = settings.FUEL_PLANNER
    url = f"{cfg['OSRM_URL']}/route/v1/driving/{start[1]:.6f},{start[0]:.6f};{finish[1]:.6f},{finish[0]:.6f}"
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"},
            headers={"User-Agent": cfg["USER_AGENT"]},
            timeout=cfg["OSRM_TIMEOUT_SECONDS"],
        )
        payload = resp.json()
    except ValueError as exc:  # includes requests' JSONDecodeError (must precede RequestException)
        raise RoutingError(f"Routing service returned a non-JSON response (HTTP {resp.status_code}).") from exc
    except requests.RequestException as exc:
        raise RoutingError(f"Routing service unavailable ({exc.__class__.__name__}).") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(payload.get("message") or f"No drivable route found ({payload.get('code')}).")
    route = payload["routes"][0]
    return {
        "coords": route["geometry"]["coordinates"],
        "miles": route["distance"] / 1609.344,
        "duration_s": route["duration"],
    }
