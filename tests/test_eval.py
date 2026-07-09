from scripts.eval_prompts import score_against_golden


def test_mae_and_gate_agreement_perfect_match():
    golden = [
        {"url": "u1", "relevance_score": 9, "impact_score": 8, "authenticity_score": 7},
        {"url": "u2", "relevance_score": 3, "impact_score": 4, "authenticity_score": 5},
    ]
    scored = [dict(g) for g in golden]
    result = score_against_golden(golden, scored, threshold=6)
    assert result["relevance_mae"] == 0.0
    assert result["impact_mae"] == 0.0
    assert result["authenticity_mae"] == 0.0
    assert result["gate_agreement"] == 1.0


def test_mae_computes_absolute_error():
    golden = [{"url": "u1", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8}]
    scored = [{"url": "u1", "relevance_score": 6, "impact_score": 10, "authenticity_score": 8}]
    result = score_against_golden(golden, scored, threshold=6)
    assert result["relevance_mae"] == 2.0
    assert result["impact_mae"] == 2.0
    assert result["authenticity_mae"] == 0.0


def test_gate_agreement_counts_threshold_side():
    # golden relevance 8 (pass), scored 4 (fail) -> disagreement
    golden = [{"url": "u1", "relevance_score": 8, "impact_score": 5, "authenticity_score": 5}]
    scored = [{"url": "u1", "relevance_score": 4, "impact_score": 5, "authenticity_score": 5}]
    result = score_against_golden(golden, scored, threshold=6)
    assert result["gate_agreement"] == 0.0


def test_empty_golden_and_scored_no_zero_division_error():
    """Empty golden and scored should return all-zero metrics without ZeroDivisionError."""
    golden = []
    scored = []
    result = score_against_golden(golden, scored)
    assert result["relevance_mae"] == 0.0
    assert result["impact_mae"] == 0.0
    assert result["authenticity_mae"] == 0.0
    assert result["gate_agreement"] == 0.0


def test_golden_url_not_in_scored():
    """A golden entry whose url is not in scored should be skipped gracefully."""
    golden = [{"url": "u1", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8}]
    scored = [{"url": "u2", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8}]
    result = score_against_golden(golden, scored)
    # u1 not found in scored, so no comparisons, all metrics are 0
    assert result["relevance_mae"] == 0.0
    assert result["impact_mae"] == 0.0
    assert result["authenticity_mae"] == 0.0
    assert result["gate_agreement"] == 0.0


def test_scored_entry_missing_url():
    """A scored entry missing 'url' should be skipped, no KeyError."""
    golden = [{"url": "u1", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8}]
    scored = [
        {"relevance_score": 8, "impact_score": 8, "authenticity_score": 8},  # missing url
        {"url": "u1", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8},
    ]
    result = score_against_golden(golden, scored)
    # The entry without url is skipped in by_url dict, but u1 is there and matches
    assert result["relevance_mae"] == 0.0
    assert result["impact_mae"] == 0.0
    assert result["authenticity_mae"] == 0.0
    assert result["gate_agreement"] == 1.0


def test_golden_entry_missing_relevance_score():
    """A golden entry missing 'relevance_score' should not contribute to gate_agreement."""
    golden = [
        {"url": "u1", "impact_score": 8, "authenticity_score": 8},  # missing relevance_score
        {"url": "u2", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8},
    ]
    scored = [
        {"url": "u1", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8},
        {"url": "u2", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8},
    ]
    result = score_against_golden(golden, scored)
    # u1 has no relevance_score in golden, so it's excluded from gate agreement
    # u2 matches perfectly, so gate_agreement = 1/1 = 1.0
    assert result["gate_agreement"] == 1.0
