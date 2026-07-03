"""
search_agent.py — Clinical decision support agent and tool registry.

Architecture decision (Phase 7 onwards):
The pydantic-ai Mistral agent path has been RETIRED from the live request
path.  api.py routes all queries through the Groq/Puter OpenAI-compatible
provider path, which calls search_tools.SearchIndex.search_guidelines()
directly and passes results as a numbered context block to the LLM.

This module now owns:
  1. The SearchDeps dataclass consumed by any future agent integration.
  2. build_agent() — retained for integration tests and future re-activation
     if a provider that supports pydantic-ai is added.  It is NOT called by
     api.py at runtime.
  3. build_system_prompt() — consumed by api.py to build the system message
     injected into every Groq/Puter request.
  4. The three tool definitions (search_guidelines, get_section, lookup_kb)
     kept here so the agent is ready to be re-wired without refactoring.

If you want to re-activate the pydantic-ai path for a new provider, add
a branch to api.py run_stream() that calls build_agent() and runs it.
Do not rebuild the tool definitions here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import DISEASE_CONFIG
from .search_tools import SearchIndex


@dataclass
class SearchDeps:
    index: SearchIndex
    session_id: str
    query_id: str
    available_diseases: List[str]


BASE_PROMPT = """
You are Kini, a clinical decision support assistant for Kenya National Clinical Guidelines.
Provide healthcare professionals with evidence-based guidance derived strictly from the
official guideline passages supplied to you in [GUIDELINE_CONTEXT].

Core rules:
1. Cite every clinical claim with [Guideline Name, Section Title, p.N].
2. If a query targets one disease, answer from that disease's guidelines only.
3. For comorbidity queries, synthesise across all relevant diseases supplied.
4. Do not introduce external clinical knowledge absent from the supplied context.
5. If retrieval is weak, incomplete, or conflicting, state this explicitly.
   Emit the appropriate HITL marker so the frontend can prompt the clinician.
6. For malaria content always note: the indexed guideline is the 3rd Edition
   (2010) and may not reflect current recommendations.
7. Dosing and drug answers use structured bullets.
   Diagnostic pathways and treatment algorithms use numbered steps.
8. Evidence graph triples supplied in [EVIDENCE_GRAPH] are pre-validated and
   take precedence over free-text retrieval for the specific relationship they
   describe. Cite them as [Graph: source_ref].
9. Structured KB dosing/regimen results supplied in [STRUCTURED_KB_RESULT] are
   authoritative for dosing and regimen questions. Cite them as
   [Structured KB: source_ref].
10. Drug interaction information supplied in [DRUG_INTERACTION_CHECK] is sourced
    from RxNorm/openFDA and checked at query time. Cite it distinctly as
    [Drug Interaction: source] and do not treat it as a guideline citation.

HITL markers — emit exactly as shown when the condition applies:
  [HITL:CLARIFICATION: <specific question to ask the clinician>]
  [HITL:MISSING_PARAMS: param1, param2, ...]
  [HITL:CONFLICT: <brief description of the conflicting guidance>]

{context_section}

Available knowledge bases:
{disease_context}
"""


def build_system_prompt(
    available_diseases: List[str],
    context_block: Optional[str] = None,
) -> str:
    lines: List[str] = []
    for disease in available_diseases:
        cfg = DISEASE_CONFIG.get(disease.lower(), {})
        name = cfg.get("guideline_name", "Official guidelines")
        warning = cfg.get("guideline_warning")
        line = f"- {disease.upper()}: {name}"
        if warning:
            line += f" [WARNING: {warning}]"
        lines.append(line)

    disease_context = (
        "\n".join(lines)
        if lines
        else "- No indexed guideline tables are currently available."
    )
    context_section = context_block or ""

    return BASE_PROMPT.format(
        disease_context=disease_context,
        context_section=context_section,
    )


def build_agent(
    available_diseases: List[str],
    context_block: Optional[str] = None,
):
    """
    Build the pydantic-ai agent.

    NOT called by the live request path (api.py uses Groq/Puter directly).
    Retained for integration tests, future provider additions, and CLI use.
    """
    from pydantic_ai import Agent, RunContext

    agent: Agent = Agent(
        "mistral:mistral-small-latest",
        deps_type=SearchDeps,
        system_prompt=build_system_prompt(available_diseases, context_block),
        name="clinical_cdss_agent",
        defer_model_check=True,
    )

    @agent.tool
    async def search_guidelines(
        ctx: RunContext[SearchDeps],
        query: str,
        disease: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search official Kenya clinical guidelines for relevant passages.

        Pass disease=None for comorbidity or cross-disease queries — all indexed
        tables are searched in parallel.  Use a specific disease key when the
        query clearly targets one condition.
        """
        use_hyde = bool(
            disease
            and DISEASE_CONFIG.get(disease.lower(), {}).get("use_hyde")
        )
        results = await ctx.deps.index.search_guidelines(
            query=query,
            disease=disease,
            session_id=ctx.deps.session_id,
            query_id=ctx.deps.query_id,
            use_hyde=use_hyde,
        )
        return [
            {
                "text": r.text,
                "parent_context": r.parent_text,
                "source": (
                    f"{r.guideline_name or r.disease.upper() + ' Guidelines'}, "
                    f"{r.section_title}, p.{r.page}"
                ),
                "disease": r.disease,
                "confidence": r.score,
                "low_confidence": r.low_confidence,
                "chunk_id": r.chunk_id,
                "parent_id": r.parent_id,
            }
            for r in results
        ]

    @agent.tool
    async def get_section(
        ctx: RunContext[SearchDeps],
        section_id: str,
        disease: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch the full text of a known guideline section by section ID.

        Use when search_guidelines returned a chunk and you need the complete
        parent section — e.g. the full dosing table a child chunk belongs to.
        """
        result = ctx.deps.index.get_section(section_id, disease)
        if not result:
            return None
        return {
            "text": result.parent_text or result.text,
            "title": result.section_title,
            "source": (
                f"{result.guideline_name or result.disease.upper() + ' Guidelines'}, "
                f"p.{result.page}"
            ),
            "disease": result.disease,
        }

    @agent.tool
    async def lookup_kb(
        ctx: RunContext[SearchDeps],
        query_type: str,
        disease: str,
        filters: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Exact lookup against validated structured KB tables.

        Use for regimen lookup, dosing criteria, diagnostic thresholds, and
        monitoring schedules where an exact structured answer exists.
        """
        result = await ctx.deps.index.lookup_kb(
            query_type=query_type,
            disease=disease,
            filters=filters,
            session_id=ctx.deps.session_id,
            query_id=ctx.deps.query_id,
        )
        if not result:
            return None
        return {
            "structured_data": result.data,
            "text": result.text,
            "source": result.source,
            "disease": result.disease,
            "confidence": result.confidence,
        }

    return agent


def init_agent(
    index: SearchIndex,
    available_diseases: Optional[List[str]] = None,
):
    """DEPRECATED: kept for Streamlit/CLI compatibility."""
    return build_agent(available_diseases or index.available_diseases())
