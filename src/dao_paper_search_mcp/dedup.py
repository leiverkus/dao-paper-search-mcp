"""Dubletten-Erkennung — Key-Funktion.

Briefing-Referenz: ``2026-05-18-...-session5-...`` §III.C.

Session 5 legt nur den **Hook**: eine reine Funktion, die einem
``DAOPaper`` einen stabilen Dedupe-Schlüssel auf Basis der normalisierten
DOI zuordnet. Die eigentliche Merge-Logik (welcher Adapter „gewinnt"
pro Feld) ist explizit für Session 6 vorgesehen — siehe
Briefing §VI.

Konsumenten existieren noch nicht; die Funktion wird in Session 6
verkabelt. Bis dahin schützt sie ausschließlich die Test-Invariante:
identische DOIs in unterschiedlicher Schreibweise → identischer Key.
"""

from __future__ import annotations

from .models import DAOPaper


def dedupe_key(paper: DAOPaper) -> str | None:
    """Stabiler Dedupe-Schlüssel auf Basis der normalisierten DOI.

    Gibt ``None`` zurück, wenn der Datensatz keine DOI führt — in diesem
    Fall ist der Caller dafür verantwortlich, eine alternative Strategie
    zu wählen (z. B. Titel/Autoren-Fingerprint) oder den Datensatz als
    „nicht dedupe-bar" zu behandeln.
    """
    if paper.identifiers is None:
        return None
    doi = paper.identifiers.doi
    if not doi:
        return None
    # ``identifiers.doi`` ist bereits durch ``normalize_doi`` gegangen;
    # wir lowercasen defensiv erneut, falls Adapter-Pfade aussen
    # vorbeigeführt werden.
    return doi.lower()
