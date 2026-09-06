"""匯出：CSV 與 Excel（.xlsx）。

XLSX 直接以 zipfile + XML 產生，不需要 openpyxl 等第三方套件。
CSV 以 UTF-8-BOM 輸出，Excel 開啟中文不會亂碼。
"""

import csv
import io
import zipfile
from datetime import date, datetime
from xml.sax.saxutils import escape

from . import models

EXCEL_EPOCH = date(1899, 12, 30)

# 匯出欄寬（字元數），依 EXPORT_COLUMNS 順序。
COLUMN_WIDTHS = [18, 18, 18, 22, 14, 16, 30, 12, 12, 10, 14, 46, 20, 12]


def _cell_value(item, key):
    value = item.get(key, "")
    return "" if value is None else str(value)


def rows_for_export(items, columns=None):
    """回傳 (表頭, 資料列) 供 CSV／XLSX 共用。"""
    columns = columns or models.EXPORT_COLUMNS
    header = [label for _, label in columns]
    body = [[_cell_value(item, key) for key, _ in columns] for item in items]
    return header, body


def to_csv(items, columns=None):
    """回傳 bytes（UTF-8 with BOM，CRLF 換行）。"""
    header, body = rows_for_export(items, columns)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(body)
    return buffer.getvalue().encode("utf-8-sig")


# --------------------------------------------------------------------------
# XLSX
# --------------------------------------------------------------------------

def _col_letter(index):
    """1 -> A, 27 -> AA"""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _clean(text):
    """移除 XML 不允許的控制字元。"""
    return "".join(ch for ch in text if ch >= " " or ch in "\t\n")


def _excel_serial(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (parsed - EXCEL_EPOCH).days


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "</Types>"
)

ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)

WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)

STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/></numFmts>'
    '<fonts count="2">'
    '<font><sz val="11"/><color rgb="FF1C1C1A"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FF1C1C1A"/><name val="Calibri"/></font>'
    "</fonts>"
    '<fills count="3">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE6E5DE"/><bgColor indexed="64"/></patternFill></fill>'
    "</fills>"
    '<borders count="2">'
    "<border><left/><right/><top/><bottom/><diagonal/></border>"
    '<border><left/><right/><top/>'
    '<bottom style="thin"><color rgb="FF8C8C84"/></bottom><diagonal/></border>'
    "</borders>"
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1"'
    ' applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0"'
    ' applyNumberFormat="1" applyAlignment="1"><alignment vertical="top"/></xf>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


def _workbook_xml(sheet_name):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )


def _sheet_xml(header, body, date_columns, widths):
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        '<sheetViews><sheetView workbookViewId="0" tabSelected="1">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>",
        '<sheetFormatPr defaultRowHeight="15"/>',
    ]

    if widths:
        parts.append("<cols>")
        for index, width in enumerate(widths[: len(header)], start=1):
            parts.append(
                f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
            )
        parts.append("</cols>")

    parts.append("<sheetData>")
    span = f"1:{len(header)}"

    parts.append(f'<row r="1" spans="{span}" ht="18" customHeight="1">')
    for index, label in enumerate(header, start=1):
        ref = f"{_col_letter(index)}1"
        parts.append(
            f'<c r="{ref}" s="1" t="inlineStr"><is><t xml:space="preserve">'
            f"{escape(_clean(str(label)))}</t></is></c>"
        )
    parts.append("</row>")

    for row_index, row in enumerate(body, start=2):
        parts.append(f'<row r="{row_index}" spans="{span}">')
        for col_index, value in enumerate(row, start=1):
            ref = f"{_col_letter(col_index)}{row_index}"
            text = "" if value is None else str(value)
            if not text:
                continue
            serial = _excel_serial(text) if col_index in date_columns else None
            if serial is not None:
                parts.append(f'<c r="{ref}" s="2"><v>{serial}</v></c>')
            else:
                parts.append(
                    f'<c r="{ref}" s="0" t="inlineStr"><is><t xml:space="preserve">'
                    f"{escape(_clean(text))}</t></is></c>"
                )
        parts.append("</row>")

    parts.append("</sheetData>")
    if body:
        last = f"{_col_letter(len(header))}{len(body) + 1}"
        parts.append(f'<autoFilter ref="A1:{last}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


def to_xlsx(items, columns=None, sheet_name="案件清單", widths=None):
    """回傳 .xlsx 檔案 bytes。到期日欄位輸出為真正的 Excel 日期。"""
    columns = columns or models.EXPORT_COLUMNS
    header, body = rows_for_export(items, columns)
    date_columns = {
        index for index, (key, _) in enumerate(columns, start=1) if key == "due_date"
    }
    widths = widths or COLUMN_WIDTHS

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("xl/workbook.xml", _workbook_xml(sheet_name))
        archive.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", STYLES)
        archive.writestr(
            "xl/worksheets/sheet1.xml", _sheet_xml(header, body, date_columns, widths)
        )
    return buffer.getvalue()
