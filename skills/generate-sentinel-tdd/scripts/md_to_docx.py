#!/usr/bin/env python
"""
md_to_docx.py  --  Convert a Claude-generated Markdown (.md) file into a
Microsoft Word (.docx) document.

Pure Python: depends only on `python-docx` (MIT). No external apps, no network.

Supported Markdown:
    - ATX headings  (# .. ######)
    - Paragraphs with inline **bold**, *italic*, `code`, [text](url)
    - Bullet lists (-, *, +) and numbered lists (1.) with nesting by indent
    - Fenced code blocks (``` ... ```)
    - Block quotes (>)
    - Tables (GitHub pipe syntax)
    - Horizontal rules (---)

Diagrams (optional):
    .drawio files are rendered to PNG via the diagrams.net export server and
    auto-placed right after a matching heading. NOTE: this uploads the diagram
    XML to convert.diagrams.net (a third-party server). Requires `requests`.

Usage:
    python md_to_docx.py INPUT.md [-o OUTPUT.docx] [--title "Doc Title"]
                          [--diagram "FILE.drawio::Heading substring" ...]
                          [--diagram-scale 2]

If -o is omitted, the .docx is written next to the source file.

Examples:
    python tools/md_to_docx.py "specs/Design.md"
    python tools/md_to_docx.py report.md -o C:/out/Report.docx --title "My Report"
    python tools/md_to_docx.py specs/TDD.md \
        --diagram "specs/GTI_Alerts_Architecture.drawio::Overall System Architecture" \
        --diagram "specs/GTI_Alerts_DC_Architecture.drawio::Data Connector Architecture"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import io

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
except ImportError:
    sys.exit(
        "python-docx is not installed.\n"
        "Install it with:  python -m pip install python-docx"
    )


# ---------------------------------------------------------------------------
# House formatting  (Arial, black headings sized per level, gray-header tables)
# Matches the team's TDD reference document.
# ---------------------------------------------------------------------------

BASE_FONT = "Arial"

# level -> (point size, hex color, bold).  level 0 == Title.
HEADING_SCHEME = {
    0: (26, "000000", True),
    1: (20, "000000", True),
    2: (16, "000000", True),
    3: (14, "434343", True),
    4: (12, "666666", True),
    5: (11, "666666", True),
    6: (11, "666666", True),
}

HEADER_FILL = "BFBFBF"        # table header row shading
BORDER_COLOR = "000000"
BORDER_SIZE = "4"             # eighths of a point -> 0.5pt single line


def _hex(color: str) -> RGBColor:
    return RGBColor.from_string(color)


def apply_house_styles(doc) -> None:
    """Force Arial everywhere and black/sized headings (no blue)."""
    # Document default font -> Arial (so tables / unstyled runs inherit it).
    styles_el = doc.styles.element
    dd = styles_el.find(qn("w:docDefaults"))
    if dd is not None:
        rpr = dd.find(qn("w:rPrDefault"))
        if rpr is not None:
            rPr = rpr.find(qn("w:rPr"))
            if rPr is None:
                rPr = OxmlElement("w:rPr")
                rpr.append(rPr)
            rFonts = rPr.find(qn("w:rFonts"))
            if rFonts is None:
                rFonts = OxmlElement("w:rFonts")
                rPr.append(rFonts)
            for attr in ("w:ascii", "w:hAnsi", "w:cs"):
                rFonts.set(qn(attr), BASE_FONT)

    # Normal style
    normal = doc.styles["Normal"]
    normal.font.name = BASE_FONT
    normal.font.size = Pt(11)
    normal.font.color.rgb = _hex("000000")

    # Heading + Title styles
    for level, (size, color, bold) in HEADING_SCHEME.items():
        name = "Title" if level == 0 else f"Heading {level}"
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        st.font.name = BASE_FONT
        st.font.size = Pt(size)
        st.font.bold = bold
        st.font.color.rgb = _hex(color)


def _set_cell_shading(cell, fill_hex: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    tcPr.append(shd)


def _set_table_borders(table) -> None:
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), BORDER_SIZE)
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), BORDER_COLOR)
        borders.append(el)
    tblPr.append(borders)


def add_table_of_contents(doc) -> None:
    """Insert a real Word TOC field (updates on open / right-click > Update)."""
    doc.add_heading("Table of Contents", level=1)
    p = doc.add_paragraph()
    run = p.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    fld_text = OxmlElement("w:t")
    fld_text.text = "Right-click and choose “Update Field” to build the table of contents."
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    for el in (fld_begin, instr, fld_sep, fld_text, fld_end):
        run._r.append(el)


# ---------------------------------------------------------------------------
# diagrams.net (.drawio) rendering  -- uploads XML to a third-party server
# ---------------------------------------------------------------------------

EXPORT_URL = "https://convert.diagrams.net/node/export"


def render_drawio_to_png(drawio_path: Path, scale: int = 2) -> bytes:
    """Render a .drawio file to PNG bytes via the diagrams.net export server.

    Sends the diagram XML to convert.diagrams.net (third-party). Raises on
    failure so the caller can decide whether to continue without the image.
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError(
            "`requests` is required for diagram rendering. "
            "Install it with:  python -m pip install requests"
        )

    raw = drawio_path.read_text(encoding="utf-8").strip()
    # The export server wants a full <mxfile>; wrap a bare <mxGraphModel>.
    if raw.startswith("<mxGraphModel"):
        xml = f'<mxfile><diagram id="d1" name="Page-1">{raw}</diagram></mxfile>'
    else:
        xml = raw

    resp = requests.post(
        EXPORT_URL,
        data={"format": "png", "xml": xml, "bg": "#ffffff", "scale": str(scale)},
        headers={
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://app.diagrams.net",
            "Referer": "https://app.diagrams.net/",
        },
        timeout=60,
    )
    resp.raise_for_status()
    if not resp.content[:4] == b"\x89PNG":
        raise RuntimeError(
            f"Export server did not return a PNG (got {len(resp.content)} bytes, "
            f"content-type={resp.headers.get('content-type')!r})."
        )
    return resp.content

# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------

# Order matters: code first (so ** inside `code` is left literal), then links,
# then bold, then italic.
_INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<link>\[[^\]]+\]\([^)]+\))"
    r"|(?P<placeholder><[^>\n]{1,60}>)"
    r"|(?P<bold>\*\*[^*]+\*\*|__[^_]+__)"
    r"|(?P<italic>\*[^*]+\*|_[^_]+_)"
)


def add_inline(paragraph, text: str) -> None:
    """Add `text` to `paragraph`, rendering inline markdown as runs."""
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        kind = m.lastgroup
        token = m.group()
        if kind == "code":
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
            run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        elif kind == "link":
            lm = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token)
            label, url = lm.group(1), lm.group(2)
            run = paragraph.add_run(label)
            run.font.underline = True
            run.font.color.rgb = RGBColor(0x05, 0x63, 0xC1)
            # store the url so it isn't lost (visual hyperlink styling only)
            run.font.name = run.font.name
            paragraph.add_run(f" ({url})").font.size = Pt(8)
        elif kind == "placeholder":
            # Manual-fill markers like <API Response>, <TBD>, <Access Token>.
            run = paragraph.add_run(token)
            run.bold = True
            run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
        elif kind == "bold":
            paragraph.add_run(token.strip("*_")).bold = True
        elif kind == "italic":
            paragraph.add_run(token.strip("*_")).italic = True
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------

def _flush_table(doc, rows):
    """Render collected pipe-table rows. rows[1] is the --- separator."""
    if len(rows) < 2:
        return
    header = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    body = rows[2:]
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    _set_table_borders(table)
    for i, cell_text in enumerate(header):
        cell = table.rows[0].cells[i]
        _set_cell_shading(cell, HEADER_FILL)
        cell.paragraphs[0].text = ""
        add_inline(cell.paragraphs[0], cell_text)
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for line in body:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        row = table.add_row().cells
        for i in range(min(len(cells), len(header))):
            row[i].paragraphs[0].text = ""
            add_inline(row[i].paragraphs[0], cells[i])
    doc.add_paragraph()


def _place_diagrams(doc, diagrams):
    """Insert each rendered diagram right after the first heading whose text
    contains the given (case-insensitive) substring.

    `diagrams` is a list of (heading_substring, png_bytes, caption).
    Returns the list of substrings that could NOT be matched.
    """
    unmatched = []
    for heading_sub, png, caption in diagrams:
        target = None
        for p in doc.paragraphs:
            if p.style.name.startswith("Heading") and \
                    heading_sub.lower() in p.text.lower():
                target = p
                break
        if target is None:
            unmatched.append(heading_sub)
            continue
        # Append picture at end (creates a trailing paragraph), then move it.
        doc.add_picture(io.BytesIO(png), width=Inches(6.0))
        pic_par = doc.paragraphs[-1]
        pic_par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap_par = doc.add_paragraph()
        cap_par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cr = cap_par.add_run(caption or target.text)
        cr.italic = True
        cr.font.size = Pt(9)
        # Move both the picture and its caption to just after the heading.
        target._p.addnext(cap_par._p)
        target._p.addnext(pic_par._p)
    return unmatched


