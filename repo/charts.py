"""Charts of property values, drawn by matplotlib on the server as SVG, which the page
includes as it is: no JavaScript. matplotlib is used through its Figure object, never
pyplot, which keeps every chart in one shared state and isn't safe in a web server."""

import io

import matplotlib
from django.utils.safestring import mark_safe
from matplotlib.figure import Figure

from .models import Series, Variable

# A series of more than this many points is drawn as a thin line, without markers or
# error bars, which would merge into a thick band.
DENSE_POINTS = 200

# Each series' colour and marker, in this order on every chart. The colours were checked
# as a set for colour-blind readers; the markers tell series apart without colour.
COLOURS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MARKERS = ["o", "s", "^", "D"]
# The page's own colours (style.css): white surface, ink for text, hairline for the grid.
SURFACE, INK, INK_2, HAIRLINE = "#ffffff", "#17202a", "#4a5664", "#d9dfe4"

# Settings for every chart, set once, when this module is first imported.
matplotlib.rcParams.update({
    "svg.fonttype": "none",  # text stays text: a smaller file, in a font the browser has
    "font.size": 10,
    "text.color": INK,
    "axes.labelcolor": INK,
    "axes.edgecolor": INK_2,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
})


def chart_groups(series):
    """Series split into the charts they can share: [(kind, variable, [series, ...])], one
    per kind and variable, because measured, extrapolated and computed values never share
    a chart (data rule 2), nor do two x-axes. Kinds come in Series.Kind's order, then
    variables in theirs. A series against nothing has no x, so it gets no chart."""
    groups = {}
    for one in series:
        if not one.is_against_nothing():
            groups.setdefault((one.kind, one.variable), []).append(one)
    kinds = Series.Kind.values
    ordered = sorted(groups, key=lambda key: (kinds.index(key[0]), key[1].sort_order, key[1].code))
    return [(kind, variable, groups[(kind, variable)]) for kind, variable in ordered]


def plotted_points(series):
    """The points of a series a chart draws, in x order. Limits are left out: < 0.1 isn't
    a value of 0.1. Uses the points prefetched with the series, so it costs no queries."""
    points = [point for point in series.points.all() if not point.is_limit() and point.x_value() is not None]
    return sorted(points, key=lambda point: point.x_value())


def plain(text):
    """Text as matplotlib shows it as typed. Between two $ signs it reads a formula, and
    stops with an error at one it can't read, so each $ is escaped as \\$."""
    return text.replace("$", r"\$")


def axis_title(name, unit):
    """"Temperature (°C)", "Ps (µC/cm²)", or the name alone without a unit: "Δn"."""
    return plain(name if unit in ("", "dimensionless") else f"{name} ({unit})")


def figure(prop, variable, series_list):
    """One chart: prop against variable, one line per series, labelled in the legend by
    its reference. A single value is one marker. Series with no points to draw are left
    out. Returns the matplotlib Figure, so tests can look at what was drawn."""
    lines = [(one, plotted_points(one)) for one in series_list]
    lines = [(one, points) for one, points in lines if points]
    # Taller with each line, so the legend under the plot doesn't squash it.
    fig = Figure(figsize=(6.4, 3.2 + 0.25 * len(lines)), facecolor=SURFACE, layout="constrained")
    ax = fig.subplots()
    handles = []  # what each line looks like in the legend, in the order drawn
    for index, (one, points) in enumerate(lines):
        xs = [point.x_value() for point in points]
        values = [point.value for point in points]
        style = {"color": COLOURS[index % len(COLOURS)], "label": plain(one.reference.label)}
        if len(points) > DENSE_POINTS:
            handles += ax.plot(xs, values, linewidth=1, **style)
            continue
        errors = [point.uncertainty or 0 for point in points]
        handles.append(ax.errorbar(
            xs, values, yerr=errors if any(errors) else None, linewidth=1.5, elinewidth=1,
            marker=MARKERS[index % len(MARKERS)], markersize=6, markeredgecolor=SURFACE, **style,
        ))
    if variable.scale == Variable.Scale.LOG:
        ax.set_xscale("log")
    ax.set_xlabel(axis_title(variable.name, variable.unit))
    ax.set_ylabel(axis_title(prop.symbol, prop.unit))
    ax.grid(color=HAIRLINE, linewidth=0.75)
    ax.set_axisbelow(True)  # the grid behind the lines
    ax.spines[["top", "right"]].set_visible(False)
    # Given the handles, the legend keeps the lines' order. Left to itself, it would list
    # dense lines before the others.
    fig.legend(handles=handles, loc="outside lower center", frameon=False)
    return fig


def to_svg(fig):
    """The figure as SVG to put in a page: from <svg on, without the XML declaration and
    DOCTYPE, which belong only at the top of a file of its own. No date, so drawing the
    same chart twice gives the same text, apart from matplotlib's random ids."""
    out = io.StringIO()
    fig.savefig(out, format="svg", metadata={"Date": None})
    text = out.getvalue()
    return text[text.index("<svg"):]


def description(prop, kind, variable, series_list):
    """What a screen reader says for a chart, which it can't see: "Ps against temperature,
    measured values: 1 series, from Chen 2022, Liq. Cryst." References are joined with ;
    because a label can contain a comma."""
    references = "; ".join(dict.fromkeys(one.reference.label for one in series_list))
    n = len(series_list)
    return f"{prop.symbol} against {variable.name.lower()}, {kind} values: {n} series, from {references}"


def charts_for(prop, series_list):
    """A property's charts, as the template shows them: [{"kind", "variable", "svg",
    "description", "n_limits"}], one per kind and variable. A chart whose values are all
    limits has no svg, only the count of limits left out; one with nothing at all is left out."""
    charts = []
    for kind, variable, group in chart_groups(series_list):
        drawn = [one for one in group if plotted_points(one)]
        n_limits = sum(point.is_limit() for one in group for point in one.points.all())
        if not (drawn or n_limits):
            continue
        charts.append({
            "kind": kind,
            "variable": variable,
            # mark_safe(): the template includes the SVG as it is. matplotlib escapes the
            # text it writes into it, such as the legend's labels.
            "svg": mark_safe(to_svg(figure(prop, variable, drawn))) if drawn else "",
            "description": description(prop, kind, variable, drawn),
            "n_limits": n_limits,
        })
    return charts
