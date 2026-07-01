# exo 현재 진행상황과 구현 세부사항

> 확인 시각: 2026-07-01 17:58 KST
> 원본 작업트리: `/Users/dshs_llm/exo`
> upstream 기준 커밋: `90f24bef`
> 검증 대상: 로컬 master + 원격 worker 3대, Agentic Local Server, 공개 tunnel

## 1. 요약

현재 시스템은 다음 상태다.

- node1의 `52415`에서 exo control plane/API가, `52416`에서 libp2p transport가
  동작하지만 현재 topology에는 node1 한 대만 보인다.
- worker 3대는 Thunderbolt line 및 management SSH가 모두 timeout이라 현재
  원격 동기화와 4노드 재합류가 불가능하다.
- node1의 standalone llama.cpp가 `52417`에서 Qwen3.6 35B Q4_K_M GGUF를 제공한다.
- Agentic Local Server는 llama.cpp `52417/v1`을 backend로 사용하며 실제 채팅이
  약 1.2초에 성공했다.
- exo `52415`에 요청 단위 llama.cpp router를 활성화했다. 현재 replica 1개를
  등록했고 비스트리밍과 스트리밍 Chat Completions가 모두 성공했다.
- 기존 4노드 `Pipeline/MlxRing` 배치는 현재 활성 instance가 없으며, 마지막 정상
  검증에서는 네 노드 모두 model weight 5개가 일치했다.
- Agentic Local Server는 `8765`에서 실행 중이며 로그인, 권한, 관리자 화면,
  세션 CRUD, 세션별 resource 조회가 실제 HTTP 호출로 동작한다.
- Cloudflare tunnel이 실행 중이고 공개 HTTPS 로그인 페이지가 HTTP 200을
  반환한다.
- Python 단위/API 테스트는 확인 범위에서 모두 통과했다.
- 기존 multi-node MLX 추론은 토큰 1개 요청도 45초 내 응답하지 못했지만,
  standalone llama.cpp와 새 request router 경로는 실제 생성에 성공했다.
- dashboard 정적 build는 성공하지만 `svelte-check`는 15 errors/6 warnings로
  실패한다.
- Thunderbolt poll의 들여쓰기 결함을 수정하고 정상/timeout 경로 회귀 테스트를
  추가한 뒤 worker 3대에 동기화하고 4노드 cluster를 재시작했다.
- 갱신된 인증 DB, 실제 환경 설정, 로그와 세션을 암호화 runtime archive로 다시
  보존했고 복호화 및 SQLite 무결성 검사를 통과했다.

즉, node1 단독 사용자 서비스와 llama.cpp inference는 동작한다. 현재 가장 큰
미완료 항목은 worker 3대의 SSH/재합류, multi-node MLX distributed 경로,
dashboard type-check 정리다.

## 2. 실행 구조

```text
Public HTTPS
    |
Cloudflare Tunnel
    |
    v
Agentic Local Server :8765
  - 사용자 로그인/RBAC/CSRF
  - 세션, 대화, resource allocation
  - 현재 backend: node1 llama.cpp :52417/v1
    |
    +--------------------------+
                               v
                      llama.cpp :52417

exo control plane :52415
  - topology/state/model/instance API
  - OpenAI/Claude/Responses/Ollama 호환 API
  - matching model → llama replica router → :52417
  - unmatched model → 기존 MLX instance 경로
  - dashboard
    |
    v
libp2p transport :52416
    |
    +-- 현재 node1만 연결
    +-- node2~node4 SSH timeout
```

`8765`는 사용자용 애플리케이션이고 `52415`는 클러스터 운영용 control
plane이다. 두 포트를 하나의 UI/API로 취급하면 안 된다.

## 3. 현재 런타임 상태

### 3.1 프로세스와 포트

확인된 listener:

| 포트 | 프로세스 역할 | 상태 |
|---|---|---|
| `8765` | Agentic Local Server | LISTEN |
| `52415` | exo master dashboard/API | LISTEN |
| `52416` | exo libp2p | LISTEN |
| `52417` | standalone llama.cpp Qwen GGUF | LISTEN |

