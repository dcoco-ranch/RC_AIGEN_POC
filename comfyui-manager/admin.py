"""
Admin Module for ComfyUI Manager
Dashboard, user management, model management, and ComfyUI operations
"""

import os
import subprocess
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import httpx
from dotenv import load_dotenv

import json
import asyncio
from sse_starlette.sse import EventSourceResponse

from database import db
from auth import get_current_admin
from wallet import manual_adjust_rcc, get_balance

load_dotenv()

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")

# Configuration
# COMFYUI_PORT: The port where ComfyUI is accessible internally
# COMFYUI_PUBLIC_PORT: The port exposed externally in user-facing URLs
# COMFYUI_INTERNAL_HOST: Internal hostname for backend health checks (default: localhost)
COMFYUI_PORT = int(os.getenv("COMFYUI_PORT", "8188"))
COMFYUI_PUBLIC_PORT = int(os.getenv("COMFYUI_PUBLIC_PORT", os.getenv("COMFYUI_PORT", "8188")))
COMFYUI_INTERNAL_HOST = os.getenv("COMFYUI_INTERNAL_HOST", "localhost")
COMFYUI_INTERNAL_URL = f"http://{COMFYUI_INTERNAL_HOST}:{COMFYUI_PORT}"
MODELS_BASE_PATH = os.getenv("MODELS_PATH", "./storage-models/models")

# Active downloads tracking (in-memory state)
# Structure: {download_id: {status, progress, downloaded, total, filename, model_type, error, started_at}}
active_downloads: Dict[str, Dict[str, Any]] = {}

# ComfyUI model types and their folders
MODEL_TYPES = {
    "checkpoints": "Checkpoints",
    "vae": "VAE",
    "loras": "LoRAs",
    "controlnet": "ControlNet",
    "clip": "CLIP",
    "clip_vision": "CLIP Vision",
    "diffusion_models": "Diffusion",
    "text_encoders": "Text Enc.",
    "unet": "UNET",
    "upscale_models": "Upscale",
    "embeddings": "Embeddings",
    "hypernetworks": "Hypernets",
    "style_models": "Style",
    "gligen": "GLIGEN",
    "audio_encoders": "Audio Enc.",
    "diffusers": "Diffusers",
    "vae_approx": "VAE Approx",
    "latent_upscale_models": "Latent Upscale",
    "photomaker": "PhotoMaker",
    "model_patches": "Patches",
}


# ============================================
# Dashboard
# ============================================

@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, current_user: dict = Depends(get_current_admin)):
    """Admin dashboard with KPIs"""
    now = datetime.utcnow()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)
    
    # Gather stats
    stats = {
        "total_users": await db.count_users(),
        "total_jobs_24h": await db.count_jobs(since=last_24h),
        "total_jobs_7d": await db.count_jobs(since=last_7d),
        "rcc_consumed_24h": await db.get_total_rcc_consumed(since=last_24h),
        "rcc_consumed_7d": await db.get_total_rcc_consumed(since=last_7d),
        "failed_jobs_24h": await db.count_failed_jobs(since=last_24h),
        "models_count": len(list_models_in_directory())
    }
    
    # Recent jobs
    recent_jobs = await db.get_all_jobs(limit=10)
    
    # Recent logs
    recent_logs = await db.get_logs(limit=10)
    
    # ComfyUI status
    comfyui_status = await get_comfyui_status(request)
    
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "user": current_user,
        "messages": [],
        "stats": stats,
        "recent_jobs": recent_jobs,
        "recent_logs": recent_logs,
        "comfyui_status": comfyui_status
    })


# ============================================
# User Management
# ============================================

