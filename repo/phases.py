"""Phase labels, and reading phase sequences written as literature summaries.

A summary lists the phases from the lowest temperature up, with the transition temperature
(°C) between each phase and the next: "K 139.8 NF 132.7 N 187.9 I". Labels are converted to
the vocabulary's codes, so K, Nf and I are read as Cr, NF and Iso.

Nothing here touches the database: the synonyms come in as an argument (from
Phase.synonym_map()), so the code can be tested on its own.
"""

import re
from dataclasses import dataclass

# The summary rules depend on these codes, so they must stay in the phase vocabulary.
CRYSTAL = "Cr"
SOLIDS = (CRYSTAL, "g")
ISOTROPIC = "Iso"


class PhaseSequenceError(ValueError):
    """A phase sequence that can't be read. The message says why."""


@dataclass(frozen=True)
class Transition:
    upper: str  # the phase stable above the temperature
    lower: str  # the phase stable below it
    temperature_c: float


@dataclass
class ParsedSequence:
    phases: list  # codes, in the order written: lowest first in a summary
    temperatures: list  # between each phase and the next; None for a melting point not given
    transitions: list  # Transition objects, highest temperature first

    def standard_text(self):
        """The sequence under the codes: "Cr 139.8 NF 132.7 N 187.9 Iso", or "Cr ? N 24 Iso"."""
        parts = [self.phases[0]]
        for temperature, phase in zip(self.temperatures, self.phases[1:]):
            parts += ["?" if temperature is None else f"{temperature:g}", phase]
        return " ".join(parts)


def normalise_label(label):
    """A phase label as labels are compared: no underscores or spaces, lower case, so N_F == NF == nf."""
    return label.replace("_", "").replace(" ", "").lower()


# Characters papers put between labels and temperatures. They are read as spaces.
_SEPARATORS = re.compile(r"°C|°|→|->|–|—|,|;|\(|\)|\[|\]")
# A hyphen joining a label to a number ("K-61.4", "61.4-I") separates them; it isn't a minus sign.
_HYPHEN_BETWEEN_LABEL_AND_NUMBER = re.compile(r"(?<=[A-Za-z])-(?=\d)|(?<=\d)-(?=[A-Za-z])")


def tokenise(text):
    """Split a sequence into labels and temperatures: "K 61.4 °C → I" -> ["K", "61.4", "I"].
    A minus sign straight before a number is kept, so "K -14.9 I" keeps -14.9."""
    text = text.replace("−", "-")  # the Unicode minus sign
    text = _SEPARATORS.sub(" ", text)
    text = _HYPHEN_BETWEEN_LABEL_AND_NUMBER.sub(" ", text)
    text = re.sub(r"\s-\s", " ", f" {text} ")  # a hyphen standing alone between spaces
    return text.split()


def _is_number(token):
    try:
        float(token)
    except ValueError:
        return False
    return True


def is_unknown_temperature(token):
    """'?', '??' and so on: a temperature the paper didn't give."""
    return token.strip("?") == ""


def _read(tokens):
    """Split tokens into labels (positions 0, 2, 4, ...) and temperatures (1, 3, 5, ...)."""
    labels, temperatures = [], []
    for position, token in enumerate(tokens):
        if position % 2 == 0:
            if _is_number(token):
                raise PhaseSequenceError(f"Expected a phase name but found the number {token}.")
            labels.append(token)
        elif _is_number(token):
            temperatures.append(float(token))
        else:
            raise PhaseSequenceError(
                f"Expected a transition temperature after {labels[-1]} but found {token!r}."
            )
    if len(tokens) % 2 == 0:
        raise PhaseSequenceError("A phase sequence must end with a phase name.")
    return labels, temperatures


def _standardise(labels, synonyms):
    """The labels as codes. A label the vocabulary doesn't have is refused, and all such labels are named."""
    unknown = [label for label in labels if normalise_label(label) not in synonyms]
    if unknown:
        raise PhaseSequenceError(
            f"Unknown phase label: {', '.join(unknown)}. "
            "Use a label from the phase vocabulary, or add it there first."
        )
    return [synonyms[normalise_label(label)] for label in labels]


def parse_summary(text, synonyms):
    """Read a literature summary, e.g. "K 139.8 NF 132.7 N 187.9 I". `synonyms` maps each
    normalised label to its code, as Phase.synonym_map() does.

    The solid comes first, with its melting point, then each liquid-crystal phase with its
    transition to the next phase up. Those transitions rise from left to right. A monotropic
    phase (seen only on cooling) can lie below the melting point, so the melting point is not
    compared with them. It becomes a transition into the solid only when it lies below all of
    them; otherwise it stays in the text but not in the transitions. "K ?? N 24 I" or
    "K N 24 I" means the melting point wasn't given.
    """
    tokens = tokenise(text or "")
    if not tokens:
        raise PhaseSequenceError("The phase sequence is empty.")

    solid, melting = None, None
    if synonyms.get(normalise_label(tokens[0])) in SOLIDS:
        solid, tokens = tokens[0], tokens[1:]
        if tokens and is_unknown_temperature(tokens[0]):
            tokens = tokens[1:]
        elif tokens and _is_number(tokens[0]):
            melting, tokens = float(tokens[0]), tokens[1:]
        if not tokens:
            raise PhaseSequenceError("A summary needs the phase the solid melts into, e.g. 'K 61.4 I'.")

    labels, temperatures = _read(tokens)
    phases = _standardise(labels, synonyms)
    if any(higher <= lower for lower, higher in zip(temperatures, temperatures[1:])):
        raise PhaseSequenceError(
            "In a summary, transition temperatures must rise from left to right "
            "(only the melting point after the solid may be higher)."
        )
    if ISOTROPIC in phases and (phases[-1] != ISOTROPIC or phases.count(ISOTROPIC) > 1):
        raise PhaseSequenceError("In a summary, the isotropic liquid must be the last phase.")

    # Each temperature lies between a phase (below it) and the next phase up (above it).
    transitions = [
        Transition(upper, lower, temperature)
        for lower, upper, temperature in zip(phases, phases[1:], temperatures)
    ]
    if solid is not None:
        solid_code = synonyms[normalise_label(solid)]
        if melting is not None and (not temperatures or melting < temperatures[0]):
            transitions.append(Transition(phases[0], solid_code, melting))
        phases, temperatures = [solid_code] + phases, [melting] + temperatures
    transitions.sort(key=lambda transition: transition.temperature_c, reverse=True)
    return ParsedSequence(phases, temperatures, transitions)
