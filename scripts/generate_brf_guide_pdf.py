#!/usr/bin/env python3
"""
Generate brf-economy-guide.pdf from brf-economy-guide.html.

Rendering pipeline:
  • Pages 1–4 use hard-coded layouts that mirror brf-economy-guide-original.pdf
    1:1 — exact positions, fonts, colors — via render_page1..render_page4.
    Text content (and QR-encoded URLs) is pulled from the current HTML.
  • Pages 5+ use the generic PageFlow engine in build_pdf, which walks
    extract_section_blocks(...) for Sparande continuation, sections 2–7, and
    trailing sections (stambyte focus, Källor, final QR cards). New HTML
    content inside a recognized section is picked up automatically.

Usage:
    python scripts/generate_brf_guide_pdf.py \\
        [--html brf-economy-guide.html] \\
        [--out brf-economy-guide.pdf]
"""
from __future__ import annotations

import argparse
import io
import re
from dataclasses import dataclass
from pathlib import Path

import qrcode
from bs4 import BeautifulSoup
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HTML = REPO_ROOT / "brf-economy-guide.html"
DEFAULT_PDF = REPO_ROOT / "brf-economy-guide.pdf"
DEFAULT_ORIG = REPO_ROOT / "brf-economy-guide-original.pdf"
DATA_DIR = REPO_ROOT / "data"

# Sparande calculation constants — mirrors brf-economy-guide.html JS.
TOTAL_KVM = 51666
SPARANDE_CF_CATS = {3, 8, 9, 10, 11, 12, 13, 14}
SPARANDE_UH_CATS = {16, 31}

# HSB threshold colors for bar chart (matches Plotly logic in HTML)
HSB_BAR_COLORS = {
    "very_low":  "#ef4444",  # < 120 kr/kvm
    "low":       "#f59e0b",  # 120 – 200
    "moderate":  "#22c55e",  # 200 – 300
    "high":      "#3b82f6",  # > 300
}

PAGE_W, PAGE_H = A4  # 595.276 x 841.890 pt

# ---------------------------------------------------------------------------
# Visual constants — extracted from brf-economy-guide-original.pdf page 1.
# (Coordinates converted from PyMuPDF top-down to ReportLab bottom-up.)
# ---------------------------------------------------------------------------

# Palette
COL_HEADER_LINE  = HexColor("#2b5e89")
COL_DIVIDER      = HexColor("#dddddd")
COL_TITLE        = HexColor("#1b3a5c")
COL_SUBTITLE     = HexColor("#2c5f8a")
COL_LEAD         = HexColor("#666666")
COL_BODY         = HexColor("#333333")
COL_HEADFOOT     = HexColor("#666666")
COL_BLUE_FILL    = HexColor("#e8eff8")
COL_BLUE_BORDER  = HexColor("#2b5e89")
COL_YELLOW_FILL  = HexColor("#fff8e6")
COL_YELLOW_BORD  = HexColor("#d4a842")
COL_CALLOUT_HEAD = HexColor("#7a5e1e")
COL_QR_BG        = HexColor("#f4f4f4")
COL_QR_BORDER    = HexColor("#dddddd")
COL_URL          = HexColor("#4a90c4")

# Horizontal anchors
CONTENT_LEFT  = 76.866   # body text starts here
CONTENT_RIGHT = 524.409  # right edge of header line / body text wrap
CARD_RIGHT    = 530.409  # right edge of cards / callout / QR boxes
HEADER_LEFT   = 70.866   # header & footer text/lines

# Header / footer (y values converted: rl_y = PAGE_H - mupdf_y)
HEADER_LINE_Y          = PAGE_H - 51.024
HEADER_TEXT_BASELINE_Y = PAGE_H - 47.7

FOOTER_LINE_Y          = PAGE_H - 785.197
FOOTER_TEXT_BASELINE_Y = PAGE_H - 801.8

# Title block
TITLE_BASELINE_Y    = PAGE_H - 134.0   # 26pt Helvetica-Bold; bbox bottom 139.2
SUBTITLE_BASELINE_Y = PAGE_H - 156.0   # 13pt Helvetica
TITLE_DIVIDER_Y     = PAGE_H - 179.724

# "Inledning" section heading
SECTION_HEADING_BASELINE_Y = PAGE_H - 222.0  # 20pt Helvetica-Bold

# Body paragraphs
LEAD_FIRST_LINE_Y = PAGE_H - 245.0   # 11pt Helvetica #666 (lead style)
LEAD_LINE_HEIGHT  = 16.0
LEAD_PARA_GAP     = 7.0              # gap between lead and dark paragraph

DARK_FIRST_LINE_Y = PAGE_H - 282.0   # 10.5pt Helvetica #333
DARK_LINE_HEIGHT  = 15.0

# Two-col cards
CARD_Y_TOP    = PAGE_H - 307.409
CARD_Y_BOTTOM = PAGE_H - 379.409
CARD_SPLIT_X  = 295.134
CARD_PAD_X    = 8.0     # inside-card horizontal padding
CARD_TITLE_BASELINE_Y = PAGE_H - 322.5
CARD_BODY_FIRST_LINE_Y = PAGE_H - 337.0
CARD_BODY_LINE_HEIGHT  = 14.0

# Callout box
CALLOUT_Y_TOP    = PAGE_H - 387.913
CALLOUT_Y_BOTTOM = PAGE_H - 480.937
CALLOUT_LEFT_ACCENT_W = 9.92  # x=76.87 to x=86.79
CALLOUT_PAD_X = 19.83  # title/body left edge at x≈96.7 → 19.83 from CONTENT_LEFT
CALLOUT_TITLE_BASELINE_Y = PAGE_H - 405.0
CALLOUT_BODY_FIRST_LINE_Y = PAGE_H - 430.0
CALLOUT_BODY_LINE_HEIGHT  = 14.0

# Body paragraph after callout
POST_CALLOUT_FIRST_LINE_Y = PAGE_H - 502.0

# QR reference cards
QR_CARD1_TOP    = PAGE_H - 533.614
QR_CARD1_BOTTOM = PAGE_H - 601.646
QR_CARD2_TOP    = PAGE_H - 607.315
QR_CARD2_BOTTOM = PAGE_H - 675.346

QR_IMG_LEFT          = 85.370
QR_IMG_RIGHT         = 142.063
QR_CARD1_IMG_TOP     = PAGE_H - 539.283
QR_CARD1_IMG_BOTTOM  = PAGE_H - 595.976
QR_CARD2_IMG_TOP     = PAGE_H - 612.984
QR_CARD2_IMG_BOTTOM  = PAGE_H - 669.677

QR_TEXT_X            = 156.2
QR_CARD1_TITLE_Y     = PAGE_H - 555.6
QR_CARD1_URL1_Y      = PAGE_H - 572.4
QR_CARD1_URL2_Y      = PAGE_H - 583.7
QR_CARD1_INSTR_Y     = PAGE_H - 595.1
QR_CARD2_TITLE_Y     = PAGE_H - 629.3
QR_CARD2_URL_Y       = PAGE_H - 646.1
QR_CARD2_INSTR_Y     = PAGE_H - 668.8

# Divider + HSB heading (start of next section, visible on page 1)
HSB_DIVIDER_Y           = PAGE_H - 683.850
HSB_HEADING_BASELINE_Y  = PAGE_H - 725.0  # 20pt Helvetica-Bold

# Web-page URL for QR card 2 (does not change between editions)
WEB_GUIDE_URL = "https://visionmyggan.se/sjotungan-analytics/brf-economy-guide.html"

# ---------------------------------------------------------------------------
# Page 2 constants — extracted from brf-economy-guide-original.pdf page 2.
# ---------------------------------------------------------------------------

# Colors specific to page 2
COL_NYCKELTAL_HEADER = HexColor("#1a3a5b")  # dark navy card header
COL_NYCKELTAL_BODY   = HexColor("#f8f6f2")  # cream card body
COL_BADGE            = HexColor("#d4a842")  # gold circle (same as callout border)

# Body paragraph (3 lines, 10.5pt #333)
P2_BODY_FIRST_LINE_Y = PAGE_H - 115.0   # 10.5pt body text
P2_BODY_LINE_HEIGHT  = 15.0

# Single HSB QR reference card
P2_QR_TOP        = PAGE_H - 161.551
P2_QR_BOTTOM     = PAGE_H - 229.583
P2_QR_IMG_TOP    = PAGE_H - 167.220
P2_QR_IMG_BOTTOM = PAGE_H - 223.913
P2_QR_TITLE_Y    = PAGE_H - 183.5
P2_QR_URL_Y      = PAGE_H - 200.3
P2_QR_INSTR_Y    = PAGE_H - 223.0

# Sub-section heading "De sju nyckeltalen i översikt"
P2_SUBSECTION_BASELINE_Y = PAGE_H - 269.0  # 14pt Helvetica-Bold #2c5f8a

# 7-card numbered grid
# Row 1 (4 cards): y_top=284.43, y_bottom=363.80, height 79.37
# Row 2 (3 cards): y_top=372.31, y_bottom=451.68, height 79.37
P2_ROW1_TOP    = PAGE_H - 284.433
P2_ROW1_BOTTOM = PAGE_H - 363.803
P2_ROW2_TOP    = PAGE_H - 372.307
P2_ROW2_BOTTOM = PAGE_H - 451.677

# Card body fill split (header band ends at y=328.087 in PDF coords)
P2_CARD_HEADER_BOTTOM_PDF = 328.087   # PDF (top-down) y where header ends → body starts
# Header height fraction of total card: (328.087 - 284.433) / (363.803 - 284.433) = ~0.55

# Card column x-positions for row 1 (4 cards)
P2_ROW1_X = [
    (76.866, 183.874),
    (192.378, 299.386),
    (307.890, 414.898),
    (423.402, 530.409),
]
# Card column x-positions for row 2 (3 cards)
P2_ROW2_X = [
    (76.866, 222.378),
    (230.882, 376.394),
    (384.898, 530.409),
]

# Badge sizes & positions (gold circle, radius 14.17pt, centered on card width,
# offset 19.65pt down from card top in PDF coords)
P2_BADGE_DIAMETER = 28.346
P2_BADGE_OFFSET_FROM_TOP = 19.65  # from card top (PDF down) to top of badge bbox

# Text inside cards (original PDF uses asymmetric padding: ~8.5pt left, ~3pt right,
# giving narrower row-1 cards just enough room for two-line descriptions).
P2_CARD_PAD_LEFT  = 8.5
P2_CARD_PAD_RIGHT = 3.0
P2_CARD_TITLE_BASELINE_OFFSET = 14.0  # from card_body_top_pdf to title baseline (PDF y add)
P2_CARD_DESC_LINE1_OFFSET = 24.0
P2_CARD_DESC_LINE_HEIGHT  = 9.9

# Closing paragraph after grid
P2_CLOSING_FIRST_LINE_Y = PAGE_H - 476.5

# Bottom divider line on page 2
P2_BOTTOM_DIVIDER_Y = PAGE_H - 510.024

# ---------------------------------------------------------------------------
# Page 3 constants — "1. Sparande" detail page.
# ---------------------------------------------------------------------------

# Wide section card (encloses badge, title, body, formula pill, benchmarks, footnotes)
P3_SECTION_CARD_TOP    = PAGE_H - 105.213
P3_SECTION_CARD_BOTTOM = PAGE_H - 360.468

# Dark navy header band inside the section card
P3_HEADER_BAND_TOP_PDF    = 105.213
P3_HEADER_BAND_BOTTOM_PDF = 136.394

# Section badge (gold circle) — diameter 22.68pt (smaller than page-2 cards)
P3_BADGE_DIAMETER = 22.68
P3_BADGE_LEFT_PDF = 91.039   # PDF x of badge bbox left
P3_BADGE_TOP_PDF  = 109.465  # PDF y of badge bbox top

# Section title in header band
P3_TITLE_BASELINE_Y = PAGE_H - 128.0  # 13pt Helvetica-Bold #fff

# Body paragraph inside section card
P3_BODY_FIRST_LINE_Y = PAGE_H - 158.0
P3_BODY_LINE_HEIGHT  = 13.5
P3_BODY_PAD_LEFT     = 19.83  # matches page-1 callout: x_left=96.7 from CONTENT_LEFT=76.87
P3_BODY_PAD_RIGHT    = 32.0   # tighter than left to match original wrap

# Gray formula pill (the "Beräkning: ..." gray rounded rect)
P3_PILL_TOP    = PAGE_H - 199.571
P3_PILL_BOTTOM = PAGE_H - 219.413
P3_PILL_LEFT   = 96.709
P3_PILL_RIGHT  = 510.567
P3_PILL_TEXT_BASELINE_Y = PAGE_H - 215.7

# Benchmark sub-heading "Riktvärden (HSB Bostadsrättskollen):"
P3_RIKTVARDE_HEADING_BASELINE_Y = PAGE_H - 233.5

# Benchmark rows (4 fixed positions, color circles + label + value)
P3_BENCHMARK_CIRCLE_DIAMETER = 14.17
P3_BENCHMARK_CIRCLE_X_CENTER = 108.047
P3_BENCHMARK_LABEL_X = 122.2
P3_BENCHMARK_VALUE_X = 218.6
P3_BENCHMARK_ROW_Y_CENTERS = [
    PAGE_H - 247.76,  # row 1 (Högt)
    PAGE_H - 264.77,  # row 2 (Måttligt till högt)
    PAGE_H - 281.78,  # row 3 (Lågt till måttligt)
    PAGE_H - 298.78,  # row 4 (Mycket lågt)
]
P3_BENCHMARK_BASELINE_OFFSET = -3.3  # text baseline vs circle center (RL: subtract)

# 4 standard benchmark colors (HSB scale)
BENCHMARK_COLORS = [
    HexColor("#499c59"),  # green     — best
    HexColor("#8cb83d"),  # yellow-green
    HexColor("#c3a52f"),  # gold
    HexColor("#c34f4f"),  # red       — worst
]

# Footnote lines (italic gray, inside section card)
P3_FOOTNOTE1_BASELINE_Y = PAGE_H - 326.7
P3_FOOTNOTE2_BASELINE_Y = PAGE_H - 337.7

# Section subheading "Vad betyder sparande i praktiken?" (h3 style)
P3_H3_VAD_BETYDER_BASELINE_Y = PAGE_H - 398.8
H3_FONT_SIZE = 12  # used across all detail pages

# Body paragraph after first h3 (3 lines: "Ordet 'sparande'...")
P3_ORDET_FIRST_LINE_Y = PAGE_H - 418.5

# Blue callout "Hushållsanalogi"
P3_BLUE_CALLOUT_TOP    = PAGE_H - 461.154
P3_BLUE_CALLOUT_BOTTOM = PAGE_H - 652.177
P3_BLUE_CALLOUT_LEFT_ACCENT_W = 9.92

P3_CALLOUT_TITLE_BASELINE_Y = PAGE_H - 484.4
P3_CALLOUT_PARA1_FIRST_LINE_Y = PAGE_H - 508.2
P3_CALLOUT_PARA2_FIRST_LINE_Y = PAGE_H - 550.2
P3_CALLOUT_PARA3_FIRST_LINE_Y = PAGE_H - 606.2
P3_CALLOUT_LINE_HEIGHT = 14.0

# Body paragraph after callout ("Med detta i åtanke...")
P3_MED_DETTA_BASELINE_Y = PAGE_H - 677.2

# Section subheading "BRF Sjötungans sparande"
P3_H3_SJOTUNGANS_BASELINE_Y = PAGE_H - 716.9

# Body paragraph after second h3 (2 lines visible on page 3)
P3_FLERAR_FIRST_LINE_Y = PAGE_H - 736.5

# ---------------------------------------------------------------------------
# Page 4 constants — Sparande data table + historical bar chart.
# ---------------------------------------------------------------------------

# Data table at top of page 4
P4_TABLE_LEFT  = HEADER_LEFT       # 70.866
P4_TABLE_RIGHT = CONTENT_RIGHT     # 524.409
P4_TABLE_ROW_HEIGHT = 22.0
P4_TABLE_HEADER_TOP    = PAGE_H - 105.213
P4_TABLE_HEADER_BOTTOM = PAGE_H - 127.213  # row 1 (dark navy)
P4_TABLE_ROW1_BOTTOM   = PAGE_H - 149.213  # row 2 (light blue) — Sparande kr/kvm
P4_TABLE_ROW2_BOTTOM   = PAGE_H - 171.213  # row 3 (light gray) — HSB:s bedömning
# Vertical column separators (left edges of each cell)
P4_TABLE_COL_X = [70.866, 216.000, 293.102, 370.205, 447.307, 524.409]
P4_TABLE_HEADER_TEXT_BASELINE_Y = PAGE_H - 122.6  # 9.5pt Helvetica-Bold #fff
P4_TABLE_ROW1_TEXT_BASELINE_Y   = PAGE_H - 144.6  # 9.5pt Helvetica
P4_TABLE_ROW2_TEXT_BASELINE_Y   = PAGE_H - 164.5  # 8pt Helvetica-Oblique #666

# Navigation source-ref ("■ Var i årsredovisningen...") in light blue
P4_NAV_FIRST_LINE_Y = PAGE_H - 184.4
P4_NAV_LINE_HEIGHT  = 11.0

# Body paragraph (3 lines)
P4_BODY_FIRST_LINE_Y = PAGE_H - 215.4
P4_BODY_LINE_HEIGHT  = 15.0

# Chart image position (in PDF coords from original)
P4_CHART_LEFT   = 84.818
P4_CHART_RIGHT  = 510.458
P4_CHART_TOP_RL    = PAGE_H - 260.890
P4_CHART_BOTTOM_RL = PAGE_H - 464.984

# Chart caption (italic, centered)
P4_CHART_CAPTION_Y = PAGE_H - 475.3

# h3 "Vad bygger sparandet?"
P4_H3_VAD_BYGGER_Y = PAGE_H - 515.3

# Body paragraph (4 lines) after h3
P4_BYGGS_FIRST_LINE_Y = PAGE_H - 535.0
P4_HAR_AR_BASELINE_Y  = PAGE_H - 600.6

# ---------------------------------------------------------------------------
# Content model — what we extract from the HTML for page 1.
# ---------------------------------------------------------------------------


@dataclass
class Page1Content:
    title: str
    subtitle: str
    section_heading: str
    lead_paragraph: str
    body_paragraph: str
    card_left_title: str
    card_left_body: str
    card_right_title: str
    card_right_body: str
    callout_title: str
    callout_body: str
    post_callout_paragraph: str
    annual_report_url: str   # for QR card 1
    annual_report_label: str # e.g. "BRF Sjötungans årsredovisning 2025 (stämmohandlingar)"
    next_section_heading: str  # "HSB:s Bostadsrättskollen"


# A "rich" paragraph is a list of (text, is_bold) runs.
RichText = list[tuple[str, bool]]


@dataclass
class NyckeltalCard:
    number: str
    label: str
    desc: str


@dataclass
class Page2Content:
    intro_paragraph: RichText
    subsection_heading: str        # "De sju nyckeltalen i översikt"
    cards: list[NyckeltalCard]     # 7 cards in order
    closing_paragraph: str
    hsb_qr_url: str
    hsb_qr_title: str


@dataclass
class BenchmarkRow:
    label: str   # e.g. "Högt"
    value: str   # e.g. "> 301 kr/kvm"


@dataclass
class SectionCardContent:
    number: str                    # "1"
    title: str                     # "Sparande"
    body: RichText                 # intro body paragraph (inline bold ok)
    formula: str                   # e.g. "Beräkning: (Årets resultat + ...) / totalyta (kvm)"
    benchmark_heading: str         # "Riktvärden (HSB Bostadsrättskollen):"
    benchmarks: list[BenchmarkRow] # 4 rows in HSB order (best → worst)
    footnote_intro: str            # "Enligt HSB:s skala är ..."
    footnote_source: str           # "Källa: HSB Bostadsrättskollen ..."


@dataclass
class BlueCalloutContent:
    title: str                     # "Hushållsanalogi"
    paragraphs: list[RichText]     # one per <br><br>-separated block


@dataclass
class Page3Content:
    section_card: SectionCardContent
    h3_vad_betyder: str                       # "Vad betyder sparande i praktiken?"
    ordet_paragraph: RichText                 # "Ordet 'sparande' ..."
    callout: BlueCalloutContent
    med_detta_paragraph: str
    h3_sjotungans: str                        # "BRF Sjötungans sparande"
    flerar_paragraph: RichText                # "I flerårsöversikten ..."


@dataclass
class DataTable:
    """Year-comparison data table at top of detail pages."""
    column_headers: list[str]      # e.g. ["", "2025", "2024", "2023", "2022"] — first is row-label column
    rows: list[list[str]]          # each row: [label, val1, val2, val3, val4]


@dataclass
class Page4Content:
    table: DataTable
    nav_paragraph: str             # "Var i årsredovisningen: ..." (light blue navigation hint)
    body_paragraph: RichText       # "Tittar man längre tillbaka ..."
    chart_caption: str             # "Sparande per kvm för BRF Sjötungan, 2003–YYYY. Källa: årsredovisningar."
    h3_vad_bygger: str             # "Vad bygger sparandet?"
    byggs_paragraph: RichText      # body after h3 (4 lines)
    har_ar_paragraph: str          # "Här är beräkningen med Sjötungans siffror för YYYY:"
    chart_year_range: tuple[int, int]  # (min_year, max_year) for the bar chart


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _strip_leading_emoji(text: str) -> str:
    # Card titles in HTML are "🏠 Ditt boende" — original PDF drops the emoji.
    out = []
    for ch in text:
        if ord(ch) >= 0x1F000 or ch in {"✨", "✅"}:
            continue
        out.append(ch)
    return _clean("".join(out))


