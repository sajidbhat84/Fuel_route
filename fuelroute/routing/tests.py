import random
from unittest import mock

import numpy as np
from django.test import SimpleTestCase

from .services import planner
from .services.geo import cumulative_miles
from .services.locations import LocationError, resolve
from .services.planner import Node, NoFuelInRangeError, optimise

# A realistic I-80-ish corridor through real cities, used as a stand-in for the OSRM response.
WAYPOINTS = [(41.88, -87.63), (41.59, -93.62), (41.26, -95.93), (41.12, -100.77), (41.14, -104.82),
             (41.59, -109.20), (40.76, -111.89), (40.83, -115.76), (39.53, -119.81), (38.58, -121.49)]


def fake_route(start=None, finish=None, waypoints=WAYPOINTS):
    pts = []
    for (a, b), (c, d) in zip(waypoints, waypoints[1:]):
        n = max(2, int(np.hypot(c - a, d - b) * 69))  # ~1 vertex per mile
        pts += [(b + (d - b) * t, a + (c - a) * t) for t in np.linspace(0, 1, n, endpoint=False)]
    pts.append((waypoints[-1][1], waypoints[-1][0]))
    cum = cumulative_miles([p[1] for p in pts], [p[0] for p in pts])
    return {"coords": [list(p) for p in pts], "miles": float(cum[-1]), "duration_s": float(cum[-1]) / 62 * 3600}


def brute_force_cost(cands, total, start_fuel, cap):
    """Independent DP over integer miles: min cost to finish, buying fuel in 1-mile units."""
    INF = float("inf")
    pts = [(0, None)] + [(int(n.mile), n.price) for n in cands] + [(total, None)]
    states = {start_fuel: 0.0}
    for idx in range(len(pts) - 1):
        pos, price = pts[idx]
        gap = pts[idx + 1][0] - pos
        nxt_states = {}
        for f, c in states.items():
            buys = range(0, cap - f + 1) if price is not None else [0]
            for b in buys:
                f2 = f + b - gap
                if f2 < 0:
                    continue
                c2 = c + b * (price or 0)
                if c2 < nxt_states.get(f2, INF):
                    nxt_states[f2] = c2
        states = nxt_states
    return min(states.values()) if states else INF


