"""In-memory station store, loaded once per process (~6.6k rows -> a few ms per lookup)."""
import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .geo import to_xyz

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@dataclass(frozen=True)
class StationStore:
    ids: np.ndarray
    names: list
    addresses: list
    cities: list
    states: list
    prices: np.ndarray
    lats: np.ndarray
    lons: np.ndarray
    xyz: np.ndarray

    def __len__(self):
        return len(self.ids)


@lru_cache(maxsize=1)
def get_store() -> StationStore:
    with open(DATA_DIR / "stations.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    lats = np.array([float(r["lat"]) for r in rows])
    lons = np.array([float(r["lon"]) for r in rows])
    return StationStore(
        ids=np.array([int(r["id"]) for r in rows]),
        names=[r["name"] for r in rows],
        addresses=[r["address"] for r in rows],
        cities=[r["city"] for r in rows],
        states=[r["state"] for r in rows],
        prices=np.array([float(r["price"]) for r in rows]),
        lats=lats,
        lons=lons,
        xyz=to_xyz(lats, lons),
    )
