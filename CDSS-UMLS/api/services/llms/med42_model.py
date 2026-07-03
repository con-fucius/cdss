"""
Med42 clinical LLM integration
"""
from typing import Dict, List, Optional
from api.config import settings
import time
import logging

logger = logging.getLogger(__name__)


class Med42Model:
    """Med42 clinical LLM wrapper"""
    
    def __init__(self):
        self.api_key = settings.MED42_API_KEY
        # TODO: Initialize Med42 API client
    
    async def generate(
        self,
        prompt: str,
        context: Optional[List[Dict[str, str]]] = None,
        patient_history: Optional[str] = None,
        max_tokens: Optional[int] = 1000,
        temperature: float = 0.7,
        **kwargs
    ) -> Dict[str, any]:
        """Generate response using Med42 clinical LLM"""
        start_time = time.time()
        
        # Check if no evidence exists
        if not context or len(context) == 0:
            processing_time = time.time() - start_time
            return {
                "text": "Insufficient knowledge in UMLS database.",
                "confidence": 0.0,
                "processing_time": processing_time,
                "model": "med42"
            }
        
        # Build prompt
        full_prompt = self._build_clinical_prompt(prompt, context, patient_history)
        
        try:
            # TODO: Implement Med42 API call
            response_text = f"[Med42 clinical response for: {prompt}]"
            
            processing_time = time.time() - start_time
            
            return {
                "text": response_text,
                "confidence": 0.85,  # Higher confidence for clinical models
                "processing_time": processing_time,
                "model": "med42"
            }
        except Exception as e:
            logger.error(f"Med42 API error: {e}")
            raise
    
    def _build_clinical_prompt(
        self,
        prompt: str,
        context: Optional[List[Dict[str, str]]],
        patient_history: Optional[str]
    ) -> str:
        """Build clinical-focused prompt"""
        parts = ["Clinical Decision Support Request"]
        
        if patient_history:
            parts.append(f"Patient History: {patient_history}")
        
        # Add strict evidence-only instructions
        if context:
            context_text = "\n\n".join([doc.get("text", "") for doc in context])
            parts.append(f"""Use ONLY the following medical evidence to generate your response:

{context_text}

DO NOT cite external sources.""")
        
        parts.append(f"Current Presentation: {prompt}")
        
        return "\n\n".join(parts)

