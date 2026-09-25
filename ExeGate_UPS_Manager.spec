# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['ExeGate_PowerExpert_Manager.py'], pathex=[], binaries=[], datas=[],
    hiddenimports=['PyQt5.sip', 'PyQt5.QtTest', 'serial.tools.list_ports_windows']
                  + collect_submodules('pysnmp.smi.mibs'),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'test', 'tests'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='ExeGate_PowerExpert_Manager', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, uac_admin=False, uac_uiaccess=False,
    version='version_info.txt',
)
