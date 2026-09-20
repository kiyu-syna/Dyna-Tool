@echo off
echo Dang tao Shortcut "Dyna Tool (Treo May)" ra man hinh Desktop...
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Dyna Tool (Treo May).lnk')); $s.TargetPath = '%~dp0Chay_Dyna_TreoMay.bat'; $s.IconLocation = '%~dp0desktop\src-tauri\icons\icon.ico'; $s.WorkingDirectory = '%~dp0'; $s.Save()"
echo =========================================================
echo   Da tao Shortcut tren man hinh Desktop thanh cong!
echo =========================================================
pause
