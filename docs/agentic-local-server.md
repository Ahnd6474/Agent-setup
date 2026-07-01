# Agentic Local Server

> 최종 확인: 2026-06-30

Agentic Local Server는 OpenAI-compatible API를 LLM backend로 사용해 계정별
로컬 채팅 세션과 대화 기록을 제공하는 별도 FastAPI 서비스다. 코딩 실행은
제공하지 않으며 Claude Code가 exo의 Anthropic-compatible API를 직접 사용한다.

## 8765와 52415의 역할

| 주소 | 역할 | 주요 기능 |
|---|---|---|
| `node1:8765` | 사용자 애플리케이션 | 로그인, 계정, 채팅 세션, 기억, 세션별 자원 |
| `node1:52415` | exo 클러스터 제어면 | 노드·토폴로지 관측, 모델 다운로드·배치, 인스턴스 관리, 운영 진단, inference API |
| `node1:52416` | exo 내부 전송 | libp2p 노드 통신 |

사용자는 `8765`에서 작업한다. `8765`는 필요할 때 `52415/v1`을 내부 LLM
backend로 호출한다. `52415` 웹 패널은 채팅 입력을 제공하지 않으며 클러스터
리소스, 모델 배치와 인스턴스 상태만 관리한다. 운영 진단은 `52415/v1` API를
직접 호출한다.

## 현재 구조

```text
사용자 브라우저
  └─ node1:8765 Agentic 사용자 GUI/API
       ├─ ~/.agentic-local/store       Git 기반 채팅 세션 기록
       ├─ ~/.agentic-local/auth        SQLite 계정·login session (node1 only)
       └─ 내부 OpenAI-compatible backend
            ├─ 현재: node1:8080/v1 llama.cpp
            └─ 선택: node1:52415/v1 exo 제어면의 4노드 모델

클러스터 운영자
  └─ node1:52415 exo 모니터링·관리 패널/API
```

Agent 서버와 대화 저장은 node1에서 실행된다. `node2`~`node4`는 exo 모델
inference에 참여하는 노드다.

## 시작 전 조건

1. exo topology에 4개 노드가 보여야 한다.
2. 사용할 모델 인스턴스가 생성 가능해야 한다.
3. `tools/pyproject.toml`의 FastAPI/uvicorn 의존성이 설치돼 있어야 한다.

현재 llama.cpp backend 설정으로 시작:

```bash
cd /Users/dshs_llm/exo
AGENTIC_LLM_BASE_URL=http://127.0.0.1:8080/v1 \
AGENTIC_LLM_MODEL=llama-cpp \
uv run --package exo-tools agent-server
```

기본 listen 주소는 `127.0.0.1:8765`다.

채팅 입력창의 추론 수준은 llama.cpp backend에 다음과 같이 전달된다.

| UI | Thinking | 요청별 budget |
|---|---:|---:|
| `즉시` | 끔 | 0 |
| `낮음` | 켬 | 1,024 tokens |
| `중간` | 켬 | 4,096 tokens |
| `높음` | 켬 | 8,192 tokens |
| `최고` | 켬 | 제한 없음 |

선택값은 브라우저에 유지된다. llama.cpp는 `--reasoning auto`로 실행해야
요청별 `chat_template_kwargs.enable_thinking` 설정을 적용할 수 있다.

streaming 응답의 `reasoning_content`는 답변 본문과 분리해 “생각 과정” 패널에
표시한다. 생성 중에는 패널이 열린 상태로 갱신되고 완료 후 사용자가 접거나 다시
펼칠 수 있다. assistant placeholder를 요청 시작 즉시 만들고, 추론과 최종 답변을
streaming 중 주기적으로 session의 `messages.jsonl`에 별도 필드로 저장한다. 따라서
생성 중 새로 고침하거나 연결이 끊겨도 마지막으로 받은 내용까지 복구된다.

