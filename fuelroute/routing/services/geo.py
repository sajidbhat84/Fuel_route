"""Small geometry helpers shared by the services."""
import numpy as np

EARTH_RADIUS_MILES = 3958.7613


def to_xyz(lat, lon):
    """Lat/lon degrees -> 3D points (miles) on a sphere. Euclidean (chord) distance between
    these points ~= great-circle distance for the short distances we care about, and lets us
    use a KD-tree instead of O(stations x route-points) haversine."""
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    c = np.cos(lat)
    return EARTH_RADIUS_MILES * np.stack([c * np.cos(lon), c * np.sin(lon), np.sin(lat)], axis=-1)


def cumulative_miles(lat, lon):
    """Cumulative great-circle miles along a polyline (vectorised haversine)."""
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    dlat, dlon = np.diff(lat), np.diff(lon)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    seg = 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return np.concatenate([[0.0], np.cumsum(seg)])
