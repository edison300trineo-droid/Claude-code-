# qPCR 統整 - 應用程式控制原則診斷
#
# 這支腳本只讀取系統狀態，不會修改任何設定。
# 用途：找出是哪一種原則封鎖了 numpy/pandas 的編譯檔，以及那個原則是不是公司下發的。
#
# 執行方式：在專案資料夾按 Shift+右鍵 -> 在此處開啟 PowerShell，然後貼上：
#     powershell -ExecutionPolicy Bypass -File "tools\診斷.ps1"

$ErrorActionPreference = 'SilentlyContinue'

function Section($title) {
    Write-Host ""
    Write-Host ("=" * 62)
    Write-Host "  $title"
    Write-Host ("=" * 62)
}

function Item($label, $value) {
    Write-Host ("  {0,-34} {1}" -f $label, $value)
}

Write-Host ""
Write-Host "qPCR 統整 - 應用程式控制原則診斷"
Write-Host "產生時間: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# --- 1. 這台電腦是不是公司控管 ---------------------------------------------
Section "1. 這台電腦是不是公司控管"

$cs = Get-CimInstance Win32_ComputerSystem
Item "電腦名稱" $cs.Name
Item "已加入網域" $(if ($cs.PartOfDomain) { "是 -> 網域: $($cs.Domain)" } else { "否（非網域機器）" })

$mdm = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Enrollments\*" |
       Where-Object { $_.EnrollmentState -eq 1 }
Item "已註冊 MDM/Intune" $(if ($mdm) { "是（受組織管理）" } else { "否" })

Write-Host ""
Write-Host "  判讀：以上任一為「是」，代表原則多半由公司下發，"
Write-Host "        你自己關不掉，需要走 IT 申請。"

# --- 2. Smart App Control（個人機常見，可自行關閉）-------------------------
Section "2. Smart App Control"

$sacKey = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" -Name VerifiedAndReputablePolicyState
if ($null -eq $sacKey) {
    Item "狀態" "查不到（此版本 Windows 可能沒有這個功能）"
} else {
    $sac = switch ($sacKey.VerifiedAndReputablePolicyState) {
        0 { "關閉" }
        1 { "開啟（強制）  <-- 很可能就是元凶" }
        2 { "評估模式" }
        default { "未知值: $($sacKey.VerifiedAndReputablePolicyState)" }
    }
    Item "狀態" $sac
}
Write-Host ""
Write-Host "  若為「開啟（強制）」：設定 -> 隱私權與安全性 -> Windows 安全性"
Write-Host "  -> 應用程式與瀏覽器控制 -> 智慧型應用程式控制，可改為關閉。"
Write-Host "  注意：關閉後無法再開啟，只能重灌 Windows 才會恢復。"

# --- 3. WDAC / Device Guard（公司政策常用）---------------------------------
Section "3. WDAC / Device Guard 程式碼完整性原則"

$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard
if ($null -eq $dg) {
    Item "狀態" "查不到 Device Guard 資訊"
} else {
    $enforce = switch ($dg.CodeIntegrityPolicyEnforcementStatus) {
        0 { "未啟用" }
        1 { "稽核模式（只記錄不封鎖）" }
        2 { "強制模式  <-- 會封鎖未簽章的 DLL" }
        default { "未知值: $($dg.CodeIntegrityPolicyEnforcementStatus)" }
    }
    Item "程式碼完整性原則" $enforce
    Item "使用者模式原則" $(switch ($dg.UsermodeCodeIntegrityPolicyEnforcementStatus) {
        0 { "未啟用" } 1 { "稽核模式" } 2 { "強制模式  <-- 這項會擋 Python 套件" }
        default { "未知" } })
}

# --- 4. AppLocker -----------------------------------------------------------
Section "4. AppLocker"

$appLocker = Get-AppLockerPolicy -Effective -Xml
if ($appLocker -and $appLocker -match '<FilePathRule|<FilePublisherRule|<FileHashRule') {
    Item "狀態" "有生效中的 AppLocker 規則"
    $dllRules = ([xml]$appLocker).AppLockerPolicy.RuleCollection |
                Where-Object { $_.Type -eq 'Dll' }
    Item "DLL 規則集" $(if ($dllRules) { "存在（EnforcementMode: $($dllRules.EnforcementMode)）" } else { "無" })
} else {
    Item "狀態" "沒有生效中的規則"
}

# --- 5. 實際的封鎖事件（最關鍵）--------------------------------------------
Section "5. 最近的程式碼完整性封鎖事件"

$log = "Microsoft-Windows-CodeIntegrity/Operational"
$events = Get-WinEvent -LogName $log -MaxEvents 40

if ($events) {
    # 3077 = 已封鎖；3076 = 稽核模式下的紀錄
    $blocks = $events | Where-Object { $_.Id -eq 3077 -or $_.Id -eq 3076 }
    if (-not $blocks) { $blocks = $events }

    $shown = 0
    foreach ($e in $blocks) {
        if ($shown -ge 5) { break }
        $kind = "其他"
        if ($e.Id -eq 3077) { $kind = "已封鎖" }
        elseif ($e.Id -eq 3076) { $kind = "稽核（未封鎖）" }

        Write-Host ""
        Item "時間" $e.TimeCreated
        Item "事件 ID" "$($e.Id)  ($kind)"

        $lines = $e.Message -split "`r?`n" |
                 Where-Object { $_ -match 'File Name|Policy Name|PolicyGUID|Process Name' }
        foreach ($line in $lines) { Write-Host "    $($line.Trim())" }
        $shown++
    }
    if ($shown -eq 0) { Item "狀態" "有事件但沒有封鎖紀錄" }
} else {
    Item "狀態" "讀不到事件記錄（多半需要系統管理員權限）"
    Write-Host ""
    Write-Host "  請以系統管理員身分開啟 PowerShell 再跑一次，這一節最關鍵。"
}

# --- 6. Python 位置 ---------------------------------------------------------
Section "6. Python 與套件位置"

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
Item "Python 執行檔" $(if ($py) { $py } else { "找不到" })

if ($py) {
    $sitePkg = & $py -c "import site; print(site.getsitepackages()[0])" 2>$null
    Item "套件安裝位置" $sitePkg
    $inAppData = $sitePkg -like "*\AppData\*"
    Item "是否在 AppData 下" $(if ($inAppData) { "是  <-- 應用程式控制原則常封鎖此區" } else { "否" })
    Item "Python 版本" (& $py -V 2>&1)
}

# --- 總結 -------------------------------------------------------------------
Section "接下來"

Write-Host "  請把整份輸出截圖或複製回報，我會據此判斷該走哪條路："
Write-Host ""
Write-Host "    - 若第 1 節顯示已加入網域/MDM  -> 公司政策，需 IT 協助或改走純 Python 版"
Write-Host "    - 若只有第 2 節的 Smart App Control 開啟 -> 你自己就能關（不可逆，需三思）"
Write-Host "    - 若第 3 節為強制模式          -> 公司 WDAC 政策，通常無法自行解除"
Write-Host ""
