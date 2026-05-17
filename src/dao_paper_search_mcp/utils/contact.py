"""Contact identifier for API polite-pool headers.

Several upstream APIs (OpenAlex, Crossref, CORE, arXiv, Europe PMC)
encourage or require a contact e-mail in the User-Agent or as a query
parameter so they can reach out about unusual usage patterns. Set the
environment variable ``DAO_PAPER_SEARCH_CONTACT_EMAIL`` to supply your
own address; the default ``"anonymous"`` is accepted by all APIs but
may receive lower rate-limit priority from some (notably OpenAlex).
"""

from __future__ import annotations

import os

CONTACT_EMAIL: str = os.getenv("DAO_PAPER_SEARCH_CONTACT_EMAIL", "anonymous")
