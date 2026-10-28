"""論文スケルトンのMarkdownから、編集用Wordと閲覧用PDFを作る。分析は行わない。"""

import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from PIL import Image
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.plot_paper_central_result import configure

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/29_professor_progress_skeleton"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
for prefix, uri in [("w", W), ("r", R), ("wp", WP), ("a", A), ("pic", PIC)]:
    ET.register_namespace(prefix, uri)


def element(parent, tag, attrs=None, text=None):
    prefix, local = tag.split(":")
    uri = {"w": W, "r": R, "wp": WP, "a": A, "pic": PIC}[prefix]
    attrs = attrs or {}
    translated = {}
    for key, value in attrs.items():
        if ":" in key:
            pre, name = key.split(":")
            namespace = {"w": W, "r": R}[pre]
            translated[f"{{{namespace}}}{name}"] = str(value)
        else:
            translated[key] = str(value)
    child = ET.SubElement(parent, f"{{{uri}}}{local}", translated)
    child.text = text
    return child


def clean(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.replace("**", "").replace("`", "")


def blocks(source):
    lines = source.splitlines()
    result = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        if line == "<!-- pagebreak -->":
            result.append(("break", None))
        elif line.startswith("!["):
            label, path = re.match(r"!\[([^]]*)\]\(([^)]+)\)", line).groups()
            result.append(("image", (label, (REPORT / path).resolve())))
        elif line.startswith("|"):
            rows = [line]
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            cells = [
                [clean(s.strip()) for s in row.strip("|").split("|")]
                for row in rows
                if not re.fullmatch(r"[|:\-\s]+", row)
            ]
            result.append(("table", cells))
        elif line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            result.append((f"h{level}", clean(line[level:].strip())))
        else:
            result.append(("text", clean(line)))
    return result


def paragraph(parent, text, heading=0, size=21):
    p = element(parent, "w:p")
    prop = element(p, "w:pPr")
    element(prop, "w:spacing", {"w:after": 90, "w:line": 300, "w:lineRule": "auto"})
    if heading:
        element(prop, "w:keepNext")
        element(prop, "w:outlineLvl", {"w:val": min(heading - 1, 8)})
    run = element(p, "w:r")
    rpr = element(run, "w:rPr")
    element(rpr, "w:rFonts", {"w:ascii": "Calibri", "w:eastAsia": "游ゴシック"})
    element(rpr, "w:sz", {"w:val": 29 if heading == 1 else 24 if heading else size})
    if heading:
        element(rpr, "w:b")
        element(rpr, "w:color", {"w:val": "173F5F"})
    t = element(run, "w:t", text=text)
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return p


def docx(content, output):
    document = ET.Element(f"{{{W}}}document")
    body = element(document, "w:body")
    media = []
    for kind, value in content:
        if kind == "break":
            element(element(element(body, "w:p"), "w:r"), "w:br", {"w:type": "page"})
        elif kind.startswith("h"):
            paragraph(body, value, heading=int(kind[1:]))
        elif kind == "text":
            paragraph(body, value)
        elif kind == "table":
            table = element(body, "w:tbl")
            props = element(table, "w:tblPr")
            element(props, "w:tblW", {"w:w": 10100, "w:type": "dxa"})
            borders = element(props, "w:tblBorders")
            for side in ["top", "left", "bottom", "right", "insideH", "insideV"]:
                element(borders, f"w:{side}", {"w:val": "single", "w:sz": 4, "w:color": "CBD5DF"})
            cols = len(value[0])
            grid = element(table, "w:tblGrid")
            for _ in range(cols):
                element(grid, "w:gridCol", {"w:w": 10100 // cols})
            for index, row in enumerate(value):
                tr = element(table, "w:tr")
                trpr = element(tr, "w:trPr")
                element(trpr, "w:cantSplit")
                if index == 0:
                    element(trpr, "w:tblHeader")
                for text in row:
                    tc = element(tr, "w:tc")
                    tcpr = element(tc, "w:tcPr")
                    element(tcpr, "w:tcW", {"w:w": 10100 // cols, "w:type": "dxa"})
                    if index == 0:
                        element(tcpr, "w:shd", {"w:fill": "E8EFF5"})
                    paragraph(tc, text, size=18)
            paragraph(body, "", size=6)
        elif kind == "image":
            _, path = value
            media.append(path)
            index = len(media)
            width, height = Image.open(path).size
            cx = 6400800
            cy = int(cx * height / width)
            p = element(body, "w:p")
            drawing = element(element(p, "w:r"), "w:drawing")
            inline = element(drawing, "wp:inline")
            element(inline, "wp:extent", {"cx": cx, "cy": cy})
            element(inline, "wp:docPr", {"id": index, "name": path.stem})
            graphic = element(inline, "a:graphic")
            data = element(graphic, "a:graphicData", {"uri": PIC})
            pic = element(data, "pic:pic")
            nv = element(pic, "pic:nvPicPr")
            element(nv, "pic:cNvPr", {"id": index, "name": path.name})
            element(nv, "pic:cNvPicPr")
            fill = element(pic, "pic:blipFill")
            element(fill, "a:blip", {"r:embed": f"rIdImage{index}"})
            element(element(fill, "a:stretch"), "a:fillRect")
            shape = element(pic, "pic:spPr")
            transform = element(shape, "a:xfrm")
            element(transform, "a:off", {"x": 0, "y": 0})
            element(transform, "a:ext", {"cx": cx, "cy": cy})
            geom = element(shape, "a:prstGeom", {"prst": "rect"})
            element(geom, "a:avLst")
    section = element(body, "w:sectPr")
    element(section, "w:footerReference", {"w:type": "default", "r:id": "rIdFooter"})
    element(section, "w:pgSz", {"w:w": 11906, "w:h": 16838})
    element(section, "w:pgMar", {"w:top": 850, "w:bottom": 850, "w:left": 900, "w:right": 900})
    relns = "http://schemas.openxmlformats.org/package/2006/relationships"
    relationships = ET.Element("Relationships", xmlns=relns)
    ET.SubElement(
        relationships,
        "Relationship",
        {
            "Id": "rIdFooter",
            "Type": R + "/footer",
            "Target": "footer1.xml",
        },
    )
    for i, path in enumerate(media, 1):
        ET.SubElement(
            relationships,
            "Relationship",
            {
                "Id": f"rIdImage{i}",
                "Type": R + "/image",
                "Target": f"media/image{i}.png",
            },
        )
    types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/footer1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        "</Types>"
    )
    footer = ET.Element(f"{{{W}}}ftr")
    p = element(footer, "w:p")
    element(element(p, "w:pPr"), "w:jc", {"w:val": "center"})
    field = element(p, "w:fldSimple", {"w:instr": "PAGE"})
    element(element(field, "w:r"), "w:t", text="1")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr(
            "_rels/.rels",
            (
                f'<Relationships xmlns="{relns}"><Relationship Id="rId1" '
                f'Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>'
            ),
        )
        z.writestr(
            "word/document.xml", ET.tostring(document, encoding="utf-8", xml_declaration=True)
        )
        z.writestr("word/_rels/document.xml.rels", ET.tostring(relationships, encoding="utf-8"))
        z.writestr("word/footer1.xml", ET.tostring(footer, encoding="utf-8"))
        for i, path in enumerate(media, 1):
            z.write(path, f"word/media/image{i}.png")


def wrap(text, max_width):
    lines, line, width = [], "", 0
    for char in text:
        advance = 1 if unicodedata.east_asian_width(char) in "WF" else 0.56
        if width + advance > max_width and char not in "。、，．）］」』！？：；%":
            lines.append(line)
            line, width = "", 0
        line += char
        width += advance
    if line:
        lines.append(line)
    if len(lines) > 1 and len(lines[-1]) < 4:
        # 語尾だけが1〜3文字の独立した行にならないようにする。
        moved = lines[-2][-4:]
        lines[-2] = lines[-2][:-4]
        lines[-1] = moved + lines[-1]
    return lines or [""]


class PDF:
    def __init__(self, output):
        self.pdf = PdfPages(output)
        self.fig = None
        self.pages = 0
        self.y = 0
        self.new_page()

    def new_page(self):
        if self.fig:
            self.pdf.savefig(self.fig)
            plt.close(self.fig)
        self.pages += 1
        self.fig = plt.figure(figsize=(8.27, 11.69))
        self.fig.text(
            0.075, 0.97, "研究進捗／論文スケルトン　2026-10-28", fontsize=8, color="#667788"
        )
        self.fig.text(0.92, 0.025, str(self.pages), fontsize=8, ha="right", color="#667788")
        self.y = 0.935

    def ensure(self, height):
        if self.y - height < 0.065:
            self.new_page()

    def text(self, text, size=10, heading=False):
        lines = wrap(text, 50 * 10 / size)
        line_height = size * 1.55 / 841.68
        self.ensure(line_height * min(len(lines), 3) + (0.014 if heading else 0))
        for line in lines:
            self.ensure(line_height)
            self.fig.text(
                0.075,
                self.y,
                line,
                fontsize=size,
                va="top",
                weight="bold" if heading else "normal",
                color="#173f5f" if heading else "#18212b",
            )
            self.y -= line_height
        self.y -= 0.009 if heading else 0.005

    def table(self, rows):
        columns = len(rows[0])
        width = 0.85 / columns
        for index, row in enumerate(rows):
            lines = [wrap(text, 50 / columns * 10 / 8.5 - 1) for text in row]
            height = max(map(len, lines)) * 8.5 * 1.5 / 841.68 + 0.014
            self.ensure(height)
            for j, cell in enumerate(lines):
                self.fig.add_artist(
                    plt.Rectangle(
                        (0.075 + j * width, self.y - height),
                        width,
                        height,
                        transform=self.fig.transFigure,
                        facecolor="#e8eff5" if index == 0 else "white",
                        edgecolor="#cbd5df",
                        linewidth=0.5,
                    )
                )
                self.fig.text(
                    0.08 + j * width,
                    self.y - 0.006,
                    "\n".join(cell),
                    fontsize=8.5,
                    va="top",
                    linespacing=1.5,
                )
            self.y -= height
        self.y -= 0.012

    def image(self, path):
        image = plt.imread(path)
        height = 0.85 * 8.27 / 11.69 * image.shape[0] / image.shape[1]
        self.ensure(height + 0.015)
        ax = self.fig.add_axes([0.075, self.y - height, 0.85, height])
        ax.imshow(image)
        ax.axis("off")
        self.y -= height + 0.015

    def finish(self):
        self.pdf.savefig(self.fig)
        plt.close(self.fig)
        self.pdf.close()


def main():
    configure()
    source = REPORT / "paper_skeleton.md"
    content = blocks(source.read_text())
    docx(content, REPORT / "paper_skeleton.docx")
    pdf = PDF(REPORT / "paper_skeleton.pdf")
    for kind, value in content:
        if kind == "break":
            pdf.new_page()
        elif kind.startswith("h"):
            pdf.text(value, size=14 if kind == "h1" else 11.5, heading=True)
        elif kind == "text":
            pdf.text(value)
        elif kind == "table":
            pdf.table(value)
        elif kind == "image":
            pdf.image(value[1])
    pdf.finish()
    with zipfile.ZipFile(REPORT / "paper_skeleton.docx") as z:
        assert z.testzip() is None
        for name in z.namelist():
            if name.endswith(("xml", "rels")):
                ET.fromstring(z.read(name))
        assert len([n for n in z.namelist() if n.startswith("word/media/")]) == 2
    reader = PdfReader(REPORT / "paper_skeleton.pdf")
    extracted = "\n".join(page.extract_text() for page in reader.pages)
    for token in ["16,848", "15,646", "92.87", "1,861", "940", "17,520"]:
        assert token in extracted, token
    audit = {
        "pdf_pages": len(reader.pages),
        "pdf_text_extraction_checked": True,
        "docx_zip_and_all_xml_valid": True,
        "embedded_figures": 2,
        "existing_figures_reused": True,
        "new_lp_calls": 0,
        "new_data_acquisitions": 0,
        "source": str(source.relative_to(ROOT)),
        "chapters": [v for k, v in content if k == "h1" and re.match(r"[1-8]．", v)],
    }
    assert len(audit["chapters"]) == 8
    (REPORT / "qa").mkdir(exist_ok=True)
    (REPORT / "qa/render_validation.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
