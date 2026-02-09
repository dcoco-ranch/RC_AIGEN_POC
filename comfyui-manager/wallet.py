"""
RCC Wallet Module
Handles all Ranch Cloud Credits operations including:
- Balance calculation from ledger
- Reserve/Release for job execution
- Grants from subscriptions and top-ups
- Manual adjustments by admins
"""

import os
from typing import Optional
from datetime import datetime

from fastapi import HTTPException, status
from dotenv import load_dotenv

from database import db, RCCReason, JobType

load_dotenv()

# Job costs (from environment or defaults from PRD)
JOB_COST_IMAGE = int(os.getenv("JOB_COST_IMAGE", "1"))
JOB_COST_VIDEO = int(os.getenv("JOB_COST_VIDEO", "5"))


def get_job_cost(job_type: JobType) -> int:
    """
    Get the RCC cost for a job type.
    As per PRD V1:
    - IMAGE_TASK = 1 RCC
    - VIDEO_TASK = 5 RCC
    """
    if job_type == JobType.IMAGE_TASK:
        return JOB_COST_IMAGE
    elif job_type == JobType.VIDEO_TASK:
        return JOB_COST_VIDEO
    else:
        raise ValueError(f"Unknown job type: {job_type}")


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
