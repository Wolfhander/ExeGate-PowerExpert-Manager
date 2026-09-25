#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔═══════════════════════════════════════════════════════════════════╗
║       ExeGate PowerExpert UPS Manager  v1.1                       ║
║       Управление ИБП ExeGate PowerExpert TL-2000.72V              ║
║       Протокол: Megatec Q1 (RS232/USB)  |  Windows 11             ║
╚═══════════════════════════════════════════════════════════════════╝

Зависимости (установить через pip):
    pip install -r requirements.txt

Сборка Windows x64: build.bat (Python 3.14).
SNMP: PySNMP 7, SNMPv2c, UPS MIB RFC 1628.

Поддерживаемые интерфейсы:
    • RS232  — Megatec Q1 protocol (2400 baud, 8N1)
    • USB    — режим эмуляции COM (CP2102/CH340 адаптер)
    • SNMP   — чтение переменных по сети (опционально)
"""

import sys
import os
import json
import csv
import time
import threading
import subprocess
import platform
import logging
import copy
import math
import re
from ups_snmp import SNMP_AVAILABLE, snmp_get, read_ups
from tray_notifications import show_notification, remove_notification
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

# ─── winreg (Windows автозагрузка) ───────────────────────────────────────────
try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    WINREG_AVAILABLE = False   # не Windows / Wine без реестра

# ─── PyQt5 ───────────────────────────────────────────────────────────────────
try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGridLayout, QLabel, QPushButton, QComboBox, QSpinBox,
        QDoubleSpinBox, QCheckBox, QGroupBox, QTabWidget, QTableWidget,
        QTableWidgetItem, QHeaderView, QTextEdit, QLineEdit,
        QSystemTrayIcon, QMenu, QAction, QMessageBox, QFileDialog,
        QFrame, QSplitter, QProgressBar, QSlider, QScrollArea, QLayout,
        QSizePolicy, QDialog
    )
    from PyQt5.QtCore import (
        Qt, QTimer, QThread, pyqtSignal, QSettings, QSize, QPoint,
        QPropertyAnimation, QEasingCurve
    )
    from PyQt5.QtGui import (
        QFont, QColor, QPalette, QIcon, QPainter, QPen, QBrush,
        QRadialGradient, QLinearGradient, QPixmap, QFontDatabase,
        QPainterPath, QPolygonF
    )
    from PyQt5.QtCore import QPointF
    PYQT5_AVAILABLE = True
except ImportError:
    PYQT5_AVAILABLE = False
    print("ОШИБКА: PyQt5 не установлен.\nВыполните: pip install PyQt5")
    sys.exit(1)

# ─── pyserial ────────────────────────────────────────────────────────────────
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# ─── pyqtgraph ───────────────────────────────────────────────────────────────
try:
    import pyqtgraph as pg
    pg.setConfigOptions(antialias=True, background='#0D1117', foreground='#8B949E')
    PYQTGRAPH_AVAILABLE = True
except ImportError:
    PYQTGRAPH_AVAILABLE = False

# ═════════════════════════════════════════════════════════════════════════════
#  КОНСТАНТЫ И КОНФИГУРАЦИЯ
# ═════════════════════════════════════════════════════════════════════════════

APP_NAME    = "ExeGate PowerExpert Manager"
APP_VERSION = "1.1.0"
APP_AUTHOR  = "UPS Monitor"

# Параметры ExeGate PowerExpert TL-2000.72V
UPS_MODEL        = "ExeGate PowerExpert TL-2000.72V"
UPS_RATED_VA     = 2000
UPS_RATED_W      = 1800
UPS_BATTERY_V    = 72.0
UPS_INPUT_FREQ   = 50.0
UPS_INPUT_V_NOM  = 230.0
UPS_OUTPUT_V_NOM = 230.0

# Megatec Q1 протокол
MEGATEC_BAUD     = 2400
MEGATEC_DATABITS = 8
MEGATEC_STOPBITS = 1
MEGATEC_PARITY   = 'N'
MEGATEC_TIMEOUT  = 3.0

# OID-ы для SNMP (стандартный UPS MIB RFC-1628)
SNMP_OID_INPUT_VOLTAGE   = "1.3.6.1.2.1.33.1.3.3.1.3.1"
SNMP_OID_OUTPUT_VOLTAGE  = "1.3.6.1.2.1.33.1.4.4.1.2.1"
SNMP_OID_BATT_CHARGE     = "1.3.6.1.2.1.33.1.2.4.0"
SNMP_OID_LOAD_PERCENT    = "1.3.6.1.2.1.33.1.4.4.1.5.1"
SNMP_OID_BATT_RUNTIME    = "1.3.6.1.2.1.33.1.2.3.0"
SNMP_OID_ALARMS_PRESENT  = "1.3.6.1.2.1.33.1.6.1.0"

# Цвета темы
THEME = {
    "bg_dark":      "#0D1117",
    "bg_medium":    "#161B22",
    "bg_light":     "#21262D",
    "border":       "#30363D",
    "accent":       "#F78166",
    "accent2":      "#58A6FF",
    "accent3":      "#3FB950",
    "accent4":      "#E3B341",
    "text_primary": "#E6EDF3",
    "text_sec":     "#8B949E",
    "online":       "#3FB950",
    "battery":      "#E3B341",
    "fault":        "#F85149",
    "bypass":       "#D2A8FF",
}

# Конфиг по умолчанию
DEFAULT_CONFIG = {
    "connection": {
        "type": "serial",           # serial / snmp
        "port": "COM1",
        "baud": 2400,
        "timeout": 3.0,
        "poll_interval": 2000,      # мс
        "snmp_host": "192.168.1.100",
        "snmp_community": "public",
        "snmp_port": 161
    },
    "shutdown": {
        "enabled": False,
        "on_battery": True,
        "battery_low_percent": 30,
        "delay_minutes": 5,
        "warn_before_sec": 60,
        "command": "shutdown /s /t 0 /c \"ИБП: питание от батареи. Завершение работы.\"",

        # Новый триггер: завершение сразу при переходе на батарею
        "on_battery_switch": False,         # включить/выключить
        "on_battery_switch_delay_sec": 120  # задержка в секундах (0 = немедленно)
    },
    "alerts": {
        "battery_low": True,
        "utility_fail": True,
        "temp_high": True,
        "temp_threshold": 45.0,
        "sound": True,
        "popup": True,
        "log_file": True
    },
    "display": {
        "theme": "dark",
        "minimize_to_tray": True,
        "start_minimized": False,
        "show_in_taskbar": True
    },
    "test": {
        "auto_test_enabled": False,
        "auto_test_interval_days": 14,
        "last_test": ""
    }
}

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".exegate_ups_manager.json")
LOG_FILE    = os.path.join(os.path.expanduser("~"), "exegate_ups_events.csv")

# Ключ реестра Windows для автозагрузки текущего пользователя
_AUTORUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTORUN_NAME = APP_NAME


def _get_exe_path() -> str:
    """
    Вернуть путь к исполняемому файлу.
    • После сборки PyInstaller → путь к .exe
    • При запуске как .py            → путь к pythonw.exe + скрипт
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller: sys.executable = путь к .exe
        return f'"{sys.executable}"'
    else:
        # Обычный Python: запускаем через pythonw (без консольного окна)
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable          # fallback на python.exe
        script  = os.path.abspath(__file__)
        return f'"{pythonw}" "{script}"'


class AutostartManager:
    """Управление записью автозагрузки Windows через реестр HKCU\\Run."""

    @staticmethod
    def is_enabled() -> bool:
        """Проверить, есть ли запись в реестре."""
        if not WINREG_AVAILABLE:
            return False
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY,
                                 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, _AUTORUN_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    @staticmethod
    def enable(minimized: bool = True) -> tuple[bool, str]:
        """
        Добавить программу в автозагрузку.
        minimized=True → добавляет флаг --minimized для запуска свёрнутым.
        Возвращает (успех, сообщение).
        """
        if not WINREG_AVAILABLE:
            return False, "winreg недоступен (не Windows)"
        try:
            cmd = _get_exe_path()
            if minimized:
                cmd += " --minimized"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY,
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, _AUTORUN_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            return True, f"Добавлено в автозагрузку:\n{cmd}"
        except PermissionError:
            return False, "Нет прав на запись в реестр"
        except Exception as e:
            return False, f"Ошибка реестра: {e}"

    @staticmethod
    def disable() -> tuple[bool, str]:
        """Удалить запись из автозагрузки."""
        if not WINREG_AVAILABLE:
            return False, "winreg недоступен"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY,
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, _AUTORUN_NAME)
            winreg.CloseKey(key)
            return True, "Удалено из автозагрузки"
        except FileNotFoundError:
            return True, "Записи в автозагрузке не было"
        except Exception as e:
            return False, f"Ошибка реестра: {e}"

    @staticmethod
    def current_value() -> str:
        """Вернуть текущую команду из реестра или пустую строку."""
        if not WINREG_AVAILABLE:
            return ""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY,
                                 0, winreg.KEY_READ)
            val, _ = winreg.QueryValueEx(key, _AUTORUN_NAME)
            winreg.CloseKey(key)
            return val
        except Exception:
            return ""


# ═════════════════════════════════════════════════════════════════════════════
#  ОСВОБОЖДЕНИЕ ЗАНЯТОГО COM-ПОРТА
# ═════════════════════════════════════════════════════════════════════════════

class PortKiller:
    r"""
    Освобождение занятого COM-порта.

    Реальная причина PermissionError(13) — почти всегда системный драйвер
    (usbser.sys / serenum.sys) или служба Windows, а НЕ пользовательский
    процесс. Поэтому поиск через .Modules ничего не находит.

    Применяем три метода по нарастающей надёжности:
      1. Остановка UPS-служб Windows (sc stop).
      2. handle.exe (Sysinternals) — точный поиск + kill по дескриптору.
      3. PnP Device Restart — программный "вытащить/вставить" USB.
         Самый надёжный. Требует прав администратора.
    """

    @staticmethod
    def is_admin() -> bool:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    # ── Диагностика: кто держит порт ─────────────────────────────────────────
    @staticmethod
    def diagnose(port: str) -> dict:
        """
        Собрать диагностическую информацию о порте.
        Возвращает dict с полями: device_info, holding_processes, handle_found.
        """
        port_name = port.upper().strip()
        result = {
            "device_info":       [],
            "holding_processes": [],
            "handle_found":      False,
            "pnp_instance":      None,
        }

        # Информация об устройстве через PnP
        ps = f"""
$p = "{port_name}"
Get-PnpDevice | Where-Object {{
    $_.FriendlyName -match $p -or $_.Name -match $p
}} | Select-Object -First 3 | ForEach-Object {{
    Write-Output "PNP:$($_.InstanceId)|$($_.FriendlyName)|$($_.Status)"
}}
"""
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", ps],
                capture_output=True, text=True, timeout=10
            )
            for line in r.stdout.strip().splitlines():
                if line.startswith("PNP:"):
                    parts = line[4:].split("|")
                    if parts:
                        result["device_info"].append(
                            {"instance": parts[0],
                             "name":     parts[1] if len(parts) > 1 else "",
                             "status":   parts[2] if len(parts) > 2 else ""})
                        if result["pnp_instance"] is None:
                            result["pnp_instance"] = parts[0]
        except Exception:
            pass

        # handle.exe — точный поиск держателей
        handle_paths = [
            r"C:\Tools\handle.exe",
            r"C:\Sysinternals\handle.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "handle.exe"),
            "handle.exe",
        ]
        for hpath in handle_paths:
            try:
                r = subprocess.run(
                    [hpath, "-nobanner", "-a", r"\\.\ ".rstrip() + port_name],
                    capture_output=True, text=True, timeout=8,
                    creationflags=0x08000000   # CREATE_NO_WINDOW
                )
                for line in r.stdout.splitlines():
                    if port_name.lower() in line.lower() and "pid:" in line.lower():
                        result["holding_processes"].append(line.strip())
                        result["handle_found"] = True
                break
            except FileNotFoundError:
                continue
            except Exception:
                break

        return result

    # ── Метод 1: остановить службы ────────────────────────────────────────────
    @staticmethod
    def _stop_ups_services() -> list:
        stopped = []
        for svc in ["UPS", "apcupsd", "NUT", "PowerChute", "upsd", "Serial"]:
            try:
                r = subprocess.run(["sc", "query", svc],
                                   capture_output=True, text=True, timeout=4,
                                   creationflags=0x08000000)
                if "RUNNING" in r.stdout:
                    subprocess.run(["sc", "stop", svc],
                                   capture_output=True, timeout=6,
                                   creationflags=0x08000000)
                    stopped.append(svc)
            except Exception:
                pass
        return stopped

    # ── Метод 2: kill через handle.exe ───────────────────────────────────────
    @staticmethod
    def _kill_via_handle(port: str) -> list:
        port_name = port.upper().strip()
        killed = []
        handle_paths = [
            r"C:\Tools\handle.exe",
            r"C:\Sysinternals\handle.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "handle.exe"),
            "handle.exe",
        ]
        for hpath in handle_paths:
            try:
                r = subprocess.run(
                    [hpath, "-nobanner", "-a", r"\\.\ ".rstrip() + port_name],
                    capture_output=True, text=True, timeout=8,
                    creationflags=0x08000000
                )
                for line in r.stdout.splitlines():
                    if port_name.lower() in line.lower():
                        tokens = line.split()
                        pid = None
                        name = tokens[0] if tokens else "?"
                        for i, t in enumerate(tokens):
                            if t.lower() == "pid:" and i + 1 < len(tokens):
                                try:
                                    pid = int(tokens[i + 1].rstrip(":,"))
                                except ValueError:
                                    pass
                        if pid:
                            try:
                                subprocess.run(
                                    ["taskkill", "/PID", str(pid), "/F"],
                                    capture_output=True, timeout=5,
                                    creationflags=0x08000000
                                )
                                killed.append(f"{name} (PID {pid})")
                            except Exception:
                                pass
                return killed
            except FileNotFoundError:
                continue
            except Exception:
                break
        return killed

    # ── Метод 3: PnP Device Restart ───────────────────────────────────────────
    @staticmethod
    def pnp_restart(port: str, instance_id: str = "") -> tuple:
        """
        Перезапустить USB-устройство программно (Disable → Enable).
        Требует прав администратора.
        """
        port_name = port.upper().strip()

        if instance_id:
            id_clause = f'$dev = Get-PnpDevice -InstanceId "{instance_id}" -ErrorAction SilentlyContinue'
        else:
            id_clause = f"""$dev = Get-PnpDevice | Where-Object {{
    $_.FriendlyName -match '{port_name}' -or $_.Name -match '{port_name}'
}} | Select-Object -First 1"""

        # Ключевой момент: в catch выводим только HResult (число) и
        # короткий ASCII-тег — никакого $_.Exception.Message с кириллицей
        ps = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding            = [System.Text.Encoding]::UTF8
$ErrorActionPreference     = 'Stop'

{id_clause}

if (-not $dev) {{
    Write-Output "NOTFOUND:{port_name}"
    exit 0
}}

$name = $dev.FriendlyName
$iid  = $dev.InstanceId
Write-Output "FOUND:$name|$iid"

