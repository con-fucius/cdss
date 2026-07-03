"""
app/terminology/__init__.py

Bounded terminology subsystem for CDSS.

This package provides UMLS-backed concept normalisation, alias expansion,
and chunk annotation as a self-contained module.  It exposes one stable
public interface — TerminologyService — and nothing else to the rest of
the application.

HARD BOUNDARY: nothing in this package may be imported by search_tools.py,
search_agent.py, or api.py until a retrieval evaluation harness exists and
a measurable improvement is confirmed.  The boundary is enforced by the
absence of any import of this package from those modules.

Future wiring point (do not activate yet):
    from app.terminology import TerminologyService
"""

from .service import TerminologyService

__all__ = ["TerminologyService"]
