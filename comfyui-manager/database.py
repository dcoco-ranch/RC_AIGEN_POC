"""
Database module - Supabase with SQLite fallback
Handles all database operations for ComfyUI Manager
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from enum import Enum

from dotenv import load_dotenv

load_dotenv()

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./comfyui_manager.db")

# Determine which database to use
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

if USE_SUPABASE:
    from supabase import create_client, Client
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase connected")
else:
    supabase = None
    print("⚠️ Supabase not configured. Using SQLite fallback.")


# ============================================
# Enums for RCC Ledger
# ============================================

class RCCReason(str, Enum):
    JOB_RESERVE = "JOB_RESERVE"
    JOB_RELEASE = "JOB_RELEASE"
    SUBSCRIPTION_GRANT = "SUBSCRIPTION_GRANT"
    TOPUP_GRANT = "TOPUP_GRANT"
    MANUAL_ADJUST = "MANUAL_ADJUST"
    ADMIN_BYPASS = "ADMIN_BYPASS"


class JobType(str, Enum):
    IMAGE_TASK = "IMAGE_TASK"
    VIDEO_TASK = "VIDEO_TASK"


class JobStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ============================================
# SQLite Helper Functions
# ============================================

SQLITE_DB_PATH = DATABASE_URL.replace("sqlite:///", "") if DATABASE_URL.startswith("sqlite:///") else "./comfyui_manager.db"


@contextmanager
def get_sqlite_connection():
    """Context manager for SQLite connections"""
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def init_sqlite_db():
    """Initialize SQLite database with all required tables"""
    with get_sqlite_connection() as conn:
        cursor = conn.cursor()
        
        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                is_admin BOOLEAN DEFAULT FALSE,
                gitlab_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                cost_rcc INTEGER NOT NULL,
                status TEXT DEFAULT 'created',
                duration_ms INTEGER,
                output_uri TEXT,
                metadata TEXT,
                admin_bypass BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # RCC Ledger table (CRITICAL - source of truth for credits)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rcc_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                reason TEXT NOT NULL,
                job_id INTEGER,
                external_ref TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
        """)
        
        # Payments table (audit)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                provider TEXT DEFAULT 'stripe',
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT DEFAULT 'usd',
                status TEXT DEFAULT 'pending',
                external_ref TEXT UNIQUE,
                stripe_event_id TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # Logs table (ops & audit)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                ip TEXT,
                action TEXT NOT NULL,
                details TEXT,
                status TEXT DEFAULT 'success',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # Create indexes for better performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rcc_ledger_user_id ON rcc_ledger(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_external_ref ON payments(external_ref)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user_id)")
        
        print("✅ SQLite database initialized")


# ============================================
# Database Abstraction Layer
# ============================================

