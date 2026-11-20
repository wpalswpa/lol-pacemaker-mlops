@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

set "MODEL_IMPL=rule"
set "MODEL_DIR=api/tests/fixtures"
if exist "artifacts\model_sklearn.onnx" if exist "artifacts\model.json" if exist "artifacts\baseline.json" (
  set "MODEL_IMPL=onnx"
  set "MODEL_DIR=artifacts"
)
set "LLM_API_URL=http://127.0.0.1:11434/v1/chat/completions"
set "LLM_MODEL=qwen3:4b-instruct-2507-q4_K_M"
set "LLM_STRATEGY_TIMEOUT_SEC=12.0"

set "LOG_DIR=%TEMP%\lol-pace-client"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "PYW_EXE=.venv\Scripts\pythonw.exe"
if not exist "%PYW_EXE%" set "PYW_EXE=pythonw"

start "" /B "%PYW_EXE%" -m uvicorn api.app:app --host 127.0.0.1 --port 8083 >>"%LOG_DIR%\api.log" 2>&1

set "API_READY=0"
for /L %%I in (1,1,30) do (
  if "!API_READY!"=="0" (
    curl.exe -fs "http://127.0.0.1:8083/health" >nul 2>nul && set "API_READY=1"
  )
  if "!API_READY!"=="0" ping -n 2 127.0.0.1 >nul
)
if "!API_READY!"=="0" (
  echo [LoL Pacemaker] Local API server failed to start on http://127.0.0.1:8083/health within 30s.
  echo Check "%LOG_DIR%\api.log" for details.
  exit /b 1
)

start "" /B "%PYW_EXE%" -m client.bridge --server http://127.0.0.1:8083 --insecure-local --overlay --launch-popup --repin-sec 3 --clip-caption-px 32 --log-file "%LOG_DIR%\launcher.log" --exit-on-close
exit /b 0
