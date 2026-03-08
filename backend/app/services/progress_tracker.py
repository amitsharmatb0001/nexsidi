"""Progress Tracker — Budget/time/stall-based termination.

Replaces ALL hardcoded iteration limits (MAX_FIX_RETEST_CYCLES,
MAX_FIX_ITERATIONS, MAX_CHALLENGE_RETRIES) with intelligent
progress-based termination.

Only three SAFETY CAPS exist:
  1. Budget — don't spend more than user's budget
  2. Time — don't run longer than time limit
  3. Total stall — stop if system is truly stuck (10 cycles, <5% fix rate)

Everything else: keep going until the job is done.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ProgressState:
    """Tracks progress across fix-retest cycles, challenge retries, and agent iterations.

    Usage:
        progress = ProgressState(budget_limit_usd=50.0, time_limit_seconds=7200.0)
        while progress.should_continue()[0]:
            errors_found, errors_fixed = run_cycle()
            progress.record_cycle(errors_found, errors_fixed)
    """

    # Cumulative error tracking
    total_errors_found: int = 0
    total_errors_fixed: int = 0
    fix_rate: float = 0.0
    last_fix_rate: float = 0.0
    stall_count: int = 0  # Consecutive cycles with no improvement

    # Budget tracking
    budget_spent_usd: float = 0.0
    budget_limit_usd: float = 50.0  # SAFETY CAP — configurable per project

    # Time tracking
    start_time: float = field(default_factory=time.monotonic)
    elapsed_seconds: float = 0.0
    time_limit_seconds: float = 7200.0  # SAFETY CAP — 2 hours default

    # Cycle tracking
    total_cycles: int = 0

    def should_continue(self) -> tuple[bool, str]:
        """Returns (should_continue, reason).

        Only stops for genuine safety reasons:
          - Budget exhausted
          - Time limit reached
          - System truly stuck (10+ cycles with <5% fix rate)
        """
        self.elapsed_seconds = time.monotonic() - self.start_time

        if self.budget_spent_usd >= self.budget_limit_usd:
            logger.warning(
                "progress_budget_exhausted",
                spent=round(self.budget_spent_usd, 2),
                limit=round(self.budget_limit_usd, 2),
                cycles=self.total_cycles,
            )
            return False, "budget_exhausted"

        if self.elapsed_seconds >= self.time_limit_seconds:
            logger.warning(
                "progress_time_limit",
                elapsed=round(self.elapsed_seconds, 1),
                limit=round(self.time_limit_seconds, 1),
                cycles=self.total_cycles,
            )
            return False, "time_limit"

        if self.stall_count >= 10 and self.fix_rate < 0.05:
            logger.warning(
                "progress_total_stall",
                stall_count=self.stall_count,
                fix_rate=round(self.fix_rate, 3),
                cycles=self.total_cycles,
                errors_remaining=self.total_errors_found - self.total_errors_fixed,
            )
            return False, "total_stall"

        return True, "continue"

    def record_cycle(self, errors_found: int, errors_fixed: int) -> None:
        """Record results of one fix-retest or challenge cycle."""
        self.total_cycles += 1
        self.total_errors_found += errors_found
        self.total_errors_fixed += errors_fixed

        new_rate = errors_fixed / max(errors_found, 1)

        # Stall detection: if fix rate hasn't improved, increment stall counter
        if abs(new_rate - self.last_fix_rate) < 0.01 and errors_fixed == 0:
            self.stall_count += 1
        else:
            self.stall_count = 0

        self.last_fix_rate = self.fix_rate
        self.fix_rate = new_rate

        logger.info(
            "progress_cycle_recorded",
            cycle=self.total_cycles,
            errors_found=errors_found,
            errors_fixed=errors_fixed,
            fix_rate=round(new_rate, 3),
            stall_count=self.stall_count,
            budget_spent=round(self.budget_spent_usd, 2),
            elapsed_s=round(time.monotonic() - self.start_time, 1),
        )

    def update_budget(self, spent_usd: float) -> None:
        """Update budget spent from cost tracker."""
        self.budget_spent_usd = spent_usd

    def get_summary(self) -> dict:
        """Return summary for logging/reporting."""
        return {
            "total_cycles": self.total_cycles,
            "total_errors_found": self.total_errors_found,
            "total_errors_fixed": self.total_errors_fixed,
            "fix_rate": round(self.fix_rate, 3),
            "stall_count": self.stall_count,
            "budget_spent_usd": round(self.budget_spent_usd, 2),
            "budget_limit_usd": round(self.budget_limit_usd, 2),
            "elapsed_seconds": round(time.monotonic() - self.start_time, 1),
            "time_limit_seconds": round(self.time_limit_seconds, 1),
        }
