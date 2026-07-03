"""
tests/test_vitals_trend.py

Improvement 4 — tests for NEWS2 trend alerting and GCS trend alerting on
vitals write.

Tests confirm:
- NEWS2: Prior score 3, new score 6 -> trend = "rapid_deterioration", delta = 3
- NEWS2: Prior score 5 (medium), new score 7 (high) -> crossed_risk_boundary = True
- NEWS2: Prior score 7, new score 4 -> trend = "improving"
- NEWS2: No prior vitals -> trend = "no_prior_data", no error
- GCS: Prior 15, new 9 -> rapid_deterioration, crossed_severity_threshold True (mild→severe)
- GCS: Prior 9, new 7 -> crossed_severity_threshold True (moderate→severe)
- GCS: Prior 7, new 12 -> improving, crossed_severity_threshold False
- GCS: No prior -> no_prior_data, no crash
- GCS: Same score -> stable

Improvement 3.3 — tests for NEWS2 missing fields:
- ScoringError.missing_fields is captured
- add_vitals returns news2_missing_fields in the response
"""

from __future__ import annotations

from app.repositories import (
    _compute_gcs_trend,
    _compute_news2_trend,
    _gcs_severity_band,
    _news2_risk_level,
    _risk_level_index,
)


class TestComputeNews2Trend:
    def test_prior_3_new_6_deteriorating(self):
        """Delta of 3 is exactly >= 3 boundary -> rapid_deterioration."""
        result = _compute_news2_trend(
            new_score=6, new_risk="medium",
            prior_score=3, prior_risk="low",
        )
        assert result["trend"] == "rapid_deterioration"
        assert result["delta"] == 3
        assert result["crossed_risk_boundary"] is True

    def test_prior_5_new_7_crossed_risk_boundary(self):
        """Medium -> high risk level crossing."""
        result = _compute_news2_trend(
            new_score=7, new_risk="high",
            prior_score=5, prior_risk="medium",
        )
        assert result["trend"] == "deteriorating"
        assert result["delta"] == 2
        assert result["crossed_risk_boundary"] is True

    def test_prior_7_new_4_improving(self):
        """Score decreased -> improving."""
        result = _compute_news2_trend(
            new_score=4, new_risk="low",
            prior_score=7, prior_risk="high",
        )
        assert result["trend"] == "improving"
        assert result["delta"] == -3
        assert result["crossed_risk_boundary"] is False

    def test_no_prior_vitals(self):
        """No prior score -> no_prior_data."""
        result = _compute_news2_trend(
            new_score=5, new_risk="medium",
            prior_score=None, prior_risk=None,
        )
        assert result["trend"] == "no_prior_data"
        assert result["delta"] is None
        assert result["prior_news2"] is None
        assert result["new_news2"] == 5
        assert result["crossed_risk_boundary"] is False

    def test_prior_score_none_no_crash(self):
        """Prior score is None (incomplete NEWS2) -> no_prior_data, no crash."""
        result = _compute_news2_trend(
            new_score=5, new_risk="medium",
            prior_score=None, prior_risk=None,
        )
        assert result["trend"] == "no_prior_data"
        assert result["delta"] is None
        assert result["crossed_risk_boundary"] is False

    def test_new_score_none_no_crash(self):
        """New score is None -> no_prior_data."""
        result = _compute_news2_trend(
            new_score=None, new_risk=None,
            prior_score=5, prior_risk="medium",
        )
        assert result["trend"] == "no_prior_data"
        assert result["delta"] is None

    def test_both_none(self):
        """Both scores None -> no_prior_data."""
        result = _compute_news2_trend(
            new_score=None, new_risk=None,
            prior_score=None, prior_risk=None,
        )
        assert result["trend"] == "no_prior_data"
        assert result["delta"] is None
        assert result["crossed_risk_boundary"] is False

    def test_equal_scores_stable(self):
        """Same score -> stable."""
        result = _compute_news2_trend(
            new_score=3, new_risk="low",
            prior_score=3, prior_risk="low",
        )
        assert result["trend"] == "stable"
        assert result["delta"] == 0
        assert result["crossed_risk_boundary"] is False

    def test_delta_2_is_deteriorating_not_rapid(self):
        """Delta of 2 is >= 1 but < 3 -> deteriorating, not rapid."""
        result = _compute_news2_trend(
            new_score=5, new_risk="medium",
            prior_score=3, prior_risk="low",
        )
        assert result["trend"] == "deteriorating"
        assert result["delta"] == 2

    def test_delta_1_is_deteriorating(self):
        """Delta of 1 -> deteriorating."""
        result = _compute_news2_trend(
            new_score=4, new_risk="low",
            prior_score=3, prior_risk="low",
        )
        assert result["trend"] == "deteriorating"
        assert result["delta"] == 1

    def test_delta_negative_1_is_improving(self):
        """Delta of -1 -> improving."""
        result = _compute_news2_trend(
            new_score=2, new_risk="low",
            prior_score=3, prior_risk="low",
        )
        assert result["trend"] == "improving"
        assert result["delta"] == -1

    def test_no_risk_level_info_no_boundary_crossing(self):
        """If risk levels are not provided, crossed_risk_boundary is False."""
        result = _compute_news2_trend(
            new_score=8, new_risk=None,
            prior_score=6, prior_risk=None,
        )
        assert result["trend"] == "deteriorating"
        assert result["delta"] == 2
        assert result["crossed_risk_boundary"] is False

    def test_low_to_medium_crossing(self):
        """Low -> Medium risk level crossing."""
        result = _compute_news2_trend(
            new_score=6, new_risk="medium",
            prior_score=4, prior_risk="low",
        )
        assert result["crossed_risk_boundary"] is True

    def test_medium_to_low_no_crossing(self):
        """Medium -> Low is a decrease, not a crossing upward."""
        result = _compute_news2_trend(
            new_score=3, new_risk="low",
            prior_score=6, prior_risk="medium",
        )
        assert result["crossed_risk_boundary"] is False

    def test_trend_alert_dict_has_all_required_keys(self):
        """The trend_alert dict always has all keys the field UI expects."""
        result = _compute_news2_trend(
            new_score=5, new_risk="medium",
            prior_score=3, prior_risk="low",
        )
        required_keys = {"trend", "delta", "prior_news2", "new_news2", "crossed_risk_boundary"}
        assert required_keys.issubset(result.keys())


