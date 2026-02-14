"""
RCC Wallet Module
Handles all Ranch Cloud Credits operations including:
- Balance calculation from ledger
- Reserve/Release for job execution
- Grants from subscriptions and top-ups
- Manual adjustments by admins
- Configurable credit pricing with completion-based charging
"""

import os
import json
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import HTTPException, status
from dotenv import load_dotenv

from database import db, RCCReason, JobType

load_dotenv()

# ============================================
# Credit Pricing Configuration
# ============================================
# This system allows dynamic adjustment of credit costs
# The multiplier represents how many RCC per completed task

# Default pricing configuration (can be updated at runtime)
_credit_pricing: Dict[str, Any] = {
    "IMAGE_TASK": {
        "base_cost": int(os.getenv("JOB_COST_IMAGE", "1")),
        "multiplier": float(os.getenv("JOB_MULTIPLIER_IMAGE", "1.0")),
        "description": "Image generation task"
    },
    "VIDEO_TASK": {
        "base_cost": int(os.getenv("JOB_COST_VIDEO", "5")),
        "multiplier": float(os.getenv("JOB_MULTIPLIER_VIDEO", "1.0")),
        "description": "Video generation task"
    },
    # Settings for how credits are charged
    "charge_mode": os.getenv("CREDIT_CHARGE_MODE", "on_creation"),  # "on_creation" or "on_completion"
    "refund_on_failure": True,
    "updated_at": None,
    "updated_by": None
}

# Backwards compatibility
JOB_COST_IMAGE = _credit_pricing["IMAGE_TASK"]["base_cost"]
JOB_COST_VIDEO = _credit_pricing["VIDEO_TASK"]["base_cost"]


def get_credit_pricing() -> Dict[str, Any]:
    """
    Get the current credit pricing configuration.
    Returns a copy to prevent direct modification.
    """
    return _credit_pricing.copy()


