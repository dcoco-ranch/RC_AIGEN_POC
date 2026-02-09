"""
Admin Module for ComfyUI Manager
Dashboard, user management, model management, and ComfyUI operations
"""

import os
import subprocess
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import httpx
from dotenv import load_dotenv

from database import db
from auth import get_current_admin
from wallet import manual_adjust_rcc, get_balance

load_dotenv()

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")

# Configuration
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://localhost:8188")
MODELS_PATH = os.getenv("MODELS_PATH", "./models/checkpoints")


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
    comfyui_status = await get_comfyui_status()
    
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

def list_models_in_directory() -> List[dict]:
    """List model files in the models directory"""
    models_path = Path(MODELS_PATH)
    if not models_path.exists():
        models_path.mkdir(parents=True, exist_ok=True)
        return []
    
    models = []
    for file in models_path.glob("*"):
        if file.suffix.lower() in [".safetensors", ".ckpt", ".pt", ".pth"]:
            stat = file.stat()
            models.append({
                "name": file.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat()
            })
    
    return sorted(models, key=lambda x: x["name"])


@router.get("/models", response_class=HTMLResponse)
async def admin_models_list(
    request: Request,
    current_user: dict = Depends(get_current_admin)
):
    """List installed models"""
    models = list_models_in_directory()
    
    return templates.TemplateResponse("admin/models.html", {
        "request": request,
        "user": current_user,
        "messages": [],
        "models": models,
        "models_path": MODELS_PATH
    })


@router.post("/models/install")
async def admin_install_model(
    url: str = Form(...),
    filename: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_admin)
):
    """Install a model from URL"""
    if not filename:
        # Extract filename from URL
        filename = url.split("/")[-1].split("?")[0]
    
    # Ensure valid extension
    if not any(filename.lower().endswith(ext) for ext in [".safetensors", ".ckpt", ".pt", ".pth"]):
        raise HTTPException(status_code=400, detail="Invalid model file extension")
    
    models_path = Path(MODELS_PATH)
    models_path.mkdir(parents=True, exist_ok=True)
    
    target_path = models_path / filename
    
    # Log the installation attempt
    await db.add_log(
        action="model_install_start",
        user_id=current_user["id"],
        details=f"Installing model: {filename} from {url}"
    )
    
    try:
        # Download the model
        async with httpx.AsyncClient(timeout=600.0) as client:
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


@router.post("/models/{model_name}/delete")
async def admin_delete_model(
    model_name: str,
    current_user: dict = Depends(get_current_admin)
):
    """Delete a model"""
    models_path = Path(MODELS_PATH)
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

async def get_comfyui_status() -> dict:
    """Check ComfyUI container/service status"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{COMFYUI_URL}/system_stats")
            if response.status_code == 200:
                return {
                    "running": True,
                    "url": COMFYUI_URL,
                    "details": response.json()
                }
    except:
        pass
    
    return {
        "running": False,
        "url": COMFYUI_URL,
        "details": None
    }


@router.get("/comfyui/status")
async def admin_comfyui_status(current_user: dict = Depends(get_current_admin)):
    """Get ComfyUI status"""
    return await get_comfyui_status()


@router.post("/comfyui/start")
async def admin_start_comfyui(
    request: Request,
    current_user: dict = Depends(get_current_admin)
):
    """Start ComfyUI container"""
    try:
        result = subprocess.run(
            ["docker-compose", "up", "-d", "comfyui"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True
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
        result = subprocess.run(
            ["docker-compose", "stop", "comfyui"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True
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
        result = subprocess.run(
            ["docker-compose", "restart", "comfyui"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True
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
        raise HTTPException(status_code=500, detail=str(e))
