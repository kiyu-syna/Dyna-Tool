$ErrorActionPreference = "Stop"

$desktopDir = Split-Path -Parent $PSScriptRoot
$projectDir = Split-Path -Parent $desktopDir
$python = Join-Path $projectDir ".venv\Scripts\python.exe"
$backendDist = Join-Path $desktopDir "build\backend"
$backendWork = Join-Path $desktopDir "build\pyinstaller"
$backendSpec = Join-Path $desktopDir "build"
$packageOutput = if ($env:DYNA_PACKAGE_OUTPUT) {
  [System.IO.Path]::GetFullPath($env:DYNA_PACKAGE_OUTPUT)
} else {
  Join-Path $desktopDir "release"
}

if (-not (Test-Path -LiteralPath $python)) {
  throw "Không tìm thấy Python virtual environment: $python"
}

Push-Location $projectDir
try {
  # Electron portable đã giải nén resources một lần, vì vậy backend dạng onedir
  # khởi động nhanh hơn việc lồng thêm một gói PyInstaller onefile.
  & $python -m PyInstaller --noconfirm --clean --onedir --name DynaBackend `
    --paths $projectDir --distpath $backendDist --workpath $backendWork `
    --specpath $backendSpec --collect-all playwright --collect-all rich `
    --exclude-module cv2 --exclude-module numpy --exclude-module tkinter `
    --exclude-module _tkinter --exclude-module customtkinter `
    "desktop_backend\api.py"
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller build thất bại." }
} finally {
  Pop-Location
}

Push-Location $desktopDir
try {
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "Frontend build thất bại." }
  npx electron-builder --win portable "--config.directories.output=$packageOutput"
  if ($LASTEXITCODE -ne 0) { throw "Electron packaging thất bại." }
} finally {
  Pop-Location
}

Write-Host "Đã tạo bản portable trong: $packageOutput"