@router.get("/users", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    current_user: dict = Depends(get_current_admin),
    page: int = 1,
    per_page: int = 20
):
    """List all users with their RCC balance"""
    offset = (page - 1) * per_page
    users = await db.get_all_users(limit=per_page, offset=offset)
    
    # Add balance to each user
    users_with_balance = []
    for user in users:
        balance = await get_balance(user["id"])
        users_with_balance.append({**user, "rcc_balance": balance})
    
    total_users = await db.count_users()
    total_pages = (total_users + per_page - 1) // per_page
    
    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "user": current_user,
        "messages": [],
        "users": users_with_balance,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total_users": total_users
    })


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(
    request: Request,
    user_id: int,
    current_user: dict = Depends(get_current_admin)
):
    """User detail page"""
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    balance = await get_balance(user_id)
    jobs = await db.get_user_jobs(user_id, limit=20)
    rcc_history = await db.get_user_rcc_history(user_id, limit=20)
    payments = await db.get_user_payments(user_id, limit=20)
    
    return templates.TemplateResponse("admin/user_detail.html", {
        "request": request,
        "user": current_user,
        "messages": [],
        "target_user": {**user, "rcc_balance": balance},
        "jobs": jobs,
        "rcc_history": rcc_history,
        "payments": payments
    })


@router.post("/users/{user_id}/adjust-rcc")
async def admin_adjust_user_rcc(
    user_id: int,
    delta: int = Form(...),
    reason: str = Form(""),
    current_user: dict = Depends(get_current_admin)
):
    """Adjust user's RCC balance (admin only)"""
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    await manual_adjust_rcc(
        user_id=user_id,
        delta=delta,
        admin_user_id=current_user["id"],
        reason=reason
    )
    
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/toggle-admin")
async def admin_toggle_user_admin(
    user_id: int,
    current_user: dict = Depends(get_current_admin)
):
    """Toggle user's admin status"""
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Don't allow removing your own admin status
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot modify your own admin status")
    
    new_admin_status = not user.get("is_admin", False)
    await db.update_user(user_id, is_admin=new_admin_status)
    
    await db.add_log(
        action="admin_toggle",
        user_id=current_user["id"],
        details=f"Set user {user_id} admin status to {new_admin_status}"
    )
    
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


# ============================================
# Jobs Management
# ============================================

@router.get("/jobs", response_class=HTMLResponse)
async def admin_jobs_list(
    request: Request,
    current_user: dict = Depends(get_current_admin),
    page: int = 1,
    per_page: int = 20,
    status: Optional[str] = None
):
    """List all jobs"""
    offset = (page - 1) * per_page
    jobs = await db.get_all_jobs(limit=per_page, offset=offset, status=status)
    
    total_jobs = await db.count_jobs()
    total_pages = (total_jobs + per_page - 1) // per_page
    
    return templates.TemplateResponse("admin/jobs.html", {
        "request": request,
        "user": current_user,
        "messages": [],
        "jobs": jobs,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total_jobs": total_jobs,
        "status_filter": status
    })


# ============================================
# Logs Management
# ============================================

@router.get("/logs", response_class=HTMLResponse)
async def admin_logs_list(
    request: Request,
    current_user: dict = Depends(get_current_admin),
    page: int = 1,
    per_page: int = 50,
    action: Optional[str] = None
):
    """View system logs"""
    offset = (page - 1) * per_page
    logs = await db.get_logs(limit=per_page, offset=offset, action=action)
    
    return templates.TemplateResponse("admin/logs.html", {
        "request": request,
        "user": current_user,
        "messages": [],
        "logs": logs,
        "page": page,
        "per_page": per_page,
        "action_filter": action
    })


@router.get("/logs/export")
async def admin_export_logs(
    current_user: dict = Depends(get_current_admin),
    format: str = "csv"
):
    """Export logs as CSV"""
    logs = await db.get_logs(limit=10000)
    
    if format == "csv":
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "User ID", "IP", "Action", "Details", "Status", "Created At"])
        
        for log in logs:
            writer.writerow([
                log.get("id"),
                log.get("user_id"),
                log.get("ip"),
                log.get("action"),
                log.get("details"),
                log.get("status"),
                log.get("created_at")
            ])
        
        output.seek(0)
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"}
        )
    
    return logs


# ============================================
# Models Management
# ============================================

