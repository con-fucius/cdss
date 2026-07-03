"""Extraction pipeline to orchestrate resource-aware fallback logic."""
import os
from pathlib import Path
from typing import List
from .base import BaseExtractor, ExtractedDocument
from .pymupdf_ext import PyMuPDFExtractor
from .pdfplumber_ext import PDFPlumberExtractor
from .pypdf_ext import PyPDFExtractor
import logging

logger = logging.getLogger(__name__)

class ExtractionPipeline:
    def __init__(self, quality_threshold: float = 0.5):
        self.quality_threshold = quality_threshold

    def extract(self, pdf_path: str, disease: str) -> ExtractedDocument:
        """Try extractors in order until one passes the quality threshold."""
        last_result = None
        for extractor in self._extractors_for(pdf_path):
            try:
                result = extractor.extract(pdf_path)
                if result.quality_score >= self.quality_threshold:
                    logger.info(f"Extractor {result.extractor_name} succeeded for {disease} with score {result.quality_score}")
                    return result
                last_result = result
            except Exception as e:
                logger.error(f"Extractor {extractor.__class__.__name__} failed: {e}")
                continue
        
        if last_result:
            logger.warning(f"No extractor passed threshold. Using {last_result.extractor_name} with score {last_result.quality_score}")
            return last_result
            
        raise RuntimeError(f"All extractors failed for {pdf_path}")

    def _extractors_for(self, pdf_path: str) -> List[BaseExtractor]:
        extractors: List[BaseExtractor] = []
        if self._should_try_docling(pdf_path):
            try:
                from .docling_ext import DoclingExtractor

                extractors.append(DoclingExtractor())
            except Exception as exc:
                logger.warning("Docling unavailable; using fallback extractors: %s", exc)
        extractors.extend([PyMuPDFExtractor(), PDFPlumberExtractor(), PyPDFExtractor()])
        return extractors

    def _should_try_docling(self, pdf_path: str) -> bool:
        """
        Use Docling when explicitly enabled or when an auto resource gate passes.

        CDSS_DOCLING_MODE:
          - auto: default; skip Docling for large PDFs likely to OOM locally
          - enabled: always try Docling first
          - disabled: never try Docling

        CDSS_SKIP_DOCLING=1 is retained as a deprecated hard-disable alias.
        """
        if os.getenv("CDSS_SKIP_DOCLING", "").strip() == "1":
            logger.info("CDSS_SKIP_DOCLING=1 — Docling skipped")
            return False

        mode = os.getenv("CDSS_DOCLING_MODE", "auto").strip().lower()
        if mode in {"disabled", "off", "false", "0"}:
            logger.info("CDSS_DOCLING_MODE=%s — Docling skipped", mode)
            return False
        if mode in {"enabled", "on", "true", "1"}:
            return True
        if mode != "auto":
            logger.warning("Unsupported CDSS_DOCLING_MODE=%s; using auto", mode)

        path = Path(pdf_path)
        max_mb = float(os.getenv("CDSS_DOCLING_MAX_FILE_MB", "25"))
        max_pages = int(os.getenv("CDSS_DOCLING_MAX_PAGES", "120"))

        try:
            file_mb = path.stat().st_size / (1024 * 1024)
            if file_mb > max_mb:
                logger.info(
                    "Docling auto-skip for %s: %.1fMB exceeds CDSS_DOCLING_MAX_FILE_MB=%s",
                    path.name,
                    file_mb,
                    max_mb,
                )
                return False
        except OSError:
            pass

        try:
            from pypdf import PdfReader

            page_count = len(PdfReader(str(path)).pages)
            if page_count > max_pages:
                logger.info(
                    "Docling auto-skip for %s: %s pages exceeds CDSS_DOCLING_MAX_PAGES=%s",
                    path.name,
                    page_count,
                    max_pages,
                )
                return False
        except Exception as exc:
            logger.info("Docling auto page-count check unavailable: %s", exc)

        return True
