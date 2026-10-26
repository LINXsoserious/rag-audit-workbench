@echo off
setlocal
cd /d "%~dp0"
curl.exe --noproxy 127.0.0.1 --fail --silent --max-time 2 http://127.0.0.1:8766/_stcore/health >nul 2>nul
if not errorlevel 1 (
    start "" "http://127.0.0.1:8766"
    exit /b 0
)
set "PIP_CACHE_DIR=%CD%\.rag-audit\pip-cache"
if exist ".venv\Scripts\python.exe" goto run
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -e ".[ui]"
if errorlevel 1 goto failed
:run
".venv\Scripts\python.exe" -c "import streamlit, rag_audit" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -e ".[ui]"
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m streamlit run ui.py --server.address 127.0.0.1 --server.port 8766 --browser.gatherUsageStats false
if errorlevel 1 goto failed
exit /b 0
:failed
echo Launch failed. Install Python 3.11 or newer and check the error above.
pause
exit /b 1
