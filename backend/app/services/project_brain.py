"""
PROJECT BRAIN - Architectural Intelligence System
==================================================
Location: app/services/project_brain.py

Purpose: Graph-RAG for architectural understanding
- Dependency graph generation
- Impact analysis
- Smart context loading (Active Sphere)
- Change prediction

Patent Feature: Intelligent code navigation beyond flat context
"""

import os
import ast
import json
import logging
import networkx as nx
from typing import Dict, Any, List, Set, Optional, Tuple
from pathlib import Path
from collections import defaultdict


class ProjectBrain:
    """
    Architectural intelligence for code projects.
    
    Uses Graph-RAG approach:
    - Parse code to extract dependencies
    - Build directed graph of relationships
    - Analyze impact of changes
    - Load only relevant context (Active Sphere)
    
    This enables:
    - Smart file selection (not all files)
    - Change impact prediction
    - Dependency understanding
    - Context window optimization
    """
    
    def __init__(self, project_id: str, code_dir: str):
        self.project_id = project_id
        self.code_dir = Path(code_dir)
        self.logger = logging.getLogger(f"project_brain.{project_id}")
        
        # Dependency graph
        self.graph = nx.DiGraph()
        
        # File metadata
        self.file_metadata: Dict[str, Dict] = {}
        
        # Analysis cache
        self._cache: Dict[str, Any] = {}
    
    def analyze_project(self) -> Dict[str, Any]:
        """
        Analyze entire project structure.
        
        Returns:
        - Dependency graph
        - File relationships
        - Entry points
        - Hot spots (frequently modified)
        """
        
        self.logger.info(f"🧠 Analyzing project: {self.code_dir}")
        
        # Find all code files
        code_files = self._find_code_files()
        
        if not code_files:
            return {
                "status": "empty",
                "message": "No code files found"
            }
        
        # Parse each file and build graph
        for filepath in code_files:
            self._parse_file(filepath)
        
        # Analyze graph
        analysis = {
            "total_files": len(code_files),
            "total_dependencies": self.graph.number_of_edges(),
            "entry_points": self._find_entry_points(),
            "dependency_graph": self._graph_to_dict(),
            "complexity_score": self._calculate_complexity()
        }
        
        self.logger.info(
            f"✅ Analysis complete: {analysis['total_files']} files, "
            f"{analysis['total_dependencies']} dependencies"
        )
        
        return analysis
    
    def get_active_sphere(
        self,
        target_files: List[str],
        radius: int = 2
    ) -> Dict[str, Any]:
        """
        Get Active Sphere - files relevant to target files.
        
        Args:
            target_files: Files being modified/analyzed
            radius: How many hops away to include (default 2)
        
        Returns:
            Dict with relevant files and their relationships
        """
        
        relevant_files = set()
        relationships = []
        
        for target_file in target_files:
            # Normalize path
            target_path = self._normalize_path(target_file)
            
            if target_path not in self.graph:
                continue
            
            # Get files within radius
            # Upstream dependencies (files this imports)
            for _ in range(radius):
                predecessors = list(self.graph.predecessors(target_path))
                relevant_files.update(predecessors)
                
                for pred in predecessors:
                    relationships.append({
                        "from": pred,
                        "to": target_path,
                        "type": "imports"
                    })
            
            # Downstream dependencies (files that import this)
            for _ in range(radius):
                successors = list(self.graph.successors(target_path))
                relevant_files.update(successors)
                
                for succ in successors:
                    relationships.append({
                        "from": target_path,
                        "to": succ,
                        "type": "imported_by"
                    })
        
        # Always include target files
        relevant_files.update(target_files)
        
        return {
            "target_files": target_files,
            "relevant_files": list(relevant_files),
            "relationships": relationships,
            "total_relevant": len(relevant_files)
        }
    
    def predict_impact(
        self,
        changed_files: List[str]
    ) -> Dict[str, Any]:
        """
        Predict impact of changing specified files.
        
        Returns:
        - Directly affected files
        - Transitively affected files
        - Risk assessment
        """
        
        affected_files = set()
        risk_score = 0.0
        
        for changed_file in changed_files:
            file_path = self._normalize_path(changed_file)
            
            if file_path not in self.graph:
                continue
            
            # Find all downstream dependencies (BFS)
            downstream = nx.descendants(self.graph, file_path)
            affected_files.update(downstream)
            
            # Calculate risk
            risk_score += len(downstream) * 0.1  # More dependents = higher risk
            
            # Check if it's a critical file
            if self._is_critical_file(file_path):
                risk_score += 1.0
        
        return {
            "changed_files": changed_files,
            "directly_affected": len(affected_files),
            "affected_files": list(affected_files),
            "risk_score": min(risk_score, 10.0),  # Cap at 10
            "risk_level": self._risk_level(risk_score)
        }
    
    def suggest_related_files(
        self,
        context: str,
        max_files: int = 5
    ) -> List[str]:
        """
        Suggest files relevant to given context/task.
        
        Uses:
        - Keyword matching
        - Dependency relationships
        - File metadata
        """
        
        scores = {}
        
        # Score each file
        for filepath, metadata in self.file_metadata.items():
            score = 0.0
            
            # Keyword matching
            if any(keyword in filepath.lower() for keyword in context.lower().split()):
                score += 2.0
            
            # Check file content keywords
            content_keywords = metadata.get("keywords", [])
            for keyword in context.lower().split():
                if keyword in content_keywords:
                    score += 1.0
            
            # Centrality in graph (important files)
            if filepath in self.graph:
                degree = self.graph.degree(filepath)
                score += degree * 0.1
            
            scores[filepath] = score
        
        # Sort by score
        sorted_files = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        
        return [f for f, s in sorted_files[:max_files] if s > 0]
    
    def _find_code_files(self) -> List[Path]:
        """Find all code files in project"""
        
        extensions = {".py", ".js", ".jsx", ".ts", ".tsx", ".vue"}
        code_files = []
        
        for ext in extensions:
            code_files.extend(self.code_dir.rglob(f"*{ext}"))
        
        # Filter out node_modules, venv, etc.
        filtered = []
        exclude_dirs = {"node_modules", "venv", "__pycache__", ".git"}
        
        for filepath in code_files:
            if not any(exc in filepath.parts for exc in exclude_dirs):
                filtered.append(filepath)
        
        return filtered
    
    def _parse_file(self, filepath: Path):
        """Parse file and extract dependencies"""
        
        try:
            content = filepath.read_text(encoding='utf-8')
            
            # Add file to graph
            rel_path = str(filepath.relative_to(self.code_dir))
            self.graph.add_node(rel_path)
            
            # Extract metadata
            self.file_metadata[rel_path] = {
                "path": rel_path,
                "lines": len(content.split('\n')),
                "keywords": self._extract_keywords(content),
                "language": filepath.suffix
            }
            
            # Parse based on language
            if filepath.suffix == ".py":
                self._parse_python(filepath, content, rel_path)
            elif filepath.suffix in {".js", ".jsx", ".ts", ".tsx"}:
                self._parse_javascript(filepath, content, rel_path)
            
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to parse {filepath}: {e}")
    
    def _parse_python(self, filepath: Path, content: str, rel_path: str):
        """Parse Python file for imports"""
        
        try:
            tree = ast.parse(content)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self._add_dependency(rel_path, alias.name)
                
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        self._add_dependency(rel_path, node.module)
        
        except SyntaxError:
            pass  # Ignore syntax errors
    
    def _parse_javascript(self, filepath: Path, content: str, rel_path: str):
        """Parse JavaScript/TypeScript for imports"""
        
        import re
        
        # Regex for import statements
        patterns = [
            r"import\s+.*\s+from\s+['\"](.+?)['\"]",
            r"require\(['\"](.+?)['\"]\)"
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                self._add_dependency(rel_path, match)
    
    def _add_dependency(self, from_file: str, to_module: str):
        """Add dependency edge to graph"""
        
        # Try to resolve module to file
        resolved = self._resolve_module(to_module)
        
        if resolved:
            self.graph.add_edge(from_file, resolved)
    
    def _resolve_module(self, module_name: str) -> Optional[str]:
        """Resolve module name to file path"""
        
        # For local imports (starting with . or /)
        if module_name.startswith('.') or module_name.startswith('/'):
            # Try to find the file
            for filepath in self.file_metadata.keys():
                if module_name in filepath:
                    return filepath
        
        return None
    
    def _extract_keywords(self, content: str) -> List[str]:
        """Extract relevant keywords from content"""
        
        keywords = set()
        
        # Common technical keywords
        tech_keywords = {
            "auth", "user", "api", "database", "model", "view",
            "controller", "service", "utils", "config", "test"
        }
        
        content_lower = content.lower()
        
        for keyword in tech_keywords:
            if keyword in content_lower:
                keywords.add(keyword)
        
        return list(keywords)
    
    def _find_entry_points(self) -> List[str]:
        """Find entry point files (no incoming dependencies)"""
        
        entry_points = []
        
        for node in self.graph.nodes():
            if self.graph.in_degree(node) == 0:
                entry_points.append(node)
        
        return entry_points
    
    def _is_critical_file(self, filepath: str) -> bool:
        """Check if file is critical (many dependencies)"""
        
        if filepath not in self.graph:
            return False
        
        # File is critical if many files depend on it
        return self.graph.out_degree(filepath) > 5
    
    def _calculate_complexity(self) -> float:
        """Calculate overall project complexity"""
        
        if self.graph.number_of_nodes() == 0:
            return 0.0
        
        # Complexity factors
        num_files = self.graph.number_of_nodes()
        num_deps = self.graph.number_of_edges()
        avg_degree = sum(dict(self.graph.degree()).values()) / num_files if num_files > 0 else 0
        
        complexity = (num_files * 0.1) + (num_deps * 0.05) + (avg_degree * 0.5)
        
        return round(complexity, 2)
    
    def _risk_level(self, score: float) -> str:
        """Convert risk score to level"""
        
        if score < 2.0:
            return "low"
        elif score < 5.0:
            return "medium"
        elif score < 8.0:
            return "high"
        else:
            return "critical"
    
    def _normalize_path(self, filepath: str) -> str:
        """Normalize file path for graph"""
        
        # Remove leading/trailing slashes
        return filepath.strip('/')
    
    def _graph_to_dict(self) -> Dict[str, List[str]]:
        """Convert graph to dictionary format"""
        
        result = {}
        
        for node in self.graph.nodes():
            result[node] = {
                "imports": list(self.graph.predecessors(node)),
                "imported_by": list(self.graph.successors(node))
            }
        
        return result


if __name__ == "__main__":
    # Test project brain
    print("🧠 Testing ProjectBrain...")
    
    # Create test project structure
    test_dir = Path("/tmp/test_project")
    test_dir.mkdir(exist_ok=True)
    
    # Create test files
    (test_dir / "main.py").write_text("""
from utils import helper
from models import User

def main():
    user = User()
    helper.process(user)
""")
    
    (test_dir / "utils.py").write_text("""
def helper():
    pass
""")
    
    (test_dir / "models.py").write_text("""
class User:
    pass
""")
    
    # Analyze
    brain = ProjectBrain("test-001", str(test_dir))
    analysis = brain.analyze_project()
    
    print(f"\n📊 Analysis:")
    print(f"  Files: {analysis['total_files']}")
    print(f"  Dependencies: {analysis['total_dependencies']}")
    print(f"  Complexity: {analysis['complexity_score']}")
    
    # Test Active Sphere
    sphere = brain.get_active_sphere(["main.py"], radius=1)
    print(f"\n🎯 Active Sphere for main.py:")
    print(f"  Relevant files: {sphere['relevant_files']}")
    
    # Test Impact Analysis
    impact = brain.predict_impact(["models.py"])
    print(f"\n💥 Impact of changing models.py:")
    print(f"  Affected files: {impact['affected_files']}")
    print(f"  Risk level: {impact['risk_level']}")
    
    print("\n✅ ProjectBrain test complete!")
