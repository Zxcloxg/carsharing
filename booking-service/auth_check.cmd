@echo off
chcp 65001 >nul
rem Проверка сценариев доступа (аноним / клиент / оператор / администратор) на запущенном сервисе.
rem Протокол сохраняется в ..\docs\auth-check.md
"%~dp0.venv\Scripts\python.exe" "%~dp0auth_check.py" %*
