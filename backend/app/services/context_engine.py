# File: app/services/context_engine.py

import json
import logging
import hashlib
from app.core.redis import get_redis_client
from app.core.config import settings
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List



class ContextEngine:
    """
    Centralized context management for all agents WITH cryptographic verification.
    
    NEW: Hash verification on retrieval (patent requirement).
    Stores project context in Redis with SHA-256 integrity checks.
    """
    
    def __init__(self, redis_url: str = None):
        """
        Initialize Context Engine with shared Redis pool.
        """
        self.logger = logging.getLogger("context_engine")
        
        try:
            self.redis_client = get_redis_client()
            self.redis_client.ping()
            self.logger.info("✅ Connected to Redis for ContextEngine")
            self.connected = True
        except Exception as e:
            self.logger.warning(f"⚠️ Redis unavailable for ContextEngine: {e}")
            self.redis_client = None
            self.connected = False
            self.memory_store = {}
    
    def _compute_hash(self, data: str) -> str:
        """
        Compute SHA-256 hash of data.
        
        Args:
            data: JSON string to hash
        
        Returns:
            Hex digest of SHA-256 hash
        """
        return hashlib.sha256(data.encode('utf-8')).hexdigest()
    
    def store_context(
        self,
        project_id: str,
        context_type: str,
        data: Dict[str, Any],
        computed_hash: str = None
    ):
        """
        Store context for project WITH hash chaining for audit trails.
        
        Args:
            project_id: Project UUID
            context_type: Type of context (requirements, backend_code, etc.)
            data: Context data to store
            computed_hash: Pre-computed hash from Arjun (if available)
        
        How it works (Patent Claims 3b/10):
        1. Retrieve previous context hash (blockchain-style chaining)
        2. Convert data to canonical JSON (sorted keys)
        3. Compute SHA-256 hash including previous hash
        4. Store data with hash and chain metadata
        5. Update chain index for audit trail
        """
        # Step 1: Get previous hash for chaining (Patent Claim 3b)
        previous_hash = self._get_latest_hash(project_id, context_type)
        
        # Canonical JSON (sorted keys for consistent hashing)
        json_data = json.dumps(data, sort_keys=True)
        
        # Step 2: Compute chained hash (current_data + previous_hash)
        if previous_hash:
            chained_data = json_data + previous_hash
            data_hash = self._compute_hash(chained_data)
        else:
            # Genesis block - no previous hash
            data_hash = computed_hash if computed_hash else self._compute_hash(json_data)
        
        # Prepare storage payload with hash chain metadata
        payload = {
            "data": data,
            "hash": data_hash,
            "previous_hash": previous_hash,
            "context_type": context_type,
            "timestamp": datetime.now().isoformat(),
            "chain_index": self._get_next_chain_index(project_id, context_type)
        }
        payload_str = json.dumps(payload, sort_keys=True)
        
        # Storage keys
        data_key = f"context:{project_id}:{context_type}"
        hash_key = f"context_hash:{project_id}:{context_type}"
        chain_key = f"context_chain:{project_id}:{context_type}:{payload['chain_index']}"
        
        if self.connected:
            # Store current context
            self.redis_client.set(data_key, payload_str)
            # Store hash separately for quick verification
            self.redis_client.set(hash_key, data_hash)
            # Store in chain for audit trail (Patent Claim 10)
            self.redis_client.set(chain_key, payload_str)
            # Update chain index
            self.redis_client.set(
                f"chain_index:{project_id}:{context_type}",
                str(payload['chain_index'])
            )
        else:
            self.memory_store[data_key] = payload_str
            self.memory_store[hash_key] = data_hash
            self.memory_store[chain_key] = payload_str
            self.memory_store[f"chain_index:{project_id}:{context_type}"] = str(payload['chain_index'])
            
        self.logger.info(
            f"💾 Stored context: {context_type} for {project_id} "
            f"(hash: {data_hash[:8]}..., chain_index: {payload['chain_index']}, "
            f"previous: {previous_hash[:8] if previous_hash else 'genesis'}...)"
        )
    
    def get_context(
        self,
        project_id: str,
        context_type: Optional[str] = None,
        verify_hash: bool = True
    ) -> Dict[str, Any]:
        """
        Retrieve context for project WITH integrity verification.
        
        Args:
            project_id: Project UUID
            context_type: Specific context type (None = get all)
            verify_hash: Whether to verify hash (default True)
        
        Returns:
            Context data if hash valid, {} if corrupted
        
        This is the KEY DIFFERENCE from old version:
        - OLD: Just returned data without checking
        - NEW: Verifies hash, triggers automatic rollback if corrupted
        """
        if context_type:
            # Get specific context with verification
            return self._get_and_verify(project_id, context_type, verify_hash)
        else:
            # Get all contexts
            return self.get_full_context(project_id, verify_hash)
    
    def _get_and_verify(
        self,
        project_id: str,
        context_type: str,
        verify_hash: bool
    ) -> Dict[str, Any]:
        """
        Internal method: Get single context and verify integrity.
        
        Returns:
            Data if valid, {} if corrupted or missing
        """
        data_key = f"context:{project_id}:{context_type}"
        hash_key = f"context_hash:{project_id}:{context_type}"
        
        # Retrieve data
        if self.connected:
            payload_str = self.redis_client.get(data_key)
            stored_hash = self.redis_client.get(hash_key)
        else:
            payload_str = self.memory_store.get(data_key)
            stored_hash = self.memory_store.get(hash_key)
        
        if not payload_str:
            return {}
        
        # Parse payload
        try:
            payload = json.loads(payload_str)
            data = payload.get("data", {})
            embedded_hash = payload.get("hash")
        except json.JSONDecodeError:
            self.logger.error(f"❌ Corrupted payload for {context_type}")
            return {}
        
        # VERIFY HASH (patent requirement)
        if verify_hash and embedded_hash:
            # Recompute hash from data
            canonical_data = json.dumps(data, sort_keys=True)
            computed_hash = self._compute_hash(canonical_data)
            
            # Check if hash matches
            if computed_hash != embedded_hash:
                self.logger.error(
                    f"❌ HASH MISMATCH for {context_type}! "
                    f"Expected: {embedded_hash[:8]}..., "
                    f"Got: {computed_hash[:8]}..."
                )
                self.logger.error("🚨 DATA CORRUPTION DETECTED - Triggering rollback")
                
                # Trigger automatic rollback (notify Arjun)
                self._trigger_rollback(project_id, context_type, embedded_hash, computed_hash)
                
                return {}
            
            # Also verify against separately stored hash
            if stored_hash and stored_hash != embedded_hash:
                self.logger.error(
                    f"❌ HASH INCONSISTENCY for {context_type}! "
                    f"Embedded: {embedded_hash[:8]}..., "
                    f"Stored: {stored_hash[:8]}..."
                )
                self._trigger_rollback(project_id, context_type, embedded_hash, stored_hash)
                return {}
            
            self.logger.info(
                f"✅ Hash verified for {context_type} "
                f"({embedded_hash[:8]}...)"
            )
        
        return data
    
    def _trigger_rollback(
        self,
        project_id: str,
        context_type: str,
        expected_hash: str,
        actual_hash: str
    ):
        """
        Trigger automatic rollback when corruption detected.
        
        This notifies Arjun to roll back to last good state.
        In production, this could also:
        - Send alert to monitoring system
        - Create incident report
        - Freeze project until manual review
        """
        self.logger.error(
            f"🚨 ROLLBACK TRIGGERED for project {project_id}, context: {context_type}"
        )
        
        # Store rollback event
        rollback_key = f"rollback:{project_id}:{context_type}"
        rollback_data = {
            "expected_hash": expected_hash,
            "actual_hash": actual_hash,
            "timestamp": datetime.now().isoformat()
        }
        
        if self.connected:
            self.redis_client.set(rollback_key, json.dumps(rollback_data))
        else:
            self.memory_store[rollback_key] = json.dumps(rollback_data)
        
        # In production: Send alert to Arjun or monitoring system
        # For now: Just log
        self.logger.error(f"Rollback event stored: {rollback_key}")
    
    def get_full_context(
        self,
        project_id: str,
        verify_hash: bool = True
    ) -> Dict[str, Any]:
        """
        Get complete project context with verification.
        
        Returns:
            Dict of all contexts, skipping any with hash mismatches
        """
        full_context = {}
        pattern = f"context:{project_id}:*"
        
        if self.connected:
            keys = self.redis_client.keys(pattern)
            for key in keys:
                # Skip hash keys
                if key.startswith("context_hash:"):
                    continue
                
                context_type = key.split(":")[-1]
                data = self._get_and_verify(project_id, context_type, verify_hash)
                
                if data:  # Only include if hash verified
                    full_context[context_type] = data
        else:
            for key in list(self.memory_store.keys()):
                if key.startswith(f"context:{project_id}:") and not "context_hash:" in key:
                    context_type = key.split(":")[-1]
                    data = self._get_and_verify(project_id, context_type, verify_hash)
                    
                    if data:
                        full_context[context_type] = data
                    
        return full_context

    def append_to_context(
        self,
        project_id: str,
        context_type: str,
        data: Dict[str, Any]
    ):
        """
        Append or merge new data to existing context.
        Recomputes hash after merge.
        """
        current = self.get_context(project_id, context_type, verify_hash=True)
        
        if isinstance(current, dict):
            current.update(data)
        elif isinstance(current, list):
            if isinstance(data, list):
                current.extend(data)
            else:
                current.append(data)
        else:
            current = data
            
        # Store with new hash
        self.store_context(project_id, context_type, current)
    
    def clear_context(self, project_id: str):
        """Clear project context."""
        pattern = f"context:{project_id}:*"
        hash_pattern = f"context_hash:{project_id}:*"
        
        if self.connected:
            # Clear data keys
            keys = self.redis_client.keys(pattern)
            if keys:
                self.redis_client.delete(*keys)
            
            # Clear hash keys
            hash_keys = self.redis_client.keys(hash_pattern)
            if hash_keys:
                self.redis_client.delete(*hash_keys)
        else:
            # Clear from memory
            keys_to_delete = [
                k for k in self.memory_store.keys()
                if k.startswith(f"context:{project_id}:") or 
                   k.startswith(f"context_hash:{project_id}:") or
                   k.startswith(f"context_chain:{project_id}:") or
                   k.startswith(f"chain_index:{project_id}:")
            ]
            for k in keys_to_delete:
                del self.memory_store[k]
                
        self.logger.info(f"🧹 Cleared context for {project_id}")
    
    def _get_latest_hash(self, project_id: str, context_type: str) -> Optional[str]:
        """
        Get the latest hash for a context type to enable chaining.
        
        Returns:
            Latest hash or None if this is the first context (genesis)
        """
        hash_key = f"context_hash:{project_id}:{context_type}"
        
        if self.connected:
            latest_hash = self.redis_client.get(hash_key)
            return latest_hash if latest_hash else None
        else:
            return self.memory_store.get(hash_key)
    
    def _get_next_chain_index(self, project_id: str, context_type: str) -> int:
        """
        Get the next chain index for sequential ordering.
        
        Returns:
            Next index in the chain (0 for genesis block)
        """
        index_key = f"chain_index:{project_id}:{context_type}"
        
        if self.connected:
            current_index = self.redis_client.get(index_key)
            return int(current_index) + 1 if current_index else 0
        else:
            current_index = self.memory_store.get(index_key)
            return int(current_index) + 1 if current_index else 0
    
    def get_hash_chain(
        self,
        project_id: str,
        context_type: str
    ) -> List[Dict[str, Any]]:
        """
        Retrieve complete hash chain for audit trail (Patent Claim 10).
        
        Args:
            project_id: Project UUID
            context_type: Type of context
        
        Returns:
            List of all contexts in chain order with metadata
        """
        chain = []
        pattern = f"context_chain:{project_id}:{context_type}:*"
        
        if self.connected:
            keys = self.redis_client.keys(pattern)
            # Sort by chain index
            sorted_keys = sorted(keys, key=lambda k: int(k.split(":")[-1]))
            
            for key in sorted_keys:
                payload_str = self.redis_client.get(key)
                if payload_str:
                    payload = json.loads(payload_str)
                    chain.append(payload)
        else:
            for key in sorted(self.memory_store.keys()):
                if key.startswith(f"context_chain:{project_id}:{context_type}:"):
                    payload_str = self.memory_store[key]
                    payload = json.loads(payload_str)
                    chain.append(payload)
        
        return chain
    
    def verify_chain_integrity(
        self,
        project_id: str,
        context_type: str
    ) -> Dict[str, Any]:
        """
        Verify integrity of entire hash chain (Patent Claim 3b).
        
        Args:
            project_id: Project UUID
            context_type: Type of context
        
        Returns:
            Dict with:
            - valid: bool - True if chain is intact
            - chain_length: int - Number of blocks
            - broken_links: List of indices where chain breaks
            - details: List of verification results per block
        """
        chain = self.get_hash_chain(project_id, context_type)
        
        if not chain:
            return {
                "valid": True,
                "chain_length": 0,
                "broken_links": [],
                "details": []
            }
        
        broken_links = []
        details = []
        
        for i, block in enumerate(chain):
            # Verify hash matches data
            data = block["data"]
            stored_hash = block["hash"]
            previous_hash = block.get("previous_hash")
            
            # Recompute hash
            json_data = json.dumps(data, sort_keys=True)
            if previous_hash:
                chained_data = json_data + previous_hash
                computed_hash = self._compute_hash(chained_data)
            else:
                computed_hash = self._compute_hash(json_data)
            
            # Check if hash matches
            hash_valid = (computed_hash == stored_hash)
            
            # Check if previous_hash matches previous block's hash
            chain_valid = True
            if i > 0:
                expected_previous = chain[i - 1]["hash"]
                chain_valid = (previous_hash == expected_previous)
            
            block_valid = hash_valid and chain_valid
            
            if not block_valid:
                broken_links.append(i)
            
            details.append({
                "index": i,
                "hash_valid": hash_valid,
                "chain_valid": chain_valid,
                "block_valid": block_valid,
                "hash": stored_hash[:8] + "...",
                "timestamp": block.get("timestamp")
            })
        
        return {
            "valid": len(broken_links) == 0,
            "chain_length": len(chain),
            "broken_links": broken_links,
            "details": details
        }


# Global instance
context_engine = ContextEngine()