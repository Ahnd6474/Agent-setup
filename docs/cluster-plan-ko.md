# 4대 Mac mini 분산 LLM 서비스 실행 계획

이 문서는 4대 Mac mini로 exo 클러스터를 만들고 MiniMax M3를 실제로 돌리기 위한 실행 계획입니다.
핵심은 아래 3가지입니다.

- 4대 Mac mini를 하나의 클러스터처럼 묶어 큰 모델을 돌린다
- 웹 UI와 OpenAI-compatible API로 서비스를 제공한다
- Claude Code까지 연결해서 실제 개발 도구로 사용할 수 있게 만든다

## 1. 목표

- Apple Silicon Mac mini 4대를 내부망에 연결한다
- exo/MLX 기반 분산 추론으로 단일 대형 모델을 나눠서 실행한다
- master node는 API와 웹 UI 진입점 역할을 한다
- worker 3대는 같은 클러스터에 참여해 모델 연산을 분담한다
- Claude Code는 exo의 Claude Messages 호환 엔드포인트로 연결한다

## 2. 권장 구성

- 노드 수: 4대
- 권장 기종: Mac mini M4 또는 M4 Pro 계열
- 메모리: 각 64GB unified memory
- 네트워크: Thunderbolt Bridge 또는 10GbE 우선, 최소 유선 Ethernet
- 비권장: Wi-Fi

예시 고정 IP:

- Master: `10.80.118.167`
- Worker 1: `10.80.118.168`
- Worker 2: `10.80.118.169`
- Worker 3: `10.80.118.170`

## 3. 동작 구조

요청 흐름은 다음 순서로 잡는다.

`Client -> Nginx/Caddy 또는 API Gateway -> exo API/dashboard -> 4대 Mac mini 클러스터`

제어와 데이터 경로는 분리한다.

- 제어 경로
  - node1이 SSH로 worker 프로세스를 시작/중지/재시작한다
  - exo API가 모델 배치, 인스턴스 생성, 상태 확인을 담당한다
- 데이터/텐서 통신 경로
  - exo의 `MlxJaccl` + `Tensor` placement를 사용한다
  - Thunderbolt RDMA 인터페이스는 JACCL backend가 사용한다
  - fallback이나 초기 검증은 Thunderbolt TCP/Bridge로 가능하지만, 최종 목적은 RDMA-backed placement다

역할은 다음과 같다.

- master node
  - `uv run exo` 실행
  - OpenAI-compatible API 제공
  - 웹 UI 접속 지점 제공
  - 전체 추론 작업 조정
- worker node 3대
  - `uv run exo` 실행
  - 모델 일부와 연산 일부 담당

## 4. 모델 선택

기준은 다음 순서로 본다.

- 최신성
- 성능
- 메모리 적합성
- GGUF 지원 여부

exo에서 실제로 바로 구동하기 좋은 모델:

- `pipenetwork/MiniMax-M3-MLX-4bit`
- 이유: 이 저장소는 MLX 백엔드 기반이기 때문에 GGUF보다 바로 연결하기 쉽다

참고:

- llama.cpp/GGUF 경로를 쓰는 별도 클러스터라면 `unsloth/MiniMax-M3-GGUF`를 쓰면 된다
- 이 저장소에서는 모델 카드와 대시보드 연동을 위해 MLX 4bit 변환본을 우선 사용한다

대체 후보:

- `Qwen3-235B-A22B` 계열

## 5. 구축 순서

### 5-1. 네트워크 준비

1. 4대 Mac mini에 고정 IP를 설정한다
2. SSH 접속을 모두 활성화한다
3. 같은 내부망에서 서로 ping 또는 SSH가 되는지 확인한다
4. Thunderbolt Bridge 또는 10GbE 링크가 실제로 연결됐는지 확인한다
5. RDMA/JACCL 경로를 쓸 때는 Thunderbolt Bridge 대신 각 Thunderbolt 포트를 DHCP 서비스로 구성한다

SSH를 항상 켜두려면 각 Mac mini에서 아래 명령을 한 번 실행한다.

```bash
sudo /usr/sbin/systemsetup -setremotelogin on
/usr/sbin/systemsetup -getremotelogin
```

이 설정은 재부팅 후에도 Remote Login을 유지한다.

RDMA/JACCL 경로를 구성하려면 각 Mac mini에서 아래를 실행한다.

```bash
cd ~/exo
scripts/configure_rdma_network.sh
```

4대에 한 번에 적용하려면 node1에서 `scripts/cluster.env`를 먼저 맞춘 뒤 실행한다.

```bash
cp scripts/cluster.env.example scripts/cluster.env
scripts/configure_rdma_network_on_cluster.sh
```