_BOLD_TAGS = {"strong", "b", "em"}  # original PDF renders <em> as bold too


def _parse_rich_paragraph(p_tag) -> RichText:
    """Walk a <p> and emit (text, is_bold) runs, treating <strong>/<b>/<em> as bold."""
    runs: RichText = []

    def visit(node, bold: bool):
        if isinstance(node, str):
            text = _clean(node)
            if text:
                # Preserve leading/trailing space context: keep single spaces around runs
                runs.append((node, bold))
            return
        if not hasattr(node, "name") or node.name is None:
            return
        child_bold = bold or (node.name.lower() in _BOLD_TAGS)
        for child in node.children:
            visit(child, child_bold)

    for child in p_tag.children:
        visit(child, False)

    # Coalesce consecutive same-style runs, normalize whitespace.
    merged: RichText = []
    for raw, bold in runs:
        text = _WS_RE.sub(" ", raw)
        if merged and merged[-1][1] == bold:
            merged[-1] = (merged[-1][0] + text, bold)
        else:
            merged.append((text, bold))
    # Strip leading whitespace of the very first run and trailing of the last.
    if merged:
        merged[0] = (merged[0][0].lstrip(), merged[0][1])
        merged[-1] = (merged[-1][0].rstrip(), merged[-1][1])
    # Drop empty runs.
    return [(t, b) for t, b in merged if t]


def _find_section(soup, h2_predicate) -> object:
    for div in soup.find_all("div", class_="guide-section"):
        h2 = div.find("h2")
        if h2 and h2_predicate(_clean(h2.get_text())):
            return div
    return None


def _split_source_ref(text: str) -> tuple[str, str]:
    """Split a source-ref paragraph into (intro_sentence, source_sentence) on 'Källa:'."""
    idx = text.find("Källa:")
    if idx == -1:
        return text, ""
    return text[:idx].strip(), text[idx:].strip()


def _parse_section_card(section_div) -> SectionCardContent:
    """Parse the top of a numbered HSB-section: title, body, formula, benchmark table, footnotes."""
    h2 = section_div.find("h2")
    title_text = _clean(h2.get_text())
    # h2 looks like "1. Sparande"
    m = re.match(r"\s*(\d+)\.\s*(.+)", title_text)
    if m:
        number, title = m.group(1), m.group(2)
    else:
        number, title = "", title_text

    # First <p> is the body intro
    paragraphs = section_div.find_all("p", recursive=False)
    body_p = paragraphs[0] if paragraphs else None
    body = _parse_rich_paragraph(body_p) if body_p else []

    # Second <p> typically holds the "Beräkning:" formula
    formula = ""
    if len(paragraphs) >= 2 and "Beräkning" in paragraphs[1].get_text():
        formula = _clean(paragraphs[1].get_text())

    # Benchmark heading (h3 above the riktvarde-table)
    benchmark_heading = ""
    riktvarde_table = section_div.find("table", class_="riktvarde-table")
    if riktvarde_table:
        prev_h3 = riktvarde_table.find_previous_sibling("h3")
        if prev_h3:
            benchmark_heading = _clean(prev_h3.get_text())
            if not benchmark_heading.endswith(":"):
                benchmark_heading = benchmark_heading + ":"

    # Benchmark rows from the table body + unit from <th>
    benchmarks: list[BenchmarkRow] = []
    if riktvarde_table:
        unit = ""
        th_cells = riktvarde_table.find("thead").find_all("th") if riktvarde_table.find("thead") else []
        if len(th_cells) >= 2:
            unit = _clean(th_cells[1].get_text())  # e.g. "kr/kvm" — but may be category label like "Risk"
        # Only treat as a unit if it looks like one (slash, %, "år"); otherwise it's a category column.
        is_unit = bool(unit) and ("/" in unit or unit in {"%", "år", "ÅR"})
        for tr in riktvarde_table.find("tbody").find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            label = _clean(tds[0].get_text())
            value_raw = _clean(tds[1].get_text())
            # Append unit only if it's a real unit AND the value doesn't already include it.
            if is_unit and unit not in value_raw:
                value = f"{value_raw} {unit}".strip()
            else:
                value = value_raw
            benchmarks.append(BenchmarkRow(label=label, value=value))

    # Footnote source-ref paragraph (the one immediately after riktvarde-table)
    footnote_intro = ""
    footnote_source = ""
    if riktvarde_table:
        for sib in riktvarde_table.find_next_siblings():
            if getattr(sib, "name", None) == "p" and "source-ref" in (sib.get("class") or []):
                footnote_intro, footnote_source = _split_source_ref(_clean(sib.get_text()))
                break
            if getattr(sib, "name", None) == "h3":
                break  # entered next subsection

    return SectionCardContent(
        number=number,
        title=title,
        body=body,
        formula=formula,
        benchmark_heading=benchmark_heading,
        benchmarks=benchmarks,
        footnote_intro=footnote_intro,
        footnote_source=footnote_source,
    )


def _parse_callout(callout_div) -> BlueCalloutContent:
    """Parse a <div class='callout'> into (title, [paragraph rich-text, ...])."""
    # Title is the leading <strong>...</strong> ending with ":" (if present).
    strong = callout_div.find("strong")
    title = ""
    if strong:
        t = _clean(strong.get_text()).rstrip(":")
        title = t
        strong.extract()  # remove from tree so we don't include it in body

    # Split on <br><br> — bs4 represents <br> as a separate tag in the children iter.
    paragraphs: list[RichText] = []
    current_runs: RichText = []
    br_count = 0

    def flush():
        nonlocal current_runs
        if current_runs:
            # Coalesce + strip like _parse_rich_paragraph
            merged: RichText = []
            for raw, bold in current_runs:
                text = _WS_RE.sub(" ", raw)
                if merged and merged[-1][1] == bold:
                    merged[-1] = (merged[-1][0] + text, bold)
                else:
                    merged.append((text, bold))
            if merged:
                merged[0] = (merged[0][0].lstrip(), merged[0][1])
                merged[-1] = (merged[-1][0].rstrip(), merged[-1][1])
            cleaned = [(t, b) for t, b in merged if t]
            if cleaned:
                paragraphs.append(cleaned)
        current_runs = []

    def visit(node, bold: bool):
        nonlocal br_count
        if isinstance(node, str):
            text = node
            if text.strip():
                br_count = 0
                current_runs.append((text, bold))
            else:
                current_runs.append((text, bold))
            return
        if not hasattr(node, "name") or node.name is None:
            return
        name_l = node.name.lower()
        if name_l == "br":
            br_count += 1
            if br_count >= 2:
                flush()
                br_count = 0
            return
        if name_l == "p":
            # Treat <p> boundaries as paragraph breaks too (stambyte callouts use these).
            flush()
            br_count = 0
            child_bold = bold or (name_l in _BOLD_TAGS)
            for child in node.children:
                visit(child, child_bold)
            flush()
            return
        child_bold = bold or (name_l in _BOLD_TAGS)
        for child in node.children:
            visit(child, child_bold)

    for child in callout_div.children:
        visit(child, False)
    flush()

    return BlueCalloutContent(title=title, paragraphs=paragraphs)


def parse_html(html_path: Path) -> tuple[Page1Content, Page2Content, Page3Content, Page4Content]:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    # Page-level title + subtitle live inside <section> ... <h1>...</h1>
    h1 = soup.find("h1")
    title = _clean(h1.get_text()) if h1 else "Förstå din förenings ekonomi"
    subtitle = "En guide för medlemmar i BRF Sjötungan"  # not present verbatim in HTML

    # Find the "Inledning" guide-section.
    inledning = _find_section(soup, lambda t: t == "Inledning")
    if inledning is None:
        raise SystemExit("Could not find <div class='guide-section'> with <h2>Inledning</h2>")

    section_heading = "Inledning"

    # Two paragraphs before the two-col block.
    paragraphs = inledning.find_all("p", recursive=False)
    if len(paragraphs) < 2:
        raise SystemExit("Inledning section: expected >=2 top-level <p> children")
    lead_paragraph = _clean(paragraphs[0].get_text())
    body_paragraph = _clean(paragraphs[1].get_text())

    # Two-column cards.
    two_col = inledning.find("div", class_="two-col")
    if not two_col:
        raise SystemExit("Inledning section: missing <div class='two-col'>")
    cards = two_col.find_all("div", class_="two-col-card")
    if len(cards) < 2:
        raise SystemExit("Inledning section: expected 2 two-col-card divs")
    card_left_title = _strip_leading_emoji(cards[0].find(["h3", "h4"]).get_text())
    card_left_body = _clean(cards[0].find("p").get_text())
    card_right_title = _strip_leading_emoji(cards[1].find(["h3", "h4"]).get_text())
    card_right_body = _clean(cards[1].find("p").get_text())

    # Callout (class is "callout-blue" in HTML even though rendered yellow in PDF).
    callout = inledning.find("div", class_=re.compile(r"callout"))
    if not callout:
        raise SystemExit("Inledning section: missing callout div")
    callout_text = _clean(callout.get_text())
    # Original PDF splits the callout into a bold title "Nya lagkrav från 2023"
    # and the body. The HTML embeds the title inside <strong>...:</strong>.
    strong = callout.find("strong")
    if strong:
        callout_title = _clean(strong.get_text()).rstrip(":")
        # remove the strong's text (with trailing colon) from the full text
        body = callout_text.replace(_clean(strong.get_text()), "", 1).strip()
        body = body.lstrip(":").strip()
        callout_body = body
    else:
        callout_title = "Nya lagkrav från 2023"
        callout_body = callout_text

    # The last <p> in Inledning carries the annual-report link.
    post_p = paragraphs[-1]
    post_callout_paragraph = _clean(post_p.get_text())
    a = post_p.find("a")
    if a and a.has_attr("href"):
        annual_report_url = a["href"]
        # Label used inside the QR card: "<linked text> (stämmohandlingar)"
        annual_report_label = f"{_clean(a.get_text())} (stämmohandlingar)"
    else:
        annual_report_url = ""
        annual_report_label = "BRF Sjötungans årsredovisning (stämmohandlingar)"

    # Next section heading.
    hsb_section = _find_section(soup, lambda t: t.startswith("HSB"))
    next_section_heading = (
        _clean(hsb_section.find("h2").get_text()) if hsb_section else "HSB:s Bostadsrättskollen"
    )

    page1 = Page1Content(
        title=title,
        subtitle=subtitle,
        section_heading=section_heading,
        lead_paragraph=lead_paragraph,
        body_paragraph=body_paragraph,
        card_left_title=card_left_title,
        card_left_body=card_left_body,
        card_right_title=card_right_title,
        card_right_body=card_right_body,
        callout_title=callout_title,
        callout_body=callout_body,
        post_callout_paragraph=post_callout_paragraph,
        annual_report_url=annual_report_url,
        annual_report_label=annual_report_label,
        next_section_heading=next_section_heading,
    )

    # ---- Page 2: HSB section ----
    if hsb_section is None:
        raise SystemExit("Could not find HSB guide-section")

    # First <p> (intro) — keep bold/em styling
    intro_p = hsb_section.find("p", recursive=False)
    intro_rich = _parse_rich_paragraph(intro_p) if intro_p else []

    # Sub-heading "De sju nyckeltalen i översikt"
    h3 = hsb_section.find("h3")
    subsection_heading = _clean(h3.get_text()) if h3 else "De sju nyckeltalen i översikt"

    # 7 nyckeltal cards
    cards: list[NyckeltalCard] = []
    grid = hsb_section.find("div", class_="nyckeltal-grid")
    if grid:
        for card_div in grid.find_all("div", class_="nyckeltal-card"):
            num_div = card_div.find("div", class_="number")
            label_div = card_div.find("div", class_="label")
            desc_div = card_div.find("div", class_="desc")
            cards.append(
                NyckeltalCard(
                    number=_clean(num_div.get_text()) if num_div else "",
                    label=_clean(label_div.get_text()) if label_div else "",
                    desc=_clean(desc_div.get_text()) if desc_div else "",
                )
            )
    if len(cards) != 7:
        raise SystemExit(f"Expected 7 nyckeltal cards in HSB section, found {len(cards)}")

    # Closing <p> + source-ref link → QR card
    closing_p = None
    source_ref = None
    for p in hsb_section.find_all("p", recursive=False):
        if "source-ref" in (p.get("class") or []):
            source_ref = p
        else:
            # last non-source-ref <p> after the intro
            if p is not intro_p:
                closing_p = p
    closing_paragraph = _clean(closing_p.get_text()) if closing_p else ""

    hsb_qr_url = "https://www.hsb.se/bostadsrattskollen/nyckeltal/"
    hsb_qr_title = "HSB Bostadsrättskollen — Nyckeltal"
    if source_ref:
        a = source_ref.find("a")
        if a and a.has_attr("href"):
            hsb_qr_url = a["href"]
            hsb_qr_title = _clean(a.get_text())

    page2 = Page2Content(
        intro_paragraph=intro_rich,
        subsection_heading=subsection_heading,
        cards=cards,
        closing_paragraph=closing_paragraph,
        hsb_qr_url=hsb_qr_url,
        hsb_qr_title=hsb_qr_title,
    )

    # ---- Page 3: "1. Sparande" detail page ----
    sparande_section = _find_section(soup, lambda t: t.startswith("1. Sparande"))
    if sparande_section is None:
        raise SystemExit("Could not find '1. Sparande' guide-section")

    section_card = _parse_section_card(sparande_section)

    # Find <h3>Vad betyder sparande i praktiken?</h3> and the following <p>
    h3_vad_betyder = ""
    ordet_paragraph: RichText = []
    callout_content: BlueCalloutContent | None = None
    med_detta_paragraph = ""
    h3_sjotungans = ""
    flerar_paragraph: RichText = []

    children = list(sparande_section.children)
    seen_vad_betyder = False
    seen_sjotungans = False
    for child in children:
        if not hasattr(child, "name") or child.name is None:
            continue
        if child.name == "h3":
            txt = _clean(child.get_text())
            if "Vad betyder" in txt:
                h3_vad_betyder = txt
                seen_vad_betyder = True
                seen_sjotungans = False
            elif "Sjötungans sparande" in txt:
                h3_sjotungans = txt
                seen_sjotungans = True
            else:
                seen_vad_betyder = False
                seen_sjotungans = False
        elif child.name == "p":
            if seen_vad_betyder and not ordet_paragraph and "Ordet" in child.get_text():
                ordet_paragraph = _parse_rich_paragraph(child)
            elif callout_content and not med_detta_paragraph and "Med detta" in child.get_text():
                med_detta_paragraph = _clean(child.get_text())
            elif seen_sjotungans and not flerar_paragraph and "flerårsöversikten" in child.get_text():
                flerar_paragraph = _parse_rich_paragraph(child)
        elif child.name == "div" and "callout" in (child.get("class") or []):
            if callout_content is None and seen_vad_betyder:
                callout_content = _parse_callout(child)

    if callout_content is None:
        raise SystemExit("Could not find Hushållsanalogi callout in Sparande section")

    page3 = Page3Content(
        section_card=section_card,
        h3_vad_betyder=h3_vad_betyder,
        ordet_paragraph=ordet_paragraph,
        callout=callout_content,
        med_detta_paragraph=med_detta_paragraph,
        h3_sjotungans=h3_sjotungans,
        flerar_paragraph=flerar_paragraph,
    )

    # ---- Page 4: Sparande data table + chart ----
    # The Sparande-history table lives in the same guide-section as the section-card,
    # below the "BRF Sjötungans sparande" h3.
    page4_table = DataTable(column_headers=[""], rows=[])
    nav_paragraph = ""
    body_paragraph: RichText = []
    h3_vad_bygger = ""
    byggs_paragraph: RichText = []
    har_ar_paragraph = ""
    chart_caption = ""

    tables = sparande_section.find_all("table", class_="riktvarde-table")
    # The first riktvarde-table is the HSB benchmark thresholds (page 3),
    # the second is the year-comparison data table (page 4).
    if len(tables) >= 2:
        data_table = tables[1]
        head_cells = data_table.find("thead").find_all("th") if data_table.find("thead") else []
        page4_table.column_headers = [_clean(th.get_text()) for th in head_cells]
        for tr in data_table.find("tbody").find_all("tr"):
            cells = [_clean(td.get_text()) for td in tr.find_all("td")]
            page4_table.rows.append(cells)

        # The <p class="source-ref"> immediately after this table is the navigation hint
        nxt = data_table.find_next_sibling()
        while nxt is not None:
            if getattr(nxt, "name", None) == "p" and "source-ref" in (nxt.get("class") or []):
                nav_paragraph = _clean(nxt.get_text())
                break
            nxt = nxt.find_next_sibling()

    # Body paragraph after table ("Sparandet ökade kraftigt under 2025..." or similar)
    # is the first non-source-ref <p> after the data table.
    seen_data_table = False
    seen_vad_bygger = False
    for child in sparande_section.children:
        if not hasattr(child, "name") or child.name is None:
            continue
        if child.name == "table" and "riktvarde-table" in (child.get("class") or []):
            tabledict = list(child.find("thead").find_all("th")) if child.find("thead") else []
            if tabledict and "2025" in tabledict[1].get_text() if len(tabledict) > 1 else False:
                seen_data_table = True
            elif "2024" in (tabledict[1].get_text() if len(tabledict) > 1 else ""):
                seen_data_table = True
        if seen_data_table and child.name == "p":
            classes = child.get("class") or []
            if "source-ref" in classes:
                continue
            if not body_paragraph and "Sparandet" in child.get_text():
                body_paragraph = _parse_rich_paragraph(child)
        if child.name == "h3":
            txt = _clean(child.get_text())
            if "Vad bygger" in txt:
                h3_vad_bygger = txt
                seen_vad_bygger = True
            else:
                seen_vad_bygger = False
        if seen_vad_bygger and child.name == "p":
            if not byggs_paragraph and "Sparande = intäkter" in child.get_text():
                byggs_paragraph = _parse_rich_paragraph(child)
            elif byggs_paragraph and not har_ar_paragraph and "Här är beräkningen" in child.get_text():
                har_ar_paragraph = _clean(child.get_text())

    # The chart's source-ref caption — find the source-ref p that mentions "Sparande per kvm"
    for p in sparande_section.find_all("p", class_="source-ref"):
        t = _clean(p.get_text())
        if t.startswith("Sparande per kvm"):
            chart_caption = t
            break

    sparande_series = compute_sparande_per_kvm()
    chart_year_range = (sparande_series[0][0], sparande_series[-1][0]) if sparande_series else (2003, 2025)

    page4 = Page4Content(
        table=page4_table,
        nav_paragraph=nav_paragraph,
        body_paragraph=body_paragraph,
        chart_caption=chart_caption or f"Sparande per kvm för BRF Sjötungan, {chart_year_range[0]}–{chart_year_range[1]}. Källa: årsredovisningar.",
        h3_vad_bygger=h3_vad_bygger,
        byggs_paragraph=byggs_paragraph,
        har_ar_paragraph=har_ar_paragraph,
        chart_year_range=chart_year_range,
    )

    return page1, page2, page3, page4


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def wrap_text(c: Canvas, text: str, font: str, size: float, max_width: float) -> list[str]:
    """Greedy word-wrap based on canvas string-width metrics."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if c.stringWidth(candidate, font, size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_paragraph(
    c: Canvas,
    text: str,
    x: float,
    first_baseline_y: float,
    *,
    font: str,
    size: float,
    color: HexColor,
    max_width: float,
    line_height: float,
) -> float:
    """Draw a word-wrapped paragraph. Returns y of last drawn baseline."""
    c.setFont(font, size)
    c.setFillColor(color)
    y = first_baseline_y
    for line in wrap_text(c, text, font, size, max_width):
        c.drawString(x, y, line)
        y -= line_height
    return y + line_height  # last baseline used


def draw_rich_paragraph(
    c: Canvas,
    runs: RichText,
    x: float,
    first_baseline_y: float,
    *,
    regular_font: str,
    bold_font: str,
    size: float,
    color: HexColor,
    max_width: float,
    line_height: float,
) -> float:
    """Word-wrap a mixed regular/bold paragraph. Returns y of last drawn baseline."""
    # Flatten runs into a list of word-level segments, each (token, is_bold, has_space_after)
    tokens: list[tuple[str, bool, bool]] = []
    for text, bold in runs:
        if not text:
            continue
        parts = re.split(r"(\s+)", text)
        for i, part in enumerate(parts):
            if not part:
                continue
            if part.isspace():
                # mark previous token as having a trailing space
                if tokens:
                    last_tok, last_bold, _ = tokens[-1]
                    tokens[-1] = (last_tok, last_bold, True)
                continue
            tokens.append((part, bold, False))

    def font_for(b: bool) -> str:
        return bold_font if b else regular_font

    # Greedy line packing
    lines: list[list[tuple[str, bool, bool]]] = [[]]
    current_width = 0.0
    space_w_regular = c.stringWidth(" ", regular_font, size)
    space_w_bold = c.stringWidth(" ", bold_font, size)

    for i, (tok, bold, _) in enumerate(tokens):
        # The space BEFORE this token (if any) belongs to the previous token's trailing flag
        needs_leading_space = bool(lines[-1]) and lines[-1][-1][2]
        space_w = (space_w_bold if lines[-1] and lines[-1][-1][1] else space_w_regular) if needs_leading_space else 0.0
        tok_w = c.stringWidth(tok, font_for(bold), size)
        if current_width + space_w + tok_w > max_width and lines[-1]:
            # wrap to new line — drop the trailing-space flag on previous line
            if lines[-1]:
                lt, lb, _ = lines[-1][-1]
                lines[-1][-1] = (lt, lb, False)
            lines.append([])
            current_width = 0.0
            space_w = 0.0
        lines[-1].append((tok, bold, tokens[i][2]))
        current_width += space_w + tok_w

    # Draw
    c.setFillColor(color)
    y = first_baseline_y
    for line in lines:
        cx = x
        for j, (tok, bold, trail_space) in enumerate(line):
            c.setFont(font_for(bold), size)
            c.drawString(cx, y, tok)
            cx += c.stringWidth(tok, font_for(bold), size)
            if trail_space and j < len(line) - 1:
                cx += c.stringWidth(" ", font_for(bold), size)
        y -= line_height
    return y + line_height


# ---------------------------------------------------------------------------
# Sparande calculation + matplotlib bar chart
# ---------------------------------------------------------------------------


def compute_sparande_per_kvm() -> list[tuple[int, float]]:
    """Return list of (year, sparande_kr_per_kvm) using the same formula as the HTML JS.

    See [brf-economy-guide.html] renderSparandeHistoryChart for the original logic:
        for each year:
            uhTotal = sum(amount for cid in SPARANDE_UH_CATS)
            isTotal = sum(amount for cid NOT in SPARANDE_CF_CATS and cid has no parent)
            sparande_total = isTotal + abs(uhTotal)
        sparande_per_kvm = sparande_total / TOTAL_KVM
    """
    import csv

    cat_parent: dict[int, str | None] = {}
    with open(DATA_DIR / "event_categories.csv") as f:
        for row in csv.DictReader(f):
            cid = int(row["id"])
            parent = row["parent"].strip() if row["parent"].strip() else None
            cat_parent[cid] = parent

    events_by_year: dict[int, list[tuple[int, int]]] = {}
    with open(DATA_DIR / "financial_events.csv") as f:
        for row in csv.DictReader(f):
            y = int(row["year"])
            cid = int(row["category_id"])
            amt = int(row["amount"])
            events_by_year.setdefault(y, []).append((cid, amt))

    result: list[tuple[int, float]] = []
    for year in sorted(events_by_year):
        is_total = 0
        uh_total = 0
        for cid, amt in events_by_year[year]:
            if cid in SPARANDE_UH_CATS:
                uh_total += amt
            if cid not in SPARANDE_CF_CATS and not cat_parent.get(cid):
                is_total += amt
        sparande = (is_total + abs(uh_total)) / TOTAL_KVM
        result.append((year, sparande))
    return result


# ---------------------------------------------------------------------------
# Data computations for the 4 multi-line history charts
# (mirrors the JS logic in brf-economy-guide.html)
# ---------------------------------------------------------------------------


def _load_events() -> list[dict[str, str]]:
    import csv
    with open(DATA_DIR / "financial_events.csv") as f:
        return list(csv.DictReader(f))


def _load_states() -> list[dict[str, str]]:
    import csv
    with open(DATA_DIR / "financial_states.csv") as f:
        return list(csv.DictReader(f))


def compute_debt_history() -> list[tuple[int, dict[str, float]]]:
    """Per-year {short, long, cash, total} from financial_states.csv (kr/kvm)."""
    per_year: dict[int, dict[str, float]] = {}
    for s in _load_states():
        y = int(s["year"])
        cid = int(s["category_id"])
        amt = int(s["amount"])
        d = per_year.setdefault(y, {"short": 0.0, "long": 0.0, "cash": 0.0})
        if cid == 0:
            d["short"] = amt
        elif cid == 1:
            d["long"] = amt
        elif 2 <= cid <= 5:
            d["cash"] += amt
    result = []
    for y in sorted(per_year):
        d = per_year[y]
        d["total"] = d["short"] + d["long"]
        for k in d:
            d[k] = d[k] / TOTAL_KVM
        result.append((y, d))
    return result


def compute_energi_history() -> list[tuple[int, dict[str, float]]]:
    """Per-year energy costs by component (kr/kvm). Components: uppvarmning, el, vatten."""
    # cat 22 = El, 23 = Uppvärmning, 24 = Vatten
    per_year: dict[int, dict[str, float]] = {}
    for e in _load_events():
        cid = int(e["category_id"])
        if cid not in (22, 23, 24):
            continue
        y = int(e["year"])
        amt = abs(int(e["amount"]))
        d = per_year.setdefault(y, {"el": 0.0, "uppvarmning": 0.0, "vatten": 0.0})
        if cid == 22:
            d["el"] += amt
        elif cid == 23:
            d["uppvarmning"] += amt
        elif cid == 24:
            d["vatten"] += amt
    result = []
    for y in sorted(per_year):
        d = per_year[y]
        d["total"] = d["el"] + d["uppvarmning"] + d["vatten"]
        for k in d:
            d[k] = d[k] / TOTAL_KVM
        result.append((y, d))
    return result


def compute_rantekanslighet_history() -> list[tuple[int, float]]:
    """Per-year räntekänslighet (%) = (1% × total_debt) / avgifter × 100."""
    avgifter_per_year: dict[int, float] = {}
    nettoomsattning_per_year: dict[int, float] = {}
    for e in _load_events():
        y = int(e["year"])
        cid = int(e["category_id"])
        amt = int(e["amount"])
        if cid == 17:
            avgifter_per_year[y] = avgifter_per_year.get(y, 0) + amt
        elif cid == 0:
            nettoomsattning_per_year[y] = nettoomsattning_per_year.get(y, 0) + amt
    debt_per_year: dict[int, int] = {}
    for s in _load_states():
        cid = int(s["category_id"])
        if cid not in (0, 1):
            continue
        y = int(s["year"])
        debt_per_year[y] = debt_per_year.get(y, 0) + int(s["amount"])
    result: list[tuple[int, float]] = []
    for y in sorted(debt_per_year):
        avgifter = avgifter_per_year.get(y) or nettoomsattning_per_year.get(y)
        if not avgifter or avgifter <= 0:
            continue
        total_debt = debt_per_year[y]
        if total_debt <= 0:
            continue
        rk = (0.01 * total_debt) / avgifter * 100.0
        result.append((y, round(rk, 1)))
    return result


# Cost-category colors for the treemap (top-level category id → hex color).
COST_COLORS: dict[int, str] = {
    1: "#dc2626",  # Driftkostnader (red)
    5: "#f97316",  # Övriga externa kostnader (orange)
    6: "#d97706",  # Personalkostnader (amber)
    7: "#b91c1c",  # Räntekostnader (dark red)
    8: "#9333ea",  # Investeringar (purple)
}


@dataclass
class TreemapLeaf:
    name: str
    value: float   # positive (already abs'd)


@dataclass
class TreemapNode:
    name: str
    color: str
    children: list[TreemapLeaf]   # leaf nodes only (single-level hierarchy)

    def total(self) -> float:
        return sum(c.value for c in self.children)


def compute_cost_treemap(year: int = 2025) -> list[TreemapNode]:
    """Build 2-level cost treemap for `year`.

    Top-level cost categories (no parent, negative amount) become groups.
    Each group's children (categories with this as parent, in financial_events)
    become leaves; residual goes into 'Övrigt X'.
    """
    import csv

    cat_parent: dict[int, str | None] = {}
    cat_name: dict[int, str] = {}
    with open(DATA_DIR / "event_categories.csv") as f:
        for row in csv.DictReader(f):
            cid = int(row["id"])
            cat_name[cid] = row["name"]
            cat_parent[cid] = row["parent"].strip() if row["parent"].strip() else None

    children_of: dict[int, list[int]] = {}
    for cid, parent in cat_parent.items():
        if parent is not None:
            try:
                children_of.setdefault(int(parent), []).append(cid)
            except ValueError:
                pass

    year_str = str(year)
    year_amounts: dict[int, int] = {}
    for e in _load_events():
        if e["year"] != year_str:
            continue
        year_amounts[int(e["category_id"])] = int(e["amount"])

    nodes: list[TreemapNode] = []
    for cid, parent in cat_parent.items():
        if parent is not None:
            continue
        amt = year_amounts.get(cid)
        if amt is None or amt >= 0:
            continue
        total_abs = abs(amt)
        color = COST_COLORS.get(cid, "#94a3b8")

        # Build children (one level deep)
        leaves: list[TreemapLeaf] = []
        children_total = 0
        for child_id in children_of.get(cid, []):
            child_amt = year_amounts.get(child_id)
            if child_amt is None:
                continue
            v = abs(child_amt)
            if v <= 0:
                continue
            leaves.append(TreemapLeaf(name=cat_name.get(child_id, "Okänd"), value=v))
            children_total += v

        if not leaves:
            # No children: the category itself is the only leaf.
            leaves.append(TreemapLeaf(name=cat_name.get(cid, "Okänd"), value=total_abs))
        else:
            residual = total_abs - children_total
            if residual > 1:
                leaves.append(TreemapLeaf(
                    name=f"Övrigt {cat_name.get(cid, '').lower()}",
                    value=residual,
                ))

        leaves.sort(key=lambda l: -l.value)
        nodes.append(TreemapNode(
            name=cat_name.get(cid, "Okänd"),
            color=color,
            children=leaves,
        ))

    nodes.sort(key=lambda n: -n.total())
    return nodes


def render_treemap_chart_png(
    nodes: list[TreemapNode],
    *,
    width_pt: float,
    height_pt: float,
    dpi: int = 200,
) -> ImageReader:
    """Render a 2-level treemap. Top-level boxes are squarified across the full area;
    each top-level box is sub-squarified for its children leaves."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    import matplotlib.colors as mcolors
    import squarify

    fig_w_in = width_pt / 72.0
    fig_h_in = height_pt / 72.0
    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in), dpi=dpi)

    width = 100.0
    height = 100.0
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.invert_yaxis()  # top-left origin
    ax.set_aspect("auto")
    ax.axis("off")

    top_values = [n.total() for n in nodes]
    norm = squarify.normalize_sizes(top_values, width, height)
    top_rects = squarify.squarify(norm, 0, 0, width, height)

    def _fit_label(text: str, max_chars: int) -> str:
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    for node, rect in zip(nodes, top_rects):
        x, y, w, h = rect["x"], rect["y"], rect["dx"], rect["dy"]
        base_color = node.color
        ax.add_patch(Rectangle((x, y), w, h, facecolor=base_color, edgecolor="white",
                                linewidth=2.5, zorder=1))

        # Group title — only render if cell can fit at least some text
        if w > 6 and h > 5:
            max_chars = max(6, int(w * 1.3))
            ax.text(x + 1.3, y + 2.5, _fit_label(node.name, max_chars),
                    ha="left", va="top", fontsize=7.5, fontweight="bold",
                    color="white", zorder=4)
            if h > 8:
                ax.text(x + 1.3, y + 5.5, f"{int(round(node.total() / TOTAL_KVM))} kr/kvm",
                        ha="left", va="top", fontsize=6.5, color="white",
                        alpha=0.9, zorder=4)

        # Children leaves inside the group rectangle (inset slightly for spacing)
        inset = 0.6
        cx, cy = x + inset, y + inset + 6.5   # leave room for group title at top
        cw, ch = max(0.1, w - 2 * inset), max(0.1, h - inset - 7.5)
        if cw <= 1 or ch <= 1:
            continue
        leaf_values = [l.value for l in node.children]
        if sum(leaf_values) == 0:
            continue
        leaf_norm = squarify.normalize_sizes(leaf_values, cw, ch)
        leaf_rects = squarify.squarify(leaf_norm, cx, cy, cw, ch)

        # Slightly lighter shade of group color for leaves
        base_rgb = mcolors.to_rgb(base_color)
        leaf_rgb = tuple(min(1, c + 0.18) for c in base_rgb)

        for leaf, lr in zip(node.children, leaf_rects):
            lx, ly, lw, lh = lr["x"], lr["y"], lr["dx"], lr["dy"]
            ax.add_patch(Rectangle((lx, ly), lw, lh, facecolor=leaf_rgb,
                                    edgecolor=base_color, linewidth=0.8, zorder=2))
            label_value = int(round(leaf.value / TOTAL_KVM))
            if lw > 7 and lh > 4.5:
                max_chars = max(8, int(lw * 1.2))
                ax.text(lx + 0.5, ly + 1.5, _fit_label(leaf.name, max_chars),
                        ha="left", va="top", fontsize=5.5, color="#1a1a1a", zorder=5)
                ax.text(lx + 0.5, ly + 4.0, f"{label_value} kr/kvm",
                        ha="left", va="top", fontsize=5, color="#444", zorder=5)
            elif lw > 4 and lh > 2.5:
                ax.text(lx + 0.4, ly + 1.0, f"{label_value}",
                        ha="left", va="top", fontsize=5, color="#1a1a1a", zorder=5)

    fig.tight_layout(pad=0.1)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                pad_inches=0.05, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


