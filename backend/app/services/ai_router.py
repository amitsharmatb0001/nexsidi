# =============================================================================
# AI ROUTER V2 - PRODUCTION READY
# Location: backend/app/services/ai_router.py
# Purpose: Unified AI interface with REST API for all providers
# =============================================================================
#
# KEY IMPROVEMENTS FROM V1:
# 1. Vertex AI uses REST API (not SDK) - works with Gemini 3
# 2. Smart token management - no artificial limits
# 3. Automatic model escalation - handles truncation
# 4. Comprehensive error handling - production-grade
# 5. Detailed logging - debugging support
#
# =============================================================================

import os
import time
import asyncio
import logging
import httpx
import json
import hashlib

# Typing & Data structures
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

# Google Auth (The complete set)
import google.auth
import google.auth.transport.requests  # For refreshing tokens
from google.oauth2 import service_account  # For loading key files (from Block 1)

from app.core.config import settings
from app.core.secret_manager import get_secret_manager
from app.core.api_resilience import get_api_resilience

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# DATA STRUCTURES
# =============================================================================

class TaskComplexity(Enum):
    """Task complexity levels for model selection"""
    SIMPLE = "simple"           # Simple files, chat responses
    MEDIUM = "medium"           # Standard code generation
    COMPLEX = "complex"         # Complex logic, architecture
    MOST_COMPLEX = "most_complex"  # Requirements analysis, critical decisions


@dataclass
class AIResponse:
    """Standardized response from any AI provider"""
    content: str
    model_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    finish_reason: str  # "stop", "length", "content_filter", "error"
    latency_ms: float
    cost_estimate: float
    provider: str
    was_escalated: bool = False
    escalation_count: int = 0


# =============================================================================
# MODEL CONFIGURATIONS
# =============================================================================

# Claude Models (Anthropic)
CLAUDE_MODELS = {
    "claude-opus-4.6": {
        "id": "claude-opus-4-6",
        "max_output_tokens": 131072,  # 128K tokens (actual limit)
        "cost_per_1k_input": 0.015,
        "cost_per_1k_output": 0.075,
    },
    "claude-sonnet-4.5": {
        "id": "claude-sonnet-4-5-20250929",
        "max_output_tokens": 8192,
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.015,
    },
    "claude-haiku-4.5": {
        "id": "claude-haiku-4-5-20251001",
        "max_output_tokens": 8192,
        "cost_per_1k_input": 0.0008,
        "cost_per_1k_output": 0.004,
    },
}

# Gemini Models (Vertex AI - uses REST API)
GEMINI_VERTEX_MODELS = {
    "gemini-3-pro": {
        "id": "gemini-3-pro-preview",
        "max_output_tokens": 8192,
        "cost_per_1k_input": 0.00125,
        "cost_per_1k_output": 0.005,
        "location": "global",  # Gemini 3 uses global location
    },
    "gemini-3-flash": {
        "id": "gemini-3-flash-preview",
        "max_output_tokens": 8192,
        "cost_per_1k_input": 0.000075,
        "cost_per_1k_output": 0.0003,
        "location": "global",
    },
    "gemini-2.5-pro": {
        "id": "gemini-2.5-pro",
        "max_output_tokens": 8192,
        "cost_per_1k_input": 0.00125,
        "cost_per_1k_output": 0.005,
        "location": "us-central1",
    },
    "gemini-2.5-flash": {
        "id": "gemini-2.5-flash",
        "max_output_tokens": 8192,
        "cost_per_1k_input": 0.000075,
        "cost_per_1k_output": 0.0003,
        "location": "us-central1",
    },
}

# =============================================================================
# MODEL SELECTION LOGIC
# =============================================================================

