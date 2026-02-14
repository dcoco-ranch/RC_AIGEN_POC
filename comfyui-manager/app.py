"""
ComfyUI Manager - Main FastAPI Application
Portal for ComfyUI SaaS with RCC monetization
"""

import os
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

# Get the directory where this file is located
BASE_DIR = Path(__file__).resolve().parent

from fastapi import FastAPI, Request, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import OAuth2PasswordRequestForm
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import modules
from database import db, init_db, JobType, JobStatus
from auth import (
    get_current_user, get_current_user_optional, get_current_admin,
    authenticate_user, register_user, create_user_token
)
from auth_gitlab import gitlab_login, gitlab_callback, gitlab_logout
from wallet import (
    get_balance, reserve_rcc, release_rcc, get_rcc_history,
    get_job_cost, get_topup_packs, get_subscription_plans,
    get_credit_pricing, update_credit_pricing, set_charge_mode,
    process_task_completion, should_charge_on_creation
)
from payment import (
    create_topup_checkout, create_subscription_checkout,
    handle_stripe_webhook, get_stripe_publishable_key, get_payment_history
)
from schemas import (
    UserCreate, UserLogin, Token, JobCreate, JobResponse,
    RCCBalance, RCCHistory, TopupCheckoutRequest, SubscriptionCheckoutRequest,
    CheckoutSessionResponse, MessageResponse, MeResponse,
    CreditPricingConfig, CreditPricingUpdate, ChargeModeUpdate,
    TaskCompletionRequest, TaskCompletionResponse
)

# Import admin router
from admin import router as admin_router

# Import Docker manager for ComfyUI control
from docker_manager import docker_manager

# ============================================
# FastAPI App Configuration
# ============================================

app = FastAPI(
    title="ComfyUI Manager",
    description="Ranch Cloud Credits (RCC) powered ComfyUI SaaS Portal",
    version="1.0.0"
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Include admin routes
app.include_router(admin_router)


# ============================================
# Startup & Shutdown Events
# ============================================

@app.on_event("startup")
async def startup_event():
    """Initialize database and services on startup"""
    init_db()
    print("✅ ComfyUI Manager started")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    print("🛑 ComfyUI Manager shutting down")


# ============================================
# Public Routes (No Auth Required)
# ============================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: Optional[dict] = Depends(get_current_user_optional)):
    """Home page"""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "messages": [],
        "topup_packs": get_topup_packs(),
        "subscription_plans": get_subscription_plans()
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse("login.html", {"request": request, "user": None, "messages": []})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page"""
    return templates.TemplateResponse("register.html", {"request": request, "user": None, "messages": []})


# ============================================
# Authentication Routes
# ============================================

@app.post("/auth/register", response_model=Token)
async def register(user_data: UserCreate):
    """Register a new user"""
    user = await register_user(user_data.email, user_data.password)
    token = create_user_token(user)
    return Token(access_token=token)


@app.post("/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login with email and password"""
    user = await authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    token = create_user_token(user)
    
    # Log the login
    await db.add_log(
        action="user_login",
        user_id=user["id"],
        details=f"User login: {user['email']}"
    )
    
    return Token(access_token=token)


@app.post("/auth/login/form")
async def login_form(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    """Login via form (for web UI)"""
    user = await authenticate_user(email, password)
    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "user": None,
            "messages": [],
            "error": "Incorrect email or password"
        })
    
    token = create_user_token(user)
    
    # Log the login
    await db.add_log(
        action="user_login",
        user_id=user["id"],
        ip=request.client.host if request.client else None,
        details=f"User login: {user['email']}"
    )
    
    # Check balance and stop ComfyUI service if empty (to avoid resource usage without credits)
    user_balance = await get_balance(user["id"])
    if user_balance <= 0:
        try:
            status = await docker_manager.get_status()
            if status.get("status") == "running":
                await docker_manager.stop()
                await db.add_log(
                    action="comfyui_auto_stop",
                    user_id=user["id"],
                    details=f"ComfyUI stopped on login - no credits available"
                )
        except Exception as e:
            # Log but don't block login
            await db.add_log(
                action="comfyui_auto_stop_failed",
                user_id=user["id"],
                details=f"Failed to auto-stop ComfyUI: {str(e)}"
            )
    
    # Redirect with cookie
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        max_age=3600,
        samesite="lax",
        path="/"
    )
    return response


@app.get("/auth/logout")
async def logout(request: Request):
    """Logout user"""
    return await gitlab_logout(request)


