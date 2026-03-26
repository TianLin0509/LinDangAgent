@echo off
setlocal

cd /d "%~dp0"

echo Starting FastAPI server on port 80...
uvicorn main:app --host 0.0.0.0 --port 80