class OptimiserTests(SimpleTestCase):
    def test_matches_bruteforce_on_random_instances(self):
        rng = random.Random(7)
        checked = 0
        for _ in range(300):
            cap, total = 40, rng.randint(50, 160)
            miles = sorted(rng.sample(range(1, total), rng.randint(3, 9)))
            cands = [Node(float(m), rng.randint(250, 450) / 100, i) for i, m in enumerate(miles)]
            start = rng.choice([cap, cap // 2])
            expected = brute_force_cost(cands, total, start, cap)
            if expected == float("inf"):
                with self.assertRaises(NoFuelInRangeError):
                    optimise(cands, total, start, cap)
                continue
            got = sum(b * n.price for n, b, _ in optimise(cands, total, start, cap))
            self.assertAlmostEqual(got, expected, places=6, msg=f"{miles} total={total} start={start}")
            checked += 1
        self.assertGreater(checked, 150)

    def test_short_trip_needs_no_stop(self):
        self.assertEqual(optimise([Node(100, 3.0, 0)], 300, 500, 500), [])

    def test_gap_bigger_than_range_raises(self):
        with self.assertRaises(NoFuelInRangeError):
            optimise([Node(100, 3.0, 0)], 900, 500, 500)


class LocationTests(SimpleTestCase):
    def test_city_state_and_coordinates(self):
        lat, lon, src = resolve("Chicago, IL")
        self.assertEqual(src, "city-index")
        self.assertAlmostEqual(lat, 41.85, delta=0.3)
        self.assertEqual(resolve("Los Angeles, California")[2], "city-index")
        self.assertEqual(resolve("34.05,-118.24")[2], "coordinates")
        self.assertEqual(resolve({"lat": 40.7, "lon": -74.0})[2], "coordinates")

    def test_common_big_cities_need_no_external_geocoder(self):
        for text in ["New York, NY", "New York, New York", "NYC, NY", "Houston, TX", "Philadelphia, PA",
                     "Boston, MA", "Miami, FL", "Seattle, WA", "Washington, DC", "Washington DC",
                     "Las Vegas, NV", "St. Louis, MO", "San Francisco, CA", "Denver, CO"]:
            with self.subTest(text=text):
                self.assertEqual(resolve(text)[2], "city-index")

    def test_outside_usa_rejected(self):
        with self.assertRaises(LocationError):
            resolve("51.5,-0.12")  # London


@mock.patch("routing.services.osrm.fetch_route", side_effect=lambda s, f: fake_route())
class ApiTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def get(self, **params):
        return self.client.get("/api/route/", params)

    def test_long_trip_response_is_consistent(self, fetch):
        r = self.get(start="Chicago, IL", finish="Sacramento, CA")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertGreater(d["summary"]["distance_miles"], 1800)
        stops = d["fuel_stops"]
        self.assertGreaterEqual(len(stops), 3)  # >1800 mi on a 500 mi tank
        self.assertAlmostEqual(sum(s["cost"] for s in stops), d["summary"]["total_fuel_cost"], delta=0.05)
        # gallons bought + starting tank must cover the whole trip (10 mpg)
        self.assertGreaterEqual(d["summary"]["total_gallons_purchased"] + 50, d["summary"]["trip_gallons_required"] - 0.01)
        # never buy more than fits in the tank, never run dry between stops
        prev_mile, prev_after = 0.0, 50.0
        for s in stops:
            self.assertLessEqual(s["tank_after_gallons"], 50.0001)
            self.assertGreaterEqual(prev_after - (s["route_mile"] - prev_mile) / 10, -0.05)
            prev_mile, prev_after = s["route_mile"], s["tank_after_gallons"]
        self.assertGreaterEqual(prev_after - (d["summary"]["distance_miles"] - prev_mile) / 10, -0.05)
        self.assertEqual(d["meta"]["routing_api_calls"], 1)
        self.assertEqual(d["meta"]["geocoding_api_calls"], 0)
        kinds = [f["properties"]["kind"] for f in d["route"]["features"]]
        self.assertEqual(kinds.count("fuel_stop"), len(stops))

    def test_second_identical_request_uses_cache(self, fetch):
        self.get(start="Chicago, IL", finish="Sacramento, CA")
        d = self.get(start="Chicago, IL", finish="Sacramento, CA").json()
        self.assertEqual(d["meta"]["routing_api_calls"], 0)
        self.assertEqual(fetch.call_count, 1)

    def test_post_json_body(self, fetch):
        r = self.client.post("/api/route/", {"start": "Chicago, IL", "finish": "Sacramento, CA"}, content_type="application/json")
        self.assertEqual(r.status_code, 200)

    def test_short_trip_no_stops(self, fetch):
        fetch.side_effect = lambda s, f: fake_route(waypoints=WAYPOINTS[:2])
        d = self.get(start="Chicago, IL", finish="Des Moines, IA").json()
        self.assertEqual(d["fuel_stops"], [])
        self.assertEqual(d["summary"]["total_fuel_cost"], 0)

    def test_empty_start_tank_forces_purchase(self, fetch):
        fetch.side_effect = lambda s, f: fake_route(waypoints=WAYPOINTS[:2])
        d = self.get(start="Chicago, IL", finish="Des Moines, IA", starting_fuel_percent="10").json()
        self.assertGreaterEqual(len(d["fuel_stops"]), 1)

    def test_validation_errors(self, fetch):
        self.assertEqual(self.get(start="Chicago, IL").status_code, 400)
        self.assertEqual(self.get(start="Chicago, IL", finish="Denver, CO", starting_fuel_percent="150").status_code, 400)
        r = self.get(start="Chicago, IL", finish="51.5,-0.12")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "location_not_found")
        self.assertEqual(self.client.delete("/api/route/").status_code, 405)

    def test_upstream_failure_maps_to_502(self, fetch):
        from .services.osrm import RoutingError
        fetch.side_effect = RoutingError("boom")
        self.assertEqual(self.get(start="Chicago, IL", finish="Denver, CO").status_code, 502)
