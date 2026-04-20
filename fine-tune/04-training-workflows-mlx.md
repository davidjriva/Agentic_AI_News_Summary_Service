# Training workflows (MLX): SFT then DPO

This document outlines the practical training sequence on Apple Silicon using MLX.

---

## Recommended sequence

- **SFT first**: lock in structure, voice, and “newsletter-ready” completeness.
- **DPO next**: train editorial preference (what to include / what to reject).

Rationale:

- DPO works best when both chosen and rejected outputs are already in the “right shape.”

Deployment constraint (your environment):

- You run inference behind **llama.cpp**. Plan on exporting a final **GGUF** and serving it via llama.cpp’s OpenAI-compatible API. See `fine-tune/05-deploy-to-llamacpp.md`.

---

## SFT (LoRA/QLoRA) using `mlx-lm`

Install:

```bash
python -m pip install --upgrade pip
pip install mlx-lm
```

Example (from your README; adjust paths to your exported JSONL):

```bash
python -m mlx_lm.lora \
  --model mlx-community/Meta-Llama-3-8B-Instruct-4bit \
  --data ./your_newsletter_data/ \
  --train \
  --iters 600 \
  --batch-size 1
```

---

## DPO (preference optimization)

Once you have:

- SFT checkpoint
- preference dataset (draft vs edited is best)

Run DPO using the MLX tooling you choose next (exact command depends on dataset format and MLX implementation used).

---

## Evaluation loop

- Keep a small fixed eval set of URLs/examples.
- Validate:
  - strict JSON output
  - no bullets / no line breaks if that’s the requirement
  - summaries are specific and non-boilerplate

---

## Subagent tasks (implementation-ready)

### Task TR1 — Define a reproducible “golden eval set”

- **Deliverable**: `fine-tune/data/eval_urls.txt` (or similar) listing ~20 stable URLs
- **Work**:
  - Pick URLs from recent runs that represent:
    - arXiv abstracts
    - company blogs
    - journalism
    - HN discussions
  - Store expected output format checks (JSON validity, required keys)

### Task TR2 — Add an automated eval runner

- **Deliverable**: `fine-tune/eval.py` (or a Makefile target) that:
  - loads the current llama.cpp model endpoint
  - runs the eval URLs through the summarization prompt
  - validates strict JSON and required keys
- **Acceptance**:
  - Fails fast if any response is non-JSON or missing keys
  - Produces a small report file for before/after comparisons