def list_models_in_directory(model_type: Optional[str] = None) -> List[dict]:
    """List model files in the models directory"""
    base_path = Path(MODELS_BASE_PATH)
    if not base_path.exists():
        base_path.mkdir(parents=True, exist_ok=True)
        return []
    
    models = []
    
    # If specific type requested, only scan that folder
    folders_to_scan = [model_type] if model_type else MODEL_TYPES.keys()
    
    for folder in folders_to_scan:
        folder_path = base_path / folder
        if not folder_path.exists():
            continue
        for file in folder_path.glob("*"):
            if file.suffix.lower() in [".safetensors", ".ckpt", ".pt", ".pth", ".bin"]:
                stat = file.stat()
                models.append({
                    "name": file.name,
                    "type": folder,
                    "type_label": MODEL_TYPES.get(folder, folder),
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat()
                })
    
    return sorted(models, key=lambda x: (x["type"], x["name"]))


@router.get("/models", response_class=HTMLResponse)
async def admin_models_list(
    request: Request,
    current_user: dict = Depends(get_current_admin),
    type_filter: Optional[str] = None
):
    """List installed models"""
    models = list_models_in_directory(type_filter)
    
    return templates.TemplateResponse("admin/models.html", {
        "request": request,
        "user": current_user,
        "messages": [],
        "models": models,
        "models_path": MODELS_BASE_PATH,
        "model_types": MODEL_TYPES,
        "type_filter": type_filter
    })


