#!/usr/bin/env python3
"""
MacBot Health Monitor - Service health tracking and recovery
"""
import os
import sys
import time
import threading
import requests
import psutil
import logging
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta
from enum import Enum

from .utils import setup_path
setup_path()
from . import config as CFG
from .logging_utils import setup_logger

# Unified logging
logger = setup_logger("macbot.health_monitor", "logs/health_monitor.log")

class ServiceStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

class HealthCheck:
    """Individual health check for a service"""

    def __init__(self, name: str, check_func: Callable, interval: int = 30, timeout: int = 10):
        self.name = name
        self.check_func = check_func
        self.interval = interval
        self.timeout = timeout
        self.last_check = None
        self.last_status = ServiceStatus.UNKNOWN
        self.last_error = None
        self.consecutive_failures = 0
        self.total_checks = 0
        self.successful_checks = 0

    def run_check(self) -> ServiceStatus:
        """Run the health check"""
        self.total_checks += 1
        try:
            start_time = time.time()
            result = self.check_func()
            duration = time.time() - start_time

            if duration > self.timeout:
                self.last_status = ServiceStatus.DEGRADED
                self.last_error = f"Check timed out after {duration:.2f}s"
                self.consecutive_failures += 1
            elif result:
                self.last_status = ServiceStatus.HEALTHY
                self.successful_checks += 1
                self.consecutive_failures = 0
                self.last_error = None
            else:
                self.last_status = ServiceStatus.UNHEALTHY
                self.last_error = "Check returned False"
                self.consecutive_failures += 1

        except Exception as e:
            self.last_status = ServiceStatus.UNHEALTHY
            self.last_error = str(e)
            self.consecutive_failures += 1
            logger.error(f"Health check '{self.name}' failed: {e}")

        self.last_check = datetime.now()
        return self.last_status

    def get_health_info(self) -> Dict:
        """Get detailed health information"""
        return {
            'name': self.name,
            'status': self.last_status.value,
            'last_check': self.last_check.isoformat() if self.last_check else None,
            'last_error': self.last_error,
            'consecutive_failures': self.consecutive_failures,
            'success_rate': (self.successful_checks / self.total_checks) * 100 if self.total_checks > 0 else 0,
            'interval': self.interval
        }

class CircuitBreaker:
    """Circuit breaker pattern implementation"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def call(self, func: Callable, *args, **kwargs):
        """Execute function with circuit breaker protection"""
        if self.state == "OPEN":
            if self._should_attempt_reset():
                self.state = "HALF_OPEN"
            else:
                raise Exception("Circuit breaker is OPEN")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e

    def _should_attempt_reset(self) -> bool:
        """Check if we should attempt to reset the circuit"""
        if self.last_failure_time is None:
            return True
        time_since_failure = datetime.now() - self.last_failure_time
        return time_since_failure.total_seconds() >= self.recovery_timeout

    def _on_success(self):
        """Handle successful call"""
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"
            self.failure_count = 0
            logger.info("Circuit breaker reset to CLOSED state")

    def _on_failure(self):
        """Handle failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            logger.warning(f"Circuit breaker opened after {self.failure_count} failures")

    def get_status(self) -> Dict:
        """Get circuit breaker status"""
        return {
            'state': self.state,
            'failure_count': self.failure_count,
            'last_failure': self.last_failure_time.isoformat() if self.last_failure_time else None
        }

