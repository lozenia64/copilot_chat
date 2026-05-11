# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 명령어

```bash
# 의존성 설치
pip install -r requirements.txt

# 개발 서버 (UI + API 가 http://127.0.0.1:8000 에서 함께 제공됨)
uvicorn main:app --reload

# 또는 main.py 직접 실행 (reload=False 로 고정 — 코드 변경이 자동 반영되지 않음)
python main.py
```

자동화 테스트 파일은 현재 저장소에 포함되어 있지 않다.

`.env` 파일은 없어도 서버는 실행되지만 정상 운영 시 `COPILOT_ENVELOPE_SECRET` 만이라도 두는 것을 권장한다. 이 값이 없으면 서버를 재시작할 때마다 모든 envelope 가 무효화된다. GitHub/Copilot 토큰은 절대 `.env` 에 직접 넣지 말 것 — 인증은 브라우저 내 GitHub device flow 로 이루어진다. `main` 브랜치에 push 하면 `.github/workflows/deploy.yml` 이 Oracle Cloud 로 자동 배포한다 (`/home/ubuntu/copilot_chat` 진입 → `git pull` → `sudo systemctl restart copilot-chat`).

## 아키텍처

FastAPI 백엔드 + 정적 SPA 프론트엔드 구조로, LiteLLM 을 통해 GitHub Copilot 으로 스트리밍 채팅 요청을 프록시한다. 설계 전체가 다음 불변식을 중심으로 돈다: **브라우저는 암호화된 credential envelope 만 저장하고, 서버는 평문 토큰을 저장하지 않는다. 다만 세션 binding 쿠키, pending/completed login 상태, 대화 transcript 는 SQLite/쿠키에 저장된다.** Copilot 토큰을 필요로 하는 모든 요청은 envelope (`localStorage` 보관) 와 쿠키 (`HttpOnly`, `SameSite=Strict`) 를 함께 제시해야 하며, 두 값이 함께 있을 때만 복호화된다. 로그아웃은 쿠키를 회전시키므로 해당 브라우저의 envelope 가 소급적으로 무효화된다.

### 요청 배선 (`main.py`)

`main.py` 는 의도적으로 얇게 유지된다. 라우트 핸들러, 서비스 예외(`CopilotAuthError`, `CopilotChatRequestError`, `ConversationStateError`)와 `RequestValidationError` 를 정제된 `{code, message}` JSON 으로 변환하는 4개의 예외 핸들러, 그리고 binding 쿠키를 발급/회전시키고 거기서 conversation scope id 를 파생시키는 `_resolve_browser_session_context` 헬퍼로 구성된다. **인증이 필요한 모든 엔드포인트는 이 헬퍼를 거쳐야 하며, 우회하지 말 것.**

### 서비스 레이어 (`services/`)

`main.py` 에서 싱글턴으로 배선되는 3개의 서비스:

- **`CopilotAuthService`** (`copilot_auth.py`) — credential 관련 모든 것을 담당: GitHub device flow start/poll, 암호화된 envelope 생성, 세션 binding 검증(envelope 복호화 시 현재 쿠키의 session secret 필요), 만료된 Copilot API 토큰 자동 refresh, GitHub `/user` 로부터 `github_user_id`/`github_login` 확보, 그리고 GitHub 의 비공개 usage 엔드포인트(`copilot_internal/v2/usage` → `copilot_internal/user`) 에서 best-effort usage snapshot 조회. 진행 중/최근 완료된 device flow 티켓은 SQLite (`PendingLoginStore`) 에 WAL 모드로 저장되어 다수의 워커가 로그인 상태를 공유할 수 있다. 완료된 로그인 replay 를 위해 암호화된 credential envelope 도 TTL 동안 SQLite 에 저장되며, poll 응답이 유실되거나 중복 poll 이 들어와도 같은 envelope 를 다시 받을 수 있다.

- **`CopilotChatService`** (`copilot_chat.py`) — `litellm_config.yaml` 의 `model_name` 값을 런타임 모델 allow-list 로 로드하고, `messages` 를 검증하며, `litellm.acompletion(custom_llm_provider="openai", base_url=session.copilot_api_base, ...)` 을 호출한다. **`resolve_litellm_model(model_id)`** 가 사용자/UI 가 보는 model_id (`model_name`) 를 실제 LiteLLM 호출용 provider model (`litellm_params.model`) 로 변환한다 — 둘은 다를 수 있고, allow-list 강제는 `model_name` 기준이지만 upstream 호출은 provider model 기준이다. **`stream_chat_completion` 은 단일 호출이 아니라 도구 호출 루프**다: 최대 `MAX_TOOL_CALL_ITERATIONS` (기본 3) 회까지 모델을 다시 호출하면서, `web_search` tool_call 이 나오면 `services/web_search.py` 의 `WebSearchClient` 로 서버 측에서 실행하고 그 결과를 `tool` role 메시지로 다음 라운드 messages 에 첨부한다. **도구 호출 델타는 외부 SSE 로 절대 나가지 않으며** (`_accumulate_tool_call_deltas` 가 내부 누적기에 쌓고, `_extract_visible_text_payload` 는 텍스트 콘텐츠만 전달), 브라우저에는 검색 진행 표시(`🔎 웹 검색 중: <query>`)와 최종 어시스턴트 텍스트만 흘러간다. 알 수 없는 tool 이름이나 검색 실패는 sanitized JSON 오류 페이로드를 `tool` 메시지로 모델에 돌려주므로 스트림은 종료되지 않는다. `_is_error_like_stream_payload` 가 상류의 mid-stream 오류 페이로드를 감지하면 표준 `{code, message}` SSE 오류로 변환한 뒤 `data: [DONE]` 으로 종료시킨다. `_stream_error_payload` 는 `model_not_supported` / `rate limit` 같은 특정 오류를 사용자 친화 메시지로 매핑한다. `extra_headers` 는 `copilot_headers.build_copilot_headers()` 에서 로컬로 생성되며 LiteLLM 내부 헤더 헬퍼에 의존하지 않는다. `X-Initiator` 는 실제 provider 로 보낸 메시지가 아닌 **원래 대화 메시지인** `initiator_messages` 에서 계산하므로 도구 호출 라운드가 추가되어도 값이 흔들리지 않는다.

- **`WebSearchClient`** (`web_search.py`) — DuckDuckGo lite 를 우선 시도하고 봇 차단(challenge) 페이지면 html 엔드포인트로 폴백. 핵심 설계 두 가지: (1) **속성 순서·따옴표 종류에 독립적인 파서** — a 태그 전체를 매칭한 뒤 attrs 문자열에서 `class=...` 토큰과 `href=...` 값을 별도 정규식으로 추출하기 때문에 lite 의 `<a href="..." class='result-link'>` 와 html 의 `<a class="result__a" href="...">` 모두 잡힌다. (2) **봇 차단 응답 감지** — HTTP 202 또는 본문에 `anomaly.js?sv=html` / `cc=botnet` / `challenge-form` 마커가 있으면 결과 없음으로 잘못 해석하지 않고 다음 엔드포인트로 폴백. 연속 호출 사이에는 `asyncio.Lock` + `time.monotonic` 기반의 2초 cooldown 을 강제해 봇 점수 누적을 막는다. 모든 엔진이 실패하면 예외 대신 빈 리스트를 반환하고, `format_tool_result_content` 의 note (`"...tell the user that the live web search is currently unavailable."`) 는 호출자에서 모델에 전달된다. 실제 사용자에게 그 문구가 그대로 노출될지는 최종 모델 응답에 따라 달라진다. Sec-Ch-Ua / Sec-Fetch-* 같은 Chrome 전용 client hints 는 의도적으로 보내지 않는다 — 헤더와 TLS 지문 사이의 mismatch 가 더 강한 봇 신호이기 때문이다.

- **`ConversationService`** (`conversation_service.py`) — auth 와는 *별도의* SQLite DB 를 사용한다. **`scope_id` 가 두 가지**다: 로그인된 사용자는 `user:<github_user_id>` 형식이고, 익명 (인증되지 않은) 상태는 브라우저 쿠키 기반 scope. 같은 GitHub 사용자가 다른 브라우저/기기에서 같은 서버에 로그인하면 사용자 scope 의 대화가 복원된다. **익명 대화와 로그인 후 대화는 자동 병합되지 않는다.** 트랜스크립트의 source of truth 는 브라우저가 아니라 서버다: `begin_turn` 이 새 사용자 메시지를 저장하고 upstream 으로 보낼 `visible_messages` 를 반환하며, `persist_stream` 이 SSE 제너레이터를 래핑해 각 `data:` 프레임을 디코딩하면서 assistant 메시지를 증분 업데이트하고, `[DONE]` 에서 finalize 하며, 연결이 끊겨도 partial text 를 보존한다. **사용자 지정 제목은 보존된다** — `begin_turn` 은 기존 제목이 `DEFAULT_CONVERSATION_TITLE` (`"새 대화"`) 일 때만 첫 사용자 메시지에서 제목을 도출하므로, `update_conversation_title` 로 한 번이라도 직접 바꾼 제목은 이후 첫 메시지 전송으로 덮어쓰이지 않는다. 제목 수정 (`POST /api/conversations/{id}/title`) 은 `validate_conversation_title` 로 trim/공백 압축/`MAX_CONVERSATION_TITLE_LENGTH` (80자) 잘림을 거치고, 삭제 (`POST /api/conversations/{id}/delete`) 는 단건 행 제거 + 활성 대화였다면 가장 최근 대화로 active 포커스 회전 후 전체 상태 페이로드를 반환한다. **`updated_at` 이 7일을 넘은 대화는 read/write 경로의 opportunistic cleanup 에서 제거**되며, 메시지는 `ON DELETE CASCADE` 로 함께 삭제된다.

### SSE 와 오류 계약

서로 구분되는 두 가지 오류 형식이 있으며, 변경 시 둘을 함께 맞춰야 한다:

- 비스트림 JSON 오류와 SSE 오류 모두 `{code, message}` 를 사용하며 사용자에게 노출되는 한국어 문구는 안정적으로 유지된다. upstream URL 이나 예외 텍스트 원문은 절대 브라우저로 노출되지 않는다.
- SSE 스트림은 `data: [DONE]` 으로 종료된다. 오류 시에는 `data: {"code":"...","message":"..."}` 후 `[DONE]` 을 emit. `ConversationService._extract_sse_payload` / `_is_stream_error_payload` 가 persist 측에서 동일 형식을 파싱하므로 `CopilotChatService._stream_error_payload` 와 항상 동기 상태를 유지해야 한다.
- JSON body 가 잘못된 경우 `RequestValidationError` 핸들러가 일괄 처리하여 `request_validation_failed` / 422 로 반환한다.

### 채팅 진입점이 두 개라는 점

`POST /api/conversations/{id}/messages` (영구 저장) 와 `POST /api/chat` (raw — 비영구 호출자용) 가 `CopilotChatService.stream_chat_completion` 을 공유한다. 영구 저장 경로는 같은 스트림을 `ConversationService.persist_stream` 으로 래핑한다. 양쪽 모두에 영향을 주는 기능은 둘 모두에 반영해야 하지만, *raw* `/api/chat` 은 DB 쓰기를 전혀 하지 않는다.

### 프론트엔드 (`static/`)

SPA 단일 페이지 앱. `app.js` 가 로그인 모달, 모델 picker, 다중 세션 복원, abort 가능한 SSE 소비 (`보내기` ↔ `중지` 버튼 전환), 마크다운 풍 렌더링, 코드 블록 복사를 담당한다. **`WEB_SEARCH_TOOL_SPEC` 은 프론트엔드에서 정의**되어 모든 채팅 요청 본문에 `tools: [WEB_SEARCH_TOOL_SPEC]` / `tool_choice: "auto"` / `parallel_tool_calls: false` 와 함께 전송된다. 실제 검색 실행과 tool-call 루프는 모두 백엔드에서 처리되므로 브라우저는 텍스트 SSE 소비만 담당한다. 새 도구를 추가하려면 `WEB_SEARCH_TOOL_SPEC` 옆에 함수 스펙을 더하고 `CHAT_TOOLS_PAYLOAD` 에 포함시킨 뒤 `services/copilot_chat.py` 의 `_execute_tool_call` 에 라우팅을 추가하면 된다. 사이드바 각 대화 카드에는 상시 노출되는 연필/휴지통 아이콘이 있어 `beginSessionTitleEdit` (인라인 입력 + Enter/blur 저장, Esc 취소) 와 `deleteSession` (`window.confirm` 후 `POST /api/conversations/{id}/delete` 호출, 응답 상태로 사이드바 재구성) 으로 동작한다. 응답 생성 중인 활성 대화는 삭제가 거부되며 사용자에게 중지 후 시도하라는 안내 토스트를 띄운다.

### 모델 allow-list

`litellm_config.yaml` 의 `model_name` 값이 `GET /api/models` 에서 반환되고 `resolve_model` 에서 강제되는 allow-list 를 정의한다. `litellm_params.model` 은 별도로 보관되어 `resolve_litellm_model` 에서 실제 upstream 호출용으로 사용된다. `DEFAULT_MODEL` (환경 변수, 기본값 `gpt-5.4`) 이 config 에 없으면 런타임에 prepend 된다. config 파일이 없거나 비어 있거나 잘못된 경우 allow-list 는 `[DEFAULT_MODEL]` 로 fallback — "아무 모델이나 허용" 모드는 없다.

### usage snapshot 해석

GitHub 의 내부 usage 엔드포인트는 계정 유형별로 응답 shape 가 다르다. `CopilotAuthService` 는 세 가지 shape 를 best-effort 로 해석한다:

1. **Free** (`access_type_sku == free_limited_copilot`, `limited_user_quotas`, `monthly_quotas`) — `chat` 사용량만 신뢰 가능하게 표시하고, `premiumRequests` 를 직접 계산할 근거가 없으면 그 영역을 숨긴다.
2. **Pro / paid** (`quota_snapshots`, `premium_interactions`, `chat.unlimited`) — `premium_interactions.remaining / entitlement` 로 premium 을 계산하고 `chat.unlimited == true` 면 Chat 을 `무제한` 으로 해석한다.
3. **Generic fallback** — `usage.remaining_chat_messages` 등 기존 score-based 구조에서 읽을 수 있는 필드만 추출.

usage 실패는 인증 실패로 간주하지 않는다.

### SQLite DB

의도적으로 분리된 두 개의 파일이며 둘 다 WAL 사용:

- `COPILOT_PENDING_LOGIN_DB_PATH` → `.copilot_pending_login.sqlite3` (auth / device flow 상태)
- `COPILOT_CHAT_HISTORY_DB_PATH` → `.copilot_chat_history.sqlite3` (가시 트랜스크립트만)

로그아웃 시 쿠키가 회전되면서 익명 scope 의 `scope_id` 가 바뀌므로 새 브라우저 상태에서는 이전 익명 히스토리에 도달할 수 없게 된다. 하지만 사용자 scope (`user:<github_user_id>`) 의 대화는 그대로 남아 다음 로그인 시 복원된다.

## 코드 수정 전에 알아 둘 것

- auth, scope 파생, 쿠키 발급 로직을 `_resolve_browser_session_context` 밖으로 옮기지 말 것. binding / rotation / scope 불변식이 이 헬퍼의 일관성에 의존한다.
- 암호화된 envelope 는 브라우저에 대해 opaque 상태를 유지해야 한다. GitHub access token 과 Copilot API token 원본은 응답 body, 로그, 저장된 행 어디에도 노출되어서는 안 된다.
- 채팅 요청 중 `auth_service.resolve_session` 내부에서 토큰 refresh 가 발생하면, refresh 된 envelope 는 `X-Copilot-Credential-Envelope` 응답 헤더로 반환된다 — `/api/chat`, `/api/conversations/{id}/messages`, `/api/copilot/status` 모두 이 헤더를 설정하므로 새 스트리밍/상태 경로를 추가할 때도 반드시 유지할 것.
- LiteLLM 호출 시 model 인자에는 사용자가 본 `model_id` 가 아니라 `resolve_litellm_model(model_id)` 의 결과 (`provider_model`) 를 전달할 것. 두 값을 혼동하면 allow-list 는 통과해도 upstream 호출이 실패한다.
- 7일 TTL cleanup 은 read/write 경로 안에서 opportunistic 으로 일어난다. 별도 cron 이나 startup 작업이 없으므로 정리 트리거를 우회하는 새 코드 경로를 추가하지 말 것.
- 동작 중인 서버 (`python main.py`) 는 `reload=False` 다. 코드 변경이 자동 반영되지 않으므로, 로컬에서 코드를 자주 고칠 때는 `uvicorn main:app --reload` 로 띄우는 것을 권장.
- `.github/agents/` 의 16개 agent 정의는 내부 다중 에이전트 개발 워크플로 (Chief_Agent 오케스트레이터 + 전문 에이전트들) 를 기술한 문서다. Claude Code 가 자동으로 로드하지 *않는다* — 런타임 설정이 아니라 인간 워크플로용 설계 문서로 다룰 것.
