"""
Stripe Payment Integration Module
Handles checkout sessions, webhooks, and payment processing for:
- Top-up RCC packs
- Subscription plans
"""

import os
from typing import Optional

import stripe
from fastapi import HTTPException, Request
from dotenv import load_dotenv

from database import db
from wallet import (
    get_topup_pack, get_topup_packs,
    get_subscription_plan, get_subscription_plans,
    grant_topup_rcc, grant_subscription_rcc
)

load_dotenv()

# Stripe configuration
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")

# Base URL for redirects
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")


def is_stripe_configured() -> bool:
    """Check if Stripe is properly configured"""
    return bool(stripe.api_key and STRIPE_WEBHOOK_SECRET)


# ============================================
# Top-up Checkout
# ============================================

async def create_topup_checkout(
    user_id: int,
    pack_id: str,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None
) -> dict:
    """
    Create a Stripe Checkout session for a top-up pack.
    
    Returns:
        dict with checkout_url and session_id
    """
    if not is_stripe_configured():
        raise HTTPException(status_code=500, detail="Stripe not configured")
    
    pack = get_topup_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=400, detail=f"Unknown pack: {pack_id}")
    
    # Get user email for Stripe
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Default URLs
    success_url = success_url or f"{BASE_URL}/payment/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = cancel_url or f"{BASE_URL}/payment/cancel"
    
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": pack["currency"],
                    "unit_amount": pack["price"],
                    "product_data": {
                        "name": f"{pack['name']} - {pack['credits']} RCC",
                        "description": f"Ranch Cloud Credits Top-up Pack"
                    }
                },
                "quantity": 1
            }],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=user.get("email"),
            metadata={
                "type": "topup",
                "user_id": str(user_id),
                "pack_id": pack_id,
                "credits": str(pack["credits"])
            }
        )
        
        # Log the checkout creation
        await db.add_log(
            action="checkout_topup_created",
            user_id=user_id,
            details=f"Pack: {pack_id}, Session: {session.id}"
        )
        
        return {
            "checkout_url": session.url,
            "session_id": session.id
        }
    
    except stripe.error.StripeError as e:
        await db.add_log(
            action="checkout_topup_error",
            user_id=user_id,
            details=str(e),
            status="error"
        )
        raise HTTPException(status_code=400, detail=str(e))


# ============================================
# Subscription Checkout
# ============================================

async def create_subscription_checkout(
    user_id: int,
    plan_id: str,
    billing_period: str = "monthly",
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None
) -> dict:
    """
    Create a Stripe Checkout session for a subscription.
    
    Args:
        user_id: User subscribing
        plan_id: Plan ID (starter, pro, enterprise)
        billing_period: "monthly" or "yearly"
    
    Returns:
        dict with checkout_url and session_id
    """
    if not is_stripe_configured():
        raise HTTPException(status_code=500, detail="Stripe not configured")
    
    plan = get_subscription_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_id}")
    
    # Get user email for Stripe
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get price based on billing period
    if billing_period == "yearly":
        price_id = plan.get("stripe_price_yearly")
        amount = plan["price_yearly"]
    else:
        price_id = plan.get("stripe_price_monthly")
        amount = plan["price_monthly"]
    
    # Default URLs
    success_url = success_url or f"{BASE_URL}/payment/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = cancel_url or f"{BASE_URL}/payment/cancel"
    
    try:
        # If we have a predefined price ID, use it
        if price_id:
            line_items = [{"price": price_id, "quantity": 1}]
        else:
            # Create price on the fly (for POC)
            line_items = [{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount,
                    "recurring": {
                        "interval": "year" if billing_period == "yearly" else "month"
                    },
                    "product_data": {
                        "name": f"{plan['name']} Plan",
                        "description": f"{plan['monthly_rcc']} RCC/month"
                    }
                },
                "quantity": 1
            }]
        
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=user.get("email"),
            metadata={
                "type": "subscription",
                "user_id": str(user_id),
                "plan_id": plan_id,
                "monthly_rcc": str(plan["monthly_rcc"]),
                "billing_period": billing_period
            }
        )
        
        # Log the checkout creation
        await db.add_log(
            action="checkout_subscription_created",
            user_id=user_id,
            details=f"Plan: {plan_id}, Period: {billing_period}, Session: {session.id}"
        )
        
        return {
            "checkout_url": session.url,
            "session_id": session.id
        }
    
    except stripe.error.StripeError as e:
        await db.add_log(
            action="checkout_subscription_error",
            user_id=user_id,
            details=str(e),
            status="error"
        )
        raise HTTPException(status_code=400, detail=str(e))


# ============================================
# Webhook Handler
# ============================================

