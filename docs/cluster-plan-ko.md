# 4대 Mac mini exo 클러스터 운영 가이드

> 최종 확인: 2026-06-30
> 기준: 이 저장소의 현재 스크립트, node1의 `scripts/cluster.env`, 실행 중인 exo API

이 문서는 새 클러스터 구축 계획이 아니라 현재 운영 중인 4대 Mac mini 클러스터의
구성, 시작·중지, 모델 배치, API 연동 절차를 설명한다.

## 1. 현재 구성

| 항목 | 현재 값 |
|---|---|
| 노드 | Mac mini 4대 |
| SoC / 메모리 | Apple M4 Pro / 노드당 64 GiB |
| OS | macOS 26.5 (빌드 25F71), 4대 동일 |
| exo API | node1 `http://127.0.0.1:52415` |
| libp2p | TCP 52416, namespace `macmini-rdma-llm` |
| 제어 경로 | node1에서 SSH, 기본 `CONNECT_TYPE=line` |
| 추론 경로 | Thunderbolt RDMA가 포함된 exo 토폴로지 |
| 현재 모델 설정 | `mlx-community/Qwen3.6-35B-A3B-4bit` |
| 현재 배치 | `Pipeline` / `MlxRing`, 최소 4노드 |
| 모델 디렉터리 | `/Users/dshs_llm/models` |

2026-06-30에 `/state/topology`로 4개 노드와 node1↔worker의
`rdma_en*` 링크를 확인했다. 현재 인스턴스는 자동 상주하지 않으며, 클러스터 시작 후
필요한 모델 인스턴스를 별도로 배치한다.

## 2. 구조와 중요한 제약

### 서비스와 포트 경계

| 포트 | 서비스 | 책임 | 공개 범위 |
|---|---|---|---|
| `8765` | Agentic Local Server | 최종 사용자 GUI, 계정, 채팅 세션, 기억, 세션 자원 | 인증된 사용자 진입점 |
| `52415` | exo control plane | 클러스터 모니터링, 모델 다운로드·배치, 인스턴스 관리, inference API·운영 진단 | node1 내부/관리망 |
| `52416` | exo libp2p | master-worker discovery와 노드 통신 | 클러스터 내부망 |
| `8080` | 선택적 llama.cpp backend | 단일 backend inference API | node1 내부 |

`8765`가 사용자 제품이고 `52415`는 모니터링·관리 패널이다. `8765`에서 exo
모델을 사용할 때 요청은 내부적으로 `52415/v1`로 전달된다. `52415` 대시보드는
채팅 UI를 제공하지 않으며 노드별 메모리·디스크·CPU/GPU·온도·전력·RDMA 상태와
모델 인스턴스를 관리한다. 추론 진단은 `52415/v1` API를 직접 호출한다.

```text
최종 사용자
  └─ node1:8765 Agentic 사용자 GUI/API
       └─ node1:52415/v1 (선택한 exo inference backend)

클러스터 운영자·내부 API client
  └─ node1:52415 exo control plane
       ├─ 모니터링·관리 dashboard
       ├─ OpenAI API / Claude Messages API
       ├─ exo master 및 로컬 worker
       └─ node2, node3, node4 worker
```

node1은 API 진입점이면서 추론 노드에도 포함된다. node2~4만 추론하고 node1은
제어만 담당하는 구조가 아니다.

현재 Thunderbolt 배선은 node1 중심의 star 형태다. 모든 노드 사이에 직접 RDMA
링크가 있는 cycle이 아니므로 `Tensor/MlxJaccl`을 강제로 선택하지 않는다.
현재 검증된 기본값은 다음과 같다.

```bash
SHARDING=Pipeline
INSTANCE_META=MlxRing
```

완전 연결 또는 RDMA cycle로 배선을 변경하고 exo placement preview가
`Tensor/MlxJaccl`을 유효한 배치로 반환할 때만 해당 조합을 사용한다.

## 3. 설정 파일

로컬 운영값은 Git에 커밋하지 않는 `scripts/cluster.env`에 둔다.

```bash
cd /Users/dshs_llm/exo
cp scripts/cluster.env.example scripts/cluster.env
```

현재 예시 파일은 실제 구성과 같은 변수 체계를 사용한다.

