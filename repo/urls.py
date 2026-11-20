from django.urls import path

from . import views

app_name = "repo"

urlpatterns = [
    # For now the home page is the Materials page. A home page of its own changes the first line.
    path("", views.materials, name="home"),
    path("materials/", views.materials, name="materials"),
    # The Materials page's downloads. They take its search, e.g. materials.csv?phase=NF.
    path("materials/materials.csv", views.materials_csv, name="materials_csv"),
    path("materials/sequences.csv", views.sequences_csv, name="sequences_csv"),
    path("materials/values.csv", views.values_csv, name="values_csv"),
    path("material/<int:pk>/", views.material_detail, name="material"),
    path("material/<int:pk>/structure.svg", views.structure, name="structure"),
    path("references/", views.references, name="references"),
    path("reference/<int:pk>/", views.reference_detail, name="reference"),
    path("properties/", views.properties, name="properties"),
    # A property's code, e.g. ps: <slug:...> takes only letters, digits, - and _.
    path("property/<slug:code>/", views.property_detail, name="property"),
    path("property/<slug:code>/values.csv", views.property_values_csv, name="property_values_csv"),
    # Curators only.
    path("add/", views.add_data, name="add"),
    path("add/reference/<int:pk>/", views.add_from_reference, name="add_from_reference"),
    path("add/reference/<int:pk>/compound/", views.add_compound, name="add_compound"),
    path("add/reference/<int:pk>/value/", views.add_value, name="add_value"),
    path("add/reference/<int:pk>/curve/", views.add_curve, name="add_curve"),
    path("add/reference/<int:pk>/mixture/", views.add_mixture, name="add_mixture"),
    # The checks as the curator types: each reads one part of a form and sends back HTML (POST only).
    # A card's take its index from 0 after the address, e.g. material/?card=2.
    path("add/reference/<int:pk>/check/material/", views.check_card_material, name="check_card_material"),
    path("add/reference/<int:pk>/check/runs/", views.check_card_runs, name="check_card_runs"),
    path("add/reference/<int:pk>/check/table/", views.check_table, name="check_table"),
    # Make cards from the compound table (cards.js), after the page's cards, e.g. cards/?start=3.
    path("add/reference/<int:pk>/cards/", views.make_cards, name="make_cards"),
    # A card's block of values, by the card's index and its own, e.g. block/?card=2&block=0: what
    # it reads as, and the boxes its choice needs.
    path("add/reference/<int:pk>/check/block/", views.check_block, name="check_block"),
    path("add/reference/<int:pk>/check/block/boxes/", views.check_block_boxes, name="check_block_boxes"),
]
