"""
Model registry for managing different LLM providers
"""
from typing import Dict, Optional
from api.services.llms.openai_model import OpenAIModel
from api.services.llms.llama_model import LlamaModel
from api.services.llms.med42_model import Med42Model
from api.services.llms.falcon_model import FalconModel
import logging

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Registry for LLM models"""
    
    def __init__(self):
        self.models: Dict[str, any] = {}
        self._initialize_models()
    
    def _initialize_models(self):
        """Initialize available models"""
        try:
            self.models["gpt-4"] = OpenAIModel("gpt-4")
            self.models["gpt-4-turbo"] = OpenAIModel("gpt-4-turbo-preview")
            self.models["gpt-3.5-turbo"] = OpenAIModel("gpt-3.5-turbo")
        except Exception as e:
            logger.warning(f"Could not initialize OpenAI models: {e}")
        
        try:
            self.models["llama-3"] = LlamaModel("llama-3")
            self.models["llama-3.1"] = LlamaModel("llama-3.1")
        except Exception as e:
            logger.warning(f"Could not initialize Llama models: {e}")
        
        try:
            self.models["med42"] = Med42Model()
        except Exception as e:
            logger.warning(f"Could not initialize Med42 model: {e}")
        
        try:
            self.models["falcon"] = FalconModel()
        except Exception as e:
            logger.warning(f"Could not initialize Falcon model: {e}")
    
    def get_model(self, model_name: str):
        """Get model by name"""
        if model_name not in self.models:
            raise ValueError(f"Model {model_name} not found. Available: {list(self.models.keys())}")
        return self.models[model_name]
    
    def list_models(self) -> list:
        """List all available models"""
        return list(self.models.keys())
    
    def register_model(self, name: str, model_instance):
        """Register a custom model"""
        self.models[name] = model_instance

