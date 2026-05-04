# Local Copilot Chat

Local Copilot Chat is a FastAPI application with a static single-page chat UI that logs a browser into GitHub Copilot through the GitHub device flow and sends streamed chat requests through LiteLLM.

The app does not require the old LiteLLM proxy. It builds Copilot request headers locally, calls LiteLLM directly from Python, and binds each browser's encrypted Copilot credential envelope to that browser session with an HttpOnly cookie.

## What the app does

- Serves a local chat UI from `static/`.
- Starts GitHub device login and polls for completion through FastAPI endpoints.
- Encrypts the GitHub access token and Copilot API token into a browser-stored credential envelope.
- Binds that envelope to a per-browser-session cookie so it cannot be replayed from another browser session.
- Fetches a best-effort server-side usage snapshot for the logged-in Copilot account and shows remaining Chat messages and Premium requests in the UI when GitHub returns recognizable data.
- Streams chat completions back to the browser as Server-Sent Events (SSE).
- Uses `litellm_config.yaml` as a safe allow-list for selectable model IDs.

## Project layout

- `main.py`: FastAPI app, route wiring, and SSE chat endpoint.
- `services/copilot_auth.py`: GitHub device flow, encrypted credential envelopes, session binding, token refresh, and SQLite-backed pending login state.
- `services/copilot_chat.py`: model validation and LiteLLM streaming.
- `services/copilot_headers.py`: locally built Copilot request headers.
- `static/index.html`, `static/style.css`, `static/app.js`: browser UI, Copilot login modal, and streamed chat client.
- `test_api.py`: API and auth-flow tests.
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

The checked-in `.env.example` intentionally keeps normal runtime setup minimal.

For most setups, `.env` only needs one value:

```dotenv
COPILOT_ENVELOPE_SECRET=replace-with-long-random-secret
```

`DEFAULT_MODEL` is optional if you want a different default than `gpt-5.4`.

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

1. Open the Copilot status button in the top bar.
2. Start GitHub login.
3. Complete the GitHub device flow in the opened browser window.
4. Wait for the app to poll until login completes.
5. Pick a model from the model selector.
6. Send a prompt and receive a streamed response.

Notes about runtime behavior:

- Chat history lives only in browser memory. Refreshing the page resets the visible conversation list.
- The credential envelope is stored in `localStorage`, but only in encrypted form.
- The cookie binding is `HttpOnly`, `SameSite=Strict`, and rotated on logout.
- Normal runtime authentication happens through the browser login flow; users do not paste GitHub or Copilot tokens into `.env`.
- The browser never receives raw GitHub or Copilot tokens; the server only returns an encrypted envelope and a normalized usage snapshot.
- If the Copilot API token is close to expiry, the server refreshes it automatically and returns the refreshed envelope in the `X-Copilot-Credential-Envelope` response header.

## Runtime flow

1. `GET /` serves the static chat UI.
2. On startup, the browser calls `POST /api/copilot/status` with the stored envelope, if any.
3. If the browser has no binding cookie yet, the server issues one.
4. `POST /api/copilot/login/start` requests a GitHub device code and stores pending login state in SQLite.
5. The browser polls `POST /api/copilot/login/poll` using a rotating `loginId` handle and server-provided backoff.
6. When GitHub login completes, the server exchanges the GitHub token for a Copilot API token, encrypts the credential session, and returns a browser-bound envelope plus a best-effort usage snapshot.
7. `POST /api/copilot/status` decrypts the envelope for the current browser session and, when valid, tries to fetch a fresh usage snapshot from GitHub.
8. `POST /api/chat` decrypts the envelope, refreshes tokens if needed, and streams LiteLLM output as SSE.
9. `POST /api/copilot/logout` rotates the binding cookie so previously issued envelopes become invalid.

Authentication success and usage availability are separate outcomes. A browser session can be authenticated and still receive `usage.status = partial` or `usage.status = unavailable`.

## API summary