현재 `screen`에는 `exo-master`만 있다. worker screen 세션은 없다.

### 3.2 topology와 runner

현재 live state:

- topology node: 1
- connection group: 0
- runner: 0
- instance: 0
- Thunderbolt state node: 1
- state event index: 확인 시점 `93`

직전 4노드 정상 검증에서는 status/verify script가 통과했고 runner 4개가
`RunnerReady`였다. 당시 instance:

| 항목 | 값 |
|---|---|
| instance type | `MlxRingInstance` |
| model | `mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq` |
| world size | 4 |
| rank 0 layers | `[0, 4)` |
| rank 1 layers | `[4, 24)` |
| rank 2 layers | `[24, 34)` |
| rank 3 layers | `[34, 40)` |

이 표는 현재 live state가 아니라 마지막 정상 4노드 배치 기록이다. 배치가 균등
layer count가 아닌 이유는 노드별 사용 가능 메모리와 placement 계산을 반영하기
때문이다.

### 3.3 모델 보유 상태

`~/models`:

| 모델 | weight 상태 | bytes |
|---|---:|---:|
| DeepSeek R1 Distill Qwen 32B abliterated 4bit | 4/4 | 18,431,478,457 |
| Huihui Qwen3.6 35B A3B 4.4bit | 5/5 | 21,188,634,184 |
| Llama 3.2 1B Instruct 4bit | 1/1 | 695,283,921 |
| Qwen3.6 35B A3B 4bit | 4/4 | 20,402,204,271 |
| gemma-4 31B 4bit | 4/4 | 18,412,016,832 |

`~/llm-models/Qwen3.6-35B-A3B-GGUF`에는 GGUF 2개가 있으며 합계
`58,069,898,528` bytes다. Hugging Face cache에는 incomplete 파일 2개가
남아 있다.

기존 61GB DeepSeek 16-bit 13-shard 디렉터리는 제거됐고 약 18.4GB 4-bit
4-shard 모델로 교체됐다. 새 모델은 index 기준 missing weight가 없다.

기존 [progress_report.md](progress_report.md)에 기록된 단일 노드 실행 결과:

| 모델 | 단일 노드 결과 |
|---|---|
| Huihui Qwen3.6 35B A3B 4.4bit | text generation 성공 |
| Qwen3.6 35B A3B 4bit | text generation 성공 |
| gemma-4 31B 4bit | text generation 성공 |
| DeepSeek R1 Distill Qwen 32B 16bit | Metal OOM |

이 결과는 기존 보고서의 검증 기록이며 이번 작업에서 다시 실행한 것은 아니다.
당시 DeepSeek 16-bit는 64GB 단일 노드의 실제 여유 메모리보다 커서 OOM이
발생했다. 현재는 해당 모델을 4-bit 버전으로 교체했지만 새 4-bit 모델의 실제
단일 노드 생성은 이번 점검에서 실행하지 않았다.

모델 본체는 GitHub LFS 파일당 제한 때문에 저장소에 넣지 않았다.
`model-snapshots/manifest.json`에 현재 파일, 크기, 수정 시각, incomplete 상태를
기록하고 작은 설정/토크나이저 파일만 `model-snapshots/metadata`에 보존한다.

## 4. 구현 세부사항

### 4.1 4노드 lifecycle

관련 파일:

- `scripts/start_4node_exo_cluster.sh`
- `scripts/status_4node_exo_cluster.sh`
- `scripts/stop_4node_exo_cluster.sh`
- `scripts/verify_4node_exo_cluster.sh`
- `scripts/cluster_connection.sh`
- `scripts/cluster_exec.sh`

시작 스크립트는 중복 실행 lock을 만들고 master API 기동을 기다린 다음 worker를
SSH TTY + `screen`으로 실행한다. Thunderbolt line 제어 경로가 불완전하면
management network로 fallback한다. 최소 노드 수가 이미 충족된 경우 기존
worker를 불필요하게 재시작하지 않는다.

운영 환경에서 사용하는 주요 플래그:

- `EXO_NODE_NAME`: dashboard와 state에 안정적인 `node1`~`node4` 이름 제공
- `EXO_LIBP2P_NAMESPACE`: 다른 exo cluster와 discovery 공간 분리
- `EXO_MODELS_DIRS`: 사전 배포 모델 위치
- `EXO_OFFLINE=true`, `--no-downloads`: 운영 중 임의 다운로드 방지
- `EXO_NODE_TIMEOUT_SECONDS=900`: 긴 Metal compile/prefill 동안 node 제거 방지
- `EXO_SKIP_WARMUP=true`: 운영 기동 시 blocking warmup 생략
- `EXO_DEBUG_PIPELINE=true`: pipeline send/recv/prefill 진단 로그 활성화

### 4.2 node identity와 상태 수집

`src/exo/routing/router.py`는 매 실행마다 새 peer ID를 만들던 동작을 제거하고
keypair를 파일에 영속화한다.

- cross-process file lock 사용
- 손상된 keypair면 새 keypair 생성
- keypair 파일 mode `0600`
- parent directory 자동 생성

`src/exo/master/main.py`의 dead-node timeout은 고정 30초 대신
`EXO_NODE_TIMEOUT_SECONDS`로 설정 가능하다.

`src/exo/utils/info_gatherer` 변경:

- `EXO_NODE_NAME` 우선 사용
- GPU 온도가 비정상적으로 낮으면 CPU 평균 온도로 fallback
- static/misc/Thunderbolt 정보 수집 timeout을 예외 전파 대신 경고 후 다음 poll로
  넘김
- Thunderbolt interface map, connectivity 수집, identifier/connection event
  전송 전체를 동일한 30초 cancel scope 안에서 실행

timeout 처리 변경은 일시적으로 느린 macOS system API가 전체 info gatherer
task를 죽이지 않게 하기 위한 것이다.

2026-07-01 수정에서는 `ThunderboltConnectivity.gather()`와 event 전송이
timeout 조건문 내부에 잘못 들어가 정상 poll에서 실행되지 않던 들여쓰기를
바로잡았다. 회귀 테스트는 정상 poll에서 두 event가 전송되는 경우와 timeout에서
event 없이 다음 poll로 넘어가는 경우를 모두 확인한다.

수정 파일은 worker 3대에 동기화했으며 master/worker를 재시작했다. 재시작 후 실제
macOS 수집 함수의 one-shot 검증에서 identifier 3개와 connection 3개가 전송됐고,
control-plane state에도 `nodeThunderbolt` 항목 3개가 다시 등록됐다.

### 4.3 Thunderbolt RDMA

관련 파일:

- `scripts/configure_rdma_network.sh`
- `scripts/configure_rdma_network_on_cluster.sh`
- `scripts/place_rdma_instance.sh`
- `scripts/verify_rdma_cluster.sh`

직접 검증 결과:

- `AppleThunderboltRDMAInterface`: 3
- `AppleThunderboltRDMAPeerInterface`: 3
- `AppleThunderboltIPConnection`: 3
- 80 Gb/s Thunderbolt IP port 감지
- 4노드 topology와 `Pipeline/MlxRing` placement 계산 성공

RDMA 검증은 interface 존재만 보는 것이 아니라 control plane의 실제 placement
응답까지 확인한다.

### 4.4 모델 배포와 검증

관련 파일:

- `scripts/sync_exo_model_to_cluster.sh`
- `scripts/verify_exo_model_on_cluster.sh`
- `scripts/sync_model_to_cluster.sh`
- `scripts/verify_model_on_cluster.sh`
- `scripts/download_qwen36_gguf_aria2.sh`
- `scripts/verify_model_file.sh`

MLX 모델 검증은 `model.safetensors.index.json`의 `weight_map`을 기준으로 필요한
shard 목록을 계산한다. 단순 디렉터리 존재가 아니라 모든 weight 존재 여부와
합계 크기를 node1 및 worker 3대에서 확인한다.

현재 배치 모델은 네 노드 모두:

```text
weight_files=5 missing=0 size_bytes=21188634184
```