def compute_refinancing_risk() -> list[tuple[int, int]]:
    """Per-year short-term debt as % of total debt."""
    debt = compute_debt_history()
    result = []
    for year, d in debt:
        # In compute_debt_history the values are divided by TOTAL_KVM, so use ratios directly.
        total_per_kvm = d["short"] + d["long"]
        if total_per_kvm <= 0:
            continue
        pct = round(d["short"] / total_per_kvm * 100)
        result.append((year, pct))
    return result


def compute_investment_history() -> list[tuple[int, float]]:
    """Per-year investments (cat 8) in kr/kvm. Years >= 2004."""
    import csv
    per_year: dict[int, int] = {}
    with open(DATA_DIR / "financial_events.csv") as f:
        for row in csv.DictReader(f):
            if int(row["category_id"]) != 8:
                continue
            y = int(row["year"])
            if y < 2004:
                continue
            per_year[y] = per_year.get(y, 0) + abs(int(row["amount"]))
    return [(y, per_year[y] / TOTAL_KVM) for y in sorted(per_year)]


# Prognos chart constants — hardcoded series from brf-economy-guide.html.
PROGNOS_YEARS = [2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030]
PROGNOS_SKULD_ACTUAL   = [4151, 4408, 4532, 4462, None, None, None, None, None]
PROGNOS_SKULD_FORECAST = [None, None, None, 4462, 5397, 6332, 7267, 8202, 9137]
PROGNOS_RK_ACTUAL      = [6,    5,    5,    5,    None, None, None, None, None]
PROGNOS_RK_FORECAST    = [None, None, None, 5,    6,    7,    8,    9,    10]
PROGNOS_AVG_ACTUAL     = [765,  834,  897,  998,  None, None, None, None, None]
PROGNOS_AVG_FORECAST   = [None, None, None, 998,  1028, 1059, 1091, 1124, 1158]


@dataclass
class WaterfallItem:
    label: str
    value: float          # signed, in kr/kvm (already normalized)
    kind: str = "delta"   # "delta" for relative bars, "total" for sum bar


def compute_sparande_waterfall(year: int = 2025) -> list[WaterfallItem]:
    """Build the sparande waterfall items for `year`, in display order.

    Mirrors the JS in brf-economy-guide.html (renderWaterfallChart):
    - Top-level events excluding SPARANDE_CF_CATS, sorted by amount desc
    - Subtract underhåll amounts from parent categories (driftkostnader)
    - Expand Nettoomsättning (cat 0) into subcategories
    - Final "Sparande" total bar
    """
    import csv

    cat_parent: dict[int, str | None] = {}
    cat_name: dict[int, str] = {}
    with open(DATA_DIR / "event_categories.csv") as f:
        for row in csv.DictReader(f):
            cid = int(row["id"])
            parent = row["parent"].strip() if row["parent"].strip() else None
            cat_parent[cid] = parent
            cat_name[cid] = row["name"]

    year_str = str(year)
    events_for_year = []
    for e in _load_events():
        if e["year"] != year_str:
            continue
        events_for_year.append({"cid": int(e["category_id"]), "amount": int(e["amount"])})

    # Top-level IS (income statement) events: no parent, not in CF cats
    is_top = [e for e in events_for_year
              if e["cid"] not in SPARANDE_CF_CATS and not cat_parent.get(e["cid"])]
    underhall_events = [e for e in events_for_year if e["cid"] in SPARANDE_UH_CATS]
    underhall_total = sum(e["amount"] for e in underhall_events)
    underhall_abs = abs(underhall_total)
    is_total = sum(e["amount"] for e in is_top)
    sparande_total = is_total + underhall_abs

    # Underhåll per parent category id (string keys in cat_parent values)
    uh_by_parent: dict[int, int] = {}
    for e in underhall_events:
        parent = cat_parent.get(e["cid"])
        if parent is not None:
            try:
                uh_by_parent[int(parent)] = uh_by_parent.get(int(parent), 0) + e["amount"]
            except ValueError:
                pass

    # Sub-events grouped by parent for nettoomsättning expansion
    sub_events_by_parent: dict[int, list[dict]] = {}
    for e in events_for_year:
        parent = cat_parent.get(e["cid"])
        if parent is not None:
            try:
                sub_events_by_parent.setdefault(int(parent), []).append(e)
            except ValueError:
                pass

    items: list[WaterfallItem] = []
    for e in sorted(is_top, key=lambda x: -x["amount"]):
        cid = e["cid"]
        if cid in SPARANDE_UH_CATS:
            continue
        val = e["amount"]
        label = cat_name.get(cid, "Okänd")
        if cid in uh_by_parent:
            val -= uh_by_parent[cid]
            label += " (exkl. underhåll)"
        # Expand Nettoomsättning (cid 0) into its sub-categories
        if cid == 0:
            subs = [s for s in sub_events_by_parent.get(cid, [])
                    if s["cid"] not in SPARANDE_UH_CATS]
            if subs:
                sub_items = sorted(
                    [(cat_name.get(s["cid"], "Okänd"), s["amount"]) for s in subs],
                    key=lambda x: -x[1],
                )
                sub_total = sum(v for _, v in sub_items)
                residual = val - sub_total
                for sl, sv in sub_items:
                    items.append(WaterfallItem(label=sl, value=sv / TOTAL_KVM))
                if abs(residual) > 1:
                    items.append(WaterfallItem(
                        label=f"Övrigt ({cat_name.get(cid, '').lower()})",
                        value=residual / TOTAL_KVM,
                    ))
                continue
        items.append(WaterfallItem(label=label, value=val / TOTAL_KVM))

    items.append(WaterfallItem(label="Sparande", value=sparande_total / TOTAL_KVM, kind="total"))
    return items


def compute_cashflow_waterfall(year: int = 2025) -> list[WaterfallItem]:
    """Build the cashflow waterfall items for `year`, in display order.

    Mirrors the JS (renderCashflowWaterfall):
    - All top-level events (no parent), sorted positives-then-negatives
    - Final "Nettokassaförändring" total bar
    """
    import csv

    cat_parent: dict[int, str | None] = {}
    cat_name: dict[int, str] = {}
    with open(DATA_DIR / "event_categories.csv") as f:
        for row in csv.DictReader(f):
            cid = int(row["id"])
            parent = row["parent"].strip() if row["parent"].strip() else None
            cat_parent[cid] = parent
            cat_name[cid] = row["name"]

    year_str = str(year)
    items_raw = []
    for e in _load_events():
        if e["year"] != year_str:
            continue
        cid = int(e["category_id"])
        if cat_parent.get(cid):
            continue  # only top-level
        items_raw.append((cat_name.get(cid, "Okänd"), int(e["amount"])))

    # Sort: positives first (descending), then negatives (descending magnitude)
    def key(x):
        label, val = x
        if val > 0:
            return (0, -val)
        return (1, -abs(val))

    items_raw.sort(key=key)
    items = [WaterfallItem(label=l, value=v / TOTAL_KVM) for l, v in items_raw]
    net = sum(v for _, v in items_raw)
    items.append(WaterfallItem(label="Nettokassaförändring",
                                value=net / TOTAL_KVM, kind="total"))
    return items


