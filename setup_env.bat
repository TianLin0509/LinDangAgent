@echo off
setlocal

cd /d "%~dp0"

echo [1/3] Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [2/3] Installing requirements.txt...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [3/3] Installing FastAPI runtime...
python -m pip install fastapi uvicorn
if errorlevel 1 goto :error

echo Environment setup completed successfully.
exit /b 0

:error
echo Environment setup failed.
exit /b 1