- `GET /`: serves `static/index.html`.
- `GET /api/models`: returns `{ "data": [{ "id": "..." }] }` for allowed models.
- `POST /api/copilot/status`: checks whether the provided credential envelope is valid for the current browser session and, when authenticated, returns a normalized best-effort usage snapshot. When the stored envelope is invalid, the response still returns `200` with `authenticated: false`, `shouldClearEnvelope: true`, and a stable `code`/`message` pair.
- `POST /api/copilot/login/start`: starts GitHub device login and returns `loginId`, `userCode`, verification URLs, and poll timing.
- `POST /api/copilot/login/poll`: polls login completion and returns either a pending payload with updated poll timing or a completed credential envelope plus a best-effort usage snapshot.
- `POST /api/copilot/logout`: rotates the browser-session binding and reports `authenticated: false`.
- `POST /api/chat`: validates the requested model and message list, then streams SSE chat chunks. If a token refresh happens, the response includes `X-Copilot-Credential-Envelope`.

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

Malformed JSON body contract for body-parsing endpoints (`POST /api/copilot/status`, `POST /api/copilot/login/poll`, `POST /api/chat`):

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

Usage snapshot shape:

The usage payload is a best-effort server-side snapshot derived from undocumented GitHub internal endpoints. It is useful for UI display, but it is not treated as a guaranteed or authoritative quota contract.

```json
{
	"status": "ok | partial | unavailable",
	"reason": "not_authenticated | copilot_usage_pending | copilot_usage_ok | copilot_usage_partial | copilot_usage_unavailable | copilot_usage_auth_failed | copilot_usage_shape_unrecognized",
	"detail": "string | null",
	"source": "copilot_usage_api | copilot_user_api | copilot_usage_other | null",
	"fetchedAt": 1714800000.0,
	"chatMessages": {
		"remaining": 42,
		"status": "available | missing"
	},
	"premiumRequests": {
		"remaining": 7,
		"status": "available | missing"
	}
}
```

- `ok`: both remaining counts were found.
- `partial`: GitHub returned a usage payload, but only one metric could be normalized. `detail` is the fixed message `GitHub Copilot 사용량 정보 일부만 확인되었습니다.`
- `unavailable`: the user is unauthenticated or GitHub did not return a usable usage payload.
- `detail` is `null` for `not_authenticated`, `copilot_usage_pending`, and `copilot_usage_ok`.
- `detail` is a fixed user-facing message for `copilot_usage_partial`, `copilot_usage_unavailable`, `copilot_usage_auth_failed`, and `copilot_usage_shape_unrecognized`.
- `source` is a normalized server enum, never a raw upstream URL.
- The browser UI does not render `detail` verbatim; it maps normalized `reason` and `status` values to fixed summary text.
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

At runtime, the application currently uses the `model_name` values as the selectable and allowed model IDs. Keep that list in sync with the models you want exposed in the UI.

## Testing

Run the automated test suite:

```bash
python test_api.py
```

The current suite covers:

- form-encoded GitHub device-flow requests
- poll throttling and backoff behavior
- replay safety for older `loginId` handles
- shared SQLite pending-login state across service instances
- logout invalidating previously issued envelopes
- normalized best-effort usage snapshots on status and login completion
- usage endpoint fallback and partial metric handling
- sanitized `code`/`message` error responses for status/login/chat/SSE paths
- automatic envelope refresh during chat requests
- SSE error contract when LiteLLM streaming fails at startup or emits an error-like mid-stream chunk

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
- Completed login results are temporarily replayable so a lost success response or duplicate poll can still receive the same credential envelope.
- The browser only stores the encrypted envelope, never the raw Copilot token in plain text.
- Usage is fetched server-side with the authenticated GitHub session and returned only as normalized remaining counts plus stable `reason/detail/source` metadata; the browser never receives raw tokens, upstream URLs, or exception text.
- Copilot request headers are built locally in `services/copilot_headers.py`; the app does not depend on LiteLLM internal helper modules for this.
- The UI includes a model picker, multiple local chat sessions, markdown-like rendering, code-block copy buttons, and a stop button for in-flight streaming.
- The usage endpoints are not a documented public contract. The app currently tries `copilot_internal/v2/usage` first and falls back to `copilot_internal/user`, then normalizes recognized remaining-count fields. If GitHub changes those responses, the API and UI degrade to `partial` or `unavailable` instead of guessing values.
- A successful browser login and a successful usage display are separate concerns. The app treats usage as best-effort metadata and does not report every usage failure as an authentication failure.