line network 전송/검증 실패 시 management network로 fallback한다.

### 4.5 MLX pipeline 변경

`src/exo/worker/engines/mlx/auto_parallel.py`:

- prefill send queue flush 전후 진단 로그
- rank별 `recv_like` 시작/완료 로그
- queued send 대상과 tensor shape 기록

`src/exo/worker/engines/mlx/generator/generate.py`:

- 모든 rank가 같은 prompt chunk 순서로 `model()`에 진입
- leading/trailing dummy iteration 제거
- 각 chunk의 model/flush 진행 로그
- 4096 token 미만 짧은 prompt는 custom queued prefill 대신 동기 경로 사용

목적은 rank별 send/recv 횟수를 맞추고 짧은 prompt에서 첫 Metal graph 대기로
교착되는 경우를 줄이는 것이다.

`src/exo/worker/plan.py`는 warmup 준비 판정 시 이미 `RunnerReady`인 peer도
준비 완료로 인정한다. `src/exo/worker/runner/runner.py`는
`EXO_SKIP_WARMUP`을 지원한다.

현재 실제 추론이 45초 내 완료되지 않았으므로 이 변경의 end-to-end 효과는 아직
확정할 수 없다.

기존 [progress_report.md](progress_report.md)는 분산 추론이 초기
`mx_barrier`에서 멈추며, Wi-Fi
management network, Thunderbolt bridge, tunnel/VPN interface가 동시에 노출되어
placement가 잘못된 coordinator 주소를 선택하는 것을 원인으로 분석했다. 현재
master 로그에서도 host matrix와 rank hostfile에 `198.51.100.1:0`,
`0.0.0.0:59811` 같은 부적절한 항목이 포함된 것이 확인된다. topology 연결 성공과
MLX distributed group 초기화 성공은 별개의 문제다.

### 4.6 API compatibility

exo `52415`는 다음 계열을 제공한다.

- OpenAI Chat Completions: `/v1/chat/completions`
- OpenAI Responses: `/v1/responses`
- Anthropic Claude Messages: `/v1/messages`
- Ollama chat/generate/tags/show/ps/version
- image generation/edit API
- model/instance/state/topology/trace API

Claude adapter 변경:

- Claude role에 `system` 허용
- `thinking.type=enabled`만 local thinking opt-in으로 처리
- Claude Code가 보내는 `adaptive`는 local Qwen에서 무제한 reasoning으로
  이어질 수 있어 thinking 비활성으로 변환

빠른 HTTP 확인:

- `/state`, `/state/topology`, `/models`, `/v1/models`,
  `/v1/feature-flags`, `/v1/instance-links`: HTTP 200
- 모델 card 수: 123
- `/ollama/api/tags`, `/ollama/api/ps`, `/ollama/api/version`,
  `/ollama/api/show`: HTTP 200
- `/v1/traces`: HTTP 200

### 4.7 요청 단위 llama.cpp replica router

관련 파일:

- `src/exo/api/llama_router.py`
- `src/exo/api/tests/test_llama_router.py`
- `scripts/start_llama_replica.sh`
- `scripts/llama-replicas.json.example`
- `scripts/start_4node_exo_cluster.sh`

`LlamaRequestRouter`는 하나의 요청 전체를 독립 llama.cpp server 한 곳에 보낸다.
multi-node pipeline처럼 토큰마다 노드 간 동기화하지 않으므로 현재 distributed
`mx_barrier` 문제를 우회할 수 있다.

구현:

- external model ID를 replica별 upstream alias로 변환
- 모델 지원 여부와 `max_concurrency`를 기준으로 replica 후보 필터
- active/max concurrency 비율, 완료 요청 수, replica ID 순으로 부하 분산
- 모든 replica가 사용 중이면 condition queue에서 capacity 대기
- `maximum_queue_size` 초과 시 HTTP 429와 `Retry-After`
- `queue_timeout_seconds` 초과 시 HTTP 503과 `Retry-After`
- transport/5xx 실패 replica에 cooldown 적용
- streaming body 종료 또는 오류 시 lease 반환
- 응답에 `X-Exo-Replica`, `X-Exo-Node`, `X-Exo-Queue-Wait-Ms`
- `/v1/llama-router/status`에서 queue와 replica counter 확인
- 설정에 없는 모델은 기존 exo MLX 경로로 fallback

