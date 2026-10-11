# -*- coding: utf-8 -*-
"""
Программа-окно для компьютера.
Открывает мессенджер «Поговорим» в отдельном окне (как обычное приложение).

Адрес сервера можно:
  * задать ниже в DEFAULT_ADDRESS (тогда программа не спросит его);
  * или ввести один раз — программа запомнит его в файле server.txt.
При первом запуске программа также создаёт ярлык на рабочем столе.
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import simpledialog

import webview

# === Адрес твоего мессенджера ===
# Впиши сюда, например: "https://мой-домен.ru" (без слэша в конце).
# Если здесь пусто — программа спросит адрес один раз и запомнит.
DEFAULT_ADDRESS = ""


def base_dir():
    """Папка, где лежит программа (у .exe — рядом с ним)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG = os.path.join(base_dir(), "server.txt")


def desktop_lnk_path():
    return os.path.join(os.path.expanduser("~"), "Desktop", "Поговорим.lnk")


def create_desktop_shortcut():
    """Создаём ярлык на рабочем столе (один раз)."""
    if not getattr(sys, "frozen", False):
        return  # запущено как .py — ярлык не нужен
    lnk = desktop_lnk_path()
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
            creationflags=0x08000000,  # CREATE_NO_WINDOW — без мелькания окна
        )
    except Exception:
        pass


def ask_address():
    """Спрашиваем адрес мессенджера (один раз)."""
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
    """Адрес: сначала зашитый, потом сохранённый, потом спросить."""
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


def main():
    create_desktop_shortcut()
    url = load_address()
    if not url:
        return
    webview.create_window(
        "Поговорим", url, width=1000, height=760, min_size=(420, 560)
    )
    webview.start()


if __name__ == "__main__":
    main()
