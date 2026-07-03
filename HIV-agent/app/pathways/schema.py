from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable, Any

@dataclass
class PathwayStep:
    step_id: str
    name: str
    description: str
    guideline_ref: str  # Matches a section title in the indexed guideline
    completion_criteria: Callable[[Dict[str, Any]], bool]  # Takes patient_state, returns True if complete
    blocking_inputs: List[str] = field(default_factory=list)  # Data fields required to evaluate this step
    contraindication_check: Optional[str] = None  # Name of drug/class to check against evidence graph

@dataclass
class ClinicalPathway:
    pathway_id: str
    disease: str
    name: str
    target_population: str
    entry_criteria: Callable[[Dict[str, Any]], bool]
    steps: List[PathwayStep] = field(default_factory=list)
