# WorkBuddy 任务监控 — 一键部署脚本（Windows）
#
# 作用：
#   1. 检查 Python 是否可用
#   2. 若尚无配置文件，从示例复制一份并提示你填写
#   3. 注册计划任务（登录时启动 + 每5分钟自愈守护）
#   4. 立即启动监控程序
#
# 用法（在解压后的项目目录里，右键「使用 PowerShell 运行」，或执行）：
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# 说明：脚本不需要管理员权限（计划任务注册到当前用户即可）。

$ErrorActionPreference = "Stop"
$taskName = "WorkBuddyTaskMonitor"
$baseDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$script   = Join-Path $baseDir "task_monitor.py"
$configEx = Join-Path $baseDir "task_monitor_config.example.json"
$config   = Join-Path $baseDir "task_monitor_config.json"

Write-Host "===== WorkBuddy 任务监控 部署 =====" -ForegroundColor Cyan
Write-Host "项目目录: $baseDir"

# ---- 1. 检查脚本存在 ----
if (-not (Test-Path $script)) {
    Write-Host "[错误] 找不到 task_monitor.py，请确认在本项目目录内运行。" -ForegroundColor Red
    exit 1
}

# ---- 2. 检查 Python ----
$python  = $null   # 有控制台版本，用于检测与自测
$pythonw = $null   # 无窗口版本，用于后台常驻
$candidates = @(
    "python.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
    "C:\Python313\python.exe",
    "C:\Python312\python.exe"
)
foreach ($c in $candidates) {
    try {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { $python = $cmd.Source; break }
    } catch {}
}
if (-not $python) {
    Write-Host "[错误] 未找到 python.exe。请先安装 Python 3.8+（安装时勾选 Add to PATH）。" -ForegroundColor Red
    Write-Host "       下载: https://www.python.org/downloads/"
    exit 1
}
# 由 python.exe 推导同目录的 pythonw.exe；没有则退回 python
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }

Write-Host "[OK] Python: $python"
try {
    $ver = & $python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
    Write-Host "[OK] 版本: $ver"
} catch {
    Write-Host "[警告] 无法获取 Python 版本，继续尝试部署。"
}

# ---- 3. 准备配置文件 ----
if (-not (Test-Path $config)) {
    if (-not (Test-Path $configEx)) {
        Write-Host "[错误] 缺少配置文件，且找不到示例 task_monitor_config.example.json。" -ForegroundColor Red
        exit 1
    }
    Copy-Item $configEx $config
    Write-Host ""
    Write-Host "[需要你操作] 已生成配置文件：" -ForegroundColor Yellow
    Write-Host "    $config"
    Write-Host "  请用记事本打开，至少填写以下两项后重新运行本脚本：" -ForegroundColor Yellow
    Write-Host "    - sendkey         : Server 酱 Turbo 的 SendKey（https://sct.ftqq.com 获取）"
    Write-Host "    - email.smtp_user / email.smtp_pass / email.mail_to （可选，邮件兜底）"
    Write-Host ""
    Write-Host "  提示：如果本机 WorkBuddy 数据库不在默认位置（~\.workbuddy\workbuddy.db），"
    Write-Host "        请同时填写 db_path 为你的实际路径。"
    Write-Host ""
    Start-Process notepad.exe $config
    Write-Host ""
    Read-Host "填好保存后，按回车退出，然后重新运行本脚本"
    exit 0
}

# 检查 sendkey 是否仍是占位符
$cfgText = Get-Content $config -Raw
if ($cfgText -match "YOUR_SERVERCHAN_SENDKEY") {
    Write-Host "[需要你操作] 配置里的 sendkey 还是占位符，请填写真实 SendKey 后保存。" -ForegroundColor Yellow
    Start-Process notepad.exe $config
    Write-Host ""
    Read-Host "填好保存后，按回车退出，然后重新运行本脚本"
    exit 0
}
Write-Host "[OK] 配置文件已就绪"

# ---- 4. 注册计划任务 ----
$action   = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`""
$trigLogon  = New-ScheduledTaskTrigger -AtLogOn
$trigRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
                -RepetitionInterval (New-TimeSpan -Minutes 5) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit (New-TimeSpan -Days 3650) -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "[OK] 已移除同名旧任务"
}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($trigLogon, $trigRepeat) `
    -Settings $settings -Description "监控 WorkBuddy 任务状态并推送通知（含进程感知与自愈守护）" | Out-Null
Write-Host "[OK] 计划任务已注册：$taskName（登录时启动 + 每5分钟自愈）"

# ---- 5. 启动 ----
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "[OK] 当前状态: $state"

# ---- 6. 桌面快捷方式（指向可视化面板，失败不影响部署）----
try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnkPath = Join-Path $desktop "任务监控面板.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortCut($lnkPath)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = "`"$(Join-Path $baseDir 'monitor_panel.py')`""
    $shortcut.WorkingDirectory = $baseDir
    $shortcut.IconLocation = "$pythonw,0"
    $shortcut.Description = "WorkBuddy 任务监控面板"
    $shortcut.Save()
    Write-Host "[OK] 已在桌面创建快捷方式：任务监控面板"
} catch {
    Write-Host "[提示] 桌面快捷方式创建失败（不影响使用），可直接双击 启动面板.bat / start_panel.bat"
}

Write-Host ""
Write-Host "===== 部署完成 =====" -ForegroundColor Green
Write-Host "打开面板：双击 启动面板.bat（或 start_panel.bat）、或桌面「任务监控面板」，可查看状态并一键开关监控"
Write-Host "验证：运行  python task_monitor.py --self-test   会读取任务并发一条测试通知"
Write-Host "日志：$baseDir\monitor.log"
Write-Host "提示：若本机 WorkBuddy 未安装或未运行，程序会自动待机，启动 WorkBuddy 后自动开始巡检。"
