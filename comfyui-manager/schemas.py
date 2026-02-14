"""
Pydantic schemas for ComfyUI Manager API
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field
from enum import Enum


# ============================================
# Enums
# ============================================

class JobType(str, Enum):
    IMAGE_TASK = "IMAGE_TASK"
    VIDEO_TASK = "VIDEO_TASK"


class JobStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RCCReason(str, Enum):
    JOB_RESERVE = "JOB_RESERVE"
    JOB_RELEASE = "JOB_RELEASE"
    SUBSCRIPTION_GRANT = "SUBSCRIPTION_GRANT"
    TOPUP_GRANT = "TOPUP_GRANT"
    MANUAL_ADJUST = "MANUAL_ADJUST"
    ADMIN_BYPASS = "ADMIN_BYPASS"


class PaymentType(str, Enum):
    TOPUP = "topup"
    SUBSCRIPTION = "subscription"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


# ============================================
# User Schemas
# ============================================

class UserBase(BaseModel):
    email: EmailStr


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)


class UserLogin(UserBase):
    password: str


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    is_admin: Optional[bool] = None


class User(UserBase):
    id: int
    is_admin: bool = False
    gitlab_id: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class UserWithBalance(User):
    rcc_balance: int = 0


class UserAdminAdjust(BaseModel):
    rcc_delta: int = Field(..., description="Amount to add (positive) or remove (negative)")
    reason: Optional[str] = None


# ============================================
# Job Schemas
# ============================================

class JobCreate(BaseModel):
    type: JobType
    metadata: Optional[dict] = None


class JobBase(BaseModel):
    id: int
    user_id: int
    type: JobType
    cost_rcc: int
    status: JobStatus
    duration_ms: Optional[int] = None
    output_uri: Optional[str] = None
    metadata: Optional[dict] = None
    admin_bypass: bool = False
    created_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class JobResponse(JobBase):
    pass


class JobList(BaseModel):
    jobs: List[JobBase]
    total: int
    page: int
    per_page: int


# ============================================
# RCC Ledger Schemas
# ============================================

class RCCLedgerEntry(BaseModel):
    id: int
    user_id: int
    delta: int
    reason: RCCReason
    job_id: Optional[int] = None
    external_ref: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class RCCBalance(BaseModel):
    user_id: int
    balance: int


class RCCHistory(BaseModel):
    entries: List[RCCLedgerEntry]
    balance: int


# ============================================
# Payment Schemas
# ============================================

class TopupPack(BaseModel):
    pack_id: str
    name: str
    credits: int
    price: int  # in cents
    currency: str = "usd"


class TopupCheckoutRequest(BaseModel):
    pack_id: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class SubscriptionPlan(BaseModel):
    plan_id: str
    name: str
    monthly_rcc: int
    price_monthly: int  # in cents
    price_yearly: Optional[int] = None  # in cents


class SubscriptionCheckoutRequest(BaseModel):
    plan_id: str
    billing_period: str = "monthly"  # "monthly" or "yearly"
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PaymentBase(BaseModel):
    id: int
    user_id: int
    provider: str = "stripe"
    type: PaymentType
    amount: int
    currency: str
    status: PaymentStatus
    external_ref: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class CheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str


# ============================================
# Credit Pricing Schemas
# ============================================

class JobTypePricing(BaseModel):
    """Pricing configuration for a single job type"""
    base_cost: int = Field(..., ge=0, description="Base RCC cost for this job type")
    multiplier: float = Field(..., ge=0, description="Multiplier applied to base cost (e.g., 1.5 means 50% more)")
    description: Optional[str] = None


class CreditPricingConfig(BaseModel):
    """Full credit pricing configuration"""
    IMAGE_TASK: JobTypePricing
    VIDEO_TASK: JobTypePricing
    charge_mode: str = Field("on_creation", description="When to charge: 'on_creation' or 'on_completion'")
    refund_on_failure: bool = True
    updated_at: Optional[str] = None
    updated_by: Optional[int] = None


class CreditPricingUpdate(BaseModel):
    """Request to update credit pricing for a job type"""
    job_type: str = Field(..., description="Job type to update: 'IMAGE_TASK' or 'VIDEO_TASK'")
    base_cost: Optional[int] = Field(None, ge=0, description="New base RCC cost")
    multiplier: Optional[float] = Field(None, ge=0, description="New multiplier ratio")


class ChargeModeUpdate(BaseModel):
    """Request to update the charge mode"""
    mode: str = Field(..., description="Charge mode: 'on_creation' or 'on_completion'")


class TaskCompletionRequest(BaseModel):
    """Request to process task completion for credit charging"""
    job_id: int
    success: bool = True


class TaskCompletionResponse(BaseModel):
    """Response from task completion processing"""
    charged: bool
    amount: int
    balance: int
    job_id: int
    charge_mode: str
    error: Optional[str] = None


# ============================================
# Log Schemas
# ============================================

class LogEntry(BaseModel):
    id: int
    user_id: Optional[int] = None
    ip: Optional[str] = None
    action: str
    details: Optional[str] = None
    status: str = "success"
    created_at: datetime
    
    class Config:
        from_attributes = True


class LogList(BaseModel):
    logs: List[LogEntry]
    total: int


# ============================================
# Auth Schemas
# ============================================

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    email: Optional[str] = None
    user_id: Optional[int] = None
    is_admin: bool = False


# ============================================
# Admin Dashboard Schemas
# ============================================

class DashboardStats(BaseModel):
    total_users: int
    active_users: int  # users with RCC > 0
    total_jobs_24h: int
    total_jobs_7d: int
    rcc_consumed_24h: int
    rcc_consumed_7d: int
    failed_jobs_24h: int
    models_count: int


class ModelInfo(BaseModel):
    name: str
    size_mb: float
    created_at: datetime


class ModelInstallRequest(BaseModel):
    url: str
    filename: Optional[str] = None


class ComfyUIStatus(BaseModel):
    running: bool
    container_id: Optional[str] = None
    uptime: Optional[str] = None
    url: str


# ============================================
# API Response Schemas
# ============================================

class MessageResponse(BaseModel):
    message: str
    success: bool = True


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


# ============================================
# Me / Profile Schemas
# ============================================

class MeResponse(BaseModel):
    user: UserWithBalance
    recent_jobs: List[JobBase] = []
    recent_transactions: List[RCCLedgerEntry] = []
