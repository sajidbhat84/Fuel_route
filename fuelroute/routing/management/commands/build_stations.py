"""One-time data prep (NOT run per request).

The supplied CSV has no coordinates. This command:
  1. drops non-US rows (the file includes Canadian provinces),
  2. collapses duplicate OPIS IDs (same stop listed under several names/prices) to one
     row using the LOWEST listed price,
  3. attaches lat/lon offline from GeoNames (city+state) with a ZIP-code fallback,
  4. writes routing/data/stations.csv and routing/data/places.csv (city index used to
     resolve "City, ST" inputs without calling any external geocoder).

Requires requirements-dev.txt.  Usage:  python manage.py build_stations
"""
import re
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand

DATA = Path(__file__).resolve().parents[2] / "data"
US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


def norm(s: str) -> str:
    s = str(s).lower().strip()
    s = re.sub(r"^(saint|st\.?)\s", "st ", s)
    s = re.sub(r"^(mount|mt\.?)\s", "mt ", s)
    s = re.sub(r"^(fort|ft\.?)\s", "ft ", s)
    s = re.sub(r"\bmc\s+", "mc", s)
    return re.sub(r"[^a-z0-9 ]", "", s)


class Command(BaseCommand):
    help = "Build stations.csv / places.csv (with coordinates) from the raw fuel price CSV."

    def handle(self, *args, **opts):
        import geonamescache
        import zipcodes

        raw = pd.read_csv(DATA / "fuel-prices-raw.csv")
        for c in ("Truckstop Name", "Address", "City", "State"):
            raw[c] = raw[c].astype(str).str.strip()
        n_raw = len(raw)
        raw = raw[raw["State"].isin(US_STATES)]
        n_us = len(raw)

        # one row per OPIS id, lowest listed price wins
        raw = raw.sort_values("Retail Price").drop_duplicates("OPIS Truckstop ID", keep="first")

        # ---- offline coordinate indexes -------------------------------------------------
        gc = geonamescache.GeonamesCache(min_city_population=500)
        places = {}
        for c in gc.get_cities().values():
            if c["countrycode"] != "US" or c["admin1code"] not in US_STATES:
                continue
            k = (norm(c["name"]), c["admin1code"])
            if k not in places or c["population"] > places[k]["population"]:
                places[k] = c
        alt = {}
        for c in gc.get_cities().values():
            if c["countrycode"] == "US" and c["admin1code"] in US_STATES:
                for a in c.get("alternatenames", []):
                    alt.setdefault((norm(a), c["admin1code"]), c)

        zip_idx = {}
        for z in zipcodes.list_all():
            if not z.get("lat") or z["state"] not in US_STATES:
                continue
            for name in [z["city"], *z.get("acceptable_cities", [])]:
                zip_idx.setdefault((norm(name), z["state"]), []).append((float(z["lat"]), float(z["long"])))

        def locate(city, state):
            k = (norm(city), state)
            if k in places:
                return places[k]["latitude"], places[k]["longitude"], "city"
            if k in zip_idx:
                pts = zip_idx[k]
                return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), "zip"
            if k in alt:
                return alt[k]["latitude"], alt[k]["longitude"], "alt"
            return None, None, None

        rows, missing = [], []
        for r in raw.itertuples(index=False):
            lat, lon, q = locate(r.City, r.State)
            if lat is None:
                missing.append((r.City, r.State))
                continue
            rows.append(
                dict(
                    id=r[0], name=r[1], address=r[2], city=r.City, state=r.State,
                    price=round(float(r[6]), 4), lat=round(lat, 5), lon=round(lon, 5), geo_quality=q,
                )
            )
        out = pd.DataFrame(rows)
        out.to_csv(DATA / "stations.csv", index=False)

        pl = pd.DataFrame(
            [
                dict(name=c["name"], state=c["admin1code"], lat=c["latitude"], lon=c["longitude"], pop=c["population"])
                for c in places.values()
            ]
        )
        pl.to_csv(DATA / "places.csv", index=False)

        self.stdout.write(
            f"raw rows {n_raw} -> US rows {n_us} -> unique stations {len(raw)} -> "
            f"geocoded {len(out)} ({len(missing)} dropped, no city match)\n"
            f"quality: {out.geo_quality.value_counts().to_dict()}\nplaces index: {len(pl)} cities"
        )