현재 운영 설정은 Git에서 제외한 `scripts/llama-replicas.json`에 node1의
`127.0.0.1:52417/v1` replica 하나를 등록한다. router status는 enabled이며
completed 2, failed 0, active 0을 반환했다.

직접 검증:

- standalone llama.cpp non-stream 생성: HTTP 200, 약 1초
- router class → 실제 llama.cpp: HTTP 200
- live `52415` non-stream router: HTTP 200, 약 0.3초
- live `52415` stream router: SSE 3,615 bytes, `[DONE]` 확인
- queue timeout 경로: `LlamaQueueTimeoutError` 확인
- stream 완료 후 active 0, completed counter 증가

현재 replica는 node1 한 개뿐이므로 다중 노드 load balancing은 unit test에서만
확인했다. worker가 복귀하면 각 worker의 GGUF/llama-server 준비 후 replica를
추가해야 한다.

### 4.8 dashboard 역할 분리

dashboard는 end-user chat UI가 아니라 cluster control plane으로 고정하는 방향이다.

- `topologyOnlyMode` 기본값 `true`
- localStorage 값이 없을 때도 topology-only
- topology-only 화면의 exit 버튼 제거
- README/API 문서에서 `52415`와 `8765` 역할 명시

정적 build는 성공하지만 type-check는 실패하므로 완료로 보지 않는다.

### 4.9 Agentic Local Server

관련 패키지:

- `tools/src/exo_tools/agent_server.py`
- `tools/src/exo_tools/accounts.py`
- `tools/src/exo_tools/agent_core/`

주요 기능:

- FastAPI 기반 사용자 UI/API
- SQLite 계정 DB
- `user`, `admin`, `master` role과 permission override
- scrypt password hash, 사용자별 salt
- 24시간 login session, DB에는 token hash만 저장
- CSRF token 검증
- login failure rate limit
- HttpOnly/Secure/SameSite=Lax cookie
- Trusted Host middleware
- Google OAuth 연결/가입 코드
- 사용자별 session ownership 격리
- session 수 제한
- Git-backed session/chat persistence
- `chat.md`, `messages.jsonl`, `session.json`, `resources.json`
- message retention과 Git history compaction
- 세션별 compute semaphore, disk quota, memory limit
- background chat job과 NDJSON stream
- OpenAI/Claude proxy 및 model 목록 proxy
- 관리자 사용자 생성, role/permission/status 관리
- account/password 화면
- cluster control URL redirect

실제 live API로 확인한 항목:

- master login/logout
- `/auth/me`: role `master`, permission 13개
- 로그인/관리자/account HTML
- `/admin/users`
- session 생성, 조회, 제목 변경, 삭제
- session resource allocation/usage 조회
- logo asset
- cluster control HTTPS redirect
- security header와 secure cookie flag

검증용 session은 생성 후 삭제했다.

현재 Agentic backend:

```text
AGENTIC_LLM_BASE_URL=http://127.0.0.1:52417/v1
AGENTIC_LLM_MODEL=Huihui-Qwen3.6-35B-A3B-abliterated-Q4_K_M.gguf
```

실제 session을 생성해 `reasoning_effort=none`으로 메시지를 보냈으며 HTTP 200,
약 1.2초에 assistant content가 저장됐다. 검증 session은 삭제했다.

### 4.10 공개 접근

현재 상태:

- Cloudflare tunnel 프로세스 실행 중
- 공개 HTTPS `/login`: HTTP 200
- `AGENTIC_PUBLIC_URL`: HTTPS로 설정
- `AGENTIC_ALLOWED_HOSTS`: 설정
- HSTS 활성
- Google OAuth client: 미설정

`scripts/start_cloudflare_tunnel.sh`는 config, token file, macOS Keychain 순서로
자격증명을 찾는다.

### 4.11 로컬 runtime 이관

