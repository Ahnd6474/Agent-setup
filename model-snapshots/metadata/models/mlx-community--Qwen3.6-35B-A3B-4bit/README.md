---
library_name: mlx
license: apache-2.0
license_link: https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/main/LICENSE
pipeline_tag: image-text-to-text
tags:
- mlx
base_model: Qwen/Qwen3.6-35B-A3B
---

# mlx-community/Qwen3.6-35B-A3B-4bit

This model was converted to MLX format from [`Qwen/Qwen3.6-35B-A3B`](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
using mlx-vlm version **0.4.4**.
Refer to the [original model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) for more details on the model.

## Use with mlx

```bash
pip install -U mlx-vlm
```

```bash
python -m mlx_vlm.generate --model mlx-community/Qwen3.6-35B-A3B-4bit --max-tokens 100 --temperature 0.0 --prompt "Describe this image." --image <path_to_image>
```
