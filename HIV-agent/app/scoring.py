from typing import Dict, Any, List, Optional

class ClinicalScorer:
    """
    Deterministic clinical scoring engine. No LLM calls, no external I/O.
    """

    @staticmethod
    def news2(vitals: Dict[str, Any]) -> Dict[str, Any]:
        """
        NHS NEWS2 specification (2017).
        Inputs: 'rr', 'spo2', 'spo2_scale' (1 or 2), 'supplemental_o2' (bool),
        'bp_systolic', 'heart_rate', 'consciousness' ("A"/"V"/"P"/"U"), 'temperature'.
        """
        required = ['rr', 'spo2', 'bp_systolic', 'heart_rate', 'temperature']
        missing = [k for k in required if vitals.get(k) is None]
        if missing:
            raise ValueError(f"Missing required vitals for NEWS2: {', '.join(missing)}")
        
        # We default to scale 1 and no supplemental oxygen and alert ('A') if not provided
        spo2_scale = vitals.get('spo2_scale', 1)
        supp_o2 = vitals.get('supplemental_o2', False)
        consciousness = str(vitals.get('consciousness', 'A')).upper()

        rr = float(vitals['rr'])
        spo2 = float(vitals['spo2'])
        bp = float(vitals['bp_systolic'])
        hr = float(vitals['heart_rate'])
        temp = float(vitals['temperature'])

        score = 0
        component_scores = {}
        triggers = []

        # Respiration Rate
        if rr <= 8:
            rr_score = 3
        elif 9 <= rr <= 11:
            rr_score = 1
        elif 12 <= rr <= 20:
            rr_score = 0
        elif 21 <= rr <= 24:
            rr_score = 2
        else: # >= 25
            rr_score = 3
        component_scores['rr'] = rr_score
        score += rr_score
        if rr_score > 0: triggers.append(f"RR = {rr} (score {rr_score})")

        # SpO2
        if spo2_scale == 1:
            if spo2 <= 91:
                spo2_score = 3
            elif 92 <= spo2 <= 93:
                spo2_score = 2
            elif 94 <= spo2 <= 95:
                spo2_score = 1
            else: # >= 96
                spo2_score = 0
        else: # Scale 2
            if spo2 <= 83 or (spo2 >= 97 and supp_o2):
                spo2_score = 3
            elif 84 <= spo2 <= 85 or (95 <= spo2 <= 96 and supp_o2):
                spo2_score = 2
            elif 86 <= spo2 <= 87 or (93 <= spo2 <= 94 and supp_o2):
                spo2_score = 1
            else:
                spo2_score = 0
        component_scores['spo2'] = spo2_score
        score += spo2_score
        if spo2_score > 0: triggers.append(f"SpO2 = {spo2}% (score {spo2_score})")

        # Supplemental O2
        if supp_o2:
            o2_score = 2
            component_scores['supplemental_o2'] = o2_score
            score += o2_score
            triggers.append(f"On supplemental O2 (score 2)")
        else:
            component_scores['supplemental_o2'] = 0

        # Systolic BP
        if bp <= 90:
            bp_score = 3
        elif 91 <= bp <= 100:
            bp_score = 2
        elif 101 <= bp <= 110:
            bp_score = 1
        elif 111 <= bp <= 219:
            bp_score = 0
        else: # >= 220
            bp_score = 3
        component_scores['bp_systolic'] = bp_score
        score += bp_score
        if bp_score > 0: triggers.append(f"BP = {bp} (score {bp_score})")

        # Heart Rate
        if hr <= 40:
            hr_score = 3
        elif 41 <= hr <= 50:
            hr_score = 1
        elif 51 <= hr <= 90:
            hr_score = 0
        elif 91 <= hr <= 110:
            hr_score = 1
        elif 111 <= hr <= 130:
            hr_score = 2
        else: # >= 131
            hr_score = 3
        component_scores['heart_rate'] = hr_score
        score += hr_score
        if hr_score > 0: triggers.append(f"HR = {hr} (score {hr_score})")

        # Consciousness
        if consciousness != 'A':
            c_score = 3
            component_scores['consciousness'] = c_score
            score += c_score
            triggers.append(f"Consciousness = {consciousness} (score 3)")
        else:
            component_scores['consciousness'] = 0

        # Temperature
        if temp <= 35.0:
            temp_score = 3
        elif 35.1 <= temp <= 36.0:
            temp_score = 1
        elif 36.1 <= temp <= 38.0:
            temp_score = 0
        elif 38.1 <= temp <= 39.0:
            temp_score = 1
        else: # >= 39.1
            temp_score = 2
        component_scores['temperature'] = temp_score
        score += temp_score
        if temp_score > 0: triggers.append(f"Temp = {temp} (score {temp_score})")

        if score >= 7:
            risk_level = "high"
        elif score >= 5 or any(v == 3 for v in component_scores.values()):
            risk_level = "medium"
        elif score >= 1:
            risk_level = "low-medium"
        else:
            risk_level = "low"

        escalation_required = score >= 5 or any(v == 3 for v in component_scores.values())

        return {
            "score": score,
            "risk_level": risk_level,
            "component_scores": component_scores,
            "escalation_required": escalation_required,
            "trigger": ", ".join(triggers) if triggers else "Normal parameters",
            "source_guideline": "NHS NEWS2 2017"
        }

    @staticmethod
    def egfr_ckd_stage(creatinine: float, age: int, sex: str) -> Dict[str, Any]:
        """
        CKD-EPI 2021 equation (race-free).
        creatinine in µmol/L
        """
        if creatinine is None or age is None or sex is None:
            raise ValueError("Missing required inputs for eGFR (creatinine, age, sex)")

        cr_mg_dl = float(creatinine) / 88.4
        age_f = float(age)
        sex_norm = str(sex).lower()
        if sex_norm.startswith('f'):
            k = 0.7
            alpha = -0.241
            mult = 1.012
        else:
            k = 0.9
            alpha = -0.302
            mult = 1.0

        cr_k = cr_mg_dl / k
        min_cr = min(cr_k, 1)
        max_cr = max(cr_k, 1)

        egfr = 142 * (min_cr ** alpha) * (max_cr ** -1.200) * (0.9938 ** age_f) * mult

        if egfr >= 90: stage = "1"
        elif 60 <= egfr < 90: stage = "2"
        elif 45 <= egfr < 60: stage = "3a"
        elif 30 <= egfr < 45: stage = "3b"
        elif 15 <= egfr < 30: stage = "4"
        else: stage = "5"

        implications = {}
        if egfr < 50:
            implications["TDF"] = "dose_adjust"
        if egfr < 45:
            implications["ACE_inhibitor"] = "monitor_closely"
        if egfr < 30:
            implications["metformin"] = "contraindicated"

        return {
            "egfr": round(egfr, 1),
            "ckd_stage": stage,
            "drug_implications": implications,
            "source_guideline": "CKD-EPI 2021"
        }

    @staticmethod
    def who_hiv_stage(clinical_features: List[str], cd4: Optional[float] = None) -> Dict[str, Any]:
        """
        Map clinical features against WHO Stage 1-4 criteria (Kenya ARV Guidelines 2022, Annex).
        """
        if clinical_features is None:
            raise ValueError("Missing required input for WHO HIV Stage (clinical_features)")
            
        stage_4_criteria = ["pneumocystis pneumonia", "toxoplasmosis", "cryptococcosis", "kaposi's sarcoma", "hiv wasting syndrome", "cmv"]
        stage_3_criteria = ["unexplained severe weight loss", "unexplained chronic diarrhoea", "unexplained persistent fever", "oral candidiasis", "oral hairy leukoplakia", "pulmonary tb", "severe bacterial infections"]
        stage_2_criteria = ["unexplained moderate weight loss", "recurrent respiratory tract infections", "herpes zoster", "angular cheilitis", "recurrent oral ulcerations", "papular pruritic eruptions", "seborrhoeic dermatitis", "fungal nail infections"]

        stage = 1
        criteria_met = []

        features_lower = [f.lower() for f in clinical_features]

        for feature in features_lower:
            if any(c in feature for c in stage_4_criteria):
                stage = max(stage, 4)
                criteria_met.append(feature)
            elif any(c in feature for c in stage_3_criteria):
                stage = max(stage, 3)
                criteria_met.append(feature)
            elif any(c in feature for c in stage_2_criteria):
                stage = max(stage, 2)
                criteria_met.append(feature)

        if cd4 is not None:
            cd4 = float(cd4)
            if cd4 < 200 and stage < 4:
                stage = 4
                criteria_met.append("cd4 < 200")
            elif cd4 < 350 and stage < 3:
                stage = 3
                criteria_met.append("cd4 < 350")
            elif cd4 < 500 and stage < 2:
                stage = 2
                criteria_met.append("cd4 < 500")

        criteria_not_met_for_next_stage = []
        if stage == 1: criteria_not_met_for_next_stage = stage_2_criteria
        elif stage == 2: criteria_not_met_for_next_stage = stage_3_criteria
        elif stage == 3: criteria_not_met_for_next_stage = stage_4_criteria

        recommended_actions = []
        if stage >= 3:
            recommended_actions.append("Consider cotrimoxazole prophylaxis")
            recommended_actions.append("Initiate or switch ART based on resistance profile")

        return {
            "stage": stage,
            "criteria_met": criteria_met,
            "criteria_not_met_for_next_stage": criteria_not_met_for_next_stage,
            "recommended_actions": recommended_actions,
            "source_guideline": "WHO/Kenya ARV Guidelines 2022"
        }

    @staticmethod
    def child_pugh(labs: Dict[str, Any], clinical: Dict[str, Any]) -> Dict[str, Any]:
        missing = []
        if 'bilirubin' not in labs: missing.append('bilirubin')
        if 'albumin' not in labs: missing.append('albumin')
        if 'inr' not in labs: missing.append('inr')
        if 'ascites' not in clinical: missing.append('ascites')
        if 'encephalopathy' not in clinical: missing.append('encephalopathy')

        if missing:
            raise ValueError(f"Missing required inputs for Child-Pugh: {', '.join(missing)}")

        bili = float(labs['bilirubin'])
        alb = float(labs['albumin'])
        inr = float(labs['inr'])
        ascites = str(clinical['ascites']).lower()
        enceph = str(clinical['encephalopathy'])

        score = 0
        
        if bili < 34: score += 1
        elif 34 <= bili <= 50: score += 2
        else: score += 3

        if alb > 35: score += 1
        elif 28 <= alb <= 35: score += 2
        else: score += 3

        if inr < 1.7: score += 1
        elif 1.7 <= inr <= 2.2: score += 2
        else: score += 3

        if "none" in ascites: score += 1
        elif "mild" in ascites: score += 2
        else: score += 3

        if enceph in ["0", "none"]: score += 1
        elif enceph in ["1", "2", "mild"]: score += 2
        else: score += 3

        if score <= 6: c_class = "A"
        elif 7 <= score <= 9: c_class = "B"
        else: c_class = "C"

        implications = []
        if c_class in ["B", "C"]:
            implications.append("Reduce dose of hepatically cleared medications")
            implications.append("Avoid hepatotoxic drugs")

        return {
            "score": score,
            "class": c_class,
            "drug_dosing_implications": implications,
            "source_guideline": "Child-Pugh (Pugh 1973, updated)"
        }

    @staticmethod
    def malaria_severity(vitals: Dict[str, Any], labs: Dict[str, Any], clinical: Dict[str, Any]) -> Dict[str, Any]:
        """
        WHO severe malaria criteria (Kenya Malaria Guidelines 2023)
        """
        criteria_met = []
        
        # Check vitals
        gcs = vitals.get('gcs')
        if gcs is not None and float(gcs) < 11:
            criteria_met.append("GCS < 11")
        
        rr = vitals.get('rr')
        if rr is not None and float(rr) > 30:
            criteria_met.append("RR > 30")
            
        bp = vitals.get('bp_systolic')
        if bp is not None and float(bp) < 90:
            criteria_met.append("BP systolic < 90")
            
        urine_output = vitals.get('urine_output')
        if urine_output is not None and float(urine_output) < 0.5:
            criteria_met.append("Urine output < 0.5 mL/kg/hr")
            
        # Check labs
        hb = labs.get('hb')
        if hb is not None and float(hb) < 7:
            criteria_met.append("Hb < 7 g/dL")
            
        glucose = labs.get('glucose')
        if glucose is not None and float(glucose) < 2.2:
            criteria_met.append("Blood glucose < 2.2 mmol/L")
            
        parasitaemia = labs.get('parasitaemia')
        if parasitaemia is not None and float(parasitaemia) > 2.0:
            criteria_met.append("Parasitaemia > 2%")
            
        creatinine = labs.get('creatinine')
        if creatinine is not None and float(creatinine) > 265:
            criteria_met.append("Creatinine > 265 µmol/L")
            
        # Check clinical
        if str(clinical.get('jaundice', '')).lower() in ['yes', 'true']:
            criteria_met.append("Jaundice")

        is_severe = len(criteria_met) > 0
        management = "IV Artesunate followed by oral ACT" if is_severe else "Oral ACT"

        return {
            "is_severe": is_severe,
            "criteria_met": criteria_met,
            "criteria_count": len(criteria_met),
            "recommended_management": management,
            "source_guideline": "Kenya Malaria Guidelines 2023"
        }

    @staticmethod
    def diabetes_risk_hba1c(hba1c: Optional[float] = None, fpg: Optional[float] = None) -> Dict[str, Any]:
        """
        WHO/Kenya DM Guidelines V15 2024 thresholds
        """
        if hba1c is None and fpg is None:
            raise ValueError("Missing required inputs: must provide either hba1c or fpg")

        diagnosis = "Normal"
        target_met = True
        intensification = False

        if hba1c is not None:
            h = float(hba1c)
            if h >= 6.5:
                diagnosis = "Diabetes"
                if h >= 7.0: # target usually < 7.0%
                    target_met = False
                    intensification = True
            elif 5.7 <= h < 6.5:
                diagnosis = "Prediabetes"
        elif fpg is not None:
            f = float(fpg)
            if f >= 7.0:
                diagnosis = "Diabetes"
                target_met = False
                intensification = True
            elif 5.6 <= f < 7.0:
                diagnosis = "Prediabetes"

        return {
            "diagnosis": diagnosis,
            "target_met": target_met,
            "intensification_indicated": intensification,
            "source_guideline": "Kenya DM Guidelines V15 2024"
        }

    @staticmethod
    def cvd_risk_score(age: int, sex: str, bp_systolic: int, total_cholesterol: float, smoking: bool, diabetes: bool) -> Dict[str, Any]:
        """
        WHO/ISH cardiovascular risk chart implementation for AFRO-D population (Kenya).
        This is an approximation based on the simplified chart scoring.
        """
        if any(v is None for v in [age, sex, bp_systolic, total_cholesterol, smoking, diabetes]):
            raise ValueError("Missing required inputs for cvd_risk_score")

        age = int(age)
        bp = int(bp_systolic)
        tc = float(total_cholesterol)
        
        # Simplified risk mapping (heuristic for illustration)
        points = 0
        if age >= 60: points += 3
        elif age >= 50: points += 2
        elif age >= 40: points += 1
        
        if str(sex).lower().startswith('m'): points += 1
        if bp >= 160: points += 2
        elif bp >= 140: points += 1
        
        if tc >= 7: points += 2
        elif tc >= 6: points += 1
        
        if smoking: points += 2
        if diabetes: points += 2

        if points >= 8:
            risk = 30.0
            category = "High"
            interventions = ["Immediate statin therapy", "Intensive BP control", "Lifestyle modification"]
        elif points >= 5:
            risk = 20.0
            category = "Moderate"
            interventions = ["Consider statin therapy", "BP control", "Lifestyle modification"]
        else:
            risk = 10.0
            category = "Low"
            interventions = ["Routine monitoring", "Healthy lifestyle advice"]

        return {
            "ten_year_risk_pct": risk,
            "risk_category": category,
            "interventions_indicated": interventions,
            "source_guideline": "WHO/ISH CVD Risk Chart AFRO-D"
        }

