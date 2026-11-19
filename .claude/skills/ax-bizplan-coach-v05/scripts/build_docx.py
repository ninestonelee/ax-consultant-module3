#!/usr/bin/env python3
"""Render a Korean coaching plan, optionally filling verified DOCX form slots."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import unicodedata

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.image import Image
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
from docx.text.paragraph import Paragraph

FONT = "Noto Sans KR"
SLOT = re.compile(r"\{\{[A-Za-z][A-Za-z0-9_-]*\}\}")
IMAGE = re.compile(r"!\[([^\]]*)\]\((.+)\)")
HEAD = re.compile(r"^(#{1,6})\s+(.+)$")
RULE = re.compile(r"^:?-{2,}:?$")


def heading(line):
    for marker, level in (("■", 1), ("□", 2)):
        if line.startswith(marker):
            return level, line[1:].strip()
    match = HEAD.fullmatch(line)
    return (len(match[1]), match[2].strip()) if match else None


def field(paragraph, instruction):
    node = OxmlElement("w:fldSimple")
    node.set(qn("w:instr"), instruction)
    paragraph._p.append(node)


def typeface(run, size, *, bold=False, color="24343D"):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT)


def inline(paragraph, text, size=10.5, bold=False, color="24343D"):
    for bit in re.split(r"(\*\*.+?\*\*|`[^`]+`)", text):
        if not bit:
            continue
        emphasis = bit.startswith("**") and bit.endswith("**")
        code = bit.startswith("`") and bit.endswith("`")
        value = bit[2:-2] if emphasis else bit[1:-1] if code else bit
        typeface(paragraph.add_run(value), size, bold=bold or emphasis, color=color)


def add_paragraph(doc, text, level=None, style=None):
    paragraph = doc.add_paragraph(style=style or ("Heading " + str(level) if level else "Normal"))
    fmt = paragraph.paragraph_format
    fmt.line_spacing = 1.16
    fmt.space_after = Pt(4)
    if level:
        fmt.space_before = Pt(11)
        fmt.keep_with_next = True
    inline(paragraph, text, 15 if level == 1 else 12 if level else 10.5,
           bold=bool(level), color="17394B" if level else "24343D")
    return paragraph


def split_cells(line):
    return [v.strip().replace(r"\|", "|") for v in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def add_table(doc, lines, available_inches):
    rows = [split_cells(line) for line in lines]
    rows = [r for r in rows if not all(RULE.fullmatch(c) for c in r)]
    if not rows:
        raise ValueError("표에 표제와 본문이 필요합니다.")
    n = len(rows[0])
    if any(len(r) != n for r in rows):
        raise ValueError(f"표의 열 수가 일치하지 않습니다: {rows[0]}")
    # Widths use typical content and header labels, bounded to leave usable narrow columns.
    weights = []
    for col in zip(*rows):
        sizes = sorted(display_width(v) for v in col)
        typical = sizes[(len(sizes) - 1) * 3 // 4]
        weights.append(min(34, max(6, display_width(col[0]), typical)) ** .72)
    widths = [Inches(available_inches * w / sum(weights)) for w in weights]
    table = doc.add_table(rows=len(rows), cols=n)
    previous = table._tbl.getprevious()
    if previous is not None and previous.tag == qn("w:tbl"):
        # Word-compatible renderers may join adjacent tables and repeat the wrong header.
        separator = doc.add_paragraph()
        separator.paragraph_format.space_before = Pt(0)
        separator.paragraph_format.space_after = Pt(0)
        separator.paragraph_format.line_spacing = Pt(2)
        table._tbl.addprevious(separator._p)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for column, w in zip(table.columns, widths):
        column.width = w
    for i, (row, values) in enumerate(zip(table.rows, rows)):
        props = row._tr.get_or_add_trPr()
        props.append(OxmlElement("w:cantSplit"))
        if i == 0:
            props.append(OxmlElement("w:tblHeader"))
        for j, value in enumerate(values):
            cell = row.cells[j]
            cell.width = widths[j]
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.line_spacing = 1.10
            p.paragraph_format.keep_with_next = i == 0
            value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
            inline(p, value, 9, bold=i == 0, color="FFFFFF" if i == 0 else "24343D")
            if i == 0:
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "285066")
                cell._tc.get_or_add_tcPr().append(shade)
    return table


def render(doc, lines, base, width, max_height):
    i = 0
    in_code = False
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code and line:
            add_paragraph(doc, lines[i-1])
            continue
        if not line or re.fullmatch(r"-{3,}", line):
            continue
        if line.startswith("|"):
            table_lines = [line]
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            add_table(doc, table_lines, width)
            continue
        fig = IMAGE.fullmatch(line)
        if fig:
            caption, rel = fig.groups()
            img_path = (base / rel.strip("<>")).resolve()
            if not img_path.is_file():
                raise ValueError(f"로컬 이미지가 없습니다: {img_path}")
            img = Image.from_file(str(img_path))
            scale = min(1.0, width / img.width.inches, max_height / img.height.inches)
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_with_next = bool(caption)
            shape = p.add_run().add_picture(str(img_path), width=Inches(img.width.inches * scale))
            shape._inline.docPr.set("descr", caption)
            if caption:
                p = doc.add_paragraph(style="Caption")
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                inline(p, caption, 9, color="475E6A")
            continue
        h = heading(line)
        if h:
            add_paragraph(doc, h[1], level=h[0])
        elif re.match(r"^[-·•]\s+", line):
            p = add_paragraph(doc, "• " + line[2:])
            p.paragraph_format.left_indent = Mm(4)
        else:
            add_paragraph(doc, line)


def configure(doc, title, company, mode):
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Mm(297), Mm(210)
    sec.left_margin = sec.right_margin = Mm(17)
    sec.top_margin, sec.bottom_margin = Mm(17), Mm(17)
    style = doc.styles["Title"]
    pr = style.element.get_or_add_pPr()
    border = pr.find(qn("w:pBdr"))
    if border is not None:
        pr.remove(border)
    title_p = doc.add_paragraph(style="Title")
    typeface(title_p.add_run(title), 20, bold=True, color="171C21")
    items = [company, "제출 준비 초안 · 공고 원본 양식 미적용" if mode == "submission" else "교육·코칭용 계획서"]
    p = doc.add_paragraph()
    inline(p, "   |   ".join(x for x in items if x), 9, color="475E6A")


def section_range(lines, key):
    if key == "$body":
        return 0, len(lines)
    headings = [(i, h) for i, l in enumerate(lines) if (h := heading(l.strip()))]
    found = [(i, h) for i, h in headings if h[1] == key]
    if len(found) != 1:
        raise ValueError(f"본문 절 이름이 없거나 중복됩니다: {key}")
    start, h = found[0]
    end = next((j for j, g in headings if j > start and g[0] <= h[0]), len(lines))
    return start, end


def fill_template(doc, mapping, template, lines, base, width, height):
    """Fill explicit dedicated paragraph slots in document body and table cells."""
    if mapping.get("template_sha256") != hashlib.sha256(template.read_bytes()).hexdigest():
        raise ValueError("양식 SHA-256이 매핑 기준과 다릅니다.")
    fields = mapping.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields에 자리표시자와 본문 절을 지정해 주세요.")
    body = doc._element.body
    slots = {}
    for elem in body.xpath(".//w:p"):
        value = Paragraph(elem, doc).text.strip()
        for token in SLOT.findall(value):
            if value != token or token in slots:
                raise ValueError(f"자리표시자는 독립된 문단 하나에만 있어야 합니다: {token}")
            slots[token] = elem
    if set(slots) != set(fields):
        raise ValueError(f"양식과 매핑의 자리표시자가 다릅니다: {sorted(set(slots)^set(fields))}")
    ranges = {}
    used = set()
    for token, source in fields.items():
        if not isinstance(source, str):
            raise ValueError(f"매핑의 절 제목을 문자열로 지정하세요: {token}")
        a, b = section_range(lines, source)
        indices = set(range(a, b))
        if used & indices:
            raise ValueError(f"같은 원문이 여러 양식 항목에 중복됩니다: {token}")
        used |= indices
        ranges[token] = (a, b)
    missing = [l for i,l in enumerate(lines) if i not in used and l.strip() and l.strip()!="---"]
    if missing:
        raise ValueError(f"매핑되지 않은 본문이 있습니다: {missing[0][:80]}")
    for token, anchor in slots.items():
        parent = anchor.getparent()
        available = width
        tds = anchor.xpath("ancestor::w:tc[1]/w:tcPr/w:tcW")
        if tds and tds[0].get(qn("w:type")) == "dxa":
            available = min(width, max(.1, int(tds[0].get(qn("w:w"))) / 1440 - .17))
        before = set(body)
        a,b = ranges[token]
        render(doc, lines[a:b], base, available, height)
        for added in [e for e in body if e not in before]:
            anchor.addprevious(added)
        parent.remove(anchor)
        if parent.tag == qn("w:tc") and parent[-1].tag != qn("w:p"):
            parent.append(OxmlElement("w:p"))


def export(src, out, *, title, company, version, date, mode, template=None, field_map=None):
    if src.resolve() == out.resolve() or (template and template.resolve() == out.resolve()):
        raise ValueError("입력과 출력 경로를 다르게 지정해 주세요.")
    if bool(template) != bool(field_map):
        raise ValueError("--template과 --field-map은 함께 지정해야 합니다.")
    lines = src.read_text(encoding="utf-8-sig").splitlines()
    doc = Document(str(template)) if template else Document()
    if not template:
        configure(doc, title, company, mode)
        if lines and heading(lines[0]) and heading(lines[0])[1] == title:
            lines = lines[1:]
    sec = doc.sections[0]
    width = (sec.page_width-sec.left_margin-sec.right_margin)/914400
    height = (sec.page_height-sec.top_margin-sec.bottom_margin)/914400 - .7
    if template:
        fill_template(doc, json.loads(field_map.read_text(encoding="utf-8-sig")), template,
                      lines, src.parent, width, height)
    else:
        render(doc, lines, src.parent, width, height)
        p = sec.footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        inline(p, f"{date}   ·   {version}   |   ", 8, color="475E6A")
        field(p, " PAGE ")
    doc.core_properties.title = title
    doc.core_properties.version = version
    doc.core_properties.subject = f"{company} · {date}"
    out.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=out.parent, suffix=".docx", delete=False) as tmp:
        name = Path(tmp.name)
    try:
        doc.save(str(name))
        os.replace(name, out)
    finally:
        name.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--title", default="사업계획서")
    parser.add_argument("--company", default="")
    parser.add_argument("--version", default="v3")
    parser.add_argument("--date", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--mode", choices=["education","submission"], default="education")
    parser.add_argument("--template", type=Path)
    parser.add_argument("--field-map", type=Path)
    args = parser.parse_args()
    try:
        export(**vars(args))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Word 생성 실패: {exc}\n")
    print(args.out.resolve())


if __name__ == "__main__":
    main()
