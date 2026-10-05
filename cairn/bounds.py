"""Shared semantic checks for values that bound work."""

from __future__ import annotations

from typing import Any, cast


def positive_integer(value: Any) -> bool:
    """Whether ``value`` is a strictly positive integer, excluding booleans."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def nonnegative_integer(value: Any) -> bool:
    """Whether ``value`` is a nonnegative integer, excluding booleans."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def retry_policy_is(
    value: Any, *, limit: int | None = None, interval_seconds: int | None = None
) -> bool:
    """Whether a retry policy is exact, integer-valued, and semantically bounded."""
    if not isinstance(value, dict):
        return False
    policy = cast(dict[str, Any], value)
    if set(policy) != {"limit", "interval_sec"}:
        return False
    found_limit: Any = policy.get("limit")
    found_interval: Any = policy.get("interval_sec")
    return (
        nonnegative_integer(found_limit)
        and positive_integer(found_interval)
        and (limit is None or found_limit == limit)
        and (interval_seconds is None or found_interval == interval_seconds)
    )


__all__ = [
    "nonnegative_integer",
    "positive_integer",
    "retry_policy_is",
]
