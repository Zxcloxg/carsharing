@echo off
chcp 65001 >nul
rem Проверка типовых уязвимостей API на запущенном сервисе (перед повторным запуском перезапустите serve.cmd).
rem Протокол сохраняется в ..\docs\security-check.md
"%~dp0.venv\Scripts\python.exe" "%~dp0security_check.py" %*
