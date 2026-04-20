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

