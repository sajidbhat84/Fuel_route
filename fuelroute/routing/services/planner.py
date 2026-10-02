"""Fuel-stop planning along a route.

Pipeline (all in-memory, a few ms after the single routing call):
  1. corridor search : KD-tree of route vertices; every station within CORRIDOR_MILES of the
                       route gets a position (miles from start) and a price.
  2. optimisation    : classic "gas-station problem" greedy, which is optimal when fuel can be
                       bought in any quantity and price is fixed per station:
                         - at a station, if a CHEAPER station is reachable on a full tank,
                           buy only enough to get there;
                         - otherwise fill the tank (unless the destination is reachable, then
                           buy only what is needed) and continue to the cheapest reachable stop.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .geo import cumulative_miles, to_xyz
from .stations import StationStore

EPS = 1e-9


class NoFuelInRangeError(RuntimeError):
    def __init__(self, at_mile: float, max_range: float):
        self.at_mile = at_mile
        super().__init__(
            f"No fuel station within {max_range:.0f} miles after mile {at_mile:.0f} of the route; "
            "the vehicle cannot complete this trip."
        )


@dataclass
class Node:
    mile: float
    price: float
    store_idx: int  # -1 = start, -2 = destination
    detour: float = 0.0


# --------------------------------------------------------------------------------------
def route_positions(coords, total_miles: float):
    """Cumulative miles at each route vertex, scaled so the last equals the API's distance."""
    arr = np.asarray(coords, dtype=float)
    lon, lat = arr[:, 0], arr[:, 1]
    cum = cumulative_miles(lat, lon)
    if cum[-1] > 0:
        cum = cum * (total_miles / cum[-1])
    return arr, cum


def find_candidates(arr: np.ndarray, cum: np.ndarray, store: StationStore, corridor: float) -> list[Node]:
    lon, lat = arr[:, 0], arr[:, 1]
    pad = corridor / 35.0  # degrees; generous bbox pre-filter (1 deg lon >= ~35 mi in the US)
    mask = (
        (store.lats >= lat.min() - pad) & (store.lats <= lat.max() + pad)
        & (store.lons >= lon.min() - pad) & (store.lons <= lon.max() + pad)
    )
    idxs = np.nonzero(mask)[0]
    if idxs.size == 0:
        return []
    tree = cKDTree(to_xyz(lat, lon))
    dist, vert = tree.query(store.xyz[idxs], distance_upper_bound=corridor)
    ok = np.isfinite(dist)
    out = [
        Node(mile=float(cum[v]), price=float(store.prices[i]), store_idx=int(i), detour=float(d))
        for i, v, d in zip(idxs[ok], vert[ok], dist[ok])
    ]
    out.sort(key=lambda n: (n.mile, n.price))
    return out


def optimise(cands: list[Node], total_miles: float, start_fuel_miles: float, max_range: float):
    """Return list of (Node, miles_bought, range_before_purchase_miles)."""
    nodes = [Node(0.0, 0.0, -1)] + cands + [Node(total_miles, -1.0, -2)]
    miles = [n.mile for n in nodes]
    last = len(nodes) - 1
    i, fuel = 0, min(start_fuel_miles, max_range)
    purchases = []

    while i < last:
        cur = nodes[i]
        can_buy = i > 0  # the start is not a station: the initial tank is what it is
        horizon = max_range if can_buy else fuel
        end = bisect.bisect_right(miles, cur.mile + horizon + EPS)
        window = range(i + 1, min(end, last + 1))

        nxt = next((k for k in window if nodes[k].price < cur.price), None)
        bought = 0.0
        arrival_range_before = fuel
        if nxt is not None:
            need = (nodes[nxt].mile - cur.mile) - fuel
            if need > EPS and can_buy:
                bought = need
        else:
            stations = [k for k in window if k != last]
            if not stations:
                raise NoFuelInRangeError(cur.mile, max_range)
            best = min(stations, key=lambda k: (nodes[k].price, -nodes[k].mile))
            nxt = best
            if can_buy:
                bought = max_range - fuel

        if bought > EPS:
            purchases.append((cur, bought, fuel))
            fuel += bought
        fuel -= nodes[nxt].mile - cur.mile
        i = nxt
    return purchases


# --------------------------------------------------------------------------------------
def simplify(coords, tolerance_deg: float = 0.0008):
    """Iterative Ramer-Douglas-Peucker for the OUTPUT geometry only (planning uses full res)."""
    pts = np.asarray(coords, dtype=float)
    n = len(pts)
    if n < 3:
        return pts.tolist()
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = pts[b] - pts[a]
        rel = pts[a + 1 : b] - pts[a]
        norm = np.hypot(*seg)
        d = np.hypot(*rel.T) if norm == 0 else np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm
        m = int(np.argmax(d))
        if d[m] > tolerance_deg:
            k = a + 1 + m
            keep[k] = True
            stack += [(a, k), (k, b)]
    return pts[keep].round(5).tolist()


def build_plan(
    route: dict,
    store: StationStore,
    *,
    max_range: float,
    mpg: float,
    corridor: float,
    start_fuel_pct: float = 100.0,
):
    arr, cum = route_positions(route["coords"], route["miles"])
    total = float(route["miles"])
    cands = find_candidates(arr, cum, store, corridor)
    purchases = optimise(cands, total, max_range * start_fuel_pct / 100.0, max_range)

    stops, total_cost, total_gal = [], 0.0, 0.0
    for order, (node, bought_miles, range_before) in enumerate(purchases, 1):
        s = node.store_idx
        gallons = bought_miles / mpg
        cost = gallons * node.price
        total_cost += cost
        total_gal += gallons
        stops.append(
            {
                "order": order,
                "station": {
                    "id": int(store.ids[s]),
                    "name": store.names[s],
                    "address": store.addresses[s],
                    "city": store.cities[s],
                    "state": store.states[s],
                    "lat": float(store.lats[s]),
                    "lon": float(store.lons[s]),
                },
                "route_mile": round(node.mile, 1),
                "miles_off_route": round(node.detour, 2),
                "price_per_gallon": round(node.price, 3),
                "gallons_purchased": round(gallons, 2),
                "cost": round(cost, 2),
                "tank_before_gallons": round(range_before / mpg, 2),
                "tank_after_gallons": round((range_before + bought_miles) / mpg, 2),
            }
        )
    return {
        "distance_miles": round(total, 1),
        "trip_gallons_required": round(total / mpg, 2),
        "stops": stops,
        "total_gallons_purchased": round(total_gal, 2),
        "total_fuel_cost": round(total_cost, 2),
        "candidate_stations_in_corridor": len(cands),
        "geometry": simplify(route["coords"]),
    }
