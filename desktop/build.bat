@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=py
where py >nul 2>nul || set PY="C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe"

echo ==================================================
echo   Сборка программы "Поговорим" для Windows
echo ==================================================
echo.

echo [1/4] Устанавливаю нужные библиотеки...
%PY% -m pip install --upgrade pywebview pyinstaller pyinstaller-hooks-contrib pillow

echo.
echo [2/4] Готовлю иконку...
%PY% -c "from PIL import Image; im=Image.open(r'..\static\icons\icon-512.png'); im.save('app.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

echo.
echo [3/4] Собираю программу (1-2 минуты)...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name Pogovorim --icon=app.ico desktop_app.py

echo.
echo ==================================================
echo   ГОТОВО!
echo   Программа лежит здесь:
echo   desktop\dist\Pogovorim.exe
echo   (можно переименовать, например в Pogovorim.exe)
echo ==================================================
echo.
pause
