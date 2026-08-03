"""
backend/api/pdf_tables.py — the ONE branded, overflow-proof PDF table renderer.

Every tabular PDF in the app renders through `render_table_pdf()`. It replaces
the old `reports.to_pdf` / `reports.to_pdf_sheets`, which laid every table out
with EQUAL column widths and then hard-truncated content to fixed character
counts (`[:18]` headers, `[:24]` cells). Both halves of that were broken:

  * fpdf's `cell()` does NOT clip — text wider than the cell is simply drawn
    over the neighbour. Measured on a real receipts row, "CUMICRETE PU MF 300
    (1MM) COMPONENT C HARDENER RESIN" ran **4.1 mm into the next column** while
    `Date` and `UOM` each wasted ~20 mm of the same page.
  * The truncation destroyed 28 characters of that description and turned the
    header `Equipment_Description` into `Equipment_Descript`.

The four rules this module enforces instead:

  1. **Widths are measured from content**, proportional to what each column
     actually needs, and always sum to EXACTLY the printable width — so a table
     can never run off the page, and a narrow `UOM` column donates its slack to
     a long description.
  2. **Cells wrap onto as many lines as they need.** Nothing is truncated.
     fpdf2's own line-breaker does the wrapping (`dry_run`/`output="LINES"`),
     so the measured lines are the drawn lines — including character-level
     breaking of unbreakable tokens like a 40-char lot number.
  3. **Font size adapts to column count.** Wide tables step down through
     8 → 7 → 6 → 5.5 pt so a 14-column report still gets readable wrapping
     instead of one word per line.
  4. **Rows are atomic.** A row is measured before it is drawn and moved whole
     to the next page if it does not fit; the table header then repeats.

Shared letterhead: navy band + the GI logo (the same `sme_logo.png` the premium
xlsx exports use), a "Page X of Y" footer, and zebra striping — so a PDF and an
xlsx of the same report look like siblings.
"""
from __future__ import annotations

import datetime as _dt
import os

from fpdf import FPDF

# Palette — identical to exec_pdf.py and the xlsx exports (navy #0A192F).
NAVY = (10, 25, 47)
ACCENT = (24, 144, 255)
ZEBRA = (245, 247, 250)
BORDER = (208, 213, 221)
MUTED = (100, 110, 125)

_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sme_logo.png")

_PAD = 1.2          # mm of breathing room each side of cell text
_LINE_H = 3.6       # mm per wrapped line of body text
_MIN_COL_MM = 11.0  # a column never shrinks below this…
_MAX_COL_MM = 78.0  # …nor hogs more than this before the rest get their share
_MAX_LINES = 8      # runaway-cell guard (see _wrap)


def _latin(s: str) -> str:
    """Core PDF fonts are latin-1 only; degrade rather than crash."""
    return str(s).encode("latin-1", "ignore").decode("latin-1")


