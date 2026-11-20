"""Phase ribbons: a phase sequence drawn as coloured bands on a temperature axis, coldest on
the left, as a small SVG written by hand. A page can draw 25 of them, which matplotlib would
make slow; and each is only rectangles and text.

A ribbon shows no more than the data says. Only the isotropic liquid runs to the top of the
axis, and only a solid to the bottom. A phase whose other end wasn't reported is drawn open,
fading out where the data stops: on a material's page in three steps, and in the Materials
list as a block wide enough for its code and then two steps, so the code can be read there.

The functions return SVG marked safe for templates, so every piece of text written into it
(phase codes, labels, tooltips) is escaped first.
"""

import math
from dataclasses import dataclass

from django.utils.html import escape
from django.utils.safestring import mark_safe

from .phases import ISOTROPIC, SOLIDS, SUBSCRIPTS, Transition

INK = "#17202a"
WHITE = "#ffffff"

# Each phase's fill, and the text colour that reads on it (whichever of white and ink contrasts
# more). The fills are the prototype's, chosen to stay apart for colour-blind readers. Colour is
# never the only cue: each band has its code written in it when it fits, and a tooltip.
PHASE_COLOURS = {
    ISOTROPIC: ("#eef1f4", INK),  # as pale as the page, so it is drawn with an outline
    "N": ("#2a8fbd", INK),
    "NS": ("#5b3bb0", WHITE),
    "NF": ("#b8306a", WHITE),
    "SmAF": ("#c0641c", WHITE),
    "SmA": ("#5aa86e", INK),
    "SmC": ("#5aa86e", INK),
    "Cr": ("#6b7580", WHITE),
}
OTHER_COLOURS = ("#c5ccd3", INK)  # every phase not listed above
KEY_STUB_FILL = "#8a939b"  # the key's example of an open end: a neutral grey, standing for any phase

GAP = 2  # pixels between touching bands
FADE = (0.5, 0.2)  # the list's open end: the opacities of the two steps after the block, outwards
STEPS = (0.7, 0.45, 0.2)  # a material page's open end: the opacities of its three steps, outwards


@dataclass
class RibbonData:
    """What a ribbon is drawn from."""

    transitions: list  # phases.Transition objects, highest temperature first
    melting: float = None  # a summary's melting point, which may not be a transition
    lowest: float = None  # the lowest temperature reached, where the reference gives it


def ribbon_data(sequence):
    """What a ribbon needs from a saved PhaseSequence. The transitions are the stored ones, so
    the ribbon shows what the temperature search finds. Prefetch transitions__upper and
    transitions__lower when drawing many, or each transition costs two queries.

    The melting point is marked for a summary only, as in the prototype: in a summary it can
    lie above a monotropic phase, so it isn't always a transition. In a heating run it always
    is one, so the ribbon already shows it."""
    transitions = sorted(
        (Transition(t.upper.code, t.lower.code, t.temperature_c) for t in sequence.transitions.all()),
        key=lambda transition: transition.temperature_c,
        reverse=True,
    )
    melting = sequence.t_melting_c if sequence.direction == sequence.Direction.SUMMARY else None
    return RibbonData(transitions, melting, sequence.lowest_temperature_c)


def bands(data, lo, hi):
    """The bands of a ribbon on an axis from lo to hi (°C), from the top down, as (phase, low,
    high). An end the data doesn't give is None, and is drawn open.

    The top phase runs to hi only if it is the isotropic liquid; any other top phase has no
    reported clearing point. Each phase below runs from its transition up to the next. The
    lowest phase ends at the lowest temperature reached, if given; a solid runs on to lo; any
    other phase has no reported lower limit, so a run that stops at NF gets no solid.
    """
    transitions = data.transitions
    if not transitions:
        return []
    top = transitions[0]
    found = [(top.upper, top.temperature_c, hi if top.upper == ISOTROPIC else None)]
    for i, transition in enumerate(transitions):
        if i + 1 < len(transitions):
            low = transitions[i + 1].temperature_c
        elif data.lowest is not None and data.lowest < transition.temperature_c:
            low = data.lowest
        elif transition.lower in SOLIDS:
            low = lo
        else:
            low = None
        found.append((transition.lower, low, transition.temperature_c))
    return found


def nice_step(span, target=6):
    """A round step giving about target ticks across span: 1, 2 or 5 times a power of ten.
    An axis spans at least 10 °C (temperature_range()), so the step is a whole number."""
    raw = span / target
    magnitude = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 5):
        if raw <= multiple * magnitude:
            return multiple * magnitude
    return 10 * magnitude


