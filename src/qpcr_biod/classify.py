"""逐孔資料的檢體分類與 rerun 判定。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from .config import StudyConfig


# 版本標籤沿用既有統整表的寫法，對帳時才不會整批顯示為文字不一致
ORIGINAL_LABEL = "原始"
RERUN_LABEL = "Rerun"


class SampleClass(str, Enum):
    STANDARD = "標準品(標準曲線點)"
    ANIMAL = "動物檢體"
    SENSITIVITY_QC = "敏感度對照"
    MATRIX_QC = "臟器基質QC對照組"
    NTC = "陰性對照(NTC)"
    STD_ACCURACY = "已知濃度回推QC"
    UNKNOWN = "未分類"


@dataclass(frozen=True)
class SampleIdentity:
    """單一孔位解析出的身分資訊。"""

    sample_class: SampleClass
    animal_id: str | None = None
    organ_code: str | None = None
    std_point: str | None = None
    nominal_concentration: float | None = None
    is_rerun_by_name: bool = False


def classify_sample(sample_name: str, task: str, config: StudyConfig) -> SampleIdentity:
    """依樣品名稱與 Task 判定檢體類別。

    順序有意義：STANDARD task 的 STDxx 是標準曲線點，但同名樣品若以 UNKNOWN
    task 重跑，就是「已知濃度回推 QC」，兩者不可混為一談。
    """
    name = (sample_name or "").strip()
    task_upper = (task or "").strip().upper()

    if not name:
        return SampleIdentity(SampleClass.UNKNOWN)

    # NTC 先判，因為它的 task 就叫 NTC
    ntc_names = {n.upper() for n in config.qc.get("ntc", {}).get("names", ["NTC"])}
    if task_upper == "NTC" or name.upper() in ntc_names:
        return SampleIdentity(SampleClass.NTC)

    std_match = config.std_regex.match(name)
    if std_match:
        nominal = config.nominal_concentration(name)
        if task_upper == "STANDARD":
            return SampleIdentity(
                SampleClass.STANDARD, std_point=name, nominal_concentration=nominal
            )
        # 同名但 task 是 UNKNOWN -> 已知濃度回推 QC
        return SampleIdentity(
            SampleClass.STD_ACCURACY, std_point=name, nominal_concentration=nominal
        )

    matrix_match = config.matrix_regex.match(name)
    if matrix_match:
        return SampleIdentity(
            SampleClass.MATRIX_QC, organ_code=matrix_match.group("organ_code")
        )

    animal_match = config.animal_regex.match(name)
    if animal_match:
        suffix = (animal_match.groupdict().get("suffix") or "").strip()
        return SampleIdentity(
            SampleClass.ANIMAL,
            animal_id=animal_match.group("animal"),
            organ_code=animal_match.group("organ_code"),
            is_rerun_by_name=_is_rerun_suffix(suffix, config),
        )

    # 敏感度對照組：名稱含 "<濃度>pg"，且不是動物檢體
    sens_match = config.sensitivity_regex.search(name)
    if sens_match:
        return SampleIdentity(
            SampleClass.SENSITIVITY_QC,
            nominal_concentration=float(sens_match.group("conc")),
        )

    return SampleIdentity(SampleClass.UNKNOWN)


def _is_rerun_suffix(suffix: str, config: StudyConfig) -> bool:
    if not suffix:
        return False
    suffixes = config.sample.get("rerun_suffixes", [])
    lowered = suffix.lower()
    return any(lowered == s.lower() or lowered.startswith(s.lower()) for s in suffixes)


def annotate_wells(wells: pd.DataFrame, config: StudyConfig) -> pd.DataFrame:
    """為逐孔資料加上分類欄位，回傳新的 DataFrame（不就地修改）。"""
    frame = wells.copy()
    identities = [
        classify_sample(row.sample_name, row.task, config)
        for row in frame.itertuples(index=False)
    ]
    frame["sample_class"] = [i.sample_class.value for i in identities]
    frame["animal_id"] = [i.animal_id for i in identities]
    frame["organ_code"] = [i.organ_code for i in identities]
    frame["std_point"] = [i.std_point for i in identities]
    frame["nominal_concentration"] = [i.nominal_concentration for i in identities]
    frame["is_rerun_by_name"] = [i.is_rerun_by_name for i in identities]
    frame["sample_key"] = [
        f"{i.animal_id}|{i.organ_code}" if i.animal_id and i.organ_code else None
        for i in identities
    ]
    return frame


def resolve_reruns(wells: pd.DataFrame, run_order: dict[str, int],
                   config: StudyConfig) -> pd.DataFrame:
    """標記每個檢體版本是「原始」還是「Rerun」。

    版本的身分是 (來源檔案, Sample Name)，不是 (來源檔案) —— 因為 rerun 有兩種
    形式，而且可能同時出現：

      1. 名稱後綴：同一塊盤上有 `1011_03` 與 `1011_03_re`
      2. 較晚的 run：同一 動物_臟器 在後面的檔案又跑了一次

    以檔案為單位判定會把同盤的兩個版本併成一筆，並把原始那筆也誤標成 Rerun。
    第 2 條可由設定關閉。
    """
    frame = wells.copy()
    frame["version"] = ORIGINAL_LABEL
    frame["rerun_basis"] = ""
    frame["run_order"] = frame["source_file"].map(run_order).fillna(0).astype(int)

    animals = frame[frame["sample_class"] == SampleClass.ANIMAL.value]
    if animals.empty:
        return frame

    later_is_rerun = bool(config.sample.get("later_run_is_rerun", True))

    for key, group in animals.groupby("sample_key", dropna=True):
        versions = group[["source_file", "sample_name", "is_rerun_by_name"]].drop_duplicates()
        earliest = min(run_order.get(f, 0) for f in versions["source_file"])

        for version in versions.itertuples(index=False):
            named = bool(version.is_rerun_by_name)
            positional = later_is_rerun and run_order.get(version.source_file, 0) > earliest
            if not (named or positional):
                continue

            basis = []
            if named:
                basis.append("樣品名稱標記")
            if positional:
                basis.append("較晚的run")

            mask = (
                (frame["sample_key"] == key)
                & (frame["source_file"] == version.source_file)
                & (frame["sample_name"] == version.sample_name)
            )
            frame.loc[mask, "version"] = RERUN_LABEL
            frame.loc[mask, "rerun_basis"] = "、".join(basis)

    return frame
