"""
Chaos Configuration Module

Provides centralized configuration for chaos injection with
environment variable support and sensible defaults.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ChaosConfig:
    """
    Chaos injection configuration.
    
    All settings are read from environment variables with sensible defaults.
    Chaos is DISABLED by default for safety.
    """
    
    # Master switch - must be explicitly enabled
    enabled: bool = field(default_factory=lambda: os.getenv("CHAOS_MODE", "").lower() in ("true", "1", "yes"))
    
    # Individual chaos scenario switches
    payment_confirm_delay_enabled: bool = field(
        default_factory=lambda: os.getenv("CHAOS_PAYMENT_CONFIRM_DELAY", "").lower() in ("true", "1", "yes")
    )
    partial_failure_enabled: bool = field(
        default_factory=lambda: os.getenv("CHAOS_PARTIAL_FAILURE", "").lower() in ("true", "1", "yes")
    )
    race_amplification_enabled: bool = field(
        default_factory=lambda: os.getenv("CHAOS_RACE_AMPLIFICATION", "").lower() in ("true", "1", "yes")
    )
    async_task_failure_enabled: bool = field(
        default_factory=lambda: os.getenv("CHAOS_ASYNC_TASK_FAILURE", "").lower() in ("true", "1", "yes")
    )
    
    # Timing configurations (milliseconds)
    payment_confirm_delay_ms: int = field(
        default_factory=lambda: int(os.getenv("CHAOS_PAYMENT_CONFIRM_DELAY_MS", "2000"))
    )
    race_delay_ms: int = field(
        default_factory=lambda: int(os.getenv("CHAOS_RACE_DELAY_MS", "500"))
    )
    
    # Probability configurations (0.0 to 1.0)
    partial_failure_probability: float = field(
        default_factory=lambda: float(os.getenv("CHAOS_PARTIAL_FAILURE_PROB", "0.3"))
    )
    async_task_failure_probability: float = field(
        default_factory=lambda: float(os.getenv("CHAOS_ASYNC_TASK_FAILURE_PROB", "0.2"))
    )
    race_trigger_probability: float = field(
        default_factory=lambda: float(os.getenv("CHAOS_RACE_TRIGGER_PROB", "0.4"))
    )
    
    # Affected endpoints (comma-separated list)
    affected_endpoints: list = field(
        default_factory=lambda: os.getenv("CHAOS_AFFECTED_ENDPOINTS", "confirm,cancel").split(",")
    )
    
    # Request counter for deterministic chaos (testing)
    _request_counter: int = field(default=0, repr=False)
    
    def is_chaos_active(self, scenario: str = None) -> bool:
        """
        Check if chaos is active for a specific scenario.
        
        Args:
            scenario: Optional specific scenario name
            
        Returns:
            True if chaos should be triggered
        """
        # Master switch must be on
        if not self.enabled:
            return False
            
        # If no specific scenario, just check master switch
        if scenario is None:
            return True
            
        # Check specific scenario
        scenario_map = {
            "payment_confirm_delay": self.payment_confirm_delay_enabled,
            "partial_failure": self.partial_failure_enabled,
            "race_amplification": self.race_amplification_enabled,
            "async_task_failure": self.async_task_failure_enabled,
        }
        
        # If CHAOS_MODE is true but no individual flags set, enable all
        if self.enabled and not any([
            self.payment_confirm_delay_enabled,
            self.partial_failure_enabled,
            self.race_amplification_enabled,
            self.async_task_failure_enabled,
        ]):
            return True
            
        return scenario_map.get(scenario, False)
    
    def should_trigger(self, probability: float = None) -> bool:
        """
        Determine if chaos should trigger based on probability.
        
        For reproducibility in tests, uses a deterministic counter
        when CHAOS_DETERMINISTIC=true.
        
        Args:
            probability: Override probability (0.0 to 1.0)
            
        Returns:
            True if chaos should be triggered
        """
        import random
        
        if probability is None:
            probability = 1.0  # Default to always trigger if enabled
            
        # Deterministic mode for testing
        if os.getenv("CHAOS_DETERMINISTIC", "").lower() in ("true", "1", "yes"):
            self._request_counter += 1
            trigger_every_n = int(1 / probability) if probability > 0 else 0
            if trigger_every_n > 0:
                return self._request_counter % trigger_every_n == 0
            return False
            
        return random.random() < probability
    
    def get_delay_seconds(self, scenario: str) -> float:
        """
        Get delay in seconds for a scenario.
        
        Args:
            scenario: Scenario name
            
        Returns:
            Delay in seconds
        """
        delay_map = {
            "payment_confirm_delay": self.payment_confirm_delay_ms,
            "race_amplification": self.race_delay_ms,
        }
        return delay_map.get(scenario, 1000) / 1000.0
    
    def log_chaos_event(
        self,
        breakpoint_name: str,
        action: str,
        context: dict = None,
        level: str = "warning"
    ):
        """
        Log a chaos event with standardized format.
        
        Args:
            breakpoint_name: Name of the chaos breakpoint
            action: Action being taken (delay, fail, etc.)
            context: Additional context information
            level: Log level (debug, info, warning, error)
        """
        log_data = {
            "chaos_breakpoint": breakpoint_name,
            "chaos_action": action,
            "chaos_enabled": self.enabled,
        }
        if context:
            log_data.update(context)
            
        message = f"[CHAOS] {breakpoint_name}: {action}"
        if context:
            context_str = ", ".join(f"{k}={v}" for k, v in context.items())
            message = f"{message} | {context_str}"
            
        log_func = getattr(logger, level, logger.warning)
        log_func(message, extra={"chaos_data": log_data})
        
    def reset_counter(self):
        """Reset the request counter (for testing)."""
        self._request_counter = 0
        
    def __str__(self) -> str:
        return (
            f"ChaosConfig(enabled={self.enabled}, "
            f"payment_confirm_delay={self.payment_confirm_delay_enabled}, "
            f"partial_failure={self.partial_failure_enabled}, "
            f"race_amplification={self.race_amplification_enabled}, "
            f"async_task_failure={self.async_task_failure_enabled})"
        )


# Singleton instance
chaos_config = ChaosConfig()


def reload_config():
    """Reload chaos configuration from environment variables."""
    global chaos_config
    chaos_config = ChaosConfig()
    logger.info(f"[CHAOS] Configuration reloaded: {chaos_config}")
    return chaos_config
