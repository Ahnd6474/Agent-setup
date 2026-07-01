# Model snapshot

`manifest.json` records every file currently present under `~/models` and
`~/llm-models`, including incomplete downloads, sizes, timestamps, and inferred
Hugging Face repository identifiers.

Small model configuration and tokenizer files are copied under `metadata/`.
Model weights remain in Hugging Face or local storage. They are not copied into
this repository because the local set is roughly 178 GB and includes individual
files larger than GitHub LFS permits.

Regenerate the inventory with:

```bash
python3 scripts/generate_model_manifest.py \
  model-snapshots/manifest.json \
  "$HOME/models" "$HOME/llm-models"
```