def render_waterfall_chart_png(
    items: list[WaterfallItem],
    *,
    width_pt: float,
    height_pt: float,
    unit_label: str = "kr/kvm",
    dpi: int = 200,
) -> ImageReader:
    """Render a horizontal waterfall chart (top → down, deltas accumulating then total bar).

    Bars: green for positive deltas, red for negative deltas, blue for total bar.
    Each bar has its signed value labeled at the right edge of its extent.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_w_in = width_pt / 72.0
    fig_h_in = height_pt / 72.0
    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in), dpi=dpi)

    n = len(items)
    # y positions: index 0 at top, larger indices go down
    ys = list(range(n))
    bar_height = 0.62

    running = 0.0
    bar_positions = []  # list of (left, right) for each bar — used for connectors
    for i, item in enumerate(items):
        y = ys[i]
        v = item.value
        if item.kind == "total":
            left = 0.0
            right = v
            width = abs(v)
            color = "#3b82f6"
        else:
            if v >= 0:
                left = running
                right = running + v
            else:
                left = running + v   # negative bars: shrink to the left
                right = running
            width = abs(v)
            color = "#22c55e" if v >= 0 else "#ef4444"
            running += v
        bar_positions.append((left, right))
        ax.barh(y, width, left=left, height=bar_height, color=color, zorder=3)

        # Value label placed at the OUTER edge of the bar
        sign = "−" if v < 0 else ""
        label_text = f"{sign}{abs(v):,.0f} {unit_label}".replace(",", " ")
        text_x = right + 4
        ax.text(text_x, y, label_text, va="center", ha="left",
                fontsize=8, color="#3b82f6" if item.kind == "total" else "#333")

    # Connector lines: each delta bar's running edge connects to the next delta bar's start edge.
    # The "running edge" is the right edge for positive bars, left edge for negative bars.
    cum = 0.0
    for i in range(n - 1):
        item = items[i]
        if item.kind == "total":
            continue
        if items[i + 1].kind == "total":
            continue
        cum += item.value
        ax.plot([cum, cum], [ys[i] + bar_height / 2 - 0.04, ys[i + 1] - bar_height / 2 + 0.04],
                color="#94a3b8", linewidth=0.7, zorder=2)

    # Y axis: invert so first item is at top
    ax.set_yticks(ys)
    ax.set_yticklabels([item.label for item in items], fontsize=8.5)
    ax.invert_yaxis()

    # X axis
    ax.set_xlabel(unit_label, fontsize=8)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(axis="x", color="#e9ecef", linewidth=0.5, zorder=1)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccccc")
        ax.spines[spine].set_linewidth(0.6)

    # Compute actual running min/max to set x-axis correctly.
    running_track = 0.0
    min_run, max_run = 0.0, 0.0
    for it in items:
        if it.kind == "total":
            max_run = max(max_run, it.value)
            min_run = min(min_run, it.value)
        else:
            running_track += it.value
            max_run = max(max_run, running_track)
            min_run = min(min_run, running_track)
    x_left = min(0.0, min_run * 1.05)
    x_right = max_run * 1.20
    ax.set_xlim(left=x_left, right=x_right)

    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                pad_inches=0.05, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


# ---------------------------------------------------------------------------
# Comparison-stick chart (peer benchmarking with HSB band background)
# ---------------------------------------------------------------------------


@dataclass
class ComparisonBar:
    name: str    # display name, may contain a newline for two-line labels
    value: float
    is_focus: bool = False  # teal if true (i.e. BRF Sjötungan), amber otherwise


@dataclass
class ReferenceLine:
    value: float
    label: str
    color: str = "#1b3a5c"


@dataclass
class ComparisonChartData:
    bars: list[ComparisonBar]
    bands: list[HSBBand]
    y_max: float
    y_label: str = "kr/kvm"
    title: str = ""
    value_suffix: str = ""    # appended to bar value labels (e.g. "%")
    reference_line: ReferenceLine | None = None  # optional horizontal benchmark line


def _comparison_data(chart_id: str) -> ComparisonChartData | None:
    """Hardcoded peer data, matching the JS in brf-economy-guide.html."""
    if chart_id == "sparandeComparisonChart":
        return ComparisonChartData(
            bars=[
                ComparisonBar("BRF Siken",        120),
                ComparisonBar("Stockholm\nsnitt", 165),
                ComparisonBar("HSB\nSödertörn",   175),
                ComparisonBar("BRF Gäddan",       302),
                ComparisonBar("BRF\nBjörkbacken", 322),
                ComparisonBar("BRF\nSjötungan",   306, is_focus=True),
            ],
            bands=[
                HSBBand(0,   120, "#ffecec", "Mycket lågt"),
                HSBBand(120, 200, "#fff0e0", "Lågt till måttligt"),
                HSBBand(200, 300, "#e8f5e8", "Rekommenderat"),
                HSBBand(300, 400, "#fff5f0", "Högt (onödigt högt?)"),
            ],
            y_max=400,
            title="Sparande per kvm — jämförelse 2025",
        )
    if chart_id == "skuldComparisonChart":
        return ComparisonChartData(
            bars=[
                ComparisonBar("BRF\nGäddan",      2449),
                ComparisonBar("BRF Siken",        3070),
                ComparisonBar("BRF\nBjörkbacken", 4283),
                ComparisonBar("BRF\nSjötungan",   4462, is_focus=True),
                ComparisonBar("Stockholm\nsnitt", 5773),
            ],
            bands=[
                HSBBand(0,    3000,  "#dcfce7", "Låg"),
                HSBBand(3000, 8000,  "#fef3c7", "Normal"),
                HSBBand(8000, 16000, "#fee2e2", "Hög"),
            ],
            y_max=16000,
            title="Skuldsättning per kvm — jämförelse 2025",
        )
    if chart_id == "rantekanslighetComparisonChart":
        return ComparisonChartData(
            bars=[
                ComparisonBar("Gäddan 6",        3),
                ComparisonBar("Siken",           4),
                ComparisonBar("Björkbacken",     5),
                ComparisonBar("Sjötungan",       5, is_focus=True),
                ComparisonBar("Sthlm snitt\n2021", 8),
            ],
            bands=[
                HSBBand(0, 5,  "#dcfce7", "Låg"),
                HSBBand(5, 9,  "#fef3c7", "Normal"),
                HSBBand(9, 15, "#fee2e2", "Hög"),
            ],
            y_max=15,
            y_label="%",
            value_suffix="%",
            title="Räntekänslighet — jämförelse 2025",
        )
    if chart_id == "energiComparisonChart":
        return ComparisonChartData(
            bars=[
                ComparisonBar("BRF Siken",        229),
                ComparisonBar("BRF\nGäddan",      248),
                ComparisonBar("BRF\nBjörkbacken", 269),
                ComparisonBar("BRF\nSjötungan",   304, is_focus=True),
            ],
            bands=[
                HSBBand(0,   150, "#dcfce7", "Låg"),
                HSBBand(150, 250, "#fef3c7", "Normal"),
                HSBBand(250, 350, "#fee2e2", "Hög"),
                HSBBand(350, 450, "#fecaca", "Mycket hög"),
            ],
            y_max=450,
            title="Energikostnad per kvm — jämförelse 2025",
        )
    if chart_id == "arsavgiftComparisonChart":
        return ComparisonChartData(
            bars=[
                ComparisonBar("BRF Siken",        734),
                ComparisonBar("Stockholm\nsnitt", 833),
                ComparisonBar("BRF\nGäddan",      888),
                ComparisonBar("BRF\nBjörkbacken", 962),
                ComparisonBar("BRF\nSjötungan",   998, is_focus=True),
            ],
            bands=[
                HSBBand(0,    500, "#f5f5f5", ""),
                HSBBand(500,  800, "#dcfce7", "Vanligt (500–800)"),
                HSBBand(800,  1000, "#fef3c7", "Högre än vanligt"),
                HSBBand(1000, 1200, "#fee2e2", "Se upp"),
            ],
            y_max=1200,
            title="Årsavgift per kvm — jämförelse 2025",
        )
    if chart_id == "ltvMarknadComparisonChart":
        # No bands — HSB defines no LTV-thresholds and bank-LTV-tak for
        # BRF-finansiering saknar etablerad standardkälla. SCB-snitt för
        # 40+-åriga BRF (15%, 2023) finns dock som referenslinje.
        return ComparisonChartData(
            bars=[
                ComparisonBar("BRF\nGäddan",      8),
                ComparisonBar("BRF Siken",        8),
                ComparisonBar("BRF\nBjörkbacken", 14),
                ComparisonBar("BRF\nSjötungan",   19, is_focus=True),
            ],
            bands=[],
            y_max=24,
            y_label="%",
            value_suffix="%",
            title="LTV mot marknadsvärde — jämförelse",
            reference_line=ReferenceLine(value=15, label="SCB-snitt BRF 40+ år (2023): 15%", color="#7f1d1d"),
        )
    if chart_id == "ltvTaxComparisonChart":
        return ComparisonChartData(
            bars=[
                ComparisonBar("BRF\nGäddan",      11),
                ComparisonBar("BRF Siken",        21),
                ComparisonBar("BRF\nBjörkbacken", 30),
                ComparisonBar("BRF\nSjötungan",   31, is_focus=True),
            ],
            bands=[],
            y_max=38,
            y_label="%",
            value_suffix="%",
            title="LTV mot taxeringsvärde — jämförelse",
        )
    return None


def render_comparison_chart_png(
    data: ComparisonChartData,
    *,
    width_pt: float,
    height_pt: float,
    dpi: int = 200,
) -> ImageReader:
    """Vertical bar chart with HSB band shading + bar value labels + band labels on right."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_w_in = width_pt / 72.0
    fig_h_in = height_pt / 72.0
    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in), dpi=dpi)

    # HSB bands behind bars
    for band in data.bands:
        ax.axhspan(band.y0, band.y1, color=band.color, zorder=0)

    # Threshold dotted lines at band boundaries
    for band in data.bands[:-1]:
        ax.axhline(band.y1, color="#bbbbbb", linestyle=":", linewidth=0.7, zorder=1)

    # Bars
    n = len(data.bars)
    xs = list(range(n))
    values = [b.value for b in data.bars]
    colors = ["#1A9E9E" if b.is_focus else "#e0a030" for b in data.bars]
    ax.bar(xs, values, color=colors, width=0.55, zorder=3, edgecolor="none")

    # Optional reference line (e.g. SCB sector benchmark)
    if data.reference_line is not None:
        rl = data.reference_line
        ax.axhline(rl.value, color=rl.color, linestyle="--", linewidth=1.0, zorder=2)
        ax.text(-0.45, rl.value, rl.label,
                ha="left", va="bottom", fontsize=8, color=rl.color, zorder=4)

    # Bar value labels on top
    for i, b in enumerate(data.bars):
        label = f"{b.value:,.0f}".replace(",", " ") + data.value_suffix
        ax.text(i, b.value + data.y_max * 0.012, label,
                ha="center", va="bottom", fontsize=10, fontweight="bold",
                color="#1b3a5c", zorder=4)

    # Band labels — placed OUTSIDE the bars on the right
    label_x = n - 0.35
    for band in data.bands:
        if not band.label:
            continue
        ax.text(label_x, (band.y0 + band.y1) / 2.0, band.label,
                ha="left", va="center", fontsize=8.5,
                color={"#ffecec": "#c45050", "#fff0e0": "#b8860b",
                       "#e8f5e8": "#3a7d44", "#fff5f0": "#888888",
                       "#dcfce7": "#16a34a", "#fef3c7": "#d97706",
                       "#fee2e2": "#dc2626", "#fecaca": "#7f1d1d",
                       "#f5f5f5": "#aaaaaa"}.get(band.color, "#666"),
                fontweight="bold", zorder=2)

    # Axes
    ax.set_xticks(xs)
    # Smaller font + extra space below for two-line labels
    ax.set_xticklabels([b.name for b in data.bars], fontsize=7.5, color="#1b3a5c")
    ax.tick_params(axis="y", labelsize=8, colors="#666")
    ax.set_ylabel(data.y_label, fontsize=9, labelpad=4, color="#666")
    ax.set_ylim(0, data.y_max)
    ax.set_xlim(-0.6, n + 1.2)  # extra right padding so band labels don't overlap bars
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccccc")
        ax.spines[spine].set_linewidth(0.6)
    ax.grid(False)

    if data.title:
        ax.set_title(data.title, fontsize=11, fontweight="bold",
                     color="#1b3a5c", loc="left", pad=6)

    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                pad_inches=0.05, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


