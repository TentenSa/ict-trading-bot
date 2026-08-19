"""Minimal multi-sheet .xlsx writer — stdlib only (no openpyxl/xlsxwriter here).

An xlsx file is a zip of XML parts. This writes just enough of the spec for a
usable workbook: styled headers, column widths, frozen panes, number formats,
dropdown validation, conditional formatting and formulas.

Cell values:
    ("s", "text")            inline string
    ("n", 12.5)              number
    ("f", "=A1+B1")          formula
    ("d", 45890)             date serial (see date_serial)
    None                     empty
Each cell may carry a style index: (type, value, style).
"""
import datetime
import zipfile

# style indices produced by _styles_xml()
S_DEFAULT, S_HEADER, S_DATE, S_NUM, S_INR, S_USD, S_PCT, S_CALC, S_TITLE, \
    S_WRAP, S_INPUT, S_SUBHDR = range(12)


def date_serial(d):
    """Excel serial date (1900 system, with its leap-year quirk baked in)."""
    return (d - datetime.date(1899, 12, 30)).days


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def read_sheet(path, sheet_name):
    """Read a sheet back out of a workbook this module wrote.

    Returns {row_number: {column_letter: value}}. Values are floats for numeric
    cells and str for inline strings; formula cells are skipped, since they are
    regenerated on every write and their cached results are not authoritative.

    Deliberately narrow: it only understands the subset this module emits
    (inline strings, no sharedStrings table), which is all that is needed to
    read a journal back and preserve what a human typed into it.
    """
    import re
    import xml.etree.ElementTree as ET

    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        names = [s.get("name") for s in wb.iter(f"{NS}sheet")]
        if sheet_name not in names:
            raise KeyError(f"no sheet named {sheet_name!r} in {path} (have {names})")
        data = z.read(f"xl/worksheets/sheet{names.index(sheet_name) + 1}.xml")

    out = {}
    for row in ET.fromstring(data).iter(f"{NS}row"):
        r = int(row.get("r"))
        cells = {}
        for c in row.iter(f"{NS}c"):
            if c.find(f"{NS}f") is not None:
                continue
            ref = c.get("r")
            col = re.match(r"([A-Z]+)", ref).group(1)
            if c.get("t") == "inlineStr":
                t = c.find(f"{NS}is/{NS}t")
                if t is not None and t.text:
                    cells[col] = t.text
            else:
                v = c.find(f"{NS}v")
                if v is not None and v.text:
                    try:
                        cells[col] = float(v.text)
                    except ValueError:
                        cells[col] = v.text
        if cells:
            out[r] = cells
    return out


class Sheet:
    def __init__(self, name, freeze="A2", widths=None):
        self.name = name
        self.rows = []
        self.freeze = freeze
        self.widths = widths or {}
        self.validations = []      # (range, [options])
        self.cond_formats = []     # (range, operator, formula, dxf_id)
        self.merges = []

    def add(self, cells):
        self.rows.append(cells)

    def validate(self, rng, options):
        self.validations.append((rng, options))

    def highlight(self, rng, text, dxf_id):
        self.cond_formats.append((rng, text, dxf_id))

    def merge(self, rng):
        self.merges.append(rng)


def _cell_xml(ref, cell):
    if cell is None:
        return ""
    if not isinstance(cell, tuple):
        cell = ("s", cell)
    kind, val = cell[0], cell[1]
    style = cell[2] if len(cell) > 2 else S_DEFAULT
    s = f' s="{style}"' if style else ""
    if val is None or val == "":
        return f'<c r="{ref}"{s}/>'
    if kind == "s":
        return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{esc(val)}</t></is></c>'
    if kind == "f":
        return f'<c r="{ref}"{s}><f>{esc(str(val).lstrip("="))}</f></c>'
    return f'<c r="{ref}"{s}><v>{val}</v></c>'


