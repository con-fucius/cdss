"""Evidence graph seeding and retrieval services."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

APP_DIR = Path(__file__).resolve().parent


def normalise_graph_seed(seed: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    nodes = seed.get("nodes") or []
    edges = seed.get("edges") or []
    node_refs = {node["ref_id"] for node in nodes if node.get("ref_id")}
    clean_edges = [
        edge for edge in edges
        if edge.get("source_ref") in node_refs and edge.get("target_ref") in node_refs
    ]
    return {"nodes": nodes, "edges": clean_edges}


def load_seed(disease: str) -> Dict[str, List[Dict[str, Any]]]:
    path = APP_DIR / "data" / "concepts" / f"{disease}.json"
    with path.open("r", encoding="utf-8") as handle:
        return normalise_graph_seed(json.load(handle))


def score_graph_hit(query: str, node: Dict[str, Any], edge: Dict[str, Any], target: Dict[str, Any]) -> float:
    tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    haystack = " ".join(
        [
            node.get("label", ""),
            node.get("node_type", ""),
            edge.get("relation_type", ""),
            target.get("label", ""),
            json.dumps(node.get("payload", {})),
            json.dumps(target.get("payload", {})),
        ]
    ).lower()
    if not tokens:
        return 0.0
    overlap = sum(1 for token in tokens if token in haystack)
    return overlap / len(tokens)


async def seed_evidence_graph(disease: str, clinician_id: str) -> Dict[str, int]:
    seed = load_seed(disease)
    from .repositories import upsert_evidence_graph

    return await upsert_evidence_graph(
        disease=disease,
        nodes=seed["nodes"],
        edges=seed["edges"],
        clinician_id=clinician_id,
    )


async def query_evidence_graph(disease: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    from .repositories import query_evidence_graph_db

    return await query_evidence_graph_db(disease=disease, query=query, top_k=top_k)


def format_evidence_triples(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return graph hits as structured triples for API events and UI panels."""
    triples = []
    for item in results:
        source = item["source_node"]
        edge = item["edge"]
        target = item["target_node"]
        triples.append({
            "source": source.get("label", ""),
            "relation": edge.get("relation_type", ""),
            "target": target.get("label", ""),
            "weight": edge.get("weight"),
            "source_ref": edge.get("source_ref", ""),
        })
    return triples


def format_evidence_context(results: List[Dict[str, Any]]) -> str:
    """Format graph triples as compact context for the clinical answer."""
    lines = []
    for idx, triple in enumerate(format_evidence_triples(results), start=1):
        lines.append(
            f"[G{idx}] {triple['source']} --{triple['relation']}--> "
            f"{triple['target']} (weight {triple['weight']}, source {triple['source_ref']})"
        )
    return "\n".join(lines)
