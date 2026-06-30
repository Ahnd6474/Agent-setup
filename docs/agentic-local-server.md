# Agentic Local Server

This server keeps node1 responsible for API, IO, session storage, and sandbox
workspaces. The local LLM backend is reached through node1's OpenAI-compatible
API, while exo can place actual inference on node2-node4.

## Topology

- node1: agent server, Git-backed session store, temporary workspaces, exo API
- node2-node4: LLM inference workers through exo placement
- store: `~/.agentic-local/store`
- workspaces: `~/.agentic-local/workspaces/<session_id>/<run_id>`

## Start

```bash
cd /Users/dshs_llm/exo
AGENTIC_LLM_BASE_URL=http://127.0.0.1:52415/v1 \
AGENTIC_LLM_MODEL=<local-model-id> \
uv run --package exo-tools agent-server
```

## Coding run request

```json
{
  "prompt": "Fix the failing test",
  "source_dir": "/Users/dshs_llm/exo",
  "target": {
    "connect_type": "line"
  },
  "sandbox": {
    "max_workspace_bytes": 5000000000,
    "environment_profile": "full",
    "create_venv": true,
    "install_packages": false
  },
  "limits": {
    "timeout_seconds": 1200,
    "max_tool_iterations": 40
  }
}
```

The run manifest records:

- `target.server_node=node1`
- `target.io_node=node1`
- `target.llm_inference_nodes=["node2","node3","node4"]`
- `sandbox.write_scope=workspace_only`
- `llm_backend.routing=node1_api_io_with_worker_inference`

## Sandbox policy

The first implementation uses a copied workspace, not a host-level sandbox.
The copy excludes `.git`, virtualenvs, build folders, node modules, caches, and
large model files such as `.gguf` and `.safetensors`. Tool writes are expected
to stay inside the workspace; final changes are exported as `result.patch`.

## Sandbox environment profiles

The sandbox has a reproducible environment layer. It uses shared venvs under
`~/.agentic-local/envs/<profile>` and writes per-run metadata under
`<workspace>/.agentic/`.

Profiles:

- `coding`: `pytest`, `ruff`, `mypy`, `httpx`, `pydantic`
- `document`: `python-docx`, `pypdf`, `pdfplumber`, `reportlab`, `openpyxl`,
  `python-pptx`, `pillow`, `markdown`, `beautifulsoup4`, `lxml`
- `ocr`: `pillow`, `pytesseract`, `opencv-python-headless`, `pdf2image`,
  `numpy`
- `korean_document`: `jakal-hwpx`, `hwp-hwpx-parser`, `python-hwpx`,
  `olefile`,
  `beautifulsoup4`, `lxml`, `python-docx`, `pypdf`, `pdfplumber`,
  `pillow`, `pytesseract`
- `research`: `httpx`, `beautifulsoup4`, `lxml`, `markdownify`,
  `duckduckgo-search`
- `full`: union of coding, document, OCR, Korean document, and research

System tools are inventoried in the run metadata. Expected tools include
`git`, `rg`, `python3`, `curl`, and optionally `pdftotext`, `pandoc`, and
`tesseract`, `pdftoppm` for document/OCR work.

By default `install_packages=false`, so the server creates the venv and records
requirements without doing network installs. Set `install_packages=true` on a
trusted run to install the profile requirements into the shared venv.

For Korean office documents, prefer `environment_profile="korean_document"`.
HWP/HWPX is handled with `jakal-hwpx` as the primary processor because it
supports reading, editing, and writing both HWPX and HWP through a document
model. XML-level parsing with `lxml` is only a fallback for low-level inspection,
not the main path. OCR remains a fallback for scanned PDFs/images.