def update_credit_pricing(
    job_type: str,
    base_cost: Optional[int] = None,
    multiplier: Optional[float] = None,
    admin_user_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Update credit pricing for a job type.
    
    Args:
        job_type: "IMAGE_TASK" or "VIDEO_TASK"
        base_cost: New base cost in RCC (if provided)
        multiplier: New multiplier ratio (if provided)
        admin_user_id: Admin making the change
    
    Returns:
        Updated pricing configuration
    """
    if job_type not in ["IMAGE_TASK", "VIDEO_TASK"]:
        raise ValueError(f"Invalid job type: {job_type}")
    
    if base_cost is not None:
        if base_cost < 0:
            raise ValueError("Base cost cannot be negative")
        _credit_pricing[job_type]["base_cost"] = base_cost
    
    if multiplier is not None:
        if multiplier < 0:
            raise ValueError("Multiplier cannot be negative")
        _credit_pricing[job_type]["multiplier"] = multiplier
    
    _credit_pricing["updated_at"] = datetime.utcnow().isoformat()
    _credit_pricing["updated_by"] = admin_user_id
    
    return get_credit_pricing()


def set_charge_mode(mode: str, admin_user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Set whether credits are charged on creation or completion.
    
    Args:
        mode: "on_creation" or "on_completion"
        admin_user_id: Admin making the change
    
    Returns:
        Updated pricing configuration
    """
    if mode not in ["on_creation", "on_completion"]:
        raise ValueError(f"Invalid charge mode: {mode}. Must be 'on_creation' or 'on_completion'")
    
    _credit_pricing["charge_mode"] = mode
    _credit_pricing["updated_at"] = datetime.utcnow().isoformat()
    _credit_pricing["updated_by"] = admin_user_id
    
    return get_credit_pricing()


def calculate_job_cost(job_type: JobType) -> int:
    """
    Calculate the RCC cost for a job type using base cost * multiplier.
    
    The multiplier allows adjusting the credit consumption ratio.
    For example:
    - base_cost=1, multiplier=1.0 → 1 RCC per task
    - base_cost=1, multiplier=2.0 → 2 RCC per task
    - base_cost=1, multiplier=0.5 → 0.5 → rounds to 1 RCC per task (minimum 1)
    """
    type_key = job_type.value
    if type_key not in _credit_pricing:
        raise ValueError(f"Unknown job type: {job_type}")
    
    config = _credit_pricing[type_key]
    calculated = config["base_cost"] * config["multiplier"]
    
    # Round and ensure minimum of 1 (unless base is 0)
    if config["base_cost"] == 0:
        return 0
    return max(1, round(calculated))


def get_job_cost(job_type: JobType) -> int:
    """
    Get the RCC cost for a job type.
    Uses the configurable pricing system with multiplier.
    
    As per PRD V1 (defaults):
    - IMAGE_TASK = 1 RCC (adjustable)
    - VIDEO_TASK = 5 RCC (adjustable)
    """
    return calculate_job_cost(job_type)


def should_charge_on_creation() -> bool:
    """Check if credits should be charged on job creation."""
    return _credit_pricing.get("charge_mode", "on_creation") == "on_creation"


def should_charge_on_completion() -> bool:
    """Check if credits should be charged on job completion."""
    return _credit_pricing.get("charge_mode", "on_creation") == "on_completion"


async def get_balance(user_id: int) -> int:
    """
    Get the current RCC balance for a user.
    Calculated as the sum of all ledger entries (source of truth).
    """
    return await db.get_user_rcc_balance(user_id)


async def check_sufficient_balance(user_id: int, required: int) -> bool:
    """
    Check if user has sufficient RCC balance for an operation.
    """
    balance = await get_balance(user_id)
    return balance >= required


async def reserve_rcc(user_id: int, job_id: int, job_type: JobType, is_admin: bool = False) -> dict:
    """
    Reserve RCC for a job (debit at creation).
    
    As per PRD V1 (FR-04):
    - Non-admin: creates ledger entry with JOB_RESERVE (negative delta)
    - Admin: creates ledger entry with ADMIN_BYPASS (delta=0)
    
    Returns the ledger entry.
    Raises HTTPException if insufficient balance (non-admin only).
    """
    cost = get_job_cost(job_type)
    
    if is_admin:
        # Admin bypass - log but don't debit
        entry = await db.add_rcc_entry(
            user_id=user_id,
            delta=0,
            reason=RCCReason.ADMIN_BYPASS,
            job_id=job_id
        )
        return entry
    
    # Check balance for non-admin
    balance = await get_balance(user_id)
    if balance < cost:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient RCC balance. Required: {cost}, Available: {balance}"
        )
    
    # Debit RCC (negative delta)
    entry = await db.add_rcc_entry(
        user_id=user_id,
        delta=-cost,
        reason=RCCReason.JOB_RESERVE,
        job_id=job_id
    )
    
    return entry


async def release_rcc(user_id: int, job_id: int, cost: int) -> dict:
    """
    Release (refund) RCC for a failed job.
    
    As per PRD V1 (FR-05):
    - Job failed → ledger JOB_RELEASE with positive delta
    - Restores the full cost to user balance
    
    Returns the ledger entry.
    """
    # Only refund if there was a cost (not admin bypass)
    if cost <= 0:
        return None
    
    entry = await db.add_rcc_entry(
        user_id=user_id,
        delta=cost,  # Positive delta = credit
        reason=RCCReason.JOB_RELEASE,
        job_id=job_id
    )
    
    return entry


