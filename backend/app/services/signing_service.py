"""
SIGNING SERVICE - Digital Signatures for Agent Outputs
=======================================================
Location: app/services/signing_service.py

Purpose: Cryptographic signatures for agent outputs (patent feature)
- RSA key generation per agent
- Sign all agent outputs
- Verify signatures on use
- Non-repudiation

Patent Feature: Ensures agent outputs are authentic and unmodified
"""

import os
import json
import hashlib
import base64
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
from cryptography.fernet import Fernet
from pathlib import Path


class SigningService:
    """
    Manages digital signatures for agent outputs.
    
    Each agent gets unique RSA keypair:
    - Private key: Signs agent's outputs
    - Public key: Verifies signatures
    
    Features:
    - 2048-bit RSA keys
    - SHA-256 hashing
    - PKCS1v15 padding
    - Encrypted key storage (Fernet)
    - Key persistence in secure location
    - Automatic key rotation (optional)
    """
    
    def __init__(self, keys_directory: str = None):
        """
        Initialize signing service with secure key storage.
        
        Args:
            keys_directory: Where to store agent keypairs (defaults to ~/.nexsidi/keys)
        """
        self.logger = logging.getLogger("signing_service")
        
        # Use secure default location instead of /tmp
        if keys_directory is None:
            keys_directory = os.path.expanduser("~/.nexsidi/keys")
        
        self.keys_dir = Path(keys_directory)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        
        # Encryption key for keys at rest
        self.encryption_key = self._get_or_create_encryption_key()
        self.cipher = Fernet(self.encryption_key)
        
        # Cache loaded keys in memory
        self._key_cache: Dict[str, Dict[str, Any]] = {}
        
        self.logger.info(f"🔐 SigningService initialized: {keys_directory}")
    
    def get_or_create_keypair(self, agent_name: str) -> Dict[str, Any]:
        """
        Get existing keypair or generate new one for agent.
        
        Args:
            agent_name: Name of the agent (e.g., "shubham", "navya")
        
        Returns:
            Dict with private_key and public_key objects
        """
        # Check cache first
        if agent_name in self._key_cache:
            return self._key_cache[agent_name]
        
        private_key_path = self.keys_dir / f"{agent_name}_private.pem"
        public_key_path = self.keys_dir / f"{agent_name}_public.pem"
        
        # Load existing keys if available
        if private_key_path.exists() and public_key_path.exists():
            try:
                private_key = self._load_private_key(private_key_path)
                public_key = self._load_public_key(public_key_path)
                
                keypair = {
                    "private_key": private_key,
                    "public_key": public_key,
                    "agent_name": agent_name,
                    "created_at": os.path.getctime(private_key_path)
                }
                
                self._key_cache[agent_name] = keypair
                self.logger.info(f"🔑 Loaded existing keys for {agent_name}")
                return keypair
                
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to load keys for {agent_name}: {e}")
                # Fall through to generate new keys
        
        # Generate new keypair
        self.logger.info(f"🔨 Generating new keypair for {agent_name}...")
        
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        
        public_key = private_key.public_key()
        
        # Save to disk
        self._save_private_key(private_key, private_key_path)
        self._save_public_key(public_key, public_key_path)
        
        keypair = {
            "private_key": private_key,
            "public_key": public_key,
            "agent_name": agent_name,
            "created_at": datetime.now().timestamp()
        }
        
        self._key_cache[agent_name] = keypair
        self.logger.info(f"✅ Generated and saved new keys for {agent_name}")
        
        return keypair
    
    def sign_output(
        self,
        agent_name: str,
        output_data: Any,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Sign agent output with digital signature.
        
        Args:
            agent_name: Name of the agent
            output_data: Agent's output (any JSON-serializable data)
            metadata: Optional metadata (timestamp, version, etc.)
        
        Returns:
            Dict with:
            - data: Original output
            - signature: Base64-encoded RSA signature
            - metadata: Signing metadata
            - hash: SHA-256 hash of data
        """
        try:
            # Get agent's private key
            keypair = self.get_or_create_keypair(agent_name)
            private_key = keypair["private_key"]
            
            # Serialize output to canonical JSON
            data_str = json.dumps(output_data, sort_keys=True, separators=(',', ':'))
            data_bytes = data_str.encode('utf-8')
            
            # Compute SHA-256 hash
            data_hash = hashlib.sha256(data_bytes).hexdigest()
            
            # Sign the hash
            signature_bytes = private_key.sign(
                data_bytes,
                padding.PKCS1v15(),
                hashes.SHA256()
            )
            
            # Base64 encode signature for storage
            signature_b64 = base64.b64encode(signature_bytes).decode('utf-8')
            
            # Prepare metadata
            signing_metadata = {
                "agent_name": agent_name,
                "signed_at": datetime.now().isoformat(),
                "algorithm": "RSA-2048-SHA256",
                "version": "1.0"
            }
            
            if metadata:
                signing_metadata.update(metadata)
            
            signed_output = {
                "data": output_data,
                "signature": signature_b64,
                "hash": data_hash,
                "metadata": signing_metadata
            }
            
            self.logger.debug(f"✍️ Signed output for {agent_name}")
            
            return signed_output
            
        except Exception as e:
            self.logger.error(f"❌ Failed to sign output for {agent_name}: {e}")
            raise
    
    def verify_signature(
        self,
        signed_output: Dict[str, Any],
        agent_name: Optional[str] = None
    ) -> bool:
        """
        Verify digital signature on agent output.
        
        Args:
            signed_output: Signed output from sign_output()
            agent_name: Optional agent name (extracted from metadata if not provided)
        
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            # Extract agent name
            if not agent_name:
                agent_name = signed_output.get("metadata", {}).get("agent_name")
            
            if not agent_name:
                self.logger.error("❌ Agent name not provided and not in metadata")
                return False
            
            # Get agent's public key
            keypair = self.get_or_create_keypair(agent_name)
            public_key = keypair["public_key"]
            
            # Extract components
            data = signed_output["data"]
            signature_b64 = signed_output["signature"]
            
            # Decode signature
            signature_bytes = base64.b64decode(signature_b64)
            
            # Recreate canonical JSON
            data_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
            data_bytes = data_str.encode('utf-8')
            
            # Verify signature
            try:
                public_key.verify(
                    signature_bytes,
                    data_bytes,
                    padding.PKCS1v15(),
                    hashes.SHA256()
                )
                
                self.logger.debug(f"✅ Signature verified for {agent_name}")
                return True
                
            except Exception:
                self.logger.warning(f"❌ Invalid signature for {agent_name}")
                return False
            
        except Exception as e:
            self.logger.error(f"❌ Verification failed: {e}")
            return False
    
    def verify_hash(self, signed_output: Dict[str, Any]) -> bool:
        """
        Verify SHA-256 hash matches data.
        
        Quick integrity check without signature verification.
        """
        try:
            data = signed_output["data"]
            stored_hash = signed_output["hash"]
            
            # Recompute hash
            data_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
            computed_hash = hashlib.sha256(data_str.encode('utf-8')).hexdigest()
            
            return computed_hash == stored_hash
            
        except Exception as e:
            self.logger.error(f"❌ Hash verification failed: {e}")
            return False
    
    def _get_or_create_encryption_key(self) -> bytes:
        """
        Get or create encryption key for keys at rest.
        
        In production, this should be stored in a secure key management service.
        For now, we store it in a protected file.
        """
        key_file = self.keys_dir / ".encryption_key"
        
        if key_file.exists():
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            # Generate new encryption key
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            # Restrict permissions
            os.chmod(key_file, 0o600)
            self.logger.info("🔑 Generated new encryption key for key storage")
            return key
    
    def _save_private_key(self, private_key, path: Path):
        """Save private key to encrypted PEM file"""
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        # Encrypt the PEM before storing
        encrypted_pem = self.cipher.encrypt(pem)
        
        path.write_bytes(encrypted_pem)
        # Restrict permissions (owner read/write only)
        os.chmod(path, 0o600)
        self.logger.debug(f"🔐 Saved encrypted private key to {path}")
    
    def _save_public_key(self, public_key, path: Path):
        """Save public key to PEM file"""
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        path.write_bytes(pem)
    
    def _load_private_key(self, path: Path):
        """Load private key from encrypted PEM file"""
        encrypted_pem = path.read_bytes()
        
        # Decrypt the PEM
        try:
            pem = self.cipher.decrypt(encrypted_pem)
        except Exception as e:
            # Fallback for unencrypted keys (backward compatibility)
            self.logger.warning(f"⚠️ Failed to decrypt key, trying unencrypted: {e}")
            pem = encrypted_pem
        
        return serialization.load_pem_private_key(
            pem,
            password=None,
            backend=default_backend()
        )
    
    def _load_public_key(self, path: Path):
        """Load public key from PEM file"""
        pem = path.read_bytes()
        return serialization.load_pem_public_key(
            pem,
            backend=default_backend()
        )
    
    def export_public_key_pem(self, agent_name: str) -> str:
        """
        Export agent's public key as PEM string.
        
        Useful for sharing with external verification systems.
        """
        keypair = self.get_or_create_keypair(agent_name)
        public_key = keypair["public_key"]
        
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        return pem.decode('utf-8')
    
    def get_agent_fingerprint(self, agent_name: str) -> str:
        """
        Get fingerprint (hash) of agent's public key.
        
        Returns SHA-256 hex digest of public key.
        """
        pem = self.export_public_key_pem(agent_name)
        return hashlib.sha256(pem.encode('utf-8')).hexdigest()


# Global instance
signing_service = SigningService()


# ==============================================================================
# USAGE EXAMPLES
# ==============================================================================

"""
# In agent code (e.g., shubham.py):

from app.services.signing_service import signing_service

class Shubham:
    async def execute(self, input_data):
        # Generate code
        code = await self._generate_code(input_data)
        
        # Sign output
        signed_output = signing_service.sign_output(
            agent_name="shubham",
            output_data=code,
            metadata={
                "project_id": self.project_id,
                "version": "1.0"
            }
        )
        
        return signed_output


# In Arjun (verification):

from app.services.signing_service import signing_service

class Arjun:
    async def _delegate_to_agent(self, agent_name, input_data):
        # Get agent output
        output = await agent.execute(input_data)
        
        # Verify signature
        if not signing_service.verify_signature(output, agent_name):
            self.logger.error(f"❌ Invalid signature from {agent_name}")
            raise SecurityError("Agent output signature invalid")
        
        # Verify hash
        if not signing_service.verify_hash(output):
            self.logger.error(f"❌ Hash mismatch for {agent_name}")
            raise SecurityError("Agent output tampered")
        
        return output["data"]  # Extract original data


# For public verification API:

@app.get("/api/verify/{project_id}")
async def verify_project(project_id: str):
    # Get all agent outputs for project
    outputs = get_project_outputs(project_id)
    
    verification_results = {}
    
    for agent_name, output in outputs.items():
        verification_results[agent_name] = {
            "signature_valid": signing_service.verify_signature(output),
            "hash_valid": signing_service.verify_hash(output),
            "public_key_fingerprint": signing_service.get_agent_fingerprint(agent_name)
        }
    
    return verification_results
"""


if __name__ == "__main__":
    # Test signing service
    print("🔐 Testing SigningService...")
    
    # Create test data
    test_data = {
        "code": "def hello(): print('world')",
        "files": ["main.py", "utils.py"],
        "timestamp": datetime.now().isoformat()
    }
    
    # Sign output
    signed = signing_service.sign_output(
        agent_name="test_agent",
        output_data=test_data
    )
    
    print("\n✍️ Signed output:")
    print(f"- Signature: {signed['signature'][:50]}...")
    print(f"- Hash: {signed['hash']}")
    print(f"- Agent: {signed['metadata']['agent_name']}")
    
    # Verify signature
    is_valid = signing_service.verify_signature(signed)
    print(f"\n✅ Signature valid: {is_valid}")
    
    # Verify hash
    hash_valid = signing_service.verify_hash(signed)
    print(f"✅ Hash valid: {hash_valid}")
    
    # Test tampering detection
    print("\n🔍 Testing tampering detection...")
    tampered = signed.copy()
    tampered["data"]["code"] = "MALICIOUS CODE"
    
    tamper_check = signing_service.verify_signature(tampered)
    print(f"❌ Tampered signature valid: {tamper_check}")
    
    hash_tamper = signing_service.verify_hash(tampered)
    print(f"❌ Tampered hash valid: {hash_tamper}")
    
    print("\n✅ SigningService test complete!")
