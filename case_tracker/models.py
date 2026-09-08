"""資料模型：欄位定義、選項清單與驗證邏輯。"""

from datetime import date, datetime

CASE_TYPES = [
    "GLP 研究",
    "藥理試驗",
    "Pilot study",
    "方法確效",
    "生物分析",
    "稽核",
    "外包試驗支援",
    "BD 授權評估",
]

# 依實際流程順序排列，前端以此顯示進度。
STAGES = [
    "計畫書撰寫",
    "計畫書已核准",
    "試驗執行中",
    "檢體分析中",
    "QA 審查",
    "報告草稿",
    "客戶審閱",
    "正式報告已發出",
    "結案",
]

STATUSES = ["進行中", "需留意", "已延遲", "已結案"]

CLOSED_STATUS = "已結案"

# 到期日在幾天內視為「即將到期」。
DUE_SOON_DAYS = 7

# 案件編號（報價／委託案號）一律以此開頭；研究編號與合約編號則不限格式。
CASE_NO_PREFIX = "QT"

# 可編輯欄位 -> 中文標籤（同時作為匯出欄位順序）。
FIELD_LABELS = [
    ("case_no", "案件編號"),
    ("contract_no", "合約編號"),
    ("study_no", "研究編號"),
    ("client", "客戶名稱"),
    ("case_type", "案件類型"),
    ("stage", "目前階段"),
    ("next_milestone", "下一個里程碑"),
    ("due_date", "到期日"),
    ("owner", "負責人"),
    ("status", "狀態"),
    ("notes", "備註"),
]

EDITABLE_FIELDS = [name for name, _ in FIELD_LABELS]

EXPORT_COLUMNS = FIELD_LABELS[:10] + [
    ("due_label", "期限狀態"),
    ("notes", "備註"),
    ("updated_at", "最後更新"),
    ("updated_by", "更新者"),
]

MAX_LEN = {
    "case_no": 64,
    "contract_no": 64,
    "study_no": 64,
    "client": 120,
    "next_milestone": 200,
    "owner": 60,
    "notes": 4000,
}


class ValidationError(ValueError):
    """欄位驗證失敗，errors 為 {欄位: 訊息}。"""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("；".join(f"{k}：{v}" for k, v in errors.items()))


def parse_date(value):
    """把 'YYYY-MM-DD' 轉成 date；空值回傳 None，格式錯誤丟 ValueError。"""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def validate(payload, partial=False):
    """驗證並正規化案件資料，回傳只含合法欄位的 dict。"""
    errors = {}
    clean = {}

    for field in EDITABLE_FIELDS:
        if partial and field not in payload:
            continue
        raw = payload.get(field, "")
        value = "" if raw is None else str(raw).strip()

        if field == "case_no":
            if not value:
                errors[field] = "案件編號為必填"
            elif len(value) > MAX_LEN[field]:
                errors[field] = f"案件編號不可超過 {MAX_LEN[field]} 字"
            elif value[: len(CASE_NO_PREFIX)].upper() == CASE_NO_PREFIX:
                value = CASE_NO_PREFIX + value[len(CASE_NO_PREFIX):]  # 前綴統一大寫
            else:
                errors[field] = f"案件編號須以 {CASE_NO_PREFIX} 開頭（例：{CASE_NO_PREFIX}114001）"
        elif field == "case_type":
            if value not in CASE_TYPES:
                errors[field] = "案件類型不在允許清單中"
        elif field == "stage":
            if value not in STAGES:
                errors[field] = "目前階段不在允許清單中"
        elif field == "status":
            if value not in STATUSES:
                errors[field] = "狀態不在允許清單中"
        elif field == "due_date":
            try:
                parsed = parse_date(value)
            except ValueError:
                errors[field] = "到期日格式須為 YYYY-MM-DD"
            else:
                value = parsed.isoformat() if parsed else ""
        elif field in MAX_LEN and len(value) > MAX_LEN[field]:
            errors[field] = f"{dict(FIELD_LABELS)[field]}不可超過 {MAX_LEN[field]} 字"

        clean[field] = value

    if errors:
        raise ValidationError(errors)
    return clean


def due_state(due_date, status, today=None):
    """計算期限狀態，回傳 (state, days, label)。

    state 為 overdue / due_soon / scheduled / none / closed，
    days 為距離到期日的天數（負數代表已逾期）。
    """
    today = today or date.today()
    if status == CLOSED_STATUS:
        return "closed", None, "已結案"
    try:
        due = parse_date(due_date)
    except ValueError:
        due = None
    if due is None:
        return "none", None, "未設定"

    days = (due - today).days
    if days < 0:
        return "overdue", days, f"逾期 {abs(days)} 天"
    if days == 0:
        return "due_soon", 0, "今日到期"
    if days <= DUE_SOON_DAYS:
        return "due_soon", days, f"剩 {days} 天"
    return "scheduled", days, f"剩 {days} 天"


def decorate(row, today=None):
    """把資料列補上前端／匯出用的衍生欄位。"""
    item = dict(row)
    state, days, label = due_state(item.get("due_date"), item.get("status"), today)
    item["due_state"] = state
    item["due_days"] = days
    item["due_label"] = label
    return item
