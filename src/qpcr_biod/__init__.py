"""Alu qPCR 生物分布數據統整 pipeline。

設計原則（三條，其餘規則都從這裡長出來）：

1. 原始 StepOnePlus export 是唯一真實來源，pipeline 只讀不寫。
2. 統整表是「產出物」，任何人都不該手改；重跑一次就會覆蓋。
3. 所有人工判斷（rerun 採用、HIGHSD 單孔採用、Day 29 分組…）寫在
   decisions.xlsx，帶覆核者與日期，pipeline 每次重跑都會套用。
"""

__version__ = "0.2.0"
