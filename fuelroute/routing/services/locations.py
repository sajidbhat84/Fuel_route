"""Turn user input into (lat, lon) inside the USA.

Resolution order (cheapest first, so most requests need ZERO geocoding calls):
  1. "lat,lon" pair or {"lat":..,"lon":..} object
  2. "City, ST" / "City, State Name" via the bundled offline city index
  3. Nominatim (free, OSM) as a last resort for street addresses - 1 call per address
"""
import csv
import re
from functools import lru_cache

import requests
from django.conf import settings

from .stations import DATA_DIR

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
STATE_CODES = set(STATE_NAMES.values())
_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$")


class LocationError(ValueError):
    pass


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"^(saint|st\.?)\s", "st ", s)
    s = re.sub(r"^(mount|mt\.?)\s", "mt ", s)
    s = re.sub(r"^(fort|ft\.?)\s", "ft ", s)
    return re.sub(r"[^a-z0-9 ]", "", s)


@lru_cache(maxsize=1)
def _places():
    idx = {}
    with open(DATA_DIR / "places.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (_norm(r["name"]), r["state"])
            pop = int(float(r["pop"] or 0))
            if k not in idx or pop > idx[k][2]:
                idx[k] = (float(r["lat"]), float(r["lon"]), pop)
    # Aliases (never override a real city): GeoNames calls it "New York City", people type
    # "New York"; also accept "NYC". Same trick covers "<name> City" -> "<name>".
    for (name, st), val in list(idx.items()):
        if name.endswith(" city") and len(name) > 6:
            idx.setdefault((name[:-5], st), val)
    if ("new york city", "NY") in {(n, s) for n, s in idx}:
        idx.setdefault(("nyc", "NY"), idx[("new york city", "NY")])
    return idx


def in_usa(lat: float, lon: float) -> bool:
    """Rough bounding boxes: contiguous US, Alaska, Hawaii."""
    return (
        (24.4 <= lat <= 49.5 and -125.0 <= lon <= -66.9)
        or (51.0 <= lat <= 71.6 and -171.0 <= lon <= -129.9)
        or (18.8 <= lat <= 22.4 and -160.6 <= lon <= -154.7)
    )


def _parse_city_state(text: str):
    parts = [p.strip() for p in re.split(r",", text) if p.strip()]
    if len(parts) >= 2:
        city, state = parts[-2], parts[-1]
        state = re.sub(r"\s*(usa|us|united states)$", "", state, flags=re.I).strip() or (parts[-2] if len(parts) > 2 else "")
        if len(parts) >= 3 and re.match(r"(?i)^(usa|us|united states)$", parts[-1]):
            city, state = parts[-3], parts[-2]
    else:
        m = re.match(r"^(.*\S)\s+([A-Za-z]{2})$", text.strip())
        if not m:
            return None
        city, state = m.group(1), m.group(2)
    state = state.strip()
    code = state.upper() if state.upper() in STATE_CODES else STATE_NAMES.get(state.lower())
    return (city, code) if code else None


def _nominatim(text: str):
    try:
        r = requests.get(
            settings.FUEL_PLANNER["NOMINATIM_URL"],
            params={"q": text, "format": "jsonv2", "countrycodes": "us", "limit": 1},
            headers={"User-Agent": settings.FUEL_PLANNER["USER_AGENT"]},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise LocationError(f"Could not geocode '{text}' (geocoder unavailable: {exc.__class__.__name__}).")
    if not data:
        raise LocationError(f"Could not find a USA location for '{text}'.")
    return float(data[0]["lat"]), float(data[0]["lon"])


def resolve(value) -> tuple[float, float, str]:
    """Return (lat, lon, source) where source is 'coordinates' | 'city-index' | 'nominatim'."""
    if isinstance(value, dict):
        try:
            lat, lon = float(value["lat"]), float(value.get("lon", value.get("lng")))
        except (KeyError, TypeError, ValueError):
            raise LocationError("Location objects need numeric 'lat' and 'lon'.")
        src = "coordinates"
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        m = _LATLON.match(text)
        if m:
            lat, lon, src = float(m.group(1)), float(m.group(2)), "coordinates"
        else:
            cs = _parse_city_state(text)
            hit = _places().get((_norm(cs[0]), cs[1])) if cs else None
            if hit:
                lat, lon, src = hit[0], hit[1], "city-index"
            else:
                lat, lon = _nominatim(text)
                src = "nominatim"
    else:
        raise LocationError("Location is required (e.g. 'Chicago, IL', a street address, or 'lat,lon').")

    if not in_usa(lat, lon):
        raise LocationError(f"Location '{value}' resolved outside the USA ({lat:.3f}, {lon:.3f}).")
    return lat, lon, src
