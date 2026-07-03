from typing import Dict, Any
from .schema import ClinicalPathway, PathwayStep

def _has_cd4(state: Dict[str, Any]) -> bool:
    vitals = state.get("vitals", {})
    return "cd4" in vitals or "cd4_count" in vitals

def _has_creatinine(state: Dict[str, Any]) -> bool:
    for lab in state.get("labs", []):
        if lab.get("lab_type") == "creatinine":
            return True
    return False

def _has_art(state: Dict[str, Any]) -> bool:
    meds = state.get("active_medications", [])
    return any(med.get("name") for med in meds if "art" in med.get("name", "").lower() or "tdf" in med.get("name", "").lower() or "dtg" in med.get("name", "").lower())

PATHWAY_REGISTRY: Dict[str, ClinicalPathway] = {
    "hiv_art_initiation": ClinicalPathway(
        pathway_id="hiv_art_initiation",
        disease="hiv",
        name="HIV ART Initiation (Treatment-Naive Adult)",
        target_population="Adults newly diagnosed with HIV, not previously on ART",
        entry_criteria=lambda s: "hiv" in s.get("active_diagnoses", []),
        steps=[
            PathwayStep(
                step_id="hiv_art_1",
                name="Baseline CD4 and Staging",
                description="Assess WHO clinical stage and obtain baseline CD4 count.",
                guideline_ref="Baseline Investigations for ART",
                completion_criteria=lambda s: _has_cd4(s),
                blocking_inputs=["cd4_count", "who_stage"]
            ),
            PathwayStep(
                step_id="hiv_art_2",
                name="Baseline Renal Function",
                description="Assess renal function (creatinine, eGFR) prior to TDF-based regimen.",
                guideline_ref="Baseline Investigations for ART",
                completion_criteria=lambda s: _has_creatinine(s),
                blocking_inputs=["creatinine"]
            ),
            PathwayStep(
                step_id="hiv_art_3",
                name="TB Screening",
                description="Perform symptom screen for TB (cough, fever, weight loss, night sweats).",
                guideline_ref="TB/HIV Co-infection Management",
                completion_criteria=lambda s: "tb_screen" in s.get("vitals", {}),
                blocking_inputs=["tb_screen_result"]
            ),
            PathwayStep(
                step_id="hiv_art_4",
                name="Initiate Preferred First-Line ART",
                description="Start TDF/3TC/DTG (Tenofovir/Lamivudine/Dolutegravir) if no contraindications.",
                guideline_ref="Preferred First-Line ART Regimens for Adults",
                completion_criteria=lambda s: _has_art(s),
                blocking_inputs=["art_regimen_prescribed"],
                contraindication_check="tdf"
            ),
            PathwayStep(
                step_id="hiv_art_5",
                name="Schedule 2-Week Follow-up",
                description="Review tolerability, adherence, and signs of IRIS.",
                guideline_ref="Monitoring on ART",
                completion_criteria=lambda s: False,  # Pending
                blocking_inputs=["follow_up_appointment_date"]
            )
        ]
    ),
    "tb_ds_initiation": ClinicalPathway(
        pathway_id="tb_ds_initiation",
        disease="tb",
        name="DS-TB Treatment Initiation",
        target_population="Patients diagnosed with Drug-Susceptible TB",
        entry_criteria=lambda s: "tb" in s.get("active_diagnoses", []),
        steps=[
            PathwayStep(
                step_id="tb_ds_1",
                name="Baseline LFTs",
                description="Assess baseline liver function before starting intensive phase.",
                guideline_ref="Baseline Investigations for TB Treatment",
                completion_criteria=lambda s: any(l.get("lab_type") in ["alt", "ast", "bilirubin"] for l in s.get("labs", [])),
                blocking_inputs=["alt", "ast"]
            ),
            PathwayStep(
                step_id="tb_ds_2",
                name="Initiate Intensive Phase (RHZE)",
                description="Start Rifampicin, Isoniazid, Pyrazinamide, Ethambutol.",
                guideline_ref="Treatment Regimens for Drug-Susceptible TB",
                completion_criteria=lambda s: any("rifampicin" in m.get("name", "").lower() for m in s.get("active_medications", [])),
                blocking_inputs=["rhze_prescription"],
                contraindication_check="rifampicin"
            )
        ]
    ),
    "t2dm_initial": ClinicalPathway(
        pathway_id="t2dm_initial",
        disease="diabetes",
        name="T2DM Initial Management",
        target_population="Newly diagnosed Type 2 Diabetes",
        entry_criteria=lambda s: "t2dm" in s.get("active_diagnoses", []) or "diabetes" in s.get("active_diagnoses", []),
        steps=[
            PathwayStep(
                step_id="t2dm_1",
                name="Baseline HbA1c",
                description="Obtain baseline HbA1c to guide therapy.",
                guideline_ref="Diagnosis of Diabetes",
                completion_criteria=lambda s: any(l.get("lab_type") == "hba1c" for l in s.get("labs", [])),
                blocking_inputs=["hba1c"]
            ),
            PathwayStep(
                step_id="t2dm_2",
                name="Lifestyle and Metformin",
                description="Initiate lifestyle modifications and Metformin (if eGFR > 45).",
                guideline_ref="Pharmacological Management of Type 2 Diabetes",
                completion_criteria=lambda s: any("metformin" in m.get("name", "").lower() for m in s.get("active_medications", [])),
                blocking_inputs=["metformin_prescription"],
                contraindication_check="metformin"
            )
        ]
    ),
    # 9 Shell Pathways
    "hiv_pmtct": ClinicalPathway(pathway_id="hiv_pmtct", disease="hiv", name="HIV PMTCT", target_population="Pregnant women living with HIV", entry_criteria=lambda s: True, steps=[]),
    "tb_mdr": ClinicalPathway(pathway_id="tb_mdr", disease="tb", name="MDR-TB Management", target_population="Patients with Rifampicin-resistant TB", entry_criteria=lambda s: True, steps=[]),
    "t1dm_pediatric": ClinicalPathway(pathway_id="t1dm_pediatric", disease="diabetes", name="Pediatric T1DM", target_population="Children diagnosed with Type 1 Diabetes", entry_criteria=lambda s: True, steps=[]),
    "cvd_hypertension": ClinicalPathway(pathway_id="cvd_hypertension", disease="cvd", name="Uncomplicated Hypertension", target_population="Adults with blood pressure >140/90", entry_criteria=lambda s: True, steps=[]),
    "cvd_heart_failure": ClinicalPathway(pathway_id="cvd_heart_failure", disease="cvd", name="Chronic Heart Failure", target_population="Patients with symptomatic heart failure", entry_criteria=lambda s: True, steps=[]),
    "malaria_uncomplicated": ClinicalPathway(pathway_id="malaria_uncomplicated", disease="malaria", name="Uncomplicated Malaria", target_population="Patients with positive mRDT/microscopy and no danger signs", entry_criteria=lambda s: True, steps=[]),
    "malaria_severe": ClinicalPathway(pathway_id="malaria_severe", disease="malaria", name="Severe Malaria", target_population="Patients with severe malaria criteria", entry_criteria=lambda s: True, steps=[]),
    "mh_depression": ClinicalPathway(pathway_id="mh_depression", disease="mental_health", name="Major Depressive Disorder", target_population="Patients with PHQ-9 > 9", entry_criteria=lambda s: True, steps=[]),
    "mh_psychosis": ClinicalPathway(pathway_id="mh_psychosis", disease="mental_health", name="Acute Psychosis", target_population="Patients presenting with acute psychotic symptoms", entry_criteria=lambda s: True, steps=[])
}