class TestNews2RiskLevel:
    def test_score_7_is_high(self):
        assert _news2_risk_level(7) == "high"
        assert _news2_risk_level(10) == "high"

    def test_score_5_is_medium(self):
        assert _news2_risk_level(5) == "medium"
        assert _news2_risk_level(6) == "medium"

    def test_score_4_is_low(self):
        assert _news2_risk_level(4) == "low"
        assert _news2_risk_level(0) == "low"


class TestRiskLevelIndex:
    def test_low_is_lowest(self):
        assert _risk_level_index("low") == 0

    def test_medium_is_middle(self):
        assert _risk_level_index("medium") == 2

    def test_high_is_highest(self):
        assert _risk_level_index("high") == 3

    def test_unknown_level_returns_0(self):
        assert _risk_level_index("unknown") == 0


# ── GCS severity band helper ──────────────────────────────────────────────

class TestGcsSeverityBand:
    def test_mild_band(self):
        assert _gcs_severity_band(13) == "mild"
        assert _gcs_severity_band(15) == "mild"
        assert _gcs_severity_band(14) == "mild"

    def test_moderate_band(self):
        assert _gcs_severity_band(9) == "moderate"
        assert _gcs_severity_band(12) == "moderate"
        assert _gcs_severity_band(10) == "moderate"

    def test_severe_band(self):
        assert _gcs_severity_band(8) == "severe"
        assert _gcs_severity_band(3) == "severe"
        assert _gcs_severity_band(1) == "severe"
        assert _gcs_severity_band(0) == "severe"


# ── GCS trend computation ────────────────────────────────────────────────

