@echo off
rem Запуск API на http://localhost:8010
"%~dp0.venv\Scripts\python.exe" "%~dp0manage.py" runserver 8010 %*
