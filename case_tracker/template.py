"""產生「匯入範本.xlsx」：可直接填寫並匯入的空白表格。

第一個工作表是要填的表格（表頭 + 下拉選單），刻意不放範例資料，
避免有人忘了刪就整份匯進來；範例與逐欄說明放在第二個工作表。
"""

import io
import zipfile
from xml.sax.saxutils import escape

from . import models
from .export import _clean, _col_letter

SHEET_DATA = "案件清單"
SHEET_HELP = "填寫說明"

# 要填寫的欄位（順序即欄位順序）
COLUMNS = [(field, label) for field, label in models.FIELD_LABELS]

COLUMN_WIDTHS = [16, 16, 16, 34, 20, 14, 16, 30, 13, 12, 10, 40]

# 下拉選單：欄位 -> 選項
DROPDOWNS = {
    "case_type": models.CASE_TYPES,
    "stage": models.STAGES,
    "status": models.STATUSES,
}

VALIDATION_ROWS = 500  # 下拉選單套用到第幾列

REQUIRED = {"case_no"}

FIELD_NOTES = {
    "case_no": "必填。必須以 QT 開頭，例如 QT114001。同一個編號再匯入一次會更新原本那筆，不會重複新增。",
    "contract_no": "可留空。同一份合約涵蓋多個案件時，填相同的合約編號即可，日後用搜尋就能一次列出。",
    "study_no": "可留空。格式不限，例如 TMT-114-003。尚未立案可先空著，之後在系統裡補。",
    "title": "可留空，但建議填。用一句話說明這件案子做什麼，例如「SD 大鼠 28 天重複劑量毒性試驗」。搜尋時也會比對這一欄。",
    "client": "可留空，但建議填，篩選與搜尋都會用到。",
    "case_type": "從下拉選單選；留空預設為「" + models.CASE_TYPES[0] + "」。",
    "stage": "從下拉選單選；留空預設為「" + models.STAGES[0] + "」。",
    "next_milestone": "可留空。寫下一個要交付或完成的事項，例如「計畫書送 QA 審查」。",
    "due_date": "可留空。建議用 2026-10-15 這種寫法；2026/10/15、20261015、民國年 115/1/15 也認得。逾期會自動標紅。",
    "owner": "可留空。填公司內部慣用的姓名即可。",
    "status": "從下拉選單選；留空預設為「" + models.STATUSES[0] + "」。標為「已結案」的案件不再計算逾期。",
    "notes": "可留空，長度不限。",
}

EXAMPLE_ROWS = [
    ["QT114001", "C-114-021", "TMT-114-003", "SD 大鼠 28 天重複劑量毒性試驗",
     "宏碩生技", "GLP 研究", "試驗執行中", "第 28 天中期報告", "2026-10-15",
     "陳彥廷", "進行中", "含恢復期組別"],
    ["QT114002", "C-114-021", "", "小鼠急性藥效評估",
     "宏碩生技", "藥理試驗", "計畫書撰寫", "計畫書送客戶確認", "2026-09-30",
     "林郁涵", "需留意", "與 QT114001 同一份合約"],
    ["QT114003", "", "", "血漿檢體前導分析",
     "光宇製藥", "Pilot study", "計畫書撰寫", "報價確認", "",
     "王孟儒", "進行中", "尚未簽約，合約與研究編號待補"],
]

USAGE_STEPS = [
    "1. 在「" + SHEET_DATA + "」工作表填資料，一件案子一列，從第 2 列開始填。",
    "2. 案件類型、目前階段、狀態這三欄請用儲存格右側的下拉選單選，避免打錯字。",
    "3. 欄位順序可以調整，也可以自己加欄（多出來的欄不會被匯入）；但表頭那一列請保留。",
    "4. 填好後存檔，在系統裡按「匯入 Excel／CSV」選這個檔案。",
    "5. 系統會先顯示預覽（哪幾列新增、哪幾列更新、哪幾列有問題），確認後才寫入。",
    "",
    "本檔案的第一個工作表刻意留空（只有表頭），避免有人忘記刪掉範例列就整份匯入。",
    "下方的範例僅供參考，請不要匯入這個「" + SHEET_HELP + "」工作表。",
]

# --------------------------------------------------------------------------
# XML 樣板
# --------------------------------------------------------------------------

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "</Types>"
)

ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{REL}/officeDocument"'
    ' Target="xl/workbook.xml"/></Relationships>'
)

WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>'
    f'<sheet name="{SHEET_DATA}" sheetId="1" r:id="rId1"/>'
    f'<sheet name="{SHEET_HELP}" sheetId="2" r:id="rId2"/>'
    "</sheets></workbook>"
)

WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PKG}">'
    f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>'
    f'<Relationship Id="rId2" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/>'
    f'<Relationship Id="rId3" Type="{REL}/styles" Target="styles.xml"/>'
    "</Relationships>"
)

# 樣式索引：0 一般、1 表頭、2 日期、3 換行說明、4 粗體小標、5 灰字
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<styleSheet xmlns="{NS}">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/></numFmts>'
    '<fonts count="4">'
    '<font><sz val="11"/><color rgb="FF1C1C1A"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FF1C1C1A"/><name val="Calibri"/></font>'
    '<font><b/><sz val="12"/><color rgb="FF2F5266"/><name val="Calibri"/></font>'
    '<font><sz val="11"/><color rgb="FF74736A"/><name val="Calibri"/></font>'
    "</fonts>"
    '<fills count="3">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE6E5DE"/><bgColor indexed="64"/></patternFill></fill>'
    "</fills>"
    '<borders count="2">'
    "<border><left/><right/><top/><bottom/><diagonal/></border>"
    '<border><left/><right/><top/><bottom style="thin"><color rgb="FF8C8C84"/></bottom><diagonal/></border>'
    "</borders>"
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


def _cell(ref, text, style=0):
    if text == "" or text is None:
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    return (
        f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">'
        f"{escape(_clean(str(text)))}</t></is></c>"
    )


def _row(index, cells):
    """cells 為 [(文字, 樣式)]；空字串也會佔位，欄位才不會偏掉。"""
    body = "".join(
        _cell(f"{_col_letter(column)}{index}", text, style)
        for column, (text, style) in enumerate(cells, start=1)
    )
    return f'<row r="{index}">{body}</row>'


def _validations():
    parts = []
    for field, options in DROPDOWNS.items():
        column = _col_letter([f for f, _ in COLUMNS].index(field) + 1)
        formula = escape(",".join(options))
        parts.append(
            f'<dataValidation type="list" allowBlank="1" showInputMessage="1"'
            f' showErrorMessage="1" errorTitle="請從清單選擇"'
            f' error="請直接點右側箭頭選一個選項。" sqref="{column}2:{column}{VALIDATION_ROWS}">'
            f"<formula1>&quot;{formula}&quot;</formula1></dataValidation>"
        )
    return f'<dataValidations count="{len(parts)}">' + "".join(parts) + "</dataValidations>"


def _data_sheet():
    date_column = [f for f, _ in COLUMNS].index("due_date") + 1
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{width}" customWidth="1"'
        + (' style="2"' if i == date_column else "")
        + "/>"
        for i, width in enumerate(COLUMN_WIDTHS, start=1)
    )
    header = _row(1, [(label, 1) for _, label in COLUMNS])
    last_column = _col_letter(len(COLUMNS))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS}">'
        '<sheetViews><sheetView workbookViewId="0" tabSelected="1">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{cols}</cols>"
        f"<sheetData>{header}</sheetData>"
        f'<autoFilter ref="A1:{last_column}1"/>'
        f"{_validations()}"
        "</worksheet>"
    )


def _help_sheet():
    rows = []
    index = 1

    def add(cells):
        nonlocal index
        rows.append(_row(index, cells))
        index += 1

    add([("案件追蹤系統　匯入範本使用說明", 4)])
    add([("", 0)])
    for step in USAGE_STEPS:
        add([(step, 0)])
    add([("", 0)])

    add([("欄位說明", 4)])
    add([("欄位", 1), ("必填", 1), ("說明", 1)])
    for field, label in COLUMNS:
        add([
            (label, 0),
            ("必填" if field in REQUIRED else "可留空", 0),
            (FIELD_NOTES.get(field, ""), 3),
        ])
    add([("", 0)])

    add([("填寫範例（僅供參考，請勿直接匯入本工作表）", 4)])
    add([(label, 1) for _, label in COLUMNS])
    for example in EXAMPLE_ROWS:
        add([(value, 0) for value in example])

    cols = (
        '<col min="1" max="1" width="18" customWidth="1"/>'
        '<col min="2" max="2" width="10" customWidth="1"/>'
        '<col min="3" max="3" width="82" customWidth="1"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS}">'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{cols}</cols>"
        f"<sheetData>{''.join(rows)}</sheetData>"
        "</worksheet>"
    )


def build():
    """回傳匯入範本的 .xlsx 檔案 bytes。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("xl/workbook.xml", WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", STYLES)
        archive.writestr("xl/worksheets/sheet1.xml", _data_sheet())
        archive.writestr("xl/worksheets/sheet2.xml", _help_sheet())
    return buffer.getvalue()