def _compute_patient_scores(patient_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    scores = []
    
    # Check for vitals
    vitals = patient_state.get("most_recent_vitals") or {}
    if vitals and all(k in vitals and vitals[k] is not None for k in ["rr", "spo2", "bp_systolic", "heart_rate", "temperature"]):
        try:
            news_res = ClinicalScorer.news2(vitals)
            # Map risk_level to alert_level
            if news_res["score"] >= 7:
                alert = "CRITICAL"
            elif news_res["score"] >= 5:
                alert = "WARNING"
            elif news_res["score"] >= 1:
                alert = "INFO"
            else:
                alert = "BACKGROUND"
            
            scores.append({
                "scorer": "news2",
                "score_result": news_res,
                "alert_level": alert,
                "source_guideline": news_res["source_guideline"]
            })
        except ValueError:
            pass
            
    # Check for eGFR
    clinical_params = patient_state.get("clinical_params") or {}
    labs = patient_state.get("latest_labs_by_type") or {}
    
    creatinine_lab = labs.get("creatinine")
    creatinine = creatinine_lab["value"] if creatinine_lab else clinical_params.get("creatinine")
    age = clinical_params.get("age")
    sex = clinical_params.get("sex")
    
    if creatinine and age and sex:
        try:
            egfr_res = ClinicalScorer.egfr_ckd_stage(float(creatinine), int(age), sex)
            alert = "INFO"
            if float(egfr_res["egfr"]) < 30:
                alert = "WARNING"
            elif float(egfr_res["egfr"]) < 45:
                alert = "INFO"
            else:
                alert = "BACKGROUND"
                
            scores.append({
                "scorer": "egfr",
                "score_result": egfr_res,
                "alert_level": alert,
                "source_guideline": egfr_res["source_guideline"]
            })
        except ValueError:
            pass

    # Check WHO HIV Stage
    active_conditions = patient_state.get("active_conditions", [])
    if "hiv" in [c.lower() for c in active_conditions]:
        cd4_lab = labs.get("cd4")
        cd4 = cd4_lab["value"] if cd4_lab else clinical_params.get("cd4_count")
        
        # We need clinical features, maybe from diagnoses or active_conditions
        clinical_features = []
        diagnoses = patient_state.get("active_diagnoses", [])
        for d in diagnoses:
            clinical_features.append(d.get("condition_name", ""))
            
        try:
            who_res = ClinicalScorer.who_hiv_stage(clinical_features, float(cd4) if cd4 else None)
            if who_res["stage"] >= 3:
                alert = "WARNING"
            else:
                alert = "INFO"
                
            scores.append({
                "scorer": "who_hiv_stage",
                "score_result": who_res,
                "alert_level": alert,
                "source_guideline": who_res["source_guideline"]
            })
        except ValueError:
            pass
            
    # Check Malaria
    if "malaria" in [c.lower() for c in active_conditions] or any("malaria" in d.get("condition_name", "").lower() for d in patient_state.get("active_diagnoses", [])):
        lab_dict = {k: v["value"] for k, v in labs.items()}
        try:
            mal_res = ClinicalScorer.malaria_severity(vitals, lab_dict, clinical_params)
            if mal_res["is_severe"]:
                alert = "CRITICAL"
            else:
                alert = "INFO"
                
            scores.append({
                "scorer": "malaria_severity",
                "score_result": mal_res,
                "alert_level": alert,
                "source_guideline": mal_res["source_guideline"]
            })
        except ValueError:
            pass

    return scores
