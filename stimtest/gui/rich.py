"""HTML/rich-text helpers for Qt labels.

QLabel and QGroupBox titles render a subset of HTML when given input
that contains tags. We use that to produce subscripts, superscripts,
and italicized variable names without dragging in MathJax / KaTeX.

Conventions
-----------
- ``var('Q', 'inj')`` -> ``<i>Q</i><sub>inj</sub>``
- ``unit('μC', 'cm', 2)`` -> ``μC/cm<sup>2</sup>``
- Greek letters are inserted as Unicode literals (μ, Ω, …); Qt renders
  them in any UI font we ship with.

Only used on **labels and display** widgets. Spinbox suffixes,
QLineEdit content, and QComboBox items stay plain text — Qt input
widgets don't honour HTML.
"""
from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
def var(name: str, sub: Optional[str] = None, sup: Optional[str] = None,
        italic: bool = True) -> str:
    """Render a math variable: italic name with optional sub/superscripts.

        var('Q', 'inj')        -> '<i>Q</i><sub>inj</sub>'
        var('R', 'a')          -> '<i>R</i><sub>a</sub>'
        var('E', 'pol')        -> '<i>E</i><sub>pol</sub>'
        var('μ', italic=False) -> 'μ'
    """
    body = f"<i>{name}</i>" if italic else name
    if sub:
        body += f"<sub>{sub}</sub>"
    if sup:
        body += f"<sup>{sup}</sup>"
    return body


def unit(numerator: str, denom: Optional[str] = None,
         denom_pow: Optional[int] = None) -> str:
    """Render a unit string. ``unit('μC', 'cm', 2)`` -> ``μC/cm<sup>2</sup>``."""
    if denom is None:
        return numerator
    if denom_pow is None or denom_pow == 1:
        return f"{numerator}/{denom}"
    return f"{numerator}/{denom}<sup>{denom_pow}</sup>"


def label_with_units(label_html: str, unit_html: str) -> str:
    """Compose a row label like ``<i>Q</i><sub>inj</sub> [μC/cm²]``."""
    return f"{label_html} [{unit_html}]"


# ---------------------------------------------------------------------------
# Plain-text Unicode subscript / superscript helpers
# ---------------------------------------------------------------------------
# The HTML-based ``var()`` helper only works inside Qt RichText widgets
# (QLabel, QGroupBox titles). Many of our display surfaces are plain
# text — QTableWidgetItem cells, log lines, tsv exports that open in
# Excel — and rendering ``<sub>``/``<sup>`` tags there leaves literal
# angle brackets in the output. The two helpers below rewrite to
# Unicode subscript / superscript code-points where every character
# in the input is representable; if any character isn't (e.g. there
# is no Unicode subscript ``d``), the helper returns ``None`` so the
# caller can fall back to the ``_d`` underscore notation.
_SUBSCRIPT_MAP = {
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
}
_SUPERSCRIPT_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "i": "ⁱ", "n": "ⁿ",
}


def to_subscript(s: str) -> Optional[str]:
    """Convert ``s`` to a Unicode subscript string. Lower-cases first,
    and returns ``None`` if any character isn't representable
    (e.g. ``d``/``f``/``c``/``b``/``g``/``q``/``y``/``z`` / capitals).
    """
    out = []
    for ch in s.lower():
        glyph = _SUBSCRIPT_MAP.get(ch)
        if glyph is None:
            return None
        out.append(glyph)
    return "".join(out)


def to_superscript(s: str) -> Optional[str]:
    """Same as :func:`to_subscript` but for Unicode superscripts. Used
    chiefly for unit exponents (``cm²`` is the only common case in
    this codebase, but we also produce ``s⁻¹`` etc. cleanly)."""
    out = []
    for ch in s:
        glyph = _SUPERSCRIPT_MAP.get(ch.lower())
        if glyph is None:
            return None
        out.append(glyph)
    return "".join(out)


def plain_label(name: str, sub: Optional[str] = None,
                sup: Optional[str] = None) -> str:
    """Plain-text variable label using Unicode subscripts /
    superscripts where possible. Used by tables and tsv exports that
    can't render the HTML-tagged ``var()`` output.

      >>> plain_label("Q", "ph")        # 'Qₚₕ'
      >>> plain_label("V", "d")         # 'V_d'   (no subscript d)
      >>> plain_label("C", "eff")       # 'C_eff' (no subscript f)
      >>> plain_label("R", "a")         # 'Rₐ'
      >>> plain_label("cm", sup="2")    # 'cm²'
    """
    out = name
    if sub:
        u = to_subscript(sub)
        out += u if u is not None else f"_{sub}"
    if sup:
        u = to_superscript(sup)
        out += u if u is not None else f"^{sup}"
    return out


# ---------------------------------------------------------------------------
# Frequently-used labels (single source of truth so all tabs match)
# ---------------------------------------------------------------------------
# Scalar variables
I_STIM        = var("I", "stim")
Q_PH          = var("Q", "ph")
Q_INJ         = var("Q", "inj")
V_D           = var("V", "d")
V_A           = var("V", "a")
R_A           = var("R", "a")
C_D           = var("C", "d")
E_POL         = var("E", "pol")
E_LC          = var("E", "lc")
E_LA          = var("E", "la")
E_RET         = var("E", "ret")
E_ACT         = var("E", "act")
V_MON         = var("V", "mon")
I_MON         = var("I", "mon")
T_PH          = var("t", "ph")
T_IPH         = var("t", "iph")
T_DD          = var("t", "dd")
A_GS          = var("A", "gs")
# Pulse-train timing — kept in sync with the
# ``pattern_panel`` row labels (the rate/period unit-toggle
# rewrites the row text via ``field_label`` so both halves
# of the toggle reference these constants). ``f_stim`` is
# the canonical neurostim notation for stimulation
# frequency; ``T_pulse`` is the inter-pulse period
# (1 / f_stim). Defining them here means a future tab that
# also surfaces these knobs picks up the same abbreviation.
F_STIM        = var("f", "stim")
T_PULSE       = var("T", "pulse")
# Ramp-starting amplitude. Used by experiment tabs that sweep
# the stim amplitude from a small floor up to a ceiling
# (Voltage Transient, Progressive Stress) — both the spinbox
# label and any saved-session export reference this constant
# so a reader sees the same ``I_start`` symbol everywhere.
I_START       = var("I", "start")

