"""DOI normalisation.

Each aggregator hands back DOIs in a slightly different envelope:
OpenAlex emits the resolver URL (``https://doi.org/10.x/y``), Crossref
the bare DOI, IAA an OAI ``info:doi/...`` URI, Zenon either bare or as a
``urls`` entry. Adapters used to carry a per-module ``_strip_doi_prefix``;
this module centralises that to keep normalisation consistent and to
make case-insensitive dedupe keys reliable.

The normalised form is lower-case, prefix-free, and starts with ``10.``.
Any input that does not resolve to that form is treated as missing
(``None``).
"""

from __future__ import annotations

from typing import Optional

_PREFIXES: tuple[str, ...] = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
    "dx.doi.org/",
    "info:doi/",
    "doi:",
)


def normalize_doi(value: Optional[str]) -> Optional[str]:
    """Return the bare, lower-cased DOI, or ``None`` if absent/invalid.

    DOIs are case-insensitive per the DOI Handbook; lower-casing makes
    dedupe keys reliable. Whitespace, empty strings, and inputs that do
    not resolve to a ``10.<registrant>/<suffix>`` form return ``None``.
    """
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    lowered = s.lower()
    for prefix in _PREFIXES:
        if lowered.startswith(prefix):
            s = s[len(prefix):]
            lowered = s.lower()
            break
    if not lowered.startswith("10."):
        return None
    return lowered
