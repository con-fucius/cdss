from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PathwayStep:
    step_id: str
    name: str
    description: str
    guideline_ref: str  # Matches a section title in the indexed guideline
    completion_criteria: Callable[
        [dict[str, Any]], bool
    ]  # Takes patient_state, returns True if complete
    blocking_inputs: list[str] = field(
        default_factory=list
    )  # Data fields required to evaluate this step
    contraindication_check: str | None = (
        None  # Name of drug/class to check against evidence graph
    )


@dataclass
class ClinicalPathway:
    pathway_id: str
    disease: str
    name: str
    target_population: str
    entry_criteria: Callable[[dict[str, Any]], bool]
    steps: list[PathwayStep] = field(default_factory=list)