```bash
MASTER_HOST=127.0.0.1
CONNECT_TYPE=line

WORKER1_LINE_HOST=node2@10.0.0.2
WORKER2_LINE_HOST=node3@10.0.0.3
WORKER3_LINE_HOST=node4@10.0.0.4

NAMESPACE=macmini-rdma-llm
MODEL_ID=mlx-community/Qwen3.6-35B-A3B-4bit
MIN_NODES=4

REPO_DIR=/Users/dshs_llm/exo
MODELS_DIR=/Users/dshs_llm/models
API_HOST=127.0.0.1
API_PORT=52415
LIBP2P_PORT=52416

SHARDING=Pipeline
INSTANCE_META=MlxRing
FAST_SYNCH=true
```

`*_LINE_HOST`는 Thunderbolt 또는 전용 유선 제어 경로이고 `*_NET_HOST`는 일반
LAN fallback이다. 전용 경로에 문제가 있으면 `CONNECT_TYPE=net`으로 바꿔
SSH 제어 경로만 전환할 수 있다.

각 장치는 `MASTER_NODE_NAME=node1`, `WORKER1_NODE_NAME=node2`부터
`WORKER3_NODE_NAME=node4`까지의 고정 표시 이름을 사용한다. libp2p peer ID는
장치별 `~/.exo/node_id.keypair`에 mode `0600`으로 영속화되므로 동일 장치를
재시작해도 node ID가 바뀌지 않는다. 이 파일을 다른 장치에 복사하면 peer ID가
충돌하므로 모델·runtime 동기화 대상에 포함하지 않는다.

## 4. 최초 준비

### 공통 소프트웨어

각 노드에 같은 저장소 revision과 다음 도구가 필요하다.

- Xcode 및 Metal toolchain
- `uv`, Node.js, Rust nightly
- `screen`, `git`, `curl`
- 저장소의 `.venv`

대시보드는 node1에서 빌드한다.

```bash
cd /Users/dshs_llm/exo/dashboard
npm install
npm run build
```

### SSH

각 worker에서 Remote Login을 활성화한다.

```bash
sudo /usr/sbin/systemsetup -setremotelogin on
/usr/sbin/systemsetup -getremotelogin
```

`scripts/bootstrap_macmini_worker.sh`에는 특정 node1 공개키가 포함되어 있으므로
새 클러스터에 그대로 재사용하지 말고 공개키를 먼저 검토한다.

### RDMA

RDMA는 macOS Recovery에서 `rdma_ctl enable`을 실행한 뒤, macOS 부팅 후
각 Thunderbolt 포트를 exo용 DHCP 서비스로 구성해야 한다. 4대의 macOS 버전과
빌드 번호를 동일하게 유지한다.

현재 저장소 래퍼:

```bash
scripts/configure_rdma_network.sh
```

이 명령은 `tmp/set_rdma_network_config.sh`를 실행하며 기존 Thunderbolt Bridge와
네트워크 location을 변경한다. 원격 SSH 경로를 끊을 수 있으므로 물리 접근이 가능한
상태에서 한 노드씩 적용한다. 모든 노드에 일괄 적용하는 명령은 다음과 같다.

```bash
CONFIG_FILE=scripts/cluster.env \
scripts/configure_rdma_network_on_cluster.sh
```

## 5. 시작, 상태 확인, 중지

### 시작

```bash
cd /Users/dshs_llm/exo
scripts/start_4node_exo_cluster.sh
```

스크립트는 다음 작업을 수행한다.

1. node1에서 `exo-master` screen 세션을 시작한다.
2. 선택한 SSH 제어 경로로 worker 3대를 시작한다.
3. 각 worker에 node1의 bootstrap peer를 전달한다.
4. `/state/topology`에 최소 4개 노드가 나타날 때까지 기다린다.

기존 master까지 재시작하려면:

```bash
RESTART=true scripts/start_4node_exo_cluster.sh
```

### 상태

```bash
scripts/status_4node_exo_cluster.sh
scripts/verify_4node_exo_cluster.sh 127.0.0.1 52415
```

직접 확인할 수도 있다.

```bash
curl -fsS http://127.0.0.1:52415/state/topology | python3 -m json.tool
curl -fsS http://127.0.0.1:52415/state | python3 -m json.tool
```

정상 기준:

- `topology_nodes=4`
- 4개 노드의 `lastSeen`이 계속 갱신됨
- node identity의 OS version/build가 동일함
- topology connection에 기대한 `sourceRdmaIface`와 `sinkRdmaIface`가 존재함

