# exo 현재 진행상황과 구현 세부사항

> 확인 시각: 2026-07-01 17:08 KST
> 원본 작업트리: `/Users/dshs_llm/exo`
> upstream 기준 커밋: `90f24bef`
> 검증 대상: 로컬 master + 원격 worker 3대, Agentic Local Server, 공개 tunnel

## 1. 요약

현재 시스템은 다음 상태다.

- exo master와 worker 3대가 연결되어 topology에 4노드가 보인다.
- node1의 `52415`에서 control plane/API가, `52416`에서 libp2p transport가
  동작한다.
- `mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq` 모델이
  `Pipeline/MlxRing`, world size 4로 배치되어 있다.
- 네 노드 모두 모델 weight 5개, 총 `21,188,634,184` bytes가 존재한다.
- Agentic Local Server는 `8765`에서 실행 중이며 로그인, 권한, 관리자 화면,
  세션 CRUD, 세션별 resource 조회가 실제 HTTP 호출로 동작한다.
- Cloudflare tunnel이 실행 중이고 공개 HTTPS 로그인 페이지가 HTTP 200을
  반환한다.
- Python 단위/API 테스트는 확인 범위에서 모두 통과했다.
- 실제 모델 추론은 토큰 1개 요청도 45초 내 응답 본문을 반환하지 못했다.
  프로세스와 runner는 살아 있으나 inference data path는 추가 조사가 필요하다.
- dashboard 정적 build는 성공하지만 `svelte-check`는 15 errors/6 warnings로
  실패한다.
- Thunderbolt poll의 들여쓰기 결함을 수정하고 정상/timeout 경로 회귀 테스트를
  추가한 뒤 worker 3대에 동기화하고 4노드 cluster를 재시작했다.

즉, control plane, cluster topology, 모델 배치, Agentic 애플리케이션의
비추론 기능은 동작한다. 현재 가장 큰 미완료 항목은 실제 inference 응답성과
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
  - OpenAI/Claude proxy
    |
    v
exo control plane :52415
  - topology/state/model/instance API
  - OpenAI/Claude/Responses/Ollama 호환 API
  - dashboard
    |
    v
libp2p transport :52416
    |
    +-- node1
    +-- node2
    +-- node3
    +-- node4
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

master와 worker 세션은 `screen`의 `exo-master`, `exo-worker1`,
`exo-worker2`, `exo-worker3`로 유지된다.

### 3.2 topology와 runner

`scripts/status_4node_exo_cluster.sh`와
`scripts/verify_4node_exo_cluster.sh 127.0.0.1 52415`가 모두 성공했다.

- topology node: 4
- connection group: 4
- runner: 4개 모두 `RunnerReady`
- state event index: 재시작 후 확인 시점 `628`

현재 instance:

| 항목 | 값 |
|---|---|
| instance type | `MlxRingInstance` |
| model | `mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq` |
| world size | 4 |
| rank 0 layers | `[0, 4)` |
| rank 1 layers | `[4, 24)` |
| rank 2 layers | `[24, 34)` |
| rank 3 layers | `[34, 40)` |

배치가 균등 layer count가 아닌 이유는 노드별 사용 가능 메모리와 placement
계산을 반영하기 때문이다.

### 3.3 모델 보유 상태

`~/models`:

| 모델 | weight 상태 | bytes |
|---|---:|---:|
| DeepSeek R1 Distill Qwen 32B abliterated | 13/13 | 65,527,840,741 |
| Huihui Qwen3.6 35B A3B 4.4bit | 5/5 | 21,188,634,184 |
| Llama 3.2 1B Instruct 4bit | 1/1 | 695,283,921 |
| Qwen3.6 35B A3B 4bit | 4/4 | 20,402,204,271 |
| gemma-4 31B 4bit | 4/4 | 18,412,016,832 |

`~/llm-models/Qwen3.6-35B-A3B-GGUF`에는 GGUF 2개가 있으며 합계
`58,069,898,528` bytes다. Hugging Face cache에는 incomplete 파일 2개가
남아 있다. DeepSeek 디렉터리에도 완료된 weight와 별개로 `.aria2` 잔여 파일
3개가 있으므로 정리 전 실제 다운로드 프로세스가 종료됐는지 다시 확인해야 한다.

기존 [progress_report.md](progress_report.md)에 기록된 단일 노드 실행 결과:

| 모델 | 단일 노드 결과 |
|---|---|
| Huihui Qwen3.6 35B A3B 4.4bit | text generation 성공 |
| Qwen3.6 35B A3B 4bit | text generation 성공 |
| gemma-4 31B 4bit | text generation 성공 |
| DeepSeek R1 Distill Qwen 32B 16bit | Metal OOM |

이 결과는 기존 보고서의 검증 기록이며 이번 작업에서 다시 실행한 것은 아니다.
DeepSeek는 weight가 완전해도 64GB 단일 노드의 실제 여유 메모리보다 커서
quantization 또는 multi-node sharding이 필요하다는 결론이었다.

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

### 4.7 dashboard 역할 분리

