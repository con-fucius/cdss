"""
Evaluation metrics for CDSS performance
"""
from typing import List, Dict
import numpy as np
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from rouge_score import rouge_scorer
import logging

logger = logging.getLogger(__name__)


class MetricsCalculator:
    """Calculate evaluation metrics"""
    
    def __init__(self):
        self.rouge_scorer = rouge_scorer.RougeScorer(
            ['rouge1', 'rouge2', 'rougeL'],
            use_stemmer=True
        )
        self.smoothing = SmoothingFunction()
    
    def calculate_bleu(
        self,
        reference: str,
        hypothesis: str
    ) -> float:
        """Calculate BLEU score"""
        try:
            ref_tokens = reference.split()
            hyp_tokens = hypothesis.split()
            
            score = sentence_bleu(
                [ref_tokens],
                hyp_tokens,
                smoothing_function=self.smoothing.method1
            )
            return float(score)
        except Exception as e:
            logger.error(f"BLEU calculation error: {e}")
            return 0.0
    
    def calculate_rouge(
        self,
        reference: str,
        hypothesis: str
    ) -> Dict[str, float]:
        """Calculate ROUGE scores"""
        try:
            scores = self.rouge_scorer.score(reference, hypothesis)
            return {
                "rouge1": scores['rouge1'].fmeasure,
                "rouge2": scores['rouge2'].fmeasure,
                "rougeL": scores['rougeL'].fmeasure
            }
        except Exception as e:
            logger.error(f"ROUGE calculation error: {e}")
            return {"rouge1": 0.0, "rouge2": 0.0, "rougeL": 0.0}
    
    def calculate_accuracy(
        self,
        predictions: List[str],
        references: List[str]
    ) -> float:
        """Calculate exact match accuracy"""
        if len(predictions) != len(references):
            return 0.0
        
        matches = sum(1 for p, r in zip(predictions, references) if p.strip().lower() == r.strip().lower())
        return matches / len(predictions)
    
    def calculate_semantic_similarity(
        self,
        reference: str,
        hypothesis: str
    ) -> float:
        """Calculate semantic similarity (placeholder for embedding-based similarity)"""
        # TODO: Implement using sentence embeddings
        return 0.0
    
    def calculate_all_metrics(
        self,
        predictions: List[str],
        references: List[str]
    ) -> Dict[str, float]:
        """Calculate all metrics for a set of predictions"""
        bleu_scores = []
        rouge_scores = {"rouge1": [], "rouge2": [], "rougeL": []}
        
        for pred, ref in zip(predictions, references):
            bleu_scores.append(self.calculate_bleu(ref, pred))
            rouge = self.calculate_rouge(ref, pred)
            for key in rouge_scores:
                rouge_scores[key].append(rouge[key])
        
        return {
            "bleu": np.mean(bleu_scores),
            "rouge1": np.mean(rouge_scores["rouge1"]),
            "rouge2": np.mean(rouge_scores["rouge2"]),
            "rougeL": np.mean(rouge_scores["rougeL"]),
            "accuracy": self.calculate_accuracy(predictions, references)
        }

