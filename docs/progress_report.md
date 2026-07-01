# Local LLM Cluster & Claude Code Integration Progress Report

> Historical report imported on 2026-07-01. It records earlier single-node
> model tests and the first distributed inference diagnosis. For current status,
> corrected configuration, and later verification, use
> [current-progress-ko.md](current-progress-ko.md).

This report summarizes the current status, verification tests, and next steps for the 4-node Mac mini local LLM cluster running `exo` and its integration with Claude Code.

---

## 1. Model Verification & Local Execution Tests
We verified the completeness of all downloaded model weights on `node1` and ran direct execution tests using `mlx_lm` to verify functional correctness.

| Model ID | Size (GB) | Format | Status | Test Result (Single Node) |
| :--- | :--- | :--- | :--- | :--- |
| **`mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq`** | 19.7 GB | 4.4-bit MLX | **100% Complete** (0 missing) | **PASS** (Fast local text generation) |
| **`mlx-community/Qwen3.6-35B-A3B-4bit`** | 19.0 GB | 4.0-bit MLX | **100% Complete** (0 missing) | **PASS** (Fast local text generation) |
| **`mlx-community/gemma-4-31b-it-4bit`** | 17.1 GB | 4.0-bit MLX | **100% Complete** (0 missing) | **PASS** (Fast local text generation) |
| **`mlx-community/DeepSeek-R1-Distill-Qwen-32B-abliterated`** | 61.0 GB | 16-bit MLX | **100% Complete** (0 missing) | **OOM** (Out of memory on 1 node) |

### Key Takeaways:
1. **4-Bit Models:** All 4-bit models (Qwen 3.6, Huihui Qwen 3.6, Gemma 4) are **fully functional** and run perfectly on a single Mac mini.
2. **DeepSeek 16-Bit:** The DeepSeek-R1 model weights are fully downloaded, but loading them on a single node causes a Metal Out of Memory (`kIOGPUCommandBufferCallbackErrorOutOfMemory`) because the unquantized weights (61 GB) exceed the available Unified Memory (64 GB total, but ~26 GB free).
   - *Recommendation:* Quantize this model to 4-bit or run it sharded once the multi-node distributed communication is active.

---

## 2. Cluster Distributed Mode & applied Code Fixes
We resolved a major background crash bug and analyzed the distributed execution behavior:

### applied Bug Fix:
* **Issue:** macOS system profiler calls for Thunderbolt monitoring were timing out on some nodes (taking >30s), causing `anyio.fail_after` to raise `CancelledError`. Since `CancelledError` inherits from `BaseException` (not `Exception`), it was not caught by `except Exception:` and crashed the daemon loop.
* **Fix:** We modified [info_gatherer.py](../src/exo/utils/info_gatherer/info_gatherer.py) to use `anyio.move_on_after` instead, which handles timeouts silently and logs a warning instead of raising thread-cancelling errors. The update was successfully synced and restarted across all nodes.

### Distributed Ring/Pipeline Inference block:
* **Status:** All 4 nodes connect and register a fully connected clique in `/state/topology`.
* **Blocker:** Multi-node text generation currently hangs at the initial synchronization barrier (`mx_barrier`).
* **Root Cause:** The nodes have multiple active IP networks (WiFi management `10.80.118.x`, Thunderbolt bridges `10.0.0.x`, and virtual interfaces like tunnels/VPNs). The `exo` placement algorithm extracts the wrong/mismatched IP addresses (e.g. assigning a dummy tunnel IP like `198.51.100.1` to `node4`'s coordinator slot), causing the MLX distributed backend group initialization to fail to bind or connect.

---

## 3. Claude Code Integration & Account Management
To integrate Claude Code with your local cluster, use the environment variables below.

> The examples below are historical. The current repository uses the exo API
> root as `ANTHROPIC_BASE_URL`; see
> [cluster-plan-ko.md](cluster-plan-ko.md#8-claude-code-연동) before applying
> these settings.

### Local Execution (on `node1`):
If you want to run Claude Code locally on the cluster master:
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:52415/v1"
export ANTHROPIC_API_KEY="x"
export ANTHROPIC_MODEL="mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq"
claude
```

### Remote Execution (from Windows Client via Tunnel):
If you are connecting from Windows PowerShell, you need to bypass the web login layer of your proxy.
1. **Expose raw API:** Run the Cloudflare tunnel script on the master node to get a raw HTTPS endpoint without the login UI redirect:
   ```bash
   ./scripts/start_cloudflare_tunnel.sh
   ```
2. **Windows Client Configuration:**
   ```powershell
   $env:ANTHROPIC_BASE_URL="https://your-cf-tunnel-subdomain.trycloudflare.com/v1"
   $env:ANTHROPIC_API_KEY="x"
   $env:ANTHROPIC_MODEL="mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq"
   claude
   ```
