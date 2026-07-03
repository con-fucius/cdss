"""app/terminology/embed.py.

Generate and upload UMLS concept embeddings to Qdrant.

Adapted from CDSS-UMLS/etl/generate_embeddings_qdrant.py with the
following corrections and changes:
- Removed hard dependency on api.config from the UMLS repo.
  Settings are read from environment variables directly.
- Uses fastembed (already a CDSS dependency) instead of sentence-transformers
  so no new Python dependency is introduced.
- Embedding model matches the CDSS embedding model (BAAI/bge-base-en-v1.5)
  to ensure query vectors are comparable at search time.
- Reads embeddable_text.jsonl produced by combine_umls.py (unchanged format).
- CUI → Qdrant point-id mapping stored in Postgres (terminology_concepts.qdrant_id)
  so the CDSS can resolve CUIs to Qdrant IDs without a separate mapping file.
- Upsert-safe: re-running appends new concepts and updates existing ones.

Usage
-----
uv run python -m app.terminology.embed \
    --input /path/to/embeddable_text.jsonl \
    [--collection umls_concepts]  \
    [--batch-size 128]            \
    [--qdrant-url http://localhost:6333]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

_DEFAULT_COLLECTION = "umls_concepts"
_DEFAULT_QDRANT_URL = "http://localhost:6333"
_DEFAULT_BATCH = 128
_UPLOAD_BATCH = 1000


def _qdrant_url() -> str:
    return os.getenv("CDSS_QDRANT_URL", _DEFAULT_QDRANT_URL)


def _collection_name() -> str:
    return os.getenv("CDSS_QDRANT_COLLECTION", _DEFAULT_COLLECTION)


def _iter_embeddable(path: Path, limit: int | None) -> Iterator[dict[str, Any]]:
    count = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
            if limit and count >= limit:
                break


def _ensure_collection(client: Any, collection: str, vector_size: int) -> None:
    from qdrant_client.models import Distance, VectorParams

    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info("Created Qdrant collection '%s' (dim=%d)", collection, vector_size)
    else:
        logger.info("Qdrant collection '%s' already exists", collection)


async def _update_qdrant_ids(cui_id_pairs: list[tuple[str, int]]) -> None:
    """Write CUI → Qdrant integer ID back to terminology_concepts.qdrant_id."""
    from sqlalchemy import update

    from ..db import get_session
    from .models import TerminologyConcept

    async with get_session() as session:
        for cui, qdrant_id in cui_id_pairs:
            await session.execute(
                update(TerminologyConcept)
                .where(TerminologyConcept.cui == cui)
                .values(qdrant_id=qdrant_id)
            )
        await session.commit()


async def embed_and_upload(
    input_path: Path,
    collection: str | None = None,
    qdrant_url: str | None = None,
    batch_size: int = _DEFAULT_BATCH,
    limit: int | None = None,
) -> dict[str, int]:
    """Embed concepts and upload to Qdrant.

    Returns {processed, uploaded, skipped, errors}.
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct
    except ImportError as exc:
        raise ImportError("qdrant-client is not installed. Run: uv add qdrant-client") from exc

    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise ImportError("fastembed is not installed. Run: uv add fastembed") from exc

    col = collection or _collection_name()
    url = qdrant_url or _qdrant_url()

    if not input_path.exists():
        raise FileNotFoundError(f"embeddable_text.jsonl not found: {input_path}")

    client = QdrantClient(url=url)
    logger.info("Connected to Qdrant at %s", url)

    # Determine vector dimension from model
    from ..config import get_embedding_model_name

    model_name = get_embedding_model_name()
    model = TextEmbedding(model_name=model_name)
    probe = list(model.embed(["probe"]))[0]
    vector_size = len(probe)
    logger.info("Embedding model: %s (dim=%d)", model_name, vector_size)

    _ensure_collection(client, col, vector_size)

    # Get starting point ID from existing collection
    info = client.get_collection(col)
    next_id: int = int(info.points_count)
    logger.info("Resuming from Qdrant point id %d", next_id)

    # Load existing CUI→ID mapping to enable upsert
    cui_to_id: dict[str, int] = {}
    if next_id > 0:
        logger.info("Loading existing CUI→ID mappings from Qdrant ...")
        offset = None
        while True:
            points, next_offset = client.scroll(
                collection_name=col,
                limit=10000,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                if p.payload and "cui" in p.payload:
                    cui_to_id[p.payload["cui"]] = p.id
            if next_offset is None:
                break
            offset = next_offset
        logger.info("  Loaded %d existing mappings", len(cui_to_id))

    processed = skipped = errors = 0
    upload_buffer: list[Any] = []  # PointStruct
    pg_update_buffer: list[tuple[str, int]] = []

    text_batch: list[str] = []
    data_batch: list[dict[str, Any]] = []

    def _flush_embed_batch() -> None:
        nonlocal processed
        if not text_batch:
            return
        vecs = list(model.embed(text_batch))
        for rec, vec in zip(data_batch, vecs, strict=False):
            cui = rec["cui"]
            if cui not in cui_to_id:
                cui_to_id[cui] = _next_id_ref[0]
                _next_id_ref[0] += 1
            point_id = cui_to_id[cui]
            upload_buffer.append(
                PointStruct(
                    id=point_id,
                    vector=vec.tolist(),
                    payload={
                        "cui": cui,
                        "preferred_name": rec.get("preferred_name", ""),
                        "semantic_types": (rec.get("semantic_types") or [])[:10],
                        "synonyms": (rec.get("synonyms") or [])[:10],
                        "codes": (rec.get("codes") or [])[:10],
                    },
                )
            )
            pg_update_buffer.append((cui, point_id))
        text_batch.clear()
        data_batch.clear()
        processed += len(vecs)

    _next_id_ref = [next_id]

    async def _flush_upload() -> int:
        nonlocal errors
        if not upload_buffer:
            return 0
        try:
            client.upsert(collection_name=col, points=list(upload_buffer))
            uploaded = len(upload_buffer)
        except Exception as exc:
            logger.error("Qdrant upsert error: %s", exc)
            errors += len(upload_buffer)
            uploaded = 0
        upload_buffer.clear()

        if pg_update_buffer:
            try:
                await _update_qdrant_ids(list(pg_update_buffer))
            except Exception as exc:
                logger.warning("Postgres qdrant_id update failed: %s", exc)
            pg_update_buffer.clear()
        return uploaded

    uploaded = 0
    for rec in _iter_embeddable(input_path, limit):
        text = (rec.get("text") or "").strip()
        if not text:
            skipped += 1
            continue
        text_batch.append(text)
        data_batch.append(rec)

        if len(text_batch) >= batch_size:
            _flush_embed_batch()

        if len(upload_buffer) >= _UPLOAD_BATCH:
            uploaded += await _flush_upload()
            if processed % 10000 < batch_size:
                logger.info("  %d processed, %d uploaded", processed, uploaded)

    # Flush remainder
    _flush_embed_batch()
    uploaded += await _flush_upload()

    final_info = client.get_collection(col)
    logger.info("=" * 60)
    logger.info("Embedding upload complete")
    logger.info("  processed:  %d", processed)
    logger.info("  uploaded:   %d", uploaded)
    logger.info("  skipped:    %d", skipped)
    logger.info("  errors:     %d", errors)
    logger.info("  Qdrant total points: %d", final_info.points_count)
    logger.info("=" * 60)

    return {
        "processed": processed,
        "uploaded": uploaded,
        "skipped": skipped,
        "errors": errors,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Embed UMLS concepts and upload to Qdrant")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to embeddable_text.jsonl (output of combine_umls.py)",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Qdrant collection name (default: CDSS_QDRANT_COLLECTION env or 'umls_concepts')",
    )
    parser.add_argument(
        "--qdrant-url",
        default=None,
        help="Qdrant URL (default: CDSS_QDRANT_URL env or http://localhost:6333)",
    )
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH)
    parser.add_argument("--limit", type=int, default=None, help="Stop after N concepts")
    args = parser.parse_args()

    result = asyncio.run(
        embed_and_upload(
            input_path=Path(args.input),
            collection=args.collection,
            qdrant_url=args.qdrant_url,
            batch_size=args.batch_size,
            limit=args.limit,
        )
    )
    print(f"\nResult: {result}")


if __name__ == "__main__":
    main()
