"""以標準函式庫讀取 .xlsx／.xlsm 工作表，不需要 openpyxl。

.xlsx 其實是一個 zip 檔，裡面放著幾份 XML。這裡只做「讀成一格一格的文字」
這件事：共用字串、行內字串、公式結果都會取出，設定成日期格式的儲存格會
還原成 YYYY-MM-DD。合併儲存格取左上角的值，其餘視為空白（Excel 本來就
只把值存在左上角）。
"""

import datetime
import io
import re
import zipfile
from xml.etree import ElementTree

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

EXCEL_EPOCH = datetime.date(1899, 12, 30)

# Excel 內建的日期／時間格式代碼
BUILTIN_DATE_FORMATS = (
    set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
)
DATE_TOKEN_RE = re.compile(r"(?<!\\)[ymd]", re.IGNORECASE)
CELL_REF_RE = re.compile(r"^([A-Z]+)")

MAX_ROWS = 50000
MAX_COLUMNS = 200


class SheetNotFound(ValueError):
    """指定的工作表不存在。"""


def _column_index(ref):
    """儲存格位址轉欄位序號：A1 -> 0、AB7 -> 27。"""
    match = CELL_REF_RE.match(ref or "")
    if not match:
        return None
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _text_of(element):
    """取出 <si>／<is> 底下所有 <t> 的文字（含 rich text 的多段落）。"""
    return "".join(node.text or "" for node in element.iter(f"{MAIN}t"))


def _shared_strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_text_of(si) for si in root.findall(f"{MAIN}si")]


def _date_style_indexes(archive):
    """回傳「顯示成日期」的樣式索引集合。"""
    if "xl/styles.xml" not in archive.namelist():
        return set()
    root = ElementTree.fromstring(archive.read("xl/styles.xml"))

    custom = {}
    for fmt in root.iter(f"{MAIN}numFmt"):
        try:
            fmt_id = int(fmt.get("numFmtId", "-1"))
        except ValueError:
            continue
        custom[fmt_id] = fmt.get("formatCode", "")

    date_styles = set()
    cell_xfs = root.find(f"{MAIN}cellXfs")
    if cell_xfs is None:
        return date_styles
    for index, xf in enumerate(cell_xfs.findall(f"{MAIN}xf")):
        try:
            fmt_id = int(xf.get("numFmtId", "0"))
        except ValueError:
            continue
        if fmt_id in BUILTIN_DATE_FORMATS:
            date_styles.add(index)
        elif fmt_id in custom and DATE_TOKEN_RE.search(custom[fmt_id]):
            date_styles.add(index)
    return date_styles


def sheet_targets(archive):
    """回傳 [(工作表名稱, zip 內路徑)]，順序同 Excel 分頁順序。"""
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))

    targets = {}
    if "xl/_rels/workbook.xml.rels" in archive.namelist():
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rels.findall(f"{PKG_REL}Relationship"):
            target = rel.get("Target", "")
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = "xl/" + target
            targets[rel.get("Id")] = target.replace("/./", "/")

    sheets = []
    for sheet in root.iter(f"{MAIN}sheet"):
        path = targets.get(sheet.get(f"{REL}id"))
        if path and path in archive.namelist():
            sheets.append((sheet.get("name", ""), path))
    return sheets


def _serial_to_date(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > 2958466:  # 超出 Excel 日期範圍
        return None
    return (EXCEL_EPOCH + datetime.timedelta(days=int(number))).isoformat()


def _number_text(value):
    """數值去掉多餘的小數點：45000.0 -> 45000。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number.is_integer():
        return str(int(number))
    return repr(number)


def _cell_text(cell, shared, date_styles):
    kind = cell.get("t", "n")

    if kind == "inlineStr":
        node = cell.find(f"{MAIN}is")
        return _text_of(node) if node is not None else ""

    value_node = cell.find(f"{MAIN}v")
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text

    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if kind in ("str", "e"):
        return "" if kind == "e" else raw
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"

    try:
        style = int(cell.get("s", "-1"))
    except ValueError:
        style = -1
    if style in date_styles:
        converted = _serial_to_date(raw)
        if converted:
            return converted
    return _number_text(raw)


def _read_sheet(archive, path, shared, date_styles):
    rows = []
    root = ElementTree.fromstring(archive.read(path))
    for row in root.iter(f"{MAIN}row"):
        # Excel 會略過完全空白的列，這裡依 r 屬性補回來，
        # 讀到的列號才會跟使用者在 Excel 看到的一致。
        try:
            target = int(row.get("r", "0"))
        except ValueError:
            target = 0
        if target > 0:
            while len(rows) < target - 1:
                rows.append([])
                if len(rows) >= MAX_ROWS:
                    return rows

        values = []
        for cell in row.findall(f"{MAIN}c"):
            index = _column_index(cell.get("r", ""))
            if index is None:
                index = len(values)
            if index >= MAX_COLUMNS:
                continue
            while len(values) < index:
                values.append("")
            text = _cell_text(cell, shared, date_styles)
            values.append(text.strip() if isinstance(text, str) else text)
        rows.append(values)
        if len(rows) >= MAX_ROWS:
            break
    return rows


def sheet_names(source):
    with zipfile.ZipFile(_as_file(source)) as archive:
        return [name for name, _ in sheet_targets(archive)]


def _as_file(source):
    if isinstance(source, (bytes, bytearray)):
        return io.BytesIO(source)
    return source


def read_rows(source, sheet=None):
    """讀取工作表內容，回傳 (工作表名稱, 所有工作表名稱, 資料列)。

    sheet 可給名稱；省略時取第一個工作表。
    """
    try:
        archive = zipfile.ZipFile(_as_file(source))
    except zipfile.BadZipFile:
        raise ValueError(
            "這個檔案不是有效的 Excel 檔（.xlsx）。若為舊版 .xls，"
            "請用 Excel 另存為 .xlsx 後再試一次。"
        )

    with archive:
        if "xl/workbook.xml" not in archive.namelist():
            raise ValueError("Excel 檔內容不完整，找不到工作表資料。")
        sheets = sheet_targets(archive)
        if not sheets:
            raise ValueError("這個 Excel 檔沒有可讀取的工作表。")

        names = [name for name, _ in sheets]
        if sheet:
            matched = [item for item in sheets if item[0] == sheet]
            if not matched:
                raise SheetNotFound(
                    f"找不到名為「{sheet}」的工作表；這個檔案有："
                    + "、".join(names)
                )
            name, path = matched[0]
        else:
            name, path = sheets[0]

        shared = _shared_strings(archive)
        date_styles = _date_style_indexes(archive)
        return name, names, _read_sheet(archive, path, shared, date_styles)
