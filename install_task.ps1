# 一键创建 Windows 任务计划程序，每天定时运行微软积分脚本
# 以管理员身份运行此脚本

$TaskName = "MicrosoftRewardsDaily"
$Time = "09:30"   # 每天运行时间，可修改
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = (Get-Command python).Source
$ScriptPath = Join-Path $ScriptDir "main.py"

# 如果走虚拟环境
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) { $PythonExe = $VenvPython }

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $ScriptPath -WorkingDirectory $ScriptDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# 若已存在则先删除
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已删除旧任务 $TaskName"
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "微软积分每日自动获取" -Force
Write-Host "已创建计划任务：$TaskName （每天 $Time 运行）"
Write-Host "脚本目录：$ScriptDir"
