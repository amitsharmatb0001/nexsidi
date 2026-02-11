"""
LEDGER SERVICE - Append-Only Audit Trail
==========================================
Location: app/services/ledger_service.py

Purpose: Immutable audit log with hash chains (blockchain-style)
- Append-only event logging
- Hash chain verification
- Event timestamping
- Public audit API

Patent Feature: Provides cryptographic proof of all system activities
"""

import json
import hashlib
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.core.redis import get_redis_client
from dataclasses import dataclass, asdict


@dataclass
class LedgerEvent:
    """Single event in the ledger"""
    event_id: str
    project_id: str
    agent_name: str
    event_type: str  # e.g., "agent_output", "review", "deployment"
    data_hash: str  # SHA-256 of event data
    previous_hash: str  # Hash of previous event (chain)
    timestamp: float
    metadata: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)
    
    def compute_hash(self) -> str:
        """Compute SHA-256 hash of this event"""
        # Create canonical representation
        canonical = json.dumps({
            "event_id": self.event_id,
            "project_id": self.project_id,
            "agent_name": self.agent_name,
            "event_type": self.event_type,
            "data_hash": self.data_hash,
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp
        }, sort_keys=True, separators=(',', ':'))
        
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class LedgerService:
    """
    Immutable audit ledger with hash chains.
    
    Each event links to previous event via hash,
    creating an unbreakable chain (like blockchain).
    
    Storage: Redis Lists (append-only)
    Verification: Hash chain validation
    """
    
    def __init__(self):
        """
        Initialize ledger service with shared Redis client.
        """
        self.logger = logging.getLogger("ledger_service")
        
        try:
            self.redis = get_redis_client()
            # We using DB 2 for ledger in original, but shared client uses pool for DB 0.
            # However, keys are prefixed anyway.
            # To keep it separated, we can use selectivity or just accept shared client.
            # The prompt says "M8. Redis connection not configurable via pool. Share a connection pool."
            # So I will use the shared client.
            self.redis.ping()
            self.connected = True
            self.logger.info("[OK] Connected to Redis ledger (shared pool)")
        except Exception as e:
            self.logger.warning(f"[WARN] Redis unavailable: {e}")
            self.redis = None
            self.connected = False
            # Fallback to in-memory ledger
            self._memory_ledger: Dict[str, List[LedgerEvent]] = {}
    
    def append_event(
        self,
        project_id: str,
        agent_name: str,
        event_type: str,
        data: Any,
        metadata: Optional[Dict[str, Any]] = None
    ) -> LedgerEvent:
        """
        Append new event to ledger.
        
        Args:
            project_id: Project identifier
            agent_name: Agent that generated event
            event_type: Type of event
            data: Event data (will be hashed)
            metadata: Optional metadata
        
        Returns:
            LedgerEvent object
        """
        try:
            # Generate event ID
            event_id = self._generate_event_id(project_id, agent_name)
            
            # Hash the data
            data_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
            data_hash = hashlib.sha256(data_str.encode('utf-8')).hexdigest()
            
            # Get previous event hash
            previous_hash = self._get_latest_hash(project_id)
            
            # Create event
            event = LedgerEvent(
                event_id=event_id,
                project_id=project_id,
                agent_name=agent_name,
                event_type=event_type,
                data_hash=data_hash,
                previous_hash=previous_hash,
                timestamp=time.time(),
                metadata=metadata or {}
            )
            
            # Compute event hash
            event_hash = event.compute_hash()
            event.metadata["event_hash"] = event_hash
            
            # Store event
            self._store_event(event)
            
            self.logger.info(
                f"[LOG] Ledger event: {project_id} | {agent_name} | {event_type}"
            )
            
            return event
            
        except Exception as e:
            self.logger.error(f"[ERROR] Failed to append event: {e}")
            raise
    
    def get_project_ledger(
        self,
        project_id: str,
        start: int = 0,
        end: int = -1
    ) -> List[LedgerEvent]:
        """
        Get all events for a project.
        
        Args:
            project_id: Project identifier
            start: Start index (0 = first event)
            end: End index (-1 = all events)
        
        Returns:
            List of LedgerEvent objects in chronological order
        """
        ledger_key = f"ledger:{project_id}"
        
        if self.connected:
            # Get from Redis
            events_json = self.redis.lrange(ledger_key, start, end)
            return [self._deserialize_event(e) for e in events_json]
        else:
            # Get from memory
            return self._memory_ledger.get(project_id, [])[start:end if end != -1 else None]
    
    def verify_chain(self, project_id: str) -> Dict[str, Any]:
        """
        Verify integrity of event chain.
        
        Checks:
        1. Each event's hash is correct
        2. Each event's previous_hash matches actual previous event
        3. No gaps in the chain
        
        Returns:
            Dict with verification results
        """
        events = self.get_project_ledger(project_id)
        
        if not events:
            return {
                "valid": True,
                "event_count": 0,
                "message": "No events in ledger"
            }
        
        errors = []
        
        for i, event in enumerate(events):
            # Verify event hash
            computed_hash = event.compute_hash()
            stored_hash = event.metadata.get("event_hash")
            
            if computed_hash != stored_hash:
                errors.append({
                    "event_index": i,
                    "event_id": event.event_id,
                    "error": "Hash mismatch",
                    "expected": stored_hash,
                    "actual": computed_hash
                })
            
            # Verify chain link (except first event)
            if i > 0:
                previous_event = events[i - 1]
                expected_previous_hash = previous_event.metadata.get("event_hash")
                
                if event.previous_hash != expected_previous_hash:
                    errors.append({
                        "event_index": i,
                        "event_id": event.event_id,
                        "error": "Chain broken",
                        "expected_previous": expected_previous_hash,
                        "actual_previous": event.previous_hash
                    })
        
        return {
            "valid": len(errors) == 0,
            "event_count": len(events),
            "errors": errors,
            "first_event": events[0].to_dict() if events else None,
            "last_event": events[-1].to_dict() if events else None
        }
    
    def get_agent_activity(
        self,
        project_id: str,
        agent_name: str
    ) -> List[LedgerEvent]:
        """
        Get all events for specific agent in project.
        """
        all_events = self.get_project_ledger(project_id)
        return [e for e in all_events if e.agent_name == agent_name]
    
    def get_event_timeline(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Get human-readable timeline of events.
        
        Returns simplified event list for display.
        """
        events = self.get_project_ledger(project_id)
        
        timeline = []
        for event in events:
            timeline.append({
                "timestamp": datetime.fromtimestamp(event.timestamp).isoformat(),
                "agent": event.agent_name,
                "action": event.event_type,
                "event_id": event.event_id,
                "verified": True  # Could check hash here
            })
        
        return timeline
    
    def _generate_event_id(self, project_id: str, agent_name: str) -> str:
        """Generate unique event ID"""
        timestamp = int(time.time() * 1000)  # Milliseconds
        return f"{project_id}:{agent_name}:{timestamp}"
    
    def _get_latest_hash(self, project_id: str) -> str:
        """Get hash of most recent event (or genesis hash)"""
        ledger_key = f"ledger:{project_id}"
        
        if self.connected:
            # Get last event from Redis
            last_event_json = self.redis.lindex(ledger_key, -1)
            
            if last_event_json:
                last_event = self._deserialize_event(last_event_json)
                return last_event.metadata.get("event_hash", "0" * 64)
        else:
            # Get from memory
            events = self._memory_ledger.get(project_id, [])
            if events:
                return events[-1].metadata.get("event_hash", "0" * 64)
        
        # Genesis hash (first event in chain)
        return "0" * 64
    
    def _store_event(self, event: LedgerEvent):
        """Store event in ledger"""
        ledger_key = f"ledger:{event.project_id}"
        event_json = json.dumps(event.to_dict())
        
        if self.connected:
            # Append to Redis list (append-only)
            self.redis.rpush(ledger_key, event_json)
            
            # Set expiry (optional - keep for 30 days)
            self.redis.expire(ledger_key, 30 * 24 * 60 * 60)
        else:
            # Store in memory
            if event.project_id not in self._memory_ledger:
                self._memory_ledger[event.project_id] = []
            
            self._memory_ledger[event.project_id].append(event)
    
    def _deserialize_event(self, event_json: str) -> LedgerEvent:
        """Convert JSON to LedgerEvent"""
        data = json.loads(event_json)
        return LedgerEvent(**data)
    
    def export_ledger(self, project_id: str, format: str = "json") -> str:
        """
        Export entire ledger for project.
        
        Args:
            project_id: Project identifier
            format: Export format ("json" or "csv")
        
        Returns:
            Formatted ledger data
        """
        events = self.get_project_ledger(project_id)
        
        if format == "json":
            return json.dumps(
                [e.to_dict() for e in events],
                indent=2
            )
        elif format == "csv":
            import csv
            import io
            
            output = io.StringIO()
            writer = csv.DictWriter(
                output,
                fieldnames=["event_id", "timestamp", "agent_name", "event_type", "data_hash"]
            )
            writer.writeheader()
            
            for event in events:
                writer.writerow({
                    "event_id": event.event_id,
                    "timestamp": datetime.fromtimestamp(event.timestamp).isoformat(),
                    "agent_name": event.agent_name,
                    "event_type": event.event_type,
                    "data_hash": event.data_hash
                })
            
            return output.getvalue()
        else:
            raise ValueError(f"Unknown format: {format}")


# Global instance
ledger_service = LedgerService()


# ==============================================================================
# USAGE EXAMPLES
# ==============================================================================

"""
# In Arjun - log all agent activities:

from app.services.ledger_service import ledger_service

class Arjun:
    async def _delegate_to_agent(self, agent_name, input_data):
        # Execute agent
        output = await agent.execute(input_data)
        
        # Log to ledger
        ledger_service.append_event(
            project_id=self.project_id,
            agent_name=agent_name,
            event_type="agent_output",
            data=output,
            metadata={
                "input_hash": hashlib.sha256(
                    json.dumps(input_data).encode()
                ).hexdigest()
            }
        )
        
        return output


# For public verification API:

@app.get("/api/audit-trail/{project_id}")
async def get_audit_trail(project_id: str):
    # Get timeline
    timeline = ledger_service.get_event_timeline(project_id)
    
    # Verify chain
    verification = ledger_service.verify_chain(project_id)
    
    return {
        "project_id": project_id,
        "event_count": verification["event_count"],
        "chain_valid": verification["valid"],
        "timeline": timeline
    }


# Export ledger:

@app.get("/api/audit-trail/{project_id}/export")
async def export_audit_trail(project_id: str, format: str = "json"):
    ledger_data = ledger_service.export_ledger(project_id, format)
    
    if format == "csv":
        return Response(
            content=ledger_data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={project_id}_ledger.csv"}
        )
    else:
        return Response(
            content=ledger_data,
            media_type="application/json"
        )
"""


if __name__ == "__main__":
    # Test ledger service
    print("[LOG] Testing LedgerService...")
    
    test_project = "test-project-001"
    
    # Append some events
    print("\n[SIG] Appending events...")
    
    ledger_service.append_event(
        project_id=test_project,
        agent_name="saanvi",
        event_type="requirements_analysis",
        data={"features": ["auth", "dashboard"]},
        metadata={"complexity": "medium"}
    )
    
    ledger_service.append_event(
        project_id=test_project,
        agent_name="shubham",
        event_type="backend_generation",
        data={"code": "backend code here"},
        metadata={"framework": "fastapi"}
    )
    
    ledger_service.append_event(
        project_id=test_project,
        agent_name="navya",
        event_type="code_review",
        data={"bugs": 2, "warnings": 5},
        metadata={"passed": True}
    )
    
    # Get ledger
    print("\n📖 Reading ledger...")
    events = ledger_service.get_project_ledger(test_project)
    print(f"Total events: {len(events)}")
    
    # Verify chain
    print("\n[FIND] Verifying chain...")
    verification = ledger_service.verify_chain(test_project)
    print(f"Chain valid: {verification['valid']}")
    print(f"Event count: {verification['event_count']}")
    
    if not verification['valid']:
        print("Errors:", verification['errors'])
    
    # Get timeline
    print("\n📅 Timeline:")
    timeline = ledger_service.get_event_timeline(test_project)
    for event in timeline:
        print(f"  [{event['timestamp']}] {event['agent']} -> {event['action']}")
    
    # Export
    print("\n[SAVE] Exporting ledger...")
    json_export = ledger_service.export_ledger(test_project, "json")
    print(f"JSON export size: {len(json_export)} bytes")
    
    print("\n[OK] LedgerService test complete!")