# Task-to-model mapping based on complexity and agent
TASK_MODEL_MAPPING = {
    # CHAT (Tilotma) - Fast and cheap
    "chat": {
        TaskComplexity.SIMPLE: "gemini-2.5-flash",
        TaskComplexity.MEDIUM: "gemini-2.5-flash",
        TaskComplexity.COMPLEX: "gemini-2.5-flash",
    },
    
    # ARCHITECTURE (Saanvi) - Highest quality for critical decisions
    "architecture": {
        TaskComplexity.SIMPLE: "claude-sonnet-4.5",
        TaskComplexity.MEDIUM: "claude-opus-4.6",
        TaskComplexity.COMPLEX: "claude-opus-4.6",
        TaskComplexity.MOST_COMPLEX: "claude-opus-4.6",
    },
    
    # CODE GENERATION (Shubham, Aanya) - Balance quality and cost
    "code_generation": {
        TaskComplexity.SIMPLE: "gemini-3-flash",
        TaskComplexity.MEDIUM: "gemini-3-pro",
        TaskComplexity.COMPLEX: "gemini-3-pro",
    },
    
    # CODE REVIEW (Navya) - High quality, no compromise
    "code_review": {
        TaskComplexity.SIMPLE: "claude-sonnet-4.5",
        TaskComplexity.MEDIUM: "claude-sonnet-4.5",
        TaskComplexity.COMPLEX: "claude-sonnet-4.5",
    },
    
    # ADVERSARIAL LOGIC ERROR DETECTION (Navya) - Always Claude Sonnet 4.5
    "adversarial_logic": {
        TaskComplexity.SIMPLE: "claude-sonnet-4.5",
        TaskComplexity.MEDIUM: "claude-sonnet-4.5",
        TaskComplexity.COMPLEX: "claude-sonnet-4.5",
        TaskComplexity.MOST_COMPLEX: "claude-sonnet-4.5",
    },

    # ADVERSARIAL SECURITY DETECTION (Karan) - Always Claude Sonnet 4.5
    "adversarial_security": {
        TaskComplexity.SIMPLE: "claude-sonnet-4.5",
        TaskComplexity.MEDIUM: "claude-sonnet-4.5",
        TaskComplexity.COMPLEX: "claude-sonnet-4.5",
        TaskComplexity.MOST_COMPLEX: "claude-sonnet-4.5",
    },

    # ADVERSARIAL PERFORMANCE DETECTION (Deepika) - Always Claude Sonnet 4.5
    "adversarial_performance": {
        TaskComplexity.SIMPLE: "claude-sonnet-4.5",
        TaskComplexity.MEDIUM: "claude-sonnet-4.5",
        TaskComplexity.COMPLEX: "claude-sonnet-4.5",
        TaskComplexity.MOST_COMPLEX: "claude-sonnet-4.5",
    },

    # BROWSER TESTING (Aarav) - Fast and efficient
    "browser_testing": {
        TaskComplexity.SIMPLE: "gemini-3-flash",
        TaskComplexity.MEDIUM: "gemini-3-flash",
        TaskComplexity.COMPLEX: "gemini-2.5-pro",
    },

    # BRAND/DESIGN EVALUATION (Brand Agent) - Quality for design decisions
    "brand_evaluation": {
        TaskComplexity.SIMPLE: "gemini-3-pro",
        TaskComplexity.MEDIUM: "gemini-3-pro",
        TaskComplexity.COMPLEX: "gemini-3-pro",
    },
    
    # DEPLOYMENT (Pranav) - Simple and fast
    "deployment": {
        TaskComplexity.SIMPLE: "gemini-3-flash",
        TaskComplexity.MEDIUM: "gemini-3-flash",
        TaskComplexity.COMPLEX: "gemini-2.5-pro",
    },
}

# Escalation chain for each model (if truncated, try these in order)
ESCALATION_CHAINS = {
    "gemini-3-flash": ["gemini-3-pro", "claude-sonnet-4.5", "claude-opus-4.6"],
    "gemini-3-pro": ["claude-sonnet-4.5", "claude-opus-4.6"],
    "gemini-2.5-flash": ["gemini-2.5-pro", "claude-sonnet-4.5"],
    "gemini-2.5-pro": ["claude-sonnet-4.5", "claude-opus-4.6"],
    "claude-haiku-4.5": ["claude-sonnet-4.5", "claude-opus-4.6"],
    "claude-sonnet-4.5": ["claude-opus-4.6"],
    "claude-opus-4.6": [],  # Largest model, nowhere to escalate
}

# =============================================================================
# AI ROUTER CLASS
# =============================================================================