def _sheet_xml(sheet):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if sheet.freeze:
        row = int("".join(c for c in sheet.freeze if c.isdigit()))
        col = "".join(c for c in sheet.freeze if c.isalpha())
        ycount = row - 1
        xcount = sum((ord(c) - 64) * 26 ** i for i, c in enumerate(reversed(col))) - 1
        parts = []
        if xcount:
            parts.append(f'xSplit="{xcount}"')
        if ycount:
            parts.append(f'ySplit="{ycount}"')
        out.append('<sheetViews><sheetView workbookViewId="0">'
                   f'<pane {" ".join(parts)} topLeftCell="{sheet.freeze}" activePane="bottomRight" state="frozen"/>'
                   '</sheetView></sheetViews>')
    if sheet.widths:
        cols = "".join(f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
                       for i, w in sorted(sheet.widths.items()))
        out.append(f"<cols>{cols}</cols>")
    out.append("<sheetData>")
    for r, cells in enumerate(sheet.rows, start=1):
        body = "".join(_cell_xml(f"{col_letter(c)}{r}", cell) for c, cell in enumerate(cells))
        out.append(f'<row r="{r}">{body}</row>')
    out.append("</sheetData>")
    if sheet.merges:
        out.append(f'<mergeCells count="{len(sheet.merges)}">' +
                   "".join(f'<mergeCell ref="{m}"/>' for m in sheet.merges) + "</mergeCells>")
    for rng, text, dxf in sheet.cond_formats:
        out.append(f'<conditionalFormatting sqref="{rng}">'
                   f'<cfRule type="cellIs" dxfId="{dxf}" priority="{dxf+1}" operator="equal">'
                   f'<formula>"{esc(text)}"</formula></cfRule></conditionalFormatting>')
    if sheet.validations:
        out.append(f'<dataValidations count="{len(sheet.validations)}">')
        for rng, opts in sheet.validations:
            lst = esc(",".join(opts))
            out.append(f'<dataValidation type="list" allowBlank="1" showInputMessage="1" '
                       f'showErrorMessage="1" sqref="{rng}"><formula1>"{lst}"</formula1></dataValidation>')
        out.append("</dataValidations>")
    out.append("</worksheet>")
    return "".join(out)


def _styles_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="5">
<numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/>
<numFmt numFmtId="165" formatCode="#,##0.00"/>
<numFmt numFmtId="166" formatCode="&quot;₹&quot;#,##0.00"/>
<numFmt numFmtId="167" formatCode="&quot;$&quot;#,##0.00"/>
<numFmt numFmtId="168" formatCode="0.0%"/>
</numFmts>
<fonts count="5">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
<font><b/><sz val="14"/><color rgb="FF1F3864"/><name val="Calibri"/></font>
<font><i/><sz val="11"/><color rgb="FF555555"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FF1F3864"/><name val="Calibri"/></font>
</fonts>
<fills count="6">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F2"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFFF9E6"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9E2F3"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color rgb="FFBFBFBF"/></right><top style="thin"><color rgb="FFBFBFBF"/></top><bottom style="thin"><color rgb="FFBFBFBF"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="12">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="166" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="167" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="168" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="165" fontId="3" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1"/>
<xf numFmtId="0" fontId="4" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
</cellXfs>
<dxfs count="3">
<dxf><font><color rgb="FF006100"/></font><fill><patternFill><bgColor rgb="FFC6EFCE"/></patternFill></fill></dxf>
<dxf><font><color rgb="FF9C0006"/></font><fill><patternFill><bgColor rgb="FFFFC7CE"/></patternFill></fill></dxf>
<dxf><font><color rgb="FF7F7F7F"/></font><fill><patternFill><bgColor rgb="FFEDEDED"/></patternFill></fill></dxf>
</dxfs>
</styleSheet>"""


def write(path, sheets):
    n = len(sheets)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    for i in range(n):
        ct.append(f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    ct.append("</Types>")

    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
    for i, s in enumerate(sheets):
        wb.append(f'<sheet name="{esc(s.name)}" sheetId="{i+1}" r:id="rId{i+1}"/>')
    wb.append("</sheets></workbook>")

    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(n):
        rels.append(f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/'
                    f'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i+1}.xml"/>')
    rels.append(f'<Relationship Id="rId{n+1}" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    rels.append("</Relationships>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
                   '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
        z.writestr("xl/styles.xml", _styles_xml())
        for i, s in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i+1}.xml", _sheet_xml(s))