# GitLab OAuth Routes
@app.get("/auth/gitlab")
async def gitlab_auth():
    """Initiate GitLab OAuth for admin login"""
    # In dev mode, redirect to dev admin login
    if os.getenv("DEBUG", "false").lower() == "true":
        return RedirectResponse(url="/auth/dev-admin")
    return await gitlab_login()


@app.get("/auth/dev-admin")
async def dev_admin_login():
    """Dev mode: Quick admin login without GitLab OAuth"""
    if os.getenv("DEBUG", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")
    
    try:
        # Check if dev admin user exists, create if not
        email = "admin@dev.local"
        user = await db.get_user_by_email(email)
        
        if not user:
            # Create dev admin user
            from auth import get_password_hash
            password_hash = get_password_hash("admin123")
            user = await db.create_user(email=email, password_hash=password_hash, is_admin=True)
        elif not user.get("is_admin"):
            # Make sure they're admin
            await db.update_user(user["id"], is_admin=True)
            user["is_admin"] = True
        
        # Create token
        token = create_user_token(user)
        
        # Redirect to admin dashboard with cookie
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(
            key="access_token",
            value=f"Bearer {token}",
            httponly=True,
            max_age=3600,
            samesite="lax",
            path="/"
        )
        return response
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Dev admin login error: {e}")
        print(error_trace)
        # In DEBUG mode, show full error in response
        return HTMLResponse(
            content=f"""
            <html>
            <head><title>Dev Admin Login Error</title></head>
            <body style="font-family: monospace; padding: 20px;">
                <h1>Dev Admin Login Error</h1>
                <p><strong>Error:</strong> {str(e)}</p>
                <h2>Traceback:</h2>
                <pre style="background: #f0f0f0; padding: 15px; overflow-x: auto;">{error_trace}</pre>
                <h2>Debug Info:</h2>
                <ul>
                    <li>SUPABASE_URL set: {bool(os.getenv('SUPABASE_URL'))}</li>
                    <li>SUPABASE_KEY set: {bool(os.getenv('SUPABASE_KEY'))}</li>
                    <li>Database mode: {'Supabase' if db.use_supabase else 'SQLite'}</li>
                </ul>
            </body>
            </html>
            """,
            status_code=500
        )


@app.get("/auth/gitlab/callback")
async def gitlab_auth_callback(request: Request):
    """Handle GitLab OAuth callback"""
    return await gitlab_callback(request)


# ============================================
# User Profile Routes
# ============================================

@app.get("/me", response_model=MeResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current user profile with RCC balance"""
    user_id = current_user["id"]
    
    # Get balance
    balance = await get_balance(user_id)
    
    # Get recent jobs
    recent_jobs = await db.get_user_jobs(user_id, limit=5)
    
    # Get recent transactions
    rcc_history = await get_rcc_history(user_id, limit=5)
    
    user_with_balance = {**current_user, "rcc_balance": balance}
    
    return MeResponse(
        user=user_with_balance,
        recent_jobs=recent_jobs,
        recent_transactions=rcc_history["entries"]
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request, current_user: dict = Depends(get_current_user)):
    """User dashboard page"""
    user_id = current_user["id"]
    
    balance = await get_balance(user_id)
    jobs = await db.get_user_jobs(user_id, limit=10)
    rcc_history = await get_rcc_history(user_id, limit=10)
    
    # Get current credit pricing
    pricing = get_credit_pricing()
    credit_pricing = {
        "IMAGE_TASK": {
            "cost": get_job_cost(JobType.IMAGE_TASK),
            "base_cost": pricing["IMAGE_TASK"]["base_cost"],
            "multiplier": pricing["IMAGE_TASK"]["multiplier"]
        },
        "VIDEO_TASK": {
            "cost": get_job_cost(JobType.VIDEO_TASK),
            "base_cost": pricing["VIDEO_TASK"]["base_cost"],
            "multiplier": pricing["VIDEO_TASK"]["multiplier"]
        },
        "charge_mode": pricing.get("charge_mode", "on_creation")
    }
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": current_user,
        "messages": [],
        "balance": balance,
        "jobs": jobs,
        "rcc_history": rcc_history["entries"],
        "topup_packs": get_topup_packs(),
        "subscription_plans": get_subscription_plans(),
        "credit_pricing": credit_pricing
    })


# ============================================
# RCC Wallet Routes
# ============================================

@app.get("/wallet/balance", response_model=RCCBalance)
async def get_wallet_balance(current_user: dict = Depends(get_current_user)):
    """Get current RCC balance"""
    balance = await get_balance(current_user["id"])
    return RCCBalance(user_id=current_user["id"], balance=balance)


@app.get("/wallet/history", response_model=RCCHistory)
async def get_wallet_history(
    current_user: dict = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0
):
    """Get RCC transaction history"""
    history = await get_rcc_history(current_user["id"], limit=limit, offset=offset)
    return RCCHistory(entries=history["entries"], balance=history["balance"])


# ============================================
# Credit Pricing Routes
# ============================================

@app.get("/pricing")
async def get_pricing_config():
    """
    Get current credit pricing configuration.
    Public endpoint - shows how much each task type costs.
    """
    pricing = get_credit_pricing()
    return {
        "IMAGE_TASK": {
            "cost": get_job_cost(JobType.IMAGE_TASK),
            "base_cost": pricing["IMAGE_TASK"]["base_cost"],
            "multiplier": pricing["IMAGE_TASK"]["multiplier"],
            "description": pricing["IMAGE_TASK"]["description"]
        },
        "VIDEO_TASK": {
            "cost": get_job_cost(JobType.VIDEO_TASK),
            "base_cost": pricing["VIDEO_TASK"]["base_cost"],
            "multiplier": pricing["VIDEO_TASK"]["multiplier"],
            "description": pricing["VIDEO_TASK"]["description"]
        },
        "charge_mode": pricing.get("charge_mode", "on_creation"),
        "refund_on_failure": pricing.get("refund_on_failure", True)
    }


@app.put("/pricing", dependencies=[Depends(get_current_admin)])
async def update_pricing_config(
    update: CreditPricingUpdate,
    current_user: dict = Depends(get_current_admin)
):
    """
    Update credit pricing for a job type.
    Admin only - adjusts the cost ratio for completed tasks.
    """
    try:
        updated = update_credit_pricing(
            job_type=update.job_type,
            base_cost=update.base_cost,
            multiplier=update.multiplier,
            admin_user_id=current_user["id"]
        )
        
        # Log the change
        await db.add_log(
            action="pricing_updated",
            user_id=current_user["id"],
            details=f"Updated {update.job_type}: base_cost={update.base_cost}, multiplier={update.multiplier}"
        )
        
        return {
            "success": True,
            "message": f"Pricing updated for {update.job_type}",
            "pricing": updated
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/pricing/charge-mode", dependencies=[Depends(get_current_admin)])
async def update_charge_mode(
    update: ChargeModeUpdate,
    current_user: dict = Depends(get_current_admin)
):
    """
    Update when credits are charged (on_creation or on_completion).
    Admin only.
    """
    try:
        updated = set_charge_mode(update.mode, admin_user_id=current_user["id"])
        
        # Log the change
        await db.add_log(
            action="charge_mode_updated",
            user_id=current_user["id"],
            details=f"Changed charge mode to: {update.mode}"
        )
        
        return {
            "success": True,
            "message": f"Charge mode updated to {update.mode}",
            "pricing": updated
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tasks/complete", response_model=TaskCompletionResponse)
async def process_completion(
    request: TaskCompletionRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Process a task completion and charge credits if configured.
    
    This endpoint should be called when a ComfyUI task finishes.
    If charge_mode is "on_completion", credits will be deducted here.
    """
    # Get the job
    job = await db.get_job(request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Check ownership
    if job["user_id"] != current_user["id"] and not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Process the completion
    result = await process_task_completion(
        user_id=job["user_id"],
        job_id=request.job_id,
        job_type=JobType(job["type"]),
        is_admin=job.get("admin_bypass", False),
        task_success=request.success
    )
    
    return TaskCompletionResponse(**result)


# ============================================
# Job Routes
# ============================================

@app.post("/jobs", response_model=JobResponse)
async def create_job(
    job_data: JobCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new compute job.
    - Checks RCC balance (unless admin)
    - Reserves RCC at creation if charge_mode is "on_creation"
    - If charge_mode is "on_completion", credits are charged when task completes
    - Returns job details
    """
    user_id = current_user["id"]
    is_admin = current_user.get("is_admin", False)
    
    # Get job cost (uses base_cost * multiplier)
    cost = get_job_cost(job_data.type)
    
    # Create job record
    job = await db.create_job(
        user_id=user_id,
        job_type=job_data.type,
        cost_rcc=cost,
        admin_bypass=is_admin,
        metadata=str(job_data.metadata) if job_data.metadata else None
    )
    
    if not job:
        raise HTTPException(status_code=500, detail="Failed to create job")
    
    # Reserve RCC only if charging on creation (or log admin bypass)
    if should_charge_on_creation():
        try:
            await reserve_rcc(
                user_id=user_id,
                job_id=job["id"],
                job_type=job_data.type,
                is_admin=is_admin
            )
        except HTTPException as e:
            # If reservation fails (insufficient balance), delete the job
            # In a real implementation, you'd use a transaction
            raise e
    
    # Log job creation
    charge_mode = "on_creation" if should_charge_on_creation() else "on_completion"
    await db.add_log(
        action="job_created",
        user_id=user_id,
        details=f"Job {job['id']}: {job_data.type.value}, Cost: {cost} RCC, Admin: {is_admin}, ChargeMode: {charge_mode}"
    )
    
    return job


@app.get("/jobs", response_model=list)
async def list_jobs(
    current_user: dict = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0
):
    """List user's jobs"""
    jobs = await db.get_user_jobs(current_user["id"], limit=limit, offset=offset)
    return jobs


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, current_user: dict = Depends(get_current_user)):
    """Get job details"""
    job = await db.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Check ownership (unless admin)
    if job["user_id"] != current_user["id"] and not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Not authorized to view this job")
    
    return job


@app.patch("/jobs/{job_id}/status")
async def update_job_status(
    job_id: int,
    status: JobStatus,
    output_uri: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Update job status.
    - If charge_mode is "on_completion" and status is SUCCEEDED, charges credits
    - If failed and charge_mode was "on_creation", releases (refunds) RCC.
    """
    job = await db.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Check ownership (unless admin)
    if job["user_id"] != current_user["id"] and not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Not authorized to update this job")
    
    update_data = {"status": status.value}
    
    if status == JobStatus.RUNNING:
        update_data["started_at"] = datetime.utcnow().isoformat()
    elif status in [JobStatus.SUCCEEDED, JobStatus.FAILED]:
        update_data["ended_at"] = datetime.utcnow().isoformat()
        if job.get("started_at"):
            # Calculate duration
            started = datetime.fromisoformat(job["started_at"].replace("Z", ""))
            duration = (datetime.utcnow() - started).total_seconds() * 1000
            update_data["duration_ms"] = int(duration)
    
    if output_uri:
        update_data["output_uri"] = output_uri
    
    # Update job
    updated_job = await db.update_job(job_id, **update_data)
    
    # Handle credit operations based on job status and charge mode
    if status == JobStatus.SUCCEEDED and not job.get("admin_bypass"):
        # If charging on completion, process the charge now
        if not should_charge_on_creation():
            try:
                result = await process_task_completion(
                    user_id=job["user_id"],
                    job_id=job_id,
                    job_type=JobType(job["type"]),
                    is_admin=job.get("admin_bypass", False),
                    task_success=True
                )
                if result.get("charged"):
                    await db.add_log(
                        action="job_completed_charged",
                        user_id=job["user_id"],
                        details=f"Job {job_id} completed, charged {result['amount']} RCC"
                    )
            except HTTPException as e:
                # If charging fails, log but don't fail the status update
                await db.add_log(
                    action="job_completion_charge_failed",
                    user_id=job["user_id"],
                    details=f"Job {job_id} completed but charge failed: {e.detail}"
                )
    
    elif status == JobStatus.FAILED and not job.get("admin_bypass"):
        # Only refund if we charged on creation
        if should_charge_on_creation():
            await release_rcc(
                user_id=job["user_id"],
                job_id=job_id,
                cost=job["cost_rcc"]
            )
            await db.add_log(
                action="job_failed_refund",
                user_id=job["user_id"],
                details=f"Job {job_id} failed, refunded {job['cost_rcc']} RCC"
            )
    
    return updated_job


# ============================================
# Payment Routes
# ============================================

@app.post("/checkout/topup", response_model=CheckoutSessionResponse)
async def checkout_topup(
    request: TopupCheckoutRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a checkout session for RCC top-up"""
    result = await create_topup_checkout(
        user_id=current_user["id"],
        pack_id=request.pack_id,
        success_url=request.success_url,
        cancel_url=request.cancel_url
    )
    return CheckoutSessionResponse(**result)


@app.post("/checkout/subscription", response_model=CheckoutSessionResponse)
async def checkout_subscription(
    request: SubscriptionCheckoutRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a checkout session for subscription"""
    result = await create_subscription_checkout(
        user_id=current_user["id"],
        plan_id=request.plan_id,
        billing_period=request.billing_period,
        success_url=request.success_url,
        cancel_url=request.cancel_url
    )
    return CheckoutSessionResponse(**result)


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhooks (idempotent)"""
    return await handle_stripe_webhook(request)


@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success(request: Request, session_id: str = None):
    """Payment success page"""
    return templates.TemplateResponse("payment_success.html", {
        "request": request,
        "user": None,
        "messages": [],
        "session_id": session_id
    })


@app.get("/payment/cancel", response_class=HTMLResponse)
async def payment_cancel(request: Request):
    """Payment cancelled page"""
    return templates.TemplateResponse("payment_cancel.html", {"request": request, "user": None, "messages": []})


@app.get("/payments", response_model=list)
async def list_payments(current_user: dict = Depends(get_current_user)):
    """List user's payment history"""
    return await get_payment_history(current_user["id"])


# ============================================
# API Info Routes
# ============================================

@app.get("/api/packs")
async def list_packs():
    """List available top-up packs"""
    return get_topup_packs()


@app.get("/api/plans")
async def list_plans():
    """List available subscription plans"""
    return get_subscription_plans()


@app.get("/api/stripe-key")
async def get_stripe_key():
    """Get Stripe publishable key for frontend"""
    return {"publishable_key": get_stripe_publishable_key()}


# ============================================
# ComfyUI Docker Control Routes
# ============================================

@app.get("/comfyui/status")
async def comfyui_status(request: Request, current_user: dict = Depends(get_current_user)):
    """Get ComfyUI container status (requires authentication)"""
    status = await docker_manager.get_status()
    
    # Build public URL from request host if running
    if status.get("port"):
        host = request.headers.get("host", "localhost").split(":")[0]
        scheme = request.headers.get("x-forwarded-proto", "http")
        # Get public port from database setting, fallback to env var, then internal port
        public_port = await db.get_setting("comfyui_public_port")
        if public_port:
            status["port"] = int(public_port)
        status["url"] = f"{scheme}://{host}:{status['port']}"
    
    return status


@app.post("/comfyui/start")
async def comfyui_start(current_user: dict = Depends(get_current_user)):
    """Start ComfyUI container (requires authentication and credits)"""
    # Check if user has credits (admins bypass this check)
    if not current_user.get("is_admin", False):
        balance = await get_balance(current_user["id"])
        if balance <= 0:
            await db.add_log(
                action="comfyui_start_blocked",
                user_id=current_user["id"],
                details=f"User {current_user['email']} tried to start ComfyUI with no credits"
            )
            return {
                "success": False,
                "message": "Insufficient credits. Please top up your RCC balance to use ComfyUI."
            }
    
    result = await docker_manager.start()
    
    # Log the action
    await db.add_log(
        action="comfyui_start",
        user_id=current_user["id"],
        details=f"User {current_user['email']} started ComfyUI: {result['message']}"
    )
    
    return result


@app.post("/comfyui/stop")
async def comfyui_stop(current_user: dict = Depends(get_current_user)):
    """Stop ComfyUI container (requires authentication)"""
    result = await docker_manager.stop()
    
    # Log the action
    await db.add_log(
        action="comfyui_stop",
        user_id=current_user["id"],
        details=f"User {current_user['email']} stopped ComfyUI: {result['message']}"
    )
    
    return result


@app.post("/comfyui/restart")
async def comfyui_restart(current_user: dict = Depends(get_current_user)):
    """Restart ComfyUI container (requires authentication and credits)"""
    # Check if user has credits (admins bypass this check)
    if not current_user.get("is_admin", False):
        balance = await get_balance(current_user["id"])
        if balance <= 0:
            await db.add_log(
                action="comfyui_restart_blocked",
                user_id=current_user["id"],
                details=f"User {current_user['email']} tried to restart ComfyUI with no credits"
            )
            return {
                "success": False,
                "message": "Insufficient credits. Please top up your RCC balance to use ComfyUI."
            }
    
    result = await docker_manager.restart()
    
    # Log the action
    await db.add_log(
        action="comfyui_restart",
        user_id=current_user["id"],
        details=f"User {current_user['email']} restarted ComfyUI: {result['message']}"
    )
    
    return result


@app.get("/comfyui/logs")
async def comfyui_logs(
    lines: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """Get ComfyUI container logs (requires authentication)"""
    if lines > 500:
        lines = 500  # Limit to prevent excessive data
    
    result = await docker_manager.get_logs(lines=lines)
    return result


@app.get("/comfyui/startup-logs")
async def comfyui_startup_logs(current_user: dict = Depends(get_current_user)):
    """Get ComfyUI startup/pull logs (requires authentication)"""
    result = await docker_manager.get_startup_logs()
    return result


# ============================================
# Health Check
# ============================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0"
    }


# ============================================
# Error Handlers
# ============================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    if request.headers.get("accept", "").startswith("text/html"):
        return templates.TemplateResponse("error.html", {
            "request": request,
            "user": None,
            "messages": [],
            "status_code": exc.status_code,
            "detail": exc.detail
        }, status_code=exc.status_code)
    
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )
