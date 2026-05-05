# Local Copilot Chat

Local Copilot Chat is a FastAPI application with a static single-page chat UI that logs a browser into GitHub Copilot through the GitHub device flow and sends streamed chat requests through LiteLLM.

The app does not require the old LiteLLM proxy. It builds Copilot request headers locally, calls LiteLLM directly from Python, and checks each browser's encrypted Copilot credential envelope against a session-derived binding tied to an HttpOnly cookie.

## What the app does

- Serves a local chat UI from `static/`.
- Starts GitHub device login and polls for completion through FastAPI endpoints.
- Encrypts the GitHub access token and Copilot API token into a browser-stored credential envelope.
- Checks that envelope against a per-browser-session cookie-derived binding so requests with a different current cookie state are rejected.
- Fetches a best-effort server-side usage snapshot for the logged-in Copilot account and shows remaining Chat messages plus a consistent Premium requests summary across the main usage cards and the auth modal when GitHub returns recognizable data.
- Detects only explicit opt-in web-search intent from the latest user message and, in `searchMode: "auto"`, performs a server-side DuckDuckGo prefetch with a minimized query before LiteLLM streaming.
- Stores visible conversation history in a separate SQLite chat-history database and restores sessions, the active conversation, messages, and model after refresh through a scope derived from the current browser session cookie.
- Streams chat completions back to the browser as Server-Sent Events (SSE).
- Uses `litellm_config.yaml` `model_name` values as the primary allow-list for selectable model IDs, while still prepending `DEFAULT_MODEL` at runtime when the config omits it or cannot produce any valid model.

## Project layout

- `main.py`: FastAPI app, route wiring, and SSE chat endpoint.
- `services/copilot_auth.py`: GitHub device flow, encrypted credential envelopes, session-binding checks, token refresh, and SQLite-backed pending login state.
- `services/copilot_chat.py`: model validation, `searchMode` handling, explicit search-query extraction, lower-trust reference-message preparation, and LiteLLM streaming.
- `services/conversation_service.py`: separate SQLite chat-history persistence, session-bound conversation restore, and partial/aborted assistant transcript saving.
- `services/copilot_headers.py`: locally built Copilot request headers.
- `services/web_search.py`: DuckDuckGo fetching and HTML result parsing used by the server-side search prefetch path.
- `static/index.html`, `static/style.css`, `static/app.js`: browser UI, Copilot login modal, and streamed chat client.
- `test_api.py`: API and service-contract tests for auth, usage, search, and SSE behavior.
- `test_litellm.py`: optional manual smoke test against a real Copilot API token.

## Requirements

- Python 3.10 or newer.
- A GitHub account with GitHub Copilot access if you want to complete the interactive login flow.

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

`main.py` loads `.env` from the repository root automatically if the file exists.

The `.env.example` file intentionally keeps normal runtime setup minimal.

For most setups, `.env` only needs one value:

```dotenv
COPILOT_ENVELOPE_SECRET=replace-with-long-random-secret
```

`DEFAULT_MODEL` is optional if you want a different default than `gpt-5.4`.

If you want to override the chat-history database location, set `COPILOT_CHAT_HISTORY_DB_PATH`.

Do not put your GitHub account token or Copilot user token in `.env` for normal app usage. Normal runtime authentication happens through the browser-based GitHub device flow, and the web app manages the Copilot credential envelope from that login.

## Run the app

Start the FastAPI server:

```bash
uvicorn main:app --reload
```

Open the app in your browser:

```text
http://127.0.0.1:8000
```

Typical flow:

1. Open the page and let the UI check the stored Copilot status.
2. If the top-bar Copilot status button shows a disconnected state, click it once; the auth modal opens and the UI immediately starts GitHub login.
3. Complete the GitHub device flow in the opened browser window, using the shown user code when GitHub asks for it.
4. Wait for the app to keep polling until the modal switches to the connected state.
5. Pick a model from the model selector.
6. Send a prompt and receive a streamed response.

Notes about runtime behavior:

- Chat history is stored server-side in a separate SQLite database. Refreshing the page restores the visible conversation list, active session, messages, and selected model for the current browser session-derived history scope.
- The credential envelope is stored in `localStorage`, but only in encrypted form.
- The cookie used for binding checks is `HttpOnly`, `SameSite=Strict`, and rotated on logout in the current browser.
- Conversation restore derives its scope from the same `HttpOnly`, `SameSite=Strict` session cookie used for Copilot binding checks, so logout rotation clears both envelope validity and history restore for the current browser.
- Normal runtime authentication happens through the browser login flow; users do not paste GitHub or Copilot tokens into `.env`.
- The browser never receives raw GitHub or Copilot tokens; the server only returns an encrypted envelope and a normalized usage snapshot.
- If the Copilot API token is close to expiry during a chat request, the server refreshes it automatically and returns the refreshed envelope in the `X-Copilot-Credential-Envelope` response header.

## Runtime flow

1. `GET /` serves the static chat UI.
2. On startup, the browser calls `POST /api/copilot/status` with the stored envelope, if any.
3. If the browser has no binding cookie yet, the server issues one.
4. `POST /api/copilot/login/start` requests a GitHub device code and stores pending login state in SQLite.
5. The browser polls `POST /api/copilot/login/poll` using a rotating `loginId` handle and server-provided backoff.
6. When GitHub login completes, the server exchanges the GitHub token for a Copilot API token, encrypts the credential session, and returns a browser-bound envelope plus a best-effort usage snapshot.
7. `POST /api/copilot/status` decrypts the envelope for the current browser session and, when valid, tries to fetch a fresh usage snapshot from GitHub.
8. `GET /api/conversations` restores the current browser's conversation list from the separate chat-history SQLite database and re-selects the last active conversation for that session-derived history scope.
9. `POST /api/conversations/{id}/messages` persists the new user message before streaming, saves only visible assistant transcript text, and keeps partial or aborted assistant text when the browser already saw it.
10. `POST /api/chat` remains available as the lower-level raw chat endpoint for compatibility tests and non-persistent callers.
11. `POST /api/copilot/logout` rotates the binding cookie so the logged-out browser's stored envelope no longer matches the new cookie value.

Authentication success and usage availability are separate outcomes. A browser session can be authenticated and still receive `usage.status = partial` or `usage.status = unavailable`.

## API summary

- `GET /`: serves `static/index.html`.
- `GET /api/models`: returns `{ "data": [{ "id": "..." }] }` for allowed models.
- `GET /api/conversations`: returns `{ "sessions": [...], "activeSessionId": "..." }` from the server-side chat-history store for the current browser session-derived history scope.
- `POST /api/conversations`: creates a new persisted conversation and marks it active.
- `POST /api/conversations/{conversationId}/activate`: switches the active persisted conversation for the current browser session-derived history scope.
- `POST /api/conversations/{conversationId}/model`: updates the stored model for that conversation.
- `POST /api/conversations/{conversationId}/messages`: appends one new user message to the server-side transcript, optionally accepts `searchMode`, and streams the assistant reply while persisting visible transcript text only.
- `POST /api/copilot/status`: checks whether the provided credential envelope is valid for the current browser session and, when authenticated, returns a normalized best-effort usage snapshot. All status responses include `ephemeralSecret`; authenticated responses also include `credentialId`, `copilotTokenExpiresAt`, and `needsRefresh`. When the stored envelope is invalid, the response still returns `200` with `authenticated: false`, `shouldClearEnvelope: true`, and a stable `code`/`message` pair.
- `POST /api/copilot/login/start`: starts GitHub device login and returns `loginId`, `userCode`, verification URLs, and poll timing.
- `POST /api/copilot/login/poll`: polls login completion and returns either a pending payload with updated poll timing or a completed credential envelope plus a best-effort usage snapshot.
- `POST /api/copilot/logout`: rotates the current browser-session binding cookie and reports `authenticated: false`.
- `POST /api/chat`: resolves the requested model against the runtime allow-list, validates a non-empty message array, optionally accepts `searchMode`, and then streams SSE chat chunks. If a token refresh happens, the response includes `X-Copilot-Credential-Envelope`.

`POST /api/conversations/{conversationId}/messages` request body shape:

```json
{
	"content": "서울 날씨를 검색해보고 알려줘",
	"model": "gpt-5.4",
	"credentialEnvelope": "opaque-envelope",
	"searchMode": "auto"
}
```

- The browser sends only the new visible user message text; the server loads prior transcript state from SQLite and remains the source of truth for the conversation.
- Only visible `user` and `assistant` transcript text is stored. Server guard messages, search reference messages, credential envelopes, GitHub tokens, and Copilot tokens are never written into the chat-history database.
- If streaming is aborted after visible assistant text was already shown, that partial assistant text is kept and marked server-side for restore; if no visible assistant text was produced, the placeholder is dropped.

`POST /api/chat` request body shape:

```json
{
	"model": "gpt-5.4",
	"messages": [{ "role": "user", "content": "서울 날씨를 검색해보고 알려줘" }],
	"credentialEnvelope": "opaque-envelope",
	"searchMode": "auto"
}
```

- `model` is optional. If omitted, the server uses `DEFAULT_MODEL`. If `DEFAULT_MODEL` is missing from `litellm_config.yaml`, the server prepends it to the runtime allow-list; if the config is missing, invalid, or empty, the default model becomes the only allowed model.
- `messages` must be a non-empty array of objects. Each object must have a non-empty string `role`; `content` may be a string or a list. The current implementation does not enforce a fixed role allow-list or require non-empty text content.
- `searchMode` is optional. The only accepted values are `auto`, `off`, or omission. Omission is normalized to `off`. Blank and whitespace-only values are rejected as invalid.
- The shipped browser UI only sends `searchMode: "auto"` for explicit search requests that use Korean `검색` phrasing such as `서울 날씨를 검색해보고 알려줘`, or explicit English web-search phrasing such as `search for Seoul weather`, `look up Seoul weather`, or `find online Seoul weather`. Otherwise it omits the field.
- Generic local-analysis prompts such as `이 코드 버그를 찾아줘`, `이 함수 동작 알아봐`, `로그인 상태 조회해줘`, or `find the bug in this code` do not opt into external search.
- In `auto`, the server only considers the latest `user` message, extracts a minimized query from explicit search phrasing, and sends only that minimized query to DuckDuckGo.
- If `auto` is present but the latest `user` message does not yield an explicit search query, the server skips external search and continues with ordinary chat.
- Search results are never injected into the `system` role. A static server-controlled system guard is allowed, and the untrusted external titles, URLs, and snippets are passed only in a lower-trust reference message. `X-Initiator` is still computed from the original conversation messages.
- If DuckDuckGo is unreachable or the HTML shape is not recognized, `auto` silently falls back to ordinary chat. The SSE error and `[DONE]` contract stays unchanged.

HTTP JSON error contract for `login/start`, `login/poll`, `chat`, and other non-status failures:

```json
{
	"code": "copilot_login_invalid",
	"message": "로그인 세션을 확인할 수 없습니다. 다시 로그인하세요."
}
```

- `code`: stable machine-readable identifier.
- `message`: fixed user-facing Korean message.
- The browser never receives raw upstream URLs, exception strings, or provider error bodies.

Malformed JSON body contract for body-parsing endpoints (`POST /api/copilot/status`, `POST /api/copilot/login/poll`, `POST /api/conversations`, `POST /api/conversations/{conversationId}/model`, `POST /api/conversations/{conversationId}/messages`, `POST /api/chat`):

```json
{
	"code": "request_validation_failed",
	"message": "요청 형식이 올바르지 않습니다. 다시 시도하세요."
}
```

- These malformed-body responses use HTTP `422`.
- `POST /api/copilot/login/start` does not accept a JSON request body.

SSE chat error contract:

```text
data: {"code":"copilot_chat_stream_failed","message":"채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}
```

- SSE errors use the same stable `code`/`message` pattern before the final `data: [DONE]` event.
- The server emits this sanitized SSE error both when LiteLLM streaming fails before the first chunk and when an upstream chunk looks like an error payload mid-stream.

Usage snapshot shape:

The usage payload is still a best-effort server-side snapshot derived from undocumented GitHub internal endpoints. When GitHub returns authoritative premium quota fields such as `total`, `used`, and `plan`, the server preserves them instead of replacing them with a client-side estimate. When any usage source exposes only premium `remaining` in the `0..100` range and omits premium `used` plus `total`, the server treats that value as percent remaining and normalizes it onto a `total = 100` basis.

```json
{
	"status": "ok | partial | unavailable",
	"reason": "not_authenticated | copilot_usage_pending | copilot_usage_ok | copilot_usage_partial | copilot_usage_unavailable | copilot_usage_auth_failed | copilot_usage_shape_unrecognized",
	"detail": "string | null",
	"source": "copilot_usage_api | copilot_user_api | copilot_usage_other | null",
	"fetchedAt": 1714800000.0,
	"chatMessages": {
		"remaining": 42,
		"used": null,
		"total": null,
		"plan": null,
		"status": "available | missing"
	},
	"premiumRequests": {
		"remaining": 7,
		"used": 23,
		"total": 30,
		"plan": "copilot_pro",
		"status": "available | missing"
	}
}
```

- `ok`: both metrics exposed usable quota basis fields.
- `partial`: GitHub returned a usage payload, but only one metric exposed usable quota basis fields. `detail` is the fixed message `GitHub Copilot 사용량 정보 일부만 확인되었습니다.`
- `unavailable`: the user is unauthenticated or GitHub did not return a usable usage payload.
- `premiumRequests.total`, `premiumRequests.used`, and `premiumRequests.plan` are preserved when the upstream payload exposes them.
- For any `source`, if premium usage only exposes `remaining` in the `0..100` range and omits authoritative `used` plus `total`, the server interprets that as percent remaining and normalizes `premiumRequests.total = 100` and `premiumRequests.used = 100 - remaining`.
- The browser shows Premium requests with the same percent-first primary value in both the main summary card and the auth modal whenever the server provides a usable total basis, including those normalized remaining-only runtime snapshots. If the server cannot establish a reliable total basis, the UI falls back to remaining counts or generic snapshot state.
- `detail` is `null` for `not_authenticated`, `copilot_usage_pending`, and `copilot_usage_ok`.
- `detail` is a fixed user-facing message for `copilot_usage_partial`, `copilot_usage_unavailable`, `copilot_usage_auth_failed`, and `copilot_usage_shape_unrecognized`.
- `source` is a normalized server enum, never a raw upstream URL.
- The browser UI does not render `detail` verbatim. When disconnected it shows a fixed login prompt, when `status` is `ok` it shows the last refreshed timestamp, and otherwise it maps normalized `reason` values to fixed summary text. The auth modal reuses the same summary rule.
- The browser UI does not render premium `plan` or tier metadata in visible labels, tooltips, or meta text.
- When `source` is `copilot_user_api`, `chatMessages.remaining` is `0`, and `premiumRequests.remaining` is numeric, the browser labels `Chat messages` as `무제한`.
- A successful login can still return `partial` or `unavailable`; that does not by itself mean the browser is logged out.

## Configuration

For normal app runtime, `.env.example` is the recommended baseline.

Most users only need:

- `COPILOT_ENVELOPE_SECRET`: stable secret used to encrypt credential envelopes and derive browser-session bindings. If omitted, the app generates an ephemeral in-memory secret and saved envelopes stop working after a restart.

Optional common override:

- `DEFAULT_MODEL`: default model ID used when the request does not specify one. Defaults to `gpt-5.4`.

The app already has built-in defaults for the GitHub device-flow endpoints, cookie settings, token refresh skew, usage endpoints, and pending-login storage. Those values usually do not need to be added to `.env`.

Advanced overrides:

- `COPILOT_PENDING_LOGIN_DB_PATH`: path to the SQLite database used for pending and recently completed device-login state.
- `COPILOT_CHAT_HISTORY_DB_PATH`: path to the separate SQLite database used for persisted chat history.
- `COPILOT_SESSION_COOKIE_SECURE`: set to `true` when serving over HTTPS.
- `COPILOT_SESSION_COOKIE_NAME`, `COPILOT_SESSION_COOKIE_MAX_AGE`: browser-session binding cookie overrides.
- `COPILOT_COMPLETED_LOGIN_TTL_SECONDS`, `COPILOT_TOKEN_REFRESH_SKEW_SECONDS`: login replay and token refresh tuning.
- `GITHUB_COPILOT_CLIENT_ID`, `GITHUB_COPILOT_DEVICE_CODE_URL`, `GITHUB_COPILOT_ACCESS_TOKEN_URL`, `GITHUB_COPILOT_API_KEY_URL`, `GITHUB_COPILOT_USAGE_URLS`: GitHub/Copilot endpoint overrides for advanced debugging or non-default environments.

Normal runtime configuration does not include a user GitHub token or Copilot API token in `.env`. Authentication is expected to happen through the browser login flow.

## Model configuration

The server reads `litellm_config.yaml` to build the allow-list returned by `GET /api/models` and enforced by `POST /api/chat`.

Current example:

```yaml
model_list:
	- model_name: gpt-5.4
		litellm_params:
			model: github_copilot/gpt-5.4
```

At runtime, the application uses the `model_name` values as the selectable IDs and the server-enforced allow-list. If `DEFAULT_MODEL` is not already present, the server prepends it so omitted `model` requests still resolve to an allowed value. If the config file is missing, unreadable, or yields no valid entries, the runtime allow-list falls back to `[DEFAULT_MODEL]`.

If the browser cannot load `/api/models`, the client falls back locally to its built-in `gpt-5.4` default until a valid model list is available.

## Testing

Run the automated test suite:

```bash
python test_api.py
```

The current `test_api.py` suite covers API and service-level contracts such as:

- form-encoded GitHub device-flow requests
- poll throttling and backoff behavior
- replay safety for older `loginId` handles
- shared SQLite pending-login state across service instances
- separate SQLite-backed chat history state and refresh restore through the conversation API using the existing auth session cookie scope
- logout causing the current browser to reject its stored envelope after cookie rotation
- normalized best-effort usage snapshots on status and login completion, including remaining-only premium snapshots from runtime usage sources
- usage endpoint fallback and partial metric handling
- explicit auto-search opt-in from the latest user message only
- minimized provider queries and lower-trust server-side search reference injection before LiteLLM streaming
- silent fallback to ordinary chat when the search provider fails
- visible transcript-only persistence, excluding system/search reference messages and credentials
- assistant partial/aborted transcript save policy for interrupted streams
- scrubbed server logs for search and streaming failures plus auto-search SSE passthrough/error fallback coverage
- sanitized `code`/`message` error responses for status/login/chat/SSE paths
- automatic envelope refresh during chat requests
- SSE error contract when LiteLLM streaming fails at startup or emits an error-like mid-stream chunk

This suite does not run a real browser session, a real GitHub device-flow login, or a real LiteLLM chat call.

Optional manual LiteLLM smoke test:

```bash
python test_litellm.py
```

This script is separate from normal app runtime configuration. It is only for a manual smoke test and directly calls LiteLLM with a token you provide.

This script requires:

- `GITHUB_COPILOT_API_TOKEN`: a real user-specific Copilot API token
- optional `COPILOT_TEST_MODEL`: model ID to call, default `gpt-5.4`
- optional `GITHUB_COPILOT_API_BASE`: API base URL, default `https://api.githubcopilot.com`

These variables are not part of the usual `.env` setup for the web app. The browser-based login flow used by `main.py` does not require `GITHUB_COPILOT_API_TOKEN`.

## Notes and limitations

- Pending GitHub device-login state is stored server-side in SQLite, with WAL enabled, so multiple service processes on the same host can coordinate login polling safely.
- Chat history is stored in a separate SQLite file from the auth/login state, keyed by a scope derived from the browser's current auth session cookie and restored from the server on refresh.
- Completed login results are temporarily replayable so a lost success response or duplicate poll can still receive the same credential envelope.
- The browser only stores the encrypted envelope, never the raw Copilot token in plain text.
- Logout rotates the current browser's binding cookie and makes that browser revalidate stored envelopes, but the server does not keep a separate revocation list for any pre-logout cookie-plus-envelope pair that was already copied.
- Only visible transcript messages are persisted. Search guard/reference messages are recomputed per request and are not saved in the chat-history database.
- Usage is fetched server-side with the authenticated GitHub session and returned as normalized quota metadata plus stable `reason/detail/source` fields; the browser never receives raw tokens, upstream URLs, or exception text.
- Copilot request headers are built locally in `services/copilot_headers.py`; the app does not depend on LiteLLM internal helper modules for this.
- The UI includes a model picker, persisted multi-session chat restore, markdown-like rendering, code-block copy buttons, and a single composer button that switches from `보내기` to `중지`, aborts the in-flight request, and then shows a stopped composer status plus a toast.
- The usage endpoints are not a documented public contract. The app currently tries `copilot_internal/v2/usage` first and falls back to `copilot_internal/user`, then normalizes recognized quota fields such as `remaining`, `used`, `total`, and `plan` when present. If GitHub changes those responses, the API and UI degrade to `partial` or `unavailable` instead of guessing premium percentages from hardcoded totals.
- A successful browser login and a successful usage display are separate concerns. The app treats usage as best-effort metadata and does not report every usage failure as an authentication failure.