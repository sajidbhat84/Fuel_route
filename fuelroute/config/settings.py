"""Minimal settings: the API is stateless (no DB models), so no database work per request."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {}  # intentionally none: station data is loaded into memory at startup

# Route results are cached so repeat requests never hit the routing API again.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuelroute",
        "OPTIONS": {"MAX_ENTRIES": 500},
    }
}

# ---- Fuel planner configuration -------------------------------------------------
FUEL_PLANNER = {
    "MAX_RANGE_MILES": 500,          # full-tank range
    "MPG": 10,                        # vehicle efficiency
    "CORRIDOR_MILES": 5.0,            # how far off the route a station may be
    "ROUTE_CACHE_SECONDS": 60 * 60 * 24,
    # Free routing API: OSRM public demo server (no key). Swap for a self-hosted
    # OSRM / OpenRouteService by changing this URL.
    "OSRM_URL": os.environ.get("OSRM_URL", "https://router.project-osrm.org"),
    "OSRM_TIMEOUT_SECONDS": 15,
    # Only used when a start/finish is a street address the offline city index can't resolve.
    "NOMINATIM_URL": "https://nominatim.openstreetmap.org/search",
    "USER_AGENT": "fuelroute-assessment/1.0 (contact: bhatsajid8494@gmail.com)",
}

# Django's default ("same-origin") hides the Referer from other sites, and OpenStreetMap's
# tile servers answer 403 "Access blocked" when no Referer is sent.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"