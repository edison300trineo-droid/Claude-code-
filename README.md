# 案件／專案追蹤系統（Trifecta MedTek）

臨床前 CRO 的案件追蹤系統：GLP 研究、方法確效、生物分析、稽核、外包試驗支援、
BD 授權評估等案件的階段、里程碑、到期日與負責人追蹤。

- **後端**：Python 3.8+ 標準函式庫（`sqlite3` + `http.server`），**零第三方套件**
- **資料**：單一 SQLite 檔案（`data/cases.db`），多人同時存取
- **前端**：原生 HTML/CSS/JS，無 CDN、無框架，介面為實驗室紀錄簿式的清單排版
- **匯入／匯出**：可直接吃現有的 Excel 表格（表頭不必在第一列），
  匯出 Excel／CSV 接回既有試算表流程

---

> **不熟悉程式的使用者請先看 [安裝說明.md](安裝說明.md)** ——
> 從安裝 Python、下載程式到發網址給同仁，逐步圖文說明。

## 一、快速開始

```bash
python3 run.py                 # 啟動服務（預設 http://0.0.0.0:8765）
python3 run.py seed-demo       # 想先看看畫面，可寫入 7 筆示範資料
```

啟動後畫面會印出兩個網址：

```
本機開啟：http://127.0.0.1:8765/
同仁連線：http://192.168.x.x:8765/     ← 把這個網址發給同仁
```

同仁用瀏覽器開這個網址即可，**不需在自己電腦安裝任何東西**。

停止服務：`Ctrl + C`。

### Windows

1. 安裝 Python 3（python.org，安裝時勾選 *Add Python to PATH*）
2. 連按 `start.bat`（會自動開啟瀏覽器），或在資料夾執行 `python run.py --open`
3. 首次啟動時 Windows 防火牆會詢問，選「允許私人網路存取」，同仁才連得進來

---

## 二、日常操作

| 功能 | 操作方式 |
| --- | --- |
| 新增案件 | 左上「＋ 新增案件」 |
| 編輯 | 該列右側「編輯」，或**在該列上連點兩下** |
| 刪除 | 該列右側「刪除」（會再確認一次） |
| 搜尋 | 上方搜尋框，比對**案件編號／合約編號／研究編號／客戶名稱** |
| 篩選 | 狀態／類型／階段／負責人下拉選單，或勾選「只看未結案」「只看逾期」 |
| 排序 | 點欄位標題切換升冪／降冪 |
| 逾期提醒 | 逾期列標紅、7 日內到期標黃，頂端顯示逾期件數 |
| 到期摘要 | 上方「本週到期摘要」，可切換 7／14／30 天、列印或匯出 |
| 匯出 | 「Excel (.xlsx)」「CSV」，**匯出的是目前篩選後的清單** |
| 匯入 | 「匯入 Excel／CSV」，選檔後會先顯示預覽，確認才寫入 |
| 儲存快捷鍵 | 編輯視窗中 `Ctrl + Enter` 儲存、`Esc` 關閉 |

右上角的「操作者」請填自己的姓名——每一次新增／修改都會記錄是誰改的、
改了哪個欄位、舊值與新值（`case_history` 資料表），日後要追溯不必靠記憶。
這個姓名存在瀏覽器裡，只是免得每次重打；**案件資料一律存在伺服器的 SQLite**。

---

## 三、欄位定義

| 欄位 | 說明 |
| --- | --- |
| 案件編號 | 必填、**須以 `QT` 開頭**、不可重複（不分大小寫），例：`QT114001`。輸入 `qt114001` 會自動把前綴轉成大寫 |
| 合約編號 | 選填、**可重複**（一份合約涵蓋多個案件時填同一組編號），會自動建議已輸入過的合約編號 |
| 研究編號 | 選填、格式不限（例：`TMT-114-003`）。尚未立案時留空，簽約立案後再補 |
| 客戶名稱 | 自由輸入，會自動建議已輸入過的客戶 |
| 案件類型 | GLP 研究／藥理試驗／Pilot study／方法確效／生物分析／稽核／外包試驗支援／BD 授權評估 |
| 目前階段 | 計畫書撰寫 → 計畫書已核准 → 試驗執行中 → 檢體分析中 → QA 審查 → 報告草稿 → 客戶審閱 → 正式報告已發出 → 結案 |
| 下一個里程碑 | 文字，例：「計畫書送 QA 審查」 |
| 到期日 | `YYYY-MM-DD`，可留空 |
| 負責人 | 自由輸入，會自動建議已輸入過的人名 |
| 狀態 | 進行中／需留意／已延遲／已結案 |
| 備註 | 長文字 |