@router.post("/models/install")
async def admin_install_model(
    url: str = Form(...),
    model_type: str = Form("checkpoints"),
    filename: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_admin)
):
    """Install a model from URL (legacy form submit - redirects)"""
    if not filename:
        filename = url.split("/")[-1].split("?")[0]
    
    if model_type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid model type: {model_type}")
    
    if not any(filename.lower().endswith(ext) for ext in [".safetensors", ".ckpt", ".pt", ".pth", ".bin"]):
        raise HTTPException(status_code=400, detail="Invalid model file extension")
    
    models_path = Path(MODELS_BASE_PATH) / model_type
    models_path.mkdir(parents=True, exist_ok=True)
    target_path = models_path / filename
    
    await db.add_log(
        action="model_install_start",
        user_id=current_user["id"],
        details=f"Installing model: {filename} from {url}"
    )
    
    try:
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(target_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
        
        await db.add_log(
            action="model_install_success",
            user_id=current_user["id"],
            details=f"Installed model: {filename}"
        )
        
        return RedirectResponse(url="/admin/models", status_code=303)
    
    except Exception as e:
        await db.add_log(
            action="model_install_error",
            user_id=current_user["id"],
            details=f"Failed to install {filename}: {str(e)}",
            status="error"
        )
        raise HTTPException(status_code=500, detail=f"Failed to download model: {str(e)}")


# Background download task
async def background_download_model(download_id: str, url: str, model_type: str, filename: str, user_id: int):
    """Background task to download a model with progress tracking"""
    global active_downloads
    
    models_path = Path(MODELS_BASE_PATH) / model_type
    models_path.mkdir(parents=True, exist_ok=True)
    target_path = models_path / filename
    
    try:
        await db.add_log(
            action="model_install_start",
            user_id=user_id,
            details=f"Installing model: {filename} from {url}"
        )
        
        async with httpx.AsyncClient(timeout=3600.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                
                active_downloads[download_id]["total"] = total_size
                active_downloads[download_id]["status"] = "downloading"
                
                # Speed tracking variables
                import time
                start_time = time.time()
                last_speed_update = start_time
                last_downloaded_for_speed = 0
                
                with open(target_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        # Check if cancelled
                        if active_downloads.get(download_id, {}).get("status") == "cancelled":
                            f.close()
                            if target_path.exists():
                                target_path.unlink()
                            return
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            progress = int((downloaded / total_size) * 100)
                        else:
                            progress = 0
                        
                        # Calculate download speed (bytes/sec) - update every 0.5 seconds
                        current_time = time.time()
                        time_diff = current_time - last_speed_update
                        if time_diff >= 0.5:
                            bytes_diff = downloaded - last_downloaded_for_speed
                            speed = bytes_diff / time_diff if time_diff > 0 else 0
                            active_downloads[download_id]["speed"] = speed
                            last_speed_update = current_time
                            last_downloaded_for_speed = downloaded
                        
                        active_downloads[download_id]["downloaded"] = downloaded
                        active_downloads[download_id]["progress"] = progress
        
        active_downloads[download_id]["status"] = "complete"
        active_downloads[download_id]["progress"] = 100
        active_downloads[download_id]["completed_at"] = datetime.utcnow().isoformat()
        
        await db.add_log(
            action="model_install_success",
            user_id=user_id,
            details=f"Installed model: {filename}"
        )
        
    except Exception as e:
        active_downloads[download_id]["status"] = "error"
        active_downloads[download_id]["error"] = str(e)
        
        # Clean up partial file
        if target_path.exists():
            try:
                target_path.unlink()
            except:
                pass
        
        await db.add_log(
            action="model_install_error",
            user_id=user_id,
            details=f"Failed to install {filename}: {str(e)}",
            status="error"
        )


@router.post("/models/install/start")
async def admin_start_model_install(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    model_type: str = Form("checkpoints"),
    filename: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_admin)
):
    """Start a background model download"""
    if not filename:
        filename = url.split("/")[-1].split("?")[0]
    
    if model_type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid model type: {model_type}")
    
    if not any(filename.lower().endswith(ext) for ext in [".safetensors", ".ckpt", ".pt", ".pth", ".bin"]):
        raise HTTPException(status_code=400, detail="Invalid model file extension")
    
    # Generate unique download ID
    download_id = str(uuid.uuid4())[:8]
    
    # Initialize download state
    active_downloads[download_id] = {
        "id": download_id,
        "status": "starting",
        "progress": 0,
        "downloaded": 0,
        "total": 0,
        "speed": 0,
        "filename": filename,
        "model_type": model_type,
        "url": url,
        "error": None,
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": None,
        "user_id": current_user["id"]
    }
    
    # Start background download
    asyncio.create_task(background_download_model(download_id, url, model_type, filename, current_user["id"]))
    
    return {"download_id": download_id, "status": "started", "filename": filename}


@router.get("/models/downloads")
async def admin_get_downloads(current_user: dict = Depends(get_current_admin)):
    """Get all active and recent downloads"""
    # Clean up old completed downloads (older than 1 hour)
    cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    to_remove = []
    for did, download in active_downloads.items():
        if download["status"] in ("complete", "error", "cancelled"):
            completed_at = download.get("completed_at")
            if completed_at and completed_at < cutoff:
                to_remove.append(did)
    for did in to_remove:
        del active_downloads[did]
    
    return {"downloads": list(active_downloads.values())}


@router.get("/models/downloads/{download_id}")
async def admin_get_download_status(download_id: str, current_user: dict = Depends(get_current_admin)):
    """Get status of a specific download"""
    if download_id not in active_downloads:
        raise HTTPException(status_code=404, detail="Download not found")
    return active_downloads[download_id]


@router.delete("/models/downloads/{download_id}")
async def admin_cancel_download(download_id: str, current_user: dict = Depends(get_current_admin)):
    """Cancel an active download"""
    if download_id not in active_downloads:
        raise HTTPException(status_code=404, detail="Download not found")
    
    if active_downloads[download_id]["status"] == "downloading":
        active_downloads[download_id]["status"] = "cancelled"
        active_downloads[download_id]["completed_at"] = datetime.utcnow().isoformat()
        return {"status": "cancelled"}
    
    return {"status": active_downloads[download_id]["status"]}


@router.get("/models/downloads/{download_id}/stream")
async def admin_stream_download_progress(
    request: Request,
    download_id: str,
    current_user: dict = Depends(get_current_admin)
):
    """SSE stream for download progress"""
    if download_id not in active_downloads:
        raise HTTPException(status_code=404, detail="Download not found")
    
    async def generate_progress():
        last_progress = -1
        while True:
            if await request.is_disconnected():
                break
            
            if download_id not in active_downloads:
                yield {"event": "progress", "data": json.dumps({"status": "not_found"})}
                break
            
            download = active_downloads[download_id]
            current_progress = download["progress"]
            
            # Always send update if status changed or progress changed
            if current_progress != last_progress or download["status"] in ("complete", "error", "cancelled"):
                last_progress = current_progress
                yield {"event": "progress", "data": json.dumps(download)}
                
                if download["status"] in ("complete", "error", "cancelled"):
                    break
            
            await asyncio.sleep(0.5)
    
    return EventSourceResponse(generate_progress())


@router.get("/models/install/stream")
async def admin_install_model_stream(
    request: Request,
    url: str,
    model_type: str = "checkpoints",
    filename: Optional[str] = None,
    current_user: dict = Depends(get_current_admin)
):
    """Install a model with SSE progress streaming (legacy - starts background download)"""
    if not filename:
        filename = url.split("/")[-1].split("?")[0]
    
    if model_type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid model type: {model_type}")
    
    if not any(filename.lower().endswith(ext) for ext in [".safetensors", ".ckpt", ".pt", ".pth", ".bin"]):
        raise HTTPException(status_code=400, detail="Invalid model file extension")
    
    # Generate unique download ID and start background task
    download_id = str(uuid.uuid4())[:8]
    
    active_downloads[download_id] = {
        "id": download_id,
        "status": "starting",
        "progress": 0,
        "downloaded": 0,
        "total": 0,
        "speed": 0,
        "filename": filename,
        "model_type": model_type,
        "url": url,
        "error": None,
        "started_at": datetime.utcnow().isoformat(),
        "completed_at": None,
        "user_id": current_user["id"]
    }
    
    asyncio.create_task(background_download_model(download_id, url, model_type, filename, current_user["id"]))
    
    # Stream progress from background task
    async def generate_progress():
        last_progress = -1
        while True:
            if await request.is_disconnected():
                break
            
            if download_id not in active_downloads:
                yield {"event": "progress", "data": json.dumps({"status": "not_found", "filename": filename})}
                break
            
            download = active_downloads[download_id]
            current_progress = download["progress"]
            
            if current_progress != last_progress or download["status"] in ("complete", "error", "cancelled"):
                last_progress = current_progress
                yield {"event": "progress", "data": json.dumps({
                    "status": download["status"],
                    "progress": download["progress"],
                    "downloaded": download["downloaded"],
                    "total": download["total"],
                    "filename": download["filename"],
                    "error": download.get("error"),
                    "download_id": download_id
                })}
                
                if download["status"] in ("complete", "error", "cancelled"):
                    break
            
            await asyncio.sleep(0.5)
    
    return EventSourceResponse(generate_progress())


@router.post("/models/{model_type}/{model_name}/delete")
async def admin_delete_model(
    model_type: str,
    model_name: str,
    current_user: dict = Depends(get_current_admin)
):
    """Delete a model"""
    if model_type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid model type: {model_type}")
    
    models_path = Path(MODELS_BASE_PATH) / model_type
    target_path = models_path / model_name
    
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Model not found")
    
    try:
        target_path.unlink()
        
        await db.add_log(
            action="model_delete",
            user_id=current_user["id"],
            details=f"Deleted model: {model_name}"
        )
        
        return RedirectResponse(url="/admin/models", status_code=303)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete model: {str(e)}")


# ============================================
# ComfyUI Operations
# ============================================

async def get_comfyui_public_url(request: Request) -> str:
    """Build public ComfyUI URL from request host"""
    # Get the host from the request (e.g., remote.ranchcomputing.com)
    host = request.headers.get("host", "localhost").split(":")[0]
    scheme = request.headers.get("x-forwarded-proto", "http")
    # Get public port from database setting, fallback to env var
    public_port = await db.get_setting("comfyui_public_port")
    port = int(public_port) if public_port else COMFYUI_PUBLIC_PORT
    return f"{scheme}://{host}:{port}"


async def get_comfyui_status(request: Request = None) -> dict:
    """Check ComfyUI container/service status"""
    # Get public port from database setting, fallback to env var
    public_port_str = await db.get_setting("comfyui_public_port") if request else None
    public_port = int(public_port_str) if public_port_str else COMFYUI_PUBLIC_PORT
    public_url = await get_comfyui_public_url(request) if request else f"http://localhost:{public_port}"
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{COMFYUI_INTERNAL_URL}/system_stats")
            if response.status_code == 200:
                return {
                    "running": True,
                    "url": public_url,
                    "internal_url": COMFYUI_INTERNAL_URL,
                    "port": public_port,
                    "details": response.json()
                }
    except:
        pass
    
    return {
        "running": False,
        "url": public_url,
        "internal_url": COMFYUI_INTERNAL_URL,
        "port": public_port,
        "details": None
    }


@router.get("/comfyui/status")
async def admin_comfyui_status(request: Request, current_user: dict = Depends(get_current_admin)):
    """Get ComfyUI status"""
    return await get_comfyui_status(request)


@router.post("/comfyui/start")
async def admin_start_comfyui(
    request: Request,
    current_user: dict = Depends(get_current_admin)
):
    """Start ComfyUI container"""
    try:
        # Get HOST_PROJECT_DIR for volume mounts (must be host path, not container path)
        host_project_dir = os.getenv("HOST_PROJECT_DIR", os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env["HOST_PROJECT_DIR"] = host_project_dir
        
        result = subprocess.run(
            "docker compose -f docker-compose-comfyui.yml up -d",
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            shell=True,
            env=env
        )
        
        await db.add_log(
            action="comfyui_start",
            user_id=current_user["id"],
            ip=request.client.host if request.client else None,
            details=f"Output: {result.stdout or result.stderr}",
            status="success" if result.returncode == 0 else "error"
        )
        
        return {"success": result.returncode == 0, "output": result.stdout or result.stderr}
    
    except Exception as e:
        await db.add_log(
            action="comfyui_start",
            user_id=current_user["id"],
            details=str(e),
            status="error"
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/comfyui/stop")
async def admin_stop_comfyui(
    request: Request,
    current_user: dict = Depends(get_current_admin)
):
    """Stop ComfyUI container"""
    try:
        host_project_dir = os.getenv("HOST_PROJECT_DIR", os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env["HOST_PROJECT_DIR"] = host_project_dir
        
        result = subprocess.run(
            "docker compose -f docker-compose-comfyui.yml stop",
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            shell=True,
            env=env
        )
        
        await db.add_log(
            action="comfyui_stop",
            user_id=current_user["id"],
            ip=request.client.host if request.client else None,
            details=f"Output: {result.stdout or result.stderr}",
            status="success" if result.returncode == 0 else "error"
        )
        
        return {"success": result.returncode == 0, "output": result.stdout or result.stderr}
    
    except Exception as e:
        await db.add_log(
            action="comfyui_stop",
            user_id=current_user["id"],
            details=str(e),
            status="error"
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/comfyui/restart")
async def admin_restart_comfyui(
    request: Request,
    current_user: dict = Depends(get_current_admin)
):
    """Restart ComfyUI container"""
    try:
        host_project_dir = os.getenv("HOST_PROJECT_DIR", os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env["HOST_PROJECT_DIR"] = host_project_dir
        
        result = subprocess.run(
            "docker compose -f docker-compose-comfyui.yml restart",
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            shell=True,
            env=env
        )
        
        await db.add_log(
            action="comfyui_restart",
            user_id=current_user["id"],
            ip=request.client.host if request.client else None,
            details=f"Output: {result.stdout or result.stderr}",
            status="success" if result.returncode == 0 else "error"
        )
        
        return {"success": result.returncode == 0, "output": result.stdout or result.stderr}
    
    except Exception as e:
        await db.add_log(
            action="comfyui_restart",
            user_id=current_user["id"],
            details=str(e),
            status="error"
        )


# ============================================
# Admin Settings
# ============================================

@router.get("/settings")
async def get_admin_settings(current_user: dict = Depends(get_current_admin)):
    """Get all admin settings"""
    settings = await db.get_all_settings()
    return {
        "comfyui_public_port": settings.get("comfyui_public_port", str(COMFYUI_PORT))
    }


@router.post("/settings")
async def update_admin_settings(
    request: Request,
    current_user: dict = Depends(get_current_admin)
):
    """Update admin settings"""
    data = await request.json()
    
    # Update ComfyUI public port if provided
    if "comfyui_public_port" in data:
        port = str(data["comfyui_public_port"])
        # Validate port number
        try:
            port_num = int(port)
            if not (1 <= port_num <= 65535):
                raise ValueError("Port must be between 1 and 65535")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        await db.set_setting("comfyui_public_port", port)
        
        await db.add_log(
            action="settings_update",
            user_id=current_user["id"],
            ip=request.client.host if request.client else None,
            details=f"Updated comfyui_public_port to {port}"
        )
    
    return {"success": True, "message": "Settings updated"}