`runtime/agentic-local-20260701.tar.zst.enc`를 현재 상태로 다시 만들었다.
다음 private runtime을 AES-256-CBC/PBKDF2 암호화 archive 안에 보존한다.

- `.agentic-local` 인증 DB, bootstrap 자격증명, 로그, 세션 store
- 실제 `server.env`, `cluster.env`, `model.env`, `.envrc`
- 로컬 exo 설정

실행 중인 인증 DB는 파일 복사가 아니라 SQLite backup으로 일관된 snapshot을
만들었다. 새 archive를 임시 경로에 복호화한 뒤 `PRAGMA integrity_check=ok`와
환경 설정 원본 비교를 확인했다. 복호화 키는 저장소 밖
`~/.config/agent-setup/runtime-backup.key`에만 있으며 별도 보관해야 한다.

Apple Silicon 가상환경 archive와 큰 tokenizer/runtime archive는 Git LFS로
관리한다. 모델 weight와 대형 model cache는 GitHub 파일 제한 때문에 manifest와
복원용 metadata만 저장소에 두고 실제 파일은 로컬 모델 저장소에 유지한다.

## 5. 직접 검증 결과

| 검증 | 결과 | 비고 |
|---|---|---|
| shell syntax (`bash -n scripts/*.sh`) | 통과 | 전체 운영 shell |
| 변경 Python compile | 통과 | `compileall` |
| Agentic unit/API tests | 37 passed | 약 5초 |
| exo API tests | 54 passed | llama router 4개 포함, 약 0.4초 |
| Claude/macmon/node-ID/plan/runner tests | 121 passed | 약 17초, fork deprecation warning 210개 |
| info gatherer tests | 8 passed | 정상/timeout Thunderbolt poll 포함 |
| 4노드 수정 파일 hash | 통과 | node1/worker 3대 SHA-256 일치 |
| 실제 Thunderbolt poll one-shot | 통과 | identifier 3, connection 3 |
| 4노드 status/verify | 과거 통과, 현재 실패 | 현재 node1만 연결 |
| RDMA verify | 과거 통과 | 3 links, 80Gb/s, placement 성공 |
| 4노드 model file verify | 과거 통과 | 당시 각 노드 5/5 shards |
| control plane HTTP endpoints | 통과 | 주요 state/model API 200 |
| Ollama metadata endpoints | 통과 | tags/show/ps/version |
| Agentic live auth/session/admin | 통과 | 실제 `8765` 호출 |
| standalone llama.cpp 생성 | 통과 | HTTP 200, 약 1초 |
| Agentic → llama.cpp 생성 | 통과 | HTTP 200, 약 1.2초 |
| llama router unit/API | 통과 | routing/capacity/queue/replica count |
| live router non-stream | 통과 | HTTP 200, 약 0.3초, replica header |
| live router stream | 통과 | SSE `[DONE]`, lease/counter 정상 |
| router queue timeout | 통과 | capacity 대기 timeout 확인 |
| 공개 HTTPS login | 통과 | Cloudflare 경유 200 |
| dashboard production build | 통과 | bundle size warning 존재 |
| dashboard `svelte-check` | 실패 | 15 errors, 6 warnings, 9 files |
| multi-node MLX 1-token inference | 과거 실패/시간초과 | HTTP 200 header 후 45초간 body 0 bytes |

## 6. 오래 걸리거나 환경상 건너뛴 검증

### 6.1 multi-node MLX Claude/Ollama 생성

standalone llama.cpp, Agentic, OpenAI router 경로는 실제 생성에 성공했다.
현재 worker가 연결되지 않아 multi-node MLX를 사용하는 Claude Messages와 Ollama
generate는 재검증할 수 없다.

예상 시간: worker SSH/재합류 해결 후 요청당 1분 이상 또는 무기한 대기 가능.

### 6.2 장시간 처리량/안정성 benchmark

다음은 수행하지 않았다.

- 동시 사용자 부하
- 장문 prefill
- tokens/sec, TTFT 측정
- 1시간 이상 연속 생성
- memory leak 관찰