三組編號的關係：**案件編號（QT）是唯一識別**，一件案子一個號；合約編號與研究編號
可留空、日後補上。同一份合約下的多個案件填相同的合約編號，在搜尋框輸入該合約編號
即可把這些案件一次列出。

期限判定規則：狀態為「已結案」者不再計算逾期；逾期＝到期日早於今天；
7 天內（含今天）到期標示為即將到期。

> 案件編號規則一體適用：新增、編輯、CSV 匯入都必須是 `QT` 開頭，沒有例外。
> 若資料庫裡還有舊制編號，存檔時會被擋下，請一併改成對應的 QT 編號。

---

## 四、與既有 Excel 整合

### 匯入既有 Excel／CSV

**沒有現成表格？** 匯入視窗右上角有「下載空白範本」，或用命令列
`python3 run.py template`。範本第一個工作表是要填的表格（案件類型、目前階段、
狀態三欄有下拉選單），第二個工作表有逐欄說明與填寫範例；資料工作表刻意留空，
不會有人忘了刪範例列就整份匯進來。

**在網頁上匯入（建議）**：按工具列的「匯入 Excel／CSV」→ 選檔 →
畫面會顯示**預覽**（每一列會新增還是更新、哪幾列有問題、對應到哪些欄位）→
確認無誤再按「確認匯入」。預覽階段不會動到資料庫。

設計成能直接吃公司原本在用的表格：

- **表頭不必在第一列**：上方可以有標題列、製表人、空白列，系統會自己找
- **欄位順序不拘**，用不到的欄位（例如金額、備查欄）會列出來但不匯入
- **欄名支援常見寫法**：案件編號可寫成 `編號`、`案號`、`報價單號`；
  到期日可寫成 `期限`、`預計完成日`、`截止日`；負責人可寫成 `主持人`、
  `計畫主持人` 等
- **日期格式自動判讀**：Excel 日期格式、`2026/9/20`、`2026-09-20`、
  `20260920`、民國年 `115/1/15` 都可以
- **多個工作表**：預設由第一個往後找，取第一個有表頭的工作表（封面、說明頁
  會自動跳過），也可在預覽畫面手動切換
- **CSV 編碼不拘**：Excel 中文版的「CSV（逗號分隔）」是 Big5，「CSV UTF-8」
  是 UTF-8，兩種都讀得到；「Unicode 文字」（UTF-16 + Tab）也可以
- 缺少的欄位會帶預設值（類型＝GLP 研究、階段＝計畫書撰寫、狀態＝進行中）
- 案件編號已存在者會被**更新**（可取消勾選「已存在的案件編號一併更新」）
- 有問題的列會逐列列出原因與**檔案中的列號**，其餘照常匯入

命令列也可以，適合大批次或排程：

```bash
python3 run.py template                       # 產生空白範本
python3 run.py import 既有清單.xlsx --dry-run   # 只試算，不寫入
python3 run.py import 既有清單.xlsx             # 實際匯入
python3 run.py import 清單.xlsx --sheet 2026年度  # 指定工作表
```

> 舊版 `.xls` 不支援，請用 Excel 另存為 `.xlsx`。
> 匯入的案件編號必須以 `QT` 開頭。

### 匯出

網頁上的「Excel / CSV」按鈕匯出**目前篩選結果**；命令列則可匯出全部：

```bash
python3 run.py export 案件清單.xlsx
python3 run.py export 未結案.csv --open-only
```

匯出的 .xlsx 到期日是**真正的 Excel 日期格式**（可直接排序、做樞紐分析），
表頭已凍結並套用篩選器；CSV 為 UTF-8 BOM，Excel 開啟不會亂碼。

---

## 五、到期／逾期摘要

網頁上按「本週到期摘要」，或用命令列（適合排程寄信）：

```bash
python3 run.py report                      # 印在終端機
python3 run.py report --days 14            # 改成未來 14 天
python3 run.py report -o 週報.xlsx          # 輸出 .txt / .csv / .xlsx
```

摘要分三段：**已逾期**、**N 日內到期**、**未設定到期日**，並附各負責人的件數統計。

排程範例（每週一早上 8:00 產生週報；Linux/macOS `crontab -e`）：

```
0 8 * * 1 cd /path/to/case-tracker && python3 run.py report -o /path/to/週報_$(date +\%F).xlsx
```

---

## 六、部署與維運

### 常用參數