채팅 생성은 브라우저 연결과 분리된 서버 background job으로 실행된다. 사용자가
다른 세션으로 이동하거나 stream 연결을 닫아도 job은 끝까지 실행되어 저장된다.
같은 세션에서 응답 생성 중 추가 질문을 보낼 수 있으며, 추가 질문은 즉시
placeholder와 함께 저장된 뒤 대화 문맥 순서를 보존하도록 세션별 queue에서
순차 처리된다. 서로 다른 세션의 queue는 독립적으로 실행된다.

JPEG, PNG, WebP, GIF 이미지는 data URL로 OpenAI-compatible multimodal
`image_url` content에 전달된다. 요청당 최대 4개, 이미지당 data URL 12 MB까지
허용한다. 실제 분석에는 backend 모델 카드의 `vision` capability와 vision
processor/weights가 필요하다.

최초 한 번 master/admin 계정을 생성한다.

```bash
uv run --package exo-tools agent-account bootstrap
```

변경 불가능한 username `master`, `admin`이 생성되고 임시 비밀번호는 mode
`0600`인 `~/.agentic-local/auth/bootstrap-credentials.txt`에만 기록된다.
첫 로그인 후 각 계정 페이지에서 비밀번호를 변경한다.

### 계정 생성 Python API

`exo_tools.accounts`는 로컬 관리 자동화를 위한 공개 Python API다. HTTP 계정
관리와 동일한 역할 제한을 적용한다.

```python
from exo_tools.accounts import create_account

account = create_account(
    "operator",
    "replace-with-a-long-temporary-password",
    "admin",
    actor_username="master",
    actor_password="master-password",
)
print(account.user_id, account.username, account.role)
```

기본 저장 위치는 `$AGENTIC_HOME/auth/auth.db`이며 `AGENTIC_HOME`이 없으면
`~/.agentic-local`을 사용한다. 테스트나 별도 인스턴스에서는 `home`을 지정한다.

```python
from pathlib import Path

from exo_tools.accounts import create_account

account = create_account(
    "developer",
    "replace-with-a-long-temporary-password",
    actor_username="admin",
    actor_password="admin-password",
    home=Path("/srv/agentic-local"),
)
```

권한 규칙:

- admin은 `user` 계정만 생성할 수 있다.
- master는 `user`와 `admin` 계정을 생성할 수 있다.
- `master` 계정은 빈 DB에서 `bootstrap_accounts()`로만 생성한다.
- username은 3~32자의 영문자·숫자·`_`·`-` 조합이며 소문자로 정규화된다.
- 비밀번호는 최소 12자, UTF-8 기준 최대 256 bytes다.
- 인증·권한 실패는 `PermissionError`, 잘못된 입력이나 중복 username은
  `ValueError`를 발생시킨다.

빈 DB를 Python에서 초기화하는 예:

```python
from exo_tools.accounts import bootstrap_accounts

result = bootstrap_accounts()
print(result.credentials_path)
```

임시 비밀번호는 반환된 `BootstrapResult`에도 포함되지만 `repr`에서는 숨기며,
mode `0600`인 `bootstrap-credentials.txt`에도 기록한다. 계정이 하나라도 있으면
`bootstrap_accounts()`는 `RuntimeError`를 발생시킨다.

환경 변수:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AGENTIC_HOST` | `127.0.0.1` | Agent 서버 listen 주소 |
| `AGENTIC_PORT` | `8765` | Agent 서버 port |
| `AGENTIC_HOME` | `~/.agentic-local` | 채팅 store와 auth root |
| `AGENTIC_LLM_BASE_URL` | `http://127.0.0.1:52415/v1` | OpenAI API base URL |
| `AGENTIC_LLM_MODEL` | `local-agentic-model` | 요청에 넣을 모델 ID |
| `AGENTIC_LLM_API_KEY` | `x` | Bearer token |
| `AGENTIC_LLM_TIMEOUT_S` | `300` | LLM HTTP timeout |
| `AGENTIC_TITLE_LLM_BASE_URL` | `AGENTIC_LLM_BASE_URL` | 세션 제목 생성용 OpenAI API base URL |
| `AGENTIC_TITLE_LLM_MODEL` | `mlx-community/Llama-3.2-1B-Instruct-4bit` | 새 채팅의 첫 메시지를 요약해 제목을 만드는 소형 모델 |
| `AGENTIC_TITLE_LLM_TIMEOUT_S` | `10` | 제목 생성용 LLM HTTP timeout. 실패 시 첫 메시지 기반 제목으로 fallback |
| `AGENTIC_CLUSTER_CONTROL_URL` | `AGENTIC_LLM_BASE_URL`의 origin | admin/master 리소스 관리 버튼이 여는 52415 주소 |
| `AGENTIC_MAX_MESSAGES_PER_SESSION` | `100` | working tree에 유지할 session message 수 |
| `AGENTIC_MAX_GIT_COMMITS` | `50` | Git snapshot history squash 기준 |

