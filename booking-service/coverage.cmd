@echo off
chcp 65001 >nul
rem Автотесты с замером покрытия: отчёт в консоли и HTML в htmlcov\index.html
cd /d "%~dp0"
".venv\Scripts\python.exe" -m coverage run manage.py test tests %* || exit /b 1
".venv\Scripts\python.exe" -m coverage report
".venv\Scripts\python.exe" -m coverage html
echo HTML-отчёт: %~dp0htmlcov\index.html