async def charge_on_completion(user_id: int, job_id: int, job_type: JobType, is_admin: bool = False) -> Optional[dict]:
    """
    Charge RCC when a ComfyUI task is completed successfully.
    
    This function is used when charge_mode is "on_completion" instead of "on_creation".
    The cost is calculated using the base cost * multiplier for the job type.
    
    Args:
        user_id: User who completed the task
        job_id: The job that was completed
        job_type: Type of job (IMAGE_TASK or VIDEO_TASK)
        is_admin: Whether the user is an admin (admins don't pay)
    
    Returns:
        The ledger entry if charged, None if admin bypass or no charge needed.
    
    Raises:
        HTTPException if insufficient balance
    """
    if is_admin:
        # Admin bypass - log but don't charge
        entry = await db.add_rcc_entry(
            user_id=user_id,
            delta=0,
            reason=RCCReason.ADMIN_BYPASS,
            job_id=job_id
        )
        return entry
    
    cost = get_job_cost(job_type)
    
    # Check balance
    balance = await get_balance(user_id)
    if balance < cost:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient RCC balance for task completion. Required: {cost}, Available: {balance}"
        )
    
    # Debit RCC for completed task
    entry = await db.add_rcc_entry(
        user_id=user_id,
        delta=-cost,
        reason=RCCReason.JOB_RESERVE,  # Using same reason for now, could add JOB_COMPLETE
        job_id=job_id
    )
    
    return entry


async def process_task_completion(
    user_id: int,
    job_id: int,
    job_type: JobType,
    is_admin: bool = False,
    task_success: bool = True
) -> dict:
    """
    Process credit operations when a ComfyUI task completes.
    
    This is the main hook for connecting credit usage to task completion.
    The ratio of RCC to task completion is determined by the multiplier setting.
    
    Args:
        user_id: User who ran the task
        job_id: The job ID
        job_type: Type of job
        is_admin: Admin bypass flag
        task_success: Whether the task succeeded
    
    Returns:
        dict with charge info: {"charged": bool, "amount": int, "balance": int}
    """
    charge_mode = _credit_pricing.get("charge_mode", "on_creation")
    
    result = {
        "charged": False,
        "amount": 0,
        "balance": await get_balance(user_id),
        "job_id": job_id,
        "charge_mode": charge_mode
    }
    
    if charge_mode == "on_completion" and task_success and not is_admin:
        # Charge on successful completion
        try:
            entry = await charge_on_completion(user_id, job_id, job_type, is_admin)
            if entry:
                result["charged"] = True
                result["amount"] = abs(entry.get("delta", 0))
                result["balance"] = await get_balance(user_id)
        except HTTPException as e:
            result["error"] = str(e.detail)
            raise
    
    return result


async def grant_subscription_rcc(user_id: int, amount: int, external_ref: Optional[str] = None) -> dict:
    """
    Grant RCC from a subscription payment.
    
    As per PRD V1:
    - Monthly credit via invoice.paid webhook
    - Idempotent via external_ref (stripe_event_id)
    
    Returns the ledger entry.
    """
    entry = await db.add_rcc_entry(
        user_id=user_id,
        delta=amount,
        reason=RCCReason.SUBSCRIPTION_GRANT,
        external_ref=external_ref
    )
    
    return entry


async def grant_topup_rcc(user_id: int, amount: int, external_ref: Optional[str] = None) -> dict:
    """
    Grant RCC from a top-up purchase.
    
    As per PRD V1:
    - Credits via checkout.session.completed webhook
    - Idempotent via external_ref (stripe_event_id)
    
    Returns the ledger entry.
    """
    entry = await db.add_rcc_entry(
        user_id=user_id,
        delta=amount,
        reason=RCCReason.TOPUP_GRANT,
        external_ref=external_ref
    )
    
    return entry


async def manual_adjust_rcc(user_id: int, delta: int, admin_user_id: int, reason: Optional[str] = None) -> dict:
    """
    Manually adjust RCC balance (admin only).
    
    As per PRD V1:
    - Used for support/corrections
    - Fully logged and audited
    
    Args:
        user_id: User to adjust
        delta: Amount to add (positive) or remove (negative)
        admin_user_id: Admin performing the adjustment
        reason: Optional reason for the adjustment
    
    Returns the ledger entry.
    """
    external_ref = f"admin_adjust:{admin_user_id}:{reason or 'no_reason'}"
    
    entry = await db.add_rcc_entry(
        user_id=user_id,
        delta=delta,
        reason=RCCReason.MANUAL_ADJUST,
        external_ref=external_ref
    )
    
    # Log the admin action
    await db.add_log(
        action="rcc_manual_adjust",
        user_id=admin_user_id,
        details=f"Adjusted user {user_id} RCC by {delta}. Reason: {reason or 'not specified'}"
    )
    
    return entry


