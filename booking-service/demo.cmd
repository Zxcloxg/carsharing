@echo off
chcp 65001 >nul
rem Демонстрация CRUD и ошибок на запущенном сервисе
"%~dp0.venv\Scripts\python.exe" "%~dp0demo.py" %*
