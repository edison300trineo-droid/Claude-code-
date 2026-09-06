"""產生貼近 StepOnePlus 實際輸出格式的測試 fixture。

數值以 Ct = 25.4 - 3.35*log10(Quantity) 反推，這條線的斜率/截距取自
BD-TS-20260701 實際 run 的數量級（STD01 10000 pg -> Ct ~12，
0.0117 pg -> Ct ~32），因此 fixture 的 R2 與 PCR 效率都落在真實允收範圍內。
"""

from __future__ import annotations

import math
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

SLOPE = -3.35
INTERCEPT = 25.4
TARGET = "new Alu probe+ABI supermix"

# 實際為 STD01 起一路 5 倍序列稀釋。濃度刻意寫成儀器輸出的 float32 形式，
# 讓 fixture 能驗證容差比較確實吸收得掉這種尾差。
STD_CONC = {
    "STD01": 10000, "STD02": 2000, "STD03": 400, "STD04": 80,
    "STD05": 16, "STD06": 3.200000048, "STD07": 0.639999986,
    "STD08": 0.128000006,
}

HEADER = [
    "Well", "Sample Name", "Target Name", "Task", "Ct", "Ct Mean", "Ct SD",
    "Quantity", "Quantity Mean", "Quantity SD", "HIGHSD",
]
HEADER_NO_HIGHSD = HEADER[:-1]


def ct_of(q: float) -> float:
    return INTERCEPT + SLOPE * math.log10(q)


def quantity_of(ct: float) -> float:
    return 10 ** ((ct - INTERCEPT) / SLOPE)