def fmt_value(v) -> str:
    """Human formatting: ints with separators, floats trimmed to <=4 dp."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        s = f"{v:,.4f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    return str(v)


class BrandedPDF(FPDF):
    """A4 landscape report page: navy letterhead + logo, numbered footer."""

    def __init__(self, *, title: str, username: str, orientation: str = "L"):
        super().__init__(orientation=orientation, unit="mm", format="A4")
        self.doc_title = _latin(title)
        self.username = _latin(username or "")
        self.set_margins(10, 10, 10)
        self.set_auto_page_break(auto=True, margin=14)
        self.alias_nb_pages()

    def header(self):
        self.set_fill_color(*NAVY)
        self.rect(0, 0, self.w, 13, style="F")
        self.set_fill_color(*ACCENT)
        self.rect(0, 13, self.w, 0.7, style="F")
        if os.path.exists(_LOGO_PATH):
            try:
                self.image(_LOGO_PATH, x=self.l_margin, y=1.6, h=9.8)
            except Exception:  # noqa: BLE001 — branding is never worth a 500
                pass
        self.set_xy(self.l_margin + 20, 3.2)
        self.set_font("helvetica", "B", 10)
        self.set_text_color(255, 255, 255)
        self.cell(0, 6, self.doc_title.upper(), align="L")
        self.set_font("helvetica", "", 7)
        self.set_xy(self.l_margin, 3.6)
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.cell(0, 5, _latin(f"GENERAL INDUSTRIES  ·  {self.username}  ·  {stamp}"),
                  align="R")
        self.set_text_color(0, 0, 0)
        self.set_y(18)

    def footer(self):
        self.set_y(-10)
        self.set_draw_color(*BORDER)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_font("helvetica", "", 6.5)
        self.set_text_color(*MUTED)
        self.cell(0, 5, _latin(f"GI Hub  ·  {self.doc_title}"), align="L")
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")
        self.set_text_color(0, 0, 0)


def pick_font_size(n_cols: int) -> float:
    """Step the body font down as a table gets wider. A 14-column table at 8pt
    wraps to one word per line; at 5.5pt it stays legible and compact."""
    if n_cols <= 6:
        return 8.0
    if n_cols <= 9:
        return 7.0
    if n_cols <= 12:
        return 6.0
    return 5.5


def col_widths(pdf: FPDF, columns: list, rows: list, font_size: float) -> list[float]:
    """Measured widths that sum EXACTLY to the printable width, allocated
    MAX-MIN FAIR so no column is starved by a greedy neighbour.

    A flat proportional scale is not good enough: scaling every column by the
    same factor pushes the *narrow* ones below what they need, so a `Date`
    wraps to "2026-08-0 / 3" and `Site_ID` to "CNCE / C" purely to make room
    for a description that was going to wrap anyway.

    Water-filling instead. Repeatedly offer every unsatisfied column an equal
    share of what is left; any column needing less than its share is settled at
    exactly its need and drops out, releasing the slack. What remains is split
    among the genuinely wide columns — which are the only ones that then wrap.

    Widths are sampled over the first 400 rows: representative, and bounded for
    a 50k-row export.
    """
    n = len(columns)
    epw = pdf.w - pdf.l_margin - pdf.r_margin
    if n == 0:
        return []
    pdf.set_font("helvetica", "", font_size)
    needs = []
    for i, col in enumerate(columns):
        # Headers are bold, so allow ~15% over the regular-weight measurement.
        w = pdf.get_string_width(_latin(str(col).replace("_", " "))) * 1.15
        for r in rows[:400]:
            if i < len(r):
                w = max(w, pdf.get_string_width(_latin(fmt_value(r[i]))))
        needs.append(min(max(w + 2 * _PAD + 0.6, _MIN_COL_MM), _MAX_COL_MM))

    out = [0.0] * n
    unsettled = set(range(n))
    left = epw
    while unsettled:
        share = left / len(unsettled)
        small = {i for i in unsettled if needs[i] <= share}
        if not small:                      # everyone still wants more than a share
            for i in unsettled:
                out[i] = share
            break
        for i in small:
            out[i] = needs[i]
            left -= needs[i]
        unsettled -= small
    else:
        # Every column was satisfied and there is slack left over; hand it back
        # in proportion to need so the table still fills the page edge to edge.
        total = sum(out)
        if total > 0:
            out = [w * epw / total for w in out]
    return out


def _wrap(pdf: FPDF, text: str, w: float) -> list[str]:
    """Lines of `text` that each fit inside `w`, via fpdf2's own breaker (so
    what we measure is what it draws). Long unbreakable tokens are split
    character-wise rather than allowed to overflow.

    A cell is capped at _MAX_LINES; only a genuinely pathological value (a
    free-text note in a 12 mm column) can reach that, and it ellipses rather
    than turning one row into a full page.
    """
    t = _latin(text)
    if not t:
        return [""]
    # multi_cell reserves pdf.c_margin on EACH side of the box it is handed, so
    # the text actually gets `w - 2*c_margin`. get_string_width knows nothing
    # about that, so a value measured as fitting would still wrap ("2026-08-03"
    # → "2026-08-0" / "3"). Hand it back the margin it is about to take.
    inner = max(w - 2 * _PAD, 2.0) + 2 * pdf.c_margin
    lines = pdf.multi_cell(inner, _LINE_H, t, dry_run=True, output="LINES",
                           wrapmode="WORD")
    lines = list(lines) or [""]
    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]
        lines[-1] = (lines[-1][:-1] if len(lines[-1]) > 1 else lines[-1]) + "…"
    return lines


def _aligns(columns: list, rows: list) -> list[str]:
    """Right-align a column only if every sampled value in it is numeric."""
    out = []
    for i in range(len(columns)):
        vals = [r[i] for r in rows[:60] if i < len(r)]
        numeric = vals and all(
            (isinstance(v, (int, float)) and not isinstance(v, bool)) or v in (None, "")
            for v in vals)
        out.append("R" if numeric else "L")
    return out


def _header_row(pdf: BrandedPDF, columns: list, widths: list[float], font_size: float):
    pdf.set_font("helvetica", "B", font_size)
    cells = [_wrap(pdf, str(c).replace("_", " "), w) for c, w in zip(columns, widths)]
    h = max(len(c) for c in cells) * _LINE_H + 2.0
    if pdf.get_y() + h > pdf.page_break_trigger:
        pdf.add_page()
    _paint(pdf, cells, widths, ["C"] * len(widths), h, NAVY, (255, 255, 255))
    pdf.set_text_color(0, 0, 0)


def _paint(pdf: BrandedPDF, cells: list[list[str]], widths: list[float],
           aligns: list[str], h: float, bg: tuple | None, fg: tuple):
    """Draw one measured row: box every cell, then its wrapped lines, centred
    vertically. Text is inset by _PAD so it never touches a border."""
    x0, y0 = pdf.l_margin, pdf.get_y()
    x = x0
    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.15)
    pdf.set_text_color(*fg)
    for lines, w, a in zip(cells, widths, aligns):
        if bg is not None:
            pdf.set_fill_color(*bg)
            pdf.rect(x, y0, w, h, style="FD")
        else:
            pdf.rect(x, y0, w, h, style="D")
        y = y0 + (h - len(lines) * _LINE_H) / 2.0
        for ln in lines:
            pdf.set_xy(x + _PAD, y)
            pdf.cell(w - 2 * _PAD, _LINE_H, ln, align=a)
            y += _LINE_H
        x += w
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(x0, y0 + h)


def draw_table(pdf: BrandedPDF, columns: list, rows: list, *,
               title: str = "", subtitle: str = ""):
    """One fully-measured table: proportional widths, wrapped cells, repeating
    header, atomic rows, zebra striping."""
    if title:
        if pdf.get_y() + 24 > pdf.page_break_trigger:
            pdf.add_page()
        pdf.ln(2)
        pdf.set_font("helvetica", "B", 10.5)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 6.5, _latin(title), new_x="LMARGIN", new_y="NEXT")
        y = pdf.get_y()
        pdf.set_fill_color(*ACCENT)
        pdf.rect(pdf.l_margin, y, 20, 0.7, style="F")
        pdf.set_y(y + 1.8)
        if subtitle:
            pdf.set_font("helvetica", "", 7)
            pdf.set_text_color(*MUTED)
            pdf.cell(0, 4, _latin(subtitle), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    columns = list(columns)
    if not columns:
        return
    if not rows:
        pdf.set_font("helvetica", "I", 8)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6, "No data for this report.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        return

    fs = pick_font_size(len(columns))
    widths = col_widths(pdf, columns, rows, fs)
    aligns = _aligns(columns, rows)
    _header_row(pdf, columns, widths, fs)

    pdf.set_font("helvetica", "", fs)
    # Printable band on a fresh page — the guard below needs it so a row taller
    # than a whole page breaks out instead of paging forever.
    usable = pdf.page_break_trigger - pdf.t_margin
    for n, row in enumerate(rows):
        cells = [_wrap(pdf, fmt_value(row[i] if i < len(row) else ""), w)
                 for i, w in enumerate(widths)]
        h = max(len(c) for c in cells) * _LINE_H + 1.6
        # Atomic rows: move the whole row down rather than splitting it. The
        # `h <= usable` guard stops a row taller than a page looping forever.
        if pdf.get_y() + h > pdf.page_break_trigger and h <= usable:
            pdf.add_page()
            _header_row(pdf, columns, widths, fs)
            pdf.set_font("helvetica", "", fs)
        _paint(pdf, cells, widths, aligns, h, ZEBRA if n % 2 else None, (0, 0, 0))

    pdf.set_font("helvetica", "", 6.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 4.5, f"{len(rows):,} row(s)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


def render_table_pdf(title: str, sheets: list, username: str, *,
                     page_break_between: bool = False) -> bytes:
    """`sheets` = [(section_title, columns, rows)] → branded multi-page PDF.

    A single-table report is just a one-section list; `reports.to_pdf` passes
    one and `reports.to_pdf_sheets` passes many.
    """
    pdf = BrandedPDF(title=str(title), username=username)
    pdf.add_page()
    sections = list(sheets)
    if not sections:
        pdf.set_font("helvetica", "I", 9)
        pdf.cell(0, 8, "No data for this report.", new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output())
    for n, (sheet_title, columns, rows) in enumerate(sections):
        if page_break_between and n:
            pdf.add_page()
        draw_table(pdf, list(columns), list(rows),
                   title=str(sheet_title) if len(sections) > 1 or sheet_title else "")
    return bytes(pdf.output())