def temperature_range(datas):
    """One axis for several ribbons, as (lo, hi) in °C: every transition, melting point and
    lowest temperature reached, with room either side (6% of the span, and at least 5 °C),
    widened out to whole steps. (0, 200) when there are no temperatures at all."""
    temperatures = [t.temperature_c for data in datas for t in data.transitions]
    temperatures += [value for data in datas for value in (data.melting, data.lowest) if value is not None]
    if not temperatures:
        return 0, 200
    low, high = min(temperatures), max(temperatures)
    pad = max(5, (high - low) * 0.06)
    step = nice_step(high - low + 2 * pad)
    return math.floor((low - pad) / step) * step, math.ceil((high + pad) / step) * step


def ticks(lo, hi):
    """The temperatures an axis from lo to hi labels: the multiples of a round step."""
    step = nice_step(hi - lo)
    return list(range(math.ceil(lo / step) * step, math.floor(hi / step) * step + 1, step))


def number(value):
    """A temperature as a ribbon writes it, with a true minus sign: -14.9 -> "−14.9"."""
    return f"{value:g}".replace("-", "−")


def colours(phase):
    """(fill, text colour) for a phase code."""
    return PHASE_COLOURS.get(phase, OTHER_COLOURS)


def label_width(phase):
    """About how many pixels a phase code needs inside a band, padding included."""
    return 7.2 * len(phase) + 8


def _rect(left, right, height, fill, extra=""):
    """A band's rectangle from left to right (pixels). Nothing when it is too thin to see."""
    if right - left < 0.5:
        return ""
    return f'<rect x="{left:.1f}" y="1" width="{right - left:.1f}" height="{height - 2}" rx="2" fill="{fill}"{extra}/>'


def svg_phase(phase):
    """A phase code as SVG text, escaped, with any subscript (phases.SUBSCRIPTS) set lower and
    smaller: NF -> N<tspan ...>F</tspan>. SVG has no <sub>, so a shifted <tspan> does its job."""
    if phase in SUBSCRIPTS:
        main, sub = SUBSCRIPTS[phase]
        return f'{escape(main)}<tspan baseline-shift="sub" font-size="75%">{escape(sub)}</tspan>'
    return escape(phase)


def _label(phase, middle, height, colour):
    return (
        f'<text x="{middle:.1f}" y="{height / 2 + 4:.1f}" text-anchor="middle" class="ribbon-label" '
        f'fill="{colour}">{svg_phase(phase)}</text>'
    )


def _band_title(phase, low, high, lo, hi):
    """A band's tooltip, e.g. "N: 132.7 to 187.9 °C"."""
    if low is None:
        return f"{phase}: below {number(high)} °C; lower limit not reported"
    if high is None:
        return f"{phase}: above {number(low)} °C; upper limit not reported"
    if high == hi:
        return f"{phase}: above {number(low)} °C"
    if low == lo:
        return f"{phase}: below {number(high)} °C"
    return f"{phase}: {number(low)} to {number(high)} °C"


def _open_band(phase, edge, outward, width, height, block):
    """A band with one end not reported. It starts at the reported end (edge, in pixels) and
    runs outward, -1 down the axis or +1 up it, fading out where the data stops. Kept inside the
    ribbon's width.

    With block (the Materials list), a block wide enough for the phase's code, with the code in
    it, then two short steps (FADE): at a list's small size the code is what tells the phases
    apart. Without (a material's page), three steps (STEPS) and no code, as in the prototype:
    the colour, the key, the tooltip and the sequence under the ribbon name the phase."""
    fill, text = colours(phase)

    def inside(a, b):
        left, right = sorted((a, b))
        return max(1.0, left), min(width - 1.0, right)

    start = edge + outward * GAP / 2
    parts = []
    if block:
        end = start + outward * max(24.0, label_width(phase))
        left, right = inside(start, end)
        parts.append(_rect(left, right, height, fill))
        if right - left >= label_width(phase):
            parts.append(_label(phase, (left + right) / 2, height, text))
        opacities, step = FADE, 4
        start = end + outward * GAP
    else:
        # The three steps and the gaps between them take 4.5% of the width, and at least 18 px.
        opacities = STEPS
        step = (max(18.0, width * 0.045) - GAP * (len(STEPS) - 1)) / len(STEPS)
    for opacity in opacities:
        end = start + outward * step
        parts.append(_rect(*inside(start, end), height, fill, f' fill-opacity="{opacity}"'))
        start = end + outward * GAP
    return "".join(parts)


def _melting_mark(data, x, height):
    """A dashed line at a summary's melting point, on a white line so it shows on any band."""
    title = f"Melting point {number(data.melting)} °C"
    if any(t.temperature_c < data.melting and t.lower not in SOLIDS for t in data.transitions):
        title += "; phases below it were seen on cooling only (monotropic)"
    return (
        f"<g><title>{escape(title)}</title>"
        # A wider transparent strip, so the tooltip is easy to find with the mouse.
        f'<rect x="{x - 5:.1f}" y="0" width="10" height="{height}" fill="transparent"/>'
        f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{height}" stroke="{WHITE}" stroke-width="3.5"/>'
        f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{height}" stroke="{INK}" stroke-width="1.5" '
        f'stroke-dasharray="3 2"/></g>'
    )


