# 🚀 Agent-setup: 4-Node Mac mini AI Cluster

This repository contains the deployment configurations, scripts, and documentation for running the 4-node Apple Silicon Mac mini AI cluster using **exo** (distributed inference backend) and **Exodus** (user agent chat application).

---

## 🇰🇷 빠른 시작 가이드 (Quick Start - Korean)

본 클러스터는 4대의 Mac mini를 하나로 묶어 로컬 단독 추론 및 분배 처리를 수행하도록 최적화되어 있습니다.

### 1. 클러스터 구동 순서
1. **설정 확인:** `scripts/cluster.env` 파일에 네트워크 및 노드 IP가 맞게 지정되어 있는지 확인합니다.
2. **클러스터 기동:** Master 노드(`node1`) 터미널에서 다음 스크립트를 가동하여 노드 간의 SSH 통신 및 exo 백그라운드 인스턴스를 띄웁니다.
   ```bash
   scripts/start_4node_exo_cluster.sh
   ```
3. **웹 채팅 서비스(Exodus) 실행:**
   ```bash
   scripts/start_agent_server.sh
   ```
   이후 브라우저에서 `http://node1:8765`로 접속하여 사용할 수 있습니다.

### 2. 주요 문서 링크 (Documentation)
* 💡 [쉽고 직관적인 사용자 및 운영 매뉴얼 (Markdown)](docs/user-manual-ko.md)
* 📕 [쉽고 직관적인 사용자 및 운영 매뉴얼 (PDF)](docs/user-manual-ko.pdf)
* 📑 [4노드 물리 클러스터 운영 상세 계획서](docs/cluster-plan-ko.md)
* 📝 [현재 런타임 진행 상황 및 검증 보고서](docs/current-progress-ko.md)

---

## 📐 Cluster Architecture & Ports

The architecture separates the **user interaction layer** (Exodus) from the **distributed inference layer** (exo) to maximize memory efficiency and inference speeds.

```
                    ┌─────────────────────────┐
                    │      Client Browser     │
                    │   http://node1:8765     │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Exodus User Web App   │
                    └────────────┬────────────┘
                                 │ (Internal API call)
                                 ▼
                    ┌─────────────────────────┐
                    │    exo Control Plane    │
                    │   http://node1:52415    │
                    └────────────┬────────────┘
                                 │ (Job Routing)
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ node2 (Worker 1) │    │ node3 (Worker 2) │    │ node4 (Worker 3) │
│  Local Inference │    │  Local Inference │    │  Local Inference │
└──────────────────┘    └──────────────────┘    └──────────────────┘
```

### Port Allocation Map

| Port | Service | Role / Description |
|---|---|---|
| **`8765`** | **Exodus Agent App** | User chat UI, session persistence, auth, database, and Python tool sandbox. |
| **`52415`** | **exo Control Plane** | Cluster monitoring, model downloads, device placement coordinator, and API router. |
| **`52416`** | **exo Transport** | Internal libp2p node-to-node synchronization and communication. |
| **`52417`** | **Standalone GGUF Backend** | Fast local `llama-server` loading quantized GGUF models (yielding **58.5+ tps**). |

---

## ⚡ Active Local Models

The models are pre-downloaded and verified across all nodes in `/Users/dshs_llm/models` (MLX community models) and `/Users/dshs_llm/llm-models` (GGUF weights).

* **DeepSeek R1 Distill Qwen 32B (4-bit)**
  * MLX Model: `mlx-community/DeepSeek-R1-Distill-Qwen-32B-abliterated-4bit` (~18.4 GB)
  * Optimized for highly complex reasoning tasks, running smoothly within memory limits.
* **Huihui Qwen 3.6 35B (4.4-bit MSQ)**
  * MLX Model: `mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq` (~21.1 GB)
* **Qwen 3.6 35B abliterated (Q4_K_M GGUF)**
  * Local GGUF: Loaded on port `52417` for ultra-fast, uncensored generation.
* **Gemma 4 31B (4-bit)**
  * MLX Model: `gemma-4-31b-it-4bit` (~18.4 GB)
* **Llama 3.2 1B (4-bit)**
  * MLX Model: `mlx-community/Llama-3.2-1B-Instruct-4bit` (used for fast session-title generation)

---

## 🚧 Work In Progress Features

Some experimental features are currently under active development. Ensure you check their status in [docs/current-progress-ko.md](docs/current-progress-ko.md) before deployment.

### 1. Google OAuth Account Sync (WIP)
Google OAuth login flows are currently in development. Standard accounts are created locally via `agent-account bootstrap` or through the `/admin` user-management console.

### 2. Claude Code Integration (WIP)
Direct backend connection to the terminal coding assistant **Claude Code** is undergoing local testing. If you wish to test it on the cluster, configure your terminal as follows:
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:52415"
export ANTHROPIC_API_KEY="x"
export ANTHROPIC_DEFAULT_OPUS_MODEL="mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq"
export ANTHROPIC_DEFAULT_SONNET_MODEL="mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq"
export API_TIMEOUT_MS=3000000
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

claude
```

---

## 🛠️ Cluster Troubleshooting (Q&A)

#### Q. `No instance found for model` error is raised in the browser.
* **Cause:** The model name requested does not match the active model instance loaded on `52415`.
* **Resolution:** Re-execute the placement coordinator script:
  ```bash
  scripts/place_rdma_instance.sh
  ```

#### Q. Worker nodes are missing in the dashboard.
* **Cause:** Physical Thunderbolt links are down or SSH handshake timed out.
* **Resolution:** Change `CONNECT_TYPE=line` to `CONNECT_TYPE=net` in `scripts/cluster.env` to bypass the Thunderbolt interface and use the standard local LAN network. Re-run `scripts/start_4node_exo_cluster.sh`.