# Common unit strings
UA            = "μA"
US            = "μs"
MS            = "ms"
PPS           = "pps"
UM2           = "μm²"
MM2           = "mm²"
CM2           = "cm²"
NA            = "nA"
NC            = "nC"
PC            = "pC"
UC_PER_CM2    = "μC/cm²"
MC_PER_CM2    = "mC/cm²"
V_PER_DIV     = "V/div"
S_PER_DIV     = "s/div"

# Greek
MU            = "μ"
OMEGA         = "Ω"
DELTA_BIG     = "Δ"
SIGMA         = "σ"


def make_form(parent=None) -> "QtWidgets.QFormLayout":
    """Return a :class:`QFormLayout` configured for in-line labels.

    Forces three policies that together guarantee labels render to the
    LEFT of their fields on every platform, regardless of how narrow
    the column is:

    * ``RowWrapPolicy.DontWrapRows`` — never push a label above its
      field, even if the field row is wider than the form column.
      Some Qt themes default to ``WrapLongRows``, which silently
      breaks the "Name [unit]:" layout convention.
    * ``LabelAlignment.AlignRight | AlignVCenter`` — labels hug their
      fields and share the spinbox's vertical centerline so no row
      reads as misaligned.
    * ``FieldGrowthPolicy.AllNonFixedFieldsGrow`` — fields stretch to
      fill the form column, so spinboxes / line edits don't end up
      orphaned in the middle of a wide row.

    ``parent`` matches Qt's own ``QFormLayout(parent)`` ergonomics so
    call sites that previously installed the layout directly on a
    widget keep working unchanged.
    """
    from PyQt6 import QtCore, QtWidgets   # local imports keep this
    if parent is None:                    # module side-effect-free
        f = QtWidgets.QFormLayout()
    else:
        f = QtWidgets.QFormLayout(parent)
    f.setRowWrapPolicy(
        QtWidgets.QFormLayout.RowWrapPolicy.DontWrapRows)
    f.setLabelAlignment(
        QtCore.Qt.AlignmentFlag.AlignRight |
        QtCore.Qt.AlignmentFlag.AlignVCenter)
    f.setFieldGrowthPolicy(
        QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    # Tighten vertical spacing — Qt's per-style default leaves a lot of
    # air between rows that adds up visually on the Setup tab. Keep
    # horizontal spacing roomier so labels stay clearly separated from
    # their fields.
    f.setVerticalSpacing(2)
    f.setHorizontalSpacing(8)
    f.setContentsMargins(4, 4, 4, 4)
    return f


def find_form_layout(layout, field_widget) -> "QtWidgets.QFormLayout | None":
    """Recursively search ``layout`` for a :class:`QFormLayout` whose
    field column owns ``field_widget``.

    Returns the matching form, or ``None`` if no enclosing form
    contains the field. Used by row-visibility helpers so they still
    work when the form has been added to an outer VBox/HBox (the
    common case here — most forms are nested inside group-box
    layouts rather than installed directly on the parent widget).
    """
    from PyQt6 import QtWidgets
    if layout is None:
        return None
    if isinstance(layout, QtWidgets.QFormLayout):
        if layout.labelForField(field_widget) is not None:
            return layout
    for i in range(layout.count()):
        item = layout.itemAt(i)
        sub = item.layout() if item is not None else None
        if sub is None:
            continue
        found = find_form_layout(sub, field_widget)
        if found is not None:
            return found
    return None


def make_label(text: str, *, rich_text: bool = True) -> "QtWidgets.QLabel":
    """Return a :class:`QLabel` whose text renders on the widget's
    vertical centerline.

    QLabel's default content alignment (``AlignTop | AlignLeft``) makes
    the text sit at the top of the widget's bounding box. When the
    label shares an HBox / form row with a taller spinbox or combo,
    the result is the label hugging the top of the row while the
    input centerline floats lower — a visual misalignment the user
    sees as "the label is on a different line from the input".

    This helper sets ``AlignLeft | AlignVCenter`` so the text shares
    the row's vertical centerline with whatever input it sits next
    to. Pass ``rich_text=False`` for plain-text labels that shouldn't
    interpret HTML.
    """
    from PyQt6 import QtCore, QtWidgets
    lab = QtWidgets.QLabel(text)
    if rich_text:
        lab.setTextFormat(QtCore.Qt.TextFormat.RichText)
    lab.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft |
                     QtCore.Qt.AlignmentFlag.AlignVCenter)
    return lab


def field_label(name: str, var: Optional[str] = None,
                unit_str: Optional[str] = None) -> str:
    """Build a form-row label in the project's house style:

        ``Name (variable) [unit]:``

    Where ``name`` is the spelled-out parameter ("Phase width"),
    ``var`` is the italic-with-subscripts variable form (``rich.T_PH``),
    and ``unit_str`` is a plain unit ("μs"). Anything missing is
    omitted; the trailing colon is always present to keep
    QFormLayout's alignment consistent.
    """
    bits = [name]
    if var:
        bits.append(f"({var})")
    if unit_str:
        bits.append(f"[{unit_str}]")
    return " ".join(bits) + ":"
