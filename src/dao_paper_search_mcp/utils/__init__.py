"""Shared helpers used across adapters and the inline-citation layer."""

from typing import TypeAlias

# Matches httpx's accepted query-parameter sequence shape. Adapters build
# params as a list of (key, str) tuples; the wider value type satisfies
# httpx's type stubs without forcing every adapter to widen its locals.
HttpxParams: TypeAlias = list[tuple[str, str | int | float | bool | None]]
