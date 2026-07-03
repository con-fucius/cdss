import json
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Reference drug list for validation
VALID_DRUGS = {
    # HIV
    "tdf", "dtg", "lamivudine", "efavirenz", "nvp", "abacavir",
    # Diabetes
    "metformin", "insulin", "glibenclamide", "sitagliptin",
    # CVD
    "amlodipine", "losartan", "atenolol", "hydrochlorothiazide", "aspirin", "statin",
    # TB
    "rifampicin", "isoniazid", "pyrazinamide", "ethambutol",
    # Malaria
    "artemether", "lumefantrine", "artesunate", "amodiaquine", "quinine",
    "dihydroartemisinin", "piperaquine", "sp", "sulfadoxine", "pyrimethamine"
}

class TableValidator:
    """
    Validates structured tables based on their type.
    Failed validation -> flagged for manual review, NOT auto-imported.
    """
    def __init__(self, raw_dir: str = "app/kb/raw", validated_dir: str = "app/kb/validated", flagged_dir: str = "app/kb/flagged"):
        self.raw_dir = Path(raw_dir)
        self.validated_dir = Path(validated_dir)
        self.flagged_dir = Path(flagged_dir)
        
        self.validated_dir.mkdir(parents=True, exist_ok=True)
        self.flagged_dir.mkdir(parents=True, exist_ok=True)

    def validate_table(self, table_data: Dict[str, Any]) -> bool:
        """Run validation logic based on table type."""
        t_type = table_data.get("type")
        headers = [str(h).lower() for h in table_data.get("schema", {}).get("columns", [])]
        data = table_data.get("data", [])
        
        if not data:
            return False

        if t_type == "regimen":
            # must have columns for population, drugs, frequency, notes
            has_drugs = any(col for col in headers if 
                "drug" in col or "regimen" in col or "arv" in col or
                "artemether" in col or "act" in col or "treatment" in col)
            if not has_drugs:
                table_data["validation_error"] = "Missing drug/regimen column"
                return False

        elif t_type == "diagnostic_criteria":
            # must have threshold values (numeric with units)
            has_value = any(col for col in headers if 
                "value" in col or "threshold" in col or "target" in col or
                "criteria" in col or "result" in col or "rdt" in col or "smear" in col)
            if not has_value:
                table_data["validation_error"] = "Missing threshold/value column"
                return False

        elif t_type == "dosing":
            # must have weight or age ranges, dose, frequency
            has_dose = any(col for col in headers if "dose" in col or "tablet" in col or "mg" in col)
            has_range = any(col for col in headers if 
                "weight" in col or "age" in col or "kg" in col or "year" in col)
            if not (has_dose and has_range):
                table_data["validation_error"] = "Missing dose or weight/age column"
                return False

        return True

    def process_all_raw(self, disease: str):
        """Process all raw tables for a disease, moving to validated or flagged."""
        disease_raw_dir = self.raw_dir / disease
        if not disease_raw_dir.exists():
            return
            
        disease_val_dir = self.validated_dir / disease
        disease_flag_dir = self.flagged_dir / disease
        
        disease_val_dir.mkdir(parents=True, exist_ok=True)
        disease_flag_dir.mkdir(parents=True, exist_ok=True)
        
        for file_path in disease_raw_dir.glob("*.json"):
            with open(file_path, "r") as f:
                table_data = json.load(f)
                
            if table_data.get("quality", {}).get("status") == "degraded":
                table_data["validation_error"] = "Degraded quality score from extraction"
                is_valid = False
            else:
                is_valid = self.validate_table(table_data)
                
            out_dir = disease_val_dir if is_valid else disease_flag_dir
            out_file = out_dir / file_path.name
            
            with open(out_file, "w") as f:
                json.dump(table_data, f, indent=2)
                
            logger.info(f"Table {file_path.name} -> {'VALIDATED' if is_valid else 'FLAGGED'}")
