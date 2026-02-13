"""
Docker Manager module for ComfyUI Manager
Handles starting, stopping, and checking status of ComfyUI Docker container

Uses Python Docker SDK instead of CLI for smaller image size.
Works both locally and when running inside a Docker container.
Requires Docker socket mounted at /var/run/docker.sock when containerized.
"""

import asyncio
import os
import threading
from pathlib import Path
from typing import Optional, Dict, Any
from enum import Enum
from datetime import datetime

import docker
from docker.errors import NotFound, APIError, ImageNotFound
from dotenv import load_dotenv

load_dotenv()

# Get the directory for logs
BASE_DIR = Path(os.environ.get('COMPOSE_PROJECT_DIR', Path(__file__).resolve().parent))
LOGS_DIR = BASE_DIR / "logs"
STARTUP_LOG_FILE = LOGS_DIR / "comfyui_startup.log"

# Configuration from environment
COMFYUI_PORT = int(os.getenv("COMFYUI_PORT", "8188"))
COMFYUI_INTERNAL_HOST = os.getenv("COMFYUI_INTERNAL_HOST", "localhost")

# Container configuration (matches docker-compose-comfyui.yml)
CONTAINER_NAME = "comfyui"
IMAGE_NAME = "yanwk/comfyui-boot:cu128-slim"

# Local storage paths (relative to BASE_DIR)
STORAGE_MODELS_DIR = BASE_DIR / "storage-models"
STORAGE_USER_DIR = BASE_DIR / "storage-user"

CONTAINER_CONFIG = {
    "image": IMAGE_NAME,
    "name": CONTAINER_NAME,
    "ports": {"8188/tcp": COMFYUI_PORT},
    "environment": {"CLI_ARGS": ""},
    "volumes": {
        "comfyui-storage": {"bind": "/root", "mode": "rw"},
        # Bind mounts use absolute paths (resolved at runtime)
        str(STORAGE_MODELS_DIR / "models"): {"bind": "/root/ComfyUI/models", "mode": "rw"},
        str(STORAGE_MODELS_DIR / "hf-hub"): {"bind": "/root/.cache/huggingface/hub", "mode": "rw"},
        str(STORAGE_MODELS_DIR / "torch-hub"): {"bind": "/root/.cache/torch/hub", "mode": "rw"},
        str(STORAGE_USER_DIR / "input"): {"bind": "/root/ComfyUI/input", "mode": "rw"},
        str(STORAGE_USER_DIR / "output"): {"bind": "/root/ComfyUI/output", "mode": "rw"},
        str(STORAGE_USER_DIR / "workflows"): {"bind": "/root/ComfyUI/user/default/workflows", "mode": "rw"},
    },
    "detach": True,
    "restart_policy": {"Name": "unless-stopped"},
    "device_requests": [
        docker.types.DeviceRequest(
            device_ids=["0"],
            capabilities=[["gpu"]]
        )
    ],
    "healthcheck": {
        "test": ["CMD", "curl", "-f", f"http://localhost:8188"],
        "interval": 30000000000,  # 30s in nanoseconds
        "timeout": 10000000000,   # 10s
        "retries": 5,
        "start_period": 120000000000,  # 120s
    },
    "network": "comfyui-network",
}


class ContainerStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR = "error"
    NOT_FOUND = "not_found"


