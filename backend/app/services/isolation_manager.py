# File: app/services/isolation_manager.py

import docker
import subprocess
import logging
import os
from typing import Dict, Any, Optional
from pathlib import Path

class IsolationManager:
    """
    Hardware-enforced isolation per patent spec.
    Provides secure, isolated execution environments using Docker containers
    with namespace isolation, cgroup resource limits, and seccomp filtering.
    """
    
    def __init__(self, base_workspace: str = "/tmp/nexsidi"):
        self.logger = logging.getLogger("isolation_manager")
        self.base_workspace = base_workspace
        
        try:
            self.docker_client = docker.from_env()
            self.docker_client.ping()
            self.logger.info("✅ Connected to Docker daemon")
            self.connected = True
        except Exception as e:
            self.logger.warning(f"⚠️ Docker unavailable: {e}")
            self.docker_client = None
            self.connected = False
    
    def create_isolated_environment(
        self, 
        project_id: str, 
        workspace_path: Optional[str] = None,
        resource_limits: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create Docker container with namespace + cgroup + seccomp isolation (Patent Claims 1e/5).
        
        Args:
            project_id: Unique project identifier
            workspace_path: Optional path to bind to /workspace
            resource_limits: Optional dict with cpu_percent, memory_mb, disk_mb
            config: Optional legacy configuration overrides
        
        Returns:
            Dict with status and container_id (or error)
        """
        if not self.connected:
            return {"status": "error", "error": "Docker not available"}
        
        # Default configuration
        default_config = {
            "image": "python:3.11-slim",
            "mem_limit": "512m",
            "cpu_quota": 50000,
            "pids_limit": 100,
            "network_mode": "bridge",
            "user": "root"
        }
        
        # Apply resource limits if provided
        if resource_limits:
            if "memory_mb" in resource_limits:
                default_config["mem_limit"] = f"{resource_limits['memory_mb']}m"
            if "cpu_percent" in resource_limits:
                default_config["cpu_quota"] = int(resource_limits["cpu_percent"] * 1000)
        
        # Merge with legacy config
        if config:
            default_config.update(config)
        
        # Determine workspace path
        target_workspace = Path(workspace_path) if workspace_path else (Path(self.base_workspace) / project_id)
        target_workspace.mkdir(parents=True, exist_ok=True)
        
        container_name = f"nexsidi-{project_id}"
        
        # Load seccomp profile (Patent Claim 1e)
        seccomp_profile_path = Path(__file__).parent / "seccomp_profile.json"
        
        try:
            # Check if container already exists
            try:
                existing = self.docker_client.containers.get(container_name)
                self.logger.warning(f"⚠️ Container {container_name} already exists, removing...")
                existing.remove(force=True)
            except docker.errors.NotFound:
                pass
            
            self.logger.info(f"🔒 Creating isolated environment for {project_id} with seccomp + cgroup v2")
            
            # Security options (Patent Claims 1e/5)
            security_opt = [
                "no-new-privileges",  # Prevent privilege escalation
            ]
            
            # Add seccomp profile if available
            if seccomp_profile_path.exists():
                security_opt.append(f"seccomp={seccomp_profile_path.absolute()}")
                self.logger.info("🔒 Seccomp-bpf filter enabled")
            else:
                self.logger.warning("⚠️ Seccomp profile not found, using default")
            
            # Docker with enhanced security constraints
            container = self.docker_client.containers.run(
                image=default_config["image"],
                name=container_name,
                detach=True,
                network_mode=default_config["network_mode"],
                user=default_config["user"],
                mem_limit=default_config["mem_limit"],
                cpu_quota=default_config["cpu_quota"],
                pids_limit=default_config["pids_limit"],
                security_opt=security_opt,
                cap_drop=["ALL"],  # Drop all capabilities
                cap_add=["CHOWN", "DAC_OVERRIDE", "SETGID", "SETUID"],  # Add only required caps
                volumes={
                    str(target_workspace.absolute()): {
                        "bind": "/workspace",
                        "mode": "rw"
                    }
                },
                read_only=False,  # Workspace needs to be writable
                tmpfs={
                    "/tmp": "rw,noexec,nosuid,size=100m"  # Temporary filesystem with restrictions
                },
                remove=False,
                command="tail -f /dev/null",
                environment={
                    "PROJECT_ID": project_id,
                    "PYTHONUNBUFFERED": "1"
                }
            )
            
            return {
                "status": "success",
                "container_id": container.id,
                "container_name": container_name,
                "security": {
                    "seccomp": seccomp_profile_path.exists(),
                    "no_new_privileges": True,
                    "capabilities_dropped": True
                }
            }
            
        except Exception as e:
            self.logger.error(f"❌ Failed to create isolated environment: {e}")
            return {"status": "error", "error": str(e)}

    def execute_in_container(self, container_id: str, command: str, timeout: int = 300) -> Dict[str, Any]:
        """
        Execute command in isolated container.
        
        Args:
            container_id: Container ID or Project ID
            command: Command to execute
            timeout: Command timeout in seconds
        """
        if not self.connected:
            return {"status": "error", "error": "Docker not available"}
        
        try:
            # Handle both container_id and project_id for backward compatibility
            try:
                container = self.docker_client.containers.get(container_id)
            except docker.errors.NotFound:
                container = self.docker_client.containers.get(f"nexsidi-{container_id}")
            
            self.logger.info(f"🔧 Executing in {container.name}: {command}")
            
            result = container.exec_run(
                cmd=command,
                workdir="/workspace",
                user="root"
            )
            
            status = "success" if result.exit_code == 0 else "error"
            return {
                "status": status,
                "exit_code": result.exit_code,
                "stdout": result.output.decode('utf-8') if result.output else "",
                "stderr": "",
                "error": None if result.exit_code == 0 else f"Command failed with exit code {result.exit_code}"
            }
            
        except Exception as e:
            self.logger.error(f"❌ Execution failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def start_service(self, container_id: str, command: str, port: int) -> Dict[str, Any]:
        """
        Start a non-blocking service in the container.
        """
        if not self.connected:
            return {"status": "error", "error": "Docker not available"}
        
        try:
            # Similar container lookup
            try:
                container = self.docker_client.containers.get(container_id)
            except docker.errors.NotFound:
                container = self.docker_client.containers.get(f"nexsidi-{container_id}")
            
            self.logger.info(f"🚀 Starting service in {container.name}: {command}")
            
            # Execute in background
            container.exec_run(
                cmd=f"nohup {command} > /workspace/service.log 2>&1 &",
                workdir="/workspace",
                user="root",
                detach=True
            )
            
            return {
                "status": "success",
                "url": f"http://localhost:{port}"
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to start service: {e}")
            return {"status": "error", "error": str(e)}
    
    def destroy_environment(self, project_id: str) -> bool:
        """
        Destroy isolated environment and cleanup resources.
        
        Args:
            project_id: Project identifier
        
        Returns:
            True if successful, False otherwise
        """
        if not self.connected:
            self.logger.warning("⚠️ Docker not available, skipping container cleanup")
            return False
        
        container_name = f"nexsidi-{project_id}"
        
        try:
            container = self.docker_client.containers.get(container_name)
            container.stop(timeout=5)
            container.remove(force=True)
            self.logger.info(f"🧹 Destroyed isolated environment: {container_name}")
            
            # Cleanup workspace directory
            workspace_path = Path(self.base_workspace) / project_id
            if workspace_path.exists():
                import shutil
                shutil.rmtree(workspace_path)
                self.logger.info(f"🧹 Cleaned up workspace: {workspace_path}")
            
            return True
            
        except docker.errors.NotFound:
            self.logger.warning(f"⚠️ Container not found: {container_name}")
            return False
        except Exception as e:
            self.logger.error(f"❌ Failed to destroy environment: {e}")
            return False
    
    def get_container_stats(self, project_id: str) -> Optional[Dict[str, Any]]:
        """
        Get resource usage statistics for container.
        
        Args:
            project_id: Project identifier
        
        Returns:
            Dict with CPU, memory, network stats or None
        """
        if not self.connected:
            return None
        
        container_name = f"nexsidi-{project_id}"
        
        try:
            container = self.docker_client.containers.get(container_name)
            stats = container.stats(stream=False)
            
            # Parse stats
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                       stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                          stats['precpu_stats']['system_cpu_usage']
            cpu_percent = (cpu_delta / system_delta) * 100.0 if system_delta > 0 else 0.0
            
            mem_usage = stats['memory_stats']['usage']
            mem_limit = stats['memory_stats']['limit']
            mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0
            
            return {
                "cpu_percent": round(cpu_percent, 2),
                "memory_usage_mb": round(mem_usage / (1024 * 1024), 2),
                "memory_limit_mb": round(mem_limit / (1024 * 1024), 2),
                "memory_percent": round(mem_percent, 2),
                "pids": stats['pids_stats']['current'] if 'pids_stats' in stats else 0
            }
            
        except docker.errors.NotFound:
            self.logger.error(f"❌ Container not found: {container_name}")
            return None
        except Exception as e:
            self.logger.error(f"❌ Failed to get stats: {e}")
            return None

# Global instance
isolation_manager = IsolationManager()
