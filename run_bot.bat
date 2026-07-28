@echo off
REM Лаунчер бота мониторинга цен (для автозапуска через Планировщик заданий).
REM Запускает бота из виртуального окружения без окна консоли, логи -> monitor.log
cd /d "%~dp0"
".venv\Scripts\pythonw.exe" monitor.py --bot >> monitor.log 2>&1
