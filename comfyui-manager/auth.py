"""
Authentication module for ComfyUI Manager
Handles JWT tokens, password hashing, and user authentication
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
import bcrypt
from dotenv import load_dotenv

from database import db
from schemas import TokenData

load_dotenv()

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# OAuth2 scheme - uses token from header
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# HTTP Bearer for API access
security = HTTPBearer(auto_error=False)


# ============================================
# Password Utilities
# ============================================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def get_password_hash(password: str) -> str:
    """Hash a password"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


# ============================================
# JWT Token Utilities
# ============================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[TokenData]:
    """Decode and validate a JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        user_id: int = payload.get("user_id")
        is_admin: bool = payload.get("is_admin", False)
        
        if email is None:
            return None
        
        return TokenData(email=email, user_id=user_id, is_admin=is_admin)
    except JWTError:
        return None


# ============================================
# Authentication Dependencies
# ============================================

async def get_token_from_request(
    request: Request,
    oauth2_token: Optional[str] = Depends(oauth2_scheme),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """
    Extract token from various sources:
    1. Authorization header (Bearer token)
    2. OAuth2 password flow
    3. Cookie (for web UI)
    """
    # Try Bearer token first
    if bearer and bearer.credentials:
        return bearer.credentials
    
    # Try OAuth2 token
    if oauth2_token:
        return oauth2_token
    
    # Try cookie
    token = request.cookies.get("access_token")
    if token:
        # Remove "Bearer " prefix if present
        if token.startswith("Bearer "):
            token = token[7:]
        return token
    
    return None


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(get_token_from_request)
) -> dict:
    """
    Get the current authenticated user.
    Raises 401 if not authenticated.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    if not token:
        raise credentials_exception
    
    token_data = decode_token(token)
    if token_data is None:
        raise credentials_exception
    
    # Get user from database
    user = await db.get_user_by_email(token_data.email)
    if user is None:
        raise credentials_exception
    
    return user


async def get_current_user_optional(
    request: Request,
    token: Optional[str] = Depends(get_token_from_request)
) -> Optional[dict]:
    """
    Get the current user if authenticated, otherwise return None.
    Does not raise exceptions.
    """
    if not token:
        return None
    
    token_data = decode_token(token)
    if token_data is None:
        return None
    
    user = await db.get_user_by_email(token_data.email)
    return user


async def get_current_admin(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """
    Get the current user and verify they are an admin.
    Raises 403 if not an admin.
    """
    if not current_user.get("is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


# ============================================
# User Authentication Functions
# ============================================

async def authenticate_user(email: str, password: str) -> Optional[dict]:
    """Authenticate a user with email and password"""
    user = await db.get_user_by_email(email)
    if not user:
        return None
    
    if not user.get("password_hash"):
        return None
    
    if not verify_password(password, user["password_hash"]):
        return None
    
    return user


async def register_user(email: str, password: str) -> dict:
    """Register a new user"""
    # Check if user already exists
    existing_user = await db.get_user_by_email(email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Hash password and create user
    password_hash = get_password_hash(password)
    user = await db.create_user(email=email, password_hash=password_hash)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user"
        )
    
    # Log the registration
    await db.add_log(action="user_register", user_id=user["id"], details=f"New user registered: {email}")
    
    return user


def create_user_token(user: dict) -> str:
    """Create a JWT token for a user"""
    access_token = create_access_token(
        data={
            "sub": user["email"],
            "user_id": user["id"],
            "is_admin": user.get("is_admin", False)
        }
    )
    return access_token


# ============================================
# Admin Allowlist Check
# ============================================

def is_admin_allowed(email: str) -> bool:
    """
    Check if an email is in the admin allowlist.
    Admins can be allowed by exact email or by domain.
    """
    allowed_emails = os.getenv("ADMIN_ALLOWED_EMAILS", "").split(",")
    allowed_domains = os.getenv("ADMIN_ALLOWED_DOMAINS", "").split(",")
    
    # Clean up the lists
    allowed_emails = [e.strip().lower() for e in allowed_emails if e.strip()]
    allowed_domains = [d.strip().lower() for d in allowed_domains if d.strip()]
    
    email_lower = email.lower()
    
    # Check exact email match
    if email_lower in allowed_emails:
        return True
    
    # Check domain match
    email_domain = email_lower.split("@")[-1] if "@" in email_lower else ""
    if email_domain in allowed_domains:
        return True
    
    return False