try {{
    Disable-PnpDevice -InstanceId $iid -Confirm:$false -ErrorAction Stop
    Write-Output "DISABLED:OK"
    Start-Sleep -Milliseconds 1000
    Enable-PnpDevice -InstanceId $iid -Confirm:$false -ErrorAction Stop
    Write-Output "ENABLED:OK"
    Start-Sleep -Milliseconds 1500
    Write-Output "DONE:OK"
}} catch {{
    $hr  = $_.Exception.HResult
    $src = $_.Exception.Source
    if ($hr -eq -2147024891 -or $hr -eq 5) {{
        Write-Output "NOADMIN:hr=$hr"
    }} elseif ($hr -eq -2147024809) {{
        Write-Output "NOTFOUND2:hr=$hr"
    }} else {{
        Write-Output "ERRCODE:hr=$hr,src=$src"
    }}
}}
"""
        log = []
        success = False
        found_instance = instance_id

        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass",
                 "-Command", ps],
                capture_output=True,
                timeout=30,
                creationflags=0x08000000   # CREATE_NO_WINDOW
            )
            # stdout декодируем: UTF-8 для наших тегов, кириллица в exception
            # намеренно не выводится — только числовые коды
            stdout = PortKiller._decode_ps(r.stdout)

            for line in stdout.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                tag, _, msg = line.partition(":")
                if tag == "NOTFOUND":
                    log.append(f"Устройство {msg} не найдено в Диспетчере устройств")
                elif tag == "NOTFOUND2":
                    log.append(f"Устройство не найдено ({msg})")
                elif tag == "FOUND":
                    parts = msg.split("|")
                    name = parts[0] if parts else msg
                    if len(parts) > 1:
                        found_instance = parts[1]
                    log.append(f"Устройство: {name}")
                    log.append(f"InstanceId: {found_instance}")
                elif tag == "DISABLED":
                    log.append("Устройство отключено (Disable-PnpDevice) ✓")
                elif tag == "ENABLED":
                    log.append("Устройство включено (Enable-PnpDevice) ✓")
                    success = True
                elif tag == "DONE":
                    log.append("PnP Device Restart выполнен успешно ✓")
                elif tag == "NOADMIN":
                    log.append(
                        "Ошибка: недостаточно прав для Disable-PnpDevice.\n"
                        "→ Закройте программу и запустите её заново:\n"
                        "  ПКМ на EXE → «Запуск от имени администратора»"
                    )
                elif tag == "ERRCODE":
                    log.append(
                        f"PnP-операция завершилась с ошибкой ({msg}).\n"
                        "Возможные действия:\n"
                        "  • Диспетчер устройств → COM-порты → ПКМ → Отключить → Включить\n"
                        "  • Физически переподключите USB-кабель ИБП"
                    )
                else:
                    log.append(line)

        except subprocess.TimeoutExpired:
            log.append("Превышено время ожидания PowerShell (30 сек)")
        except FileNotFoundError:
            log.append("powershell.exe не найден в PATH")
        except Exception as e:
            log.append(f"Исключение: {e}")

        return success, "\n".join(log)

    @staticmethod
    def _decode_ps(raw: bytes) -> str:
        """Декодировать вывод PowerShell/pnputil: UTF-8 → cp866 → cp1251 → latin-1."""
        if not raw:
            return ""
        for enc in ("utf-8-sig", "utf-8", "cp866", "cp1251", "latin-1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("latin-1", errors="replace")

    # ── cfgmgr32: прямой вызов Windows API через ctypes ─────────────────────
    @staticmethod
    def _cfgmgr_restart(instance_id: str) -> tuple:
        """
        Перезапуск устройства через cfgmgr32.dll (низкоуровневый API Windows).
        Не зависит от PowerShell и pnputil — работает напрямую.
        Шаги: CM_Locate_DevNode → CM_Query_And_Remove_SubTree → CM_Setup_DevNode
        """
        log = []
        try:
            import ctypes
            cfgmgr = ctypes.WinDLL("CfgMgr32", use_last_error=True)

            DEVINST     = ctypes.c_uint32
            CR_SUCCESS  = 0x00
            CM_LOCATE_DEVNODE_NORMAL     = 0x00000000
            CM_LOCATE_DEVNODE_PHANTOM    = 0x00000001
            CM_REMOVE_UI_NOT_OK          = 0x00000001
            CM_SETUP_DEVNODE_READY       = 0x00000000
            CM_REENUMERATE_SYNCHRONOUS   = 0x00000001
            CM_REENUMERATE_RETRY_INSTALLATION = 0x00000002

            devinst = DEVINST(0)

            # Найти DevNode по InstanceId
            cr = cfgmgr.CM_Locate_DevNodeW(
                ctypes.byref(devinst),
                ctypes.c_wchar_p(instance_id),
                CM_LOCATE_DEVNODE_NORMAL
            )
            if cr != CR_SUCCESS:
                # Попробовать phantom (устройство может быть не полностью активным)
                cr = cfgmgr.CM_Locate_DevNodeW(
                    ctypes.byref(devinst),
                    ctypes.c_wchar_p(instance_id),
                    CM_LOCATE_DEVNODE_PHANTOM
                )
            if cr != CR_SUCCESS:
                return False, f"CM_Locate_DevNodeW: CR={cr:#x}"
            log.append(f"CM_Locate_DevNodeW: OK (devinst={devinst.value}) ✓")

            # Сначала попробуем мягкий перезапуск через CM_Reenumerate_DevNode
            cr_re = cfgmgr.CM_Reenumerate_DevNode(
                devinst,
                ctypes.c_uint32(CM_REENUMERATE_SYNCHRONOUS |
                                CM_REENUMERATE_RETRY_INSTALLATION)
            )
            if cr_re == CR_SUCCESS:
                log.append("CM_Reenumerate_DevNode: OK ✓")
                time.sleep(1.5)
                return True, "\n".join(log)
            else:
                log.append(f"CM_Reenumerate_DevNode: CR={cr_re:#x} (продолжаем...)")

            # Жёсткий перезапуск: удалить из дерева устройств и переустановить
            cr_rm = cfgmgr.CM_Query_And_Remove_SubTreeW(
                devinst,
                None,   # pVetoType
                None,   # pszVetoName
                0,      # ulNameLength
                CM_REMOVE_UI_NOT_OK
            )
            if cr_rm == CR_SUCCESS:
                log.append("CM_Query_And_Remove_SubTree: OK ✓")
                time.sleep(1.0)
            else:
                log.append(f"CM_Query_And_Remove_SubTree: CR={cr_rm:#x}")

            # Переустановить (Setup)
            cr_setup = cfgmgr.CM_Setup_DevNode(
                devinst,
                ctypes.c_uint32(CM_SETUP_DEVNODE_READY)
            )
            if cr_setup == CR_SUCCESS:
                log.append("CM_Setup_DevNode: OK ✓")
                time.sleep(1.5)
                return True, "\n".join(log)
            else:
                log.append(f"CM_Setup_DevNode: CR={cr_setup:#x}")

            return False, "\n".join(log)

        except OSError as e:
            return False, f"cfgmgr32 недоступен: {e}"
        except Exception as e:
            return False, f"cfgmgr32 исключение: {e}"

    @staticmethod
    def _clear_pending_reboot(instance_id: str) -> tuple:
        """
        Очистить флаг «ожидание перезагрузки» для устройства в реестре.
        Именно он вызывает ERROR_NOT_SUPPORTED (50) в pnputil.
        Ключ: HKLM\\SYSTEM\\CurrentControlSet\\Enum\\<instanceId>
        Значение ConfigFlags — бит 0x400 = CONFIGFLAG_REINSTALL
        """
        if not WINREG_AVAILABLE:
            return False, "winreg недоступен"
        try:
            # InstanceId содержит '\', превращаем в путь реестра
            # USB\VID_1A86&PID_7523\5&28A11E14&0&5 → Enum\USB\VID_1A86&PID_7523\5&28A11E14&0&5
            reg_path = r"SYSTEM\CurrentControlSet\Enum\\" + instance_id.replace("\\", "\\")
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, reg_path,
                0, winreg.KEY_READ | winreg.KEY_SET_VALUE
            )
            try:
                cfg_flags, reg_type = winreg.QueryValueEx(key, "ConfigFlags")
                original = cfg_flags

                # Очищаем все "pending" биты:
                # 0x0001 = CONFIGFLAG_DISABLED
                # 0x0020 = CONFIGFLAG_REMOVED
                # 0x0400 = CONFIGFLAG_REINSTALL
                # 0x0800 = CONFIGFLAG_FAILEDINSTALL
                PENDING_BITS = 0x0400 | 0x0020
                new_flags = cfg_flags & ~PENDING_BITS

                if new_flags != cfg_flags:
                    winreg.SetValueEx(key, "ConfigFlags", 0, winreg.REG_DWORD, new_flags)
                    return (True,
                            f"ConfigFlags: {original:#010x} → {new_flags:#010x} ✓\n"
                            "(флаг ожидания перезагрузки снят)")
                else:
                    return (False,
                            f"ConfigFlags={cfg_flags:#010x}: pending-битов не обнаружено")
            finally:
                winreg.CloseKey(key)
        except PermissionError:
            return False, "Нет прав на запись в HKLM (нужен администратор)"
        except FileNotFoundError:
            return False, f"Ключ реестра не найден для {instance_id}"
        except Exception as e:
            return False, f"Реестр: {e}"

    @staticmethod
    def _pnputil_restart_device(instance_id: str) -> tuple:
        """pnputil /restart-device — без disable, просто перезапуск (Win10 1809+)."""
        try:
            r = subprocess.run(
                ["pnputil", "/restart-device", instance_id],
                capture_output=True, timeout=15,
                creationflags=0x08000000
            )
            out = (PortKiller._decode_ps(r.stdout) +
                   PortKiller._decode_ps(r.stderr)).strip()
            # Оставляем только ASCII из вывода (обходим кодировку)
            out_safe = out.encode("ascii", errors="replace").decode("ascii")
            if r.returncode == 0:
                return True, f"pnputil /restart-device: OK ✓\n{out_safe}"
            else:
                return False, f"pnputil /restart-device: код {r.returncode}\n{out_safe}"
        except FileNotFoundError:
            return False, "pnputil.exe не найден"
        except Exception as e:
            return False, f"pnputil: {e}"

    # ── Главный метод ─────────────────────────────────────────────────────────
    @staticmethod
    def free_port(port: str) -> tuple:
        """
        Освободить занятый COM-порт.
        Возвращает (успех: bool, подробный_лог: str).
        """
        port_name = port.upper().strip()
        log = []

        # 1. Остановить UPS-службы
        stopped = PortKiller._stop_ups_services()
        if stopped:
            log.append(f"Остановлены службы: {', '.join(stopped)}")

        # 2. handle.exe
        killed = PortKiller._kill_via_handle(port_name)
        if killed:
            log.append(f"Завершены процессы: {', '.join(killed)}")
            time.sleep(0.8)

        # 3. Порт уже свободен?
        if PortKiller._try_open(port_name):
            log.append(f"Порт {port_name} свободен ✓")
            return True, "\n".join(log)

        if not PortKiller.is_admin():
            log.append(
                f"Порт {port_name} занят системным драйвером.\n"
                "Нужны права администратора для следующих шагов.\n"
                "→ ПКМ на EXE → «Запуск от имени администратора»"
            )
            return False, "\n".join(log)

        # 4. Получить InstanceId
        log.append(f"Поиск InstanceId для {port_name}...")
        instance_id = PortKiller._get_instance_id(port_name)
        if not instance_id:
            log.append("InstanceId не найден.")
        else:
            log.append(f"InstanceId: {instance_id}")

            # 4a. Очистить флаг pending reboot из реестра
            ok_reg, reg_log = PortKiller._clear_pending_reboot(instance_id)
            log.append(f"Реестр (pending-флаг): {reg_log}")
            if ok_reg:
                time.sleep(0.5)

            # 4b. cfgmgr32 — прямой API (самый надёжный, не через PS)
            log.append("cfgmgr32 API (CM_Reenumerate / CM_Setup)...")
            ok_cfg, cfg_log = PortKiller._cfgmgr_restart(instance_id)
            log.append(cfg_log)
            if ok_cfg:
                time.sleep(1.0)
                if PortKiller._try_open(port_name):
                    log.append(f"Порт {port_name} успешно освобождён ✓")
                    return True, "\n".join(log)
                log.append("Порт занят — пробуем следующий метод...")

            # 4c. pnputil /restart-device (без disable/enable)
            log.append("pnputil /restart-device...")
            ok_pnp, pnp_log = PortKiller._pnputil_restart_device(instance_id)
            log.append(pnp_log)
            if ok_pnp:
                time.sleep(1.5)
                if PortKiller._try_open(port_name):
                    log.append(f"Порт {port_name} успешно освобождён ✓")
                    return True, "\n".join(log)
                log.append("Порт занят — пробуем следующий метод...")

            # 4d. pnputil /disable + /enable (если pending-флаг уже снят)
            log.append("pnputil /disable-device → /enable-device...")
            try:
                r1 = subprocess.run(
                    ["pnputil", "/disable-device", instance_id],
                    capture_output=True, timeout=15, creationflags=0x08000000)
                time.sleep(1.0)
                r2 = subprocess.run(
                    ["pnputil", "/enable-device", instance_id],
                    capture_output=True, timeout=15, creationflags=0x08000000)
                if r1.returncode == 0 and r2.returncode == 0:
                    log.append("pnputil disable/enable: OK ✓")
                    time.sleep(1.5)
                    if PortKiller._try_open(port_name):
                        log.append(f"Порт {port_name} успешно освобождён ✓")
                        return True, "\n".join(log)
                else:
                    log.append(f"pnputil: disable={r1.returncode}, enable={r2.returncode}")
            except Exception as e:
                log.append(f"pnputil disable/enable: {e}")

        log.append(
            "\nАвтоматическое освобождение не удалось.\n"
            "Ручные действия (по эффективности):\n"
            "  1. Диспетчер устройств → Порты (COM и LPT) →\n"
            "     ПКМ «USB-SERIAL CH340 (COM3)» → Отключить устройство → Включить\n"
            "  2. Физически переподключите USB-кабель ИБП\n"
            "  3. Перезагрузите компьютер\n"
            "  4. Диспетчер устройств → Вид → Показать скрытые устройства →\n"
            "     Удалить старый COM3 → Обновить конфигурацию оборудования"
        )
        return False, "\n".join(log)

    @staticmethod
    def _try_open(port: str) -> bool:
        """Попробовать открыть порт — вернуть True если успешно."""
        if not SERIAL_AVAILABLE:
            return False
        try:
            s = serial.Serial(port, timeout=0.1)
            s.close()
            return True
        except Exception:
            return False


def _fmt_sec(seconds: int) -> str:
    """Форматировать секунды в читаемый вид: 'X мин Y сек' или 'X сек'."""
    s = max(0, int(seconds))
    if s >= 60:
        m, r = divmod(s, 60)
        return f"{m} мин {r:02d} сек" if r else f"{m} мин"
    return f"{s} сек"


# ═════════════════════════════════════════════════════════════════════════════
#  МОДЕЛИ ДАННЫХ
# ═════════════════════════════════════════════════════════════════════════════

class UPSStatus:
    """Текущее состояние ИБП — результат парсинга Q1-ответа."""

    def __init__(self):
        self.timestamp        = datetime.now()
        self.connected        = False

        # Напряжения / частота
        self.input_voltage    = 0.0     # В
        self.input_fault_v    = 0.0     # В (напряжение во время сбоя)
        self.output_voltage   = 0.0     # В
        self.output_load_pct  = 0       # %
        self.input_freq       = 0.0     # Гц
        self.battery_voltage  = 0.0     # В (суммарное)
        self.temperature      = 0.0     # °C

        # Расчётные
        self.battery_charge      = 0    # % (вычисляется из напряжения)
        self.output_watts        = 0    # Вт
        self.runtime_min         = 0    # мин (оценка)
        # Напряжение батареи: raw = как пришло из Q1 (может быть на ячейку или суммарное)
        self.battery_voltage_raw = 0.0  # В (как пришло из Q1)
        self.battery_cells       = 36   # ячеек (72В / 2В = 36; уточняется из F-команды)

        # Биты состояния
        self.utility_fail     = False   # bit7: питание от батареи
        self.battery_low      = False   # bit6: батарея разряжена
        self.bypass_active    = False   # bit5: AVR/Bypass
        self.ups_failed       = False   # bit4: неисправность ИБП
        self.is_standby_type  = False   # bit3: 1=standby, 0=on-line
        self.test_in_progress = False   # bit2: идёт тест
        self.shutdown_active  = False   # bit1: активно отключение
        self.beeper_on        = False   # bit0: пищалка включена

        # Рейтинговые данные (команда F)
        self.rated_voltage    = UPS_INPUT_V_NOM
        self.rated_current    = UPS_RATED_VA / UPS_INPUT_V_NOM
        self.rated_batt_v     = UPS_BATTERY_V
        self.rated_freq       = UPS_INPUT_FREQ

        # Информация об ИБП (команда I)
        self.company          = "ExeGate"
        self.model            = "PowerExpert TL-2000"
        self.firmware         = "unknown"

    def mode_string(self) -> str:
        if not self.connected:
            return "Нет связи"
        if self.ups_failed:
            return "НЕИСПРАВНОСТЬ"
        if self.utility_fail:
            return "БАТАРЕЯ"
        if self.bypass_active:
            return "AVR/BYPASS"
        return "СЕТЕВОЕ ПИТАНИЕ"

    def mode_color(self) -> str:
        if not self.connected:
            return THEME["text_sec"]
        if self.ups_failed:
            return THEME["fault"]
        if self.utility_fail:
            return THEME["battery"]
        if self.bypass_active:
            return THEME["bypass"]
        return THEME["online"]

    # ── Автодетект формата напряжения батареи ───────────────────────────────
    def battery_format(self) -> str:
        """
        Определяет формат напряжения батареи из Q1-ответа.

        Megatec Q1 не даёт явного указания формата — приходится определять
        по диапазону значения:

          v <  5 В  → «per_cell_2v»   : напряжение 2В свинцовой ячейки (~1.75–2.27 В)
          5 ≤ v < 20 В → «per_module_12v»: напряжение одного 12В модуля (~10.5–13.8 В)
                         Самый распространённый формат у китайских OEM (включая ExeGate)
         v ≥ 20 В  → «pack_total»    : суммарное напряжение пакета (например 72–82 В)

        Примеры для ExeGate TL-2000 (72 В = 6 × 12В):
          12.7 В → per_module_12v → 6 модулей × 12.7 = 76.2 В суммарно → ~72% заряда
          2.12 В → per_cell_2v   → 36 ячеек × 2.12 = 76.3 В суммарно → ~71% заряда
          76.2 В → pack_total    → напрямую                            → ~72% заряда
        """
        v = self.battery_voltage
        if v <= 0:
            return "unknown"
        if v < 5.0:
            return "per_cell_2v"
        if v < 20.0:
            return "per_module_12v"
        return "pack_total"

    def battery_charge_pct(self) -> int:
        """
        Оценка заряда по напряжению батареи.

        Пороги для свинцово-кислотной батареи:
          per_cell_2v:     1.75 В/яч = 0%,  2.27 В/яч = 100%
          per_module_12v: 10.50 В/мод = 0%, 13.50 В/мод = 100%
          pack_total:     v_min=modules×10.5, v_max=modules×13.5
        """
        v = self.battery_voltage
        if v <= 0:
            return 0
        fmt = self.battery_format()
        if fmt == "per_cell_2v":
            v_min, v_max = 1.75, 2.27
        elif fmt == "per_module_12v":
            # 12В герметичный свинцово-кислотный аккумулятор:
            #   разряжен  ~10.5 В  (граница отсечки)
            #   номинал   ~12.0 В
            #   заряжен   ~13.5 В  (Float-напряжение зарядного устройства)
            v_min, v_max = 10.50, 13.50
        else:
            # Суммарное напряжение пакета — масштабируем от 12В эталона
            modules = max(1, round(self.rated_batt_v / 12.0))
            v_min = modules * 10.50
            v_max = modules * 13.50

        pct = (v - v_min) / (v_max - v_min) * 100.0
        return max(0, min(100, int(pct)))

    @property
    def battery_voltage_total(self) -> float:
        """Суммарное напряжение батарейного пакета (для отображения)."""
        v = self.battery_voltage
        if v <= 0:
            return 0.0
        fmt = self.battery_format()
        if fmt == "per_cell_2v":
            return round(v * self.battery_cells, 1)       # ячейки × В/яч
        if fmt == "per_module_12v":
            mods = max(1, round(self.rated_batt_v / 12.0))
            return round(v * mods, 1)                      # модули × В/мод
        return round(v, 1)                                 # уже суммарное

    @property
    def battery_voltage_per_cell(self) -> float:
        """Напряжение на одну 2В ячейку (для отображения)."""
        v = self.battery_voltage
        if v <= 0:
            return 0.0
        fmt = self.battery_format()
        if fmt == "per_cell_2v":
            return round(v, 3)
        if fmt == "per_module_12v":
            return round(v / 6.0, 3)   # 12В модуль = 6 × 2В ячеек
        cells = max(1, self.battery_cells)
        return round(v / cells, 3)

    @property
    def battery_format_label(self) -> str:
        """Человекочитаемое описание формата для журнала/tooltip."""
        fmt = self.battery_format()
        v   = self.battery_voltage
        if fmt == "per_cell_2v":
            return f"В/ячейку ({v:.3f} В × {self.battery_cells} яч.)"
        if fmt == "per_module_12v":
            mods = max(1, round(self.rated_batt_v / 12.0))
            return f"В/модуль ({v:.2f} В × {mods} акк.)"
        return f"суммарно ({v:.1f} В)"

    def estimated_runtime(self) -> int:
        """Упрощённая оценка времени автономной работы (минуты)."""
        charge_pct = self.battery_charge_pct()
        load_pct   = max(1, self.output_load_pct)
        # 2000VA UPS с 72В / 36 Ач батареей: ~30 мин при 100% нагрузке
        base_min   = 30
        return int(base_min * (charge_pct / 100.0) * (100.0 / load_pct))


class EventRecord:
    """Запись журнала событий."""
    def __init__(self, level: str, message: str, data: dict = None):
        self.timestamp = datetime.now()
        self.level     = level     # INFO / WARNING / CRITICAL / ACTION
        self.message   = message
        self.data      = data or {}


# ═════════════════════════════════════════════════════════════════════════════
#  MEGATEC Q1 ПРОТОКОЛ — ПАРСЕР
# ═════════════════════════════════════════════════════════════════════════════

class MegatecParser:
    """Парсинг ответов Megatec Q1 протокола."""

    @staticmethod
    def parse_q1(response: str, status: UPSStatus) -> bool:
        """
        Формат: (MMM.M NNN.N PPP.P QQQ RR.R SS.SS TT.T b7b6b5b4b3b2b1b0
        Пример: (230.0 230.0 230.0 025 50.0 72.00 28.0 00000000
        """
        try:
            line = response.strip()
            if not line.startswith('('):
                return False
            parts = line[1:].split()
            if len(parts) < 8:
                return False

            values = [float(value) for value in parts[:7]]
            if not all(math.isfinite(value) for value in values):
                return False
            if len(parts[7]) != 8 or set(parts[7]) - {"0", "1"}:
                return False
            status.input_voltage   = float(parts[0])
            status.input_fault_v   = float(parts[1])
            status.output_voltage  = float(parts[2])
            status.output_load_pct = int(float(parts[3]))
            status.input_freq      = float(parts[4])

            # Поле SS.SS / S.SS — для Online-ИБП это В/ячейку, для Standby — суммарное
            batt_raw = float(parts[5])
            status.battery_voltage_raw = batt_raw
            status.battery_voltage     = batt_raw  # итог после bits-анализа ниже

            status.temperature     = float(parts[6])

            bits = parts[7].zfill(8)
            status.utility_fail     = bits[0] == '1'
            status.battery_low      = bits[1] == '1'
            status.bypass_active    = bits[2] == '1'
            status.ups_failed       = bits[3] == '1'
            status.is_standby_type  = bits[4] == '1'  # 0 = Online, 1 = Standby
            status.test_in_progress = bits[5] == '1'
            status.shutdown_active  = bits[6] == '1'
            status.beeper_on        = bits[7] == '1'

            # Уточнить battery_cells на случай per_cell_2v формата
            if batt_raw < 5.0 and status.rated_batt_v > 10.0:
                status.battery_cells = max(1, round(status.rated_batt_v / 2.0))

            status.output_watts   = int(status.output_load_pct / 100.0 * UPS_RATED_W)
            status.battery_charge = status.battery_charge_pct()
            status.runtime_min    = status.estimated_runtime()
            status.timestamp      = datetime.now()
            return True
        except Exception:
            return False

    @staticmethod
    def parse_f(response: str, status: UPSStatus) -> bool:
        """
        Формат: #MMM.M QQQ SS.SS RR.R
        """
        try:
            line = response.strip()
            if not line.startswith('#'):
                return False
            parts = line[1:].split()
            if len(parts) < 4:
                return False
            status.rated_voltage = float(parts[0])
            status.rated_current = float(parts[1])
            rated_bv = float(parts[2])
            status.rated_batt_v  = rated_bv
            status.rated_freq    = float(parts[3])
            # Вычислить число ячеек: для Online-ИБП F-команда может вернуть
            # либо суммарное (72.0), либо per-cell (~2.0). Если > 10 — суммарное.
            if rated_bv > 10.0:
                status.battery_cells = max(1, round(rated_bv / 2.0))
            # если < 10 — это per-cell рейтинг (редкость), cells остаётся как есть
            return True
        except Exception:
            return False

    @staticmethod
    def parse_i(response: str, status: UPSStatus) -> bool:
        """
        Формат: #Company_Name    UPS_Model  Version
        """
        try:
            line = response.strip()
            if not line.startswith('#'):
                return False
            content = line[1:]
            status.company  = content[0:15].strip() or "ExeGate"
            status.model    = content[16:26].strip() or "TL-2000"
            status.firmware = content[27:37].strip() or "unknown"
            return True
        except Exception:
            return False


# ═════════════════════════════════════════════════════════════════════════════
#  ПОТОК ОПРОСА ИБП
# ═════════════════════════════════════════════════════════════════════════════

class UPSPollerThread(QThread):
    """Фоновый поток: периодически опрашивает ИБП по Megatec Q1."""

    status_updated  = pyqtSignal(object)   # UPSStatus
    event_occurred  = pyqtSignal(object)   # EventRecord
    connection_lost = pyqtSignal()
    connected       = pyqtSignal()
    port_error      = pyqtSignal(str, str) # (port_name, kind: "busy"|"missing"|"other")

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config          = copy.deepcopy(config)
        self._wake = threading.Event()
        self._device_info = UPSStatus()
        self._temperature_alarm = False
        self._temperature_threshold = None
        self._running        = False
        self._serial: Optional[serial.Serial] = None
        self._was_connected  = False
        self._prev_status: Optional[UPSStatus] = None
        self.parser          = MegatecParser()
        self._lock           = threading.Lock()
        self._demo_mode      = False   # работа без реального ИБП
        self._force_reconnect = False  # установить True чтобы принудительно переподключиться

        # Счётчик неудачных попыток — контролирует паузы между ретраями
        self._retry_count    = 0
        # Интервалы между ретраями (сек): 3 → 5 → 10 → 30 → 60 → 60 → ...
        self._retry_delays   = [3, 5, 10, 30, 60]

    def run(self):
        self._running = True
        self._connect()
        try:
            while self._running:
                start = time.monotonic()
                if self._force_reconnect:
                    self._force_reconnect = False
                    self._disconnect()
                    self._prev_status = None
                    self._temperature_alarm = False
                    self._device_info = UPSStatus()
                    self._demo_mode = False
                    self._retry_count = 0
                    self._connect()
                self._poll_once()
                interval = self.config["connection"]["poll_interval"] / 1000.0
                self._wake.wait(max(0.1, interval - (time.monotonic() - start)))
                self._wake.clear()
        finally:
            self._disconnect()

    def stop(self):
        self._running = False
        self._wake.set()

    def request_reconnect(self):
        self._force_reconnect = True
        self._wake.set()

    # ── Диагностика: кто держит порт ────────────────────────────────────────
    @staticmethod
    def _find_port_owner(port_name: str) -> str:
        """Возвращает имя процесса, занимающего порт, или '' если неизвестно."""
        try:
            # tasklist не покажет COM-порт напрямую, но handle.exe (Sysinternals) может.
            # Используем более доступный способ через WMI (Windows).
            import subprocess
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-WmiObject Win32_PnPEntity | Where-Object {{$_.Name -like '*{port_name}*'}} | Select-Object Name"],
                capture_output=True, text=True, timeout=3
            )
            out = result.stdout.strip()
            return out if out else ""
        except Exception:
            return ""

    @staticmethod
    def _list_available_ports() -> List[str]:
        """Вернуть список COM-портов, которые реально можно открыть."""
        available = []
        if not SERIAL_AVAILABLE:
            return available
        for port_info in serial.tools.list_ports.comports():
            try:
                s = serial.Serial(port_info.device, timeout=0.1)
                s.close()
                available.append(port_info.device)
            except serial.SerialException:
                pass
        return available

    # ── Подключение ──────────────────────────────────────────────────────────
    def _connect(self):
        if self.config["connection"]["type"] == "snmp":
            self._demo_mode = False
            return
        if not SERIAL_AVAILABLE:
            self._demo_mode = True
            self.event_occurred.emit(EventRecord(
                "WARNING", "pyserial не установлен. Запущен демо-режим."))
            return

        conn = self.config["connection"]
        if conn["type"] != "serial":
            return

        port = conn["port"]

        try:
            self._serial = serial.Serial(
                port     = port,
                baudrate = conn.get("baud", MEGATEC_BAUD),
                bytesize = MEGATEC_DATABITS,
                stopbits = MEGATEC_STOPBITS,
                parity   = MEGATEC_PARITY,
                timeout  = conn.get("timeout", MEGATEC_TIMEOUT)
            )

            # Запросить информацию об устройстве
            status = UPSStatus()
            resp = self._send_command("I")
            if resp:
                self.parser.parse_i(resp, status)
            resp = self._send_command("F")
            if resp:
                self.parser.parse_f(resp, status)

            self._device_info = status
            self._was_connected  = False  # only a valid Q1 confirms connection
            self._demo_mode      = False
            self._retry_count    = 0
            self.event_occurred.emit(
                EventRecord("INFO",
                    f"✅ Подключено к {port} | {status.company} {status.model} "
                    f"(прошивка: {status.firmware})")
            )

        except PermissionError as e:
            # Порт существует, но занят другим приложением
            self._demo_mode = True
            delay = self._retry_delays[min(self._retry_count, len(self._retry_delays)-1)]
            self._retry_count += 1

            # Найти свободные альтернативные порты
            alternatives = self._list_available_ports()
            alt_msg = (f"  Свободные порты: {', '.join(alternatives)}"
                       if alternatives else "  Других свободных портов не найдено.")

            self.event_occurred.emit(EventRecord(
                "WARNING",
                f"🔒 {port} занят другим приложением (PermissionError).\n"
                f"   Возможные причины: диспетчер устройств, другой экземпляр программы,\n"
                f"   служба Windows, антивирус.\n"
                f"{alt_msg}\n"
                f"   Следующая попытка через {delay} сек."
            ))
            self.port_error.emit(port, "busy")
            # Ждём перед следующей попыткой, не блокируя поток целиком
            for _ in range(delay * 10):
                if not self._running or self._force_reconnect:
                    break
                time.sleep(0.1)

        except serial.SerialException as e:
            err_str = str(e).lower()
            if "cannot find" in err_str or "not found" in err_str or "fnf" in err_str:
                kind = "missing"
                # Порт вообще не существует в системе
                all_ports = ([p.device for p in serial.tools.list_ports.comports()]
                             if SERIAL_AVAILABLE else [])
                ports_list = (", ".join(all_ports) if all_ports
                              else "ни одного COM-порта не обнаружено")
                msg = (f"⚠️ Порт {port} не найден в системе.\n"
                       f"   Подключите ИБП через USB или RS232.\n"
                       f"   Доступные порты: {ports_list}")
            else:
                kind = "other"
                msg = f"⚠️ Ошибка открытия {port}: {e}"

            self._demo_mode = True
            self._retry_count += 1
            self.event_occurred.emit(EventRecord("WARNING", msg))
            self.port_error.emit(port, kind)

        except Exception as e:
            self._demo_mode = True
            self._retry_count += 1
            self.event_occurred.emit(EventRecord(
                "WARNING", f"⚠️ {port}: непредвиденная ошибка: {e}. Демо-режим."))
            self.port_error.emit(port, "other")

    def _disconnect(self):
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._was_connected = False

    # ── Отправка команды ─────────────────────────────────────────────────────
    def _send_command(self, cmd: str) -> Optional[str]:
        """Отправить команду ИБП, вернуть ответ строкой или None."""
        if not self._serial or not self._serial.is_open:
            return None
        with self._lock:
            try:
                self._serial.reset_input_buffer()
                self._serial.write((cmd + '\r').encode('ascii'))
                self._serial.flush()
                time.sleep(0.1)
                resp = self._serial.read_until(b'\r').decode('ascii', errors='ignore')
                return resp.strip()
            except Exception as e:
                return None

    # ── Один цикл опроса ─────────────────────────────────────────────────────
    # Счётчик циклов для периодических авто-ретраев в демо-режиме
    _poll_cycle = 0
    # Интервалы авто-ретрая (в циклах опроса при poll_interval=2000мс):
    # попытка каждые ~30 сек (15 циклов × 2 сек)
    _AUTO_RETRY_CYCLES = 15

    # Флаг: отправляли ли уже диагностическое событие о батарее
    _batt_diag_sent = False

    def _poll_once(self):
        self._poll_cycle += 1
        status = copy.deepcopy(self._device_info)
        status.connected = False

        if self.config["connection"]["type"] == "snmp":
            try:
                for key, value in read_ups(self.config["connection"]).items():
                    setattr(status, key, value)
                status.connected = True
                status.timestamp = datetime.now()
                if not self._was_connected:
                    self._was_connected = True
                    self.connected.emit()
            except Exception as exc:
                if self._was_connected or self._poll_cycle == 1:
                    self.event_occurred.emit(EventRecord("CRITICAL", f"SNMP: {exc}"))
                    self.connection_lost.emit()
                self._was_connected = False
        elif self._demo_mode:
            # Периодически пробуем выйти из демо-режима и подключиться к реальному ИБП
            if self._poll_cycle % self._AUTO_RETRY_CYCLES == 0:
                self._disconnect()
                self._connect()
                if not self._demo_mode:
                    return  # подключились — следующий цикл уже реальный

            # Симуляция работы для демонстрации
            status = self._demo_status()
        else:
            if self._serial and self._serial.is_open:
                resp = self._send_command("Q1")
                if resp and self.parser.parse_q1(resp, status):
                    status.connected = True
                    if not self._was_connected:
                        self._was_connected = True
                        self.connected.emit()
                    # Однократная диагностика формата батарейного напряжения
                    if not self._batt_diag_sent and status.battery_voltage > 0:
                        self._batt_diag_sent = True
                        self.event_occurred.emit(EventRecord(
                            "INFO",
                            f"🔋 Диагностика батареи: "
                            f"raw={status.battery_voltage:.3f} В → "
                            f"формат «{status.battery_format_label}» → "
                            f"суммарно {status.battery_voltage_total:.1f} В → "
                            f"заряд {status.battery_charge}%"
                        ))
                else:
                    status.connected = False
                    if self._was_connected:
                        self._was_connected = False
                        self._batt_diag_sent = False  # сброс при переподключении
                        self.connection_lost.emit()
                        self.event_occurred.emit(
                            EventRecord("CRITICAL", "Связь с ИБП потеряна")
                        )
            else:
                # Порт закрылся — переподключение
                self._connect()
                return

        # Генерация событий при изменении состояния
        if status.connected and not self._demo_mode:
            self._detect_events(status)
            self._prev_status = status
        self.status_updated.emit(status)

    # ── Демо-режим ───────────────────────────────────────────────────────────
    _demo_t = 0.0

    def _demo_status(self) -> 'UPSStatus':
        """Реалистичная симуляция состояния ИБП для демонстрации."""
        import math
        self._demo_t += 0.05

        s = UPSStatus()
        s.connected       = True
        # Небольшой дрейф сетевого напряжения (225–235В)
        s.input_voltage   = 230.0 + 3.0 * math.sin(self._demo_t * 0.3)
        s.input_fault_v   = s.input_voltage
        s.output_voltage  = 230.0 + 0.5 * math.sin(self._demo_t * 0.5)
        s.input_freq      = 50.0 + 0.05 * math.sin(self._demo_t * 0.2)
        s.output_load_pct = 35 + int(8 * math.sin(self._demo_t * 0.15))
        # Симулируем per-module формат (12В аккумуляторы), типичный для ExeGate
        # 72В = 6 × 12В → при полном заряде каждый ~13.2В
        s.battery_voltage = 13.1 + 0.15 * math.sin(self._demo_t * 0.1)
        s.temperature     = 32.0 + 2.0 * math.sin(self._demo_t * 0.05)

        s.utility_fail    = False
        s.battery_low     = False
        s.bypass_active   = False
        s.ups_failed      = False
        s.is_standby_type = False
        s.beeper_on       = False

        s.output_watts    = int(s.output_load_pct / 100.0 * UPS_RATED_W)
        s.battery_charge  = s.battery_charge_pct()
        s.runtime_min     = s.estimated_runtime()
        return s

    # ── Детектор событий ─────────────────────────────────────────────────────
    def _detect_events(self, curr: UPSStatus):
        if not curr.connected:
            return
        prev = self._prev_status
        if curr.utility_fail and (prev is None or not prev.utility_fail):
            self.event_occurred.emit(EventRecord("CRITICAL",
                "⚡ Сбой сетевого питания — ИБП перешёл на батарею",
                {"alert": "utility_fail", "input_v": curr.input_voltage}))
        elif prev is not None and not curr.utility_fail and prev.utility_fail:
            self.event_occurred.emit(EventRecord("INFO", "✅ Сетевое питание восстановлено"))
        if curr.battery_low and (prev is None or not prev.battery_low):
            self.event_occurred.emit(EventRecord("CRITICAL",
                f"🔋 Низкий заряд батареи: {curr.battery_charge}%", {"alert": "battery_low"}))
        threshold = float(self.config["alerts"]["temp_threshold"])
        if threshold != self._temperature_threshold:
            self._temperature_alarm = False
            self._temperature_threshold = threshold
        if curr.temperature >= threshold and not self._temperature_alarm:
            self._temperature_alarm = True
            self.event_occurred.emit(EventRecord("WARNING",
                f"🌡️ Высокая температура: {curr.temperature:.1f}°C (порог {threshold:g}°C)",
                {"alert": "temp_high"}))
        elif curr.temperature <= threshold - 2.0:
            self._temperature_alarm = False
        if curr.bypass_active and (prev is None or not prev.bypass_active):
            self.event_occurred.emit(EventRecord("WARNING", "⚠️ Активирован режим AVR/Bypass"))
        if curr.ups_failed and (prev is None or not prev.ups_failed):
            self.event_occurred.emit(EventRecord("CRITICAL", "⛔ ИБП сообщает о неисправности"))

    # ── Команды управления ────────────────────────────────────────────────────
    def _can_control(self):
        if self.config["connection"]["type"] == "snmp":
            self.event_occurred.emit(EventRecord("WARNING", "Команды управления ИБП доступны только через COM-порт."))
            return False
        if not self._demo_mode and not self._was_connected:
            self.event_occurred.emit(EventRecord("WARNING", "Команда не отправлена: нет подтверждённой связи с ИБП."))
            return False
        return True

    def send_test_short(self):
        """Тест 10 секунд (команда T)."""
        if not self._can_control():
            return
        if self._demo_mode:
            self.event_occurred.emit(EventRecord("ACTION", "Тест батареи (10 сек) — демо режим"))
            return
        self._send_command("T")
        self.event_occurred.emit(EventRecord("ACTION", "Запущен тест батареи (10 сек)"))

    def send_test_long(self):
        """Тест до разряда (команда TL)."""
        if not self._can_control():
            return
        if self._demo_mode:
            self.event_occurred.emit(EventRecord("ACTION", "Длинный тест батареи — демо режим"))
            return
        self._send_command("TL")
        self.event_occurred.emit(EventRecord("ACTION", "Запущен длинный тест батареи"))

    def send_test_cancel(self):
        """Отмена теста (команда CT)."""
        if not self._can_control():
            return
        if self._demo_mode:
            return
        self._send_command("CT")
        self.event_occurred.emit(EventRecord("ACTION", "Тест отменён"))

    def send_shutdown(self, minutes: float = 5.0):
        """Выключить выход ИБП через N минут (команда S<n>)."""
        if not self._can_control():
            return
        if self._demo_mode:
            self.event_occurred.emit(EventRecord("ACTION", f"Команда выключения через {minutes} мин — демо"))
            return
        n = f"{minutes:.1f}" if minutes < 1 else str(int(minutes))
        self._send_command(f"S{n}")
        self.event_occurred.emit(EventRecord("ACTION", f"Команда выключения ИБП через {minutes} мин"))

    def send_shutdown_restore(self, off_min: float, on_min: int):
        """Выключить и восстановить (команда S<n>R<m>)."""
        if not self._can_control():
            return
        if self._demo_mode:
            return
        n = f"{off_min:.1f}" if off_min < 1 else str(int(off_min))
        self._send_command(f"S{n}R{on_min:04d}")
        self.event_occurred.emit(EventRecord(
            "ACTION", f"Перезагрузка ИБП: откл через {off_min} мин, вкл через {on_min} мин"))

    def send_cancel_shutdown(self):
        """Отмена команды выключения (команда C)."""
        if not self._can_control():
            return
        if self._demo_mode:
            return
        self._send_command("C")
        self.event_occurred.emit(EventRecord("ACTION", "Команда выключения отменена"))

    def send_toggle_beeper(self):
        """Переключить пищалку (команда Q)."""
        if not self._can_control():
            return
        if self._demo_mode:
            return
        self._send_command("Q")
        self.event_occurred.emit(EventRecord("ACTION", "Пищалка переключена"))


# ═════════════════════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ВИДЖЕТЫ
# ═════════════════════════════════════════════════════════════════════════════

class PowerFlowWidget(QWidget):
    """
    Анимированная блок-схема потока энергии через ИБП.

    Топология ExeGate TL-2000 (online double-conversion):
      СЕТЬ ──► ВЫПРЯМИТЕЛЬ ──► [DC-шина] ──► ИНВЕРТОР ──► НАГРУЗКА
                                   │
                               БАТАРЕЯ (заряд ↕ разряд)

      БАЙПАС: СЕТЬ ────────────────────────────────────► НАГРУЗКА
                                                         (минуя инвертор)
    """

    # Цвета путей
    _C_AC_NORMAL = "#4ade80"   # зелёный — сетевое AC
    _C_DC        = "#38bdf8"   # голубой  — DC шина
    _C_BATT_CHG  = "#38bdf8"   # голубой  — зарядка батареи
    _C_BATT_DIS  = "#facc15"   # жёлтый   — разрядка (батарея питает)
    _C_BYPASS    = "#f97316"   # оранжевый— байпас
    _C_INACTIVE  = "#2d3748"   # серый     — неактивный путь
    _C_BOX_ACT   = "#1e293b"   # фон блока активного
    _C_BOX_INA   = "#111827"   # фон блока неактивного
    _C_TEXT      = "#e2e8f0"
    _C_TEXT_DIM  = "#64748b"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(118)
        self.setMaximumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Состояние
        self._on_battery  = False
        self._bypass      = False
        self._connected   = False
        self._batt_charge = 0
        self._batt_color  = _THEME_REF = THEME["accent3"]

        # Анимация: фаза 0.0–1.0 для движущихся точек
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)   # 20 FPS

    def update_status(self, s):
        self._on_battery  = bool(s.utility_fail)
        self._bypass      = bool(s.bypass_active)
        self._connected   = True
        self._batt_charge = int(s.battery_charge)
        c = s.battery_charge
        self._batt_color = (THEME["fault"]   if c < 20 else
                            THEME["battery"] if c < 40 else THEME["accent3"])
        self.update()

    def set_disconnected(self):
        self._connected = False
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.04) % 1.0
        self.update()

    # ── Рисование ─────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        from PyQt5.QtCore import QRectF, QPointF
        from PyQt5.QtGui  import QPainterPath, QLinearGradient

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(THEME["bg_medium"]))

        W, H = self.width(), self.height()
        if W < 100 or H < 60:
            return

        # ── Геометрия (в % от W/H) ────────────────────────────────────────────
        # Вертикальный центр основной шины
        MID_Y  = H * 0.48
        # Y центра байпас-дуги
        BYP_Y  = H * 0.12
        # Высота блоков
        BH = max(28, H * 0.30)
        BW = max(72, W * 0.13)

        # X-центры блоков (0..W)
        xG  = W * 0.08   # СЕТЬ
        xR  = W * 0.30   # ВЫПРЯМИТЕЛЬ
        xD  = W * 0.50   # DC-ШИНА (центр)
        xI  = W * 0.70   # ИНВЕРТОР
        xL  = W * 0.92   # НАГРУЗКА
        # Y-центр батареи
        yB  = H * 0.82

        def box(cx, cy, label1, label2="", active=True, border_color=None):
            bx = cx - BW / 2
            by = cy - BH / 2
            bc = border_color or (THEME["accent3"] if active else self._C_INACTIVE)
            bg = self._C_BOX_ACT if active else self._C_BOX_INA

            p.setPen(QPen(QColor(bc), 1.5))
            p.setBrush(QColor(bg))
            p.drawRoundedRect(QRectF(bx, by, BW, BH), 5, 5)

            tc = self._C_TEXT if active else self._C_TEXT_DIM
            p.setPen(QColor(tc))
            f1 = QFont("Segoe UI", max(6, int(BH * 0.22)), QFont.Bold)
            p.setFont(f1)
            if label2:
                p.drawText(QRectF(bx, by, BW, BH * 0.52),
                           Qt.AlignCenter, label1)
                f2 = QFont("Segoe UI", max(5, int(BH * 0.18)))
                p.setFont(f2)
                p.setPen(QColor(self._C_TEXT_DIM if active else self._C_TEXT_DIM))
                p.drawText(QRectF(bx, by + BH * 0.50, BW, BH * 0.50),
                           Qt.AlignCenter, label2)
            else:
                p.drawText(QRectF(bx, by, BW, BH), Qt.AlignCenter, label1)

        def line(x1, y1, x2, y2, color, width=2.0, active=True, dots=True):
            """Линия с движущимися точками."""
            c = QColor(color) if active else QColor(self._C_INACTIVE)
            pen = QPen(c, width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            if active and dots:
                # Движущиеся точки вдоль линии
                n_dots = max(2, int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 // 28))
                for i in range(n_dots):
                    t = (self._phase + i / n_dots) % 1.0
                    dx = x1 + (x2 - x1) * t
                    dy = y1 + (y2 - y1) * t
                    p.setPen(Qt.NoPen)
                    p.setBrush(c)
                    p.drawEllipse(QPointF(dx, dy), 3.5, 3.5)

        def arc_bypass(active):
            """Дуга байпаса сверху: от СЕТЬ до НАГРУЗКА."""
            c = QColor(self._C_BYPASS if active else self._C_INACTIVE)
            pen = QPen(c, 1.5)
            pen.setStyle(Qt.DashLine if not active else Qt.SolidLine)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)

            path = QPainterPath()
            path.moveTo(xG, MID_Y - BH / 2)
            path.cubicTo(xG, BYP_Y,
                         xL, BYP_Y,
                         xL, MID_Y - BH / 2)
            p.drawPath(path)

            # Метка БАЙПАС
            lc = self._C_BYPASS if active else self._C_TEXT_DIM
            p.setPen(QColor(lc))
            p.setFont(QFont("Segoe UI", max(6, int(H * 0.09))))
            p.drawText(QRectF(W * 0.35, 0, W * 0.30, H * 0.22),
                       Qt.AlignCenter, "BYPASS")

            if active:
                # Точки вдоль дуги
                for i in range(3):
                    t = (self._phase + i / 3) % 1.0
                    # Аппроксимация кубика Безье
                    mt = 1 - t
                    bx = (mt**3 * xG
                          + 3 * mt**2 * t * xG
                          + 3 * mt * t**2 * xL
                          + t**3 * xL)
                    by_ = (mt**3 * (MID_Y - BH / 2)
                           + 3 * mt**2 * t * BYP_Y
                           + 3 * mt * t**2 * BYP_Y
                           + t**3 * (MID_Y - BH / 2))
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(self._C_BYPASS))
                    p.drawEllipse(QPointF(bx, by_), 3.5, 3.5)

        def batt_box(active_chg, active_dis):
            """Батарея с мини-прогрессбаром заряда."""
            bx = xD - BW / 2
            by = yB - BH / 2
            bc = (self._batt_color if (active_chg or active_dis)
                  else self._C_INACTIVE)
            p.setPen(QPen(QColor(bc), 1.5))
            p.setBrush(QColor(self._C_BOX_ACT if (active_chg or active_dis)
                              else self._C_BOX_INA))
            p.drawRoundedRect(QRectF(bx, by, BW, BH), 5, 5)

            # Заряд текстом
            tc = self._C_TEXT if (active_chg or active_dis) else self._C_TEXT_DIM
            p.setPen(QColor(tc))
            p.setFont(QFont("Segoe UI", max(6, int(BH * 0.22)), QFont.Bold))
            p.drawText(QRectF(bx, by, BW, BH * 0.50), Qt.AlignCenter, "БАТАРЕЯ")

            # Полоска заряда
            pad = BW * 0.12
            pw  = BW - 2 * pad
            ph  = max(4, BH * 0.18)
            px  = bx + pad
            py_ = by + BH * 0.58
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(THEME["bg_light"]))
            p.drawRoundedRect(QRectF(px, py_, pw, ph), 2, 2)
            if self._batt_charge > 0:
                fill = pw * min(1.0, self._batt_charge / 100)
                p.setBrush(QColor(self._batt_color))
                p.drawRoundedRect(QRectF(px, py_, fill, ph), 2, 2)

            # Процент
            p.setPen(QColor(tc))
            p.setFont(QFont("Segoe UI", max(5, int(BH * 0.17))))
            p.drawText(QRectF(bx, py_ + ph, BW, BH * 0.25),
                       Qt.AlignCenter, f"{self._batt_charge}%")

        # ══════════════════════════════════════════════════════════════════════
        # Определяем активные пути
        if not self._connected:
            on_mains = False
            on_batt  = False
            bypass   = False
        else:
            bypass   = self._bypass
            on_batt  = self._on_battery and not bypass
            on_mains = not self._on_battery and not bypass

        chg_active = on_mains   # зарядка батареи идёт от сети
        dis_active = on_batt    # разрядка — батарея питает DC-шину

        # ── Байпас-дуга (рисуем первой, чтоб блоки были поверх) ──
        arc_bypass(bypass)

        # ── Горизонтальные линии основной шины ────────────────────
        # СЕТЬ → ВЫПРЯМИТЕЛЬ
        line(xG + BW / 2, MID_Y,
             xR - BW / 2, MID_Y,
             self._C_AC_NORMAL, 2.0, on_mains)

        # ВЫПРЯМИТЕЛЬ → DC-шина
        line(xR + BW / 2, MID_Y,
             xD - BW / 2, MID_Y,
             self._C_DC, 2.0, on_mains)

        # DC-шина → ИНВЕРТОР
        line(xD + BW / 2, MID_Y,
             xI - BW / 2, MID_Y,
             self._C_DC, 2.0, on_mains or on_batt)

        # ИНВЕРТОР → НАГРУЗКА
        line(xI + BW / 2, MID_Y,
             xL - BW / 2, MID_Y,
             self._C_AC_NORMAL, 2.0, on_mains or on_batt)

        # ── Вертикальная линия: DC-шина ↕ БАТАРЕЯ ────────────────
        batt_line_color = (self._C_BATT_DIS if on_batt else
                           self._C_BATT_CHG if on_mains else
                           self._C_INACTIVE)
        batt_active = on_mains or on_batt

        # Рисуем линию со стрелкой-направлением
        lx = xD
        ly_top = MID_Y + BH / 2
        ly_bot = yB - BH / 2
        c_bl = QColor(batt_line_color if batt_active else self._C_INACTIVE)
        pen_b = QPen(c_bl, 2.0)
        pen_b.setCapStyle(Qt.RoundCap)
        p.setPen(pen_b)
        p.drawLine(QPointF(lx, ly_top), QPointF(lx, ly_bot))

        if batt_active:
            # Точки: движутся вниз при зарядке, вверх при разрядке
            n_v = max(2, int((ly_bot - ly_top) // 20))
            for i in range(n_v):
                if on_batt:
                    t = 1.0 - (self._phase + i / n_v) % 1.0   # вверх
                else:
                    t = (self._phase + i / n_v) % 1.0           # вниз
                dy = ly_top + (ly_bot - ly_top) * t
                p.setPen(Qt.NoPen)
                p.setBrush(c_bl)
                p.drawEllipse(QPointF(lx, dy), 3.5, 3.5)

        # ── Блоки (поверх линий) ──────────────────────────────────
        box(xG,  MID_Y, "СЕТЬ",       "~AC",        on_mains or bypass,
            self._C_AC_NORMAL if (on_mains or bypass) else None)
        box(xR,  MID_Y, "ВЫПРЯМИТЕЛЬ", "RECTIFIER", on_mains,
            self._C_DC if on_mains else None)
        box(xD,  MID_Y, "DC",          "шина",      on_mains or on_batt,
            self._C_DC if (on_mains or on_batt) else None)
        box(xI,  MID_Y, "ИНВЕРТОР",    "INVERTER",  on_mains or on_batt,
            self._C_AC_NORMAL if (on_mains or on_batt) else None)
        box(xL,  MID_Y, "НАГРУЗКА",    "LOAD",      self._connected,
            THEME["accent2"] if self._connected else None)

        batt_box(chg_active, dis_active)

        # ── Надпись режима ────────────────────────────────────────
        if self._connected:
            if bypass:
                mode_txt  = "● БАЙПАС"
                mode_col  = self._C_BYPASS
            elif on_batt:
                mode_txt  = "● ПИТАНИЕ ОТ БАТАРЕИ"
                mode_col  = THEME["battery"]
            else:
                mode_txt  = "● СЕТЕВОЕ ПИТАНИЕ"
                mode_col  = THEME["accent3"]
        else:
            mode_txt = "○ НЕТ СВЯЗИ"
            mode_col = self._C_TEXT_DIM

        p.setPen(QColor(mode_col))
        p.setFont(QFont("Segoe UI", max(7, int(H * 0.09)), QFont.Bold))
        p.drawText(QRectF(W * 0.65, H * 0.78, W * 0.35, H * 0.22),
                   Qt.AlignCenter, mode_txt)


class GaugeWidget(QWidget):
    """Круговой индикатор (аналог спидометра) для значений 0–100%."""

    def __init__(self, label="", unit="%", max_val=100, parent=None):
        super().__init__(parent)
        self.label   = label
        self.unit    = unit
        self.max_val = max_val
        self._value  = 0.0
        self._color  = QColor(THEME["accent3"])
        self.setMinimumSize(130, 130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setValue(self, val: float, color: str = None):
        self._value = float(val)
        if color:
            self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        # Резервируем 20px снизу под подпись, 4px сверху
        margin_top  = 4
        margin_bot  = 22
        size = min(w, h - margin_bot - margin_top) - 10
        if size < 20:
            return
        cx = w // 2
        cy = margin_top + size // 2

        r = size // 2
        rect_full = (cx - r, cy - r, size, size)
        arc_thick = max(4, size // 14)

        from PyQt5.QtCore import QRectF, QRect

        # Фон дуги
        pen = QPen(QColor(THEME["bg_light"]), arc_thick)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(QRectF(*rect_full), 225 * 16, -270 * 16)

        # Дуга значения
        pct  = min(1.0, self._value / self.max_val)
        span = int(-270 * 16 * pct)
        if span != 0:
            pen2 = QPen(self._color, arc_thick)
            pen2.setCapStyle(Qt.RoundCap)
            painter.setPen(pen2)
            painter.drawArc(QRectF(*rect_full), 225 * 16, span)

        # Числовое значение — по центру круга
        painter.setPen(QColor(THEME["text_primary"]))
        val_str = (f"{self._value:.1f}" if self._value != int(self._value)
                   else str(int(self._value)))
        val_font_size = max(9, size // 7)
        painter.setFont(QFont("Consolas", val_font_size, QFont.Bold))
        # Прямоугольник для числа: верхняя половина центра
        painter.drawText(QRect(cx - r, cy - size // 4, size, size // 3),
                         Qt.AlignCenter, f"{val_str}{self.unit}")

        # Подпись — под числом, внутри круга или ниже
        painter.setPen(QColor(THEME["text_sec"]))
        lbl_font_size = max(7, size // 14)
        painter.setFont(QFont("Segoe UI", lbl_font_size))
        # Рисуем подпись в нижней части: cy + небольшой отступ
        lbl_y = cy + size // 8
        painter.drawText(QRect(cx - r, lbl_y, size, margin_bot + (h - cy - r)),
                         Qt.AlignHCenter | Qt.AlignTop, self.label)


class BarMeter(QWidget):
    """Горизонтальная полоса со значением и пороговой раскраской."""

    def __init__(self, label="", unit="", min_val=0.0, max_val=100.0,
                 warn=70.0, crit=90.0, parent=None):
        super().__init__(parent)
        self.label   = label
        self.unit    = unit
        self.min_val = min_val
        self.max_val = max_val
        self.warn    = warn
        self.crit    = crit
        self._value  = 0.0
        self.setFixedHeight(52)          # было 46 — добавляем запас сверху/снизу
        self.setMinimumWidth(220)

    def setValue(self, val: float):
        self._value = float(val)
        self.update()

    def paintEvent(self, event):
        from PyQt5.QtCore import QRect, QRectF
        from PyQt5.QtGui import QFontMetrics

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()

        lbl_font = QFont("Segoe UI", 9)
        painter.setFont(lbl_font)
        fm = QFontMetrics(lbl_font)
        # Ширина метки = реальная ширина текста + 8px запас, минимум 80px
        label_w = max(80, fm.horizontalAdvance(self.label) + 8)

        val_w   = 72
        bar_x   = label_w + 6
        bar_w   = max(20, w - bar_x - val_w - 4)
        bar_h   = 10
        bar_y   = (h - bar_h) // 2 + 2

        # Метка
        painter.setPen(QColor(THEME["text_sec"]))
        painter.drawText(QRect(0, 0, label_w, h),
                         Qt.AlignVCenter | Qt.AlignLeft, self.label)

        # Фон полосы
        painter.setBrush(QColor(THEME["bg_light"]))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 3, 3)

        # Заполнение
        pct  = (self._value - self.min_val) / max(1, self.max_val - self.min_val)
        pct  = max(0.0, min(1.0, pct))
        fill = int(bar_w * pct)
        if fill > 0:
            if pct >= self.crit / 100:
                color = QColor(THEME["fault"])
            elif pct >= self.warn / 100:
                color = QColor(THEME["battery"])
            else:
                color = QColor(THEME["accent3"])
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(bar_x, bar_y, fill, bar_h), 3, 3)

        # Значение
        val_font = QFont("Consolas", 10, QFont.Bold)
        painter.setPen(QColor(THEME["text_primary"]))
        painter.setFont(val_font)
        val_str = f"{self._value:.1f} {self.unit}"
        painter.drawText(QRect(bar_x + bar_w + 4, 0, val_w, h),
                         Qt.AlignVCenter | Qt.AlignRight, val_str)


class StatusBadge(QLabel):
    """Цветной бейдж с текстом режима работы ИБП."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.setFixedHeight(36)
        self.setColor(THEME["text_sec"])

    def setColor(self, color: str):
        self.setStyleSheet(f"""
            QLabel {{
                color: {color};
                background: transparent;
                border: 2px solid {color};
                border-radius: 6px;
                padding: 4px 16px;
            }}
        """)


class ValueCard(QFrame):
    """Карточка с одним числовым показателем."""

    def __init__(self, title: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("valueCard")
        self.setStyleSheet(f"""
            QFrame#valueCard {{
                background: {THEME['bg_medium']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(1)

        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            f"color: {THEME['text_sec']}; font: 8pt 'Segoe UI'; background: transparent;")
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        self._val_lbl = QLabel("—")
        self._val_lbl.setStyleSheet(
            f"color: {THEME['text_primary']}; font: bold 12pt 'Consolas';"
            f" background: transparent;")
        self._val_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._val_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        self._unit = unit
        layout.addWidget(self._val_lbl)    # значение сверху
        layout.addWidget(self._title_lbl)  # подпись снизу

    def setValue(self, val, decimals: int = 1, color: str = None):
        if isinstance(val, float):
            text = f"{val:.{decimals}f} {self._unit}"
        else:
            text = f"{val} {self._unit}"
        self._val_lbl.setText(text.strip())
        c = color or THEME["text_primary"]
        self._val_lbl.setStyleSheet(
            f"color: {c}; font: bold 12pt 'Consolas'; background: transparent;")


# ═════════════════════════════════════════════════════════════════════════════
#  ВКЛАДКА: ДАШБОРД
# ═════════════════════════════════════════════════════════════════════════════

class DashboardTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: #0D1117; } "
                            "QScrollBar:vertical { background: #161B22; width: 12px; } "
                            "QScrollBar::handle:vertical { background: #30363D; min-height: 30px; }")
        inner = QWidget()
        inner.setObjectName("dashboardContent")
        inner.setStyleSheet("QWidget#dashboardContent { background: #0D1117; }")
        root = QVBoxLayout(inner)
        root.setSizeConstraint(QLayout.SetMinimumSize)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Верхняя строка: режим + ключевые показатели ──
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        # Карточка режима
        mode_frame = QFrame()
        mode_frame.setObjectName("modeFrame")
        mode_frame.setStyleSheet(f"""
            QFrame#modeFrame {{
                background: {THEME['bg_medium']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
            }}
        """)
        mode_layout = QVBoxLayout(mode_frame)
        mode_layout.setContentsMargins(16, 12, 16, 12)

        lbl_model = QLabel(UPS_MODEL)
        lbl_model.setStyleSheet(f"color: {THEME['text_sec']}; font: 9pt 'Segoe UI';")
        mode_layout.addWidget(lbl_model)

        self.mode_badge = StatusBadge("НЕТ СВЯЗИ")
        mode_layout.addWidget(self.mode_badge)

        lbl_rated = QLabel(f"2000 ВА / 1800 Вт  ·  72В батарея  ·  4 розетки Schuko")
        lbl_rated.setStyleSheet(f"color: {THEME['text_sec']}; font: 8pt 'Segoe UI';")
        mode_layout.addWidget(lbl_rated)

        top_row.addWidget(mode_frame, 2)

        # Карточки показателей
        self.card_input_v   = ValueCard("Вход", "В")
        self.card_output_v  = ValueCard("Выход", "В")
        self.card_freq      = ValueCard("Частота", "Гц")
        self.card_watts     = ValueCard("Нагрузка", "Вт")
        self.card_runtime   = ValueCard("Автономия (оценка)", "мин")
        self.card_temp      = ValueCard("Температура", "°C")
        self.card_batt_v    = ValueCard("Батарея", "В")

        for card in [self.card_input_v, self.card_output_v, self.card_freq,
                     self.card_watts, self.card_runtime, self.card_temp,
                     self.card_batt_v]:
            top_row.addWidget(card, 1)

        root.addLayout(top_row)

        # ── Средняя зона: датчики + индикаторы ──
        mid_row = QHBoxLayout()
        mid_row.setSpacing(10)

        # Круговые датчики
        gauges_frame = QFrame()
        gauges_frame.setObjectName("gf")
        gauges_frame.setStyleSheet(f"""
            QFrame#gf {{
                background: {THEME['bg_medium']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
            }}
        """)
        gauges_layout = QHBoxLayout(gauges_frame)
        gauges_layout.setContentsMargins(10, 10, 10, 10)

        self.gauge_batt = GaugeWidget("Заряд батареи", "%", 100)
        self.gauge_load = GaugeWidget("Нагрузка", "%", 100)
        self.gauge_temp = GaugeWidget("Температура", "°C", 60)

        for g in [self.gauge_batt, self.gauge_load, self.gauge_temp]:
            g.setMinimumSize(140, 140)
            gauges_layout.addWidget(g)

        mid_row.addWidget(gauges_frame, 3)

        # Панель состояния
        status_frame = QFrame()
        status_frame.setObjectName("sf")
        status_frame.setStyleSheet(f"""
            QFrame#sf {{
                background: {THEME['bg_medium']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
            }}
        """)
        sf_layout = QVBoxLayout(status_frame)
        sf_layout.setContentsMargins(12, 10, 12, 10)
        sf_layout.setSpacing(4)

        lbl_flags = QLabel("Состояние флагов")
        lbl_flags.setStyleSheet(f"color: {THEME['text_sec']}; font: 9pt 'Segoe UI';")
        sf_layout.addWidget(lbl_flags)

        flags = [
            ("utility_fail",     "⚡", "Питание от батареи"),
            ("battery_low",      "🔋", "Батарея разряжена"),
            ("bypass_active",    "⤳",  "AVR / Bypass"),
            ("ups_failed",       "🚫", "Неисправность ИБП"),
            ("test_in_progress", "🔬", "Идёт тест"),
            ("shutdown_active",  "⏻",  "Завершение работы"),
            ("beeper_on",        "🔔", "Пищалка активна"),
        ]
        self._flag_labels: Dict[str, QLabel] = {}
        for key, icon, desc in flags:
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(6)
            ico = QLabel(icon)
            ico.setFixedWidth(20)
            txt = QLabel(desc)
            txt.setStyleSheet(f"color: {THEME['text_sec']}; font: 9pt 'Segoe UI';")
            indicator = QLabel("●")
            indicator.setStyleSheet(f"color: {THEME['bg_light']};")
            indicator.setFixedWidth(16)
            row_l.addWidget(ico)
            row_l.addWidget(txt, 1)
            row_l.addWidget(indicator)
            sf_layout.addWidget(row_w)
            self._flag_labels[key] = indicator

        sf_layout.addStretch()
        mid_row.addWidget(status_frame, 1)
        root.addLayout(mid_row)

        # ── Блок-схема потока энергии ──
        self.power_flow = PowerFlowWidget()
        root.addWidget(self.power_flow)

        # ── Нижняя зона: полосы ──
        bars_frame = QFrame()
        bars_frame.setObjectName("bf")
        bars_frame.setStyleSheet(f"""
            QFrame#bf {{
                background: {THEME['bg_medium']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
            }}
        """)
        bars_layout = QVBoxLayout(bars_frame)
        bars_layout.setContentsMargins(12, 10, 12, 10)
        bars_layout.setSpacing(2)

        lbl_bars = QLabel("Параметры питания")
        lbl_bars.setStyleSheet(f"color: {THEME['text_sec']}; font: 9pt 'Segoe UI';")
        bars_layout.addWidget(lbl_bars)

        self.bar_input_v  = BarMeter("Входное напряжение", "В",
                                      min_val=180, max_val=260, warn=75, crit=92)
        self.bar_output_v = BarMeter("Выходное напряжение", "В",
                                      min_val=180, max_val=260, warn=80, crit=95)
        self.bar_load     = BarMeter("Нагрузка", "%",
                                      min_val=0, max_val=100, warn=70, crit=90)
        # Диапазон bar_batt_v обновляется динамически в update_status
        self.bar_batt_v   = BarMeter("Напряжение батареи", "В",
                                      min_val=60, max_val=82, warn=85, crit=95)
        self.bar_temp     = BarMeter("Температура", "°C",
                                      min_val=0, max_val=70, warn=64, crit=90)

        for bar in [self.bar_input_v, self.bar_output_v, self.bar_load,
                    self.bar_batt_v, self.bar_temp]:
            bars_layout.addWidget(bar)

        root.addWidget(bars_frame)

        # График (pyqtgraph)
        if PYQTGRAPH_AVAILABLE:
            graph_frame = QFrame()
            graph_frame.setObjectName("grFrame")
            graph_frame.setStyleSheet(f"""
                QFrame#grFrame {{
                    background: {THEME['bg_medium']};
                    border: 1px solid {THEME['border']};
                    border-radius: 8px;
                }}
            """)
            grfl = QVBoxLayout(graph_frame)
            grfl.setContentsMargins(8, 6, 8, 6)

            lbl_gr = QLabel("История (последние 120 точек)")
            lbl_gr.setStyleSheet(f"color: {THEME['text_sec']}; font: 9pt 'Segoe UI';")
            grfl.addWidget(lbl_gr)

            self.plot_widget = pg.PlotWidget(title="")
            self.plot_widget.setFixedHeight(140)
            self.plot_widget.setBackground(THEME["bg_dark"])
            self.plot_widget.getAxis('left').setTextPen(QColor(THEME["text_sec"]))
            self.plot_widget.getAxis('bottom').setTextPen(QColor(THEME["text_sec"]))
            self.plot_widget.showGrid(x=False, y=True, alpha=0.3)
            self.plot_widget.addLegend()

            self._buf_input  = []
            self._buf_output = []
            self._buf_load   = []
            self._curve_input  = self.plot_widget.plot(
                pen=pg.mkPen(THEME["accent2"], width=1.5), name="Вход (В)")
            self._curve_output = self.plot_widget.plot(
                pen=pg.mkPen(THEME["accent3"], width=1.5), name="Выход (В)")
            self._curve_load   = self.plot_widget.plot(
                pen=pg.mkPen(THEME["battery"], width=1.5), name="Нагрузка (%×2)")
            grfl.addWidget(self.plot_widget)
            root.addWidget(graph_frame)

    # ── Обновление данных ─────────────────────────────────────────────────────
    def update_status(self, s: UPSStatus):
        self.mode_badge.setText(s.mode_string())
        self.mode_badge.setColor(s.mode_color())

        c = s.mode_color()
        self.card_input_v.setValue(s.input_voltage, 1, THEME["accent2"])
        self.card_output_v.setValue(s.output_voltage, 1, THEME["accent2"])
        self.card_freq.setValue(s.input_freq, 1)
        self.card_watts.setValue(s.output_watts, 0,
            THEME["fault"] if s.output_load_pct > 90 else
            THEME["battery"] if s.output_load_pct > 70 else THEME["accent3"])
        self.card_runtime.setValue(s.runtime_min, 0,
            THEME["fault"] if s.runtime_min < 5 else
            THEME["battery"] if s.runtime_min < 10 else THEME["accent3"])
        threshold = self.window().config["alerts"]["temp_threshold"]
        self.card_runtime.setToolTip("Оценка по напряжению и нагрузке для Q1; значение UPS MIB для SNMP. Не гарантированное время работы.")
        self.card_temp.setValue(s.temperature if math.isfinite(s.temperature) else "—", 1,
            THEME["fault"] if s.temperature >= threshold else
            THEME["battery"] if s.temperature >= threshold - 2 else THEME["accent3"])
        # Батарея: показываем суммарное В, tooltip — формат и ячейки
        batt_total = s.battery_voltage_total
        batt_cell  = s.battery_voltage_per_cell
        batt_color = (THEME["fault"]   if s.battery_charge < 20 else
                      THEME["battery"] if s.battery_charge < 40 else THEME["accent3"])
        self.card_batt_v.setValue(batt_total, 1, batt_color)
        self.card_batt_v._val_lbl.setToolTip(
            f"Формат: {s.battery_format_label}\n"
            f"Суммарно: {batt_total:.1f} В  |  "
            f"На ячейку: {batt_cell:.3f} В  |  "
            f"Заряд: {s.battery_charge}%"
        )

        # Круговые датчики
        batt_c = (THEME["fault"] if s.battery_charge < 20 else
                  THEME["battery"] if s.battery_charge < 40 else THEME["accent3"])
        self.gauge_batt.setValue(s.battery_charge, batt_c)

        load_c = (THEME["fault"] if s.output_load_pct > 90 else
                  THEME["battery"] if s.output_load_pct > 70 else THEME["accent3"])
        self.gauge_load.setValue(s.output_load_pct, load_c)

        temp_c = (THEME["fault"] if s.temperature >= threshold else
                  THEME["battery"] if s.temperature >= threshold - 2 else THEME["accent2"])
        self.gauge_temp.setVisible(math.isfinite(s.temperature))
        if math.isfinite(s.temperature):
            self.gauge_temp.setValue(s.temperature, temp_c)

        # Полосы
        self.bar_input_v.setValue(s.input_voltage)
        self.bar_output_v.setValue(s.output_voltage)
        self.bar_load.setValue(s.output_load_pct)
        # Динамически подстраиваем диапазон полосы под формат ИБП
        bv  = s.battery_voltage
        fmt = s.battery_format()
        if bv > 0:
            if fmt == "per_cell_2v":
                self.bar_batt_v.min_val = 1.75
                self.bar_batt_v.max_val = 2.30
                self.bar_batt_v.label   = "Батарея (В/яч.)"
                self.bar_batt_v.setValue(bv)
            elif fmt == "per_module_12v":
                self.bar_batt_v.min_val = 10.50
                self.bar_batt_v.max_val = 13.50
                self.bar_batt_v.label   = "Батарея (В/мод.)"
                self.bar_batt_v.setValue(bv)
            else:   # pack_total
                mods = max(1, round(s.rated_batt_v / 12.0))
                self.bar_batt_v.min_val = mods * 10.50
                self.bar_batt_v.max_val = mods * 13.50
                self.bar_batt_v.label   = "Напряжение батареи"
                self.bar_batt_v.setValue(bv)
        self.bar_temp.crit = threshold / self.bar_temp.max_val * 100
        self.bar_temp.warn = (threshold - 2) / self.bar_temp.max_val * 100
        self.bar_temp.setVisible(math.isfinite(s.temperature))
        if math.isfinite(s.temperature):
            self.bar_temp.setValue(s.temperature)

        # Флаги
        flag_map = {
            "utility_fail":     (s.utility_fail,     THEME["fault"]),
            "battery_low":      (s.battery_low,       THEME["battery"]),
            "bypass_active":    (s.bypass_active,     THEME["bypass"]),
            "ups_failed":       (s.ups_failed,        THEME["fault"]),
            "test_in_progress": (s.test_in_progress,  THEME["accent2"]),
            "shutdown_active":  (s.shutdown_active,   THEME["fault"]),
            "beeper_on":        (s.beeper_on,         THEME["text_sec"]),
        }
        for key, (active, color) in flag_map.items():
            lbl = self._flag_labels.get(key)
            if lbl:
                lbl.setStyleSheet(
                    f"color: {color};" if active else f"color: {THEME['bg_light']};"
                )

        # Блок-схема потока
        self.power_flow.update_status(s)

        # График
        if PYQTGRAPH_AVAILABLE:
            N = 120
            self._buf_input.append(s.input_voltage)
            self._buf_output.append(s.output_voltage)
            self._buf_load.append(s.output_load_pct * 2)  # ×2 для масштаба
            if len(self._buf_input) > N:
                self._buf_input  = self._buf_input[-N:]
                self._buf_output = self._buf_output[-N:]
                self._buf_load   = self._buf_load[-N:]
            self._curve_input.setData(self._buf_input)
            self._curve_output.setData(self._buf_output)
            self._curve_load.setData(self._buf_load)


# ═════════════════════════════════════════════════════════════════════════════
#  ВКЛАДКА: УПРАВЛЕНИЕ
# ═════════════════════════════════════════════════════════════════════════════

class ControlTab(QWidget):
    test_short      = pyqtSignal()
    test_long       = pyqtSignal()
    test_cancel     = pyqtSignal()
    shutdown_cmd    = pyqtSignal(float)
    shutdown_restore= pyqtSignal(float, int)
    cancel_shutdown = pyqtSignal()
    toggle_beeper   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # ── Тест батареи ──
        grp_test = QGroupBox("🔬  Тест батареи")
        grp_test.setStyleSheet(self._grp_style())
        tl = QVBoxLayout(grp_test)

        row1 = QHBoxLayout()
        btn_t10  = self._btn("Тест 10 секунд", THEME["accent2"])
        btn_tl   = self._btn("Тест до разряда", THEME["battery"])
        btn_tc   = self._btn("Отменить тест", THEME["text_sec"])
        btn_t10.clicked.connect(self.test_short)
        btn_tl.clicked.connect(self.test_long)
        btn_tc.clicked.connect(self.test_cancel)
        row1.addWidget(btn_t10)
        row1.addWidget(btn_tl)
        row1.addWidget(btn_tc)
        tl.addLayout(row1)

        info = QLabel("Тест 10 сек: ИБП переключается на батарею на 10 секунд.\n"
                      "Тест до разряда: работа от батареи до полного разряда.")
        info.setStyleSheet(f"color: {THEME['text_sec']}; font: 8pt 'Segoe UI';")
        tl.addWidget(info)
        root.addWidget(grp_test)

        # ── Управление выходом ──
        grp_out = QGroupBox("⏻  Управление выходом ИБП")
        grp_out.setStyleSheet(self._grp_style())
        ol = QVBoxLayout(grp_out)

        r2 = QHBoxLayout()
        lbl_delay = QLabel("Задержка (мин):")
        lbl_delay.setStyleSheet(f"color: {THEME['text_sec']};")
        self.spin_shutdown = QDoubleSpinBox()
        self.spin_shutdown.setRange(0.2, 99.9)
        self.spin_shutdown.setValue(5.0)
        self.spin_shutdown.setSingleStep(1.0)
        self.spin_shutdown.setDecimals(1)
        self.spin_shutdown.setFixedWidth(90)
        self.spin_shutdown.setStyleSheet(self._spin_style())

        btn_shut = self._btn("Выключить выход", THEME["battery"])
        btn_shut.clicked.connect(lambda: self.shutdown_cmd.emit(self.spin_shutdown.value()))
        btn_cancel = self._btn("Отменить", THEME["text_sec"])
        btn_cancel.clicked.connect(self.cancel_shutdown)

        r2.addWidget(lbl_delay)
        r2.addWidget(self.spin_shutdown)
        r2.addWidget(btn_shut)
        r2.addWidget(btn_cancel)
        r2.addStretch()
        ol.addLayout(r2)

        # Перезагрузка
        r3 = QHBoxLayout()
        lbl_off = QLabel("Откл (мин):")
        lbl_off.setStyleSheet(f"color: {THEME['text_sec']};")
        self.spin_off = QDoubleSpinBox()
        self.spin_off.setRange(0.2, 99.9)
        self.spin_off.setValue(1.0)
        self.spin_off.setDecimals(1)
        self.spin_off.setFixedWidth(80)
        self.spin_off.setStyleSheet(self._spin_style())

        lbl_on = QLabel("Вкл (мин):")
        lbl_on.setStyleSheet(f"color: {THEME['text_sec']};")
        self.spin_on = QSpinBox()
        self.spin_on.setRange(1, 9999)
        self.spin_on.setValue(2)
        self.spin_on.setFixedWidth(80)
        self.spin_on.setStyleSheet(self._spin_style())

        btn_restart = self._btn("Перезагрузить выход", THEME["accent"])
        btn_restart.clicked.connect(
            lambda: self.shutdown_restore.emit(
                self.spin_off.value(), self.spin_on.value()))

        r3.addWidget(lbl_off)
        r3.addWidget(self.spin_off)
        r3.addWidget(lbl_on)
        r3.addWidget(self.spin_on)
        r3.addWidget(btn_restart)
        r3.addStretch()
        ol.addLayout(r3)

        ol.addWidget(QLabel(
            "⚠️  Выключение выхода отключит всё оборудование, подключённое к ИБП!",
        ).also(lambda l: l.setStyleSheet(f"color:{THEME['fault']};font:8pt 'Segoe UI';")))

        root.addWidget(grp_out)

        # ── Пищалка ──
        grp_beep = QGroupBox("🔔  Звуковые уведомления")
        grp_beep.setStyleSheet(self._grp_style())
        bl = QHBoxLayout(grp_beep)
        btn_beep = self._btn("Переключить пищалку ИБП", THEME["accent2"])
        btn_beep.clicked.connect(self.toggle_beeper)
        bl.addWidget(btn_beep)
        bl.addStretch()
        root.addWidget(grp_beep)

        # ── Завершение работы ПК ──
        grp_pc = QGroupBox("💻  Завершение работы ПК")
        grp_pc.setStyleSheet(self._grp_style())
        pl = QVBoxLayout(grp_pc)

        r4 = QHBoxLayout()
        lbl_pc_d = QLabel("Задержка (сек):")
        lbl_pc_d.setStyleSheet(f"color:{THEME['text_sec']};")
        self.spin_pc_delay = QSpinBox()
        self.spin_pc_delay.setRange(10, 600)
        self.spin_pc_delay.setValue(60)
        self.spin_pc_delay.setFixedWidth(80)
        self.spin_pc_delay.setStyleSheet(self._spin_style())

        btn_pc_shut = self._btn("Завершить работу Windows", THEME["fault"])
        btn_pc_abort = self._btn("Отменить", THEME["text_sec"])
        btn_pc_shut.clicked.connect(self._pc_shutdown)
        btn_pc_abort.clicked.connect(self._pc_abort)
        r4.addWidget(lbl_pc_d)
        r4.addWidget(self.spin_pc_delay)
        r4.addWidget(btn_pc_shut)
        r4.addWidget(btn_pc_abort)
        r4.addStretch()
        pl.addLayout(r4)
        root.addWidget(grp_pc)

        root.addStretch()

    def _pc_shutdown(self):
        delay = self.spin_pc_delay.value()
        msg = f"ИБП Manager: плановое завершение работы через {delay} сек."
        cmd = f'shutdown /s /t {delay} /c "{msg}"'
        try:
            subprocess.run(cmd, shell=True, check=True)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Команда выключения не выполнена:\n{e}")

    def _pc_abort(self):
        try:
            subprocess.run("shutdown /a", shell=True)
        except Exception:
            pass

    @staticmethod
    def _grp_style():
        return f"""
            QGroupBox {{
                color: {THEME['text_sec']};
                border: 1px solid {THEME['border']};
                border-radius: 6px;
                margin-top: 8px;
                font: bold 9pt 'Segoe UI';
                background: {THEME['bg_medium']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                background: {THEME['bg_medium']};
            }}
        """

    @staticmethod
    def _btn(text, color):
        btn = QPushButton(text)
        btn.setFixedHeight(32)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {color};
                border: 1px solid {color};
                border-radius: 5px;
                padding: 0 14px;
                font: 9pt 'Segoe UI';
            }}
            QPushButton:hover {{
                background: {color}22;
            }}
            QPushButton:pressed {{
                background: {color}44;
            }}
        """)
        return btn

    @staticmethod
    def _spin_style():
        return f"""
            QSpinBox, QDoubleSpinBox {{
                background: {THEME['bg_dark']};
                color: {THEME['text_primary']};
                border: 1px solid {THEME['border']};
                border-radius: 4px;
                padding: 2px 6px;
                font: 10pt 'Consolas';
            }}
        """