def convert(md_text: str, title: str | None = None, diagrams=None,
            logo: str | None = None, toc: bool = False) -> Document:
    doc = Document()
    apply_house_styles(doc)

    if logo:
        lp = doc.add_paragraph()
        lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        lp.add_run().add_picture(logo, width=Inches(2.5))

    if title:
        tp = doc.add_heading(title, level=0)
        tp.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if toc:
        add_table_of_contents(doc)

    lines = md_text.splitlines()
    i = 0
    n = len(lines)
    table_buf: list[str] = []

    def flush_table():
        nonlocal table_buf
        if table_buf:
            _flush_table(doc, table_buf)
            table_buf = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Fenced code block
        if stripped.startswith("```"):
            flush_table()
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            p = doc.add_paragraph()
            run = p.add_run("\n".join(code_lines))
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            try:
                p.style = doc.styles["No Spacing"]
            except KeyError:
                pass
            continue

        # Table rows
        if "|" in stripped and stripped.startswith("|"):
            table_buf.append(line)
            i += 1
            continue
        else:
            flush_table()

        # Blank line
        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            doc.add_paragraph().add_run("─" * 30)
            i += 1
            continue

        # Heading
        h = re.match(r"(#{1,6})\s+(.*)", stripped)
        if h:
            level = len(h.group(1))
            doc.add_heading(h.group(2).strip(), level=level)
            i += 1
            continue

        # Block quote
        if stripped.startswith(">"):
            p = doc.add_paragraph(style="Intense Quote"
                                  if "Intense Quote" in [s.name for s in doc.styles]
                                  else None)
            add_inline(p, stripped.lstrip(">").strip())
            i += 1
            continue

        # Lists (bullet / numbered), nesting by leading spaces
        indent = len(line) - len(line.lstrip(" "))
        bullet = re.match(r"[-*+]\s+(.*)", stripped)
        number = re.match(r"\d+[.)]\s+(.*)", stripped)
        if bullet or number:
            level = min(indent // 2, 2)
            style = "List Bullet" if bullet else "List Number"
            if level:
                style = f"{style} {level + 1}"
            try:
                p = doc.add_paragraph(style=style)
            except KeyError:
                p = doc.add_paragraph(style="List Bullet" if bullet else "List Number")
            add_inline(p, (bullet or number).group(1))
            i += 1
            continue

        # Plain paragraph (merge consecutive non-blank lines)
        para_lines = [stripped]
        i += 1
        while i < n and lines[i].strip() and not re.match(
            r"(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|```|\|)", lines[i].strip()
        ):
            para_lines.append(lines[i].strip())
            i += 1
        p = doc.add_paragraph()
        add_inline(p, " ".join(para_lines))

    flush_table()

    if diagrams:
        unmatched = _place_diagrams(doc, diagrams)
        for sub in unmatched:
            print(f"  warning: no heading matched '{sub}' -- diagram skipped",
                  file=sys.stderr)
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a Markdown file to a Word (.docx) document."
    )
    ap.add_argument("input", help="Path to the source .md file")
    ap.add_argument("-o", "--output", help="Destination .docx path "
                                           "(default: same name as input)")
    ap.add_argument("--title", help="Optional document title (Heading 0)")
    ap.add_argument(
        "--diagram", action="append", default=[], metavar="FILE.drawio::Heading",
        help="Render a .drawio file and place it after the heading containing "
             "the given substring. Repeatable. Paths are relative to the .md.",
    )
    ap.add_argument("--diagram-scale", type=int, default=2,
                    help="Render scale for diagrams (default 2 = crisp).")
    ap.add_argument("--logo", help="Path to a logo image placed centered at the top.")
    ap.add_argument("--toc", action="store_true",
                    help="Insert a Word Table of Contents field after the title.")
    args = ap.parse_args(argv)

    src = Path(args.input)
    if not src.is_file():
        sys.exit(f"Source file not found: {src}")

    out = Path(args.output) if args.output else src.with_suffix(".docx")
    out.parent.mkdir(parents=True, exist_ok=True)

    diagrams = []
    for spec in args.diagram:
        if "::" not in spec:
            sys.exit(f"--diagram needs FILE.drawio::Heading, got: {spec!r}")
        file_part, heading = spec.split("::", 1)
        dpath = Path(file_part)
        if not dpath.is_absolute():
            dpath = (src.parent / file_part).resolve()
        if not dpath.is_file():
            sys.exit(f"Diagram file not found: {dpath}")
        print(f"Rendering diagram: {dpath.name} -> heading '{heading}'")
        png = render_drawio_to_png(dpath, scale=args.diagram_scale)
        diagrams.append((heading, png, dpath.stem.replace("_", " ")))

    if args.logo and not Path(args.logo).is_file():
        sys.exit(f"Logo file not found: {args.logo}")

    md_text = src.read_text(encoding="utf-8")
    doc = convert(md_text, title=args.title, diagrams=diagrams,
                  logo=args.logo, toc=args.toc)
    doc.save(str(out))
    print(f"Created: {out}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
