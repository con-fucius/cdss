"""
Disease-specific and runtime configuration for CDSS.

Phase 0 fixes:
- TB: guideline_name and source_url updated to match actual indexed PDF
  (2021 Integrated Guideline, not the 2025 URL that returns 404)
- Malaria: guideline_name and guideline_warning updated to reflect
  actual indexed PDF (3rd Edition 2010, not 2016)
- Mental Health: source_url set to empty string (no public URL confirmed);
  ingestion uses local PDF in app/docs/Mental Health/
- CVD: source_url noted as unverified; ingestion uses local PDF
- DM: source_url noted as unverified; ingestion uses local PDF
- CDSS_CHECK_GUIDELINE_UPDATES default is explicitly false in check
  (enforced in api.py lifespan)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

load_dotenv(ROOT_DIR / ".env", override=False)
load_dotenv(APP_DIR / ".env", override=False)

logger = logging.getLogger(__name__)

MIN_PATIENT_SALT_BYTES = 16
_DEV_PATIENT_SALT = "cdss-development-only-patient-salt"


def get_cdss_env() -> str:
    """Return the deployment environment key."""
    return os.getenv("CDSS_ENV", "development").strip().lower()


def get_patient_salt() -> str:
    """
    Return the configured patient hashing salt.

    Development falls back to an explicit non-production value so local smoke
    tests still run; production startup validation refuses this state.
    """
    salt = os.getenv("CDSS_PATIENT_SALT", "").strip()
    return salt or _DEV_PATIENT_SALT


def get_database_url() -> str:
    """Return the async SQLAlchemy database URL for Phase 1 storage."""
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://cdss:cdss@localhost:5432/cdss",
    ).strip()


def get_session_storage_backend() -> str:
    """Return configured session history backend."""
    backend = os.getenv("CDSS_SESSION_STORAGE_BACKEND", "postgres").strip().lower()
    if backend not in {"memory", "postgres"}:
        logger.warning(
            "Unsupported CDSS_SESSION_STORAGE_BACKEND=%s; falling back to memory",
            backend,
        )
        return "memory"
    return backend


def get_audit_storage_backend() -> str:
    """Return configured audit-log backend."""
    if os.getenv("CDSS_AUDIT_DB_PATH"):
        return "sqlite"
    backend = os.getenv("CDSS_AUDIT_STORAGE_BACKEND", "postgres").strip().lower()
    if backend not in {"sqlite", "postgres"}:
        logger.warning(
            "Unsupported CDSS_AUDIT_STORAGE_BACKEND=%s; falling back to postgres",
            backend,
        )
        return "postgres"
    return backend


def get_embedding_model_name() -> str:
    """Return the embedding model used for guideline and PageIndex vectors."""
    return os.getenv("CDSS_EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5").strip()


def validate_patient_salt() -> None:
    """
    Validate patient hash salt at startup.

    Production refuses to start without at least 16 bytes of salt. Development
    logs once but keeps running so local remediation work is not blocked by a
    missing secret.
    """
    salt = os.getenv("CDSS_PATIENT_SALT", "").strip()
    production = get_cdss_env() == "production"
    valid = len(salt.encode("utf-8")) >= MIN_PATIENT_SALT_BYTES

    if valid:
        return

    message = (
        "CDSS_PATIENT_SALT is missing or shorter than "
        f"{MIN_PATIENT_SALT_BYTES} bytes; patient references will use a "
        "development-only salt."
    )
    if production:
        raise RuntimeError(
            "CDSS_PATIENT_SALT must be set to at least "
            f"{MIN_PATIENT_SALT_BYTES} bytes when CDSS_ENV=production."
        )
    logger.warning(message)

DISEASE_CONFIG = {
    "hiv": {
        "display_name": "HIV/AIDS",
        "guideline_name": "Kenya HIV Prevention and Treatment Guidelines 2022",
        "source_url": "https://nascop.or.ke/wp-content/uploads/2022/07/Kenya-ART-Guidelines-2022.pdf",
        "table_name": "hiv_guidelines",
        "use_hyde": True,
        "population_options": [
            "Select...", "Adult", "Adolescent (10-19)", "Child (<10)", "Infant (<1)",
        ],
        "condition_options": [
            "Select...", "Treatment-naive", "Treatment-experienced",
            "Pregnant", "Breastfeeding",
        ],
        "comorbidity_options": [
            "None", "TB", "Hepatitis B", "Hepatitis C", "CKD", "Diabetes",
        ],
        "filter_options": [
            "First-line", "Second-line", "Prophylaxis", "Monitoring", "PMTCT",
        ],
        "clinical_params": [
            {"id": "cd4_count", "label": "CD4 Count", "unit": "cells/mm³"},
            {"id": "viral_load", "label": "Viral Load", "unit": "copies/mL"},
            {"id": "who_stage", "label": "WHO Stage", "options": ["1", "2", "3", "4"]},
        ],
        "validation_keywords": ["TDF", "DTG", "lamivudine", "CD4", "viral load"],
    },
    "diabetes": {
        "display_name": "Diabetes Mellitus",
        "guideline_name": "National Clinical Guidelines on Management of Diabetes Mellitus V15 2024",
        # URL unverified — ingestion uses local PDF in app/docs/Diabetes Mellitus/
        "source_url": "https://health.go.ke/wp-content/uploads/2024/01/Kenya-DM-Guidelines-2024.pdf",
        "table_name": "diabetes_guidelines",
        "use_hyde": True,
        "population_options": [
            "Select...", "Adult", "Elderly (>65)", "Pregnant", "Child/Adolescent",
        ],
        "condition_options": [
            "Select...", "Type 1 DM", "Type 2 DM", "Gestational DM",
            "DM with complications",
        ],
        "comorbidity_options": [
            "None", "Hypertension", "CKD", "Heart failure", "HIV", "TB",
        ],
        "filter_options": [
            "Diagnosis", "Pharmacotherapy", "Insulin", "Monitoring", "Complications",
        ],
        "clinical_params": [
            {"id": "hba1c", "label": "HbA1c", "unit": "%"},
            {"id": "fpg", "label": "FPG", "unit": "mmol/L"},
            {"id": "egfr", "label": "eGFR", "unit": "mL/min/1.73m²"},
            {"id": "bmi", "label": "BMI", "unit": "kg/m²"},
        ],
        "validation_keywords": ["HbA1c", "metformin", "insulin", "FPG", "SMBG"],
    },
    "cvd": {
        "display_name": "Cardiovascular Disease",
        "guideline_name": "Kenya National Guidelines for The Management of Cardiovascular Diseases",
        # URL unverified — ingestion uses local PDF in app/docs/Cardiovascular Disease/
        "source_url": "https://health.go.ke/wp-content/uploads/2024/02/Kenya-CVD-Guidelines-2024.pdf",
        "table_name": "cvd_guidelines",
        "use_hyde": False,
        "population_options": ["Select...", "Adult", "Elderly (>65)", "Pregnant"],
        "condition_options": [
            "Select...", "Hypertension", "Heart Failure",
            "Ischemic Heart Disease", "Stroke Risk",
        ],
        "comorbidity_options": ["None", "Diabetes", "CKD", "HIV", "Obesity"],
        "filter_options": [
            "Screening", "Diagnosis", "Lifestyle", "Pharmacotherapy", "Emergency Care",
        ],
        "clinical_params": [
            {"id": "bp_systolic", "label": "BP Systolic", "unit": "mmHg"},
            {"id": "bp_diastolic", "label": "BP Diastolic", "unit": "mmHg"},
            {"id": "total_cholesterol", "label": "Total Cholesterol", "unit": "mmol/L"},
        ],
        "validation_keywords": [
            "hypertension", "statin", "amlodipine", "blood pressure", "CV risk",
        ],
    },
    "tb": {
        "display_name": "Tuberculosis",
        # Actual indexed PDF: Integrated Guideline For Tuberculosis, Leprosy
        # And Lung Disease 2021 — not the 2025 URL which returns 404
        "guideline_name": "Integrated Guideline for Tuberculosis, Leprosy and Lung Disease 2021",
        "source_url": "",   # No confirmed public URL; local PDF used for ingestion
        "table_name": "tb_guidelines",
        "use_hyde": True,
        "population_options": [
            "Select...", "Adult", "Child (<15)", "Infant", "Pregnant",
        ],
        "condition_options": [
            "Select...", "Drug-Susceptible TB", "MDR-TB",
            "Extrapulmonary TB", "Latent TB",
        ],
        "comorbidity_options": ["None", "HIV", "Diabetes", "Malnutrition"],
        "filter_options": [
            "Screening", "Diagnosis", "Intensive Phase",
            "Continuation Phase", "TPT",
        ],
        "clinical_params": [
            {"id": "weight", "label": "Weight", "unit": "kg"},
            {
                "id": "genexpert_result",
                "label": "GeneXpert Result",
                "options": ["Detected", "Not Detected", "Rif Resistance"],
            },
        ],
        "validation_keywords": [
            "rifampicin", "isoniazid", "sputum", "GeneXpert", "TPT",
        ],
    },
    "malaria": {
        "display_name": "Malaria",
        # Actual indexed PDF: 3rd Edition 2010 — not 2016 as previously stated
        "guideline_name": "National Guidelines for the Diagnosis, Treatment and Prevention of Malaria (3rd Edition, 2010)",
        "guideline_warning": (
            "This guideline is the 3rd Edition from 2010 and does not reflect "
            "current recommendations. Use with significant clinical caution and "
            "consult current WHO/KEMRI guidance for treatment decisions."
        ),
        # MOH URL for historical reference only — do not rely on for updates
        "source_url": "https://health.go.ke/wp-content/uploads/2016/04/National-Guidelines-for-the-Diagnosis-Treatment-and-Prevention-of-Malaria-in-Kenya-1.pdf",
        "table_name": "malaria_guidelines",
        "use_hyde": True,
        "population_options": [
            "Select...", "Adult", "Child", "Infant", "Pregnant",
        ],
        "condition_options": [
            "Select...", "Uncomplicated Malaria", "Severe Malaria",
            "Malaria in Pregnancy",
        ],
        "comorbidity_options": ["None", "HIV", "Malnutrition", "Anemia"],
        "filter_options": [
            "Diagnosis", "First-line Treatment", "Second-line Treatment",
            "Prevention", "Severe Management",
        ],
        "clinical_params": [
            {"id": "weight", "label": "Weight", "unit": "kg"},
            {"id": "temperature", "label": "Temperature", "unit": "°C"},
            {"id": "parasitemia", "label": "Parasite Density", "unit": "parasites/µL"},
        ],
        "validation_keywords": [
            "artemether", "lumefantrine", "AL", "artesunate", "ACT", "mRDT", "smear",
        ],
    },
    "mental_health": {
        "display_name": "Mental Health",
        "guideline_name": "National Clinical Guideline for Management of Common Mental Disorders",
        "source_url": "",   # No confirmed public URL; local PDF used for ingestion
        "table_name": "mental_health_guidelines",
        "use_hyde": True,
        "population_options": [
            "Select...", "Adult", "Adolescent", "Child", "Pregnant",
        ],
        "condition_options": [
            "Select...", "Depression", "Anxiety", "Psychosis",
            "Substance Use", "Suicide Risk",
        ],
        "comorbidity_options": ["None", "HIV", "Diabetes", "TB", "Pregnancy"],
        "filter_options": [
            "Screening", "Diagnosis", "Psychosocial",
            "Pharmacotherapy", "Referral",
        ],
        "clinical_params": [
            {
                "id": "risk_level",
                "label": "Risk Level",
                "options": ["Low", "Moderate", "High", "Emergency"],
            },
            {
                "id": "suicide_risk",
                "label": "Suicide Risk",
                "options": ["None", "Ideation", "Plan", "Attempt"],
            },
        ],
        "validation_keywords": [
            "depression", "anxiety", "psychosis", "suicide", "counselling",
        ],
    },
}
