"""研究設定檔載入與存取。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """設定檔內容不合法。"""


@dataclass
class StudyConfig:
    """一份研究的完整規則設定。

    所有路徑在載入時就解析為絕對路徑（相對路徑以設定檔所在資料夾為基準），
    這樣不論從哪個工作目錄執行 CLI，行為都一致。
    """

    source_path: Path
    raw: dict[str, Any]

    # ---- 基本資訊 ----
    @property
    def study_id(self) -> str:
        return str(self.raw["study_id"])

    @property
    def study_title(self) -> str:
        return str(self.raw.get("study_title", self.study_id))

    @property
    def unit(self) -> str:
        return str(self.raw.get("unit", ""))

    # ---- 路徑 ----
    def path(self, key: str) -> Path:
        paths = self.raw.get("paths", {})
        if key not in paths:
            raise ConfigError(f"設定檔缺少 paths.{key}")
        p = Path(str(paths[key])).expanduser()
        if not p.is_absolute():
            p = (self.source_path.parent / p).resolve()
        return p

    # ---- 區塊存取 ----
    @property
    def standard_curve(self) -> dict[str, Any]:
        return self.raw.get("standard_curve", {})

    @property
    def qc(self) -> dict[str, Any]:
        return self.raw.get("qc", {})

    @property
    def sample(self) -> dict[str, Any]:
        return self.raw.get("sample", {})

    @property
    def reporting(self) -> dict[str, Any]:
        return self.raw.get("reporting", {})

    @property
    def lob(self) -> dict[str, Any]:
        return self.raw.get("lob", {})

    @property
    def summary(self) -> dict[str, Any]:
        return self.raw.get("summary", {})

    @property
    def dot_plot(self) -> dict[str, Any]:
        return self.raw.get("dot_plot", {})

    @property
    def organ_codes(self) -> dict[str, dict[str, str]]:
        return {str(k): dict(v) for k, v in self.raw.get("organ_codes", {}).items()}

    @property
    def timepoint_order(self) -> list[str]:
        return list(self.summary.get("timepoint_order", []))

    @property
    def nd_label(self) -> str:
        return str(self.reporting.get("nd_label", "ND"))

    # ---- 衍生查詢 ----
    def organ_name(self, code: str, lang: str = "en") -> str:
        entry = self.organ_codes.get(str(code))
        if not entry:
            return ""
        return entry.get(lang, "")

    def nominal_concentration(self, std_name: str) -> float | None:
        table = self.standard_curve.get("nominal_concentrations", {})
        value = table.get(std_name)
        return float(value) if value is not None else None

    @property
    def lloq_point(self) -> str:
        return str(self.standard_curve.get("lloq_point", "STD08"))

    def timepoint_sort_key(self, timepoint: str) -> tuple[int, str]:
        """未列在 timepoint_order 的時間點排到最後，但仍穩定排序。"""
        order = self.timepoint_order
        try:
            return (order.index(timepoint), "")
        except ValueError:
            return (len(order), str(timepoint))

    def group_for_timepoint(self, timepoint: str) -> str:
        """依計畫書 Table.1 由採樣時間點判定組別。

        同一時間點對應到多個組別時回傳 ambiguous_label，絕不猜測。
        """
        groups = self.raw.get("groups", {})
        ambiguous = str(groups.get("ambiguous_label", "待確認"))
        matches = [
            name
            for name, tps in groups.items()
            if name != "ambiguous_label" and timepoint in (tps or [])
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return ambiguous
        return ambiguous

    # ---- 編譯好的 regex（避免每列重複編譯）----
    @property
    def animal_regex(self) -> re.Pattern[str]:
        return _compile(self.sample.get("animal_pattern", r"^(?P<animal>\d{4})[_-](?P<organ_code>\d{2})(?P<suffix>.*)$"))

    @property
    def std_regex(self) -> re.Pattern[str]:
        return _compile(self.standard_curve.get("name_pattern", r"^STD(?P<index>\d{2})$"))

    @property
    def matrix_regex(self) -> re.Pattern[str]:
        pattern = self.qc.get("matrix_control", {}).get(
            "name_pattern", r"^(?P<organ_code>\d{2})\s+mouse\s+.*DNA$"
        )
        return _compile(pattern, re.IGNORECASE)

    @property
    def sensitivity_regex(self) -> re.Pattern[str]:
        pattern = self.qc.get("sensitivity_control", {}).get(
            "name_pattern", r"(?P<conc>\d+(?:\.\d+)?)\s*pg"
        )
        return _compile(pattern, re.IGNORECASE)


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    try:
        return re.compile(pattern, flags)
    except re.error as exc:  # pragma: no cover - 設定錯誤才會走到
        raise ConfigError(f"設定檔 regex 無法編譯: {pattern!r} ({exc})") from exc


def load_config(path: str | Path) -> StudyConfig:
    """讀取 YAML 設定檔。"""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ConfigError(f"找不到設定檔: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"設定檔格式錯誤（最外層必須是對應表）: {p}")
    for required in ("study_id", "paths"):
        if required not in data:
            raise ConfigError(f"設定檔缺少必要欄位: {required}")
    return StudyConfig(source_path=p, raw=data)
