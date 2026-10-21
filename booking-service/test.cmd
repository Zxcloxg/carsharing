@echo off
rem Прогон автотестов: автопарк, клиенты, брони (fleet) и аутентификация с ролями (accounts)
"%~dp0.venv\Scripts\python.exe" "%~dp0manage.py" test fleet accounts %*
