"""UMLS terminology service."""

import logging

import httpx

from api.config import settings

logger = logging.getLogger(__name__)


class UMLSService:
    """Service for interacting with UMLS terminology."""

    def __init__(self):
        self.api_key = settings.UMLS_API_KEY
        self.api_url = settings.UMLS_API_URL
        self.client = httpx.AsyncClient()

    async def search_concepts(
        self, query: str, max_results: int = 10, semantic_types: list[str] | None = None
    ) -> list[dict]:
        """Search UMLS concepts."""
        try:
            params = {"string": query, "apiKey": self.api_key, "pageSize": max_results}

            if semantic_types:
                params["sabs"] = ",".join(semantic_types)

            response = await self.client.get(f"{self.api_url}/search/current", params=params)
            response.raise_for_status()

            data = response.json()
            results = []

            for result in data.get("result", {}).get("results", []):
                # Extract semantic type names from objects
                semantic_types_raw = result.get("semanticTypes", [])
                semantic_types = [
                    st.get("name", st) if isinstance(st, dict) else st for st in semantic_types_raw
                ]

                results.append(
                    {
                        "cui": result.get("ui", ""),
                        "preferred_name": result.get("name", ""),
                        "definition": result.get("definition", ""),
                        "semantic_types": semantic_types,
                        "synonyms": result.get("synonyms", []),
                    }
                )

            return results
        except Exception as e:
            logger.error(f"UMLS search error: {e}")
            return []

    async def get_concept(self, cui: str) -> dict | None:
        """Get concept details by CUI."""
        try:
            response = await self.client.get(
                f"{self.api_url}/content/current/CUI/{cui}", params={"apiKey": self.api_key}
            )
            response.raise_for_status()

            data = response.json()
            result = data.get("result", {})

            # Extract semantic type names from objects
            semantic_types_raw = result.get("semanticTypes", [])
            semantic_types = [
                st.get("name", st) if isinstance(st, dict) else st for st in semantic_types_raw
            ]

            return {
                "cui": cui,
                "preferred_name": result.get("name", ""),
                "definition": result.get("definition", ""),
                "semantic_types": semantic_types,
                "synonyms": result.get("synonyms", []),
            }
        except Exception as e:
            logger.error(f"UMLS concept retrieval error: {e}")
            return None

    async def get_semantic_relations(
        self, cui: str, relation_type: str | None = None
    ) -> list[dict]:
        """Get semantic relations for a concept."""
        # TODO: Implement semantic relation retrieval
        return []

    async def get_semantic_types(self) -> list[str]:
        """Get list of available semantic types."""
        # Common UMLS semantic types
        return [
            "T001",  # Organism
            "T002",  # Plant
            "T004",  # Fungus
            "T005",  # Virus
            "T007",  # Bacterium
            "T017",  # Anatomical Structure
            "T023",  # Body Part, Organ, or Organ Component
            "T029",  # Body Location or Region
            "T031",  # Body Substance
            "T033",  # Finding
            "T034",  # Laboratory or Test Result
            "T037",  # Injury or Poisoning
            "T039",  # Physiologic Function
            "T040",  # Organism Function
            "T041",  # Mental Process
            "T042",  # Organ or Tissue Function
            "T044",  # Molecular Function
            "T046",  # Pathologic Function
            "T047",  # Disease or Syndrome
            "T048",  # Mental or Behavioral Dysfunction
            "T049",  # Cell or Molecular Dysfunction
            "T050",  # Experimental Model of Disease
            "T051",  # Event
            "T052",  # Activity
            "T053",  # Geographic Area
            "T054",  # Occupation or Discipline
            "T055",  # Group
            "T056",  # Age Group
            "T057",  # Group Attribute
            "T058",  # Health Care Activity
            "T059",  # Laboratory Procedure
            "T060",  # Diagnostic Procedure
            "T061",  # Therapeutic or Preventive Procedure
            "T062",  # Research Activity
            "T063",  # Molecular Biology Research Technique
            "T064",  # Governmental or Regulatory Activity
            "T065",  # Educational Activity
            "T066",  # Machine Activity
            "T067",  # Phenomenon or Process
            "T068",  # Human-caused Phenomenon or Process
            "T069",  # Environmental Effect of Humans
            "T070",  # Human
            "T071",  # Entity
            "T072",  # Physical Object
            "T073",  # Manufactured Object
            "T074",  # Medical Device
            "T075",  # Research Device
            "T077",  # Intellectual Product
            "T078",  # Health Care Related Organization
            "T079",  # Temporal Concept
            "T080",  # Qualitative Concept
            "T081",  # Quantitative Concept
            "T082",  # Spatial Concept
            "T083",  # Geographic Concept
            "T085",  # Molecular Sequence
            "T086",  # Nucleotide Sequence
            "T087",  # Amino Acid Sequence
            "T088",  # Carbohydrate Sequence
            "T089",  # Regulation or Law
            "T090",  # Occupation or Discipline
            "T091",  # Biomedical Occupation or Discipline
            "T092",  # Organization
            "T093",  # Health Care Related Organization
            "T094",  # Professional Society
            "T095",  # Self-help or Relief Organization
            "T096",  # Group
            "T097",  # Professional or Occupational Group
            "T098",  # Population Group
            "T099",  # Family Group
            "T100",  # Age Group
            "T101",  # Patient or Disabled Group
            "T102",  # Group Attribute
            "T103",  # Social Behavior
            "T104",  # Individual Behavior
            "T105",  # Daily or Recreational Activity
            "T106",  # Occupational Activity
            "T107",  # Educational Activity
            "T108",  # Research Activity
            "T109",  # Governmental or Regulatory Activity
            "T110",  # Machine Activity
            "T111",  # Temporal Concept
            "T112",  # Qualitative Concept
            "T113",  # Quantitative Concept
            "T114",  # Spatial Concept
            "T115",  # Geographic Concept
            "T116",  # Amino Acid, Peptide, or Protein
            "T117",  # Pharmacologic Substance
            "T118",  # Chemical
            "T119",  # Chemical Viewed Structurally
            "T120",  # Chemical Viewed Functionally
            "T121",  # Pharmacologic Substance
            "T122",  # Biomedical or Dental Material
            "T123",  # Biologically Active Substance
            "T124",  # Neuroreactive Substance or Biogenic Amine
            "T125",  # Hormone
            "T126",  # Enzyme
            "T127",  # Vitamin
            "T128",  # Immunologic Factor
            "T129",  # Receptor
            "T130",  # Antibiotic
            "T131",  # Hazardous or Poisonous Substance
            "T167",  # Substance
            "T168",  # Food
            "T169",  # Functional Concept
            "T170",  # Intellectual Product
            "T171",  # Language
            "T184",  # Sign or Symptom
            "T185",  # Mental or Behavioral Dysfunction
            "T190",  # Anatomical Abnormality
            "T191",  # Neoplastic Process
            "T192",  # Congenital Abnormality
            "T193",  # Acquired Abnormality
            "T194",  # Cell or Molecular Dysfunction
            "T195",  # Experimental Model of Disease
            "T196",  # Disease or Syndrome
            "T197",  # Injury or Poisoning
            "T200",  # Clinical Drug
            "T201",  # Clinical Attribute
            "T203",  # Drug Delivery Device
        ]
