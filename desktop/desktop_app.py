# -*- coding: utf-8 -*-
"""
Программа-окно для компьютера.
Открывает мессенджер «Поговорим» в отдельном окне (как обычное приложение).

Умеет:
  * запоминать адрес сервера (или брать его из DEFAULT_ADDRESS);
  * создавать ярлык на рабочем столе;
  * проверять новую версию и предлагать обновиться (самообновление).
"""

import json
import os
import subprocess
import sys
import tempfile
import tkinter as tk
import urllib.request
from tkinter import messagebox, simpledialog

import webview

# === Версия программы (меняй вместе с desktop/version.txt на сервере) ===
VERSION = "1.2"

# === Адрес твоего мессенджера ===
# Если здесь пусто — программа спросит адрес один раз и запомнит.
DEFAULT_ADDRESS = "https://messenger.d1abet14ek.corray.ru"


def hide_console():
    """Прячем чёрное окно консоли (нужно при сборке без «оконного» загрузчика)."""
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def base_dir():
    """Папка, где лежит программа (у .exe — рядом с ним)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG = os.path.join(base_dir(), "server.txt")


def parse_version(text):
    """«1.2.3» -> (1, 2, 3) — чтобы сравнивать версии."""
    parts = []
    for chunk in str(text).split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


# ---------------------------------------------------------------------------
# Ярлык на рабочем столе
# ---------------------------------------------------------------------------
def create_desktop_shortcut():
    if not getattr(sys, "frozen", False):
        return
    lnk = os.path.join(os.path.expanduser("~"), "Desktop", "Поговорим.lnk")
    if os.path.exists(lnk):
        return
    exe = sys.executable.replace("'", "''")
    workdir = os.path.dirname(sys.executable).replace("'", "''")
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$ws=New-Object -ComObject WScript.Shell;"
        "$sc=$ws.CreateShortcut($d+'\\Поговорим.lnk');"
        "$sc.TargetPath='" + exe + "';"
        "$sc.IconLocation='" + exe + "';"
        "$sc.WorkingDirectory='" + workdir + "';"
        "$sc.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            creationflags=0x08000000,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Адрес сервера
# ---------------------------------------------------------------------------
def ask_address():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    url = simpledialog.askstring(
        "Поговорим",
        "Введи адрес мессенджера (например, https://chat.example.com):",
        parent=root,
    )
    root.destroy()
    return (url or "").strip()


def load_address():
    if DEFAULT_ADDRESS:
        return DEFAULT_ADDRESS
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved:
                return saved
        except OSError:
            pass
    url = ask_address()
    if url:
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                f.write(url)
        except OSError:
            pass
    return url


# ---------------------------------------------------------------------------
# Проверка и установка обновления
# ---------------------------------------------------------------------------
def _hide_root():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root


def check_and_offer_update(base_url):
    """Проверяет новую версию. Если есть — спрашивает и обновляет.
    Возвращает True, если начал обновление (тогда программе надо закрыться)."""
    try:
        api = base_url.rstrip("/") + "/desktop-version"
        with urllib.request.urlopen(api, timeout=6) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False  # нет интернета или сервер недоступен — просто работаем

    remote = info.get("version", "")
    if parse_version(remote) <= parse_version(VERSION):
        return False

    root = _hide_root()
    yes = messagebox.askyesno(
        "Обновление",
        "Доступна новая версия программы ({}).\n"
        "Скачать и установить сейчас?\n\n"
        "Программа скачает обновление и перезапустится.".format(remote),
        parent=root,
    )
    root.destroy()
    if not yes:
        return False

    download_url = info.get("url", "")
    if not download_url:
        return False

    new_exe = os.path.join(tempfile.gettempdir(), "Pogovorim_new.exe")
    try:
        urllib.request.urlretrieve(download_url, new_exe)
    except Exception as exc:
        root = _hide_root()
        messagebox.showerror("Ошибка", "Не удалось скачать обновление:\n" + str(exc), parent=root)
        root.destroy()
        return False

    cur = sys.executable
    bat = os.path.join(tempfile.gettempdir(), "pogovorim_update.bat")
    with open(bat, "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")
        f.write(":wait\r\n")
        f.write('del "' + cur + '" >nul 2>nul\r\n')
        f.write('if exist "' + cur + '" (\r\n')
        f.write("  ping -n 2 127.0.0.1 >nul\r\n")
        f.write("  goto wait\r\n")
        f.write(")\r\n")
        f.write('move /y "' + new_exe + '" "' + cur + '" >nul\r\n')
        f.write('start "" "' + cur + '"\r\n')
        f.write('del "%~f0"\r\n')

    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000, close_fds=True)
    return True


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
class Api:
    """«Мостик» со страницы в программу: позволяет выйти из полноэкранного режима."""

    def toggle_fullscreen(self):
        win = webview.active_window()
        if win:
            win.toggle_fullscreen()


def main():
    hide_console()
    create_desktop_shortcut()
    url = load_address()
    if not url:
        return

    if check_and_offer_update(url):
        # Идёт обновление — закрываемся, чтобы файл программы освободился.
        os._exit(0)

    webview.create_window(
        "Поговорим",
        url,
        width=1200,
        height=800,
        min_size=(420, 560),
        fullscreen=True,
        js_api=Api(),
    )
    # private_mode=False + storage_path — чтобы вход сохранялся между запусками.
    # Папку берём в системных данных пользователя, а не рядом с программой.
    storage = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Pogovorim"
    )
    webview.start(private_mode=False, storage_path=storage)


if __name__ == "__main__":
    main()
