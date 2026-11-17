"""Phase labels and, from block 3.2, reading phase sequences.

Nothing here touches the database: the vocabulary comes in as an argument, so the code can
be tested on its own.
"""


def normalise_label(label):
    """A phase label as labels are compared: no underscores or spaces, lower case, so N_F == NF == nf."""
    return label.replace("_", "").replace(" ", "").lower()