class Database:
    """
    Database abstraction layer that works with both Supabase and SQLite
    """
    
    def __init__(self):
        self.use_supabase = USE_SUPABASE
    
    # -------------------- Users --------------------
    
    async def create_user(self, email: str, password_hash: Optional[str] = None, 
                         is_admin: bool = False, gitlab_id: Optional[str] = None) -> Dict[str, Any]:
        if self.use_supabase:
            result = supabase.table("users").insert({
                "email": email,
                "password_hash": password_hash,
                "is_admin": is_admin,
                "gitlab_id": gitlab_id
            }).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (email, password_hash, is_admin, gitlab_id) VALUES (?, ?, ?, ?)",
                    (email, password_hash, is_admin, gitlab_id)
                )
                user_id = cursor.lastrowid
                cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("users").select("*").eq("email", email).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("users").select("*").eq("id", user_id).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_user_by_gitlab_id(self, gitlab_id: str) -> Optional[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("users").select("*").eq("gitlab_id", gitlab_id).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE gitlab_id = ?", (gitlab_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def update_user(self, user_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("users").update(kwargs).eq("id", user_id).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                values = list(kwargs.values()) + [user_id]
                cursor.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
                cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_all_users(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("users").select("*").range(offset, offset + limit - 1).execute()
            return result.data
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users LIMIT ? OFFSET ?", (limit, offset))
                return [dict(row) for row in cursor.fetchall()]
    
    async def count_users(self) -> int:
        if self.use_supabase:
            result = supabase.table("users").select("id", count="exact").execute()
            return result.count or 0
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users")
                return cursor.fetchone()[0]
    
    # -------------------- Jobs --------------------
    
    async def create_job(self, user_id: int, job_type: JobType, cost_rcc: int,
                        admin_bypass: bool = False, metadata: Optional[str] = None) -> Dict[str, Any]:
        if self.use_supabase:
            result = supabase.table("jobs").insert({
                "user_id": user_id,
                "type": job_type.value,
                "cost_rcc": cost_rcc,
                "status": JobStatus.CREATED.value,
                "admin_bypass": admin_bypass,
                "metadata": metadata
            }).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO jobs (user_id, type, cost_rcc, status, admin_bypass, metadata) 
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (user_id, job_type.value, cost_rcc, JobStatus.CREATED.value, admin_bypass, metadata)
                )
                job_id = cursor.lastrowid
                cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("jobs").select("*").eq("id", job_id).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def update_job(self, job_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("jobs").update(kwargs).eq("id", job_id).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                values = list(kwargs.values()) + [job_id]
                cursor.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
                cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_user_jobs(self, user_id: int, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("jobs").select("*").eq("user_id", user_id).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            return result.data
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (user_id, limit, offset)
                )
                return [dict(row) for row in cursor.fetchall()]
    
    async def get_all_jobs(self, limit: int = 100, offset: int = 0, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.use_supabase:
            query = supabase.table("jobs").select("*")
            if status:
                query = query.eq("status", status)
            result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            return result.data
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                if status:
                    cursor.execute(
                        "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (status, limit, offset)
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (limit, offset)
                    )
                return [dict(row) for row in cursor.fetchall()]
    
    async def count_jobs(self, since: Optional[datetime] = None) -> int:
        if self.use_supabase:
            query = supabase.table("jobs").select("id", count="exact")
            if since:
                query = query.gte("created_at", since.isoformat())
            result = query.execute()
            return result.count or 0
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                if since:
                    cursor.execute("SELECT COUNT(*) FROM jobs WHERE created_at >= ?", (since.isoformat(),))
                else:
                    cursor.execute("SELECT COUNT(*) FROM jobs")
                return cursor.fetchone()[0]
    
    async def count_failed_jobs(self, since: Optional[datetime] = None) -> int:
        if self.use_supabase:
            query = supabase.table("jobs").select("id", count="exact").eq("status", "failed")
            if since:
                query = query.gte("created_at", since.isoformat())
            result = query.execute()
            return result.count or 0
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                if since:
                    cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed' AND created_at >= ?", (since.isoformat(),))
                else:
                    cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'")
                return cursor.fetchone()[0]
    
    # -------------------- RCC Ledger --------------------
    
    async def add_rcc_entry(self, user_id: int, delta: int, reason: RCCReason,
                           job_id: Optional[int] = None, external_ref: Optional[str] = None) -> Dict[str, Any]:
        if self.use_supabase:
            result = supabase.table("rcc_ledger").insert({
                "user_id": user_id,
                "delta": delta,
                "reason": reason.value,
                "job_id": job_id,
                "external_ref": external_ref
            }).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO rcc_ledger (user_id, delta, reason, job_id, external_ref) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (user_id, delta, reason.value, job_id, external_ref)
                )
                entry_id = cursor.lastrowid
                cursor.execute("SELECT * FROM rcc_ledger WHERE id = ?", (entry_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_user_rcc_balance(self, user_id: int) -> int:
        """Calculate user's RCC balance from ledger (source of truth)"""
        if self.use_supabase:
            result = supabase.table("rcc_ledger").select("delta").eq("user_id", user_id).execute()
            return sum(entry["delta"] for entry in result.data) if result.data else 0
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COALESCE(SUM(delta), 0) FROM rcc_ledger WHERE user_id = ?", (user_id,))
                return cursor.fetchone()[0]
    
    async def get_user_rcc_history(self, user_id: int, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("rcc_ledger").select("*").eq("user_id", user_id).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            return result.data
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM rcc_ledger WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (user_id, limit, offset)
                )
                return [dict(row) for row in cursor.fetchall()]
    
    async def get_total_rcc_consumed(self, since: Optional[datetime] = None) -> int:
        """Get total RCC consumed (negative deltas for JOB_RESERVE)"""
        if self.use_supabase:
            query = supabase.table("rcc_ledger").select("delta").eq("reason", "JOB_RESERVE")
            if since:
                query = query.gte("created_at", since.isoformat())
            result = query.execute()
            return abs(sum(entry["delta"] for entry in result.data)) if result.data else 0
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                if since:
                    cursor.execute(
                        "SELECT COALESCE(SUM(ABS(delta)), 0) FROM rcc_ledger WHERE reason = 'JOB_RESERVE' AND created_at >= ?",
                        (since.isoformat(),)
                    )
                else:
                    cursor.execute("SELECT COALESCE(SUM(ABS(delta)), 0) FROM rcc_ledger WHERE reason = 'JOB_RESERVE'")
                return cursor.fetchone()[0]
    
    # -------------------- Payments --------------------
    
    async def create_payment(self, user_id: int, payment_type: str, amount: int,
                            currency: str = "usd", external_ref: Optional[str] = None,
                            stripe_event_id: Optional[str] = None) -> Dict[str, Any]:
        if self.use_supabase:
            result = supabase.table("payments").insert({
                "user_id": user_id,
                "type": payment_type,
                "amount": amount,
                "currency": currency,
                "status": "pending",
                "external_ref": external_ref,
                "stripe_event_id": stripe_event_id
            }).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO payments (user_id, type, amount, currency, status, external_ref, stripe_event_id) 
                       VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                    (user_id, payment_type, amount, currency, external_ref, stripe_event_id)
                )
                payment_id = cursor.lastrowid
                cursor.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def update_payment(self, payment_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("payments").update(kwargs).eq("id", payment_id).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                values = list(kwargs.values()) + [payment_id]
                cursor.execute(f"UPDATE payments SET {set_clause} WHERE id = ?", values)
                cursor.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_payment_by_stripe_event(self, stripe_event_id: str) -> Optional[Dict[str, Any]]:
        """Check idempotency - has this Stripe event been processed?"""
        if self.use_supabase:
            result = supabase.table("payments").select("*").eq("stripe_event_id", stripe_event_id).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM payments WHERE stripe_event_id = ?", (stripe_event_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_user_payments(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        if self.use_supabase:
            result = supabase.table("payments").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute()
            return result.data
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit)
                )
                return [dict(row) for row in cursor.fetchall()]
    
    # -------------------- Logs --------------------
    
    async def add_log(self, action: str, user_id: Optional[int] = None, ip: Optional[str] = None,
                     details: Optional[str] = None, status: str = "success") -> Dict[str, Any]:
        if self.use_supabase:
            result = supabase.table("logs").insert({
                "user_id": user_id,
                "ip": ip,
                "action": action,
                "details": details,
                "status": status
            }).execute()
            return result.data[0] if result.data else None
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO logs (user_id, ip, action, details, status) VALUES (?, ?, ?, ?, ?)",
                    (user_id, ip, action, details, status)
                )
                log_id = cursor.lastrowid
                cursor.execute("SELECT * FROM logs WHERE id = ?", (log_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
    
    async def get_logs(self, limit: int = 100, offset: int = 0, action: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.use_supabase:
            query = supabase.table("logs").select("*")
            if action:
                query = query.eq("action", action)
            result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            return result.data
        else:
            with get_sqlite_connection() as conn:
                cursor = conn.cursor()
                if action:
                    cursor.execute(
                        "SELECT * FROM logs WHERE action = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (action, limit, offset)
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (limit, offset)
                    )
                return [dict(row) for row in cursor.fetchall()]


# ============================================
# Initialize Database
# ============================================

def init_db():
    """Initialize the database (creates tables if using SQLite)"""
    if not USE_SUPABASE:
        init_sqlite_db()
    else:
        print("✅ Using Supabase - ensure tables are created via Supabase dashboard")


# Global database instance
db = Database()