class DockerManager:
    """Manages ComfyUI Docker container operations using Python Docker SDK"""
    
    def __init__(self):
        self.container_name = CONTAINER_NAME
        self.image_name = IMAGE_NAME
        self._status_lock = asyncio.Lock()
        self._startup_thread = None
        self._client = None
        
        # Ensure logs directory exists
        LOGS_DIR.mkdir(exist_ok=True)
        
        # Initialize Docker client
        self._init_client()
    
    def _init_client(self):
        """Initialize Docker client"""
        try:
            self._client = docker.from_env()
            # Verify connection
            self._client.ping()
            print("[INFO] Docker client connected successfully")
        except Exception as e:
            print(f"[WARNING] Cannot connect to Docker: {e}")
            self._client = None
    
    def _get_client(self) -> docker.DockerClient:
        """Get Docker client, reinitialize if needed"""
        if self._client is None:
            self._init_client()
        return self._client
    
    def _get_container(self):
        """Get container object or None if not found"""
        client = self._get_client()
        if not client:
            return None
        try:
            return client.containers.get(self.container_name)
        except NotFound:
            return None
        except Exception as e:
            print(f"[WARNING] Error getting container: {e}")
            return None
    
    def _ensure_network(self):
        """Ensure the comfyui-network exists"""
        client = self._get_client()
        if not client:
            return
        try:
            client.networks.get("comfyui-network")
        except NotFound:
            client.networks.create("comfyui-network", driver="bridge")
            print("[INFO] Created comfyui-network")
    
    def _ensure_volumes(self):
        """Ensure all required volumes and local directories exist"""
        client = self._get_client()
        if not client:
            return
        
        # Only comfyui-storage is a named volume now
        volume_names = ["comfyui-storage"]
        
        for vol_name in volume_names:
            try:
                client.volumes.get(vol_name)
            except NotFound:
                client.volumes.create(vol_name)
                print(f"[INFO] Created volume: {vol_name}")
        
        # Ensure local storage directories exist
        local_dirs = [
            STORAGE_MODELS_DIR / "models",
            STORAGE_MODELS_DIR / "hf-hub",
            STORAGE_MODELS_DIR / "torch-hub",
            STORAGE_USER_DIR / "input",
            STORAGE_USER_DIR / "output",
            STORAGE_USER_DIR / "workflows",
        ]
        for dir_path in local_dirs:
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"[INFO] Ensured directory exists: {dir_path}")
    
    async def get_status(self) -> Dict[str, Any]:
        """
        Get the current status of the ComfyUI container
        Returns: dict with status info
        """
        try:
            container = self._get_container()
            
            if container is None:
                return {
                    "status": ContainerStatus.NOT_FOUND,
                    "message": "Container not created yet. Click Start to create and run it.",
                    "container_name": self.container_name
                }
            
            # Refresh container state
            container.reload()
            container_status = container.status
            
            # Get health status if available
            health_status = ""
            if container.attrs.get("State", {}).get("Health"):
                health_status = container.attrs["State"]["Health"].get("Status", "")
            
            if container_status == "running":
                status = ContainerStatus.RUNNING
                if health_status == "starting":
                    message = "Container is starting, health check in progress..."
                elif health_status == "healthy":
                    message = "ComfyUI is running and healthy"
                elif health_status == "unhealthy":
                    message = "ComfyUI is running but unhealthy"
                else:
                    message = "ComfyUI is running"
            elif container_status in ["created", "restarting"]:
                status = ContainerStatus.STARTING
                message = "Container is starting..."
            elif container_status == "exited":
                status = ContainerStatus.STOPPED
                message = "Container has stopped"
            else:
                status = ContainerStatus.STOPPED
                message = f"Container status: {container_status}"
            
            # Get port if running (URL will be built by the caller based on request host)
            extra_info = {}
            if status == ContainerStatus.RUNNING:
                extra_info["port"] = COMFYUI_PORT
                extra_info["internal_url"] = f"http://{COMFYUI_INTERNAL_HOST}:{COMFYUI_PORT}"
            
            return {
                "status": status,
                "message": message,
                "container_name": self.container_name,
                "health": health_status,
                **extra_info
            }
            
        except Exception as e:
            return {
                "status": ContainerStatus.ERROR,
                "message": f"Error checking status: {str(e)}",
                "container_name": self.container_name
            }
    
    def _run_startup_in_background(self):
        """Pull image if needed and start the container"""
        client = self._get_client()
        if not client:
            with open(STARTUP_LOG_FILE, 'w') as f:
                f.write("[ERROR] Docker client not available\n")
            return
        
        try:
            # Clear previous startup log
            with open(STARTUP_LOG_FILE, 'w') as f:
                f.write(f"=== ComfyUI Startup Log - {datetime.now().isoformat()} ===\n\n")
            
            # Ensure network and volumes exist
            with open(STARTUP_LOG_FILE, 'a') as f:
                f.write("[INFO] Ensuring network and volumes exist...\n")
                f.flush()
            
            self._ensure_network()
            self._ensure_volumes()
            
            # Check if image exists, pull if needed
            with open(STARTUP_LOG_FILE, 'a') as f:
                f.write(f"[INFO] Checking image: {self.image_name}\n")
                f.flush()
            
            try:
                client.images.get(self.image_name)
                with open(STARTUP_LOG_FILE, 'a') as f:
                    f.write("[INFO] Image already exists locally\n")
                    f.flush()
            except ImageNotFound:
                with open(STARTUP_LOG_FILE, 'a') as f:
                    f.write(f"[INFO] Pulling image: {self.image_name} (this may take several minutes)...\n")
                    f.flush()
                
                # Pull with progress
                for line in client.api.pull(self.image_name, stream=True, decode=True):
                    status = line.get('status', '')
                    progress = line.get('progress', '')
                    layer_id = line.get('id', '')
                    
                    with open(STARTUP_LOG_FILE, 'a') as f:
                        if layer_id:
                            f.write(f"  {layer_id}: {status} {progress}\n")
                        else:
                            f.write(f"  {status} {progress}\n")
                        f.flush()
                
                with open(STARTUP_LOG_FILE, 'a') as f:
                    f.write("[INFO] Image pull complete\n")
                    f.flush()
            
            # Check if container exists
            container = self._get_container()
            
            if container is None:
                # Create new container
                with open(STARTUP_LOG_FILE, 'a') as f:
                    f.write("[INFO] Creating new container...\n")
                    f.flush()
                
                container = client.containers.create(
                    image=self.image_name,
                    name=self.container_name,
                    ports=CONTAINER_CONFIG["ports"],
                    environment=CONTAINER_CONFIG["environment"],
                    volumes=CONTAINER_CONFIG["volumes"],
                    detach=True,
                    restart_policy=CONTAINER_CONFIG["restart_policy"],
                    device_requests=CONTAINER_CONFIG["device_requests"],
                    healthcheck=CONTAINER_CONFIG["healthcheck"],
                    network=CONTAINER_CONFIG["network"],
                )
                
                with open(STARTUP_LOG_FILE, 'a') as f:
                    f.write(f"[INFO] Container created: {container.id[:12]}\n")
                    f.flush()
            
            # Start the container
            with open(STARTUP_LOG_FILE, 'a') as f:
                f.write("[INFO] Starting container...\n")
                f.flush()
            
            container.start()
            
            with open(STARTUP_LOG_FILE, 'a') as f:
                f.write("[INFO] Container started successfully\n")
                f.write("\n=== Streaming container logs ===\n\n")
                f.flush()
            
            # Stream logs
            for line in container.logs(stream=True, follow=True, tail=50):
                with open(STARTUP_LOG_FILE, 'a') as f:
                    f.write(line.decode('utf-8', errors='replace'))
                    f.flush()
                
                # Check if container is still running
                container.reload()
                if container.status != "running":
                    break
            
        except Exception as e:
            with open(STARTUP_LOG_FILE, 'a') as f:
                f.write(f"\n=== ERROR: {str(e)} ===\n")
    
    async def start(self) -> Dict[str, Any]:
        """
        Start the ComfyUI container
        Returns: dict with result info
        """
        async with self._status_lock:
            try:
                # Check current status first
                current_status = await self.get_status()
                if current_status["status"] == ContainerStatus.RUNNING:
                    return {
                        "success": True,
                        "message": "ComfyUI is already running",
                        "status": ContainerStatus.RUNNING
                    }
                
                # Check if startup is already in progress
                if self._startup_thread and self._startup_thread.is_alive():
                    return {
                        "success": True,
                        "message": "ComfyUI startup already in progress",
                        "status": ContainerStatus.STARTING
                    }
                
                # Start container in background thread
                self._startup_thread = threading.Thread(
                    target=self._run_startup_in_background,
                    daemon=True
                )
                self._startup_thread.start()
                
                return {
                    "success": True,
                    "message": "ComfyUI container starting. Check logs for progress.",
                    "status": ContainerStatus.STARTING
                }
                
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Error starting container: {str(e)}",
                    "status": ContainerStatus.ERROR
                }
    
    async def stop(self) -> Dict[str, Any]:
        """
        Stop the ComfyUI container
        Returns: dict with result info
        """
        async with self._status_lock:
            try:
                container = self._get_container()
                
                if container is None:
                    return {
                        "success": True,
                        "message": "Container does not exist",
                        "status": ContainerStatus.NOT_FOUND
                    }
                
                container.reload()
                if container.status != "running":
                    return {
                        "success": True,
                        "message": "ComfyUI is already stopped",
                        "status": ContainerStatus.STOPPED
                    }
                
                # Stop the container
                container.stop(timeout=30)
                
                return {
                    "success": True,
                    "message": "ComfyUI container stopped successfully",
                    "status": ContainerStatus.STOPPED
                }
                
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Error stopping container: {str(e)}",
                    "status": ContainerStatus.ERROR
                }
    
    async def restart(self) -> Dict[str, Any]:
        """
        Restart the ComfyUI container
        Returns: dict with result info
        """
        async with self._status_lock:
            try:
                container = self._get_container()
                
                if container is None:
                    # No container exists, just start
                    return await self.start()
                
                # Restart the container
                container.restart(timeout=30)
                
                return {
                    "success": True,
                    "message": "ComfyUI container restarted successfully",
                    "status": ContainerStatus.STARTING
                }
                
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Error restarting container: {str(e)}",
                    "status": ContainerStatus.ERROR
                }
    
    async def get_logs(self, lines: int = 100) -> Dict[str, Any]:
        """
        Get recent logs from the ComfyUI container and startup log
        Returns: dict with logs
        """
        try:
            logs_parts = []
            
            # First, get startup logs if they exist
            if STARTUP_LOG_FILE.exists():
                try:
                    with open(STARTUP_LOG_FILE, 'r') as f:
                        startup_logs = f.read()
                    if startup_logs.strip():
                        logs_parts.append("=== STARTUP/PULL LOG ===\n" + startup_logs)
                except Exception as e:
                    logs_parts.append(f"(Error reading startup log: {e})\n")
            
            # Then get container logs
            container = self._get_container()
            if container:
                try:
                    container_logs = container.logs(tail=lines).decode('utf-8', errors='replace')
                    if container_logs.strip():
                        logs_parts.append("\n=== CONTAINER LOG ===\n" + container_logs)
                except Exception as e:
                    logs_parts.append(f"\n(Error reading container logs: {e})\n")
            
            if not logs_parts:
                return {
                    "success": True,
                    "message": "No logs available yet",
                    "logs": "(Waiting for container to start...)"
                }
            
            return {
                "success": True,
                "message": "Logs retrieved",
                "logs": "\n".join(logs_parts)
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error getting logs: {str(e)}",
                "logs": ""
            }
    
    async def get_startup_logs(self) -> Dict[str, Any]:
        """
        Get only the startup/pull logs
        Returns: dict with logs
        """
        try:
            if not STARTUP_LOG_FILE.exists():
                return {
                    "success": True,
                    "message": "No startup logs yet",
                    "logs": "(No startup initiated yet)"
                }
            
            with open(STARTUP_LOG_FILE, 'r') as f:
                logs = f.read()
            
            is_running = self._startup_thread and self._startup_thread.is_alive()
            
            return {
                "success": True,
                "message": "Startup in progress..." if is_running else "Startup logs",
                "logs": logs if logs.strip() else "(Waiting for output...)",
                "in_progress": is_running
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error getting startup logs: {str(e)}",
                "logs": ""
            }


# Singleton instance
docker_manager = DockerManager()