class TestComputeGcsTrend:
    def test_gcs_15_to_9_rapid_deterioration(self):
        """GCS 15 -> 9: delta -6, rapid_deterioration, crossed_severity True (mild→severe)."""
        result = _compute_gcs_trend(new_gcs=9, prior_gcs=15)
        assert result["trend"] == "rapid_deterioration"
        assert result["delta"] == -6
        assert result["crossed_severity_threshold"] is True
        assert result["prior_gcs"] == 15
        assert result["new_gcs"] == 9

    def test_gcs_9_to_7_crossed_severity(self):
        """GCS 9 -> 7: delta -2, deteriorating, crossed_severity True (moderate→severe)."""
        result = _compute_gcs_trend(new_gcs=7, prior_gcs=9)
        assert result["trend"] == "deteriorating"
        assert result["delta"] == -2
        assert result["crossed_severity_threshold"] is True

    def test_gcs_7_to_12_improving_no_crossing(self):
        """GCS 7 -> 12: delta +5, improving, no severity crossing (worsened direction)."""
        result = _compute_gcs_trend(new_gcs=12, prior_gcs=7)
        assert result["trend"] == "improving"
        assert result["delta"] == 5
        assert result["crossed_severity_threshold"] is False

    def test_gcs_no_prior_data(self):
        """No prior GCS -> no_prior_data, no crash."""
        result = _compute_gcs_trend(new_gcs=12, prior_gcs=None)
        assert result["trend"] == "no_prior_data"
        assert result["delta"] is None
        assert result["prior_gcs"] is None
        assert result["new_gcs"] == 12
        assert result["crossed_severity_threshold"] is False

    def test_gcs_new_none_no_crash(self):
        """New GCS is None -> no_prior_data."""
        result = _compute_gcs_trend(new_gcs=None, prior_gcs=12)
        assert result["trend"] == "no_prior_data"
        assert result["delta"] is None

    def test_gcs_both_none(self):
        """Both GCS None -> no_prior_data."""
        result = _compute_gcs_trend(new_gcs=None, prior_gcs=None)
        assert result["trend"] == "no_prior_data"
        assert result["delta"] is None
        assert result["crossed_severity_threshold"] is False

    def test_gcs_same_score_stable(self):
        """Same GCS -> stable, no crossing."""
        result = _compute_gcs_trend(new_gcs=12, prior_gcs=12)
        assert result["trend"] == "stable"
        assert result["delta"] == 0
        assert result["crossed_severity_threshold"] is False

    def test_gcs_delta_neg3_rapid_deterioration(self):
        """Delta of exactly -3 -> rapid_deterioration (boundary case)."""
        result = _compute_gcs_trend(new_gcs=12, prior_gcs=15)
        assert result["trend"] == "rapid_deterioration"
        assert result["delta"] == -3

    def test_gcs_delta_neg2_deteriorating(self):
        """Delta of -2 -> deteriorating (not rapid)."""
        result = _compute_gcs_trend(new_gcs=13, prior_gcs=15)
        assert result["trend"] == "deteriorating"
        assert result["delta"] == -2

    def test_gcs_delta_neg1_deteriorating(self):
        """Delta of -1 -> deteriorating."""
        result = _compute_gcs_trend(new_gcs=14, prior_gcs=15)
        assert result["trend"] == "deteriorating"
        assert result["delta"] == -1

    def test_gcs_delta_positive1_improving(self):
        """Delta of +1 -> improving."""
        result = _compute_gcs_trend(new_gcs=10, prior_gcs=9)
        assert result["trend"] == "improving"
        assert result["delta"] == 1

    def test_gcs_mild_to_moderate_crossing(self):
        """GCS 13 -> 12: mild -> moderate, crossed_severity True."""
        result = _compute_gcs_trend(new_gcs=12, prior_gcs=13)
        assert result["crossed_severity_threshold"] is True

    def test_gcs_moderate_to_mild_no_crossing(self):
        """GCS 9 -> 13: moderate -> mild, no crossing (improved)."""
        result = _compute_gcs_trend(new_gcs=13, prior_gcs=9)
        assert result["crossed_severity_threshold"] is False

    def test_gcs_within_same_band_no_crossing(self):
        """GCS 14 -> 13: both mild, no crossing."""
        result = _compute_gcs_trend(new_gcs=13, prior_gcs=14)
        assert result["crossed_severity_threshold"] is False

    def test_gcs_trend_alert_dict_has_all_required_keys(self):
        """The gcs_trend_alert dict always has all keys the field UI expects."""
        result = _compute_gcs_trend(new_gcs=9, prior_gcs=15)
        required_keys = {"trend", "delta", "prior_gcs", "new_gcs", "crossed_severity_threshold"}
        assert required_keys.issubset(result.keys())


# ── NEWS2 missing fields (Improvement 3.3) ────────────────────────────────

class TestNews2MissingFields:
    def test_scoring_error_has_missing_fields(self):
        """ScoringError must carry a missing_fields list."""
        from app.scoring.scorers import ScoringError, compute_news2
        try:
            compute_news2({"respiratory_rate": 20})  # missing most fields
            assert False, "Expected ScoringError"
        except ScoringError as exc:
            assert len(exc.missing_fields) > 0
            assert isinstance(exc.missing_fields, list)

    def test_scoring_error_missing_consciousness(self):
        """Missing consciousness field is captured in missing_fields."""
        from app.scoring.scorers import ScoringError, compute_news2
        vitals = {
            "respiratory_rate": 20,
            "spo2": 98,
            "bp_systolic": 120,
            "heart_rate": 72,
            "temperature": 36.5,
            # consciousness is missing
        }
        try:
            compute_news2(vitals)
            assert False, "Expected ScoringError"
        except ScoringError as exc:
            assert "consciousness" in exc.missing_fields

    def test_scoring_error_missing_temperature(self):
        """Missing temperature field is captured in missing_fields."""
        from app.scoring.scorers import ScoringError, compute_news2
        vitals = {
            "respiratory_rate": 20,
            "spo2": 98,
            "bp_systolic": 120,
            "heart_rate": 72,
            "consciousness": "A",
            # temperature is missing
        }
        try:
            compute_news2(vitals)
            assert False, "Expected ScoringError"
        except ScoringError as exc:
            assert "temperature" in exc.missing_fields

    def test_add_vitals_imports_scoring_error(self):
        """add_vitals must import ScoringError specifically."""
        import inspect
        from app.repositories import add_vitals
        source = inspect.getsource(add_vitals)
        assert "ScoringError" in source

    def test_add_vitals_catches_scoring_error(self):
        """add_vitals catches ScoringError specifically (not broad ValueError)."""
        import inspect
        from app.repositories import add_vitals
        source = inspect.getsource(add_vitals)
        assert "except ScoringError" in source

    def test_add_vitals_returns_missing_fields_in_response(self):
        """add_vitals must include news2_missing_fields in the returned dict."""
        import inspect
        from app.repositories import add_vitals
        source = inspect.getsource(add_vitals)
        assert "news2_missing_fields" in source

    def test_add_vitals_defaults_missing_fields_to_empty(self):
        """When NEWS2 computes successfully, missing_fields defaults to []."""
        import inspect
        from app.repositories import add_vitals
        source = inspect.getsource(add_vitals)
        assert "news2_missing_fields" in source and "= []" in source
