@echo off
rem Обработчик фоновых задач (ПР6): списание за поездки, чеки. Запускать рядом с serve.cmd.
"%~dp0.venv\Scripts\python.exe" "%~dp0manage.py" run_worker %*