예상 시간: 최소 2~8시간.

### 6.3 worker 장애 복구

현재 worker 3대가 이미 topology에서 이탈했고 line/management SSH가 모두
timeout이다. 장애를 의도적으로 재현하는 시험이 아니라 원격 호스트 자체의
전원·network·sshd 복구가 먼저 필요하다.

예상 시간: 물리 호스트 상태에 따라 30분 이상.

### 6.4 재부팅 자동 복구

4대 재부팅 후 SSH, RDMA, screen, 모델 instance, tunnel이 자동 복구되는지 확인하지
않았다.

예상 시간: 30~60분, 전체 서비스 중단 포함.

### 6.5 image generation/edit

현재 배치 instance는 text generation 모델이다. image model을 별도로 다운로드하고
배치해야 하므로 실행 검증을 건너뛰었다.

예상 시간: 모델 준비 상태에 따라 30분~수시간.

### 6.6 브라우저 시각/클릭 검증

인앱 브라우저가 연결되지 않아 실제 화면 렌더링, 클릭, 반응형 layout은 확인하지
못했다. HTML/API 응답과 production build까지만 확인했다.

## 7. 알려진 문제와 위험

### P0: worker 3대 SSH/topology 이탈

node2~node4는 Thunderbolt line 주소와 management 주소 모두 SSH connect timeout이
발생한다. 현재 topology는 node1 한 대이고 runner/MLX instance는 없다.

확인 순서:

1. worker 전원과 macOS login 상태
2. management switch/LAN link와 주소
3. worker의 Remote Login/sshd
4. Thunderbolt bridge 주소와 route
5. SSH 복구 후 새 router/API 파일 동기화
6. `start_4node_exo_cluster.sh`로 4노드 재합류

### P0: multi-node MLX inference가 45초 내 응답하지 않음

마지막 4노드 실행에서 runner와 instance는 `Running`이었지만 1-token 요청이 body
없이 timeout됐다. 현재는 worker 이탈로 동일 경로를 재시험할 수 없다.
기존 [progress_report.md](progress_report.md)의 `mx_barrier` 분석과 현재
hostfile 로그를 함께 보면 가장 유력한 원인은 다중 네트워크 환경에서 잘못 구성된
distributed host matrix다.

다음 확인이 필요하다.

1. host 후보에서 wildcard, port 0, tunnel/VPN interface 제외
2. 모든 rank가 동일한 Thunderbolt `10.0.0.x` host 순서를 갖는지 확인
3. rank별 `EXO_DEBUG_PIPELINE` 로그의 마지막 barrier/send/recv 지점 비교
4. master task와 네 runner task ID 매핑
5. Metal compile인지 distributed group 초기화 deadlock인지 구분
6. timeout 후 task cancel/runner 재사용 가능 여부 확인

matching model을 standalone llama replica로 보내는 새 router 경로는 성공하므로
사용자 inference의 임시 우회로는 확보됐다.

### P0: Cloudflare tunnel token이 프로세스 명령줄에 노출됨

현재 `cloudflared tunnel run --token ...` 방식은 같은 호스트의 process list에서
token이 보일 수 있다. token을 회전하고, 가능하면 credentials file/config 기반
named tunnel 또는 launchd의 안전한 secret 전달 방식으로 바꿔야 한다.

### P0: 원본 exo Xcode scheme의 AWS credential

원본 exo 작업트리의 Xcode shared scheme에 AWS access key/secret이 평문으로
존재했다. `Agent-setup`에는 빈 값으로 제거했지만 기존 키는 폐기/재발급해야 한다.

### P1: dashboard type-check 실패

대표 오류:

- PDF render parameter에 `canvas` 누락
- `expandedNodes` 미정의
- untyped `Array`와 implicit `any`
- SVG prop의 잘못된 `title`
- `userDeviceInfo` 선언 전 사용

production build 성공만으로 type safety가 확보된 것은 아니다.

### P1: llama router 운영 범위가 replica 1개

