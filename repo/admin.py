from django.contrib import admin

from .models import Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["accession", "name"]
    fields = ["name", "aliases", "smiles", "notes"]

    @admin.display(description="ID", ordering="pk")
    def accession(self, obj):
        return obj.accession