def render_single_bar_chart_png(
    series: list[tuple[int, float]],
    *,
    width_pt: float,
    height_pt: float,
    y_label: str,
    threshold: float | None = None,
    threshold_label: str = "",
    bar_color_fn=None,  # callable: value -> hex color
    default_color: str = "#3b82f6",
    y_max: float | None = None,
    dpi: int = 200,
) -> ImageReader:
    """Simple vertical bar chart (one series). Used for refinancing risk + investment history."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_w_in = width_pt / 72.0
    fig_h_in = height_pt / 72.0
    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in), dpi=dpi)

    years = [y for y, _ in series]
    values = [v for _, v in series]
    colors = [bar_color_fn(v) if bar_color_fn else default_color for v in values]
    ax.bar(years, values, color=colors, width=0.78, edgecolor="none", zorder=3)

    if threshold is not None:
        ax.axhline(threshold, color="#dc2626", linestyle="--", linewidth=1.2, zorder=4)
        if threshold_label and years:
            ax.text(years[-1], threshold + (y_max or max(values, default=100)) * 0.02,
                    threshold_label, ha="right", va="bottom",
                    fontsize=8, color="#dc2626")

    ax.set_xlabel("År", fontsize=9, labelpad=4)
    ax.set_ylabel(y_label, fontsize=9, labelpad=4)
    if years:
        x_ticks = [y for y in years if y % 2 == 0]
        if x_ticks:
            ax.set_xticks(x_ticks)
            ax.set_xticklabels([str(y) for y in x_ticks], fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", color="#e9ecef", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccccc")
        ax.spines[spine].set_linewidth(0.6)
    if y_max is not None:
        ax.set_ylim(0, y_max)

    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                pad_inches=0.05, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


def render_prognos_chart_png(
    years: list[int],
    actual: list[float | None],
    forecast: list[float | None],
    bands: list[HSBBand],
    *,
    width_pt: float,
    height_pt: float,
    y_label: str,
    y_max: float,
    dpi: int = 200,
) -> ImageReader:
    """Dual-line projection chart: actual (solid blue) + forecast (dashed red) with HSB bands."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_w_in = width_pt / 72.0
    fig_h_in = height_pt / 72.0
    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in), dpi=dpi)

    for band in bands:
        ax.axhspan(band.y0, band.y1, color=band.color, zorder=0)

    # Build x/y pairs filtering None values for each line
    actual_x = [years[i] for i, v in enumerate(actual) if v is not None]
    actual_y = [v for v in actual if v is not None]
    forecast_x = [years[i] for i, v in enumerate(forecast) if v is not None]
    forecast_y = [v for v in forecast if v is not None]

    if actual_y:
        ax.plot(actual_x, actual_y, color="#3b82f6", linewidth=2.0,
                marker="o", markersize=4.5, label="Faktiskt", zorder=3)
    if forecast_y:
        ax.plot(forecast_x, forecast_y, color="#ef4444", linewidth=2.0,
                linestyle="--", marker="D", markersize=4.5,
                label="Prognos (uppskattning)", zorder=3)

    # Band labels on the right (outside via padding)
    x_max = years[-1] if years else 2030
    for band in bands:
        if not band.label:
            continue
        ax.text(x_max + 0.4, (band.y0 + band.y1) / 2.0, band.label,
                ha="left", va="center", fontsize=8.5, color="#888")

    ax.set_xlabel("År", fontsize=9, labelpad=3)
    ax.set_ylabel(y_label, fontsize=9, labelpad=4)
    if years:
        ax.set_xticks(years)
        ax.set_xticklabels([str(y) for y in years], fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.set_ylim(0, y_max)
    ax.set_xlim(years[0] - 0.5 if years else 0, (years[-1] if years else 2030) + 2.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccccc")
        ax.spines[spine].set_linewidth(0.6)
    ax.grid(axis="y", color="#e9ecef", linewidth=0.6, zorder=1)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", fontsize=8, frameon=False)

    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                pad_inches=0.05, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


def compute_arsavgift_history() -> list[tuple[int, float]]:
    """Per-year årsavgift kr/kvm from financial_events.csv (cat 17)."""
    per_year: dict[int, float] = {}
    for e in _load_events():
        cid = int(e["category_id"])
        if cid != 17:
            continue
        y = int(e["year"])
        per_year[y] = per_year.get(y, 0) + int(e["amount"])
    return [(y, per_year[y] / TOTAL_KVM) for y in sorted(per_year)]


# ---------------------------------------------------------------------------
# Generic multi-line chart renderer (matplotlib)
# ---------------------------------------------------------------------------


@dataclass
class LineSeries:
    label: str
    x: list[int]
    y: list[float]
    color: str
    dash: str = "solid"          # "solid" or "dotted"
    width: float = 1.6
    marker_size: float = 3.5


@dataclass
class HSBBand:
    y0: float
    y1: float
    color: str        # rgba string e.g. "rgba(34,197,94,0.1)"
    label: str = ""


def render_multi_line_chart_png(
    series: list[LineSeries],
    *,
    width_pt: float,
    height_pt: float,
    y_label: str = "",
    x_label: str = "År",
    hsb_bands: list[HSBBand] | None = None,
    show_legend: bool = True,
    dpi: int = 200,
) -> ImageReader:
    """Render multi-line chart matching the style of the bar chart on page 4."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    fig_w_in = width_pt / 72.0
    fig_h_in = height_pt / 72.0
    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in), dpi=dpi)

    # HSB background bands
    if hsb_bands:
        for band in hsb_bands:
            ax.axhspan(band.y0, band.y1, color=mcolors.to_rgba(band.color, 1.0)
                       if band.color.startswith("#") else band.color,
                       zorder=0)

    all_y_max = 0.0
    for s in series:
        ls = "-" if s.dash == "solid" else (":" if s.dash == "dotted" else "--")
        ax.plot(s.x, s.y, label=s.label, color=s.color, linewidth=s.width,
                linestyle=ls, marker="o", markersize=s.marker_size, zorder=3)
        if s.y:
            all_y_max = max(all_y_max, max(s.y))

    # Axes
    ax.set_xlabel(x_label, fontsize=9, labelpad=3)
    if y_label:
        ax.set_ylabel(y_label, fontsize=9, labelpad=4)

    # X ticks every 2 years
    if series:
        all_x = sorted({x for s in series for x in s.x})
        if all_x:
            x_ticks = [x for x in all_x if x % 2 == 0]
            if x_ticks:
                ax.set_xticks(x_ticks)
                ax.set_xticklabels([str(x) for x in x_ticks], fontsize=8)

    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", color="#e9ecef", linewidth=0.6, zorder=1)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccccc")
        ax.spines[spine].set_linewidth(0.6)
    ax.set_ylim(0, max(all_y_max * 1.08, 1))

    if show_legend and len(series) > 1:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18),
                  ncol=min(len(series), 4), fontsize=8, frameon=False)

    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                pad_inches=0.05, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


def _bar_color_for(value: float) -> str:
    if value < 120:
        return HSB_BAR_COLORS["very_low"]
    if value < 200:
        return HSB_BAR_COLORS["low"]
    if value < 300:
        return HSB_BAR_COLORS["moderate"]
    return HSB_BAR_COLORS["high"]


def render_sparande_history_chart_png(
    series: list[tuple[int, float]],
    *,
    width_pt: float,
    height_pt: float,
    dpi: int = 200,
) -> ImageReader:
    """Render a matplotlib bar chart matching the page-4 style, return as ImageReader."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    years = [y for y, _ in series]
    values = [v for _, v in series]
    colors = [_bar_color_for(v) for v in values]

    # PNG dimensions = points * (dpi/72)
    fig_w_in = width_pt / 72.0
    fig_h_in = height_pt / 72.0

    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in), dpi=dpi)
    ax.bar(years, values, color=colors, width=0.78, edgecolor="none")

    # Style: minimal grid, year labels, kr/kvm label
    ax.set_xlabel("År", fontsize=10, labelpad=4)
    ax.set_ylabel("Belopp (SEK/m²)", fontsize=10, labelpad=4)
    ax.set_xticks([y for y in years if y % 5 == 0])
    ax.set_xticklabels([str(y) for y in years if y % 5 == 0], fontsize=9)
    ax.tick_params(axis="y", labelsize=9)
    ax.grid(axis="y", color="#e9ecef", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccccc")
        ax.spines[spine].set_linewidth(0.6)

    # y-axis ticks every 50
    max_v = max(values) * 1.05
    ax.set_ylim(0, max(300, int(max_v / 50) * 50 + 50))

    fig.tight_layout(pad=0.5)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.05,
                facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return ImageReader(buf)


# ---------------------------------------------------------------------------
# QR codes
# ---------------------------------------------------------------------------


def make_qr_image(url: str, box_size: int = 8, border: int = 2):
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


# ---------------------------------------------------------------------------
# Page 1 renderer
# ---------------------------------------------------------------------------


def draw_header_footer(c: Canvas, page_number: int) -> None:
    # Top line + header text
    c.setStrokeColor(COL_HEADER_LINE)
    c.setLineWidth(1.0)
    c.line(HEADER_LEFT, HEADER_LINE_Y, CONTENT_RIGHT, HEADER_LINE_Y)
    c.setFont("Helvetica", 8)
    c.setFillColor(COL_HEADFOOT)
    c.drawString(HEADER_LEFT, HEADER_TEXT_BASELINE_Y, "BRF Sjötungan — Förstå din förenings ekonomi")

    # Bottom line + footer text
    c.setStrokeColor(COL_DIVIDER)
    c.setLineWidth(0.5)
    c.line(HEADER_LEFT, FOOTER_LINE_Y, CONTENT_RIGHT, FOOTER_LINE_Y)
    c.setFont("Helvetica", 8)
    c.setFillColor(COL_HEADFOOT)
    c.drawString(HEADER_LEFT, FOOTER_TEXT_BASELINE_Y, "Framtagen av medlemmar, för medlemmar")
    c.drawRightString(CONTENT_RIGHT, FOOTER_TEXT_BASELINE_Y, f"Sida {page_number}")


def draw_title_block(c: Canvas, content: Page1Content) -> None:
    c.setFont("Helvetica-Bold", 26)
    c.setFillColor(COL_TITLE)
    c.drawString(CONTENT_LEFT, TITLE_BASELINE_Y, content.title)

    c.setFont("Helvetica", 13)
    c.setFillColor(COL_SUBTITLE)
    c.drawString(CONTENT_LEFT, SUBTITLE_BASELINE_Y, content.subtitle)

    c.setStrokeColor(COL_DIVIDER)
    c.setLineWidth(0.5)
    c.line(CONTENT_LEFT, TITLE_DIVIDER_Y, CARD_RIGHT, TITLE_DIVIDER_Y)


def draw_inledning(c: Canvas, content: Page1Content) -> None:
    # Section heading
    c.setFont("Helvetica-Bold", 20)
    c.setFillColor(COL_TITLE)
    c.drawString(CONTENT_LEFT, SECTION_HEADING_BASELINE_Y, content.section_heading)

    text_max_width = CONTENT_RIGHT - CONTENT_LEFT
    # Lead paragraph (lighter gray, 11pt)
    draw_paragraph(
        c,
        content.lead_paragraph,
        CONTENT_LEFT,
        LEAD_FIRST_LINE_Y,
        font="Helvetica",
        size=11,
        color=COL_LEAD,
        max_width=text_max_width,
        line_height=LEAD_LINE_HEIGHT,
    )

    # Dark paragraph (10.5pt #333)
    draw_paragraph(
        c,
        content.body_paragraph,
        CONTENT_LEFT,
        DARK_FIRST_LINE_Y,
        font="Helvetica",
        size=10.5,
        color=COL_BODY,
        max_width=text_max_width,
        line_height=DARK_LINE_HEIGHT,
    )


def draw_two_col_cards(c: Canvas, content: Page1Content) -> None:
    # Left blue card
    c.setFillColor(COL_BLUE_FILL)
    c.rect(
        CONTENT_LEFT,
        CARD_Y_BOTTOM,
        CARD_SPLIT_X - CONTENT_LEFT,
        CARD_Y_TOP - CARD_Y_BOTTOM,
        stroke=0,
        fill=1,
    )
    c.setStrokeColor(COL_BLUE_BORDER)
    c.setLineWidth(1.0)
    # top, bottom, left, right border (right shared with yellow card start)
    c.line(CONTENT_LEFT, CARD_Y_TOP, CARD_SPLIT_X, CARD_Y_TOP)
    c.line(CONTENT_LEFT, CARD_Y_BOTTOM, CARD_SPLIT_X, CARD_Y_BOTTOM)
    c.line(CONTENT_LEFT, CARD_Y_TOP, CONTENT_LEFT, CARD_Y_BOTTOM)
    c.line(CARD_SPLIT_X, CARD_Y_TOP, CARD_SPLIT_X, CARD_Y_BOTTOM)

    # Right yellow card
    c.setFillColor(COL_YELLOW_FILL)
    c.rect(
        CARD_SPLIT_X,
        CARD_Y_BOTTOM,
        CARD_RIGHT - 17.0 - CARD_SPLIT_X,  # matches original 513.40 right edge
        CARD_Y_TOP - CARD_Y_BOTTOM,
        stroke=0,
        fill=1,
    )
    c.setStrokeColor(COL_YELLOW_BORD)
    yellow_right = 513.402
    c.line(CARD_SPLIT_X, CARD_Y_TOP, yellow_right, CARD_Y_TOP)
    c.line(CARD_SPLIT_X, CARD_Y_BOTTOM, yellow_right, CARD_Y_BOTTOM)
    c.line(CARD_SPLIT_X, CARD_Y_TOP, CARD_SPLIT_X, CARD_Y_BOTTOM)
    c.line(yellow_right, CARD_Y_TOP, yellow_right, CARD_Y_BOTTOM)

    # Card content
    inner_max_left = CARD_SPLIT_X - CONTENT_LEFT - 2 * CARD_PAD_X
    inner_max_right = yellow_right - CARD_SPLIT_X - 2 * CARD_PAD_X

    # Left card title + body
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(COL_BODY)
    c.drawString(CONTENT_LEFT + CARD_PAD_X, CARD_TITLE_BASELINE_Y, content.card_left_title)
    draw_paragraph(
        c,
        content.card_left_body,
        CONTENT_LEFT + CARD_PAD_X,
        CARD_BODY_FIRST_LINE_Y,
        font="Helvetica",
        size=9.5,
        color=COL_BODY,
        max_width=inner_max_left,
        line_height=CARD_BODY_LINE_HEIGHT,
    )

    # Right card title + body
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(COL_BODY)
    c.drawString(CARD_SPLIT_X + CARD_PAD_X, CARD_TITLE_BASELINE_Y, content.card_right_title)
    draw_paragraph(
        c,
        content.card_right_body,
        CARD_SPLIT_X + CARD_PAD_X,
        CARD_BODY_FIRST_LINE_Y,
        font="Helvetica",
        size=9.5,
        color=COL_BODY,
        max_width=inner_max_right,
        line_height=CARD_BODY_LINE_HEIGHT,
    )


def draw_callout(c: Canvas, content: Page1Content) -> None:
    box_w = CARD_RIGHT - CONTENT_LEFT
    box_h = CALLOUT_Y_TOP - CALLOUT_Y_BOTTOM

    # Fill + border
    c.setFillColor(COL_YELLOW_FILL)
    c.setStrokeColor(COL_YELLOW_BORD)
    c.setLineWidth(1.2)
    c.rect(CONTENT_LEFT, CALLOUT_Y_BOTTOM, box_w, box_h, stroke=1, fill=1)

    # Left accent strip
    c.setFillColor(COL_YELLOW_BORD)
    c.rect(CONTENT_LEFT, CALLOUT_Y_BOTTOM, CALLOUT_LEFT_ACCENT_W, box_h, stroke=0, fill=1)

    # Title
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(COL_CALLOUT_HEAD)
    c.drawString(CONTENT_LEFT + CALLOUT_PAD_X, CALLOUT_TITLE_BASELINE_Y, content.callout_title)

    # Body
    body_max_width = CARD_RIGHT - (CONTENT_LEFT + CALLOUT_PAD_X) - 10
    draw_paragraph(
        c,
        content.callout_body,
        CONTENT_LEFT + CALLOUT_PAD_X,
        CALLOUT_BODY_FIRST_LINE_Y,
        font="Helvetica",
        size=10,
        color=COL_BODY,
        max_width=body_max_width,
        line_height=CALLOUT_BODY_LINE_HEIGHT,
    )


def draw_post_callout_paragraph(c: Canvas, content: Page1Content) -> None:
    draw_paragraph(
        c,
        content.post_callout_paragraph,
        CONTENT_LEFT,
        POST_CALLOUT_FIRST_LINE_Y,
        font="Helvetica",
        size=10.5,
        color=COL_BODY,
        max_width=CONTENT_RIGHT - CONTENT_LEFT,
        line_height=DARK_LINE_HEIGHT,
    )


def draw_qr_card(
    c: Canvas,
    *,
    top: float,
    bottom: float,
    img_top: float,
    img_bottom: float,
    qr_url: str,
    title: str,
    url_lines: list[str],
    instruction: str,
    title_y: float,
    url_first_y: float,
    instr_y: float,
    url_line_height: float = 11.3,
) -> None:
    # Background + border
    c.setFillColor(COL_QR_BG)
    c.setStrokeColor(COL_QR_BORDER)
    c.setLineWidth(0.5)
    c.rect(CONTENT_LEFT, bottom, CARD_RIGHT - CONTENT_LEFT, top - bottom, stroke=1, fill=1)

    # QR image
    qr_img = make_qr_image(qr_url)
    c.drawImage(
        qr_img,
        QR_IMG_LEFT,
        img_bottom,
        width=QR_IMG_RIGHT - QR_IMG_LEFT,
        height=img_top - img_bottom,
        preserveAspectRatio=True,
        mask="auto",
    )

    # Title
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(COL_TITLE)
    c.drawString(QR_TEXT_X, title_y, title)

    # URL lines (may be split on 2 lines for QR card 1 to match original)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(COL_URL)
    y = url_first_y
    for line in url_lines:
        c.drawString(QR_TEXT_X, y, line)
        y -= url_line_height

    # Instruction (italic gray)
    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(COL_HEADFOOT)
    c.drawString(QR_TEXT_X, instr_y, instruction)


def _split_url_two_lines(url: str, breakpoint: str = "/arsmotes_handlingar/") -> list[str]:
    """Mimic the original PDF's 2-line URL break for QR card 1."""
    idx = url.find(breakpoint)
    if idx == -1:
        return [url]
    return [url[:idx], url[idx:]]


def draw_qr_cards(c: Canvas, content: Page1Content) -> None:
    # Card 1 — annual report
    qr1_url = content.annual_report_url
    draw_qr_card(
        c,
        top=QR_CARD1_TOP,
        bottom=QR_CARD1_BOTTOM,
        img_top=QR_CARD1_IMG_TOP,
        img_bottom=QR_CARD1_IMG_BOTTOM,
        qr_url=qr1_url,
        title=content.annual_report_label,
        url_lines=_split_url_two_lines(qr1_url),
        instruction="Skanna QR-koden eller besök adressen i din webbläsare",
        title_y=QR_CARD1_TITLE_Y,
        url_first_y=QR_CARD1_URL1_Y,
        instr_y=QR_CARD1_INSTR_Y,
    )
    # Card 2 — interactive web guide
    draw_qr_card(
        c,
        top=QR_CARD2_TOP,
        bottom=QR_CARD2_BOTTOM,
        img_top=QR_CARD2_IMG_TOP,
        img_bottom=QR_CARD2_IMG_BOTTOM,
        qr_url=WEB_GUIDE_URL,
        title="Denna guide finns även som interaktiv webbsida",
        url_lines=[WEB_GUIDE_URL],
        instruction="Skanna QR-koden eller besök adressen i din webbläsare",
        title_y=QR_CARD2_TITLE_Y,
        url_first_y=QR_CARD2_URL_Y,
        instr_y=QR_CARD2_INSTR_Y,
    )


def draw_next_section_start(c: Canvas, content: Page1Content) -> None:
    # Divider above next section
    c.setStrokeColor(COL_DIVIDER)
    c.setLineWidth(0.5)
    c.line(CONTENT_LEFT, HSB_DIVIDER_Y, CARD_RIGHT, HSB_DIVIDER_Y)

    # Heading (continues to page 2 in the original)
    c.setFont("Helvetica-Bold", 20)
    c.setFillColor(COL_TITLE)
    c.drawString(CONTENT_LEFT, HSB_HEADING_BASELINE_Y, content.next_section_heading)


def render_page1(c: Canvas, content: Page1Content) -> None:
    draw_header_footer(c, page_number=1)
    draw_title_block(c, content)
    draw_inledning(c, content)
    draw_two_col_cards(c, content)
    draw_callout(c, content)
    draw_post_callout_paragraph(c, content)
    draw_qr_cards(c, content)
    draw_next_section_start(c, content)


# ---------------------------------------------------------------------------
# Page 2 renderer
# ---------------------------------------------------------------------------


def draw_p2_intro(c: Canvas, content: Page2Content) -> None:
    draw_rich_paragraph(
        c,
        content.intro_paragraph,
        CONTENT_LEFT,
        P2_BODY_FIRST_LINE_Y,
        regular_font="Helvetica",
        bold_font="Helvetica-Bold",
        size=10.5,
        color=COL_BODY,
        max_width=CONTENT_RIGHT - CONTENT_LEFT,
        line_height=P2_BODY_LINE_HEIGHT,
    )


def draw_p2_hsb_qr_card(c: Canvas, content: Page2Content) -> None:
    # Background + border
    c.setFillColor(COL_QR_BG)
    c.setStrokeColor(COL_QR_BORDER)
    c.setLineWidth(0.5)
    c.rect(
        CONTENT_LEFT,
        P2_QR_BOTTOM,
        CARD_RIGHT - CONTENT_LEFT,
        P2_QR_TOP - P2_QR_BOTTOM,
        stroke=1,
        fill=1,
    )

    # QR image
    qr_img = make_qr_image(content.hsb_qr_url)
    c.drawImage(
        qr_img,
        QR_IMG_LEFT,
        P2_QR_IMG_BOTTOM,
        width=QR_IMG_RIGHT - QR_IMG_LEFT,
        height=P2_QR_IMG_TOP - P2_QR_IMG_BOTTOM,
        preserveAspectRatio=True,
        mask="auto",
    )

    # Title
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(COL_TITLE)
    c.drawString(QR_TEXT_X, P2_QR_TITLE_Y, content.hsb_qr_title)

    # URL
    c.setFont("Helvetica", 7.5)
    c.setFillColor(COL_URL)
    c.drawString(QR_TEXT_X, P2_QR_URL_Y, content.hsb_qr_url)

    # Instruction
    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(COL_HEADFOOT)
    c.drawString(QR_TEXT_X, P2_QR_INSTR_Y, "Skanna QR-koden eller besök adressen i din webbläsare")


def draw_p2_subsection_heading(c: Canvas, content: Page2Content) -> None:
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(COL_SUBTITLE)
    c.drawString(CONTENT_LEFT, P2_SUBSECTION_BASELINE_Y, content.subsection_heading)


def _draw_nyckeltal_card(
    c: Canvas,
    *,
    x_left: float,
    x_right: float,
    y_top_rl: float,     # ReportLab y of card top
    y_bottom_rl: float,  # ReportLab y of card bottom
    card: NyckeltalCard,
) -> None:
    """Draw one numbered key-figure card: dark navy header + cream body + gold circle badge."""
    width = x_right - x_left
    total_h = y_top_rl - y_bottom_rl
    # Header occupies upper ~55% of card (matches original page 2)
    header_h = (363.803 - 328.087) / (363.803 - 284.433)  # body fraction
    body_h_rl = total_h * header_h           # cream body height in RL units
    body_y_top_rl = y_bottom_rl + body_h_rl  # top of cream body in RL coords
    # In RL coords: header is from body_y_top_rl up to y_top_rl

    # Header (dark navy)
    c.setFillColor(COL_NYCKELTAL_HEADER)
    c.rect(x_left, body_y_top_rl, width, y_top_rl - body_y_top_rl, stroke=0, fill=1)

    # Body (cream)
    c.setFillColor(COL_NYCKELTAL_BODY)
    c.rect(x_left, y_bottom_rl, width, body_h_rl, stroke=0, fill=1)

    # Outer border
    c.setStrokeColor(COL_QR_BORDER)
    c.setLineWidth(0.5)
    c.rect(x_left, y_bottom_rl, width, total_h, stroke=1, fill=0)

    # Gold circle badge — centered on card width, offset from card top
    badge_cx = (x_left + x_right) / 2.0
    badge_r = P2_BADGE_DIAMETER / 2.0
    # badge top in PDF coords = card_top_pdf + P2_BADGE_OFFSET_FROM_TOP
    # badge center y (RL) = y_top_rl - P2_BADGE_OFFSET_FROM_TOP - badge_r
    badge_cy = y_top_rl - P2_BADGE_OFFSET_FROM_TOP - badge_r
    c.setFillColor(COL_BADGE)
    c.circle(badge_cx, badge_cy, badge_r, stroke=0, fill=1)

    # Number (white, centered on badge)
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(HexColor("#ffffff"))
    num_w = c.stringWidth(card.number, "Helvetica-Bold", 12)
    # Vertical centering: baseline shift ≈ 12 * 0.3
    c.drawString(badge_cx - num_w / 2.0, badge_cy - 4.0, card.number)

    # Label (Helvetica-Bold 9pt #333) — positioned in cream body
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(COL_BODY)
    label_baseline_y = body_y_top_rl - P2_CARD_TITLE_BASELINE_OFFSET
    c.drawString(x_left + P2_CARD_PAD_LEFT, label_baseline_y, card.label)

    # Description (Helvetica 7.5pt #666, wrap to 2 lines).
    # Asymmetric padding: ample left margin, tight right margin to match original.
    desc_max_w = width - P2_CARD_PAD_LEFT - P2_CARD_PAD_RIGHT
    c.setFont("Helvetica", 7.5)
    c.setFillColor(COL_LEAD)
    desc_lines = wrap_text(c, card.desc, "Helvetica", 7.5, desc_max_w)
    y = body_y_top_rl - P2_CARD_DESC_LINE1_OFFSET
    for line in desc_lines[:3]:
        c.drawString(x_left + P2_CARD_PAD_LEFT, y, line)
        y -= P2_CARD_DESC_LINE_HEIGHT


def draw_p2_nyckeltal_grid(c: Canvas, content: Page2Content) -> None:
    # Row 1: cards 1-4
    for i, (x_left, x_right) in enumerate(P2_ROW1_X):
        _draw_nyckeltal_card(
            c,
            x_left=x_left,
            x_right=x_right,
            y_top_rl=P2_ROW1_TOP,
            y_bottom_rl=P2_ROW1_BOTTOM,
            card=content.cards[i],
        )
    # Row 2: cards 5-7
    for i, (x_left, x_right) in enumerate(P2_ROW2_X):
        _draw_nyckeltal_card(
            c,
            x_left=x_left,
            x_right=x_right,
            y_top_rl=P2_ROW2_TOP,
            y_bottom_rl=P2_ROW2_BOTTOM,
            card=content.cards[4 + i],
        )


def draw_p2_closing(c: Canvas, content: Page2Content) -> None:
    draw_paragraph(
        c,
        content.closing_paragraph,
        CONTENT_LEFT,
        P2_CLOSING_FIRST_LINE_Y,
        font="Helvetica",
        size=10.5,
        color=COL_BODY,
        max_width=CONTENT_RIGHT - CONTENT_LEFT,
        line_height=DARK_LINE_HEIGHT,
    )

    # Bottom divider
    c.setStrokeColor(COL_DIVIDER)
    c.setLineWidth(0.5)
    c.line(CONTENT_LEFT, P2_BOTTOM_DIVIDER_Y, CARD_RIGHT, P2_BOTTOM_DIVIDER_Y)


def render_page2(c: Canvas, content: Page2Content) -> None:
    draw_header_footer(c, page_number=2)
    draw_p2_intro(c, content)
    draw_p2_hsb_qr_card(c, content)
    draw_p2_subsection_heading(c, content)
    draw_p2_nyckeltal_grid(c, content)
    draw_p2_closing(c, content)


# ---------------------------------------------------------------------------
# Page 3 renderer — "1. Sparande" detail page.
# Reusable primitives that will also serve detail pages 5/7/9/11/13/15…
# ---------------------------------------------------------------------------


def draw_section_card_frame(c: Canvas) -> None:
    """Draw the big white-with-navy-header section card outline (page 3 area)."""
    # Outer rect (white fill, navy border)
    width = CARD_RIGHT - CONTENT_LEFT
    height = P3_SECTION_CARD_TOP - P3_SECTION_CARD_BOTTOM
    c.setFillColor(HexColor("#ffffff"))
    c.setStrokeColor(COL_BLUE_BORDER)
    c.setLineWidth(1.0)
    c.rect(CONTENT_LEFT, P3_SECTION_CARD_BOTTOM, width, height, stroke=1, fill=1)

    # Dark navy header band (covers full width inside the border)
    band_top_rl = PAGE_H - P3_HEADER_BAND_TOP_PDF
    band_bot_rl = PAGE_H - P3_HEADER_BAND_BOTTOM_PDF
    c.setFillColor(COL_NYCKELTAL_HEADER)
    c.rect(CONTENT_LEFT, band_bot_rl, width, band_top_rl - band_bot_rl, stroke=0, fill=1)


def draw_section_card_header(c: Canvas, card: SectionCardContent) -> None:
    """Draw the numbered gold badge + section title in the navy header band."""
    # Gold circle badge
    badge_r = P3_BADGE_DIAMETER / 2.0
    badge_cx = P3_BADGE_LEFT_PDF + badge_r
    badge_cy = PAGE_H - (P3_BADGE_TOP_PDF + badge_r)
    c.setFillColor(COL_BADGE)
    c.circle(badge_cx, badge_cy, badge_r, stroke=0, fill=1)

    # White number, centered on badge
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(HexColor("#ffffff"))
    num_w = c.stringWidth(card.number, "Helvetica-Bold", 11)
    c.drawString(badge_cx - num_w / 2.0, badge_cy - 3.7, card.number)

    # Section title in white
    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(HexColor("#ffffff"))
    c.drawString(badge_cx + badge_r + 8.0, P3_TITLE_BASELINE_Y, card.title)


def draw_section_card_body(c: Canvas, card: SectionCardContent) -> None:
    """Draw body paragraph, formula pill, benchmark heading, benchmark rows, footnotes."""
    # Body paragraph
    body_max_w = CARD_RIGHT - CONTENT_LEFT - P3_BODY_PAD_LEFT - P3_BODY_PAD_RIGHT
    draw_rich_paragraph(
        c,
        card.body,
        CONTENT_LEFT + P3_BODY_PAD_LEFT,
        P3_BODY_FIRST_LINE_Y,
        regular_font="Helvetica",
        bold_font="Helvetica-Bold",
        size=9.5,
        color=COL_BODY,
        max_width=body_max_w,
        line_height=P3_BODY_LINE_HEIGHT,
    )

    # Formula pill (gray rounded rect)
    pill_w = P3_PILL_RIGHT - P3_PILL_LEFT
    pill_h = P3_PILL_TOP - P3_PILL_BOTTOM
    c.setFillColor(COL_QR_BG)
    c.setStrokeColor(COL_QR_BORDER)
    c.setLineWidth(0.5)
    c.rect(P3_PILL_LEFT, P3_PILL_BOTTOM, pill_w, pill_h, stroke=1, fill=1)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(COL_LEAD)
    c.drawString(P3_PILL_LEFT + 8.5, P3_PILL_TEXT_BASELINE_Y, card.formula)

    # "Riktvärden (...)" sub-heading
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(COL_TITLE)
    c.drawString(
        CONTENT_LEFT + P3_BODY_PAD_LEFT,
        P3_RIKTVARDE_HEADING_BASELINE_Y,
        card.benchmark_heading,
    )

    # Benchmark rows (4 fixed positions, color circles + label + value)
    circle_r = P3_BENCHMARK_CIRCLE_DIAMETER / 2.0
    for i, row in enumerate(card.benchmarks[:4]):
        cy = P3_BENCHMARK_ROW_Y_CENTERS[i]
        color = BENCHMARK_COLORS[i]
        c.setFillColor(color)
        c.circle(P3_BENCHMARK_CIRCLE_X_CENTER, cy, circle_r, stroke=0, fill=1)
        baseline_y = cy + P3_BENCHMARK_BASELINE_OFFSET
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(COL_BODY)
        c.drawString(P3_BENCHMARK_LABEL_X, baseline_y, row.label)
        c.setFont("Helvetica", 9)
        c.drawString(P3_BENCHMARK_VALUE_X, baseline_y, row.value)

    # Two italic footnote lines
    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillColor(COL_LEAD)
    if card.footnote_intro:
        c.drawString(CONTENT_LEFT + P3_BODY_PAD_LEFT, P3_FOOTNOTE1_BASELINE_Y, card.footnote_intro)
    if card.footnote_source:
        c.drawString(CONTENT_LEFT + P3_BODY_PAD_LEFT, P3_FOOTNOTE2_BASELINE_Y, card.footnote_source)


def draw_h3(c: Canvas, text: str, baseline_y: float) -> None:
    c.setFont("Helvetica-Bold", H3_FONT_SIZE)
    c.setFillColor(COL_TITLE)
    c.drawString(CONTENT_LEFT, baseline_y, text)


def draw_blue_callout(
    c: Canvas,
    callout: BlueCalloutContent,
    *,
    box_top: float,
    box_bottom: float,
    title_baseline_y: float,
    paragraph_first_line_ys: list[float],
    line_height: float = 14.0,
) -> None:
    """Draw the blue callout box (variant of the yellow one)."""
    box_w = CARD_RIGHT - CONTENT_LEFT
    box_h = box_top - box_bottom

    # Fill + border
    c.setFillColor(COL_BLUE_FILL)
    c.setStrokeColor(COL_BLUE_BORDER)
    c.setLineWidth(1.2)
    c.rect(CONTENT_LEFT, box_bottom, box_w, box_h, stroke=1, fill=1)

    # Left accent strip
    c.setFillColor(COL_BLUE_BORDER)
    c.rect(CONTENT_LEFT, box_bottom, P3_BLUE_CALLOUT_LEFT_ACCENT_W, box_h, stroke=0, fill=1)

    # Title
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(COL_TITLE)
    c.drawString(CONTENT_LEFT + CALLOUT_PAD_X, title_baseline_y, callout.title)

    # Paragraphs
    body_max_w = CARD_RIGHT - (CONTENT_LEFT + CALLOUT_PAD_X) - 10
    for i, runs in enumerate(callout.paragraphs):
        if i >= len(paragraph_first_line_ys):
            break
        draw_rich_paragraph(
            c,
            runs,
            CONTENT_LEFT + CALLOUT_PAD_X,
            paragraph_first_line_ys[i],
            regular_font="Helvetica",
            bold_font="Helvetica-Bold",
            size=10,
            color=COL_BODY,
            max_width=body_max_w,
            line_height=line_height,
        )


# ---------------------------------------------------------------------------
# Page 4 renderer — sparande data table + historical bar chart.
# ---------------------------------------------------------------------------


def draw_data_table(c: Canvas, table: DataTable) -> None:
    """Draw the 3-row year-comparison table (dark-navy header + 2 striped data rows)."""
    if not table.rows or len(table.column_headers) < 2:
        return

    n_cols = len(table.column_headers)  # includes label column
    col_xs = P4_TABLE_COL_X
    if len(col_xs) < n_cols + 1:
        # fallback: equal width
        col_xs = [P4_TABLE_LEFT + i * (P4_TABLE_RIGHT - P4_TABLE_LEFT) / n_cols for i in range(n_cols + 1)]

    # Background fills
    c.setFillColor(COL_NYCKELTAL_HEADER)
    c.rect(P4_TABLE_LEFT, P4_TABLE_HEADER_BOTTOM,
           P4_TABLE_RIGHT - P4_TABLE_LEFT, P4_TABLE_HEADER_TOP - P4_TABLE_HEADER_BOTTOM,
           stroke=0, fill=1)
    c.setFillColor(COL_BLUE_FILL)  # light blue stripe
    c.rect(P4_TABLE_LEFT, P4_TABLE_ROW1_BOTTOM,
           P4_TABLE_RIGHT - P4_TABLE_LEFT, P4_TABLE_HEADER_BOTTOM - P4_TABLE_ROW1_BOTTOM,
           stroke=0, fill=1)
    c.setFillColor(HexColor("#f8f8f8"))  # light gray stripe
    c.rect(P4_TABLE_LEFT, P4_TABLE_ROW2_BOTTOM,
           P4_TABLE_RIGHT - P4_TABLE_LEFT, P4_TABLE_ROW1_BOTTOM - P4_TABLE_ROW2_BOTTOM,
           stroke=0, fill=1)

    # Grid lines (all #dddddd 0.5pt)
    c.setStrokeColor(COL_DIVIDER)
    c.setLineWidth(0.5)
    for y in (P4_TABLE_HEADER_TOP, P4_TABLE_HEADER_BOTTOM, P4_TABLE_ROW1_BOTTOM, P4_TABLE_ROW2_BOTTOM):
        c.line(P4_TABLE_LEFT, y, P4_TABLE_RIGHT, y)
    for x in col_xs:
        c.line(x, P4_TABLE_ROW2_BOTTOM, x, P4_TABLE_HEADER_TOP)

    # Header text — year columns 1..n centered
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(HexColor("#ffffff"))
    for i in range(1, n_cols):
        text = table.column_headers[i]
        cx = (col_xs[i] + col_xs[i + 1]) / 2.0
        tw = c.stringWidth(text, "Helvetica-Bold", 9.5)
        c.drawString(cx - tw / 2.0, P4_TABLE_HEADER_TEXT_BASELINE_Y, text)

    # Data row 1: label left-aligned, values centered, all 9.5pt
    if len(table.rows) >= 1:
        row1 = table.rows[0]
        if row1:
            c.setFont("Helvetica-Bold", 9.5)
            c.setFillColor(COL_BODY)
            c.drawString(P4_TABLE_LEFT + 6.0, P4_TABLE_ROW1_TEXT_BASELINE_Y, row1[0])
            c.setFont("Helvetica", 9.5)
            for i in range(1, min(len(row1), n_cols)):
                text = row1[i]
                cx = (col_xs[i] + col_xs[i + 1]) / 2.0
                tw = c.stringWidth(text, "Helvetica", 9.5)
                c.drawString(cx - tw / 2.0, P4_TABLE_ROW1_TEXT_BASELINE_Y, text)

    # Data row 2: italic 8pt #666, label left, values centered
    if len(table.rows) >= 2:
        row2 = table.rows[1]
        if row2:
            c.setFont("Helvetica-Oblique", 8)
            c.setFillColor(COL_LEAD)
            c.drawString(P4_TABLE_LEFT + 6.0, P4_TABLE_ROW2_TEXT_BASELINE_Y, row2[0])
            for i in range(1, min(len(row2), n_cols)):
                text = row2[i]
                cx = (col_xs[i] + col_xs[i + 1]) / 2.0
                tw = c.stringWidth(text, "Helvetica-Oblique", 8)
                c.drawString(cx - tw / 2.0, P4_TABLE_ROW2_TEXT_BASELINE_Y, text)


def draw_p4_nav_paragraph(c: Canvas, text: str) -> None:
    """Draw the light-blue navigation hint paragraph with ZapfDingbats bullet + Unicode → arrows."""
    if not text:
        return
    # Strip "Var i årsredovisningen:" leading marker characters from HTML if any.
    # The original PDF prepends a ▪ bullet using ZapfDingbats; we replicate with a small square.
    bullet_w = 8.0
    text_color = HexColor("#4a90c4")
    max_width = CONTENT_RIGHT - CONTENT_LEFT - bullet_w

    # Tokenise body, wrap with simple greedy split
    lines = wrap_text(c, text, "Helvetica", 8, max_width)
    y = P4_NAV_FIRST_LINE_Y
    c.setFillColor(text_color)
    for i, line in enumerate(lines):
        if i == 0:
            # Bullet: small filled blue square
            c.setFillColor(text_color)
            c.rect(CONTENT_LEFT, y - 1.5, 5.5, 5.5, stroke=0, fill=1)
            c.setFont("Helvetica", 8)
            c.drawString(CONTENT_LEFT + bullet_w, y, line)
        else:
            c.setFont("Helvetica", 8)
            c.drawString(CONTENT_LEFT, y, line)
        y -= P4_NAV_LINE_HEIGHT


def _count_rich_lines(c: Canvas, runs: RichText, regular_font: str, bold_font: str,
                      size: float, max_width: float) -> int:
    """Return how many lines `draw_rich_paragraph` would produce for these runs."""
    tokens: list[tuple[str, bool]] = []
    for text, bold in runs:
        if not text:
            continue
        for part in re.split(r"(\s+)", text):
            if not part or part.isspace():
                continue
            tokens.append((part, bold))
    if not tokens:
        return 0
    space_w_reg = c.stringWidth(" ", regular_font, size)
    space_w_bold = c.stringWidth(" ", bold_font, size)
    n_lines = 1
    current_w = 0.0
    for i, (tok, bold) in enumerate(tokens):
        f = bold_font if bold else regular_font
        leading = (space_w_bold if bold else space_w_reg) if i > 0 and current_w > 0 else 0.0
        tw = c.stringWidth(tok, f, size)
        if current_w + leading + tw > max_width and current_w > 0:
            n_lines += 1
            current_w = tw
        else:
            current_w += leading + tw
    return n_lines


def render_page4(c: Canvas, content: Page4Content) -> None:
    draw_header_footer(c, page_number=4)

    # Data table at top
    draw_data_table(c, content.table)

    # Navigation hint (light blue) — may be 2 lines
    draw_p4_nav_paragraph(c, content.nav_paragraph)

    # Body paragraph (variable line count — original was 3 lines, may now be more)
    body_size = 10.5
    body_lh = P4_BODY_LINE_HEIGHT
    body_max_w = CONTENT_RIGHT - CONTENT_LEFT
    body_lines = _count_rich_lines(c, content.body_paragraph, "Helvetica", "Helvetica-Bold",
                                   body_size, body_max_w)
    draw_rich_paragraph(
        c,
        content.body_paragraph,
        CONTENT_LEFT,
        P4_BODY_FIRST_LINE_Y,
        regular_font="Helvetica",
        bold_font="Helvetica-Bold",
        size=body_size,
        color=COL_BODY,
        max_width=body_max_w,
        line_height=body_lh,
    )
    # Last text baseline of body paragraph (in RL coords; goes downward)
    body_last_baseline_rl = P4_BODY_FIRST_LINE_Y - (body_lines - 1) * body_lh

    # Bar chart — top placed below body paragraph with a small gap, bottom anchored to
    # original PDF position so caption + downstream content stay aligned.
    chart_gap = 18.0
    chart_top_rl = min(P4_CHART_TOP_RL, body_last_baseline_rl - chart_gap)
    chart_bottom_rl = P4_CHART_BOTTOM_RL
    if chart_top_rl - chart_bottom_rl < 130:
        # If body paragraph crowded the chart, push chart caption + downstream content down too.
        chart_bottom_rl = chart_top_rl - 150
    chart_w = P4_CHART_RIGHT - P4_CHART_LEFT
    chart_h = chart_top_rl - chart_bottom_rl
    series = compute_sparande_per_kvm()
    chart_img = render_sparande_history_chart_png(series, width_pt=chart_w, height_pt=chart_h)
    c.drawImage(chart_img, P4_CHART_LEFT, chart_bottom_rl,
                width=chart_w, height=chart_h, preserveAspectRatio=False, mask="auto")

    # Chart caption (italic, centered) — placed just below the chart
    caption_y = chart_bottom_rl - 10.0
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(COL_LEAD)
    cap_x = (P4_CHART_LEFT + P4_CHART_RIGHT) / 2.0
    cap_w = c.stringWidth(content.chart_caption, "Helvetica-Oblique", 8)
    c.drawString(cap_x - cap_w / 2.0, caption_y, content.chart_caption)

    # h3 "Vad bygger sparandet?"
    h3_y = caption_y - 35.0
    draw_h3(c, content.h3_vad_bygger, h3_y)

    # Body paragraph (4 lines) after h3
    byggs_first_y = h3_y - 19.0
    draw_rich_paragraph(
        c,
        content.byggs_paragraph,
        CONTENT_LEFT,
        byggs_first_y,
        regular_font="Helvetica",
        bold_font="Helvetica-Bold",
        size=10.5,
        color=COL_BODY,
        max_width=CONTENT_RIGHT - CONTENT_LEFT,
        line_height=15.0,
    )

    # Single-line: "Här är beräkningen ..."
    byggs_lines = _count_rich_lines(c, content.byggs_paragraph, "Helvetica", "Helvetica-Bold",
                                    10.5, CONTENT_RIGHT - CONTENT_LEFT)
    har_ar_y = byggs_first_y - byggs_lines * 15.0 - 6.0
    c.setFont("Helvetica", 10.5)
    c.setFillColor(COL_BODY)
    c.drawString(CONTENT_LEFT, har_ar_y, content.har_ar_paragraph)


def render_page3(c: Canvas, content: Page3Content) -> None:
    draw_header_footer(c, page_number=3)

    # Top section card
    draw_section_card_frame(c)
    draw_section_card_header(c, content.section_card)
    draw_section_card_body(c, content.section_card)

    # "Vad betyder sparande i praktiken?" subheading + body paragraph
    draw_h3(c, content.h3_vad_betyder, P3_H3_VAD_BETYDER_BASELINE_Y)
    # Body paragraph: 3 lines, starts ~404 (PDF) → first baseline 418.5 (PDF) = PAGE_H - 418.5
    draw_rich_paragraph(
        c,
        content.ordet_paragraph,
        CONTENT_LEFT,
        PAGE_H - 418.5,
        regular_font="Helvetica",
        bold_font="Helvetica-Bold",
        size=10.5,
        color=COL_BODY,
        max_width=CONTENT_RIGHT - CONTENT_LEFT,
        line_height=15.0,
    )

    # Blue callout
    draw_blue_callout(
        c,
        content.callout,
        box_top=P3_BLUE_CALLOUT_TOP,
        box_bottom=P3_BLUE_CALLOUT_BOTTOM,
        title_baseline_y=P3_CALLOUT_TITLE_BASELINE_Y,
        paragraph_first_line_ys=[
            P3_CALLOUT_PARA1_FIRST_LINE_Y,
            P3_CALLOUT_PARA2_FIRST_LINE_Y,
            P3_CALLOUT_PARA3_FIRST_LINE_Y,
        ],
        line_height=P3_CALLOUT_LINE_HEIGHT,
    )

    # "Med detta i åtanke ..." paragraph
    draw_paragraph(
        c,
        content.med_detta_paragraph,
        CONTENT_LEFT,
        P3_MED_DETTA_BASELINE_Y,
        font="Helvetica",
        size=10.5,
        color=COL_BODY,
        max_width=CONTENT_RIGHT - CONTENT_LEFT,
        line_height=DARK_LINE_HEIGHT,
    )

    # "BRF Sjötungans sparande" subheading
    draw_h3(c, content.h3_sjotungans, P3_H3_SJOTUNGANS_BASELINE_Y)

    # "I flerårsöversikten ..." (first 2 lines visible on page 3)
    draw_rich_paragraph(
        c,
        content.flerar_paragraph,
        CONTENT_LEFT,
        P3_FLERAR_FIRST_LINE_Y,
        regular_font="Helvetica",
        bold_font="Helvetica-Bold",
        size=10.5,
        color=COL_BODY,
        max_width=CONTENT_RIGHT - CONTENT_LEFT,
        line_height=15.0,
    )


# ===========================================================================
# Section-template engine — block-based content flow.
#
# Each detail section (Sparande, Investeringsbehov, …) is a long sequence of
# similar building blocks: paragraphs, h3 subheadings, callouts, lists,
# tables, charts and QR cards. Hardcoded y-positions don't scale across 28
# pages, so this engine extracts a stream of Block objects from a
# guide-section and renders them top-down, inserting page breaks as needed.
# ===========================================================================


# Flow geometry (where blocks live on each page)
FLOW_TOP_Y     = PAGE_H - 95.0    # first block top edge on a flow page
FLOW_BOTTOM_Y  = PAGE_H - 780.0   # do not render below this y
DEFAULT_BLOCK_GAP = 10.0


class Block:
    """Abstract — every block must know its height and how to render itself."""

    def height(self, c: Canvas, max_width: float) -> float:
        raise NotImplementedError

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        """Render starting at y_top (the *top* of the block, RL coords).
        Return y_bottom (the y of the lowest pixel/baseline drawn)."""
        raise NotImplementedError


@dataclass
class HeadingBlock(Block):
    text: str
    level: int = 3   # 2 = section heading (20pt), 3 = subsection (12pt)
    color: HexColor = COL_TITLE
    gap_above: float = 14.0
    gap_below: float = 6.0

    def _font_size(self) -> float:
        return 20 if self.level == 2 else 12

    def height(self, c: Canvas, max_width: float) -> float:
        return self.gap_above + self._font_size() + self.gap_below

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        size = self._font_size()
        baseline = y_top - self.gap_above - size * 0.85
        c.setFont("Helvetica-Bold", size)
        c.setFillColor(self.color)
        c.drawString(x, baseline, self.text)
        return y_top - self.height(c, max_width)


@dataclass
class ParagraphBlock(Block):
    runs: RichText
    size: float = 10.5
    line_height: float = 15.0
    color: HexColor = COL_BODY

    def _lines(self, c: Canvas, max_width: float) -> int:
        return _count_rich_lines(c, self.runs, "Helvetica", "Helvetica-Bold",
                                 self.size, max_width)

    def height(self, c: Canvas, max_width: float) -> float:
        return self._lines(c, max_width) * self.line_height + 4.0

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        first_baseline = y_top - self.size * 0.85
        draw_rich_paragraph(
            c, self.runs, x, first_baseline,
            regular_font="Helvetica", bold_font="Helvetica-Bold",
            size=self.size, color=self.color,
            max_width=max_width, line_height=self.line_height,
        )
        return y_top - self.height(c, max_width)


@dataclass
class ListBlock(Block):
    """Numbered or bulleted list. Each item is a RichText paragraph."""
    items: list[RichText]
    ordered: bool = False
    size: float = 10.5
    line_height: float = 15.0
    indent: float = 22.0
    item_gap: float = 4.0

    def _lines_per_item(self, c: Canvas, max_width: float) -> list[int]:
        usable = max_width - self.indent
        return [
            _count_rich_lines(c, item, "Helvetica", "Helvetica-Bold", self.size, usable)
            for item in self.items
        ]

    def height(self, c: Canvas, max_width: float) -> float:
        line_counts = self._lines_per_item(c, max_width)
        total_lines = sum(line_counts)
        return total_lines * self.line_height + (len(self.items) - 1) * self.item_gap + 4.0

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        usable = max_width - self.indent
        y = y_top - self.size * 0.85
        for i, item in enumerate(self.items):
            marker = f"{i+1}." if self.ordered else "•"
            c.setFont("Helvetica-Bold" if self.ordered else "Helvetica", self.size)
            c.setFillColor(self.color if hasattr(self, "color") else COL_BODY)
            c.setFillColor(COL_BODY)
            c.drawString(x, y, marker)
            draw_rich_paragraph(
                c, item, x + self.indent, y,
                regular_font="Helvetica", bold_font="Helvetica-Bold",
                size=self.size, color=COL_BODY,
                max_width=usable, line_height=self.line_height,
            )
            n_lines = _count_rich_lines(c, item, "Helvetica", "Helvetica-Bold",
                                        self.size, usable)
            y -= n_lines * self.line_height + self.item_gap
        return y


@dataclass
class CalloutBlock(Block):
    """Blue or yellow callout box (multi-paragraph). Reuses draw_blue_callout / draw_callout."""
    title: str
    paragraphs: list[RichText]
    variant: str = "blue"   # "blue" or "yellow"
    line_height: float = 14.0
    size: float = 10.0
    inner_pad_top: float = 18.0
    inner_pad_bottom: float = 12.0
    para_gap: float = 8.0

    def _inner_width(self, max_width: float) -> float:
        return max_width - CALLOUT_PAD_X - 10

    title_to_body_gap: float = 10.0

    def height(self, c: Canvas, max_width: float) -> float:
        inner_w = self._inner_width(max_width)
        title_h = 14.0 if self.title else 0.0
        title_to_body = self.title_to_body_gap if self.title else 0.0
        body_h = 0.0
        for runs in self.paragraphs:
            n_lines = _count_rich_lines(c, runs, "Helvetica", "Helvetica-Bold",
                                        self.size, inner_w)
            body_h += n_lines * self.line_height + self.para_gap
        if self.paragraphs:
            body_h -= self.para_gap
        return self.inner_pad_top + title_h + title_to_body + body_h + self.inner_pad_bottom

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        h = self.height(c, max_width)
        box_top = y_top
        box_bottom = y_top - h

        fill = COL_BLUE_FILL if self.variant == "blue" else COL_YELLOW_FILL
        border = COL_BLUE_BORDER if self.variant == "blue" else COL_YELLOW_BORD
        c.setFillColor(fill)
        c.setStrokeColor(border)
        c.setLineWidth(1.2)
        c.rect(x, box_bottom, max_width, h, stroke=1, fill=1)

        # Left accent strip
        c.setFillColor(border)
        c.rect(x, box_bottom, P3_BLUE_CALLOUT_LEFT_ACCENT_W, h, stroke=0, fill=1)

        # Title (only if present)
        if self.title:
            title_baseline = box_top - self.inner_pad_top
            c.setFont("Helvetica-Bold", 11)
            c.setFillColor(COL_TITLE if self.variant == "blue" else COL_CALLOUT_HEAD)
            c.drawString(x + CALLOUT_PAD_X, title_baseline, self.title)
            y = title_baseline - self.title_to_body_gap - self.size * 0.85
        else:
            y = box_top - self.inner_pad_top - self.size * 0.85

        # Paragraphs
        inner_w = self._inner_width(max_width)
        for runs in self.paragraphs:
            draw_rich_paragraph(
                c, runs, x + CALLOUT_PAD_X, y,
                regular_font="Helvetica", bold_font="Helvetica-Bold",
                size=self.size, color=COL_BODY,
                max_width=inner_w, line_height=self.line_height,
            )
            n_lines = _count_rich_lines(c, runs, "Helvetica", "Helvetica-Bold",
                                        self.size, inner_w)
            y -= n_lines * self.line_height + self.para_gap

        return box_bottom


def _draw_wrapped_caption(c: Canvas, text: str, *, center_x: float, top_y: float,
                          max_width: float, font: str = "Helvetica-Oblique",
                          size: float = 8.0, color: HexColor = COL_LEAD,
                          line_height: float = 10.5) -> float:
    """Draw an italic, centered caption that wraps to multiple lines.
    Returns the y of the last baseline drawn."""
    c.setFont(font, size)
    c.setFillColor(color)
    lines = wrap_text(c, text, font, size, max_width)
    y = top_y
    for line in lines:
        lw = c.stringWidth(line, font, size)
        c.drawString(center_x - lw / 2.0, y, line)
        y -= line_height
    return y + line_height


def _caption_line_count(c: Canvas, text: str, *, max_width: float,
                        font: str = "Helvetica-Oblique", size: float = 8.0) -> int:
    if not text:
        return 0
    return len(wrap_text(c, text, font, size, max_width))


@dataclass
class ChartPlaceholderBlock(Block):
    """Placeholder for charts not yet implemented — draws a labeled gray box."""
    chart_id: str
    caption: str = ""
    width_pt: float = 425.0
    height_pt: float = 180.0

    def _caption_h(self, c: Canvas, max_width: float) -> float:
        if not self.caption:
            return 0.0
        n = _caption_line_count(c, self.caption, max_width=min(self.width_pt, max_width))
        return n * 10.5 + 6.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self.height_pt + self._caption_h(c, max_width)

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        box_w = min(self.width_pt, max_width)
        box_x = x + (max_width - box_w) / 2.0
        box_bottom = y_top - self.height_pt
        c.setFillColor(HexColor("#fafbff"))
        c.setStrokeColor(HexColor("#c3cee5"))
        c.setLineWidth(0.7)
        c.rect(box_x, box_bottom, box_w, self.height_pt, stroke=1, fill=1)
        c.setFillColor(HexColor("#7a8db0"))
        c.setFont("Helvetica-Oblique", 10)
        label = f"[Chart placeholder: {self.chart_id}]"
        tw = c.stringWidth(label, "Helvetica-Oblique", 10)
        c.drawString(box_x + (box_w - tw) / 2.0, box_bottom + self.height_pt / 2.0, label)

        if self.caption:
            _draw_wrapped_caption(
                c, self.caption,
                center_x=box_x + box_w / 2.0,
                top_y=box_bottom - 10.0,
                max_width=box_w,
            )
        return y_top - self.height(c, max_width)


@dataclass
class WaterfallChartBlock(Block):
    """Horizontal waterfall chart (sparande breakdown, cashflow analysis)."""
    chart_id: str
    caption: str = ""
    width_pt: float = 425.0
    base_height_pt: float = 220.0
    row_height_pt: float = 18.0

    def _items(self) -> list[WaterfallItem]:
        if self.chart_id == "sparandeWaterfallChart":
            return compute_sparande_waterfall(2025)
        if self.chart_id == "cashflowWaterfallChart":
            return compute_cashflow_waterfall(2025)
        return []

    def _chart_height(self, c: Canvas | None = None) -> float:
        items = self._items()
        # height scales with row count
        return max(self.base_height_pt, 50.0 + len(items) * self.row_height_pt)

    def _caption_h(self, c: Canvas, max_width: float) -> float:
        if not self.caption:
            return 0.0
        n = _caption_line_count(c, self.caption, max_width=min(self.width_pt, max_width))
        return n * 10.5 + 6.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self._chart_height() + self._caption_h(c, max_width)

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        box_w = min(self.width_pt, max_width)
        box_x = x + (max_width - box_w) / 2.0
        chart_h = self._chart_height()
        box_bottom = y_top - chart_h
        items = self._items()
        if items:
            img = render_waterfall_chart_png(items, width_pt=box_w, height_pt=chart_h)
            c.drawImage(img, box_x, box_bottom, width=box_w, height=chart_h,
                        preserveAspectRatio=False, mask="auto")
        if self.caption:
            _draw_wrapped_caption(
                c, self.caption,
                center_x=box_x + box_w / 2.0,
                top_y=box_bottom - 10.0,
                max_width=box_w,
            )
        return y_top - self.height(c, max_width)


_WATERFALL_CHART_IDS = {"sparandeWaterfallChart", "cashflowWaterfallChart"}


@dataclass
class ComparisonChartBlock(Block):
    """Comparison-stick chart: vertical bars with HSB band background + band labels."""
    chart_id: str
    caption: str = ""
    width_pt: float = 425.0
    height_pt: float = 240.0

    def _caption_h(self, c: Canvas, max_width: float) -> float:
        if not self.caption:
            return 0.0
        n = _caption_line_count(c, self.caption, max_width=min(self.width_pt, max_width))
        return n * 10.5 + 6.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self.height_pt + self._caption_h(c, max_width)

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        box_w = min(self.width_pt, max_width)
        box_x = x + (max_width - box_w) / 2.0
        box_bottom = y_top - self.height_pt
        data = _comparison_data(self.chart_id)
        if data is None:
            # Fallback: render a placeholder
            ChartPlaceholderBlock(chart_id=self.chart_id, caption=self.caption).render(c, x, y_top, max_width)
            return y_top - self.height(c, max_width)
        img = render_comparison_chart_png(data, width_pt=box_w, height_pt=self.height_pt)
        c.drawImage(img, box_x, box_bottom, width=box_w, height=self.height_pt,
                    preserveAspectRatio=False, mask="auto")
        if self.caption:
            _draw_wrapped_caption(
                c, self.caption,
                center_x=box_x + box_w / 2.0,
                top_y=box_bottom - 10.0,
                max_width=box_w,
            )
        return y_top - self.height(c, max_width)


_COMPARISON_CHART_IDS = {
    "sparandeComparisonChart",
    "skuldComparisonChart",
    "energiComparisonChart",
    "rantekanslighetComparisonChart",
    "arsavgiftComparisonChart",
    "ltvMarknadComparisonChart",
    "ltvTaxComparisonChart",
}


@dataclass
class SimpleBarChartBlock(Block):
    """Single-series bar chart — used by refinancingRiskChart and investmentHistoryChart."""
    chart_id: str
    caption: str = ""
    width_pt: float = 425.0
    height_pt: float = 200.0

    def _caption_h(self, c: Canvas, max_width: float) -> float:
        if not self.caption:
            return 0.0
        n = _caption_line_count(c, self.caption, max_width=min(self.width_pt, max_width))
        return n * 10.5 + 6.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self.height_pt + self._caption_h(c, max_width)

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        box_w = min(self.width_pt, max_width)
        box_x = x + (max_width - box_w) / 2.0
        box_bottom = y_top - self.height_pt

        if self.chart_id == "refinancingRiskChart":
            series = compute_refinancing_risk()
            color_fn = lambda v: ("#dc2626" if v >= 60 else "#f59e0b" if v >= 40 else "#2563eb")
            img = render_single_bar_chart_png(
                series, width_pt=box_w, height_pt=self.height_pt,
                y_label="% av totala skulder",
                threshold=60.0, threshold_label="Varningsgräns 60%",
                bar_color_fn=color_fn, y_max=100,
            )
        elif self.chart_id == "investmentHistoryChart":
            series = compute_investment_history()
            img = render_single_bar_chart_png(
                series, width_pt=box_w, height_pt=self.height_pt,
                y_label="kr/kvm", default_color="#22c55e",
            )
        else:
            return ChartPlaceholderBlock(chart_id=self.chart_id, caption=self.caption).render(c, x, y_top, max_width)

        c.drawImage(img, box_x, box_bottom, width=box_w, height=self.height_pt,
                    preserveAspectRatio=False, mask="auto")
        if self.caption:
            _draw_wrapped_caption(c, self.caption, center_x=box_x + box_w / 2.0,
                                  top_y=box_bottom - 10.0, max_width=box_w)
        return y_top - self.height(c, max_width)


_SIMPLE_BAR_CHART_IDS = {"refinancingRiskChart", "investmentHistoryChart"}


@dataclass
class PrognosChartBlock(Block):
    """Dual-line projection chart (actual + forecast) with HSB bands."""
    chart_id: str
    caption: str = ""
    width_pt: float = 425.0
    height_pt: float = 220.0

    def _config(self) -> tuple[list[float | None], list[float | None], list[HSBBand], str, float] | None:
        if self.chart_id == "prognosSkuldChart":
            return (PROGNOS_SKULD_ACTUAL, PROGNOS_SKULD_FORECAST, [
                HSBBand(0,     3000,  "#dcfce7", "Låg"),
                HSBBand(3000,  6000,  "#fef3c7", "Normal"),
                HSBBand(6000,  10000, "#fee2e2", "Hög"),
                HSBBand(10000, 16000, "#fecaca", "Mkt hög"),
            ], "kr/kvm", 16000)
        if self.chart_id == "prognosRkChart":
            return (PROGNOS_RK_ACTUAL, PROGNOS_RK_FORECAST, [
                HSBBand(0,  5,  "#dcfce7", "Låg"),
                HSBBand(5,  9,  "#fef3c7", "Normal"),
                HSBBand(9,  12, "#fee2e2", "Hög"),
                HSBBand(12, 16, "#fecaca", "Väldigt hög"),
            ], "%", 16)
        if self.chart_id == "prognosAvgiftChart":
            return (PROGNOS_AVG_ACTUAL, PROGNOS_AVG_FORECAST, [
                HSBBand(0,    500,  "#f5f5f5", ""),
                HSBBand(500,  800,  "#dcfce7", "Vanligt"),
                HSBBand(800,  1000, "#fef3c7", "Högre"),
                HSBBand(1000, 1400, "#fee2e2", "Se upp"),
            ], "kr/kvm", 1400)
        return None

    def _caption_h(self, c: Canvas, max_width: float) -> float:
        if not self.caption:
            return 0.0
        n = _caption_line_count(c, self.caption, max_width=min(self.width_pt, max_width))
        return n * 10.5 + 6.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self.height_pt + self._caption_h(c, max_width)

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        config = self._config()
        if config is None:
            return ChartPlaceholderBlock(chart_id=self.chart_id, caption=self.caption).render(c, x, y_top, max_width)
        actual, forecast, bands, y_label, y_max = config

        box_w = min(self.width_pt, max_width)
        box_x = x + (max_width - box_w) / 2.0
        box_bottom = y_top - self.height_pt

        img = render_prognos_chart_png(
            PROGNOS_YEARS, actual, forecast, bands,
            width_pt=box_w, height_pt=self.height_pt,
            y_label=y_label, y_max=y_max,
        )
        c.drawImage(img, box_x, box_bottom, width=box_w, height=self.height_pt,
                    preserveAspectRatio=False, mask="auto")
        if self.caption:
            _draw_wrapped_caption(c, self.caption, center_x=box_x + box_w / 2.0,
                                  top_y=box_bottom - 10.0, max_width=box_w)
        return y_top - self.height(c, max_width)


_PROGNOS_CHART_IDS = {"prognosSkuldChart", "prognosRkChart", "prognosAvgiftChart"}


@dataclass
class TreemapChartBlock(Block):
    """Cost distribution treemap (2-level: top categories + their child rows)."""
    chart_id: str
    caption: str = ""
    width_pt: float = 425.0
    height_pt: float = 240.0

    def _caption_h(self, c: Canvas, max_width: float) -> float:
        if not self.caption:
            return 0.0
        n = _caption_line_count(c, self.caption, max_width=min(self.width_pt, max_width))
        return n * 10.5 + 6.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self.height_pt + self._caption_h(c, max_width)

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        box_w = min(self.width_pt, max_width)
        box_x = x + (max_width - box_w) / 2.0
        box_bottom = y_top - self.height_pt
        nodes = compute_cost_treemap(2025)
        if nodes:
            img = render_treemap_chart_png(nodes, width_pt=box_w, height_pt=self.height_pt)
            c.drawImage(img, box_x, box_bottom, width=box_w, height=self.height_pt,
                        preserveAspectRatio=False, mask="auto")
        if self.caption:
            _draw_wrapped_caption(c, self.caption, center_x=box_x + box_w / 2.0,
                                  top_y=box_bottom - 10.0, max_width=box_w)
        return y_top - self.height(c, max_width)


_TREEMAP_CHART_IDS = {"costDistributionChart"}


@dataclass
class MultiLineChartBlock(Block):
    """Multi-line history chart (matplotlib). Series + bands resolved by chart_id."""
    chart_id: str
    caption: str = ""
    width_pt: float = 425.0
    height_pt: float = 200.0

    def _caption_h(self, c: Canvas, max_width: float) -> float:
        if not self.caption:
            return 0.0
        n = _caption_line_count(c, self.caption, max_width=min(self.width_pt, max_width))
        return n * 10.5 + 6.0

    def height(self, c: Canvas, max_width: float) -> float:
        # Extra ~24pt for legend at bottom for multi-series charts
        legend_pad = 24.0 if _chart_has_legend(self.chart_id) else 6.0
        return self.height_pt + legend_pad + self._caption_h(c, max_width)

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        box_w = min(self.width_pt, max_width)
        box_x = x + (max_width - box_w) / 2.0
        legend_pad = 24.0 if _chart_has_legend(self.chart_id) else 6.0
        chart_total_h = self.height_pt + legend_pad
        box_bottom = y_top - chart_total_h

        series, bands, y_label, show_legend = _resolve_chart_data(self.chart_id)
        img = render_multi_line_chart_png(
            series,
            width_pt=box_w,
            height_pt=chart_total_h,
            y_label=y_label,
            hsb_bands=bands,
            show_legend=show_legend,
        )
        c.drawImage(img, box_x, box_bottom, width=box_w, height=chart_total_h,
                    preserveAspectRatio=False, mask="auto")
        if self.caption:
            _draw_wrapped_caption(
                c, self.caption,
                center_x=box_x + box_w / 2.0,
                top_y=box_bottom - 10.0,
                max_width=box_w,
            )
        return y_top - self.height(c, max_width)


# Map chart IDs → series-resolver functions. Used by MultiLineChartBlock.
_LINE_CHART_REGISTRY = {
    "skuldHistoryChart",
    "energiHistoryChart",
    "rantekanslighetHistoryChart",
    "arsavgiftHistoryChart",
}


def _chart_has_legend(chart_id: str) -> bool:
    # Single-series charts don't need a legend.
    return chart_id in {"skuldHistoryChart", "energiHistoryChart"}


def _resolve_chart_data(chart_id: str) -> tuple[list[LineSeries], list[HSBBand] | None, str, bool]:
    """Build (series, bands, y_label, show_legend) for a given chart_id."""
    if chart_id == "skuldHistoryChart":
        rows = compute_debt_history()
        years = [y for y, _ in rows]
        total = [d["total"] for _, d in rows]
        long_t = [d["long"] for _, d in rows]
        short_t = [d["short"] for _, d in rows]
        cash = [d["cash"] for _, d in rows]
        series = [
            LineSeries(label="Totala skulder", x=years, y=total, color="#dc2626", width=2.0, marker_size=4),
            LineSeries(label="Långfristiga", x=years, y=long_t, color="#2563eb", width=1.5),
            LineSeries(label="Kortfristiga", x=years, y=short_t, color="#f59e0b", width=1.5),
            LineSeries(label="Likvida medel", x=years, y=cash, color="#22c55e", width=1.5, dash="dotted"),
        ]
        return series, None, "kr/kvm", True

    if chart_id == "energiHistoryChart":
        rows = compute_energi_history()
        years = [y for y, _ in rows]
        series = [
            LineSeries(label="Uppvärmning", x=years, y=[d["uppvarmning"] for _, d in rows],
                       color="#dc2626", width=1.5),
            LineSeries(label="El", x=years, y=[d["el"] for _, d in rows],
                       color="#3b82f6", width=1.5),
            LineSeries(label="Vatten", x=years, y=[d["vatten"] for _, d in rows],
                       color="#16a34a", width=1.5),
            LineSeries(label="Totalt", x=years, y=[d["total"] for _, d in rows],
                       color="#f59e0b", width=2.0, marker_size=4),
        ]
        bands = [
            HSBBand(0, 150, "#dcfce7", "Låg"),
            HSBBand(150, 250, "#fef3c7", "Normal"),
            HSBBand(250, 350, "#fee2e2", "Hög"),
        ]
        return series, bands, "kr/kvm", True

    if chart_id == "rantekanslighetHistoryChart":
        rows = compute_rantekanslighet_history()
        years = [y for y, _ in rows]
        vals = [v for _, v in rows]
        series = [LineSeries(label="Räntekänslighet", x=years, y=vals,
                             color="#3b82f6", width=2.0, marker_size=4.5)]
        bands = [
            HSBBand(0, 5, "#dcfce7", "Låg"),
            HSBBand(5, 9, "#fef3c7", "Normal"),
            HSBBand(9, 15, "#fee2e2", "Hög"),
        ]
        return series, bands, "%", False

    if chart_id == "arsavgiftHistoryChart":
        rows = compute_arsavgift_history()
        years = [y for y, _ in rows]
        vals = [v for _, v in rows]
        series = [LineSeries(label="Årsavgift", x=years, y=vals,
                             color="#3b82f6", width=2.0, marker_size=4.5)]
        bands = [
            HSBBand(0, 500, "#dcfce7", "Under vanligt"),
            HSBBand(500, 800, "#bbf7d0", "Vanligt"),
            HSBBand(800, 1000, "#fef3c7", "Högre"),
            HSBBand(1000, 1400, "#fee2e2", "Se upp"),
        ]
        return series, bands, "kr/kvm", False

    return [], None, "", False


@dataclass
class SparandeHistoryChartBlock(Block):
    """Real bar chart of sparande per year (matplotlib)."""
    caption: str
    width_pt: float = 425.0
    height_pt: float = 195.0

    def _caption_h(self, c: Canvas, max_width: float) -> float:
        if not self.caption:
            return 0.0
        n = _caption_line_count(c, self.caption, max_width=min(self.width_pt, max_width))
        return n * 10.5 + 6.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self.height_pt + self._caption_h(c, max_width)

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        box_w = min(self.width_pt, max_width)
        box_x = x + (max_width - box_w) / 2.0
        box_bottom = y_top - self.height_pt
        series = compute_sparande_per_kvm()
        img = render_sparande_history_chart_png(series, width_pt=box_w, height_pt=self.height_pt)
        c.drawImage(img, box_x, box_bottom, width=box_w, height=self.height_pt,
                    preserveAspectRatio=False, mask="auto")
        if self.caption:
            _draw_wrapped_caption(
                c, self.caption,
                center_x=box_x + box_w / 2.0,
                top_y=box_bottom - 10.0,
                max_width=box_w,
            )
        return y_top - self.height(c, max_width)


@dataclass
class SectionCardBlock(Block):
    """The big numbered detail-section card (page-3 style). Flowable version of
    draw_section_card_*. Renders header band (navy) + badge + body + formula pill +
    benchmark heading + 4 benchmark rows + 1-2 italic footnote lines."""
    content: SectionCardContent

    # Internal layout offsets (from card top y_top, in points downward)
    HEADER_BAND_H = 31.18
    BADGE_DIAMETER = 22.68
    BADGE_TOP_OFFSET = 4.25
    TITLE_BASELINE_OFFSET = 22.0  # from y_top
    BODY_FIRST_LINE_OFFSET = 53.0
    BODY_LINE_HEIGHT = 13.5
    PILL_GAP_AFTER_BODY = 12.0
    PILL_H = 19.84
    PILL_PAD_LEFT = 8.5
    PILL_TEXT_TOP_OFFSET = 13.0
    BENCHMARK_HEADING_GAP_AFTER_PILL = 14.0
    BENCHMARK_ROW_SPACING = 17.0
    BENCHMARK_CIRCLE_DIAMETER = 14.17
    BENCHMARK_LABEL_X_OFFSET = 45.4  # from card left
    BENCHMARK_VALUE_X_OFFSET = 141.7
    BENCHMARK_CIRCLE_X_OFFSET = 31.2  # from card left to circle center
    FOOTNOTE_GAP_AFTER_BENCHMARKS = 16.0
    FOOTNOTE_LINE_HEIGHT = 11.0
    BOTTOM_PAD = 10.0

    BODY_PAD_LEFT = 19.83
    BODY_PAD_RIGHT = 32.0

    def _body_lines(self, c: Canvas, max_width: float) -> int:
        return _count_rich_lines(c, self.content.body, "Helvetica", "Helvetica-Bold",
                                 9.5, max_width - self.BODY_PAD_LEFT - self.BODY_PAD_RIGHT)

    def height(self, c: Canvas, max_width: float) -> float:
        body_lines = self._body_lines(c, max_width)
        body_h = body_lines * self.BODY_LINE_HEIGHT
        n_benchmarks = len(self.content.benchmarks[:4])
        benchmark_h = n_benchmarks * self.BENCHMARK_ROW_SPACING
        pill_h = self.PILL_GAP_AFTER_BODY + self.PILL_H if self.content.formula else 0.0
        footnote_h = 0.0
        if self.content.footnote_intro:
            footnote_h += self.FOOTNOTE_LINE_HEIGHT
        if self.content.footnote_source:
            footnote_h += self.FOOTNOTE_LINE_HEIGHT
        if footnote_h:
            footnote_h += self.FOOTNOTE_GAP_AFTER_BENCHMARKS
        return (
            self.HEADER_BAND_H
            + 12.0  # gap below header band
            + body_h
            + pill_h
            + self.BENCHMARK_HEADING_GAP_AFTER_PILL + 14.0  # benchmark heading
            + benchmark_h
            + footnote_h
            + self.BOTTOM_PAD
        )

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        h = self.height(c, max_width)
        bottom = y_top - h

        # Outer rect (white fill, navy border)
        c.setFillColor(HexColor("#ffffff"))
        c.setStrokeColor(COL_BLUE_BORDER)
        c.setLineWidth(1.0)
        c.rect(x, bottom, max_width, h, stroke=1, fill=1)

        # Dark navy header band
        band_bottom = y_top - self.HEADER_BAND_H
        c.setFillColor(COL_NYCKELTAL_HEADER)
        c.rect(x, band_bottom, max_width, self.HEADER_BAND_H, stroke=0, fill=1)

        # Gold circle badge
        badge_r = self.BADGE_DIAMETER / 2.0
        badge_cx = x + 14.17 + badge_r  # offset from card left
        badge_cy = y_top - self.BADGE_TOP_OFFSET - badge_r
        c.setFillColor(COL_BADGE)
        c.circle(badge_cx, badge_cy, badge_r, stroke=0, fill=1)

        # Number
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(HexColor("#ffffff"))
        num_w = c.stringWidth(self.content.number, "Helvetica-Bold", 11)
        c.drawString(badge_cx - num_w / 2.0, badge_cy - 3.7, self.content.number)

        # Section title
        c.setFont("Helvetica-Bold", 13)
        c.setFillColor(HexColor("#ffffff"))
        c.drawString(badge_cx + badge_r + 8.0, y_top - self.TITLE_BASELINE_OFFSET, self.content.title)

        # Body paragraph
        body_max_w = max_width - self.BODY_PAD_LEFT - self.BODY_PAD_RIGHT
        body_first_y = y_top - self.BODY_FIRST_LINE_OFFSET
        draw_rich_paragraph(
            c, self.content.body, x + self.BODY_PAD_LEFT, body_first_y,
            regular_font="Helvetica", bold_font="Helvetica-Bold",
            size=9.5, color=COL_BODY,
            max_width=body_max_w, line_height=self.BODY_LINE_HEIGHT,
        )
        body_lines = self._body_lines(c, max_width)
        body_last_y = body_first_y - (body_lines - 1) * self.BODY_LINE_HEIGHT

        # Formula pill (if any)
        if self.content.formula:
            pill_y_top = body_last_y - self.PILL_GAP_AFTER_BODY
            pill_y_bottom = pill_y_top - self.PILL_H
            pill_left = x + self.BODY_PAD_LEFT
            pill_right = x + max_width - 20.0
            c.setFillColor(COL_QR_BG)
            c.setStrokeColor(COL_QR_BORDER)
            c.setLineWidth(0.5)
            c.rect(pill_left, pill_y_bottom, pill_right - pill_left, self.PILL_H, stroke=1, fill=1)
            c.setFont("Helvetica", 8.5)
            c.setFillColor(COL_LEAD)
            c.drawString(pill_left + self.PILL_PAD_LEFT, pill_y_top - self.PILL_TEXT_TOP_OFFSET,
                         self.content.formula)
            bh_y = pill_y_bottom - self.BENCHMARK_HEADING_GAP_AFTER_PILL
        else:
            bh_y = body_last_y - self.BENCHMARK_HEADING_GAP_AFTER_PILL
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(COL_TITLE)
        c.drawString(x + self.BODY_PAD_LEFT, bh_y, self.content.benchmark_heading)

        # 4 benchmark rows
        circle_r = self.BENCHMARK_CIRCLE_DIAMETER / 2.0
        for i, row in enumerate(self.content.benchmarks[:4]):
            row_cy = bh_y - 16.0 - i * self.BENCHMARK_ROW_SPACING
            c.setFillColor(BENCHMARK_COLORS[i])
            c.circle(x + self.BENCHMARK_CIRCLE_X_OFFSET, row_cy, circle_r, stroke=0, fill=1)
            baseline = row_cy - 3.3
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(COL_BODY)
            c.drawString(x + self.BENCHMARK_LABEL_X_OFFSET, baseline, row.label)
            c.setFont("Helvetica", 9)
            c.drawString(x + self.BENCHMARK_VALUE_X_OFFSET, baseline, row.value)

        # Footnotes
        n_benchmarks = len(self.content.benchmarks[:4])
        fn_y = bh_y - 16.0 - (n_benchmarks - 1) * self.BENCHMARK_ROW_SPACING - self.FOOTNOTE_GAP_AFTER_BENCHMARKS
        c.setFont("Helvetica-Oblique", 8.5)
        c.setFillColor(COL_LEAD)
        if self.content.footnote_intro:
            # Wrap footnote if long
            inner_w = max_width - self.BODY_PAD_LEFT - 16.0
            for line in wrap_text(c, self.content.footnote_intro, "Helvetica-Oblique", 8.5, inner_w):
                c.drawString(x + self.BODY_PAD_LEFT, fn_y, line)
                fn_y -= self.FOOTNOTE_LINE_HEIGHT
        if self.content.footnote_source:
            inner_w = max_width - self.BODY_PAD_LEFT - 16.0
            for line in wrap_text(c, self.content.footnote_source, "Helvetica-Oblique", 8.5, inner_w):
                c.drawString(x + self.BODY_PAD_LEFT, fn_y, line)
                fn_y -= self.FOOTNOTE_LINE_HEIGHT

        return bottom


@dataclass
class RatingBoxBlock(Block):
    """End-of-section 'Din bedömning av X' rating box: h4 + 3 colored option buttons."""
    label: str   # e.g. "Sparande"
    options: list[tuple[str, HexColor]] = None   # type: ignore[assignment]

    def __post_init__(self):
        if self.options is None:
            self.options = [
                ("Oroväckande",   HexColor("#ef4444")),
                ("Kan bli bättre", HexColor("#f59e0b")),
                ("Godkänt",        HexColor("#10b981")),
            ]

    HEADING_H = 20.0
    BUTTON_H = 26.0
    BUTTON_GAP = 8.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self.HEADING_H + 6.0 + self.BUTTON_H + 8.0

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        # Heading "Din bedömning av X:"
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(COL_TITLE)
        c.drawString(x, y_top - 14.0, f"Din bedömning av {self.label}:")

        # 3 buttons
        n = len(self.options)
        total_gap = (n - 1) * self.BUTTON_GAP
        button_w = (max_width - total_gap) / n
        button_y_bottom = y_top - self.HEADING_H - 6.0 - self.BUTTON_H
        for i, (label, color) in enumerate(self.options):
            bx = x + i * (button_w + self.BUTTON_GAP)
            c.setFillColor(HexColor("#ffffff"))
            c.setStrokeColor(color)
            c.setLineWidth(2.0)
            c.rect(bx, button_y_bottom, button_w, self.BUTTON_H, stroke=1, fill=1)
            c.setFont("Helvetica-Bold", 10)
            c.setFillColor(color)
            tw = c.stringWidth(label, "Helvetica-Bold", 10)
            c.drawString(bx + (button_w - tw) / 2.0, button_y_bottom + self.BUTTON_H / 2.0 - 3.5, label)
        return button_y_bottom


@dataclass
class QRCardBlock(Block):
    """Single QR reference card (same style as page 1/2)."""
    title: str
    url: str
    box_height: float = 68.0

    def height(self, c: Canvas, max_width: float) -> float:
        return self.box_height + 4.0

    def render(self, c: Canvas, x: float, y_top: float, max_width: float) -> float:
        bottom = y_top - self.box_height
        c.setFillColor(COL_QR_BG)
        c.setStrokeColor(COL_QR_BORDER)
        c.setLineWidth(0.5)
        c.rect(x, bottom, max_width, self.box_height, stroke=1, fill=1)

        # QR
        qr_img = make_qr_image(self.url)
        qr_pad = 6.0
        qr_side = self.box_height - 2 * qr_pad
        c.drawImage(qr_img, x + qr_pad + 2.5, bottom + qr_pad,
                    width=qr_side, height=qr_side,
                    preserveAspectRatio=True, mask="auto")

        # Title + URL + instruction
        text_x = x + qr_pad + qr_side + 12.0
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(COL_TITLE)
        c.drawString(text_x, y_top - 18.0, self.title)
        c.setFont("Helvetica", 7.5)
        c.setFillColor(COL_URL)
        c.drawString(text_x, y_top - 34.0, self.url)
        c.setFont("Helvetica-Oblique", 7.5)
        c.setFillColor(COL_HEADFOOT)
        c.drawString(text_x, y_top - 58.0,
                     "Skanna QR-koden eller besök adressen i din webbläsare")
        return bottom


# ---------------------------------------------------------------------------
# Block extraction — walk a <div class="guide-section"> and emit blocks.
# ---------------------------------------------------------------------------


CHART_ID_TO_BLOCK = {
    "sparandeHistoryChart": "SparandeHistoryChartBlock",
    # All other chart IDs → ChartPlaceholderBlock for now.
}


def _list_items_to_richtext(list_tag) -> list[RichText]:
    return [_parse_rich_paragraph(li) for li in list_tag.find_all("li", recursive=False)]


def extract_section_blocks(section_div, *, skip_section_card: bool = True) -> list[Block]:
    """Walk a guide-section and emit a sequence of Block objects.

    By default the leading section-card content (h2 + first paragraphs + formula +
    benchmark table + footnote) is skipped because it's rendered separately by
    `draw_section_card_*`. Set skip_section_card=False to emit a SectionCardBlock
    at the start (used for sections 2-7 where the flow engine owns the whole page).
    """
    blocks: list[Block] = []

    # Identify where the section-card content ends. The section card always includes
    # h2 + body p + formula p + h3 Riktvärden + riktvarde-table, plus an optional
    # source-ref footnote following the table. After that, all subsequent siblings
    # are "post-card" content and become flow blocks.
    cutoff_node = None
    riktvarde = section_div.find("table", class_="riktvarde-table")
    if riktvarde is not None:
        cutoff_node = riktvarde  # cutoff includes the table itself
        nxt = riktvarde.find_next_sibling()
        # If a source-ref follows immediately, that's the section-card footnote too.
        while nxt is not None and getattr(nxt, "name", None) is None:
            nxt = nxt.find_next_sibling()
        if nxt is not None and getattr(nxt, "name", None) == "p" and "source-ref" in (nxt.get("class") or []):
            cutoff_node = nxt

    if not skip_section_card:
        try:
            section_card = _parse_section_card(section_div)
            if section_card.number:  # only numbered nyckeltal sections have a card
                blocks.append(SectionCardBlock(content=section_card))
        except (AttributeError, IndexError):
            pass

    seen_cutoff = cutoff_node is None

    # Track the most recent chart id so a following source-ref becomes its caption.
    pending_chart: ChartPlaceholderBlock | SparandeHistoryChartBlock | None = None

    for child in section_div.children:
        if not hasattr(child, "name") or child.name is None:
            continue

        if not seen_cutoff:
            if child is cutoff_node:
                seen_cutoff = True
            continue

        name = child.name.lower()

        # Captions for charts — attach to pending chart
        if pending_chart is not None and name == "p" and "source-ref" in (child.get("class") or []):
            text = _clean(child.get_text())
            if isinstance(pending_chart, (ChartPlaceholderBlock, SparandeHistoryChartBlock,
                                          MultiLineChartBlock, WaterfallChartBlock,
                                          ComparisonChartBlock, SimpleBarChartBlock,
                                          PrognosChartBlock, TreemapChartBlock)):
                pending_chart.caption = text
            pending_chart = None
            continue
        pending_chart = None

        if name == "h2":
            continue  # already rendered as section card
        if name == "h3":
            blocks.append(HeadingBlock(text=_clean(child.get_text()), level=3))
            continue
        if name == "h4":
            text = _clean(child.get_text())
            m = re.match(r"Din bedömning av\s+(.+?)\s*:?\s*$", text)
            if m:
                # Look ahead for the flex div with rating buttons; rendered as a single block.
                blocks.append(RatingBoxBlock(label=m.group(1)))
            continue
        if name == "p":
            classes = child.get("class") or []
            text = _clean(child.get_text())
            if not text:
                continue
            if "source-ref" in classes:
                # Standalone source-ref (not following a chart). Render as italic line.
                blocks.append(ParagraphBlock(
                    runs=[(text, False)],
                    size=8.0,
                    line_height=11.0,
                    color=COL_LEAD,
                ))
                continue
            blocks.append(ParagraphBlock(runs=_parse_rich_paragraph(child)))
            continue
        if name == "ol":
            blocks.append(ListBlock(items=_list_items_to_richtext(child), ordered=True))
            continue
        if name == "ul":
            blocks.append(ListBlock(items=_list_items_to_richtext(child), ordered=False))
            continue
        if name == "div":
            classes = child.get("class") or []
            if "callout" in classes or "callout-blue" in classes:
                co = _parse_callout(child)
                title = co.title
                # If callout has no embedded title (no <strong>) and was preceded by an h3,
                # absorb that h3 as the callout's title (matches the "Sammanfattning: X" pattern).
                if not title and blocks and isinstance(blocks[-1], HeadingBlock):
                    title = blocks.pop().text
                blocks.append(CalloutBlock(title=title, paragraphs=co.paragraphs, variant="blue"))
                continue
            # Chart placeholders — <div id="*Chart">
            div_id = child.get("id") or ""
            if div_id.endswith("Chart"):
                if div_id == "sparandeHistoryChart":
                    pending_chart = SparandeHistoryChartBlock(caption="")
                elif div_id in _LINE_CHART_REGISTRY:
                    pending_chart = MultiLineChartBlock(chart_id=div_id)
                elif div_id in _WATERFALL_CHART_IDS:
                    pending_chart = WaterfallChartBlock(chart_id=div_id)
                elif div_id in _COMPARISON_CHART_IDS:
                    pending_chart = ComparisonChartBlock(chart_id=div_id)
                elif div_id in _SIMPLE_BAR_CHART_IDS:
                    pending_chart = SimpleBarChartBlock(chart_id=div_id)
                elif div_id in _PROGNOS_CHART_IDS:
                    pending_chart = PrognosChartBlock(chart_id=div_id)
                elif div_id in _TREEMAP_CHART_IDS:
                    pending_chart = TreemapChartBlock(chart_id=div_id)
                else:
                    pending_chart = ChartPlaceholderBlock(chart_id=div_id)
                blocks.append(pending_chart)
                continue
            # Ignore .norm-toggle, .inflation-toggle, .mobile-hint, .expand-btn-row, etc.
            continue
        if name == "table":
            classes = child.get("class") or []
            if "riktvarde-table" in classes:
                # Year-comparison data table — could be expanded into a real block.
                # For now, skip (already handled separately on page 4).
                continue

    return blocks


# ---------------------------------------------------------------------------
# Flow renderer — consume a block list, paginate.
# ---------------------------------------------------------------------------


class PageFlow:
    """Walks a sequence of Block objects, places them top-down on the canvas,
    inserts page breaks when a block won't fit."""

    def __init__(self, c: Canvas, *, page_number: int, draw_initial_header: bool = True):
        self.c = c
        self.page_number = page_number
        self.y = FLOW_TOP_Y
        if draw_initial_header:
            draw_header_footer(c, page_number=self.page_number)

    def break_page(self) -> None:
        self.c.showPage()
        self.page_number += 1
        self.y = FLOW_TOP_Y
        draw_header_footer(self.c, page_number=self.page_number)

    def place(self, block: Block, x: float = CONTENT_LEFT,
              max_width: float = CONTENT_RIGHT - CONTENT_LEFT,
              gap_after: float = DEFAULT_BLOCK_GAP) -> None:
        h = block.height(self.c, max_width)
        if self.y - h < FLOW_BOTTOM_Y:
            self.break_page()
        new_y = block.render(self.c, x, self.y, max_width)
        self.y = new_y - gap_after

    def render_blocks(self, blocks: list[Block],
                      x: float = CONTENT_LEFT,
                      max_width: float = CONTENT_RIGHT - CONTENT_LEFT) -> None:
        for b in blocks:
            self.place(b, x=x, max_width=max_width)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_pdf(html_path: Path, pdf_path: Path) -> None:
    page1, page2, page3, page4 = parse_html(html_path)
    c = Canvas(
        str(pdf_path),
        pagesize=A4,
    )
    c.setTitle("Förstå din förenings ekonomi")
    c.setAuthor("BRF Sjötungan medlemmar")
    render_page1(c, page1)
    c.showPage()
    render_page2(c, page2)
    c.showPage()
    render_page3(c, page3)
    c.showPage()
    render_page4(c, page4)
    c.showPage()

    # ---- Section 1 (Sparande) — remaining content (pages 5+) via flow engine ----
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    sparande_section = _find_section(soup, lambda t: t.startswith("1. Sparande"))
    flow: PageFlow | None = None
    if sparande_section is not None:
        blocks = extract_section_blocks(sparande_section, skip_section_card=True)
        # Find the waterfall chart — first block after page-4 content.
        start_idx = 0
        for i, b in enumerate(blocks):
            if isinstance(b, ChartPlaceholderBlock) and b.chart_id == "sparandeWaterfallChart":
                start_idx = i
                break
        flow = PageFlow(c, page_number=5)
        flow.render_blocks(blocks[start_idx:])

    # ---- Sections 2-7 — each starts on a fresh page with its own section card ----
    for section_prefix in ("2. ", "3. ", "4. ", "5. ", "6. ", "7. "):
        section = _find_section(soup, lambda t, p=section_prefix: t.startswith(p))
        if section is None:
            continue
        # Force a page break so the next section's card starts at the top.
        if flow is not None:
            flow.break_page()
        else:
            c.showPage()
            flow = PageFlow(c, page_number=9, draw_initial_header=True)
        blocks = extract_section_blocks(section, skip_section_card=False)
        flow.render_blocks(blocks)

    # ---- Trailing sections (stambyte focus, sources, final QR cards) ----
    # These don't have numbered section cards — just flow content.
    for h2_predicate in (
        lambda t: t.startswith("Hur förändras nyckeltalen"),
        lambda t: t == "Källor",
        lambda t: t == "Denna guide uppdateras",
    ):
        section = _find_section(soup, h2_predicate)
        if section is None:
            continue
        h2 = section.find("h2")
        section_title = _clean(h2.get_text()) if h2 else ""
        if flow is not None:
            flow.break_page()
        else:
            c.showPage()
            flow = PageFlow(c, page_number=1, draw_initial_header=True)
        # Section heading at top of page (h2 level)
        if section_title:
            flow.place(HeadingBlock(text=section_title, level=2, gap_above=4.0, gap_below=10.0))
        blocks = extract_section_blocks(section, skip_section_card=True)
        flow.render_blocks(blocks)

    if flow is not None:
        c.showPage()

    c.save()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--html", type=Path, default=DEFAULT_HTML)
    p.add_argument("--out", type=Path, default=DEFAULT_PDF)
    args = p.parse_args()

    if not args.html.exists():
        raise SystemExit(f"HTML file not found: {args.html}")
    build_pdf(args.html, args.out)
    print(f"Wrote {args.out}  ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