async def handle_stripe_webhook(request: Request) -> dict:
    """
    Handle incoming Stripe webhooks.
    
    Processes:
    - checkout.session.completed (top-up)
    - invoice.paid (subscription renewal)
    
    Ensures idempotency via stripe_event_id.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    event_id = event["id"]
    event_type = event["type"]
    
    # Check idempotency - has this event been processed?
    existing = await db.get_payment_by_stripe_event(event_id)
    if existing:
        # Already processed, return success
        return {"status": "already_processed", "event_id": event_id}
    
    # Process the event
    if event_type == "checkout.session.completed":
        return await handle_checkout_completed(event)
    elif event_type == "invoice.paid":
        return await handle_invoice_paid(event)
    elif event_type == "invoice.payment_failed":
        return await handle_payment_failed(event)
    else:
        # Log unhandled event types
        await db.add_log(
            action="stripe_webhook_unhandled",
            details=f"Event type: {event_type}, ID: {event_id}"
        )
        return {"status": "unhandled", "event_type": event_type}


async def handle_checkout_completed(event: dict) -> dict:
    """
    Handle checkout.session.completed event.
    Credits RCC for top-up purchases.
    """
    session = event["data"]["object"]
    event_id = event["id"]
    metadata = session.get("metadata", {})
    
    payment_type = metadata.get("type")
    user_id = int(metadata.get("user_id", 0))
    
    if not user_id:
        await db.add_log(
            action="webhook_error",
            details=f"Missing user_id in checkout session: {session['id']}"
        )
        return {"status": "error", "detail": "Missing user_id"}
    
    if payment_type == "topup":
        # Top-up payment
        credits = int(metadata.get("credits", 0))
        pack_id = metadata.get("pack_id")
        
        # Record payment for audit
        await db.create_payment(
            user_id=user_id,
            payment_type="topup",
            amount=session.get("amount_total", 0),
            currency=session.get("currency", "usd"),
            external_ref=session["id"],
            stripe_event_id=event_id
        )
        
        # Grant RCC credits
        await grant_topup_rcc(
            user_id=user_id,
            amount=credits,
            external_ref=event_id
        )
        
        await db.add_log(
            action="topup_completed",
            user_id=user_id,
            details=f"Pack: {pack_id}, Credits: {credits}"
        )
        
        return {"status": "success", "type": "topup", "credits": credits}
    
    elif payment_type == "subscription":
        # Subscription started - first payment
        monthly_rcc = int(metadata.get("monthly_rcc", 0))
        plan_id = metadata.get("plan_id")
        
        # Record payment for audit
        await db.create_payment(
            user_id=user_id,
            payment_type="subscription",
            amount=session.get("amount_total", 0),
            currency=session.get("currency", "usd"),
            external_ref=session.get("subscription"),
            stripe_event_id=event_id
        )
        
        # Grant initial RCC credits
        await grant_subscription_rcc(
            user_id=user_id,
            amount=monthly_rcc,
            external_ref=event_id
        )
        
        await db.add_log(
            action="subscription_started",
            user_id=user_id,
            details=f"Plan: {plan_id}, Monthly RCC: {monthly_rcc}"
        )
        
        return {"status": "success", "type": "subscription", "credits": monthly_rcc}
    
    return {"status": "success", "type": "unknown"}


async def handle_invoice_paid(event: dict) -> dict:
    """
    Handle invoice.paid event.
    Credits monthly RCC for subscription renewals.
    """
    invoice = event["data"]["object"]
    event_id = event["id"]
    subscription_id = invoice.get("subscription")
    
    # Get subscription to find user and plan
    if not subscription_id:
        return {"status": "skipped", "detail": "Not a subscription invoice"}
    
    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
    except stripe.error.StripeError as e:
        await db.add_log(
            action="webhook_error",
            details=f"Failed to retrieve subscription {subscription_id}: {e}"
        )
        return {"status": "error", "detail": str(e)}
    
    metadata = subscription.get("metadata", {})
    user_id = int(metadata.get("user_id", 0))
    monthly_rcc = int(metadata.get("monthly_rcc", 0))
    plan_id = metadata.get("plan_id")
    
    if not user_id or not monthly_rcc:
        await db.add_log(
            action="webhook_error",
            details=f"Missing metadata in subscription {subscription_id}"
        )
        return {"status": "error", "detail": "Missing subscription metadata"}
    
    # Record payment for audit
    await db.create_payment(
        user_id=user_id,
        payment_type="subscription",
        amount=invoice.get("amount_paid", 0),
        currency=invoice.get("currency", "usd"),
        external_ref=invoice["id"],
        stripe_event_id=event_id
    )
    
    # Grant monthly RCC credits
    await grant_subscription_rcc(
        user_id=user_id,
        amount=monthly_rcc,
        external_ref=event_id
    )
    
    await db.add_log(
        action="subscription_renewed",
        user_id=user_id,
        details=f"Plan: {plan_id}, Monthly RCC: {monthly_rcc}"
    )
    
    return {"status": "success", "type": "subscription_renewal", "credits": monthly_rcc}


async def handle_payment_failed(event: dict) -> dict:
    """
    Handle invoice.payment_failed event.
    Logs the failure for follow-up.
    """
    invoice = event["data"]["object"]
    event_id = event["id"]
    subscription_id = invoice.get("subscription")
    
    if subscription_id:
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            metadata = subscription.get("metadata", {})
            user_id = int(metadata.get("user_id", 0))
            
            if user_id:
                await db.add_log(
                    action="payment_failed",
                    user_id=user_id,
                    details=f"Invoice: {invoice['id']}, Subscription: {subscription_id}",
                    status="error"
                )
        except stripe.error.StripeError:
            pass
    
    await db.add_log(
        action="payment_failed",
        details=f"Invoice: {invoice['id']}",
        status="error"
    )
    
    return {"status": "logged", "type": "payment_failed"}


# ============================================
# Helper Functions
# ============================================

def get_stripe_publishable_key() -> str:
    """Get the Stripe publishable key for frontend"""
    return STRIPE_PUBLISHABLE_KEY or ""


async def get_payment_history(user_id: int, limit: int = 50) -> list:
    """Get payment history for a user"""
    return await db.get_user_payments(user_id, limit=limit)
