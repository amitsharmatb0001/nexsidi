
import chromadb
import json
import hashlib
import logging
import time
from typing import Dict, Any, List


class AgentPermanentMemory:
    """
    Per-agent permanent memory that persists across ALL projects.
    Separate from project context and mistake memory.
    
    Collections per agent:
    - {agent}_knowledge: Learned patterns, best practices
    - {agent}_mistakes: Error patterns (existing MistakeMemory)
    - {agent}_preferences: Coding style preferences learned over time
    """
    
    def __init__(self, persist_directory: str = "./chroma_db"):
        self.logger = logging.getLogger("agent_memory")
        try:
            self.client = chromadb.PersistentClient(path=persist_directory)
            self.logger.info(f"✅ AgentPermanentMemory initialized at {persist_directory}")
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize ChromaDB: {e}")
            self.client = chromadb.Client()
        self._collections = {}
    
    def _get_collection(self, agent_name: str, memory_type: str):
        """Get or create agent-specific collection."""
        key = f"{agent_name}_{memory_type}"
        if key not in self._collections:
            self._collections[key] = self.client.get_or_create_collection(key)
        return self._collections[key]
    
    def store_knowledge(
        self, agent_name: str, topic: str, 
        content: str, metadata: dict = None
    ):
        """Store learned knowledge for an agent."""
        collection = self._get_collection(agent_name, "knowledge")
        doc_id = hashlib.sha256(f"{agent_name}:{topic}".encode()).hexdigest()
        
        collection.upsert(
            documents=[content],
            metadatas=[{
                "agent": agent_name,
                "topic": topic,
                "timestamp": time.time(),
                **(metadata or {})
            }],
            ids=[doc_id]
        )
    
    def recall_knowledge(
        self, agent_name: str, query: str, n_results: int = 5
    ) -> List[Dict]:
        """Recall relevant knowledge for a task."""
        collection = self._get_collection(agent_name, "knowledge")
        
        try:
            results = collection.query(
                query_texts=[query],
                n_results=n_results
            )
            
            recalled = []
            if results and results["metadatas"]:
                for meta_list in results["metadatas"]:
                    for meta in meta_list:
                        recalled.append(meta)
            return recalled
        except Exception:
            return []

    def store_mistake(
        self, agent_name: str, task_type: str, 
        input_data: str, error: str, fix: str
    ):
        """Store a mistake for an agent."""
        collection = self._get_collection(agent_name, "mistakes")
        error_id = hashlib.sha256(f"{agent_name}:{task_type}:{error}".encode()).hexdigest()
        
        collection.upsert(
            documents=[f"{task_type}: {error}"],
            metadatas=[{
                "agent": agent_name,
                "task_type": task_type,
                "input": input_data,
                "error": error,
                "fix": fix,
                "timestamp": time.time()
            }],
            ids=[error_id]
        )

    def recall_mistakes(
        self, agent_name: str, task_type: str, 
        query: str, n_results: int = 5
    ) -> Dict[str, Any]:
        """Recall similar mistakes for an agent."""
        collection = self._get_collection(agent_name, "mistakes")
        
        try:
            results = collection.query(
                query_texts=[f"{task_type}: {query}"],
                n_results=n_results
            )
            return results
        except Exception:
            return {"ids": [], "metadatas": [], "documents": []}
    
    def get_agent_stats(self, agent_name: str) -> Dict:
        """Get memory statistics for an agent."""
        knowledge = self._get_collection(agent_name, "knowledge")
        mistakes = self._get_collection(agent_name, "mistakes")
        
        return {
            "agent": agent_name,
            "knowledge_entries": knowledge.count(),
            "mistake_entries": mistakes.count()
        }


agent_memory = AgentPermanentMemory()