```bash
python3 run.py --port 9000                # 換埠號
python3 run.py --host 127.0.0.1           # 只給自己用，不開放內網
python3 run.py --db /srv/tmt/cases.db     # 指定資料庫位置
python3 run.py --verbose                  # 印出每筆 HTTP 請求
```

亦可用環境變數 `CASE_TRACKER_DB`、`CASE_TRACKER_HOST`、`CASE_TRACKER_PORT`。

### 讓服務長期執行（Linux systemd 範例）

```ini
# /etc/systemd/system/case-tracker.service
[Unit]
Description=TMT Case Tracker
After=network.target

[Service]
WorkingDirectory=/srv/case-tracker
ExecStart=/usr/bin/python3 /srv/case-tracker/run.py --db /srv/case-tracker/data/cases.db
Restart=always
User=tmt

[Install]
WantedBy=multi-user.target
```

### 備份

資料就是 `data/cases.db` 一個檔案。**服務執行中也可以安全備份**：

```bash
sqlite3 data/cases.db ".backup '/backup/cases_$(date +%F).db'"
```

（或停掉服務後直接複製 `cases.db`、`cases.db-wal`、`cases.db-shm` 三個檔案。）
建議每日排程備份，並保留至少 30 份。

### 多人同時使用

- SQLite 以 WAL 模式執行，多人讀取不互相阻塞，寫入以序列化方式處理
- 兩人同時編輯同一件時，**後存的人會收到提示**（「此案件已由 ○○○ 於 ○○ 更新，
  請重新載入後再編輯」），不會默默覆蓋對方的修改
- 清單每 5 分鐘自動重新整理一次，也可自行重新整理頁面

### 安全性

本工具設計為**公司內網使用**，沒有帳號密碼——凡連得到這個網址的人都能編輯。
以 20 人內部使用而言通常足夠，但請注意：

- 請勿把這個埠號對外開放到網際網路
- 若日後需要登入驗證或唯讀權限，可在 `case_tracker/server.py` 的 `_dispatch()`
  加上驗證，或前面掛一層 nginx basic auth
- 所有異動都有紀錄（誰、何時、改了什麼），可透過 `/api/activity` 查看

---

## 七、專案結構

```
run.py                      啟動與命令列工具（serve / report / export / import / seed-demo）
start.bat                   Windows 一鍵啟動
case_tracker/
  models.py                 欄位定義、選項清單、驗證、逾期判定
  db.py                     SQLite 資料層（WAL、樂觀鎖、異動紀錄）
  server.py                 HTTP 路由與 JSON API
  export.py                 CSV 與 XLSX 產生器（純標準函式庫）
  xlsx_reader.py            XLSX 讀取器（zipfile + XML，不需 openpyxl）
  report.py                 到期／逾期摘要
  importer.py               Excel／CSV 匯入：表頭偵測、欄名對應、預覽與寫入
  template.py               產生空白匯入範本（含下拉選單與填寫說明）
  static/                   前端頁面（index.html / style.css / app.js）
tests/test_tracker.py       單元測試與 API 測試
data/cases.db               資料庫（首次啟動自動建立，不納入版控）
```

### API（供日後串接其他系統）

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| GET | `/api/meta` | 選項清單、負責人／客戶清單、各狀態件數 |
| GET | `/api/cases` | 列出案件，支援 `q` `status` `case_type` `stage` `owner` `open_only` `overdue_only` `sort` `dir` |
| POST | `/api/cases` | 新增 |
| GET/PUT/DELETE | `/api/cases/{id}` | 讀取／更新（帶 `rev` 做衝突偵測）／刪除 |
| GET | `/api/cases/{id}/history` | 該案件異動紀錄 |
| GET | `/api/report/weekly?days=7` | 到期摘要 |
| GET | `/export/cases.xlsx` `/export/cases.csv` | 匯出（吃相同篩選參數） |
| GET | `/export/weekly.xlsx` `.csv` `.txt` | 匯出摘要 |
| GET | `/export/template.xlsx` | 下載空白匯入範本 |
| POST | `/api/import/preview?filename=` | 上傳檔案內容（原始位元組），回傳試算結果，不寫入 |
| POST | `/api/import/commit?filename=` | 同上但實際寫入，可加 `sheet=`、`update_existing=0` |

---

## 八、測試

```bash
python3 -m unittest discover -s tests -v
```

涵蓋欄位驗證、逾期判定、CRUD 與樂觀鎖、篩選與排序、CSV／XLSX 產出、
XLSX 讀取（共用字串、日期格式、跳列與空欄）、Excel／CSV 匯入與預覽、
資料庫升級路徑，以及 HTTP API 的成功與錯誤路徑。
