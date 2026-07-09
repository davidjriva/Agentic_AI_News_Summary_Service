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
