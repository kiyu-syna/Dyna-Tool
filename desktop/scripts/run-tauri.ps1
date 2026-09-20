param(
  [ValidateSet("dev", "build", "info")]
  [string]$Mode = "dev",
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$TauriArgs
)

$ErrorActionPreference = "Stop"
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $vswhere)) {
  throw "Không tìm thấy Visual Studio Build Tools (vswhere.exe)."
}

$vsPath = & $vswhere -latest -products * `
  -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
  -property installationPath
if (-not $vsPath) {
  throw "Hãy cài workload Visual Studio C++ Build Tools trước khi chạy Tauri."
}

$devShellModule = Join-Path $vsPath "Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Import-Module $devShellModule
Enter-VsDevShell -VsInstallPath $vsPath -SkipAutomaticLocation `
  -DevCmdArguments "-arch=x64 -host_arch=x64"

$toolsVersionFile = Join-Path $vsPath "VC\Auxiliary\Build\Microsoft.VCToolsVersion.default.txt"
$toolsVersion = (Get-Content -LiteralPath $toolsVersionFile -Raw).Trim()
$linker = Join-Path $vsPath "VC\Tools\MSVC\$toolsVersion\bin\Hostx64\x64\link.exe"
if (-not (Test-Path -LiteralPath $linker)) {
  throw "Không tìm thấy MSVC linker: $linker"
}

# Git for Windows also installs an unrelated link.exe and may appear first on PATH.
$env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = $linker
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
$env:Path = "$cargoBin;$env:Path"

$telegramProcess = $null

function Test-TelegramServer {
  try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
    return $status.status -eq "healthy"
  } catch {
    return $false
  }
}

if ($Mode -eq "dev" -and -not (Test-TelegramServer)) {
  $projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
  $python = Join-Path $projectRoot ".venv\Scripts\python.exe"
  $telegramDirectory = Join-Path $projectRoot "payment_server"
  $telegramEnvironment = Join-Path $telegramDirectory ".env"

  if (-not (Test-Path -LiteralPath $python)) {
    throw "Không tìm thấy Python để chạy Telegram: $python"
  }
  if (-not (Test-Path -LiteralPath $telegramEnvironment)) {
    throw "Không tìm thấy cấu hình Telegram: $telegramEnvironment"
  }

  $telegramProcess = Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $telegramDirectory `
    -WindowStyle Hidden `
    -PassThru

  for ($attempt = 0; $attempt -lt 40 -and -not (Test-TelegramServer); $attempt++) {
    Start-Sleep -Milliseconds 250
  }
  if (-not (Test-TelegramServer)) {
    Stop-Process -Id $telegramProcess.Id -Force -ErrorAction SilentlyContinue
    throw "Telegram Server không khởi động được trên cổng 8000."
  }
  Write-Host "Telegram Server đã sẵn sàng trên http://127.0.0.1:8000"
}

$desktopDir = Split-Path -Parent $PSScriptRoot
Push-Location $desktopDir
try {
  & npx.cmd tauri $Mode @TauriArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Tauri $Mode thất bại với mã $LASTEXITCODE."
  }
} finally {
  Pop-Location
  if ($telegramProcess -and -not $telegramProcess.HasExited) {
    Stop-Process -Id $telegramProcess.Id -Force -ErrorAction SilentlyContinue
  }
}