class HealthMonitor:
    """Centralized health monitoring system"""

    def __init__(self):
        self.health_checks: Dict[str, HealthCheck] = {}
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.monitoring_thread = None
        self.running = False
        self.alert_callbacks: List[Callable] = []

        # Initialize default health checks
        self._setup_default_checks()

    def _setup_default_checks(self):
        """Setup default health checks for core services"""

        # LLM Server health check
        def check_llm_server():
            try:
                llm_url = CFG.get_llm_server_url()
                response = requests.get(f"{llm_url.replace('/v1/chat/completions', '/health')}", timeout=5)
                return response.status_code == 200
            except:
                return False

        # RAG Server health check
        def check_rag_server():
            try:
                rag_url = CFG.get_rag_base_url()
                response = requests.get(f"{rag_url}/health", timeout=5)
                return response.status_code == 200
            except:
                return False

        # Web Dashboard health check
        def check_web_dashboard():
            try:
                host, port = CFG.get_web_dashboard_host_port()
                response = requests.get(f"http://{host}:{port}/health", timeout=5)
                return response.status_code == 200
            except:
                return False

        # System resources check
        def check_system_resources():
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')

            # Consider unhealthy if resources are critically low
            return not (cpu_percent > 95 or memory.percent > 95 or disk.percent > 95)

        # Add health checks
        self.add_health_check("llm_server", check_llm_server, interval=30)
        self.add_health_check("rag_server", check_rag_server, interval=30)
        self.add_health_check("web_dashboard", check_web_dashboard, interval=30)
        self.add_health_check("system_resources", check_system_resources, interval=60)

        # Add circuit breakers for critical services
        self.add_circuit_breaker("llm_server", failure_threshold=3, recovery_timeout=120)
        self.add_circuit_breaker("rag_server", failure_threshold=3, recovery_timeout=120)

    def add_health_check(self, name: str, check_func: Callable, interval: int = 30, timeout: int = 10):
        """Add a health check"""
        self.health_checks[name] = HealthCheck(name, check_func, interval, timeout)
        logger.info(f"Added health check: {name}")

    def add_circuit_breaker(self, name: str, failure_threshold: int = 5, recovery_timeout: int = 60):
        """Add a circuit breaker"""
        self.circuit_breakers[name] = CircuitBreaker(failure_threshold, recovery_timeout)
        logger.info(f"Added circuit breaker: {name}")

    def add_alert_callback(self, callback: Callable):
        """Add callback for health alerts"""
        self.alert_callbacks.append(callback)

    def start_monitoring(self):
        """Start the health monitoring system"""
        if self.running:
            return

        self.running = True
        self.monitoring_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitoring_thread.start()
        logger.info("Health monitoring started")

    def stop_monitoring(self):
        """Stop the health monitoring system"""
        self.running = False
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=5)
        logger.info("Health monitoring stopped")

    def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                for name, check in self.health_checks.items():
                    # Check if it's time to run this health check
                    if (check.last_check is None or
                        (datetime.now() - check.last_check).seconds >= check.interval):

                        old_status = check.last_status
                        new_status = check.run_check()

                        # Alert on status changes
                        if old_status != new_status and old_status != ServiceStatus.UNKNOWN:
                            self._trigger_alert(name, old_status, new_status, check.last_error)

                time.sleep(5)  # Check every 5 seconds for new checks to run

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(10)

    def _trigger_alert(self, service_name: str, old_status: ServiceStatus, new_status: ServiceStatus, error: Optional[str] = None):
        """Trigger health alerts"""
        alert_data = {
            'service': service_name,
            'old_status': old_status.value,
            'new_status': new_status.value,
            'timestamp': datetime.now().isoformat(),
            'error': error
        }

        logger.warning(f"Health alert: {service_name} changed from {old_status.value} to {new_status.value}")

        # Call all alert callbacks
        for callback in self.alert_callbacks:
            try:
                callback(alert_data)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")

    def get_health_status(self) -> Dict:
        """Get overall health status"""
        services = {}
        for name, check in self.health_checks.items():
            services[name] = check.get_health_info()

        circuit_breakers = {}
        for name, cb in self.circuit_breakers.items():
            circuit_breakers[name] = cb.get_status()

        # Calculate overall system health
        unhealthy_count = sum(1 for s in services.values() if s['status'] in ['unhealthy', 'unknown'])
        degraded_count = sum(1 for s in services.values() if s['status'] == 'degraded')

        if unhealthy_count > 0:
            overall_status = "unhealthy"
        elif degraded_count > 0:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        return {
            'overall_status': overall_status,
            'services': services,
            'circuit_breakers': circuit_breakers,
            'timestamp': datetime.now().isoformat()
        }

    def is_service_healthy(self, service_name: str) -> bool:
        """Check if a specific service is healthy"""
        if service_name in self.health_checks:
            return self.health_checks[service_name].last_status == ServiceStatus.HEALTHY
        return False

    def execute_with_circuit_breaker(self, service_name: str, func: Callable, *args, **kwargs):
        """Execute a function with circuit breaker protection"""
        if service_name in self.circuit_breakers:
            return self.circuit_breakers[service_name].call(func, *args, **kwargs)
        else:
            return func(*args, **kwargs)

# Global health monitor instance
health_monitor = HealthMonitor()

def get_health_monitor() -> HealthMonitor:
    """Get the global health monitor instance"""
    return health_monitor
