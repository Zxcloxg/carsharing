@echo off
rem Проверка фонового списания (ПР6): сам запускает API, обработчик и эмулятор провайдера,
rem проходит штатный и ошибочный путь и пишет протокол в ..\docssync-scenario.md.
"%~dp0.venv\Scripts\python.exe" "%~dp0async_check.py" %*
