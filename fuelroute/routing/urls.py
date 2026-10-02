from django.urls import path

from . import views

urlpatterns = [
    path("api/route/", views.route_api, name="route-api"),
    path("map/", views.map_page, name="route-map"),
    path("", views.index, name="index"),
]