현재 실제 replica는 node1의 `52417` 한 개뿐이다. 따라서 현재 검증은 proxy와
queue/stream lifecycle을 입증하지만 여러 물리 노드에 대한 실제 load balancing과
failover를 입증하지 않는다.

추가로 필요한 검증:

- 2개 이상 실제 replica의 동시 요청 분산
- upstream 5xx/transport failure cooldown과 복귀
- streaming client disconnect 시 lease 반환
- queue 429/503의 live API 응답과 `Retry-After`
- 장시간 요청에서 memory/connection leak

### 해결됨: Thunderbolt 주기 수집 들여쓰기 결함

`InfoGatherer._monitor_system_profiler_thunderbolt_data()`의 connectivity 수집과
event 전송을 30초 cancel scope 안으로 옮겼다. 정상 경로에서는
`MacThunderboltIdentifiers`와 `MacThunderboltConnections`를 전송하고, timeout
경로에서는 event를 전송하지 않은 채 poll interval 대기로 진행한다.

전용 회귀 테스트가 asyncio/trio backend 양쪽에서 통과했다. worker 동기화,
cluster 재시작, 실제 macOS poll의 identifier/connection 3개 전송까지 확인했다.

### P1: source 작업트리가 미커밋 상태

원본 `/Users/dshs_llm/exo`는 upstream `90f24bef` 위에 수정/신규 파일이 남아 있다.
`Agent-setup`은 현재 snapshot 용도지만, 원본 변경도 별도 branch/commit으로
보존하지 않으면 upstream update와 병합할 때 추적이 어렵다.

### P1: public HTTPS 설정과 local HTTP cookie

`AGENTIC_PUBLIC_URL=https://...`이면 session cookie에 `Secure`가 붙는다. 표준
HTTP client는 `http://127.0.0.1:8765`에 이 cookie를 자동 재전송하지 않을 수 있다.
운영 사용은 공개 HTTPS를 기준으로 하고 local HTTP는 진단 경로로 취급해야 한다.

### P2: stale partial download 파일

GGUF Hugging Face cache에 incomplete 2개가 남아 있다. 실제 GGUF 2개와 MLX
weight index는 완전하지만 디스크 정리와 재다운로드 판단을 위해 잔여 파일을
점검해야 한다.

### P2: Google OAuth 미설정

코드는 구현되어 있으나 client ID/secret이 없어 실제 callback flow는 검증하지
못했다.

## 8. 다음 작업 우선순위

1. worker 3대 전원/network/sshd 복구와 4노드 재합류
2. worker별 llama.cpp replica 준비 후 실제 다중 replica 분산/장애 전환 검증
3. multi-node MLX host matrix 필터링과 barrier 진단
4. Cloudflare token 회전 및 command-line secret 제거
5. AWS credential 폐기 확인
6. dashboard 15개 type error 수정
7. TTFT/tokens-per-second 및 동시 요청 benchmark 기록
8. 재부팅 자동 복구 구성 및 시험
9. Google OAuth 설정 후 callback 검증

## 9. 재현 명령

```bash
# cluster 상태
scripts/status_4node_exo_cluster.sh
scripts/verify_4node_exo_cluster.sh 127.0.0.1 52415

# RDMA와 placement
MODEL_ID='mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq' \
  API_HOST=127.0.0.1 \
  scripts/verify_rdma_cluster.sh

# 네 노드 model shard
MODEL_ID='mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq' \
  MODELS_DIR="$HOME/models" \
scripts/verify_exo_model_on_cluster.sh

# standalone llama.cpp
scripts/start_llama_server.sh
curl http://127.0.0.1:52417/health

# request-level router
cp scripts/llama-replicas.json.example scripts/llama-replicas.json
# 실제 실행 중인 replica만 남긴 뒤 cluster.env에 LLAMA_REPLICAS_FILE 설정
curl http://127.0.0.1:52415/v1/llama-router/status

# tests
uv run --package exo-tools pytest tools/tests -q
EXO_DASHBOARD_DIR='exo/dashboard/build' PYTHONPATH=src \
  .venv/bin/pytest -q src/exo/api/tests

# dashboard
cd dashboard
npm run check
npm run build
```