`scripts/verify_rdma_cluster.sh`는 node1에서 RDMA 인터페이스 3개와 80 Gb/s
Thunderbolt IP port를 요구한다. 현재 star 배선과 `Pipeline/MlxRing` placement를
검증하는 명령이다.

```bash
scripts/verify_rdma_cluster.sh
```

### 중지

worker 세션만 중지:

```bash
scripts/stop_4node_exo_cluster.sh
```

로컬 master listener도 중지:

```bash
STOP_MASTER=true scripts/stop_4node_exo_cluster.sh
```

## 6. 모델 준비와 인스턴스 배치

현재 exo 경로는 MLX 모델 카드를 기준으로 한다. GGUF 다운로드·동기화 스크립트는
별도 llama.cpp 실험용이며 현재 exo MLX 배치 절차와 섞지 않는다.

현재 Qwen weight는 정규화된 디렉터리에 두며 모든 노드에 동일하게 복제한다.

```bash
scripts/sync_exo_model_to_cluster.sh
scripts/verify_exo_model_on_cluster.sh
```

worker에 Xcode/Metal compiler가 없어 node1에서 검증한 prebuilt MLX runtime을
복제하는 경우 다음을 사용한다.

```bash
scripts/sync_runtime_to_cluster.sh
```

클러스터 전체 노드에 MLX 모델을 다운로드하려면:

```bash
uv run python scripts/download_model_to_cluster.py \
  mlx-community/Qwen3.6-35B-A3B-4bit \
  --host 127.0.0.1
```

이 명령은 API 토폴로지의 각 노드에 `/download/start`를 보내고 완료될 때까지
확인한다. `EXO_OFFLINE=true`로 시작한 클러스터에서는 필요한 모델 파일이 이미
로컬에 있어야 한다.

현재 설정값으로 인스턴스를 배치한다.

```bash
scripts/place_rdma_instance.sh
```

실제 요청은 다음과 같다.

```json
{
  "model_id": "mlx-community/Qwen3.6-35B-A3B-4bit",
  "sharding": "Pipeline",
  "instance_meta": "MlxRing",
  "min_nodes": 4
}
```

배치 가능 여부를 먼저 확인하려면:

```bash
curl -fsS \
  'http://127.0.0.1:52415/instance/previews?model_id=mlx-community/Qwen3.6-35B-A3B-4bit' \
  | python3 -m json.tool
```

`error`가 없는 preview의 sharding, instance metadata, 예상 노드별 메모리를 확인한
뒤 배치한다. 저장소 API에는 자동 배치용 `POST /place_instance`와 preview에서
고른 인스턴스를 그대로 생성하는 `POST /instance`가 모두 있다.

## 7. API 검증

### OpenAI Chat Completions

```bash
curl -N http://127.0.0.1:52415/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "mlx-community/Qwen3.6-35B-A3B-4bit",
    "messages": [
      {"role": "user", "content": "한국어로 한 문장만 답해줘"}
    ],
    "stream": true
  }'
```

### Claude Messages

```bash
curl -N http://127.0.0.1:52415/v1/messages \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: x' \
  -H 'anthropic-version: 2023-06-01' \
  -d '{
    "model": "mlx-community/Qwen3.6-35B-A3B-4bit",
    "max_tokens": 256,
    "messages": [
      {"role": "user", "content": "한국어로 한 문장만 답해줘"}
    ],
    "stream": true
  }'
```

응답 전에 `/state`에서 인스턴스와 runner가 생성됐는지 확인한다. 오류가 나면
API 응답뿐 아니라 node1과 각 worker의 screen 로그를 함께 확인한다.

대시보드는 `thinking` 또는 `thinking_toggle` capability가 있는 텍스트 모델에서
reasoning selector를 표시한다. `INSTANT`, `LOW`, `MEDIUM`, `HIGH`, `XHIGH` 선택은
각각 Chat Completions 요청의 `reasoning_effort`로 전달되며 `INSTANT`는
`enable_thinking=false`, 나머지는 `enable_thinking=true`로 전달된다. 모델별 chat
template가 세부 effort를 구분하지 않으면 `LOW`~`XHIGH`는 모두 thinking 활성화로
동작할 수 있다.

## 8. Claude Code 연동