### 5-2. 개발 도구 설치

모든 장비에 다음을 설치한다.

- Homebrew
- `cmake`
- `git`
- `git-lfs`
- `uv`
- `node`
- Rust nightly

### 5-3. exo dashboard 빌드

master node에서 대시보드를 빌드한다.

```bash
cd dashboard && npm install && npm run build && cd ..
```

### 5-4. 모델 다운로드

master node에서 모델을 내려받는다.

```bash
uv run python scripts/download_model_to_cluster.py pipenetwork/MiniMax-M3-MLX-4bit --host <master-host>
```

- 모델이 모든 노드에 정상적으로 내려받아지는지 확인한다
- GUI에서 모델 카드가 보이는지 확인한다

### 5-5. worker 실행

4대 Mac mini에서 모두 `uv run exo`를 실행한다.

- master 노드는 대시보드와 API 진입점 역할을 한다
- 나머지 3대는 같은 클러스터에 참여해 연산을 분담한다
- 내부망에서만 통신되도록 네트워크와 namespace를 맞춘다

### 5-6. master 실행

master node에서 `uv run exo`를 실행한 뒤 대시보드에서 MiniMax M3 모델을 선택한다.

- 초기 `context size`는 작은 값부터 시작한다
- 안정화되면 더 큰 context로 늘린다
- 필요하면 `EXO_LIBP2P_NAMESPACE`로 클러스터를 분리한다

node1에서 한 번에 실행하려면:

```bash
scripts/start_4node_exo_cluster.sh
```

RDMA/JACCL placement를 확인한다.

```bash
scripts/verify_rdma_cluster.sh
```

RDMA-backed tensor parallel 인스턴스를 생성한다.

```bash
scripts/place_rdma_instance.sh
```

## 6. API 검증

먼저 `curl`로 `/v1/chat/completions`를 시험한다.

예시:

```bash
curl -N -X POST http://localhost:52415/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "pipenetwork/MiniMax-M3-MLX-4bit",
    "messages": [
      {"role": "user", "content": "한국어로 짧게 자기소개해줘"}
    ],
    "stream": true
  }'
```

확인 항목:

- 응답이 정상적으로 생성되는가
- 스트리밍이 끊기지 않는가
- 한국어 응답이 자연스러운가

## 7. Claude Code 연동

이 저장소는 이미 Claude Messages API 호환 엔드포인트인 `/v1/messages`를 제공한다.
따라서 Claude Code는 공식 설정 변수인 `ANTHROPIC_BASE_URL`을 exo 게이트웨이 주소로 지정해서 이 엔드포인트로 연결할 수 있다.

연동 원칙:

- Claude Code의 요청 대상(base URL)을 exo 게이트웨이로 맞춘다
- 내부적으로는 `/v1/messages`를 사용한다
- 인증은 Claude Code 쪽 설정과 게이트웨이 정책을 함께 맞춘다
- 외부 공개가 필요하면 master 앞단에 Nginx 또는 Caddy를 둔다

운영 순서:

1. master 또는 gateway에서 `/v1/messages`가 정상 동작하는지 먼저 확인한다
2. Claude Code 공식 설정에서 custom base URL을 exo 게이트웨이로 지정한다
3. 인증 토큰과 rate limit을 별도로 둔다
4. Claude Code로 짧은 질의응답부터 검증한다
5. 긴 코드 수정 작업과 다중 파일 편집까지 확장한다

공식 문서 참고:

- https://docs.anthropic.com/en/docs/claude-code/llm-gateway
- https://docs.anthropic.com/en/docs/claude-code/iam

## 8. 웹 UI 계획

1단계:

- llama.cpp 내장 web UI로 우선 검증

2단계:

- Next.js 또는 React 기반 커스텀 UI 구현
- 채팅 인터페이스
- 응답 스트리밍
- 대화 세션 관리
- 프롬프트 템플릿
- 관리자 페이지
- 서버 상태 표시
- API 사용량 표시

## 9. 보안 계획

- Nginx 또는 Caddy reverse proxy 사용
- HTTPS 적용
- API key 인증 적용
- rate limit 적용
- 내부망과 외부망 분리
- worker RPC 포트는 외부에 열지 않음

## 10. 검증 항목

### 기능 검증

- 모델 로딩 성공
- worker 연결 성공
- API 응답 성공
- 웹 UI 응답 성공
- 한국어 질의응답 성공
- 코드 생성 성공

### 성능 검증

- 첫 토큰 생성 시간
- 초당 생성 토큰 수
- 전체 응답 시간
- 각 Mac mini 메모리 사용량
- CPU/GPU 사용률
- 네트워크 사용량
- 동시 요청 처리 수

