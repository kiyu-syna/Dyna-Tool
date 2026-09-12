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

& npx.cmd tauri $Mode @TauriArgs
if ($LASTEXITCODE -ne 0) {
  throw "Tauri $Mode thất bại với mã $LASTEXITCODE."
}