Claude Code는 Anthropic-format gateway를 `ANTHROPIC_BASE_URL`로 지정할 수 있다.
exo base URL은 `/v1`을 붙이지 않은 API root여야 한다. Claude Code가 내부적으로
`/v1/messages`를 호출한다.

node1에서 실행:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:52415
export ANTHROPIC_API_KEY=x
export ANTHROPIC_DEFAULT_OPUS_MODEL=mlx-community/Qwen3.6-35B-A3B-4bit
export ANTHROPIC_DEFAULT_SONNET_MODEL=mlx-community/Qwen3.6-35B-A3B-4bit
export ANTHROPIC_DEFAULT_HAIKU_MODEL=mlx-community/Qwen3.6-35B-A3B-4bit
export API_TIMEOUT_MS=3000000
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
claude
```

예시는 `docs/claude-code-settings.example.json`에도 있다. 공식 gateway 문서는
고정 토큰에 `ANTHROPIC_AUTH_TOKEN` 사용을 권장하지만, 현재 exo는 인증을 강제하지
않는다. 내부망의 임시 값 `x`는 보안 경계가 아니다.

Claude Code를 다른 장비에서 실행하려면 `127.0.0.1` 대신 접근 가능한 node1의
LAN 주소 또는 인증된 reverse proxy 주소를 사용한다.

## 9. Agentic Local Server

OpenAI-compatible backend 위에서 계정별 로컬 채팅 세션을 제공하는 별도 서버가
있다. 코딩 작업은 이 서버의 run 기능이 아니라 앞 절의 Claude Code를 사용한다.

```bash
cd /Users/dshs_llm/exo
AGENTIC_LLM_BASE_URL=http://127.0.0.1:52415/v1 \
AGENTIC_LLM_MODEL=mlx-community/Qwen3.6-35B-A3B-4bit \
uv run --package exo-tools agent-server
```

최종 사용자 GUI는 `http://127.0.0.1:8765`이다. 클러스터 운영자는
`http://127.0.0.1:52415`의 모니터링·관리 패널을 별도로 사용한다. 상세 기능과 제한은
[`agentic-local-server.md`](agentic-local-server.md)를 참고한다.

## 10. 보안과 운영 기준

- `52415`는 관리면, `52416`은 내부 노드 통신이므로 인터넷에 공개하지 않는다.
- 사용자 진입점은 `8765` 하나로 제한하고 Cloudflare Tunnel과 Access 뒤에 둔다.
- 외부 접근에는 TLS, 인증, rate limit, 요청 크기 제한을 적용한다.
- libp2p/worker 통신 포트는 신뢰된 내부망에서만 접근 가능하게 한다.
- `scripts/cluster.env`와 토큰이 포함된 설정 파일은 커밋하지 않는다.
- 배포 전 `/state/topology` 응답에 포함된 내부 주소와 노드 식별자 노출 여부를 검토한다.

## 11. 장애 확인 순서

1. `scripts/status_4node_exo_cluster.sh`
2. `curl http://127.0.0.1:52415/state/topology`
3. node1과 worker의 `~/.exo/exo.pid`
4. `screen -ls` 및 각 screen 세션 로그
5. SSH 제어 경로 확인 후 필요하면 `CONNECT_TYPE=net`
6. 4대의 macOS version/build 일치 여부
7. 모델 파일 존재 여부와 노드별 사용 가능 메모리
8. `/instance/previews`의 placement error

## 12. 현재 완료 상태

- [x] M4 Pro 64 GiB Mac mini 4대 연결
- [x] macOS 26.5 / 25F71 통일
- [x] SSH 기반 일괄 시작·중지
- [x] exo topology 4노드 확인
- [x] node1↔worker RDMA 링크 확인
- [x] `Pipeline/MlxRing` 운영 설정
- [x] OpenAI 및 Claude 호환 API 제공
- [x] Agentic Local Server 기본 구현과 테스트
- [ ] 운영용 API 인증/TLS/rate limit
- [ ] 클러스터 재부팅 후 자동 복구
- [ ] 모델별 처리량·첫 토큰 지연 벤치마크 기록
- [ ] worker 장애 및 장시간 부하 테스트

## 13. 참고 자료

- [exo README](../README.md)
- [exo API 문서](api.md)
- [Agentic Local Server](agentic-local-server.md)
- [Anthropic Claude Code LLM gateway](https://docs.anthropic.com/en/docs/claude-code/llm-gateway)
