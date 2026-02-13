@echo off
echo ====================================
echo   ComfyUI Manager - Full Setup
echo ====================================

cd /d "%~dp0"

echo.
echo 📁 Creating directories...
if not exist "models\checkpoints" mkdir models\checkpoints
if not exist "custom_nodes" mkdir custom_nodes
if not exist "outputs" mkdir outputs
if not exist "input" mkdir input
if not exist "workflows" mkdir workflows
if not exist "logs" mkdir logs

echo.
echo 📦 Installing Python dependencies...
pip install -r requirements.txt

echo.
echo 📝 Checking environment configuration...
if not exist ".env" (
    echo ⚠️  No .env file found. Copying from .env.example...
    copy .env.example .env
    echo ✏️  Please edit .env with your configuration!
)

echo.
echo ====================================
echo   Setup Complete!
echo ====================================
echo.
echo Next steps:
echo 1. Edit .env with your Supabase, Stripe, and GitLab credentials
echo 2. Start the portal: uvicorn app:app --host 0.0.0.0 --port 8730 --reload
echo 3. Start ComfyUI: docker-compose up -d comfyui
echo.
echo Access the portal at: http://localhost:8730
echo.

pause