def ribbon_svg(data, lo, hi, label, width=640, height=26, temperatures=True, block_ends=False):
    """One ribbon on an axis from lo to hi (°C), width by height pixels. label is what a screen
    reader says for it: the sequence as text. With temperatures, each transition and melting
    point is written under the ribbon, leaving out one that would crowd the last written.
    block_ends draws open ends as the Materials list does (_open_band()).

    A material's page draws them at the defaults; the list at 600 by 22, without temperatures
    and with block_ends, so 25 rows stay compact and each phase's code stays readable."""
    total = height + (16 if temperatures else 0)
    scale = (width - 2) / (hi - lo)

    def x(temperature):
        return 1 + (temperature - lo) * scale

    parts = [
        f'<svg class="ribbon" viewBox="0 0 {width} {total}" width="{width}" height="{total}" '
        f'role="img" aria-label="{escape(label)}">'
    ]
    drawn = bands(data, lo, hi)
    if not drawn:
        parts.append(
            "<g><title>No transition temperatures reported</title>"
            f'<rect x="1.5" y="1.5" width="{width - 3}" height="{height - 3}" rx="3" class="not-examined"/></g>'
        )
    for phase, low, high in drawn:
        parts.append(f"<g><title>{escape(_band_title(phase, low, high, lo, hi))}</title>")
        if low is None:
            parts.append(_open_band(phase, x(high), -1, width, height, block_ends))
        elif high is None:
            parts.append(_open_band(phase, x(low), +1, width, height, block_ends))
        else:
            fill, text = colours(phase)
            left, right = x(low) + GAP / 2, x(high) - GAP / 2
            outline = ' stroke="#d3d9df" stroke-width="1"' if phase == ISOTROPIC else ""
            parts.append(_rect(left, right, height, fill, outline))
            if right - left >= label_width(phase):
                parts.append(_label(phase, (left + right) / 2, height, text))
        parts.append("</g>")
    if data.melting is not None:
        parts.append(_melting_mark(data, x(data.melting), height))
    if data.lowest is not None:
        parts.append(
            f"<g><title>Not examined below {escape(number(data.lowest))} °C</title>"
            f'<rect x="1.5" y="1.5" width="{max(0, x(data.lowest) - 3):.1f}" height="{height - 3}" rx="3" '
            f'class="not-examined"/></g>'
        )
    if temperatures:
        written = {t.temperature_c for t in data.transitions}
        if data.melting is not None:
            written.add(data.melting)
        last = -math.inf
        for temperature in sorted(written):
            if x(temperature) - last < 34:  # about the width of "132.7"
                continue
            last = x(temperature)
            parts.append(
                f'<text x="{last:.1f}" y="{height + 12}" text-anchor="middle" class="ribbon-temp">'
                f"{escape(number(temperature))}</text>"
            )
    parts.append("</svg>")
    # Safe to mark: every piece of text above went through escape().
    return mark_safe("".join(parts))


def axis_svg(lo, hi, width=640, height=20):
    """The temperature axis for ribbons drawn from lo to hi at the same width. Hidden from
    screen readers: each ribbon's label already gives its temperatures."""
    scale = (width - 2) / (hi - lo)
    parts = [
        f'<svg class="ribbon-axis" viewBox="0 0 {width} {height}" width="{width}" height="{height}" aria-hidden="true">'
        f'<line x1="1" x2="{width - 1}" y1="{height - 1}" y2="{height - 1}" class="axis-line"/>'
    ]
    for value in ticks(lo, hi):
        x = 1 + (value - lo) * scale
        # The end labels are kept inside the axis.
        anchor = "start" if x < 12 else "end" if x > width - 12 else "middle"
        parts.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{height - 5}" y2="{height - 1}" class="axis-line"/>'
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}" class="axis-text">{number(value)}</text>'
        )
    parts.append("</svg>")
    return mark_safe("".join(parts))


def key_items():
    """The key to the ribbons' colours, built from PHASE_COLOURS so the two can't disagree: one
    entry per fill, as (codes, fill), with the codes that share it together (SmA and SmC), then
    ([], fill) for every phase not listed."""
    by_fill = {}
    for code, (fill, _) in PHASE_COLOURS.items():
        by_fill.setdefault(fill, []).append(code)
    return [(codes, fill) for fill, codes in by_fill.items()] + [([], OTHER_COLOURS[0])]
