"""Template filters for FFDB. Load them in a template with {% load repo_tags %}."""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from ..models import CONDITION_NAMES, CONDITION_UNITS, Measurement, Series

register = template.Library()


@register.filter
def formula(text):
    """C22H17NO8 -> C<sub>22</sub>H<sub>17</sub>NO<sub>8</sub>.

    Only a number straight after an element symbol is a count, so a charge such as
    Ca+2 is left as it is.
    """
    # Escape first, then mark safe: the <sub> tags are the only HTML that gets through.
    return mark_safe(re.sub(r"(?<=[A-Za-z])(\d+)", r"<sub>\1</sub>", escape(text)))


@register.filter
def num(value):
    """A number as pages show it: 4 significant figures, with a true minus sign (−, not the
    hyphen -): -1.23456 -> "−1.235". None -> "–". A number that 4 figures would write as
    1e+04 is written whole instead: 10000."""
    if value is None:
        return "–"
    text = f"{value:.4g}"
    if "e+" in text:
        text = f"{value:.0f}"
    return text.replace("-", "−")


def with_unit(text, unit):
    """A number's text with its unit: "8 µm", "45°" (no space before °), and no unit for
    a dimensionless property: "0.21"."""
    if unit in ("", "dimensionless"):
        return text
    return f"{text}{unit}" if unit == "°" else f"{text} {unit}"


def range_text(numbers):
    """The lowest and highest of numbers: "60 to 90", or "60" when they show as one
    number. "–" when there are none."""
    if not numbers:
        return "–"
    low, high = num(min(numbers)), num(max(numbers))
    return low if low == high else f"{low} to {high}"


def is_limit(point):
    return point.qualifier != Measurement.Qualifier.EXACT


def point_text(point):
    """One point's value, with its sign if it is a limit, "< 0.1", or its ±, "4.4 ± 0.2".
    A limit has no ± (Measurement.clean() refuses one)."""
    text = num(point.value)
    if is_limit(point):
        return f"{Measurement.SIGNS[point.qualifier]} {text}"
    if point.uncertainty is not None:
        text += f" ± {num(point.uncertainty)}"
    return text


@register.filter
def value_text(series):
    """A series' Value cell, with its property's unit. A single value with its sign or ±:
    "< 0.1 pm/V", "4.4 ± 0.2 µC/cm²". A curve, the range of its exact values, with any
    limits counted after it: "0.2 to 6.1 µC/cm²; 2 limits". Limits stay out of the range,
    because < 0.1 isn't a value of 0.1."""
    points = list(series.points.all())
    unit = series.property.unit
    if not points:
        return "–"
    if len(points) == 1:
        return with_unit(point_text(points[0]), unit)
    exact = [point.value for point in points if not is_limit(point)]
    limits = sorted((point for point in points if is_limit(point)), key=lambda point: point.value)
    counted = f"{len(limits)} limit{'s' if len(limits) != 1 else ''}"
    if not exact:
        low, high = point_text(limits[0]), point_text(limits[-1])
        ends = low if low == high else f"{low} to {high}"
        return with_unit(f"{counted}, {ends}", unit)
    text = with_unit(range_text(exact), unit)
    return f"{text}; {counted}" if limits else text


@register.filter
def at_text(series):
    """A series' At cell: what its values were measured at. Against temperature, the
    temperature, "100 °C", or a curve's range, "60 to 90 °C". Against anything else, x in
    its variable's unit and any temperature: "1000 Hz at 25 °C". Against nothing, "–".
    Heating or cooling is added when the reference says: "60 to 90 °C, cooling"."""
    points = list(series.points.all())
    if not points or series.is_against_nothing():
        return "–"
    temperatures = [point.temperature_c for point in points if point.temperature_c is not None]
    if series.is_against_temperature():
        text = with_unit(range_text(temperatures), "°C")
    else:
        xs = [point.x for point in points if point.x is not None]
        text = with_unit(range_text(xs), series.variable.unit)
        if temperatures:
            text += f" at {with_unit(range_text(temperatures), '°C')}"
    if series.direction in (Series.Direction.HEATING, Series.Direction.COOLING):
        text += f", {series.get_direction_display().lower()}"
    return text


@register.filter
def conditions_text(conditions):
    """A series' fixed conditions in words, sorted by key: {"frequency_Hz": 50, "alignment":
    "planar"} -> "alignment: planar, frequency: 50 Hz". Numbers go through num(). "" for none.
    Sorted, because PostgreSQL doesn't keep the keys in the order they were typed."""
    conditions = conditions or {}
    parts = []
    for key in sorted(conditions):
        value = conditions[key]
        text = num(value) if isinstance(value, (int, float)) else str(value)
        parts.append(f"{CONDITION_NAMES.get(key, key)}: {with_unit(text, CONDITION_UNITS.get(key, ''))}")
    return ", ".join(parts)


@register.filter
def where_text(series):
    """Where in its reference a series is: its location, or else what it was taken from,
    with "digitised" if it was read off a figure: "Fig. 5(b), digitised", "Table".
    "" when nothing is recorded."""
    parts = [series.location or (series.get_taken_from_display() if series.taken_from else "")]
    if series.digitised:
        parts.append("digitised")
    return ", ".join(part for part in parts if part)
