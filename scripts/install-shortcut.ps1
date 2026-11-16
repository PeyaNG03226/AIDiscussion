<#
  创建 AIDiscussion 桌面快捷方式（可选开机自启）。

  用法：
    powershell -ExecutionPolicy Bypass -File scripts\install-shortcut.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install-shortcut.ps1 -Startup

  说明：
    - 优先指向无控制台的 aidiscussion-desktop.exe；
    - 该文件尚不存在时退回 pythonw.exe -m aidiscussion --desktop；
    - -OutputDir / -StartupDir 仅用于测试，分别指定快捷方式输出目录。
#>
param(
    [switch]$Startup,
    [string]$OutputDir = "",
    [string]$StartupDir = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$gui = Join-Path $root ".venv\Scripts\aidiscussion-desktop.exe"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"

if (Test-Path $gui) {
    $target = $gui
    $arguments = ""
} elseif (Test-Path $pythonw) {
    $target = $pythonw
    $arguments = "-m aidiscussion --desktop"
} else {
    Write-Error "未找到虚拟环境，请先在项目目录执行：uv sync --extra desktop"
    exit 1
}

$icon = Join-Path $root "assets\aidiscussion.ico"

function New-AppShortcut {
    param([string]$Path)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $root
    if (Test-Path $icon) {
        $shortcut.IconLocation = "$icon,0"
    }
    $shortcut.Description = "AIDiscussion - 和一群 AI 聊天"
    $shortcut.Save()
    Write-Host "已创建快捷方式：$Path"
}

$desktop = if ($OutputDir) { $OutputDir } else { [Environment]::GetFolderPath("Desktop") }
New-AppShortcut (Join-Path $desktop "AIDiscussion.lnk")

if ($Startup) {
    # 注意：PowerShell 变量名不区分大小写，这里不能复用 $startup
    $startupFolder = if ($StartupDir) { $StartupDir } else { [Environment]::GetFolderPath("Startup") }
    New-AppShortcut (Join-Path $startupFolder "AIDiscussion.lnk")
    Write-Host "已设置开机自启，登录 Windows 后悬浮球会自动出现。"
}
