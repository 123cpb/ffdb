from django.urls import path

from . import views

app_name = "repo"

urlpatterns = [
    path("material/<int:pk>/", views.material_detail, name="material"),
    path("material/<int:pk>/structure.svg", views.structure, name="structure"),
]
