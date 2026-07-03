"""
Semantic text splitter for intelligent chunking.

Phase 0 fix:
- Uses tiktoken.get_encoding("cl100k_base") directly instead of
  tiktoken.encoding_for_model("gpt-3.5-turbo"), which requires a network
  round-trip to resolve the model→encoding mapping on first use.
  cl100k_base IS the encoding for gpt-3.5-turbo; this is identical behaviour
  with no model-lookup call and no network dependency.
"""

from __future__ import annotations

from typing import List

import tiktoken
from semantic_text_splitter import TextSplitter

# Use encoding directly — no model name lookup, no network call
_ENCODING_NAME = "cl100k_base"


class SemanticChunker:
    def __init__(self, chunk_capacity: int = 400) -> None:
        self.tokenizer = tiktoken.get_encoding(_ENCODING_NAME)
        self.splitter = TextSplitter.from_tiktoken_model(
            "gpt-3.5-turbo", chunk_capacity
        )

    def chunk(self, text: str) -> List[str]:
        if not text or not text.strip():
            return []
        return self.splitter.chunks(text)

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text))