## API

주요 endpoint:

| Method | Path | 기능 |
|---|---|---|
| `GET` | `/` | 로컬 웹 UI |
| `GET/POST` | `/login`, `/auth/login` | 로컬 계정 로그인 |
| `GET` | `/account` | 비밀번호 변경·Google 연결 |
| `GET` | `/admin` | 계정·역할·권한 관리 |
| `POST` | `/sessions` | 세션 생성 |
| `GET` | `/sessions` | 세션 목록 |
| `GET` | `/sessions/{id}` | 대화와 resource 조회 |
| `DELETE` | `/sessions/{id}` | 세션과 대화 기록 삭제 |
| `POST` | `/sessions/{id}/messages` | 일반 채팅 |
| `POST` | `/sessions/{id}/messages/stream` | reasoning/content NDJSON streaming 채팅 |
| `GET/PUT` | `/sessions/{id}/resources` | session quota 조회·변경 |

## resource allocation의 실제 의미

- `compute_slots`: 해당 Agent 서버 프로세스 안에서 session별 동시 LLM 호출을
  제한하는 semaphore다.
- `compute_nodes`: 현재 metadata와 prompt context에 기록되지만 exo placement를
  직접 예약하거나 강제하지는 않는다.
- `disk_quota_bytes`: session store의 저장 한도다.
- `memory_message_limit`, `memory_char_limit`: LLM에 다시 전달할 대화 history 한도다.

즉, 이 resource layer는 Kubernetes식 물리 자원 scheduler가 아니다. 실제 모델
배치는 exo master가 담당한다.

## 대화 저장 용량 관리

- `messages.jsonl`과 사람이 읽는 `chat.md`에는 기본적으로 최근 100개 message만
  남긴다.
- 기존 Markdown session은 최초 사용 시 JSONL로 자동 이관한다.
- Git commit이 50개에 도달하면 현재 snapshot만 남긴 orphan commit으로
  history를 교체하고 reflog/object를 즉시 prune한다.
- 따라서 working file에서 삭제된 오래된 대화가 Git object에 무기한 남지 않는다.

## 인증과 권한

- 비밀번호는 node1에서 `hashlib.scrypt`와 사용자별 random salt로 hash한다.
- login token은 SHA-256 hash만 SQLite에 저장하며 cookie는 HttpOnly/SameSite다.
- public HTTPS 환경에서는 Secure cookie와 HSTS를 활성화한다.
- 역할 계층은 `master > admin > user`다.
- master만 admin을 생성·변경할 수 있고 admin은 기본 user만 관리한다.
- 기본 user는 자기 session의 chat/resource만 접근한다.
- username 변경 API는 제공하지 않는다.

## Claude Code

코딩 작업은 Agentic Local Server를 경유하지 않는다. Claude Code 사용자 설정의
`ANTHROPIC_BASE_URL`을 `http://127.0.0.1:52415`로 두면 Claude Code가 exo의
`/v1/messages`를 직접 호출한다. 설정과 실행 예시는
[`cluster-plan-ko.md`](cluster-plan-ko.md#8-claude-code-연동)를 참고한다.

## 검증

Agent 서버 단위/API 테스트:

```bash
uv run --package exo-tools pytest tools/tests -q
```

2026-06-30 기준 결과는 auth/retention 테스트를 포함한다.

실제 LLM end-to-end 검증은 별도다. 단위 테스트의 `FakeLLM` 통과만으로 실제
backend의 streaming, reasoning, vision 응답을 보장하지는 않는다.
