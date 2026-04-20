# Deploying the fine-tuned model to llama.cpp (GGUF)

You run inference locally via llama.cpp’s OpenAI-compatible server (`/v1/chat/completions`). This doc describes the *deployment target*: a **GGUF** model that llama.cpp can load.

---

## Your app’s integration point (already exists)

The backend already supports llama.cpp via:

- `LLM_PROVIDER=local`
- `LOCAL_LLM_URL=http://localhost:8080` (or your port)
- `LOCAL_LLM_MODEL=<model name>` (string sent in the OpenAI-style request)

So the only “deployment” requirement is: **produce a GGUF** and run llama.cpp server with it.

---

## What you need to produce for llama.cpp

llama.cpp loads models from a GGUF file, typically quantized (e.g. `Q4_K_M`).

After training (SFT/DPO), ensure you can export a final set of weights in a format convertible to GGUF (commonly Hugging Face format), then convert to GGUF and quantize.

---

## Conversion pipeline (conceptual)

1. **Train** (SFT, optionally then DPO) to get your tuned weights (or base + adapter).
2. **Merge adapters** (if you used LoRA) into a single set of weights you want to deploy.
3. **Convert to GGUF** using llama.cpp conversion tooling.
4. **Quantize** the GGUF to your target (e.g. `Q4_K_M`) for speed/memory.
5. **Serve** with llama.cpp’s OpenAI-compatible server.

Notes:

- If you keep adapters separate, llama.cpp support depends on how you deploy. The simplest operational path is a **merged GGUF**.
- Exact commands depend on your training artifacts (MLX vs HF, LoRA adapter layout). This plan assumes you’ll choose/standardize an export format that can be converted by llama.cpp.

---

## Serving checklist

- Confirm the GGUF loads in llama.cpp.
- Start the server exposing `/v1/chat/completions`.
- Set in your app:
  - `LLM_PROVIDER=local`
  - `LOCAL_LLM_URL=...`
  - `LOCAL_LLM_MODEL=...` (match what your server expects)

