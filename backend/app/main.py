"""
MAIN APPLICATION FILE
=====================
This is the entry point of your entire backend.

Think of this as the "manager" of your restaurant:
- Sets up the building (FastAPI app)
- Hires the staff (routers for auth, projects, chat)
- Opens the doors (CORS, allows frontend to connect)
- Handles general operations (health checks, startup/shutdown)

When you run: uvicorn app.main:app --reload
This file is what actually runs.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth, projects, chat, health, uploads
from app.core.exceptions import (
    validation_exception_handler,
    database_exception_handler,
    generic_exception_handler
)
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from app.core.logging_config import setup_logging
import os
from dotenv import load_dotenv
from app.api import websocket
from app.api.verification import router as verification_router
from contextlib import asynccontextmanager
from app.core.redis import verify_redis_connection
from app.core.config import settings

# Load environment variables from .env file
# This reads DATABASE_URL, JWT_SECRET, API keys, etc.
load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.
    Replaces deprecated @app.on_event.
    """
    # STARTUP
    setup_logging()
    
    # Verify Redis
    verify_redis_connection()
    
    # Start rate limiter cleanup task
    from app.core.rate_limit import rate_limiter
    rate_limiter.start_cleanup()
    
    print("=" * 60)
    print("🚀 NexSidi API Starting Up...")
    print("=" * 60)
    print(f"📊 Database: {os.getenv('DATABASE_URL', 'Not configured')[:50]}...")
    print(f"🔐 JWT Secret: {'Configured' if os.getenv('JWT_SECRET') else 'Using default (NOT SECURE!)'}")
    print(f"🤖 Anthropic API: {'Configured' if os.getenv('ANTHROPIC_API_KEY') else 'Not configured'}")
    print(f"🤖 Google API: {'Configured' if os.getenv('GOOGLE_API_KEY') else 'Not configured'}")
    print("=" * 60)
    print("✅ Server Ready!")
    print("📚 API Documentation: http://localhost:8000/docs")
    print("=" * 60)
    
    yield
    
    # SHUTDOWN
    print("\n" + "=" * 60)
    print("👋 NexSidi API Shutting Down...")
    print("=" * 60)

# Create FastAPI application with lifespan
app = FastAPI(
    title="NexSidi API",
    description="AI-powered software development platform - Build apps through conversation",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Exception Handlers
# ==================
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, database_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# CORS Configuration
# ==================
# CORS = Cross-Origin Resource Sharing
# 
# Non-technical explanation:
# Your frontend (React) runs on http://localhost:3000
# Your backend (FastAPI) runs on http://localhost:8000
# By default, browsers block frontend from talking to backend (security feature)
# CORS settings tell browser "it's okay, they're allowed to talk"
#
# Think of it like building security:
# - Frontend is a person trying to enter
# - Backend is the building
# - CORS is the access list saying "these people are allowed in"
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,  # Allow cookies/authentication
    allow_methods=["*"],  # Allow all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],  # Allow all headers
)

# Include Routers
# ===============
# Routers are like departments in your restaurant:
# - Auth router: Handles signups, logins (reception desk)
# - Projects router: Manages project data (order management)
# - Chat router: Handles conversations with Tilotma (the dining area)

app.include_router(
    auth.router, 
    prefix="/api/auth", 
    tags=["Authentication"]
)
# This creates endpoints:
# - POST /api/auth/signup
# - POST /api/auth/login
# - GET /api/auth/me

app.include_router(
    projects.router, 
    prefix="/api/projects", 
    tags=["Projects"]
)
# This creates endpoints:
# - POST /api/projects (create project)
# - GET /api/projects (list all projects)
# - GET /api/projects/{id} (get one project)
# - DELETE /api/projects/{id} (delete project)

app.include_router(
    chat.router, 
    prefix="/api/chat", 
    tags=["Chat"]
)
# This creates endpoints:
# - POST /api/chat/send (send message to Tilotma)
# - GET /api/chat/history (get conversation history)
# - DELETE /api/chat/history/{id} (delete one message)
# - POST /api/chat/clear (clear all messages)

app.include_router(
    health.router,
    prefix="/api",
    tags=["Health"]
)
# This creates endpoints:
# - GET /api/health/detailed (detailed system health)
# - GET /api/health/system (system resource stats)


# Include WebSocket router
app.include_router(
    websocket.router,
    prefix="/api",
    tags=["WebSocket"]
)

# Include Uploads router
app.include_router(
    uploads.router,
    prefix="/api/uploads",
    tags=["Uploads"]
)
# This creates endpoints:
# - POST /api/uploads (upload file)
# - GET /api/uploads (list user's files)
# - GET /api/uploads/{id} (get file details)
# - DELETE /api/uploads/{id} (delete file)

app.include_router(
    verification_router,
    prefix="/api/verification",
    tags=["Verification"]
)


# Root Endpoint
# =============
# When someone visits http://localhost:8000/ they see this
@app.get("/")
async def root():
    """
    Basic welcome message
    
    This is useful for:
    - Checking if server is running
    - Quick health check
    - Showing API info
    """
    return {
        "message": "Welcome to NexSidi API",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",  # Link to interactive API documentation
        "description": "AI-powered software development platform"
    }


# Health Check Endpoint
# ======================
# Used by monitoring tools to check if server is alive
@app.get("/health")
async def health_check():
    """
    Quick health check
    
    Used by:
    - Load balancers (in production)
    - Monitoring services (UptimeRobot, Pingdom, etc.)
    - Deployment platforms (Railway, Vercel)
    - Your own monitoring scripts
    
    Returns:
    - status: "healthy" or "warning" based on system state
    - queue_size: Number of projects in queue
    - alerts: Any system warnings
    """
    from app.services.monitoring import system_monitor
    
    health = await system_monitor.check_health()
    return {
        "status": health["status"],
        "queue_size": health["queue_size"],
        "alerts": health["alerts"]
    }


# Development Server
# ==================
# When you run: python app/main.py
# This section runs (but normally you use uvicorn command instead)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # Listen on all network interfaces
        port=8000,
        reload=True  # Auto-restart when code changes (DEVELOPMENT ONLY!)
    )
