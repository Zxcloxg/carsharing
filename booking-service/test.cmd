@echo off
rem Автотесты: модульные (tests\unit), API (tests\api) и безопасности (tests\security).
rem Отдельная база test_<DB_NAME> создаётся и удаляется автоматически. Пример: test.cmd --shuffle
"%~dp0.venv\Scripts\python.exe" "%~dp0manage.py" test tests %*
