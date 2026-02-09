@echo off
REM ===========================================
REM ComfyUI Manager - Docker Stop Script
REM ===========================================
REM Stops both Portal and ComfyUI containers

REM Change to the parent directory where docker-compose files are located
cd /d "%~dp0.."

echo Stopping ComfyUI Manager Docker Stack...
echo Working directory: %CD%
echo.

REM Stop Portal
echo [1/2] Stopping Portal container...
docker compose -f docker-compose-portal.yml down

REM Stop ComfyUI
echo [2/2] Stopping ComfyUI container...
docker compose -f docker-compose-comfyui.yml down

echo.
echo ===========================================
echo Stack stopped successfully!
echo ===========================================
echo.
echo To remove volumes (delete all data):
echo   docker volume rm comfyui-portal-data comfyui-portal-logs
echo   docker volume rm comfyui-storage comfyui-models comfyui-input comfyui-output
echo.
pause