async def get_rcc_history(user_id: int, limit: int = 50, offset: int = 0) -> dict:
    """
    Get RCC transaction history for a user.
    
    Returns:
        dict with entries list and current balance
    """
    entries = await db.get_user_rcc_history(user_id, limit=limit, offset=offset)
    balance = await get_balance(user_id)
    
    return {
        "entries": entries,
        "balance": balance
    }


# ============================================
# Top-up Pack Definitions
# ============================================

def get_topup_packs() -> list:
    """
    Get available top-up packs.
    Prices and credits from environment or defaults.
    """
    return [
        {
            "pack_id": "small",
            "name": "Starter Pack",
            "credits": int(os.getenv("RCC_PACK_SMALL_CREDITS", "10")),
            "price": int(os.getenv("RCC_PACK_SMALL_PRICE", "500")),  # cents
            "currency": "usd"
        },
        {
            "pack_id": "medium",
            "name": "Creator Pack",
            "credits": int(os.getenv("RCC_PACK_MEDIUM_CREDITS", "50")),
            "price": int(os.getenv("RCC_PACK_MEDIUM_PRICE", "2000")),  # cents
            "currency": "usd"
        },
        {
            "pack_id": "large",
            "name": "Pro Pack",
            "credits": int(os.getenv("RCC_PACK_LARGE_CREDITS", "150")),
            "price": int(os.getenv("RCC_PACK_LARGE_PRICE", "5000")),  # cents
            "currency": "usd"
        }
    ]


def get_topup_pack(pack_id: str) -> Optional[dict]:
    """Get a specific top-up pack by ID"""
    packs = get_topup_packs()
    for pack in packs:
        if pack["pack_id"] == pack_id:
            return pack
    return None


# ============================================
# Subscription Plan Definitions
# ============================================

def get_subscription_plans() -> list:
    """
    Get available subscription plans.
    Monthly RCC credits from environment or defaults.
    """
    return [
        {
            "plan_id": "starter",
            "name": "Starter",
            "monthly_rcc": int(os.getenv("SUBSCRIPTION_STARTER_RCC", "20")),
            "price_monthly": 999,  # $9.99/month
            "price_yearly": 9990,  # $99.90/year (save ~17%)
            "stripe_price_monthly": os.getenv("STRIPE_PRICE_STARTER_MONTHLY"),
            "stripe_price_yearly": os.getenv("STRIPE_PRICE_STARTER_YEARLY")
        },
        {
            "plan_id": "pro",
            "name": "Pro",
            "monthly_rcc": int(os.getenv("SUBSCRIPTION_PRO_RCC", "100")),
            "price_monthly": 2999,  # $29.99/month
            "price_yearly": 29990,  # $299.90/year (save ~17%)
            "stripe_price_monthly": os.getenv("STRIPE_PRICE_PRO_MONTHLY"),
            "stripe_price_yearly": os.getenv("STRIPE_PRICE_PRO_YEARLY")
        },
        {
            "plan_id": "enterprise",
            "name": "Enterprise",
            "monthly_rcc": int(os.getenv("SUBSCRIPTION_ENTERPRISE_RCC", "500")),
            "price_monthly": 9999,  # $99.99/month
            "price_yearly": 99990,  # $999.90/year (save ~17%)
            "stripe_price_monthly": os.getenv("STRIPE_PRICE_ENTERPRISE_MONTHLY"),
            "stripe_price_yearly": os.getenv("STRIPE_PRICE_ENTERPRISE_YEARLY")
        }
    ]


def get_subscription_plan(plan_id: str) -> Optional[dict]:
    """Get a specific subscription plan by ID"""
    plans = get_subscription_plans()
    for plan in plans:
        if plan["plan_id"] == plan_id:
            return plan
    return None