# Monkey-patch для удобства
def _also(self, fn):
    fn(self)
    return self
QLabel.also = _also


# ═════════════════════════════════════════════════════════════════════════════
#  ВКЛАДКА: НАСТРОЙКИ
# ═════════════════════════════════════════════════════════════════════════════

class SettingsTab(QWidget):
    config_changed = pyqtSignal(dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self._setup_ui()

    def _setup_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: {THEME['bg_dark']};
            }}
            QScrollArea > QWidget > QWidget {{
                background: {THEME['bg_dark']};
            }}
            QScrollBar:vertical {{
                background: {THEME['bg_medium']};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {THEME['border']};
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {THEME['text_sec']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

        inner = QWidget()
        inner.setStyleSheet(f"background: {THEME['bg_dark']};")
        root = QVBoxLayout(inner)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        gs = ControlTab._grp_style()

        # ── Подключение ──
        grp_conn = QGroupBox("🔌  Подключение к ИБП")
        grp_conn.setStyleSheet(gs)
        gl = QGridLayout(grp_conn)
        gl.setSpacing(8)
        gl.setContentsMargins(12, 16, 12, 12)

        lbl_type = QLabel("Тип интерфейса:")
        lbl_type.setStyleSheet(f"color:{THEME['text_sec']};")
        self.cb_type = QComboBox()
        self.cb_type.addItems(["RS232 / USB (Megatec Q1)", "SNMP"])
        self.cb_type.setStyleSheet(self._combo_style())
        self.cb_type.currentIndexChanged.connect(self._on_type_changed)

        lbl_port = QLabel("COM порт:")
        lbl_port.setStyleSheet(f"color:{THEME['text_sec']};")
        self.cb_port = QComboBox()
        self.cb_port.setStyleSheet(self._combo_style())
        self._refresh_ports()

        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedSize(32, 28)
        btn_refresh.setStyleSheet(f"""
            QPushButton {{ background:{THEME['bg_dark']}; color:{THEME['accent2']};
                           border:1px solid {THEME['border']}; border-radius:4px; }}
            QPushButton:hover {{ background:{THEME['accent2']}22; }}
        """)
        btn_refresh.clicked.connect(self._refresh_ports)
        btn_refresh.setToolTip("Обновить список портов")

        lbl_poll = QLabel("Интервал опроса (мс):")
        lbl_poll.setStyleSheet(f"color:{THEME['text_sec']};")
        self.spin_poll = QSpinBox()
        self.spin_poll.setRange(500, 30000)
        self.spin_poll.setValue(self.config["connection"]["poll_interval"])
        self.spin_poll.setSingleStep(500)
        self.spin_poll.setStyleSheet(ControlTab._spin_style())

        # SNMP поля
        lbl_host = QLabel("SNMP хост/IP:")
        lbl_host.setStyleSheet(f"color:{THEME['text_sec']};")
        self.ed_snmp_host = QLineEdit(self.config["connection"]["snmp_host"])
        self.ed_snmp_host.setStyleSheet(self._edit_style())

        lbl_comm = QLabel("SNMP Community:")
        lbl_comm.setStyleSheet(f"color:{THEME['text_sec']};")
        self.ed_snmp_comm = QLineEdit(self.config["connection"]["snmp_community"])
        self.ed_snmp_comm.setStyleSheet(self._edit_style())

        gl.addWidget(lbl_type, 0, 0); gl.addWidget(self.cb_type,  0, 1, 1, 2)
        gl.addWidget(lbl_port, 1, 0); gl.addWidget(self.cb_port,  1, 1)
        gl.addWidget(btn_refresh,     1, 2)
        gl.addWidget(lbl_poll, 2, 0); gl.addWidget(self.spin_poll, 2, 1)
        gl.addWidget(lbl_host, 3, 0); gl.addWidget(self.ed_snmp_host, 3, 1, 1, 2)
        gl.addWidget(lbl_comm, 4, 0); gl.addWidget(self.ed_snmp_comm, 4, 1, 1, 2)

        # Кнопка освобождения занятого порта
        self.btn_kill_port = QPushButton("🔓  Освободить порт")
        self.btn_kill_port.setFixedHeight(28)
        self.btn_kill_port.setCursor(Qt.PointingHandCursor)
        self.btn_kill_port.setToolTip(
            "Завершить процессы, удерживающие выбранный COM-порт.\n"
            "Для завершения системных процессов требуются права Администратора.")
        self.btn_kill_port.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['battery']}22;
                color: {THEME['battery']};
                border: 1px solid {THEME['battery']}88;
                border-radius: 4px;
                padding: 0 12px;
                font: 9pt 'Segoe UI';
            }}
            QPushButton:hover  {{ background: {THEME['battery']}44; }}
            QPushButton:pressed {{ background: {THEME['battery']}66; }}
        """)
        self.btn_kill_port.clicked.connect(self._kill_port)

        kill_row = QHBoxLayout()
        kill_row.addWidget(self.btn_kill_port)

        # Индикатор прав администратора
        is_admin = PortKiller.is_admin()
        lbl_admin = QLabel("🛡 Администратор" if is_admin else "⚠ Не администратор")
        lbl_admin.setStyleSheet(
            f"color: {THEME['accent3'] if is_admin else THEME['battery']};"
            f"font: 8pt 'Segoe UI';")
        lbl_admin.setToolTip(
            "Для завершения чужих процессов нужны права администратора.\n"
            "Перезапустите программу через ПКМ → «Запуск от имени администратора»."
            if not is_admin else
            "Программа запущена с правами администратора.")
        kill_row.addWidget(lbl_admin)
        kill_row.addStretch()
        gl.addLayout(kill_row, 5, 0, 1, 3)

        root.addWidget(grp_conn)

        # ── Автозагрузка Windows ──
        grp_auto = QGroupBox("🚀  Автозагрузка Windows")
        grp_auto.setStyleSheet(gs)
        aut_l = QVBoxLayout(grp_auto)
        aut_l.setContentsMargins(12, 16, 12, 12)
        aut_l.setSpacing(8)

        # Статус
        autorun_active = AutostartManager.is_enabled()
        self.lbl_autorun_state = QLabel()
        self._refresh_autorun_label()
        aut_l.addWidget(self.lbl_autorun_state)

        # Текущая команда из реестра (мелким шрифтом)
        self.lbl_autorun_cmd = QLabel()
        self.lbl_autorun_cmd.setStyleSheet(
            f"color:{THEME['text_sec']}; font:8pt 'Consolas';")
        self.lbl_autorun_cmd.setWordWrap(True)
        self._refresh_autorun_cmd_label()
        aut_l.addWidget(self.lbl_autorun_cmd)

        # Опция «запускать свёрнутым»
        self.chk_autorun_minimized = QCheckBox(
            "Запускать свёрнутым в системный трей")
        self.chk_autorun_minimized.setChecked(True)
        self.chk_autorun_minimized.setStyleSheet(f"color:{THEME['text_primary']};")
        self.chk_autorun_minimized.setToolTip(
            "При запуске Windows программа появится только в трее, "
            "не открывая окно.\nДвойной клик на иконке — открыть окно.")
        aut_l.addWidget(self.chk_autorun_minimized)

        # Кнопки
        btn_row_ar = QHBoxLayout()
        self.btn_autorun_on = QPushButton("✅  Добавить в автозагрузку")
        self.btn_autorun_on.setFixedHeight(30)
        self.btn_autorun_on.setCursor(Qt.PointingHandCursor)
        self.btn_autorun_on.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['accent3']}22;
                color: {THEME['accent3']};
                border: 1px solid {THEME['accent3']};
                border-radius: 5px;
                padding: 0 14px;
                font: 9pt 'Segoe UI';
            }}
            QPushButton:hover {{ background:{THEME['accent3']}44; }}
            QPushButton:disabled {{ color:{THEME['text_sec']}; border-color:{THEME['border']}; background:transparent; }}
        """)
        self.btn_autorun_on.clicked.connect(self._autorun_enable)

        self.btn_autorun_off = QPushButton("🗑  Удалить из автозагрузки")
        self.btn_autorun_off.setFixedHeight(30)
        self.btn_autorun_off.setCursor(Qt.PointingHandCursor)
        self.btn_autorun_off.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['fault']}18;
                color: {THEME['fault']};
                border: 1px solid {THEME['fault']}88;
                border-radius: 5px;
                padding: 0 14px;
                font: 9pt 'Segoe UI';
            }}
            QPushButton:hover {{ background:{THEME['fault']}33; }}
            QPushButton:disabled {{ color:{THEME['text_sec']}; border-color:{THEME['border']}; background:transparent; }}
        """)
        self.btn_autorun_off.clicked.connect(self._autorun_disable)

        # Состояние кнопок зависит от текущего статуса
        self.btn_autorun_on.setEnabled(not autorun_active)
        self.btn_autorun_off.setEnabled(autorun_active)

        btn_row_ar.addWidget(self.btn_autorun_on)
        btn_row_ar.addWidget(self.btn_autorun_off)
        btn_row_ar.addStretch()
        aut_l.addLayout(btn_row_ar)

        # Примечание про .exe
        if not getattr(sys, 'frozen', False):
            note_exe = QLabel(
                "⚠  Вы запускаете .py файл. Для надёжной автозагрузки рекомендуется "
                "собрать .exe с помощью прилагаемого build.bat и добавить в "
                "автозагрузку уже исполняемый файл.")
            note_exe.setStyleSheet(f"color:{THEME['battery']}; font:8pt 'Segoe UI';")
            note_exe.setWordWrap(True)
            aut_l.addWidget(note_exe)

        root.addWidget(grp_auto)
        grp_sd = QGroupBox("⚙️  Автоматическое завершение работы")
        grp_sd.setStyleSheet(gs)
        sdl = QGridLayout(grp_sd)
        sdl.setSpacing(8)
        sdl.setContentsMargins(12, 16, 12, 12)

        # ── Блок 1: завершение при переходе на батарею (новый) ──
        sep_new = QLabel("▶  При переходе на батарею (сразу по факту переключения)")
        sep_new.setStyleSheet(f"color:{THEME['accent2']}; font: bold 9pt 'Segoe UI';")
        sdl.addWidget(sep_new, 0, 0, 1, 3)

        self.chk_bs_enabled = QCheckBox(
            "Завершить работу компьютера при переходе ИБП на батарею")
        self.chk_bs_enabled.setChecked(
            self.config["shutdown"].get("on_battery_switch", False))
        self.chk_bs_enabled.setStyleSheet(f"color:{THEME['text_primary']};")
        sdl.addWidget(self.chk_bs_enabled, 1, 0, 1, 3)

        lbl_bs_delay = QLabel("Задержка перед завершением:")
        lbl_bs_delay.setStyleSheet(f"color:{THEME['text_sec']};")

        # SpinBox в секундах с более удобным шагом
        self.spin_bs_delay = QSpinBox()
        self.spin_bs_delay.setRange(0, 3600)
        self.spin_bs_delay.setValue(
            self.config["shutdown"].get("on_battery_switch_delay_sec", 120))
        self.spin_bs_delay.setSingleStep(30)
        self.spin_bs_delay.setSuffix(" сек")
        self.spin_bs_delay.setFixedWidth(110)
        self.spin_bs_delay.setStyleSheet(ControlTab._spin_style())
        self.spin_bs_delay.setToolTip(
            "0 = немедленно.\n"
            "Рекомендуется не менее 30–60 сек, чтобы избежать\n"
            "ложного срабатывания при кратковременных перебоях.")

        # Живое отображение «= X мин Y сек» рядом с полем
        self.lbl_bs_delay_fmt = QLabel()
        self.lbl_bs_delay_fmt.setStyleSheet(f"color:{THEME['text_sec']}; font:9pt 'Consolas';")
        self.spin_bs_delay.valueChanged.connect(
            lambda v: self.lbl_bs_delay_fmt.setText(f"= {_fmt_sec(v)}"))
        self.lbl_bs_delay_fmt.setText(
            f"= {_fmt_sec(self.spin_bs_delay.value())}")

        sdl.addWidget(lbl_bs_delay,         2, 0)
        sdl.addWidget(self.spin_bs_delay,   2, 1)
        sdl.addWidget(self.lbl_bs_delay_fmt,2, 2)

        # Пояснение
        note_bs = QLabel(
            "ℹ  Питание восстановилось в период ожидания — завершение отменяется автоматически.")
        note_bs.setStyleSheet(f"color:{THEME['text_sec']}; font:8pt 'Segoe UI';")
        note_bs.setWordWrap(True)
        sdl.addWidget(note_bs, 3, 0, 1, 3)

        # Разделитель
        line = QFrame(); line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color:{THEME['border']};")
        sdl.addWidget(line, 4, 0, 1, 3)

        # ── Блок 2: завершение по уровню заряда (старый) ──
        sep_old = QLabel("▶  По уровню заряда батареи (при уже идущей работе от батареи)")
        sep_old.setStyleSheet(f"color:{THEME['text_sec']}; font: bold 9pt 'Segoe UI';")
        sdl.addWidget(sep_old, 5, 0, 1, 3)

        self.chk_sd_enabled = QCheckBox("Завершить работу при заряде батареи ниже порога")
        self.chk_sd_enabled.setChecked(self.config["shutdown"]["enabled"])
        self.chk_sd_enabled.setStyleSheet(f"color:{THEME['text_primary']};")
        sdl.addWidget(self.chk_sd_enabled, 6, 0, 1, 3)

        lbl_sd_low = QLabel("Порог заряда (%):")
        lbl_sd_low.setStyleSheet(f"color:{THEME['text_sec']};")
        self.spin_sd_low = QSpinBox()
        self.spin_sd_low.setRange(5, 80)
        self.spin_sd_low.setValue(self.config["shutdown"]["battery_low_percent"])
        self.spin_sd_low.setSuffix(" %")
        self.spin_sd_low.setStyleSheet(ControlTab._spin_style())

        lbl_sd_delay = QLabel("Задержка (мин):")
        lbl_sd_delay.setStyleSheet(f"color:{THEME['text_sec']};")
        self.spin_sd_delay = QSpinBox()
        self.spin_sd_delay.setRange(1, 60)
        self.spin_sd_delay.setValue(self.config["shutdown"]["delay_minutes"])
        self.spin_sd_delay.setSuffix(" мин")
        self.spin_sd_delay.setStyleSheet(ControlTab._spin_style())

        lbl_sd_warn = QLabel("Предупреждать за:")
        lbl_sd_warn.setStyleSheet(f"color:{THEME['text_sec']};")
        self.spin_sd_warn = QSpinBox()
        self.spin_sd_warn.setRange(10, 300)
        self.spin_sd_warn.setValue(self.config["shutdown"]["warn_before_sec"])
        self.spin_sd_warn.setSuffix(" сек")
        self.spin_sd_warn.setStyleSheet(ControlTab._spin_style())

        sdl.addWidget(lbl_sd_low,   7, 0); sdl.addWidget(self.spin_sd_low,   7, 1)
        sdl.addWidget(lbl_sd_delay, 8, 0); sdl.addWidget(self.spin_sd_delay, 8, 1)
        sdl.addWidget(lbl_sd_warn,  9, 0); sdl.addWidget(self.spin_sd_warn,  9, 1)
        root.addWidget(grp_sd)

        # ── Уведомления ──
        grp_alr = QGroupBox("🔔  Уведомления и предупреждения")
        grp_alr.setStyleSheet(gs)
        al = QGridLayout(grp_alr)
        al.setContentsMargins(12, 16, 12, 12)

        self.chk_al_batt  = QCheckBox("Уведомлять при низком заряде батареи")
        self.chk_al_fail  = QCheckBox("Уведомлять при сбое сетевого питания")
        self.chk_al_temp  = QCheckBox("Уведомлять при высокой температуре")
        self.chk_al_sound = QCheckBox("Звуковые уведомления")
        self.chk_al_popup = QCheckBox("Всплывающие окна (Windows)")
        self.chk_al_log   = QCheckBox("Запись событий в лог-файл")

        for c, key in [(self.chk_al_batt,  "battery_low"),
                       (self.chk_al_fail,  "utility_fail"),
                       (self.chk_al_temp,  "temp_high"),
                       (self.chk_al_sound, "sound"),
                       (self.chk_al_popup, "popup"),
                       (self.chk_al_log,   "log_file")]:
            c.setChecked(self.config["alerts"].get(key, True))
            c.setStyleSheet(f"color:{THEME['text_primary']};")

        lbl_temp_thr = QLabel("Порог температуры (°C):")
        lbl_temp_thr.setStyleSheet(f"color:{THEME['text_sec']};")
        self.spin_temp_thr = QDoubleSpinBox()
        self.spin_temp_thr.setRange(30.0, 70.0)
        self.spin_temp_thr.setValue(self.config["alerts"]["temp_threshold"])
        self.spin_temp_thr.setStyleSheet(ControlTab._spin_style())

        for i, w in enumerate([self.chk_al_batt, self.chk_al_fail, self.chk_al_temp,
                                self.chk_al_sound, self.chk_al_popup, self.chk_al_log]):
            al.addWidget(w, i, 0, 1, 2)
        al.addWidget(lbl_temp_thr,     6, 0)
        al.addWidget(self.spin_temp_thr, 6, 1)
        root.addWidget(grp_alr)

        # Кнопки сохранения
        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾  Сохранить настройки")
        btn_save.setFixedHeight(36)
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['accent3']};
                color: {THEME['bg_dark']};
                border: none;
                border-radius: 6px;
                font: bold 10pt 'Segoe UI';
                padding: 0 20px;
            }}
            QPushButton:hover {{ background: #4fc660; }}
        """)
        btn_save.clicked.connect(self._save)

        btn_defaults = QPushButton("Сбросить")
        btn_defaults.setFixedHeight(36)
        btn_defaults.setCursor(Qt.PointingHandCursor)
        btn_defaults.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {THEME['text_sec']};
                border: 1px solid {THEME['border']};
                border-radius: 6px;
                padding: 0 14px;
            }}
            QPushButton:hover {{ background: {THEME['bg_light']}; }}
        """)
        btn_defaults.clicked.connect(self._reset)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_defaults)
        btn_row.addStretch()
        root.addLayout(btn_row)
        root.addStretch()

        scroll.setWidget(inner)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        self._on_type_changed(self.cb_type.currentIndex())

    def _kill_port(self):
        """Освободить занятый COM-порт — завершить удерживающие его процессы."""
        port = self.cb_port.currentText().strip()
        if not port:
            QMessageBox.warning(self, "Освобождение порта", "Выберите порт в списке.")
            return

        if not PortKiller.is_admin():
            ans = QMessageBox.question(
                self, "Права администратора",
                f"Порт {port} занят системным драйвером.\n\n"
                "Для надёжного освобождения (PnP Device Restart) нужны права "
                "администратора.\n\n"
                "Без прав попробуем остановить службы и использовать handle.exe.\n\n"
                "Попробовать?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if ans == QMessageBox.No:
                return

        self.btn_kill_port.setEnabled(False)
        self.btn_kill_port.setText("⏳  Освобождение...")

        import threading
        result_box = [None, None]

        def _run():
            result_box[0], result_box[1] = PortKiller.free_port(port)

        def _done():
            self.btn_kill_port.setEnabled(True)
            self.btn_kill_port.setText("🔓  Освободить порт")
            freed, log = result_box

            msg = QMessageBox(self)
            msg.setWindowTitle("Освобождение порта")
            if freed:
                msg.setIcon(QMessageBox.Information)
                msg.setText(
                    f"✅ Порт {port} успешно освобождён.\n\n"
                    "Программа автоматически попробует подключиться.")
                msg.setDetailedText(log.replace("\\n", "\n"))
                msg.exec_()
                main_win = self.window()
                if hasattr(main_win, '_manual_reconnect'):
                    main_win._manual_reconnect()
            else:
                msg.setIcon(QMessageBox.Warning)
                is_admin = PortKiller.is_admin()
                if not is_admin:
                    msg.setText(
                        f"⚠ Порт {port} не удалось освободить без прав администратора.\n\n"
                        "Самый надёжный метод (PnP Device Restart) требует "
                        "прав администратора.\n\n"
                        "Перезапустите программу:\n"
                        "ПКМ на EXE → «Запуск от имени администратора»")
                else:
                    msg.setText(
                        f"⚠ Порт {port} не удалось освободить автоматически.\n\n"
                        "Ручные действия:\n"
                        "1. Диспетчер устройств → COM-порты → ПКМ → Отключить → Включить\n"
                        "2. Физически переподключите USB-кабель ИБП\n"
                        "3. Перезагрузите компьютер")
                msg.setDetailedText(log.replace("\\n", "\n"))
                msg.exec_()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        def _check():
            if t.is_alive():
                QTimer.singleShot(200, _check)
            else:
                _done()
        QTimer.singleShot(200, _check)

    def _refresh_autorun_label(self):
        active = AutostartManager.is_enabled()
        if active:
            self.lbl_autorun_state.setText("🟢  Программа добавлена в автозагрузку")
            self.lbl_autorun_state.setStyleSheet(
                f"color:{THEME['accent3']}; font: bold 9pt 'Segoe UI';")
        else:
            self.lbl_autorun_state.setText("⚫  Программа не в автозагрузке")
            self.lbl_autorun_state.setStyleSheet(
                f"color:{THEME['text_sec']}; font: 9pt 'Segoe UI';")

    def _refresh_autorun_cmd_label(self):
        cmd = AutostartManager.current_value()
        if cmd:
            self.lbl_autorun_cmd.setText(f"Команда: {cmd}")
        else:
            self.lbl_autorun_cmd.setText("")

    def _autorun_enable(self):
        minimized = self.chk_autorun_minimized.isChecked()
        ok, msg = AutostartManager.enable(minimized=minimized)
        if ok:
            QMessageBox.information(self, "Автозагрузка", f"✅ {msg}")
        else:
            QMessageBox.warning(self, "Ошибка автозагрузки", f"❌ {msg}")
        self._refresh_autorun_label()
        self._refresh_autorun_cmd_label()
        self.btn_autorun_on.setEnabled(not AutostartManager.is_enabled())
        self.btn_autorun_off.setEnabled(AutostartManager.is_enabled())

    def _autorun_disable(self):
        ok, msg = AutostartManager.disable()
        if ok:
            QMessageBox.information(self, "Автозагрузка", f"✅ {msg}")
        else:
            QMessageBox.warning(self, "Ошибка автозагрузки", f"❌ {msg}")
        self._refresh_autorun_label()
        self._refresh_autorun_cmd_label()
        self.btn_autorun_on.setEnabled(not AutostartManager.is_enabled())
        self.btn_autorun_off.setEnabled(AutostartManager.is_enabled())

    def _on_type_changed(self, idx):
        is_serial = (idx == 0)
        self.cb_port.setVisible(is_serial)
        self.ed_snmp_host.setVisible(not is_serial)
        self.ed_snmp_comm.setVisible(not is_serial)

    def _refresh_ports(self):
        self.cb_port.clear()
        if SERIAL_AVAILABLE:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            self.cb_port.addItems(ports or ["COM1"])
            cur = self.config["connection"]["port"]
            idx = self.cb_port.findText(cur)
            if idx >= 0:
                self.cb_port.setCurrentIndex(idx)
        else:
            for i in range(1, 9):
                self.cb_port.addItem(f"COM{i}")

    def _save(self):
        self.config["connection"]["type"]          = ("serial" if self.cb_type.currentIndex() == 0
                                                      else "snmp")
        self.config["connection"]["port"]          = self.cb_port.currentText()
        self.config["connection"]["poll_interval"] = self.spin_poll.value()
        self.config["connection"]["snmp_host"]     = self.ed_snmp_host.text()
        self.config["connection"]["snmp_community"]= self.ed_snmp_comm.text()

        self.config["shutdown"]["enabled"]                  = self.chk_sd_enabled.isChecked()
        self.config["shutdown"]["battery_low_percent"]       = self.spin_sd_low.value()
        self.config["shutdown"]["delay_minutes"]             = self.spin_sd_delay.value()
        self.config["shutdown"]["warn_before_sec"]           = self.spin_sd_warn.value()
        self.config["shutdown"]["on_battery_switch"]         = self.chk_bs_enabled.isChecked()
        self.config["shutdown"]["on_battery_switch_delay_sec"] = self.spin_bs_delay.value()

        self.config["alerts"]["battery_low"]    = self.chk_al_batt.isChecked()
        self.config["alerts"]["utility_fail"]   = self.chk_al_fail.isChecked()
        self.config["alerts"]["temp_high"]      = self.chk_al_temp.isChecked()
        self.config["alerts"]["sound"]          = self.chk_al_sound.isChecked()
        self.config["alerts"]["popup"]          = self.chk_al_popup.isChecked()
        self.config["alerts"]["log_file"]       = self.chk_al_log.isChecked()
        self.config["alerts"]["temp_threshold"] = self.spin_temp_thr.value()

        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            self.config_changed.emit(copy.deepcopy(self.config))
            QMessageBox.information(self, "Настройки", "Настройки сохранены.")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить: {e}")

    def _reset(self):
        if QMessageBox.question(self, "Сброс", "Сбросить настройки к значениям по умолчанию?") != QMessageBox.Yes:
            return
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        conn, sd, alerts = (self.config[key] for key in ("connection", "shutdown", "alerts"))
        self.cb_type.setCurrentIndex(0)
        if self.cb_port.findText(conn["port"]) < 0:
            self.cb_port.addItem(conn["port"])
        self.cb_port.setCurrentText(conn["port"])
        self.spin_poll.setValue(conn["poll_interval"])
        self.ed_snmp_host.setText(conn["snmp_host"])
        self.ed_snmp_comm.setText(conn["snmp_community"])
        for widget, key in ((self.chk_sd_enabled, "enabled"), (self.chk_bs_enabled, "on_battery_switch")):
            widget.setChecked(sd[key])
        for widget, key in ((self.spin_sd_low, "battery_low_percent"), (self.spin_sd_delay, "delay_minutes"),
                            (self.spin_sd_warn, "warn_before_sec"), (self.spin_bs_delay, "on_battery_switch_delay_sec")):
            widget.setValue(sd[key])
        for widget, key in ((self.chk_al_batt, "battery_low"), (self.chk_al_fail, "utility_fail"),
                            (self.chk_al_temp, "temp_high"), (self.chk_al_sound, "sound"),
                            (self.chk_al_popup, "popup"), (self.chk_al_log, "log_file")):
            widget.setChecked(alerts[key])
        self.spin_temp_thr.setValue(alerts["temp_threshold"])
        self._save()

    @staticmethod
    def _combo_style():
        return f"""
            QComboBox {{
                background: {THEME['bg_dark']};
                color: {THEME['text_primary']};
                border: 1px solid {THEME['border']};
                border-radius: 4px;
                padding: 3px 8px;
                font: 9pt 'Segoe UI';
            }}
            QComboBox QAbstractItemView {{
                background: {THEME['bg_medium']};
                color: {THEME['text_primary']};
                selection-background-color: {THEME['accent2']}55;
                border: 1px solid {THEME['border']};
            }}
        """

    @staticmethod
    def _edit_style():
        return f"""
            QLineEdit {{
                background: {THEME['bg_dark']};
                color: {THEME['text_primary']};
                border: 1px solid {THEME['border']};
                border-radius: 4px;
                padding: 3px 8px;
                font: 9pt 'Consolas';
            }}
        """


# ═════════════════════════════════════════════════════════════════════════════
#  ВКЛАДКА: ЖУРНАЛ СОБЫТИЙ
# ═════════════════════════════════════════════════════════════════════════════

class EventLogTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: List[EventRecord] = []
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Панель инструментов
        toolbar = QHBoxLayout()
        lbl = QLabel("Журнал событий ИБП")
        lbl.setStyleSheet(f"color:{THEME['text_primary']}; font: bold 10pt 'Segoe UI';")
        toolbar.addWidget(lbl)
        toolbar.addStretch()

        for text, fn in [("🗑  Очистить", self._clear),
                         ("💾  Экспорт CSV", self._export_csv)]:
            btn = QPushButton(text)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {THEME['text_sec']};
                    border: 1px solid {THEME['border']};
                    border-radius: 4px;
                    padding: 0 10px;
                    font: 9pt 'Segoe UI';
                }}
                QPushButton:hover {{ background:{THEME['bg_light']}; }}
            """)
            btn.clicked.connect(fn)
            toolbar.addWidget(btn)

        root.addLayout(toolbar)

        # Таблица
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Время", "Уровень", "Сообщение"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background: {THEME['bg_medium']};
                alternate-background-color: {THEME['bg_light']};
                color: {THEME['text_primary']};
                border: 1px solid {THEME['border']};
                border-radius: 6px;
                gridline-color: {THEME['border']};
                font: 9pt 'Segoe UI';
            }}
            QTableWidget::item:selected {{
                background: {THEME['accent2']}44;
            }}
            QHeaderView::section {{
                background: {THEME['bg_dark']};
                color: {THEME['text_sec']};
                border: none;
                border-bottom: 1px solid {THEME['border']};
                padding: 4px;
                font: bold 8pt 'Segoe UI';
            }}
        """)
        root.addWidget(self.table)

        # Фильтр
        filter_row = QHBoxLayout()
        lbl_f = QLabel("Фильтр:")
        lbl_f.setStyleSheet(f"color:{THEME['text_sec']};")
        self.filter_ed = QLineEdit()
        self.filter_ed.setPlaceholderText("Поиск по тексту...")
        self.filter_ed.setStyleSheet(SettingsTab._edit_style())
        self.filter_ed.textChanged.connect(self._apply_filter)
        filter_row.addWidget(lbl_f)
        filter_row.addWidget(self.filter_ed)
        root.addLayout(filter_row)

    def add_event(self, event: EventRecord, log_file: bool = True):
        self._events.append(event)
        self._add_row(event)
        # CSV лог
        if not log_file:
            return
        try:
            with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow([event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            event.level, event.message])
        except Exception:
            pass

    def _add_row(self, event: EventRecord):
        row = self.table.rowCount()
        self.table.insertRow(row)

        ts = QTableWidgetItem(event.timestamp.strftime("%d.%m.%Y %H:%M:%S"))
        ts.setForeground(QColor(THEME["text_sec"]))

        level_colors = {
            "INFO":     THEME["accent3"],
            "WARNING":  THEME["battery"],
            "CRITICAL": THEME["fault"],
            "ACTION":   THEME["accent2"],
        }
        lvl = QTableWidgetItem(event.level)
        lvl.setForeground(QColor(level_colors.get(event.level, THEME["text_sec"])))

        msg = QTableWidgetItem(event.message)
        msg.setForeground(QColor(THEME["text_primary"]))

        self.table.setItem(row, 0, ts)
        self.table.setItem(row, 1, lvl)
        self.table.setItem(row, 2, msg)
        self.table.scrollToBottom()

    def _apply_filter(self, text):
        text = text.lower()
        for row in range(self.table.rowCount()):
            visible = not text or any(
                text in (self.table.item(row, col).text().lower() if self.table.item(row, col) else "")
                for col in range(3)
            )
            self.table.setRowHidden(row, not visible)

    def _clear(self):
        r = QMessageBox.question(self, "Очистить", "Очистить журнал событий?")
        if r == QMessageBox.Yes:
            self.table.setRowCount(0)
            self._events.clear()

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт журнала", "ups_events.csv",
            "CSV файлы (*.csv)")
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f, delimiter=';')
                w.writerow(["Время", "Уровень", "Сообщение"])
                for ev in self._events:
                    w.writerow([ev.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                                ev.level, ev.message])
            QMessageBox.information(self, "Экспорт", f"Сохранено: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", str(e))


# ═════════════════════════════════════════════════════════════════════════════
#  ВКЛАДКА: SNMP
# ═════════════════════════════════════════════════════════════════════════════

class SNMPQueryThread(QThread):
    result_ready = pyqtSignal(str)

    def __init__(self, host, community, oid, port=161, parent=None):
        super().__init__(parent)
        self.args = (host, community, [oid], port)

    def run(self):
        try:
            result = snmp_get(*self.args)
            self.result_ready.emit("\n".join(f"{key} = {value}" for key, value in result.items()))
        except Exception as exc:
            self.result_ready.emit(f"Ошибка SNMP: {exc}")


class SNMPTab(QWidget):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        gs = ControlTab._grp_style()

        # Информация
        info_frame = QFrame()
        info_frame.setStyleSheet(f"""
            QFrame {{
                background: {THEME['bg_medium']};
                border: 1px solid {THEME['accent2']}44;
                border-radius: 6px;
            }}
        """)
        il = QVBoxLayout(info_frame)
        il.setContentsMargins(12, 10, 12, 10)
        lbl_info = QLabel(
            f"{'✅' if SNMP_AVAILABLE else '❌'}  "
            f"Библиотека pysnmp {'установлена' if SNMP_AVAILABLE else 'не найдена'}.\n"
            "SNMP-агент ИБП ExeGate PowerExpert поддерживает стандартный UPS MIB (RFC 1628).\n"
            "Для использования SNMP подключите ИБП к сети через SNMP-адаптер."
        )
        lbl_info.setStyleSheet(f"color:{THEME['text_primary']}; font: 9pt 'Segoe UI';")
        lbl_info.setWordWrap(True)
        il.addWidget(lbl_info)
        root.addWidget(info_frame)

        # Параметры SNMP
        grp = QGroupBox("📡  SNMP запрос (ручной)")
        grp.setStyleSheet(gs)
        gl = QGridLayout(grp)
        gl.setContentsMargins(12, 16, 12, 12)
        gl.setSpacing(8)

        for i, (lbl_text, attr, default) in enumerate([
            ("Хост / IP:",    "ed_host",   self.config["connection"]["snmp_host"]),
            ("Community:",     "ed_comm",   self.config["connection"]["snmp_community"]),
            ("OID:",           "ed_oid",    "1.3.6.1.2.1.33.1.2.4.0"),
        ]):
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(f"color:{THEME['text_sec']};")
            ed  = QLineEdit(default)
            ed.setStyleSheet(SettingsTab._edit_style())
            setattr(self, attr, ed)
            gl.addWidget(lbl, i, 0)
            gl.addWidget(ed,  i, 1)

        btn_query = QPushButton("Выполнить SNMP Get")
        btn_query.setFixedHeight(32)
        btn_query.setCursor(Qt.PointingHandCursor)
        btn_query.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['accent2']}22;
                color: {THEME['accent2']};
                border: 1px solid {THEME['accent2']};
                border-radius: 5px;
                font: 9pt 'Segoe UI';
            }}
            QPushButton:hover {{ background:{THEME['accent2']}44; }}
        """)
        btn_query.clicked.connect(self._do_snmp_get)
        gl.addWidget(btn_query, 3, 0, 1, 2)
        root.addWidget(grp)

        # Результат
        grp2 = QGroupBox("Ответ SNMP агента")
        grp2.setStyleSheet(gs)
        gl2 = QVBoxLayout(grp2)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setStyleSheet(f"""
            QTextEdit {{
                background: {THEME['bg_dark']};
                color: {THEME['accent3']};
                border: 1px solid {THEME['border']};
                border-radius: 4px;
                font: 10pt 'Consolas';
            }}
        """)
        self.result_text.setFixedHeight(180)
        gl2.addWidget(self.result_text)
        root.addWidget(grp2)

        # Стандартные OID
        grp3 = QGroupBox("📋  Стандартные OID (RFC 1628 UPS MIB)")
        grp3.setStyleSheet(gs)
        gl3 = QVBoxLayout(grp3)
        oid_table = QTableWidget(len([
            ("Заряд батареи (%)",       SNMP_OID_BATT_CHARGE),
            ("Входное напряжение (В)",  SNMP_OID_INPUT_VOLTAGE),
            ("Выходное напряжение (В)", SNMP_OID_OUTPUT_VOLTAGE),
            ("Нагрузка (%)",           SNMP_OID_LOAD_PERCENT),
            ("Время автономии (мин)",  SNMP_OID_BATT_RUNTIME),
            ("Число аварий",           SNMP_OID_ALARMS_PRESENT),
        ]), 2)
        oid_table.setHorizontalHeaderLabels(["Параметр", "OID"])
        oid_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        oid_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        oid_table.setEditTriggers(QTableWidget.NoEditTriggers)
        oid_table.verticalHeader().setVisible(False)
        oid_table.setStyleSheet(EventLogTab(None).table.styleSheet() if False else f"""
            QTableWidget {{
                background: {THEME['bg_medium']};
                color: {THEME['text_primary']};
                border: 1px solid {THEME['border']};
                border-radius: 4px;
                font: 9pt 'Consolas';
                gridline-color: {THEME['border']};
            }}
            QHeaderView::section {{
                background: {THEME['bg_dark']};
                color: {THEME['text_sec']};
                border: none;
                border-bottom: 1px solid {THEME['border']};
                padding: 4px;
                font: bold 8pt 'Segoe UI';
            }}
        """)
        for i, (name, oid) in enumerate([
            ("Заряд батареи (%)",       SNMP_OID_BATT_CHARGE),
            ("Входное напряжение (В)",  SNMP_OID_INPUT_VOLTAGE),
            ("Выходное напряжение (В)", SNMP_OID_OUTPUT_VOLTAGE),
            ("Нагрузка (%)",           SNMP_OID_LOAD_PERCENT),
            ("Время автономии (мин)",  SNMP_OID_BATT_RUNTIME),
            ("Число аварий",           SNMP_OID_ALARMS_PRESENT),
        ]):
            oid_table.setItem(i, 0, QTableWidgetItem(name))
            item = QTableWidgetItem(oid)
            item.setForeground(QColor(THEME["accent2"]))
            oid_table.setItem(i, 1, item)
            oid_table.item(i, 0).setForeground(QColor(THEME["text_sec"]))
        oid_table.setFixedHeight(170)
        gl3.addWidget(oid_table)
        root.addWidget(grp3)
        root.addStretch()

    def _do_snmp_get(self):
        if getattr(self, "_worker", None) is not None and self._worker.isRunning():
            return
        if not SNMP_AVAILABLE:
            self.result_text.setPlainText("SNMP недоступен: установите pysnmp>=7.1,<8")
            return
        self.result_text.setPlainText("Выполняется SNMP-запрос…")
        self._worker = SNMPQueryThread(self.ed_host.text(), self.ed_comm.text(),
            self.ed_oid.text(), self.config["connection"].get("snmp_port", 161), self)
        self._worker.result_ready.connect(self.result_text.setPlainText)
        self._worker.start()


# ═════════════════════════════════════════════════════════════════════════════
#  ГЛАВНОЕ ОКНО
# ═════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self, config: dict):
        super().__init__()
        self.config   = config
        self.poller   = None
        self._auto_shutdown_armed = False
        self._shutdown_timer      = 0

        self._setup_window()
        self._setup_ui()
        self._setup_tray()
        self._setup_auto_shutdown_check()
        self._start_poller()

    # ── Окно ─────────────────────────────────────────────────────────────────
    def _setup_window(self):
        self.setWindowTitle(f"{APP_NAME} — {UPS_MODEL}")
        self.setMinimumSize(1100, 740)
        self.resize(1200, 820)

        # Тёмная тема
        palette = QPalette()
        palette.setColor(QPalette.Window,          QColor(THEME["bg_dark"]))
        palette.setColor(QPalette.WindowText,      QColor(THEME["text_primary"]))
        palette.setColor(QPalette.Base,            QColor(THEME["bg_medium"]))
        palette.setColor(QPalette.AlternateBase,   QColor(THEME["bg_light"]))
        palette.setColor(QPalette.ToolTipBase,     QColor(THEME["bg_medium"]))
        palette.setColor(QPalette.ToolTipText,     QColor(THEME["text_primary"]))
        palette.setColor(QPalette.Text,            QColor(THEME["text_primary"]))
        palette.setColor(QPalette.Button,          QColor(THEME["bg_medium"]))
        palette.setColor(QPalette.ButtonText,      QColor(THEME["text_primary"]))
        palette.setColor(QPalette.Highlight,       QColor(THEME["accent2"]))
        palette.setColor(QPalette.HighlightedText, QColor(THEME["bg_dark"]))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QMainWindow {{
                background: {THEME['bg_dark']};
            }}
            QTabWidget::pane {{
                background: {THEME['bg_dark']};
                border: 1px solid {THEME['border']};
                border-radius: 6px;
            }}
            QTabBar::tab {{
                background: {THEME['bg_medium']};
                color: {THEME['text_sec']};
                border: 1px solid {THEME['border']};
                border-bottom: none;
                border-radius: 4px 4px 0 0;
                padding: 6px 14px;
                margin-right: 2px;
                min-width: 110px;
                font: 9pt 'Segoe UI';
            }}
            QTabBar::tab:selected {{
                background: {THEME['bg_light']};
                color: {THEME['text_primary']};
                border-bottom: 2px solid {THEME['accent2']};
            }}
            QTabBar::tab:hover {{
                background: {THEME['bg_light']};
                color: {THEME['text_primary']};
            }}
            QCheckBox {{
                color: {THEME['text_primary']};
                font: 9pt 'Segoe UI';
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid {THEME['border']};
                border-radius: 3px;
                background: {THEME['bg_dark']};
            }}
            QCheckBox::indicator:checked {{
                background: {THEME['accent2']};
                border-color: {THEME['accent2']};
            }}
            QToolTip {{
                background: {THEME['bg_medium']};
                color: {THEME['text_primary']};
                border: 1px solid {THEME['border']};
                padding: 4px;
            }}
        """)

    # ── UI ───────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Заголовок
        header = QWidget()
        header.setStyleSheet(f"background: {THEME['bg_medium']}; border-radius: 6px;")
        header.setFixedHeight(44)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 0, 12, 0)

        lbl_title = QLabel(f"⚡  {APP_NAME}")
        lbl_title.setStyleSheet(f"""
            color: {THEME['text_primary']};
            font: bold 12pt 'Segoe UI';
            background: transparent;
        """)

        # Быстрый выбор порта прямо в заголовке
        self.hdr_port_cb = QComboBox()
        self.hdr_port_cb.setFixedWidth(100)
        self.hdr_port_cb.setFixedHeight(28)
        self.hdr_port_cb.setStyleSheet(f"""
            QComboBox {{
                background: {THEME['bg_dark']};
                color: {THEME['text_primary']};
                border: 1px solid {THEME['border']};
                border-radius: 4px;
                padding: 0 6px;
                font: 9pt 'Consolas';
            }}
            QComboBox QAbstractItemView {{
                background: {THEME['bg_medium']};
                color: {THEME['text_primary']};
                selection-background-color: {THEME['accent2']}55;
                border: 1px solid {THEME['border']};
            }}
            QComboBox::drop-down {{ border: none; }}
        """)
        self._refresh_header_ports()

        self.btn_connect = QPushButton("🔌  Подключить")
        self.btn_connect.setFixedHeight(28)
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        self.btn_connect.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['accent3']}22;
                color: {THEME['accent3']};
                border: 1px solid {THEME['accent3']};
                border-radius: 4px;
                padding: 0 12px;
                font: bold 9pt 'Segoe UI';
            }}
            QPushButton:hover  {{ background: {THEME['accent3']}44; }}
            QPushButton:pressed {{ background: {THEME['accent3']}66; }}
            QPushButton:disabled {{ color: {THEME['text_sec']}; border-color: {THEME['border']}; background: transparent; }}
        """)
        self.btn_connect.clicked.connect(self._manual_reconnect)

        # Кнопка освобождения порта прямо в заголовке
        self.btn_hdr_kill = QPushButton("🔓")
        self.btn_hdr_kill.setFixedSize(28, 28)
        self.btn_hdr_kill.setToolTip(
            "Освободить занятый COM-порт\n"
            "(завершить процессы, удерживающие порт)")
        self.btn_hdr_kill.setCursor(Qt.PointingHandCursor)
        self.btn_hdr_kill.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['battery']}22;
                color: {THEME['battery']};
                border: 1px solid {THEME['battery']}66;
                border-radius: 4px;
                font: 11pt 'Segoe UI';
            }}
            QPushButton:hover  {{ background: {THEME['battery']}44; }}
            QPushButton:pressed {{ background: {THEME['battery']}66; }}
        """)
        self.btn_hdr_kill.clicked.connect(self._hdr_kill_port)

        self.btn_scan_ports = QPushButton("↻")
        self.btn_scan_ports.setFixedSize(28, 28)
        self.btn_scan_ports.setToolTip("Обновить список портов")
        self.btn_scan_ports.setCursor(Qt.PointingHandCursor)
        self.btn_scan_ports.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {THEME['accent2']};
                border: 1px solid {THEME['border']};
                border-radius: 4px;
                font: bold 11pt 'Consolas';
            }}
            QPushButton:hover {{ background: {THEME['accent2']}22; }}
        """)
        self.btn_scan_ports.clicked.connect(self._refresh_header_ports)

        self.lbl_status_bar = QLabel("🔵  Инициализация...")
        self.lbl_status_bar.setStyleSheet(
            f"color:{THEME['text_sec']}; font: 9pt 'Segoe UI'; background: transparent;")

        self.lbl_clock = QLabel()
        self.lbl_clock.setStyleSheet(
            f"color:{THEME['text_sec']}; font: 9pt 'Consolas'; background: transparent;")

        hl.addWidget(lbl_title)
        hl.addSpacing(16)
        hl.addWidget(self.hdr_port_cb)
        hl.addWidget(self.btn_scan_ports)
        hl.addWidget(self.btn_connect)
        hl.addWidget(self.btn_hdr_kill)
        hl.addStretch()
        hl.addWidget(self.lbl_status_bar)
        hl.addSpacing(20)
        hl.addWidget(self.lbl_clock)
        layout.addWidget(header)

        # Вкладки
        self.tabs = QTabWidget()
        self.tabs.tabBar().setExpanding(False)          # не растягивать на всю ширину
        self.tabs.tabBar().setElideMode(Qt.ElideNone)   # не обрезать текст многоточием
        self.tab_dash     = DashboardTab()
        self.tab_control  = ControlTab()
        self.tab_settings = SettingsTab(self.config)
        self.tab_events   = EventLogTab()
        self.tab_snmp     = SNMPTab(self.config)

        self.tabs.addTab(self.tab_dash,     "📊  Дашборд")
        self.tabs.addTab(self.tab_control,  "⚙️  Управление")
        self.tabs.addTab(self.tab_settings, "🔧  Настройки")
        self.tabs.addTab(self.tab_events,   "📋  Журнал событий")
        self.tabs.addTab(self.tab_snmp,     "📡  SNMP")
        layout.addWidget(self.tabs)

        # Строка состояния
        status_bar = QWidget()
        status_bar.setStyleSheet(f"background: {THEME['bg_medium']}; border-radius: 4px;")
        status_bar.setFixedHeight(26)
        sbl = QHBoxLayout(status_bar)
        sbl.setContentsMargins(10, 0, 10, 0)

        self.lbl_conn_state = QLabel("● Нет связи")
        self.lbl_conn_state.setStyleSheet(f"color:{THEME['text_sec']}; font:8pt 'Segoe UI';")

        self.lbl_demo = QLabel("⚙  ДЕМО-РЕЖИМ" if True else "")
        self.lbl_demo.setStyleSheet(f"color:{THEME['battery']}; font:8pt 'Segoe UI'; background:transparent;")

        self.lbl_version = QLabel(f"v{APP_VERSION}  |  {UPS_MODEL}")
        self.lbl_version.setStyleSheet(f"color:{THEME['text_sec']}; font:8pt 'Segoe UI';")

        sbl.addWidget(self.lbl_conn_state)
        sbl.addSpacing(16)
        sbl.addWidget(self.lbl_demo)
        sbl.addStretch()
        sbl.addWidget(self.lbl_version)
        layout.addWidget(status_bar)

        # Сигналы управления
        self.tab_control.test_short.connect(lambda: self.poller.send_test_short() if self.poller else None)
        self.tab_control.test_long.connect(lambda: self.poller.send_test_long() if self.poller else None)
        self.tab_control.test_cancel.connect(lambda: self.poller.send_test_cancel() if self.poller else None)
        self.tab_control.shutdown_cmd.connect(lambda m: self.poller.send_shutdown(m) if self.poller else None)
        self.tab_control.shutdown_restore.connect(
            lambda o, n: self.poller.send_shutdown_restore(o, n) if self.poller else None)
        self.tab_control.cancel_shutdown.connect(lambda: self.poller.send_cancel_shutdown() if self.poller else None)
        self.tab_control.toggle_beeper.connect(lambda: self.poller.send_toggle_beeper() if self.poller else None)

        self.tab_settings.config_changed.connect(self._on_config_changed)

        # Таймер часов
        self._clock_timer = QTimer()
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    # ── Системный трей ───────────────────────────────────────────────────────
    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self)
        # Создаём простую иконку программно
        pix = QPixmap(16, 16)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setBrush(QColor(THEME["accent3"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, 12, 12)
        p.end()
        self.tray_icon.setIcon(QIcon(pix))
        self.tray_icon.setToolTip(f"{APP_NAME}\n{UPS_MODEL}")

        tray_menu = QMenu()
        tray_menu.setStyleSheet(f"""
            QMenu {{
                background: {THEME['bg_medium']};
                color: {THEME['text_primary']};
                border: 1px solid {THEME['border']};
                font: 9pt 'Segoe UI';
            }}
            QMenu::item:selected {{
                background: {THEME['accent2']}44;
            }}
        """)
        act_show = tray_menu.addAction("Показать окно")
        act_show.triggered.connect(self.show_normal)
        tray_menu.addSeparator()
        act_quit = tray_menu.addAction("Выход")
        act_quit.triggered.connect(QApplication.instance().quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def show_normal(self):
        self.showNormal()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_normal()

    def _update_tray_icon(self, status: UPSStatus):
        if not hasattr(self, 'tray_icon'):
            return
        pix = QPixmap(16, 16)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        color = QColor(status.mode_color() if status.connected else THEME["text_sec"])
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, 12, 12)
        p.end()
        self.tray_icon.setIcon(QIcon(pix))
        self.tray_icon.setToolTip(
            f"{APP_NAME}\n{status.mode_string()}\n"
            f"Батарея: {status.battery_charge}%  Нагрузка: {status.output_load_pct}%"
        )

    # ── Опросчик ─────────────────────────────────────────────────────────────
    def _start_poller(self):
        self.poller = UPSPollerThread(self.config)
        self.poller.status_updated.connect(self._on_status)
        self.poller.event_occurred.connect(self._on_event)
        self.poller.connection_lost.connect(self._on_conn_lost)
        self.poller.connected.connect(self._on_connected)
        self.poller.port_error.connect(self._on_port_error)
        self.poller.start()
        self.tab_events.add_event(
            EventRecord("INFO", f"Запуск {APP_NAME} v{APP_VERSION}"),
            self.config["alerts"].get("log_file", True))

    def _refresh_header_ports(self):
        """Обновить выпадающий список портов в заголовке."""
        cur = self.hdr_port_cb.currentText()
        self.hdr_port_cb.clear()
        if SERIAL_AVAILABLE:
            ports = serial.tools.list_ports.comports()
            for p in ports:
                label = f"{p.device}"
                if p.description and p.description != p.device:
                    short_desc = p.description[:20]
                    label = f"{p.device} — {short_desc}"
                self.hdr_port_cb.addItem(label, p.device)
            if not ports:
                self.hdr_port_cb.addItem("(нет портов)", "")
        else:
            for i in range(1, 9):
                self.hdr_port_cb.addItem(f"COM{i}", f"COM{i}")

        # Восстановить выбор: сначала по сохранённому порту из конфига
        cfg_port = self.config["connection"]["port"]
        found = False
        for i in range(self.hdr_port_cb.count()):
            if self.hdr_port_cb.itemData(i) == cfg_port:
                self.hdr_port_cb.setCurrentIndex(i)
                found = True
                break
        if not found and cur:
            idx = self.hdr_port_cb.findText(cur, Qt.MatchStartsWith)
            if idx >= 0:
                self.hdr_port_cb.setCurrentIndex(idx)

    def _manual_reconnect(self):
        """Ручное переподключение по кнопке из заголовка."""
        # Взять порт из выпадающего списка заголовка
        port_data = self.hdr_port_cb.currentData()
        if port_data:
            self.config["connection"]["port"] = port_data
            if self.poller:
                self.poller.config = copy.deepcopy(self.config)

        self.btn_connect.setEnabled(False)
        self.btn_connect.setText("⏳  Подключение...")
        self.lbl_status_bar.setText(f"🔵  Подключение к {port_data or '?'}...")
        self.lbl_status_bar.setStyleSheet(
            f"color:{THEME['accent2']}; font:9pt 'Segoe UI'; background:transparent;")

        if self.poller:
            self.poller.request_reconnect()

        # Через 5 сек разблокируем кнопку в любом случае
        QTimer.singleShot(5000, self._restore_connect_btn)

    def _hdr_kill_port(self):
        """Освободить порт — кнопка из заголовка окна."""
        port = self.hdr_port_cb.currentData() or self.config["connection"]["port"]
        if not port:
            return

        if not PortKiller.is_admin():
            ans = QMessageBox.question(
                self, "Права администратора",
                f"Для завершения процессов на {port} рекомендуются права "
                "администратора.\nПопробовать без них?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if ans == QMessageBox.No:
                return

        self.btn_hdr_kill.setEnabled(False)
        self.lbl_status_bar.setText(f"🔍  Поиск процессов на {port}...")

        result_box = [None, None]

        def _run():
            result_box[0], result_box[1] = PortKiller.free_port(port)

        import threading
        t = threading.Thread(target=_run, daemon=True)
        t.start()

        def _check():
            if t.is_alive():
                QTimer.singleShot(200, _check)
                return
            self.btn_hdr_kill.setEnabled(True)
            found, log = result_box
            if found:
                self._on_event(EventRecord(
                    "INFO", f"🔓 Порт {port} освобождён. {log.splitlines()[0] if log else ''}"))
                # Автоматически подключиться
                QTimer.singleShot(1000, self._manual_reconnect)
            else:
                self.lbl_status_bar.setText(f"⚠  Процессы на {port} не найдены")
                msg = QMessageBox(self)
                msg.setWindowTitle("Освобождение порта")
                msg.setIcon(QMessageBox.Warning)
                msg.setText(f"Процессы на {port} не обнаружены или не завершены.")
                msg.setDetailedText(log or "")
                msg.exec_()

        QTimer.singleShot(200, _check)

    def _restore_connect_btn(self):
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("🔌  Подключить")

    def _on_port_error(self, port: str, kind: str):
        """Реакция на ошибку порта — визуальное выделение и подсказка."""
        self._restore_connect_btn()
        if kind == "busy":
            self.lbl_status_bar.setText(f"🔒  {port} занят — закройте другое приложение")
            self.lbl_status_bar.setStyleSheet(
                f"color:{THEME['battery']}; font:9pt 'Segoe UI'; background:transparent;")
            self.lbl_status_bar.setToolTip(
                "Порт занят. Проверьте:\n"
                "1. Диспетчер устройств → свойства порта\n"
                "2. Другой экземпляр этой программы\n"
                "3. Службы Windows: завершите 'UPS' в services.msc\n"
                "4. Антивирус или другое ПО для мониторинга\n"
                "Программа продолжает попытки подключения автоматически каждые 30 сек."
            )
        elif kind == "missing":
            self.lbl_status_bar.setText(f"❌  {port} не найден — проверьте подключение")
            self.lbl_status_bar.setStyleSheet(
                f"color:{THEME['fault']}; font:9pt 'Segoe UI'; background:transparent;")
        # Обновить список портов
        self._refresh_header_ports()

    def _on_status(self, s: UPSStatus):
        if s.connected:
            self.tab_dash.update_status(s)
        else:
            self.tab_dash.power_flow.set_disconnected()
        self._update_tray_icon(s)
        self.lbl_conn_state.setText(
            f"●  {s.mode_string()}" if s.connected else "○  Нет достоверных данных"
        )
        self.lbl_conn_state.setStyleSheet(
            f"color:{s.mode_color() if s.connected else THEME['text_sec']}; font:8pt 'Segoe UI';"
        )
        # Показать/скрыть метку демо-режима
        dm = getattr(self.poller, '_demo_mode', False)
        self.lbl_demo.setText("⚙  ДЕМО-РЕЖИМ" if dm else "")
        if not dm:
            self._check_auto_shutdown(s)

    def _on_event(self, event: EventRecord):
        self.tab_events.add_event(event, self.config["alerts"].get("log_file", True))
        # Обновить счётчик на вкладке
        cnt = self.tab_events.table.rowCount()
        self.tabs.setTabText(3, f"📋  Журнал ({cnt})")

        alert = event.data.get("alert")
        if alert and not self.config["alerts"].get(alert, True):
            return
        if event.level not in ("CRITICAL", "WARNING"):
            return
        sound = self.config["alerts"].get("sound", True)
        if self.config["alerts"].get("popup", True):
            shown = show_notification(int(self.winId()), APP_NAME, event.message,
                                      event.level == "CRITICAL", sound)
            if shown:
                if not hasattr(self, "_notification_timer"):
                    self._notification_timer = QTimer(self)
                    self._notification_timer.setSingleShot(True)
                    self._notification_timer.timeout.connect(lambda: remove_notification(int(self.winId())))
                self._notification_timer.start(12000)
                return
            # Native shell unavailable: display a non-modal, silent in-app warning.
            self.statusBar().showMessage(event.message, 10000)
        if sound:
            QApplication.beep()

    def _on_conn_lost(self):
        self.lbl_status_bar.setText("🔴  Связь с ИБП потеряна")
        self.lbl_status_bar.setStyleSheet(
            f"color:{THEME['fault']}; font:9pt 'Segoe UI';")
        self.tab_dash.power_flow.set_disconnected()

    def _on_connected(self):
        conn = self.config["connection"]
        port = conn["snmp_host"] if conn["type"] == "snmp" else conn["port"]
        dm   = getattr(self.poller, '_demo_mode', False)
        txt  = "⚙  Демо-режим (нет порта)" if dm else f"🟢  Подключено: {port}"
        self.lbl_status_bar.setText(txt)
        self.lbl_status_bar.setStyleSheet(
            f"color:{THEME['online'] if not dm else THEME['battery']}; font:9pt 'Segoe UI'; background:transparent;")
        self.lbl_status_bar.setToolTip("")
        self._restore_connect_btn()
    # ── Автоматическое завершение ─────────────────────────────────────────────
    def _setup_auto_shutdown_check(self):
        # Таймер тикает каждую секунду для точного обратного отсчёта
        self._sd_check_timer = QTimer()
        self._sd_check_timer.timeout.connect(self._auto_shutdown_tick)
        self._sd_check_timer.start(1000)          # 1 сек

        # Состояние машины
        self._sd_phase      = 0    # 0=ожидание  1=отсчёт  2=выполнено
        self._sd_countdown  = 0    # секунд до завершения
        self._sd_trigger    = ""   # "battery_switch" | "battery_low"
        self._sd_warned_at  = -1
        self._sd_suppressed = False
        self._sd_deadline = 0.0
        self._sd_windows_pending = False

    # ── Проверка триггеров (вызывается каждые 2 сек из _on_status) ────────────
    def _check_auto_shutdown(self, s: UPSStatus):
        if not s.connected:
            return  # unknown power state must not cancel an existing countdown
        sd = self.config["shutdown"]
        if not s.utility_fail:
            if self._sd_phase in (1, 2):
                if not self._cancel_auto_shutdown(manual=False):
                    return
            self._sd_suppressed = False
            return
        if self._sd_phase != 0 or self._sd_suppressed:
            return
        if sd.get("on_battery_switch", False):
            delay = int(sd.get("on_battery_switch_delay_sec", 120))
            self._arm_shutdown("battery_switch", delay,
                f"⚡ ИБП перешёл на батарею. Завершение работы через {_fmt_sec(delay)}.")
        elif sd.get("enabled", False) and (s.battery_low or s.battery_charge <= sd.get("battery_low_percent", 30)):
            delay = int(sd.get("delay_minutes", 5) * 60)
            self._arm_shutdown("battery_low", delay,
                f"🔋 Низкий заряд батареи ({s.battery_charge}%). Завершение через {_fmt_sec(delay)}.")

    def _arm_shutdown(self, trigger: str, delay_sec: int, reason: str):
        """Запустить отсчёт завершения работы."""
        self._sd_phase     = 1
        self._sd_countdown = max(0, delay_sec)
        self._sd_deadline = time.monotonic() + self._sd_countdown
        self._sd_trigger   = trigger
        self._sd_warned_at = -1
        self._on_event(EventRecord("CRITICAL", f"🚨 {reason}"))
        # Показать баннер в заголовке
        self._show_countdown_banner(self._sd_countdown)
        # Немедленное выполнение при нулевой задержке
        if delay_sec == 0:
            self._execute_shutdown()

    # ── Тик таймера (каждую секунду) ─────────────────────────────────────────
    def _auto_shutdown_tick(self):
        if self._sd_phase != 1:
            return
        self._sd_countdown = max(0, math.ceil(self._sd_deadline - time.monotonic()))
        self._update_countdown_banner(self._sd_countdown)
        mark = int(self.config["shutdown"].get("warn_before_sec", 60))
        if 0 < self._sd_countdown <= mark and self._sd_warned_at != mark:
            self._sd_warned_at = mark
            self._on_event(EventRecord("CRITICAL",
                f"⚠️ Завершение работы через {_fmt_sec(self._sd_countdown)}! Можно отменить в окне программы."))
        if self._sd_countdown <= 0:
            self._execute_shutdown()

    def _execute_shutdown(self):
        cmd = self.config["shutdown"]["command"]
        self._sd_phase = 2
        self._on_event(EventRecord("ACTION", f"💻 Выполнение команды: {cmd}"))
        try:
            result = subprocess.run(cmd, shell=True, timeout=10, capture_output=True)
            if result.returncode:
                raise RuntimeError(f"код возврата {result.returncode}")
            self._sd_windows_pending = bool(re.search(r"(?i)\bshutdown(?:\.exe)?\b", cmd))
            if self._sd_windows_pending:
                self._show_countdown_banner(0)
                self._countdown_lbl.setText("⚠ Команда выключения передана Windows — можно отменить")
            else:
                self._hide_countdown_banner()
        except Exception as exc:
            self._sd_phase = 0
            self._sd_suppressed = True
            self._hide_countdown_banner()
            self._on_event(EventRecord("CRITICAL", f"Ошибка команды завершения: {exc}"))

    def _cancel_auto_shutdown(self, checked=False, manual=True):
        if self._sd_phase not in (1, 2):
            return True
        if self._sd_windows_pending:
            try:
                result = subprocess.run(["shutdown", "/a"], timeout=5, capture_output=True)
                # ERROR_NO_SHUTDOWN_IN_PROGRESS: command has already been cancelled.
                if result.returncode not in (0, 1116):
                    raise RuntimeError(f"код возврата {result.returncode}")
            except Exception as exc:
                self._on_event(EventRecord("CRITICAL", f"Не удалось отменить выключение Windows: {exc}"))
                return False
        self._sd_windows_pending = False
        self._sd_phase = 0
        self._sd_countdown = 0
        self._sd_trigger = ""
        self._sd_warned_at = -1
        self._sd_suppressed = manual
        self._hide_countdown_banner()
        self._on_event(EventRecord("INFO", "🛑 Автозавершение отменено вручную" if manual else
                                  "✅ Автозавершение отменено — сетевое питание восстановлено"))
        return True

    # ── Баннер обратного отсчёта в заголовке ─────────────────────────────────
    def _show_countdown_banner(self, seconds: int):
        """Показать/обновить яркий баннер с обратным отсчётом в заголовке."""
        if not hasattr(self, '_countdown_widget'):
            self._build_countdown_widget()
        self._countdown_widget.setVisible(True)
        self._update_countdown_banner(seconds)

    def _update_countdown_banner(self, seconds: int):
        if not hasattr(self, '_countdown_widget'):
            return
        sec = max(0, seconds)
        self._countdown_lbl.setText(
            f"⚠  Завершение через  {_fmt_sec(sec)}"
        )
        # Цвет меняется по мере приближения к нулю
        if sec > 60:
            color = THEME["battery"]
        elif sec > 10:
            color = THEME["accent"]
        else:
            color = THEME["fault"]
        self._countdown_lbl.setStyleSheet(
            f"color: {color}; font: bold 10pt 'Segoe UI'; background: transparent;"
        )

    def _hide_countdown_banner(self):
        if hasattr(self, '_countdown_widget'):
            self._countdown_widget.setVisible(False)

    def _build_countdown_widget(self):
        """Создать виджет баннера и вставить его в заголовок."""
        self._countdown_widget = QWidget()
        self._countdown_widget.setStyleSheet(
            f"background: {THEME['fault']}18; border: 1px solid {THEME['fault']}66;"
            f"border-radius: 5px;"
        )
        cw_layout = QHBoxLayout(self._countdown_widget)
        cw_layout.setContentsMargins(10, 0, 6, 0)
        cw_layout.setSpacing(8)

        self._countdown_lbl = QLabel("⚠  Завершение через...")
        self._countdown_lbl.setStyleSheet(
            f"color: {THEME['battery']}; font: bold 10pt 'Segoe UI'; background: transparent;")

        btn_cancel_sd = QPushButton("✕  Отменить")
        btn_cancel_sd.setFixedHeight(24)
        btn_cancel_sd.setCursor(Qt.PointingHandCursor)
        btn_cancel_sd.setStyleSheet(f"""
            QPushButton {{
                background: {THEME['fault']}33;
                color: {THEME['fault']};
                border: 1px solid {THEME['fault']};
                border-radius: 4px;
                padding: 0 10px;
                font: bold 8pt 'Segoe UI';
            }}
            QPushButton:hover  {{ background: {THEME['fault']}66; }}
            QPushButton:pressed {{ background: {THEME['fault']}99; }}
        """)
        btn_cancel_sd.clicked.connect(self._cancel_auto_shutdown)

        cw_layout.addWidget(self._countdown_lbl)
        cw_layout.addWidget(btn_cancel_sd)

        # Вставляем баннер в layout заголовка (header — первый child central widget)
        central_layout = self.centralWidget().layout()
        # header — виджет с индексом 0 в layout; вставим баннер сразу после него
        central_layout.insertWidget(1, self._countdown_widget)
        self._countdown_widget.setVisible(False)

    # ── Конфиг ───────────────────────────────────────────────────────────────
    def _on_config_changed(self, new_config: dict):
        old_conn = self.config["connection"]
        new_conn = new_config["connection"]
        reconnect = any(old_conn.get(key) != new_conn.get(key) for key in
                        ("type", "port", "baud", "timeout", "snmp_host", "snmp_community", "snmp_port"))
        self.config = copy.deepcopy(new_config)
        self.tab_snmp.config = copy.deepcopy(self.config)
        self.tab_snmp.ed_host.setText(new_conn["snmp_host"])
        self.tab_snmp.ed_comm.setText(new_conn["snmp_community"])
        if self.poller:
            self.poller.config = copy.deepcopy(self.config)
            self.poller._wake.set()
            if reconnect:
                self.poller.request_reconnect()
        if self._sd_phase == 1:
            key = "on_battery_switch" if self._sd_trigger == "battery_switch" else "enabled"
            if not self.config["shutdown"].get(key, False):
                self._cancel_auto_shutdown()

    # ── Часы ─────────────────────────────────────────────────────────────────
    def _update_clock(self):
        self.lbl_clock.setText(datetime.now().strftime("%d.%m.%Y  %H:%M:%S"))

    # ── Закрытие ─────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        if self.config["display"].get("minimize_to_tray", True) and \
                hasattr(self, 'tray_icon') and self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage(
                APP_NAME, "Программа свёрнута в трей. "
                "Двойной клик для открытия.",
                QSystemTrayIcon.Information, 3000)
            event.ignore()
        else:
            self._cleanup()
            event.accept()

    def _cleanup(self):
        remove_notification(int(self.winId()))
        if self.poller:
            self.poller.stop()
            self.poller.wait()  # stop wakes sleep; serial/SNMP calls have bounded timeouts
        if hasattr(self.tab_snmp, "_worker") and self.tab_snmp._worker is not None:
            self.tab_snmp._worker.wait()


# ═════════════════════════════════════════════════════════════════════════════
#  ЗАГРУЗКА / СОХРАНЕНИЕ КОНФИГУРАЦИИ
# ═════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    import copy
    config = copy.deepcopy(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            # Слияние с дефолтами
            for section, vals in saved.items():
                if section in config and isinstance(vals, dict):
                    config[section].update(vals)
        except Exception:
            pass
    legacy = 'shutdown /s /t 60 /c "ИБП: питание от батареи. Завершение работы."'
    if config["shutdown"].get("command") == legacy:
        config["shutdown"]["command"] = DEFAULT_CONFIG["shutdown"]["command"]
    return config


# ═════════════════════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА
# ═════════════════════════════════════════════════════════════════════════════

def main():
    # Windows High DPI
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("ExeGate")
    app.setStyle("Fusion")

    if "--self-test" in sys.argv:
        from smoke_test import run
        index = sys.argv.index("--self-test")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--self-test requires an output JSON path")
        raise SystemExit(run(sys.modules[__name__], sys.argv[index + 1]))

    config = load_config()

    # Флаг --minimized: запуск свёрнутым в трей (используется при автозагрузке)
    start_minimized = ("--minimized" in sys.argv or
                       config.get("display", {}).get("start_minimized", False))

    window = MainWindow(config)

    if start_minimized and QSystemTrayIcon.isSystemTrayAvailable():
        # Не показываем окно — только иконка в трее
        window.hide()
    else:
        window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
