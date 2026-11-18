@echo off
rem Эмулятор платёжного провайдера на http://127.0.0.1:8030 (ПР6). Ключи: --latency, --fail-first, --decline-over.
"%~dp0.venv\Scripts\python.exe" "%~dp0payment_provider_stub.py" %*
