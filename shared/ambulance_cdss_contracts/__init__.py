"""Shared Pydantic v2 contract schemas for the ambulance CDSS ecosystem.

These schemas define the HTTP API contracts between the three services:
- ambulance-cdss (incident record + dispatch engine)
- facility-mapper (geospatial facility routing)
- triage-ranker (NLP clinical enrichment)

No logic lives here — only schemas. Every field has a docstring
explaining its clinical meaning for developers without clinical background.
"""
