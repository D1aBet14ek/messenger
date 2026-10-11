# -*- coding: utf-8 -*-
"""
Прячем чёрное окно консоли у готовой программы.

PyInstaller обычно собирает «оконные» программы отдельным загрузчиком (runw.exe),
но антивирус его блокирует, поэтому мы собираем в режиме --console и затем
меняем в файле .exe «подсистему» с консольной (3) на оконную (2).

Запуск:  python patch_subsystem.py путь\к\файлу.exe
"""

import struct
import sys


def read_subsystem(path):
    with open(path, "rb") as f:
        data = f.read()
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError("Это не PE-файл")
    opt = e_lfanew + 4 + 20          # сигнатура PE + заголовок COFF
    subsystem_off = opt + 68        # поле «Subsystem» в необязательном заголовке
    subsystem = struct.unpack_from("<H", data, subsystem_off)[0]
    return subsystem, subsystem_off, bytearray(data)


def set_subsystem(path, value):
    before, off, data = read_subsystem(path)
    struct.pack_into("<H", data, off, value)
    with open(path, "wb") as f:
        f.write(data)
    return before


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Укажи путь к .exe")
        sys.exit(1)
    path = sys.argv[1]
    before, off, _ = read_subsystem(path)
    print("Подсистема ДО:", before, "(3 — с консолью, 2 — без консоли)")
    if before == 2:
        print("Уже без консоли — ничего не меняю.")
    else:
        set_subsystem(path, 2)
        after, _, _ = read_subsystem(path)
        print("Подсистема ПОСЛЕ:", after)
        print("Готово: чёрное окно консоли убрано.")
