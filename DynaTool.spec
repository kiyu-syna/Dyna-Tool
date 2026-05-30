# -*- mode: python ; coding: utf-8 -*-
import os
import customtkinter
from PyInstaller.utils.hooks import collect_all

# Lấy đường dẫn thực tế của customtkinter trên máy bạn
ctk_path = os.path.dirname(customtkinter.__file__)

datas = [('service_account.json', '.'), ('icon.ico', '.'), (ctk_path, 'customtkinter')]
import certifi
cert_path = certifi.where()
datas.append((cert_path, 'certifi'))

binaries = []
import playwright
playwright_path = os.path.dirname(playwright.__file__)
datas.append((os.path.join(playwright_path, 'driver'), 'playwright/driver'))
hiddenimports = ['main', 'bot_douyin', 'upload_tiktok', 'config', 'utils', 'sheets_manager', 'telegram_manager', 'browser_manager', 'shared_state', 'PIL']

# Thu thập dữ liệu từ customtkinter
tmp_ret = collect_all('customtkinter')
for d in tmp_ret[0]:
    if d not in datas: datas.append(d)
binaries += tmp_ret[1]
for h in tmp_ret[2]:
    if h not in hiddenimports: hiddenimports.append(h)

# Thu thập dữ liệu từ rich (để sửa lỗi thiếu unicode data)
tmp_ret_rich = collect_all('rich')
for d in tmp_ret_rich[0]:
    if d not in datas: datas.append(d)
binaries += tmp_ret_rich[1]
for h in tmp_ret_rich[2]:
    if h not in hiddenimports: hiddenimports.append(h)

a = Analysis(
    ['dyna_tool.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DynaTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    icon='icon.ico',
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
