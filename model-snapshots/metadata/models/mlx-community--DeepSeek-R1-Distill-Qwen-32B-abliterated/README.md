---
base_model: huihui-ai/DeepSeek-R1-Distill-Qwen-32B-abliterated
library_name: transformers
tags:
- abliterated
- uncensored
- mlx
---

# mlx-community/DeepSeek-R1-Distill-Qwen-32B-abliterated

The Model [mlx-community/DeepSeek-R1-Distill-Qwen-32B-abliterated](https://huggingface.co/mlx-community/DeepSeek-R1-Distill-Qwen-32B-abliterated) was
converted to MLX format from [huihui-ai/DeepSeek-R1-Distill-Qwen-32B-abliterated](https://huggingface.co/huihui-ai/DeepSeek-R1-Distill-Qwen-32B-abliterated)
using mlx-lm version **0.21.1**.

## Use with mlx

```bash
pip install mlx-lm
```

```python
from mlx_lm import load, generate

model, tokenizer = load("mlx-community/DeepSeek-R1-Distill-Qwen-32B-abliterated")

prompt = "hello"

if tokenizer.chat_template is not None:
    messages = [{"role": "user", "content": prompt}]
    prompt = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True
    )

response = generate(model, tokenizer, prompt=prompt, verbose=True)
```
