"""
MacBot Resource Management Module - Context managers and cleanup utilities
"""
import os
import time
import threading
import psutil
from contextlib import contextmanager
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

from .logging_utils import setup_logger

logger = setup_logger("macbot.resource_manager", "logs/macbot.log")

@dataclass
class ResourceInfo:
    """Information about a managed resource"""
    name: str
    resource_type: str
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

class ResourceManager:
    """Centralized resource management for MacBot"""

    def __init__(self):
        self.resources: Dict[str, ResourceInfo] = {}
        self.cleanup_callbacks: Dict[str, List[Callable]] = {}
        self.lock = threading.Lock()
        self.process_monitor = ProcessMonitor()

    def register_resource(self, name: str, resource_type: str, cleanup_callback: Optional[Callable] = None, **metadata) -> None:
        """Register a resource for management"""
        with self.lock:
            self.resources[name] = ResourceInfo(
                name=name,
                resource_type=resource_type,
                metadata=metadata
            )

            if cleanup_callback:
                if name not in self.cleanup_callbacks:
                    self.cleanup_callbacks[name] = []
                self.cleanup_callbacks[name].append(cleanup_callback)

            logger.debug(f"Registered resource: {name} ({resource_type})")

    def unregister_resource(self, name: str) -> None:
        """Unregister a resource"""
        with self.lock:
            if name in self.resources:
                del self.resources[name]
            if name in self.cleanup_callbacks:
                del self.cleanup_callbacks[name]
            logger.debug(f"Unregistered resource: {name}")

    def get_resource_info(self, name: str) -> Optional[ResourceInfo]:
        """Get information about a resource"""
        with self.lock:
            resource = self.resources.get(name)
            if resource:
                resource.last_accessed = datetime.now()
            return resource

    def cleanup_resource(self, name: str) -> bool:
        """Clean up a specific resource"""
        with self.lock:
            if name not in self.cleanup_callbacks:
                logger.warning(f"No cleanup callbacks registered for resource: {name}")
                return False

            success = True
            for callback in self.cleanup_callbacks[name]:
                try:
                    callback()
                    logger.debug(f"Successfully cleaned up resource: {name}")
                except Exception as e:
                    logger.error(f"Failed to cleanup resource {name}: {e}")
                    success = False

            # Remove from tracking
            if name in self.resources:
                del self.resources[name]
            del self.cleanup_callbacks[name]

            return success

    def cleanup_all(self) -> Dict[str, bool]:
        """Clean up all managed resources"""
        results = {}
        with self.lock:
            for name in list(self.resources.keys()):
                results[name] = self.cleanup_resource(name)
        return results

    def get_resource_stats(self) -> Dict[str, Any]:
        """Get resource usage statistics"""
        return {
            'total_resources': len(self.resources),
            'resource_types': self._get_resource_type_counts(),
            'memory_usage': self.process_monitor.get_memory_usage(),
            'file_handles': self.process_monitor.get_file_descriptor_count()
        }

    def _get_resource_type_counts(self) -> Dict[str, int]:
        """Get counts of resources by type"""
        counts = {}
        for resource in self.resources.values():
            counts[resource.resource_type] = counts.get(resource.resource_type, 0) + 1
        return counts

class ProcessMonitor:
    """Monitor system resources and process information"""

    def __init__(self):
        self.process = psutil.Process()

    def get_memory_usage(self) -> Dict[str, Any]:
        """Get memory usage statistics"""
        try:
            memory = self.process.memory_info()
            return {
                'rss_mb': memory.rss / (1024 * 1024),
                'vms_mb': memory.vms / (1024 * 1024),
                'percent': self.process.memory_percent()
            }
        except Exception as e:
            logger.warning(f"Failed to get memory usage: {e}")
            return {'error': str(e)}

    def get_file_descriptor_count(self) -> int:
        """Get number of open file descriptors"""
        try:
            return self.process.num_fds()
        except Exception:
            return 0

    def get_thread_count(self) -> int:
        """Get number of threads"""
        try:
            return self.process.num_threads()
        except Exception:
            return 0

_resource_manager_instance: Optional[ResourceManager] = None

