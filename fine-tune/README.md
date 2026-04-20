# LLM Fine Tuning

# Guide: Fine-Tuning a Local LLM for AI Newsletter Curation

This guide outlines the end-to-end technical process of fine-tuning a Large Language Model (LLM) to serve as an automated editorial assistant for an AI-focused newsletter. By the end of this process, the model should handle technical summarization, style alignment, and content filtering with higher precision than a generic "off-the-shelf" model.

---

## 1. Project Objectives
* **Style Alignment:** Transform dry technical papers into engaging, concise newsletter entries.
* **Format Enforcement (JSON-only):** Ensure 100% reliability in **structured JSON output only** (no Markdown, no prose, no code fences).
* **Editorial Judgment:** Use preference optimization to filter "signal from noise" based on your specific curation history.

### Required Output Schema (must be valid JSON)

The model output must be **one JSON object** with **separate fields** for the article summary and the author.

**Required keys**
- `summary` (string): the newsletter-ready summary of the article
- `author` (string): the article author (use an empty string if unknown)

**Example**
```json
{
  "summary": "Researchers introduce a new fine-tuning method that reduces memory use while preserving instruction-following. The approach combines quantization with low-rank adapters and achieves comparable quality to full fine-tuning on standard benchmarks.",
  "author": "Jane Doe"
}
```

---

## 2. Phase 1: Data Architecture
Fine-tuning is 80% data engineering. Since you have an existing app, your database is your primary asset.

### Supervised Fine-Tuning (SFT) Dataset
For SFT, you need "Instruction-Response" pairs. 
* **Input:** The raw text or scraped content of an AI article.
* **Output:** The final, edited summary you actually published in your newsletter.
* **Volume:** Aim for 100–500 high-quality pairs. 

**Format (JSONL):**
```json
{"instruction": "Summarize this AI research for a technical audience.", "input": "RAW_TEXT_HERE", "output": {"summary": "LLM CURATED SUMMARY HERE", "author": "Jane Doe" }}
```

---

## 2.1 Plan documents (recommended reading order)

The fine-tuning plan is split into focused documents:

- [`fine-tune/01-data-sources-and-schemas.md`](01-data-sources-and-schemas.md): what exists in `data/state.db` today, what’s missing, and target schemas
- [`fine-tune/02-bootstrapped-sft-export.md`](02-bootstrapped-sft-export.md): how to bootstrap SFT data from existing runs (top `TOP_N` only)
- [`fine-tune/03-feedback-loop-and-dpo.md`](03-feedback-loop-and-dpo.md): upvote/downvote + note + edits, and how to export DPO/SFT-from-edits
- [`fine-tune/04-training-workflows-mlx.md`](04-training-workflows-mlx.md): training workflow (SFT then DPO) on Apple Silicon with MLX
- [`fine-tune/05-deploy-to-llamacpp.md`](05-deploy-to-llamacpp.md): produce a GGUF and run it behind llama.cpp (the backend your app uses)

---

## 3. Local Training Stack

You can fine-tune locally on a **24GB unified-memory M2 MacBook Air**, but you’ll want to use the **Apple Silicon (MLX) ecosystem** rather than the standard Linux/NVIDIA toolchain.

Fine-tuning is more memory-intensive than inference because training needs additional memory for activations, gradients, and optimizer state.

- **System reality (unified memory)**:
  - **macOS overhead**: expect ~4–6GB used by the OS.
  - **8B model in 4-bit**: ~5–6GB for weights (ballpark).
  - **training overhead**: often +8–12GB depending on settings.
  - **tip**: close Chrome / heavy apps to avoid swap (swap = slow).

- **Recommended framework**: **MLX** (Apple Silicon-native). Start with `mlx-lm` (LoRA / QLoRA workflows).

- **Model targets**:
  - **Best iteration speed**: 3B–7B models.
  - **Still reasonable on 24GB**: 7B/8B in **4-bit** with conservative training settings.

- **Training strategy**: **LoRA / QLoRA (4-bit)** to keep memory usage in range.

- **Practical limits for 24GB (recommended starting values)**:
  - **quantization**: 4-bit
  - **batch size**: 1 (or 2 if stable)
  - **LoRA rank (r)**: 8 or 16
  - **max sequence length**: 512 or 1024 to start
  - **thermals (MacBook Air is fanless)**: expect throttling on long runs; use a stand and/or external airflow

---

## 4. Implementation Plan

### Step 1: Set up the environment

This setup is for **Apple Silicon + MLX** (not CUDA).

```bash
python -m pip install --upgrade pip
pip install mlx-lm
```

### Step 2: Run SFT (teach “voice” + format)

The goal here is to teach the model your specific "voice."

- **Start with conservative settings** (avoid swap / instability): batch size 1, max seq length 512–1024, LoRA r=8 or r=16.
- **Train with MLX LoRA (example)**:

```bash
python -m mlx_lm.lora \
  --model mlx-community/Meta-Llama-3-8B-Instruct-4bit \
  --data ./your_newsletter_data/ \
  --train \
  --iters 600 \
  --batch-size 1
```

- **Monitor quality, not just loss**: run a small fixed set of articles after training and compare outputs to your published summaries.

### Step 3: Run DPO alignment (teach editorial preference)

Once the model knows how to write, use DPO to teach it what to write about.

- **Load the SFT checkpoint**: Start from the model trained in Step 2.
- **Set a reference model**: Keep a frozen copy for comparison during optimization.
- **Optimize preferences**: Reward “Chosen” summaries and penalize “Rejected” ones so ranking aligns to your curation history.

---

## 5. Evaluation & Iteration
Fine-tuning is iterative. You cannot rely solely on automated metrics like Loss or ROUGE scores.

- **Vibe check**: Run ~10 raw articles through the new model and compare outputs to your previous manual summaries.

- **Negative constraints**: Confirm it respects constraints (e.g., “Do not use emojis,” “Do not mention pricing”).

- **Catastrophic forgetting check**: Ensure it still handles basic reasoning / general tasks and hasn’t become overly specialized.

---

## 6. Deployment to Your App
Once satisfied, export the model.

- **GGUF export**: For local inference (Ollama / LM Studio), export to GGUF.

- **Integration**: Point your app’s backend to the local inference server.

- **Active learning loop**: Add “Thumbs Up/Down” in your dashboard. Each reject + rewrite becomes training data for the next run.

---

## 7. Summary Checklist
- [ ] Export 200+ article/summary pairs from your database.
- [ ] Install `mlx-lm` and verify your MLX environment works on Apple Silicon.
- [ ] Close heavy apps to avoid swap (swap will slow training).
- [ ] Run SFT (LoRA/QLoRA) with conservative settings (batch size 1; seq 512–1024; r=8–16).
- [ ] Run DPO to align the model with your editorial filtering logic.
- [ ] Quantize to 4-bit GGUF and integrate back into your local app.
