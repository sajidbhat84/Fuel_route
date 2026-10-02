import json
from urllib.parse import urlencode

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services.locations import LocationError
from .services.osrm import RoutingError
from .services.pipeline import get_plan
from .services.planner import NoFuelInRangeError


def _error(status, code, message, **extra):
    return JsonResponse({"error": {"code": code, "message": message, **extra}}, status=status)


def _params(request):
    """Accept JSON body (POST) or query string (GET)."""
    if request.method == "POST" and request.body:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            raise ValueError("Request body must be valid JSON.")
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data
    return request.GET.dict()


@csrf_exempt
@require_http_methods(["GET", "POST"])
def route_api(request):
    try:
        p = _params(request)
    except ValueError as exc:
        return _error(400, "invalid_request", str(exc))

    start, finish = p.get("start"), p.get("finish")
    if not start or not finish:
        return _error(400, "invalid_request", "Both 'start' and 'finish' are required.",
                      example={"start": "New York, NY", "finish": "Los Angeles, CA"})
    try:
        pct = float(p.get("starting_fuel_percent", 100))
        corridor = float(p["corridor_miles"]) if p.get("corridor_miles") not in (None, "") else None
    except (TypeError, ValueError):
        return _error(400, "invalid_request", "'starting_fuel_percent' and 'corridor_miles' must be numbers.")
    if not 0 <= pct <= 100:
        return _error(400, "invalid_request", "'starting_fuel_percent' must be between 0 and 100.")
    if corridor is not None and not 0.5 <= corridor <= 25:
        return _error(400, "invalid_request", "'corridor_miles' must be between 0.5 and 25.")

    try:
        body = get_plan(start, finish, start_fuel_pct=pct, corridor=corridor)
    except LocationError as exc:
        return _error(404, "location_not_found", str(exc))
    except RoutingError as exc:
        return _error(502, "routing_failed", str(exc))
    except NoFuelInRangeError as exc:
        return _error(422, "no_fuel_in_range", str(exc), at_mile=round(exc.at_mile, 1))

    q = urlencode({"start": body["start"]["input"], "finish": body["finish"]["input"],
                   "starting_fuel_percent": pct, **({"corridor_miles": corridor} if corridor else {})})
    body["map_url"] = request.build_absolute_uri(reverse("route-map")) + "?" + q
    return JsonResponse(body, json_dumps_params={"indent": 2} if "pretty" in request.GET else None)


def map_page(request):
    return render(request, "routing/map.html", {"start": request.GET.get("start", ""), "finish": request.GET.get("finish", "")})


def index(request):
    return render(request, "routing/map.html", {"start": "", "finish": ""})
