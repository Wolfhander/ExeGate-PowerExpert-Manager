# -*- mode: python ; coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────────────────────
#  ExeGate PowerExpert Manager — PyInstaller .spec
#  Сборка:  pyinstaller ExeGate_UPS_Manager.spec
# ─────────────────────────────────────────────────────────────────────────────

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Скрытые импорты: Qt-платформенные плагины и serial-бэкенды
hidden_imports = [
    'PyQt5.sip',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'serial.tools.list_ports',
    'serial.tools.list_ports_windows',
    'winreg',
]

# pyqtgraph — тянет много подмодулей, собираем все
hidden_imports += collect_submodules('pyqtgraph')

a = Analysis(
    ['ExeGate_PowerExpert_Manager.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Если появятся ресурсы (иконки, шрифты) — добавить сюда:
        # ('assets', 'assets'),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Убираем лишнее — уменьшает размер .exe на 20-40 %
        'tkinter',
        'unittest',
        'email',
        'html',
        'http',
        'xml',
        'pydoc',
        'doctest',
        'difflib',
        'ftplib',
        'getpass',
        'getopt',
        'imaplib',
        'mailbox',
        'mimetypes',
        'multiprocessing',
        'numpy.distutils',
        'test',
        'tests',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ExeGate_PowerExpert_Manager',     # имя итогового .exe
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                               # сжать UPX (если установлен)
    upx_exclude=[],
    runtime_tmpdir=None,

    # ── Важно: console=False убирает чёрное окно CMD при запуске ──
    console=False,

    # Манифест Windows: запуск без повышения прав (UAC)
    uac_admin=False,
    uac_uiaccess=False,

    # Встроенная иконка (раскомментировать и указать путь к .ico):
    # icon='assets/ups_icon.ico',

    # Информация о версии в свойствах файла
    version=None,

    # Одиночный .exe (onefile=True уже задан структурой a.scripts + a.binaries в EXE)
)
