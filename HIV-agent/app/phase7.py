"""Phase 7 status.

Phase 7 is intentionally a no-op in this codebase.

The refined plan deleted Phase 7, and the current runtime direction is explicit:
do not lock production to Mistral. Groq and Puter are supported through the
OpenAI-compatible provider path in providers.py/api.py. Reintroducing a
Mistral-only pydantic-ai phase would be architectural regression.
"""

PHASE7_STATUS = {
    "status": "deleted",
    "reason": "Provider lock-in was rejected; Groq/Puter runtime is active.",
}