class AIRouter:
    """
    Production-ready AI router with automatic escalation and smart token management.
    
    Features:
    - REST API for all providers (no SDK dependencies)
    - Automatic model escalation when hitting token limits
    - Smart token allocation based on task complexity
    - Comprehensive error handling and retries
    - Detailed cost tracking and logging
    """
    
    def __init__(self):
        """Initialize AI Router with credentials and HTTP client"""
        
        self.logger = logging.getLogger("ai.router")
        self._http_client: Optional[httpx.AsyncClient] = None
        
        # Initialize resilience layer
        self.resilience = get_api_resilience()
        
        # Load credentials from environment/settings first
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY") or settings.anthropic_api_key
        self.gcp_project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or settings.gcp_project_id
        self.gcp_credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        
        # If cloud secrets enabled, override with latest from GCP
        if settings.use_cloud_secrets:
            self.logger.info("🔐 Loading AI secrets from Secret Manager...")
            secret_manager = get_secret_manager()
            
            # Load API Keys
            anthropic_key = secret_manager.get_secret("ANTHROPIC_API_KEY")
            if anthropic_key:
                self.anthropic_api_key = anthropic_key
                self.logger.info("✅ Anthropic API key loaded from Secret Manager")
            
            google_key = secret_manager.get_secret("GOOGLE_API_KEY")
            if google_key:
                self.google_api_key = google_key
                self.logger.info("✅ Google API key loaded from Secret Manager")
        
        # Check what's available
        self.has_claude = bool(self.anthropic_api_key)
        self.has_vertex = bool(self.gcp_project_id and (self.gcp_credentials_path or settings.use_cloud_secrets))
        
        # Initialize GCP credentials for Vertex AI (REST API)
        self.gcp_token = None
        self.gcp_token_expiry = 0
        if self.has_vertex:
            self._refresh_gcp_token()
        
        # Log available providers
        providers = []
        if self.has_claude:
            providers.append("Claude")
        if self.has_vertex:
            providers.append("Vertex AI")
        
        self.logger.info(f"✅ AI Router initialized: {', '.join(providers) or 'NO PROVIDERS!'}")
        
        if not providers:
            self.logger.error("❌ No AI providers configured! Check Secret Manager or .env")
        
        # Request deduplication cache (in-memory)
        self._request_cache = {}
        self._cache_ttl = 60  # Cache for 60 seconds
    
    def _refresh_gcp_token(self):
        """Refresh GCP access token for Vertex AI REST API"""
        try:
            # Method 1: Use Secret Manager if enabled
            if settings.use_cloud_secrets:
                secret_manager = get_secret_manager()
                credentials = secret_manager.get_gcp_credentials(settings.gcp_ai_key_secret)
                if credentials:
                    self.logger.info(f"✅ Loaded AI credentials from Secret Manager: {settings.gcp_ai_key_secret}")
                else:
                    self.logger.warning("⚠️ Failed to load AI credentials from Secret Manager, falling back...")
            
            # Method 2: Fallback to local file
            if not credentials and self.gcp_credentials_path:
                credentials = service_account.Credentials.from_service_account_file(
                    self.gcp_credentials_path,
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
                self.logger.info("✅ Loaded AI credentials from local file")
            
            # Refresh token
            auth_req = google.auth.transport.requests.Request()
            credentials.refresh(auth_req)
            
            self.gcp_token = credentials.token
            self.gcp_token_expiry = time.time() + 3600  # Token valid for 1 hour
            
            self.logger.info("✅ GCP token refreshed")
        except Exception as e:
            self.logger.error(f"❌ Failed to refresh GCP token: {e}")
            self.has_vertex = False
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with retries"""
        if self._http_client is None or self._http_client.is_closed:
            transport = httpx.AsyncHTTPTransport(retries=3)
            self._http_client = httpx.AsyncClient(
                timeout=120.0,
                transport=transport,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
            )
        return self._http_client
    
    async def close(self):
        """Close HTTP client"""
        if self._http_client:
            await self._http_client.aclose()
    
    def get_model_for_task(
        self, 
        task_type: str, 
        complexity: TaskComplexity
    ) -> str:
        """
        Select best model for task based on type and complexity.
        
        Args:
            task_type: "chat", "architecture", "code_generation", "code_review", "deployment"
            complexity: TaskComplexity enum (SIMPLE, MEDIUM, COMPLEX, MOST_COMPLEX)
        
        Returns:
            Model name (e.g., "gemini-3-pro", "claude-opus-4.5")
        """
        
        # Get recommended model from mapping
        if task_type in TASK_MODEL_MAPPING:
            model = TASK_MODEL_MAPPING[task_type].get(complexity)
            if model:
                return model
        
        # Default fallback based on complexity
        if complexity == TaskComplexity.MOST_COMPLEX:
            return "claude-opus-4.6"
        elif complexity == TaskComplexity.COMPLEX:
            return "gemini-3-pro"
        elif complexity == TaskComplexity.MEDIUM:
            return "gemini-3-pro"
        else:
            return "gemini-3-flash"

    def get_best_model_for_provider(
        self,
        provider: str,
        task_type: str,
        complexity: TaskComplexity
    ) -> str:
        """Get the highest quality model available for a specific provider."""
        if provider == "anthropic":
            if complexity == TaskComplexity.MOST_COMPLEX:
                return "claude-opus-4.6"
            return "claude-sonnet-4.5"
        elif provider == "google":
            if complexity == TaskComplexity.SIMPLE:
                return "gemini-3-flash"
            return "gemini-3-pro"
        elif provider == "groq":
            return "llama3-70b-8192"  # Example for Groq
        return self.get_model_for_task(task_type, complexity)
    
    def _generate_request_hash(
        self,
        messages: List[Dict],
        system_prompt: Optional[str],
        task_type: str
    ) -> str:
        """
        Generate unique hash for a request.
        
        Prevents duplicate API calls for identical requests within a short time window.
        """
        # Create hash from request parameters
        content = json.dumps({
            "messages": messages,
            "system_prompt": system_prompt,
            "task_type": task_type
        }, sort_keys=True)
        
        return hashlib.md5(content.encode()).hexdigest()
    
    async def generate(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        task_type: str = "code_generation",
        complexity: TaskComplexity = TaskComplexity.MEDIUM,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        auto_escalate: bool = True,
    ) -> AIResponse:
        """
        Generate AI response with automatic failover and resilience.
        """
        # Select primary model
        primary_model = model or self.get_model_for_task(task_type, complexity)
        primary_provider = "anthropic" if primary_model.startswith("claude") else "google"

        async def resilience_callback(provider_name: str) -> AIResponse:
            # If we are failing over, select a new model for the new provider
            current_model = model
            if not current_model or (provider_name == "google" and current_model.startswith("claude")) or \
               (provider_name == "anthropic" and current_model.startswith("gemini")):
                current_model = self.get_best_model_for_provider(provider_name, task_type, complexity)
            
            self.logger.info(f"🤖 Generation attempt: Provider={provider_name}, Model={current_model}")
            
            return await self._call_model(
                model=current_model,
                messages=messages,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature
            )

        # Call with fallback resilience
        try:
            response = await self.resilience.call_with_fallback(
                resilience_callback,
                provider=primary_provider
            )

            # Handle automatic escalation if truncated (internal retry within healthy provider)
            if response.finish_reason == "length" and auto_escalate:
                self.logger.warning(f"⚠️ Response truncated, escalating model...")
                return await self._escalate_generation(
                    model=response.model_id,
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    original_response=response
                )

            return response

        except Exception as e:
            self.logger.error(f"❌ AI Generation failed after all failovers: {e}")
            raise
    
    async def _escalate_generation(
        self,
        model: str,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
        temperature: float,
        original_response: AIResponse
    ) -> AIResponse:
        """
        Escalate to larger model when truncated.
        
        Tries models in escalation chain until one completes successfully.
        """
        
        escalation_chain = ESCALATION_CHAINS.get(model, [])
        
        if not escalation_chain:
            self.logger.error(f"❌ No escalation path for {model}")
            # Return truncated response rather than fail
            original_response.was_escalated = False
            return original_response
        
        for i, next_model in enumerate(escalation_chain):
            self.logger.info(f"🔄 Escalating to {next_model} (attempt {i+1}/{len(escalation_chain)})")
            
            try:
                response = await self._call_model(
                    model=next_model,
                    messages=messages,
                    system_prompt=system_prompt,
                    max_tokens=None,  # Use model's full capacity
                    temperature=temperature
                )
                
                # Check if complete
                if response.finish_reason == "stop":
                    response.was_escalated = True
                    response.escalation_count = i + 1
                    self.logger.info(f"✅ Escalation successful with {next_model}")
                    return response
                
                # Still truncated, continue escalation
                self.logger.warning(f"⚠️ {next_model} also truncated, continuing...")
                
            except Exception as e:
                self.logger.error(f"❌ Escalation to {next_model} failed: {e}")
                continue
        
        # All escalations failed
        self.logger.error("❌ All escalation attempts failed")
        raise Exception("Code too large for all available models - need to split file")
    
    async def _call_model(
        self,
        model: str,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
        max_tokens: Optional[int],
        temperature: float
    ) -> AIResponse:
        """
        Call specific model with automatic retry on rate limits.
        
        Determines if it's Claude or Vertex AI based on model name,
        then calls the appropriate REST API with exponential backoff
        for rate limit errors (429).
        """
        
        max_retries = 3
        base_delay = 10  # Start with 10 seconds
        
        for attempt in range(max_retries):
            try:
                # Determine provider and call appropriate API
                if model.startswith("claude-"):
                    if not self.has_claude:
                        raise Exception("Claude API not configured")
                    return await self._call_claude(
                        model=model,
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        temperature=temperature
                    )
                
                elif model.startswith("gemini-"):
                    if not self.has_vertex:
                        raise Exception("Vertex AI not configured")
                    return await self._call_vertex(
                        model=model,
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        temperature=temperature
                    )
                
                else:
                    raise Exception(f"Unknown model: {model}")
                    
            except Exception as e:
                error_msg = str(e)
                
                # Check if it's a rate limit error
                if "429" in error_msg or "rate_limit" in error_msg.lower():
                    if attempt < max_retries - 1:
                        # Exponential backoff: 10s, 20s, 40s
                        delay = base_delay * (2 ** attempt)
                        self.logger.warning(
                            f"⏱️  Rate limit hit. Retrying in {delay}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger.error("❌ Max retries exceeded for rate limit")
                        raise
                else:
                    # Not a rate limit error, raise immediately
                    raise
    
    async def _call_claude(
        self,
        model: str,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
        max_tokens: Optional[int],
        temperature: float
    ) -> AIResponse:
        """Call Claude API (Anthropic) via REST"""
        
        # Get model config
        model_config = CLAUDE_MODELS.get(model)
        if not model_config:
            raise Exception(f"Unknown Claude model: {model}")
        
        model_id = model_config["id"]
        
        # Use model's full capacity if max_tokens not specified
        if max_tokens is None:
            max_tokens = model_config["max_output_tokens"]
        
        # Build request
        request_body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        
        if system_prompt:
            request_body["system"] = system_prompt
        
        # Call API
        client = await self._get_client()
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=request_body
        )
        
        if response.status_code != 200:
            raise Exception(f"Claude API error: {response.status_code} - {response.text}")
        
        # Parse response
        data = response.json()
        
        # Extract content
        content = ""
        if data.get("content"):
            for block in data["content"]:
                if block.get("type") == "text":
                    content += block.get("text", "")
        
        # Extract usage
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        
        # Calculate cost
        cost = (
            (input_tokens / 1000) * model_config["cost_per_1k_input"] +
            (output_tokens / 1000) * model_config["cost_per_1k_output"]
        )
        
        return AIResponse(
            content=content,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            finish_reason=data.get("stop_reason", "stop"),
            latency_ms=0.0,  # Set by caller
            cost_estimate=cost,
            provider="claude"
        )
    
    def _convert_content_to_gemini(self, content):
        """Convert content (text or multimodal) to Gemini format"""
        
        # If content is a simple string
        if isinstance(content, str):
            return [{"text": content}]
        
        # If content is a list (multimodal: text + images)
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append({"text": item})
                elif isinstance(item, dict) and item.get("type") == "image":
                    # Base64 image
                    parts.append({
                        "inline_data": {
                            "mime_type": item["source"]["media_type"],
                            "data": item["source"]["data"]
                        }
                    })
            return parts
        
        return [{"text": str(content)}]
    
    async def _call_vertex(
        self,
        model: str,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
        max_tokens: Optional[int],
        temperature: float
    ) -> AIResponse:
        """
        Call Vertex AI (Gemini) via REST API.
        
        CRITICAL: Uses REST API instead of SDK to support Gemini 3.
        This is the method that works (based on your test script).
        """
        
        # Refresh token if expired
        if time.time() >= self.gcp_token_expiry:
            self._refresh_gcp_token()
        
        # Get model config
        model_config = GEMINI_VERTEX_MODELS.get(model)
        if not model_config:
            raise Exception(f"Unknown Vertex model: {model}")
        
        model_id = model_config["id"]
        location = model_config["location"]
        
        # Use model's full capacity if max_tokens not specified
        if max_tokens is None:
            max_tokens = model_config["max_output_tokens"]
        
        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            
            # Handle both string and multimodal content
            parts = self._convert_content_to_gemini(msg["content"])
            
            contents.append({
                "role": role,
                "parts": parts
            })
        
        # Build request body
        request_body = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            }
        }
        
        # Add system instruction if provided
        if system_prompt:
            request_body["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }
        
        # Build URL (non-streaming, matches your working test script)
        url = (
            f"https://aiplatform.googleapis.com/v1/"
            f"projects/{self.gcp_project_id}/"
            f"locations/{location}/"
            f"publishers/google/"
            f"models/{model_id}:generateContent"  # Non-streaming endpoint
        )
        
        # Call API
        client = await self._get_client()
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {self.gcp_token}",
                "Content-Type": "application/json"
            },
            json=request_body
        )
        
        if response.status_code != 200:
            raise Exception(f"Vertex AI error: {response.status_code} - {response.text}")
        
        # Parse complete JSON response (non-streaming)
        data = response.json()
        
        full_text = ""
        finish_reason = "stop"
        
        # Extract text from candidates
        if "candidates" in data and data["candidates"]:
            candidate = data["candidates"][0]
            
            # Get text content
            if "content" in candidate:
                parts = candidate["content"].get("parts", [])
                for part in parts:
                    if "text" in part:
                        full_text += part["text"]
            
            # Get finish reason
            if "finishReason" in candidate:
                reason = candidate["finishReason"]
                # Map Gemini finish reasons to standard format
                if reason == "MAX_TOKENS":
                    finish_reason = "length"
                elif reason in ["STOP", "FINISH_REASON_UNSPECIFIED"]:
                    finish_reason = "stop"
                elif reason in ["SAFETY", "RECITATION", "OTHER"]:
                    finish_reason = "content_filter"
                else:
                    finish_reason = "stop"
        
        # Extract token counts
        total_input_tokens = 0
        total_output_tokens = 0
        if "usageMetadata" in data:
            usage = data["usageMetadata"]
            total_input_tokens = usage.get("promptTokenCount", 0)
            total_output_tokens = usage.get("candidatesTokenCount", 0)
        
        # Calculate cost
        cost = (
            (total_input_tokens / 1000) * model_config["cost_per_1k_input"] +
            (total_output_tokens / 1000) * model_config["cost_per_1k_output"]
        )
        
        return AIResponse(
            content=full_text,
            model_id=model_id,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            total_tokens=total_input_tokens + total_output_tokens,
            finish_reason=finish_reason,
            latency_ms=0.0,  # Set by caller
            cost_estimate=cost,
            provider="vertex_ai"
        )


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

# Create singleton instance
ai_router = AIRouter()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

async def quick_generate(
    prompt: str,
    task_type: str = "chat",
    complexity: TaskComplexity = TaskComplexity.SIMPLE,
    system_prompt: Optional[str] = None
) -> str:
    """
    Quick helper for simple generations.
    
    Example:
        response = await quick_generate(
            "Explain FastAPI in 2 sentences",
            task_type="chat"
        )
    """
    response = await ai_router.generate(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system_prompt,
        task_type=task_type,
        complexity=complexity
    )
    return response.content


# =============================================================================
# END OF AI ROUTER V2
# =============================================================================
