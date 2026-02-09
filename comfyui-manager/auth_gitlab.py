"""
GitLab OAuth Authentication for Admin Access
Provides secure admin login via GitLab OAuth2
"""

import os
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

from database import db
from auth import create_user_token, is_admin_allowed

load_dotenv()

# GitLab OAuth Configuration
GITLAB_CLIENT_ID = os.getenv("GITLAB_CLIENT_ID")
GITLAB_CLIENT_SECRET = os.getenv("GITLAB_CLIENT_SECRET")
GITLAB_REDIRECT_URI = os.getenv("GITLAB_REDIRECT_URI", "http://localhost:8000/auth/gitlab/callback")

# GitLab OAuth URLs (for gitlab.com - change for self-hosted)
GITLAB_BASE_URL = os.getenv("GITLAB_BASE_URL", "https://gitlab.com")
GITLAB_AUTH_URL = f"{GITLAB_BASE_URL}/oauth/authorize"
GITLAB_TOKEN_URL = f"{GITLAB_BASE_URL}/oauth/token"
GITLAB_USER_URL = f"{GITLAB_BASE_URL}/api/v4/user"


def is_gitlab_configured() -> bool:
    """Check if GitLab OAuth is properly configured"""
    return bool(GITLAB_CLIENT_ID and GITLAB_CLIENT_SECRET)


async def gitlab_login() -> RedirectResponse:
    """
    Initiate GitLab OAuth login flow.
    Redirects user to GitLab authorization page.
    """
    if not is_gitlab_configured():
        raise HTTPException(
            status_code=500,
            detail="GitLab OAuth not configured. Set GITLAB_CLIENT_ID and GITLAB_CLIENT_SECRET."
        )
    
    params = {
        "client_id": GITLAB_CLIENT_ID,
        "redirect_uri": GITLAB_REDIRECT_URI,
        "response_type": "code",
        "scope": "read_user",
        "state": "admin_login"  # In production, use a random state token
    }
    
    auth_url = f"{GITLAB_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


async def gitlab_callback(request: Request) -> RedirectResponse:
    """
    Handle GitLab OAuth callback.
    Exchanges authorization code for access token and authenticates user.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    error_description = request.query_params.get("error_description")
    
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"GitLab OAuth error: {error_description or error}"
        )
    
    if not code:
        raise HTTPException(
            status_code=400,
            detail="No authorization code received from GitLab"
        )
    
    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            GITLAB_TOKEN_URL,
            data={
                "client_id": GITLAB_CLIENT_ID,
                "client_secret": GITLAB_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": GITLAB_REDIRECT_URI
            }
        )
        
        if token_response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to exchange code for token: {token_response.text}"
            )
        
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            raise HTTPException(
                status_code=400,
                detail="No access token received from GitLab"
            )
        
        # Get user info from GitLab
        user_response = await client.get(
            GITLAB_USER_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if user_response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to get user info from GitLab: {user_response.text}"
            )
        
        gitlab_user = user_response.json()
    
    # Extract user information
    gitlab_id = str(gitlab_user.get("id"))
    email = gitlab_user.get("email")
    username = gitlab_user.get("username")
    name = gitlab_user.get("name")
    
    if not email:
        raise HTTPException(
            status_code=400,
            detail="GitLab account must have a verified email address"
        )
    
    # Check if user is in admin allowlist
    is_admin = is_admin_allowed(email)
    
    if not is_admin:
        # Log the failed admin attempt
        await db.add_log(
            action="gitlab_admin_denied",
            details=f"GitLab user {email} ({username}) attempted admin login but is not in allowlist"
        )
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. Email {email} is not authorized for admin access."
        )
    
    # Find or create user
    user = await db.get_user_by_gitlab_id(gitlab_id)
    
    if not user:
        # Check if user exists by email
        user = await db.get_user_by_email(email)
        
        if user:
            # Link GitLab to existing user and make admin
            user = await db.update_user(user["id"], gitlab_id=gitlab_id, is_admin=True)
        else:
            # Create new admin user
            user = await db.create_user(
                email=email,
                gitlab_id=gitlab_id,
                is_admin=True
            )
    else:
        # Ensure user is marked as admin
        if not user.get("is_admin"):
            user = await db.update_user(user["id"], is_admin=True)
    
    # Log successful admin login
    await db.add_log(
        action="gitlab_admin_login",
        user_id=user["id"],
        ip=request.client.host if request.client else None,
        details=f"Admin login via GitLab: {email} ({username})"
    )
    
    # Create JWT token
    token = create_user_token(user)
    
    # Redirect to admin dashboard with token in cookie
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        max_age=3600,  # 1 hour
        samesite="lax",
        secure=os.getenv("SECURE_COOKIES", "false").lower() == "true"
    )
    
    return response


async def gitlab_logout(request: Request) -> RedirectResponse:
    """
    Logout user by clearing the session cookie.
    """
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="access_token")
    
    # Log logout if user was authenticated
    token = request.cookies.get("access_token")
    if token:
        await db.add_log(
            action="logout",
            ip=request.client.host if request.client else None,
            details="User logged out"
        )
    
    return response