### 안정성 검증

- 장시간 실행 시 crash 여부
- worker 중단 시 동작
- 긴 prompt 처리 가능 여부
- context size 증가에 따른 안정성
- 반복 요청 시 메모리 누수 여부

### TFLOPS 환산

exo 벤치마크는 기본적으로 `generation_tps`를 측정한다. 합산 TFLOPS는 아래처럼 근사할 수 있다.

```text
estimated_TFLOPS ≈ generation_tps × 2 × active_params / 1e12
```

MiniMax M3처럼 MoE 모델은 총 파라미터가 아니라 **활성 파라미터**를 사용해야 한다.
예를 들어 활성 파라미터가 약 23B이고 generation_tps가 100이면 대략 4.6 TFLOPS로 본다.

실측 결과를 넣어 계산하려면:

```bash
scripts/estimate_tflops.py --generation-tps 100 --active-params-b 23
```

## 11. 리스크와 대응

- 메모리 부족
  - `context size`를 작게 시작하고 점진적으로 늘린다
- 네트워크 병목
  - Wi-Fi를 쓰지 않고 유선 연결을 우선한다
- RPC 안정성
  - 내부망에서만 사용하고 직접 외부 노출을 피한다
- 모델 호환성
  - 최신 llama.cpp 빌드를 우선 사용하고 대체 모델을 준비한다

## 12. 실행 체크리스트

- [ ] 4대 Mac mini IP/SSH 설정
- [ ] 내부망 통신 확인
- [ ] 개발 도구 설치
- [ ] llama.cpp 빌드
- [ ] 모델 다운로드
- [ ] worker 3대 실행
- [ ] master 실행
- [ ] `/v1/chat/completions` 검증
- [ ] `/v1/messages` 검증
- [ ] Claude Code 연결
- [ ] 웹 UI 확장
- [ ] 성능/안정성 측정

## 13. 바로 실행하는 준비 스크립트

이 저장소에는 4대 Mac mini를 한 번에 준비하는 스크립트가 있다.

```bash
scripts/prepare_4node_minimax_m3_cluster.sh \
  10.80.118.167 10.80.118.168 10.80.118.169 10.80.118.170 \
  my-dev-cluster
```

이 스크립트는 다음을 해준다.

- 각 노드에 넣을 정확한 실행 명령을 출력한다
- master 노드에 SSH로 접속해 exo를 실행한다
- `EXO_LIBP2P_NAMESPACE`를 고정해서 서로 다른 클러스터와 섞이지 않게 한다
- Claude Code용 `ANTHROPIC_BASE_URL` 예시를 같이 보여준다

각 Mac mini에서 SSH를 부팅 시 자동 활성화하려면 다음을 실행한다.

```bash
scripts/enable_sshd_on_boot.sh
```

## 14. 다운로드 완료 후 실제 실행 명령

### Master

```bash
cd ~/exo
EXO_LIBP2P_NAMESPACE=my-dev-cluster EXO_OFFLINE=true \
uv run exo --force-master --api-port 52415 --libp2p-port 0
```

### Worker 1

```bash
cd ~/exo
EXO_LIBP2P_NAMESPACE=my-dev-cluster EXO_OFFLINE=true \
uv run exo --api-port 52415 --libp2p-port 0
```

### Worker 2

```bash
cd ~/exo
EXO_LIBP2P_NAMESPACE=my-dev-cluster EXO_OFFLINE=true \
uv run exo --api-port 52415 --libp2p-port 0
```

### Worker 3

```bash
cd ~/exo
EXO_LIBP2P_NAMESPACE=my-dev-cluster EXO_OFFLINE=true \
uv run exo --api-port 52415 --libp2p-port 0
```

### 웹 UI

master가 올라오면 브라우저에서 바로 연다.

```text
http://10.80.118.167:52415
```

### Claude Code

CLI로 바로 실행할 때:

```bash
export ANTHROPIC_BASE_URL=http://10.80.118.167:52415
export ANTHROPIC_API_KEY=x
export ANTHROPIC_DEFAULT_OPUS_MODEL=pipenetwork/MiniMax-M3-MLX-4bit
export ANTHROPIC_DEFAULT_SONNET_MODEL=pipenetwork/MiniMax-M3-MLX-4bit
export ANTHROPIC_DEFAULT_HAIKU_MODEL=pipenetwork/MiniMax-M3-MLX-4bit
export API_TIMEOUT_MS=3000000
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
claude
```

설정 파일로 저장할 때는 [docs/claude-code-settings.example.json](/Users/dshs_llm/exo/docs/claude-code-settings.example.json)을 `~/.claude/settings.json`으로 옮기면 된다.
