$ErrorActionPreference = "Stop"

$desktopDir = Split-Path -Parent $PSScriptRoot
$projectDir = Split-Path -Parent $desktopDir
$python = Join-Path $projectDir ".venv\Scripts\python.exe"
$backendDist = Join-Path $desktopDir "build\backend"
$backendWork = Join-Path $desktopDir "build\pyinstaller"
$backendSpec = Join-Path $desktopDir "build"
$playwrightDriverSource = Join-Path $projectDir ".venv\Lib\site-packages\playwright\driver"
$playwrightDriverOutput = Join-Path $desktopDir "build\playwright-driver"
$packageOutput = if ($env:DYNA_PACKAGE_OUTPUT) {
  [System.IO.Path]::GetFullPath($env:DYNA_PACKAGE_OUTPUT)
} else {
  Join-Path $desktopDir "release"
}

if (-not (Test-Path -LiteralPath $python)) {
  throw "Không tìm thấy Python virtual environment: $python"
}
& $python -c "import edge_tts, faster_whisper, vieneu"
if ($LASTEXITCODE -ne 0) {
  throw "Chưa cài Video AI runtime. Hãy chạy: .\.venv\Scripts\python.exe -m pip install -r requirements-video-ai.txt"
}
if (-not (Test-Path -LiteralPath (Join-Path $playwrightDriverSource "node.exe"))) {
  throw "Không tìm thấy Playwright driver: $playwrightDriverSource"
}

Push-Location $projectDir
try {
  # Electron portable đã giải nén resources một lần, vì vậy backend dạng onedir
  # khởi động nhanh hơn việc lồng thêm một gói PyInstaller onefile.
  & $python -m PyInstaller --noconfirm --clean --onedir --name DynaBackend `
    --paths $projectDir --distpath $backendDist --workpath $backendWork `
    --specpath $backendSpec --collect-all playwright --collect-all rich `
    --collect-all faster_whisper --collect-all ctranslate2 --collect-all av `
    --collect-all tokenizers --collect-all huggingface_hub --collect-all onnxruntime `
    --collect-all edge_tts --collect-all vieneu --collect-all vieneu_utils `
    --collect-all sea_g2p --collect-all soundfile --collect-all soxr `
    --exclude-module cv2 --exclude-module tkinter `
    --exclude-module _tkinter --exclude-module customtkinter `
    "desktop_backend\api.py"
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller build thất bại." }
} finally {
  Pop-Location
}

if (Test-Path -LiteralPath $playwrightDriverOutput) {
  Remove-Item -LiteralPath $playwrightDriverOutput -Recurse -Force
}
Copy-Item -LiteralPath $playwrightDriverSource -Destination $playwrightDriverOutput -Recurse

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
