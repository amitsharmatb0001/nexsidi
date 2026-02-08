
import json
import time
import hashlib
from typing import Dict, Any, Optional


class PromptVersioning:
    """
    Version control for all agent prompts.
    Track which prompt versions produce best results.
    """
    
    def __init__(self):
        self._versions = {}  # {agent: {prompt_key: [versions]}}
        self._active = {}    # {agent: {prompt_key: version_id}}
    
    def register_prompt(
        self, agent_name: str, prompt_key: str, 
        prompt_text: str, metadata: dict = None
    ) -> str:
        """Register a new prompt version."""
        version_id = hashlib.sha256(
            f"{agent_name}:{prompt_key}:{prompt_text}".encode()
        ).hexdigest()[:12]
        
        if agent_name not in self._versions:
            self._versions[agent_name] = {}
        if prompt_key not in self._versions[agent_name]:
            self._versions[agent_name][prompt_key] = []
        
        # Check if version already exists
        for v in self._versions[agent_name][prompt_key]:
            if v["version_id"] == version_id:
                return version_id

        self._versions[agent_name][prompt_key].append({
            "version_id": version_id,
            "prompt_text": prompt_text,
            "created_at": time.time(),
            "metadata": metadata or {},
            "usage_count": 0,
            "success_rate": 0.0
        })
        
        # Set as active if first version
        if agent_name not in self._active:
            self._active[agent_name] = {}
        if prompt_key not in self._active[agent_name]:
            self._active[agent_name][prompt_key] = version_id
        
        return version_id
    
    def get_active_prompt(self, agent_name: str, prompt_key: str) -> Optional[str]:
        """Get currently active prompt version."""
        version_id = self._active.get(agent_name, {}).get(prompt_key)
        if not version_id:
            return None
        
        for v in self._versions.get(agent_name, {}).get(prompt_key, []):
            if v["version_id"] == version_id:
                v["usage_count"] += 1
                return v["prompt_text"]
        return None

    def get_prompt_by_version(self, agent_name: str, prompt_key: str, version_id: str) -> Optional[str]:
        """Get a specific prompt version."""
        for v in self._versions.get(agent_name, {}).get(prompt_key, []):
            if v["version_id"] == version_id:
                v["usage_count"] += 1
                return v["prompt_text"]
        return None
    
    def record_outcome(
        self, agent_name: str, prompt_key: str, 
        success: bool
    ):
        """Record whether a prompt usage was successful."""
        version_id = self._active.get(agent_name, {}).get(prompt_key)
        if not version_id:
            return
        
        for v in self._versions.get(agent_name, {}).get(prompt_key, []):
            if v["version_id"] == version_id:
                count = v["usage_count"]
                if count == 0: # Should not happen if get_active_prompt was called
                    v["usage_count"] = 1
                    count = 1
                old_rate = v["success_rate"]
                v["success_rate"] = (old_rate * (count - 1) + (1.0 if success else 0.0)) / count
                break


prompt_versioning = PromptVersioning()