class PlateBuilder:
    """依序配孔位，模擬儀器逐孔輸出。"""

    ROWS = "ABCDEFGH"

    def __init__(self) -> None:
        self.index = 0
        self.rows: list[list[str]] = []

    def _next_well(self) -> str:
        row = self.ROWS[self.index // 12]
        col = self.index % 12 + 1
        self.index += 1
        return f"{row}{col}"

    def add_replicates(self, name: str, task: str, cts: list[float],
                       highsd: str | None, quantity_from_curve: bool = True,
                       nominal: float | None = None) -> None:
        quantities = [quantity_of(c) for c in cts] if quantity_from_curve else []
        ct_mean = sum(cts) / len(cts)
        ct_sd = _sd(cts)
        if task == "STANDARD":
            # 標準品：Quantity 欄位放標稱濃度，Quantity Mean/SD 留空（同真實檔）
            for ct in cts:
                self.rows.append([
                    self._next_well(), name, TARGET, task, f"{ct:.8f}",
                    f"{ct_mean:.8f}", f"{ct_sd:.9f}", f"{nominal:g}", "", "",
                    highsd or "N",
                ])
            return
        q_mean = sum(quantities) / len(quantities)
        q_sd = _sd(quantities)
        for ct, q in zip(cts, quantities):
            self.rows.append([
                self._next_well(), name, TARGET, task, f"{ct:.8f}",
                f"{ct_mean:.8f}", f"{ct_sd:.9f}", f"{q:.9f}",
                f"{q_mean:.9f}", f"{q_sd:.9f}", highsd or "N",
            ])

    def add_ntc(self, ct: float | None) -> None:
        if ct is None:
            for _ in range(2):
                self.rows.append([
                    self._next_well(), "NTC", TARGET, "NTC", "Undetermined",
                    "", "", "", "", "", "N",
                ])
            return
        self.add_replicates("NTC", "NTC", [ct, ct + 0.02], "N")


def _sd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def write(path: Path, preamble: dict[str, str], rows: list[list[str]],
          include_highsd: bool) -> None:
    header = HEADER if include_highsd else HEADER_NO_HIGHSD
    lines = [f"{k}\t{v}" for k, v in preamble.items()]
    lines.append("")
    lines.append("\t".join(header))
    for row in rows:
        lines.append("\t".join(row if include_highsd else row[:-1]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_run(name: str, run_end: str, animals: list[tuple[str, float, str | None]],
              include_highsd: bool, ntc_ct: float | None) -> None:
    plate = PlateBuilder()
    for std, conc in STD_CONC.items():
        base = ct_of(conc)
        plate.add_replicates(std, "STANDARD", [base - 0.06, base + 0.06], "N",
                             nominal=conc)
    for sample_name, quantity, highsd in animals:
        base = ct_of(quantity)
        spread = 0.35 if highsd == "Y" else 0.05
        plate.add_replicates(sample_name, "UNKNOWN", [base - spread, base + spread], highsd)
    # 敏感度對照組（回收率判定唯一依據）
    for nominal, measured in (("200pg", 196.0), ("20pg", 21.4), ("2pg", 1.83)):
        base = ct_of(measured)
        plate.add_replicates(f"{nominal} sensitivity control", "UNKNOWN",
                             [base - 0.04, base + 0.04], "N")
    # 臟器基質 QC 對照組
    plate.add_replicates("27 mouse blood cell pellet DNA", "UNKNOWN",
                         [ct_of(0.1385), ct_of(0.1352)], "N")
    plate.add_ntc(ntc_ct)

    preamble = {
        "Block Type": "96fast Block",
        "Chemistry": "TAQMAN",
        "Experiment File Name": name,
        "Experiment Run End Time": run_end,
        "Instrument Type": "steponeplus",
    }
    write(FIXTURES / name, preamble, plate.rows, include_highsd)


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    # Run 01：含 HIGHSD 欄位，一筆 HIGHSD=Y 且可定量（需人工複核），
    #          一筆 HIGHSD=Y 但低於 LLOQ（屬預期現象）。
    build_run(
        "20260818_BD-TS-20260701_01_data.xls",
        "2026-08-18 15:00:52 PM CST",
        [
            ("1006_99", 0.018188, "N"),
            ("1007_99", 0.006368, "N"),
            ("0006_27", 0.154891, "N"),
            ("0007_27", 0.063769, "N"),
            ("1006_27", 0.445797, "N"),
            ("1007_27", 0.305597, "N"),
            ("0009_27", 0.394162, "N"),
            ("0010_27", 0.081641, "N"),
            ("1008_27", 0.245026, "N"),
            ("1009_27", 0.390061, "N"),
            ("1010_27", 0.446435, "N"),
            ("0008_27", 0.092230, "N"),
            ("1011_03", 2.480000, "Y"),   # 可定量 + HIGHSD，但 run 02 有 rerun -> 不需複核
            ("1012_03", 0.011700, "Y"),   # ND + HIGHSD，無 rerun -> 屬預期現象
            ("1013_22", 3.140000, "Y"),   # 可定量 + HIGHSD，無 rerun -> 請人工複核
            # 同一塊盤上的名稱標記 rerun：原始與 _re 必須各自成列
            ("1014_22", 1.820000, "Y"),
            ("1014_22_re", 1.960000, "N"),
        ],
        include_highsd=True,
        ntc_ct=33.9058,                    # 回推濃度遠低於 LLOQ -> 通過
    )
    # Run 02：整盤 HIGHSD 皆為 N，儀器因此未輸出該欄位；
    #          含 1011_03 的 rerun（同 key 出現在較晚的 run）。
    build_run(
        "20260824_BD-TS-20260701_07_data.xls",
        "2026-08-24 18:35:42 PM CST",
        [
            ("1011_03", 2.615000, None),
            ("0006_19", 0.097675, None),
            ("0006_21", 0.020112, None),
            ("0006_22", 0.010259, None),
            ("1031_27", 0.023516, None),
            ("1024_27", 0.012807, None),
        ],
        include_highsd=False,
        ntc_ct=None,                       # Undetermined
    )
    print(f"fixtures written to {FIXTURES}")


if __name__ == "__main__":
    main()
