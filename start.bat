@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==================================================
echo   Запускаю мессенджер "Поговорим"
echo.
echo   Потом открой в браузере адрес:
echo       http://127.0.0.1:5000
echo.
echo   Чтобы остановить программу - просто закрой это окно.
echo ==================================================
echo.

py app.py
if errorlevel 1 (
  echo (Пробую запустить Python напрямую...)
  "C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe" app.py
)

echo.
echo Программа остановлена.
pause