dashboard는 end-user chat UI가 아니라 cluster control plane으로 고정하는 방향이다.

- `topologyOnlyMode` 기본값 `true`
- localStorage 값이 없을 때도 topology-only
- topology-only 화면의 exit 버튼 제거
- README/API 문서에서 `52415`와 `8765` 역할 명시

정적 build는 성공하지만 type-check는 실패하므로 완료로 보지 않는다.

### 4.8 Agentic Local Server

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

### 4.9 공개 접근

현재 상태:

- Cloudflare tunnel 프로세스 실행 중
- 공개 HTTPS `/login`: HTTP 200
- `AGENTIC_PUBLIC_URL`: HTTPS로 설정
- `AGENTIC_ALLOWED_HOSTS`: 설정
- HSTS 활성
- Google OAuth client: 미설정

`scripts/start_cloudflare_tunnel.sh`는 config, token file, macOS Keychain 순서로
자격증명을 찾는다.

## 5. 직접 검증 결과

| 검증 | 결과 | 비고 |
|---|---|---|
| shell syntax (`bash -n scripts/*.sh`) | 통과 | 전체 운영 shell |
| 변경 Python compile | 통과 | `compileall` |
| Agentic unit/API tests | 37 passed | 약 5초 |
| exo API tests | 50 passed | 약 0.4초 |
| Claude/macmon/node-ID/plan/runner tests | 121 passed | 약 17초, fork deprecation warning 210개 |
| info gatherer tests | 8 passed | 정상/timeout Thunderbolt poll 포함 |
| 4노드 수정 파일 hash | 통과 | node1/worker 3대 SHA-256 일치 |
| 실제 Thunderbolt poll one-shot | 통과 | identifier 3, connection 3 |
| 4노드 status script | 통과 | 4 nodes/4 connection groups |
| 4노드 verify script | 통과 | topology 4 |
| RDMA verify | 통과 | 3 links, 80Gb/s, placement 성공 |
| 4노드 model file verify | 통과 | 각 노드 5/5 shards |
| control plane HTTP endpoints | 통과 | 주요 state/model API 200 |
| Ollama metadata endpoints | 통과 | tags/show/ps/version |
| Agentic live auth/session/admin | 통과 | 실제 `8765` 호출 |
| 공개 HTTPS login | 통과 | Cloudflare 경유 200 |
| dashboard production build | 통과 | bundle size warning 존재 |
| dashboard `svelte-check` | 실패 | 15 errors, 6 warnings, 9 files |
| 실제 OpenAI 1-token inference | 실패/시간초과 | HTTP header 200 후 45초간 body 0 bytes |

## 6. 오래 걸리거나 환경상 건너뛴 검증

### 6.1 실제 Claude/Ollama/Agentic 대화 생성

OpenAI 1-token 요청이 이미 45초 제한을 초과했다. 같은 model runner를 사용하는
Claude, Ollama generate, Agentic chat을 연속 실행하면 cluster를 더 오래
점유하면서 새로운 정보는 적을 가능성이 높아 건너뛰었다.

예상 시간: 현재 상태에서는 요청당 1분 이상 또는 무기한 대기 가능.

### 6.2 장시간 처리량/안정성 benchmark

다음은 수행하지 않았다.

- 동시 사용자 부하
- 장문 prefill
- tokens/sec, TTFT 측정
- 1시간 이상 연속 생성
- memory leak 관찰

예상 시간: 최소 2~8시간.

### 6.3 worker 장애 복구

실행 중인 worker를 강제 종료하고 topology 축소, task 실패, 재합류, session 복구를
확인하는 시험은 운영 cluster를 의도적으로 중단하므로 건너뛰었다.

예상 시간: 30~60분, 서비스 중단 포함.

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

### P0: 실제 inference가 45초 내 응답하지 않음

runner와 instance는 `Running`이지만 1-token 요청이 body 없이 timeout됐다.
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

완료된 모델 디렉터리에 `.aria2` 3개, GGUF Hugging Face cache에 incomplete 2개가
남아 있다. 실제 weight index는 완전하지만 디스크 정리와 재다운로드 판단을 위해
잔여 파일을 점검해야 한다.

### P2: Google OAuth 미설정

코드는 구현되어 있으나 client ID/secret이 없어 실제 callback flow는 검증하지
못했다.

## 8. 다음 작업 우선순위

1. 실제 inference deadlock/compile 지점 진단
2. Cloudflare token 회전 및 command-line secret 제거
3. AWS credential 폐기 확인
4. dashboard 15개 type error 수정
5. OpenAI 1-token 성공 후 Claude/Ollama/Agentic 실제 생성 재검증
6. TTFT/tokens-per-second benchmark 기록
7. worker failure/rejoin 시험
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

# tests
uv run --package exo-tools pytest tools/tests -q
EXO_DASHBOARD_DIR='exo/dashboard/build' PYTHONPATH=src \
  .venv/bin/pytest -q src/exo/api/tests

# dashboard
cd dashboard
npm run check
npm run build
```
