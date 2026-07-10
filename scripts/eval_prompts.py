"""Golden-set evaluation for scoring prompts.

`score_against_golden` is pure logic (unit-tested). `main()` runs the live
pipeline stages against eval/golden_articles.json and prints agreement metrics
— it hits the configured LLM, so only run it when the llama.cpp server is free.

The eval/golden_articles.json fixture is a starter placeholder meant to grow to ~15-20 hand-labeled entries.
"""
import argparse
import json
from pathlib import Path

_GOLDEN_PATH = Path(__file__).parent.parent / "eval" / "golden_articles.json"


def score_against_golden(golden: list[dict], scored: list[dict], threshold: int = 6) -> dict:
    """Compare model scores against hand-labeled golden scores.

    Returns mean absolute error per dimension and gate-decision agreement
    (fraction of articles where model and golden agree on relevance >= threshold).
    """
    by_url = {s["url"]: s for s in scored if "url" in s}
    dims = ("relevance_score", "impact_score", "authenticity_score")
    errors = {d: [] for d in dims}
    gate_hits = 0
    gate_total = 0
    for g in golden:
        g_url = g.get("url")
        if g_url is None:
            continue
        s = by_url.get(g_url)
        if s is None:
            continue
        for d in dims:
            if d in g and g[d] is not None and s.get(d) is not None:
                errors[d].append(abs(g[d] - s[d]))
        if g.get("relevance_score") is not None and s.get("relevance_score") is not None:
            gate_total += 1
            if (g["relevance_score"] >= threshold) == (s["relevance_score"] >= threshold):
                gate_hits += 1

    def _mae(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    return {
        "relevance_mae": _mae(errors["relevance_score"]),
        "impact_mae": _mae(errors["impact_score"]),
        "authenticity_mae": _mae(errors["authenticity_score"]),
        "gate_agreement": gate_hits / gate_total if gate_total else 0.0,
    }


def main() -> None:
    from src import config as _cfg
    from src.processor import triage_articles, score_articles

    parser = argparse.ArgumentParser(description="Evaluate scoring prompts against the golden set")
    parser.add_argument("--threshold", type=int, default=_cfg.RELEVANCE_THRESHOLD)
    args = parser.parse_args()

    golden = json.loads(_GOLDEN_PATH.read_text())
    # Run the real two-stage scoring on the golden articles.
    triaged = triage_articles([{k: a[k] for k in ("url", "title", "description", "author", "publication")} for a in golden])
    scored = score_articles(triaged)
    metrics = score_against_golden(golden, scored, threshold=args.threshold)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