def get_resource_manager() -> ResourceManager:
    """Get or create resourceManager instance"""
    global _resource_manager_instance
    if _resource_manager_instance is None:
        _resource_manager_instance = ResourceManager()
    return _resource_manager_instance

# Context managers for common resource types

@contextmanager
def managed_file(file_path: str, mode: str = 'r'):
    """Context manager for file operations with automatic cleanup"""
    file_obj = None
    try:
        file_obj = open(file_path, mode)
        yield file_obj
    except Exception as e:
        logger.error(f"File operation failed for {file_path}: {e}")
        raise
    finally:
        if file_obj:
            try:
                file_obj.close()
                logger.debug(f"Closed file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to close file {file_path}: {e}")

@contextmanager
def managed_temp_file(suffix: Optional[str] = None, prefix: Optional[str] = None, delete: bool = True):
    """Context manager for temporary files"""
    import tempfile
    temp_file = None
    try:
        temp_file = tempfile.NamedTemporaryFile(suffix=suffix, prefix=prefix, delete=False)
        yield temp_file
    except Exception as e:
        logger.error(f"Temp file operation failed: {e}")
        raise
    finally:
        if temp_file:
            try:
                temp_file.close()
                if delete and os.path.exists(temp_file.name):
                    os.unlink(temp_file.name)
                    logger.debug(f"Cleaned up temp file: {temp_file.name}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp file {temp_file.name}: {e}")

@contextmanager
def managed_process(command: List[str], **kwargs):
    """Context manager for subprocess execution"""
    import subprocess
    process = None
    try:
        process = subprocess.Popen(command, **kwargs)
        yield process
    except Exception as e:
        logger.error(f"Process execution failed: {e}")
        raise
    finally:
        if process:
            try:
                if process.poll() is None:  # Still running
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                logger.debug(f"Cleaned up process: {' '.join(command)}")
            except Exception as e:
                logger.warning(f"Failed to cleanup process: {e}")

@contextmanager
def managed_thread_pool(max_workers: int = 4):
    """Context manager for thread pool"""
    import concurrent.futures
    executor = None
    try:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        yield executor
    except Exception as e:
        logger.error(f"Thread pool creation failed: {e}")
        raise
    finally:
        if executor:
            try:
                executor.shutdown(wait=True)
                logger.debug("Cleaned up thread pool")
            except Exception as e:
                logger.warning(f"Failed to cleanup thread pool: {e}")

@contextmanager
def managed_resource(name: str, resource_type: str, cleanup_callback: Optional[Callable[..., Any]] = None, **metadata):
    """Context manager for generic resources"""
    manager = get_resource_manager()
    manager.register_resource(name, resource_type, cleanup_callback, **metadata)

    try:
        yield
    except Exception as e:
        logger.error(f"Resource {name} failed: {e}")
        raise
    finally:
        try:
            manager.cleanup_resource(name)
        except Exception as e:
            logger.warning(f"Failed to cleanup resource {name}: {e}")

# Resource tracking decorators

def track_resource(resource_type: str, name: Optional[str] = None):
    """Decorator to track resource usage"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            resource_name = name or f"{func.__name__}_resource"
            manager = get_resource_manager()

            # Register resource before execution
            manager.register_resource(resource_name, resource_type)

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                # Resource will be cleaned up in the context manager
                raise
            finally:
                # Clean up resource
                try:
                    manager.cleanup_resource(resource_name)
                except Exception as cleanup_e:
                    logger.warning(f"Failed to cleanup tracked resource {resource_name}: {cleanup_e}")

        return wrapper
    return decorator

def track_memory_usage():
    """Decorator to track memory usage of a function"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            monitor = ProcessMonitor()
            before_memory = monitor.get_memory_usage()

            try:
                result = func(*args, **kwargs)
                after_memory = monitor.get_memory_usage()

                memory_delta = after_memory.get('rss_mb', 0) - before_memory.get('rss_mb', 0)
                if memory_delta > 10:  # Log if memory increased by more than 10MB
                    logger.info(f"Function {func.__name__} memory usage: +{memory_delta:.1f}MB")

                return result
            except Exception as e:
                raise

        return wrapper
    return decorator
