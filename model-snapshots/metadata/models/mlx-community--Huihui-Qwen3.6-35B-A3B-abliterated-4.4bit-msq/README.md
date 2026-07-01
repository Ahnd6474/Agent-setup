---
library_name: transformers
license: apache-2.0
license_link: https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/main/LICENSE
pipeline_tag: image-text-to-text
base_model:
- Qwen/Qwen3.6-35B-A3B
tags:
- abliterated
- uncensored
---

# Huihui-Qwen3.6-35B-A3B-abliterated — MLX 4.4 BPW

Mixed-precision MLX quantization of [`huihui-ai/Huihui-Qwen3.6-35B-A3B-abliterated`](https://huggingface.co/huihui-ai/Huihui-Qwen3.6-35B-A3B-abliterated), quantized with MLX Smart Quantize (MSQ) — my own sensitivity-based mixed-precision quantization method for Apple Silicon. It measures per-layer NMSE and assigns optimal bit widths automatically, combining architecture knowledge with measured data.

## Details

- **Type:** Vision (VLM)
- **Average:** 4.39 bits per weight
- **Method:** MLX Smart Quantize (MSQ)
- **AWQ scaling:** applied to 50 groups

## Evaluation

| Benchmark | Score | Samples |
|-----------|-------|---------|
| MMLU | 82.1% | 285 |
| HellaSwag | 91.5% | 200 |
| GSM8K | 86.5% | 200 |
