@echo off
REM One-click start for Windows: creates the virtual environment, installs packages, starts the app.
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
if not exist .env copy .env.example .env
python app.py
pause
