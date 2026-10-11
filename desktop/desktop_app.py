# -*- coding: utf-8 -*-
"""
Программа-окно для компьютера.
Открывает мессенджер «Поговорим» в отдельном окне (как обычное приложение).

Адрес сервера спрашивается один раз и запоминается в файле server.txt
рядом с программой — чтобы не вводить каждый раз.
"""

import os
import sys
import tkinter as tk
from tkinter import simpledialog

import webview


def base_dir():
    """Папка, где лежит программа (у .exe — рядом с ним)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG = os.path.join(base_dir(), "server.txt")


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
    """Читаем сохранённый адрес. Если его нет — спрашиваем и сохраняем."""
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
    url = load_address()
    if not url:
        return
    webview.create_window(
        "Поговорим", url, width=1000, height=760, min_size=(420, 560)
    )
    webview.start()


if __name__ == "__main__":
    main()
