@echo off
REM ===========================================
REM ComfyUI Manager - Docker Start Script
REM ===========================================
REM Starts both Portal and ComfyUI containers

REM Change to the parent directory where docker-compose files are located
cd /d "%~dp0.."

echo Starting ComfyUI Manager Docker Stack...
echo Working directory: %CD%
echo.

REM Create network if it doesn't exist
echo [1/3] Creating Docker network...
docker network create comfyui-network 2>nul || echo Network already exists

REM Start ComfyUI first (GPU container)
echo [2/3] Starting ComfyUI container (GPU)...
docker compose -f docker-compose-comfyui.yml up -d

REM Start Portal
echo [3/3] Starting Portal container...
docker compose -f docker-compose-portal.yml up -d --build

echo.
echo ===========================================
echo Stack started successfully!
echo.
echo Portal:   http://localhost:8000
echo ComfyUI:  http://localhost:8188
echo ===========================================
echo.
echo To view logs:
echo   docker compose -f docker-compose-portal.yml logs -f
echo   docker compose -f docker-compose-comfyui.yml logs -f
echo.
pause
