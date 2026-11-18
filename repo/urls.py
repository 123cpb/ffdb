from django.urls import path

from . import views

app_name = "repo"

urlpatterns = [
    # For now the home page is the Materials page. A home page of its own changes the first line.
    path("", views.materials, name="home"),
    path("materials/", views.materials, name="materials"),
    path("material/<int:pk>/", views.material_detail, name="material"),
    path("material/<int:pk>/structure.svg", views.structure, name="structure"),
    path("references/", views.references, name="references"),
    path("reference/<int:pk>/", views.reference_detail, name="reference"),
    path("properties/", views.properties, name="properties"),
    # A property's code, e.g. ps: <slug:...> takes only letters, digits, - and _.
    path("property/<slug:code>/", views.property_detail, name="property"),
]
