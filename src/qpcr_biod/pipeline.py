"""端到端執行流程。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .classify import annotate_wells, resolve_reruns
from .config import StudyConfig
from .consolidate import build_consolidated
from .curves import StandardCurve, fit_all_curves
from .decisions import DecisionSet, load_decisions
from .lob import compute_lob
from .manifest import Manifest, build_manifest
from .parsing import RunFile, discover_run_files, read_run_file
from .qc import evaluate_qc
from .reference import load_reference
from .report import write_report
from .summary import build_summaries


@dataclass
class PipelineResult:
    output_path: Path | None
    consolidated: pd.DataFrame
    manifest: Manifest
    warnings: list[str] = field(default_factory=list)
    run_count: int = 0
    sample_count: int = 0
    manual_review_count: int = 0


def run_pipeline(config: StudyConfig, *, output_name: str | None = None,
                 write_output: bool = True) -> PipelineResult:
    """讀原始檔 → 套規則 → 套人工決策 → 輸出統整活頁簿。

    write_output=False 時只做計算與檢查，不碰輸出檔 —— `check` 指令用這個模式，
    才不會在只想檢查的時候覆蓋掉同仁正在看的統整表。
    """
    warnings: list[str] = []
    manifest = build_manifest(config.study_id, config.source_path)

    # 1. 讀原始檔
    raw_paths = discover_run_files(config.path("raw_dir"))
    if not raw_paths:
        raise FileNotFoundError(
            f"{config.path('raw_dir')} 內找不到任何 .xls/.xlsx 原始檔。"
        )
    runs = [read_run_file(path) for path in raw_paths]
    runs.sort(key=lambda r: (r.run_end_time_sortable, r.filename))
    for run in runs:
        warnings.extend(f"[{run.filename}] {w}" for w in run.warnings)
        manifest.inputs.append({
            "檔案名稱": run.filename,
            "檔案類型": "qPCR原始數據(StepOnePlus export)",
            "Run結束時間(原始檔案內)": run.run_end_time or "",
            "孔位數": len(run.wells),
            "SHA-256": run.sha256,
            "狀態": "已匯入",
        })

    wells = pd.concat([run.wells for run in runs], ignore_index=True)
    wells = annotate_wells(wells, config)
    run_order = {run.filename: index for index, run in enumerate(runs)}
    wells = resolve_reruns(wells, run_order, config)

    unclassified = wells[wells["sample_class"] == "未分類"]
    for name in sorted(set(unclassified["sample_name"].dropna())):
        warnings.append(
            f"樣品名稱「{name}」無法歸類到任何檢體類別，該孔位僅保留於 Raw_Import，"
            "未納入統整。如為新的命名規則，請調整設定檔的 sample/qc 規則。"
        )

    # 2. 標準曲線與 QC
    curves = fit_all_curves(wells, config)
    for source_file, curve in curves.items():
        warnings.extend(f"[{source_file}] {note}" for note in curve.notes)
    qc_points = evaluate_qc(wells, curves, config)

    # 3. 參考資料與人工決策
    reference = load_reference(config.path("reference_dir"), config)
    warnings.extend(reference.warnings)
    decisions = _load_decisions_safely(config, warnings)
    _record_decisions(manifest, decisions)

    # 4. 統整
    result = build_consolidated(wells, curves, reference, decisions, config)
    warnings.extend(result.warnings)

    # 5. 血液 LOB 與彙整報表
    lob = compute_lob(wells, result.table, config)
    summaries = build_summaries(result.table, lob, config)

    # 6. 輸出
    manifest.warnings = warnings
    for entry in reference.files:
        manifest.inputs.append({**entry, "SHA-256": "", "檔案類型": entry.get("檔案類型", "參考資料")})

    output_path: Path | None = None
    if write_output:
        output_dir = config.path("output_dir")
        filename = output_name or f"{config.study_id}_統整表.xlsx"
        output_path = write_report(
            output_dir / filename,
            config=config,
            consolidated=result.table,
            summaries=summaries,
            lob=lob,
            curves_frame=_curves_frame(curves, config),
            qc_frame=_qc_frame(qc_points),
            organ_codes=_organ_codes_frame(config, decisions, result.table, warnings),
            animals=_animals_frame(result.table, reference),
            raw=wells,
            files=manifest.inputs_frame(),
            manifest=manifest,
        )
    else:
        # 即使不輸出，仍要跑一次對照表建構，才能把「未收錄的臟器代碼」這類問題檢查出來
        _organ_codes_frame(config, decisions, result.table, warnings)

    return PipelineResult(
        output_path=output_path,
        consolidated=result.table,
        manifest=manifest,
        warnings=warnings,
        run_count=len(runs),
        sample_count=int((result.table["最終採用"] == "Y").sum()) if not result.table.empty else 0,
        manual_review_count=result.manual_review_count,
    )


def _load_decisions_safely(config: StudyConfig, warnings: list[str]) -> DecisionSet:
    try:
        decisions = load_decisions(config.path("decisions_file"))
    except Exception as exc:  # noqa: BLE001 - 決策表讀不到不該讓整個流程掛掉
        warnings.append(f"決策表讀取失敗（{exc}），本次以系統預設規則執行。")
        return DecisionSet()
    warnings.extend(decisions.warnings)
    return decisions


def _record_decisions(manifest: Manifest, decisions: DecisionSet) -> None:
    for (animal, organ, source, sample_name), value in decisions.final_use.items():
        target = f"{sample_name or animal + '_' + organ} @ {source}"
        manifest.decisions.append({
            "決策類型": "最終採用覆核", "對象": target,
            "內容": value["最終採用"], "覆核者": value["覆核者"],
            "覆核日期": value["覆核日期"], "理由": value.get("理由", ""),
        })
    for (animal, organ, source, sample_name), value in decisions.highsd_well.items():
        target = f"{sample_name or animal + '_' + organ} @ {source}"
        manifest.decisions.append({
            "決策類型": "HIGHSD單孔採用", "對象": target,
            "內容": value["採用方式"], "覆核者": value["覆核者"],
            "覆核日期": value["覆核日期"], "理由": value.get("理由", ""),
        })
    for animal, value in decisions.group_override.items():
        manifest.decisions.append({
            "決策類型": "動物分組指定", "對象": animal, "內容": value["組別"],
            "覆核者": value["覆核者"], "覆核日期": value["覆核日期"],
            "理由": value.get("理由", ""),
        })
    for code, value in decisions.organ_override.items():
        manifest.decisions.append({
            "決策類型": "臟器代碼補充", "對象": code,
            "內容": f"{value.get('en', '')} / {value.get('zh', '')}",
            "覆核者": value["覆核者"], "覆核日期": value["覆核日期"],
            "理由": value.get("理由", ""),
        })


def _curves_frame(curves: dict[str, StandardCurve], config: StudyConfig) -> pd.DataFrame:
    eff_low, eff_high = config.standard_curve.get("efficiency_range", [85.0, 115.0])
    min_r2 = config.standard_curve.get("min_r_squared", 0.98)
    rows: list[dict[str, Any]] = []
    for source_file in sorted(curves):
        curve = curves[source_file]
        rows.append({
            "來源檔案(Run)": source_file,
            "標準品有效孔位數": curve.n_points,
            "Slope": curve.slope,
            "Intercept": curve.intercept,
            "R²": curve.r_squared,
            "PCR效率(%)": curve.efficiency,
            f"R²允收(≥{min_r2})": "通過" if curve.r2_pass else "未通過",
            f"效率允收({eff_low}–{eff_high}%)": "通過" if curve.efficiency_pass else "未通過",
            f"LLOQ({curve.lloq_point}, pg)": curve.lloq,
            "整體判定": curve.verdict,
            "備註": "；".join(curve.notes),
        })
    return pd.DataFrame(rows)


def _qc_frame(qc_points) -> pd.DataFrame:
    rows = [{
        "來源檔案(Run)": point.source_file,
        "QC類型": point.qc_type,
        "樣品名稱": point.sample_name,
        "標稱濃度(pg)": point.nominal,
        "測得值(pg)": point.measured,
        "回收率(%)": point.recovery_percent,
        "判定": point.verdict,
        "備註": point.note,
    } for point in qc_points]
    return pd.DataFrame(rows)


def _organ_codes_frame(config: StudyConfig, decisions: DecisionSet,
                       consolidated: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
    seen = set(consolidated["臟器代碼"].dropna()) if not consolidated.empty else set()
    known = config.organ_codes
    rows: list[dict[str, Any]] = []

    for code in sorted(set(known) | set(decisions.organ_override) | seen):
        override = decisions.organ_override.get(code)
        if override:
            english, chinese, source = override.get("en", ""), override.get("zh", ""), (
                f"決策表補充（{override['覆核者']} / {override['覆核日期']}）"
            )
        elif code in known:
            english, chinese = known[code].get("en", ""), known[code].get("zh", "")
            source = "設定檔（官方上機編號對照表）"
        else:
            english = chinese = ""
            source = "尚未收錄"
            message = (
                f"臟器代碼 {code} 出現在 qPCR 資料中，但設定檔與決策表都沒有對應名稱。"
                "請在決策表「臟器代碼補充」分頁補上，或更新設定檔。"
            )
            if message not in warnings:
                warnings.append(message)

        rows.append({
            "代碼": code,
            "臟器名稱(英文)": english,
            "臟器名稱(中文)": chinese,
            "本次資料中是否出現": "是" if code in seen else "否",
            "資料來源": source,
        })
    return pd.DataFrame(rows)


def _animals_frame(consolidated: pd.DataFrame, reference) -> pd.DataFrame:
    if consolidated.empty:
        return pd.DataFrame()
    subset = consolidated[[
        "動物編號", "性別(Sex)", "採樣時間點(Time point)", "組別(Group)"
    ]].drop_duplicates().sort_values("動物編號")
    subset = subset.assign(
        **{
            "參考資料來源": subset["動物編號"].map(
                lambda a: "、".join(reference.animals[a].sources)
                if a in reference.animals else "(無參考資料)"
            ),
            "資料衝突": subset["動物編號"].map(
                lambda a: "；".join(reference.conflicts_of(a)) or ""
            ),
        }
    )
    return subset.reset_index(drop=True)
