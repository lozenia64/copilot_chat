const DEFAULT_MODEL = window.APP_CONFIG?.defaultModel ?? "gpt-5.4";
const CREDENTIAL_STORAGE_KEY = window.APP_CONFIG?.credentialStorageKey ?? "copilotCredentialEnvelope";
const SERVER_ERROR_MESSAGES = Object.freeze({
    conversation_message_required: "보낼 메시지 또는 이미지를 추가하세요.",
    conversation_attachments_invalid: "첨부 이미지 형식이 올바르지 않습니다.",
    conversation_attachments_limit_exceeded: "이미지는 한 번에 최대 5개까지 전송할 수 있습니다.",
    conversation_not_found: "대화 세션을 찾을 수 없습니다. 새 대화를 시작하세요.",
    conversation_title_required: "대화 제목을 입력하세요.",
    attachment_already_attached: "이미 전송에 사용된 첨부 이미지입니다. 다시 업로드하세요.",
    attachment_decode_failed: "이미지 파일을 처리할 수 없습니다. 다른 파일을 선택하세요.",
    attachment_file_too_large: "이미지 1개당 5MB 이하 업로드만 허용됩니다.",
    attachment_image_only: "이미지 파일만 첨부할 수 있습니다.",
    attachment_not_found: "첨부 이미지를 찾을 수 없습니다. 다시 업로드하세요.",
    chat_messages_invalid: "채팅 메시지 형식이 올바르지 않습니다. 다시 시도하세요.",
    chat_model_not_allowed: "선택한 모델은 사용할 수 없습니다. 목록에서 다시 선택하세요.",
    chat_model_not_vision_capable: "선택한 모델은 이미지 첨부를 지원하지 않습니다. 다른 모델을 선택하세요.",
    chat_model_required: "채팅 모델을 확인할 수 없습니다. 다시 시도하세요.",
    copilot_access_token_failed: "GitHub 인증을 완료하지 못했습니다. 다시 시도하세요.",
    copilot_access_token_missing: "GitHub 액세스 토큰을 가져오지 못했습니다.",
    copilot_access_token_unreachable: "GitHub 로그인 상태를 확인하지 못했습니다. 잠시 후 다시 시도하세요.",
    copilot_api_token_invalid_expiry: "GitHub Copilot 토큰 만료 시각을 해석하지 못했습니다.",
    copilot_api_token_missing: "GitHub Copilot 토큰 응답에 token 이 없습니다.",
    copilot_api_token_unreachable: "GitHub Copilot 토큰을 가져오지 못했습니다. 잠시 후 다시 시도하세요.",
    copilot_chat_stream_failed: "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요.",
    copilot_model_not_supported: "현재 로그인 되어있는 GitHub 계정에서는 해당 모델을 사용할 수 없습니다.",
    copilot_rate_limit_exceeded: "주간 사용량 한도를 초과하여 채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요.",
    copilot_credential_binding_mismatch: "저장된 GitHub Copilot 자격 정보가 현재 브라우저 세션과 맞지 않습니다. 다시 로그인하세요.",
    copilot_credential_invalid: "저장된 GitHub Copilot 자격 정보를 복호화할 수 없습니다. 다시 로그인하세요.",
    copilot_device_flow_invalid_response: "GitHub 로그인 응답에 필요한 코드 정보가 없습니다.",
    copilot_device_flow_unreachable: "GitHub 로그인 요청을 시작하지 못했습니다. 잠시 후 다시 시도하세요.",
    copilot_json_invalid: "응답 형식을 처리하지 못했습니다. 잠시 후 다시 시도하세요.",
    copilot_login_denied: "GitHub 인증이 거부되었습니다.",
    copilot_login_expired: "GitHub 로그인 코드가 만료되었습니다. 다시 로그인하세요.",
    copilot_login_invalid: "로그인 세션을 확인할 수 없습니다. 다시 로그인하세요.",
    copilot_login_required: "이 브라우저의 GitHub Copilot 자격 정보가 없습니다. 먼저 로그인하세요.",
    copilot_login_session_mismatch: "현재 브라우저 세션과 맞지 않는 로그인 요청입니다. 다시 시도하세요.",
    copilot_upstream_error: "상류 응답을 처리하지 못했습니다. 잠시 후 다시 시도하세요.",
    request_validation_failed: "요청 형식이 올바르지 않습니다. 다시 시도하세요.",
});
const USAGE_STATUS_VALUES = Object.freeze(["ok", "partial", "unavailable"]);
const USAGE_SUMMARY_REASON_MESSAGES = Object.freeze({
    not_authenticated: "GitHub 로그인 후 서버가 조회한 usage snapshot을 표시합니다.",
    copilot_usage_pending: "GitHub usage snapshot을 아직 받지 못했습니다.",
    copilot_usage_ok: "",
    copilot_usage_partial: "GitHub usage 응답에 일부 지표만 포함되어 있습니다.",
    copilot_usage_unavailable: "GitHub usage snapshot을 조회할 수 없습니다.",
    copilot_usage_auth_failed: "GitHub 인증 상태를 다시 확인한 뒤 usage snapshot을 조회하세요.",
    copilot_usage_shape_unrecognized: "GitHub usage 응답 형식을 확인하지 못했습니다.",
});
const COMPOSER_READY_MESSAGE = "전송 준비 완료";
const COMPOSER_STREAMING_MESSAGE = "응답 생성 중";
const COMPOSER_ABORTED_MESSAGE = "응답 생성이 중단됨";
const COMPOSER_STATUS_HOLD_MS = 2600;
const MOBILE_VIEWPORT_MAX_WIDTH = 960;
const COMPACT_VIEWPORT_MAX_HEIGHT = 720;
const SIDEBAR_OVERLAY_HIDE_DELAY_MS = 180;
const SIDEBAR_COLLAPSE_STORAGE_KEY = "localChatSidebarCollapsed";
const MESSAGE_SCROLL_BOTTOM_THRESHOLD = 28;
const MESSAGE_HISTORY_VIEWPORT_OFFSET = 20;
const MESSAGE_HISTORY_TOP_OFFSET = 18;
const MAX_ATTACHMENTS_PER_MESSAGE = 5;
const MAX_ORIGINAL_IMAGE_BYTES = 15 * 1024 * 1024;
const TARGET_COMPRESSED_IMAGE_BYTES = 5 * 1024 * 1024;
const INITIAL_IMAGE_MAX_DIMENSION = 2048;
const SECONDARY_IMAGE_MAX_DIMENSION = 1600;
const INITIAL_JPEG_QUALITY = 0.86;
const MIN_JPEG_QUALITY = 0.55;
const JPEG_QUALITY_STEP = 0.08;
const COMPOSER_ATTACHMENT_BUSY_STATES = new Set(["compressing", "uploading", "deleting"]);
// 매 채팅 요청에 함께 보내는 OpenAI 형식의 web_search function tool 스펙.
// 사용자가 명시적으로 "검색해줘" 라고 말하지 않더라도, 모델이 최신 정보·실시간
// 데이터·외부 출처가 필요하다고 판단하면 자동으로 이 도구를 호출한다. 실제 검색
// 실행은 서버 측에서 이루어지고, 브라우저는 텍스트 SSE 만 소비한다.
const WEB_SEARCH_TOOL_SPEC = Object.freeze({
    type: "function",
    function: {
        name: "web_search",
        description:
            "최신 정보, 실시간 데이터, 외부 출처가 필요한 사용자 요청에 답하기 위해 웹을 검색한다. " +
            "사용자가 명시적으로 검색을 요청한 경우뿐 아니라, 현재 시점의 날씨/뉴스/시세/공식 발표 등 모델이 자체 지식만으로 정확히 답할 수 없는 경우에 사용한다.",
        parameters: {
            type: "object",
            properties: {
                query: {
                    type: "string",
                    description: "검색 엔진에 입력할 검색어. 사용자의 의도를 압축한 키워드 형태로 작성한다.",
                },
            },
            required: ["query"],
            additionalProperties: false,
        },
    },
});
const CHAT_TOOLS_PAYLOAD = Object.freeze([WEB_SEARCH_TOOL_SPEC]);
const CHAT_TOOL_CHOICE = "auto";
const ASSISTANT_STATUS_MESSAGES = Object.freeze({
    web_searching: "🔎 웹 검색 중",
});
const USAGE_VISUAL_CONFIG = Object.freeze({
    chatMessages: { low: 5, medium: 20, high: 60 },
});

function createEmptyUsageMetric() {
    return {
        remaining: null,
        used: null,
        total: null,
        plan: null,
        unlimited: false,
        status: "missing",
    };
}

function createEmptyUsageSnapshot(reason = "not_authenticated") {
    return {
        status: "unavailable",
        reason,
        detail: null,
        source: null,
        accessTypeSku: null,
        fetchedAt: 0,
        chatMessages: createEmptyUsageMetric(),
        premiumRequests: createEmptyUsageMetric(),
    };
}

const state = {
    sessions: [],
    activeSessionId: null,
    models: [],
    isStreaming: false,
    abortController: null,
    ui: {
        isCompactUsage: false,
        isUsageSummaryCollapsed: false,
        isSidebarCollapsed: window.localStorage.getItem(SIDEBAR_COLLAPSE_STORAGE_KEY) === "true",
        sidebarOverlayTimerId: null,
    },
    scroll: {
        mode: "latest",
    },
    composerStatus: {
        message: COMPOSER_READY_MESSAGE,
        tone: "ready",
        resetTimerId: null,
    },
    copilot: {
        envelope: window.localStorage.getItem(CREDENTIAL_STORAGE_KEY) ?? "",
        status: "checking",
        modalOpen: false,
        isAuthorizing: false,
        loginId: null,
        loginIntervalMs: 5000,
        loginExpiresAt: 0,
        userCode: "",
        verificationUri: "",
        verificationUriComplete: "",
        credentialId: null,
        copilotTokenExpiresAt: 0,
        errorMessage: "",
        authPromise: null,
        loginAbortController: null,
        needsRefresh: false,
        ephemeralSecret: false,
        usage: createEmptyUsageSnapshot(),
    },
    composerAttachments: [],
};

const elements = {
    appShell: document.getElementById("appShell"),
    sessionList: document.getElementById("sessionList"),
    messagePanelToolbar: document.getElementById("messagePanelToolbar"),
    messages: document.getElementById("messages"),
    messageHistoryJumpButton: document.getElementById("messageHistoryJumpButton"),
    newChatButton: document.getElementById("newChatButton"),
    composerForm: document.getElementById("composerForm"),
    composerDropzone: document.getElementById("composerDropzone"),
    composerAttachmentList: document.getElementById("composerAttachmentList"),
    attachmentInput: document.getElementById("attachmentInput"),
    attachmentButton: document.getElementById("attachmentButton"),
    promptInput: document.getElementById("promptInput"),
    sendButton: document.getElementById("sendButton"),
    sendButtonText: document.getElementById("sendButtonText"),
    modelSelect: document.getElementById("modelSelect"),
    toastContainer: document.getElementById("toastContainer"),
    authStatusButton: document.getElementById("authStatusButton"),
    authStatusText: document.getElementById("authStatusText"),
    usageSummary: document.getElementById("usageSummary"),
    usageSummaryPanel: document.getElementById("usageSummaryPanel"),
    usageSummaryToggle: document.getElementById("usageSummaryToggle"),
    chatMessagesCard: document.getElementById("chatMessagesCard"),
    chatMessagesRemaining: document.getElementById("chatMessagesRemaining"),
    chatMessagesChartFill: document.getElementById("chatMessagesChartFill"),
    chatMessagesMeta: document.getElementById("chatMessagesMeta"),
    premiumRequestsCard: document.getElementById("premiumRequestsCard"),
    premiumRequestsRemaining: document.getElementById("premiumRequestsRemaining"),
    premiumRequestsChartFill: document.getElementById("premiumRequestsChartFill"),
    premiumRequestsMeta: document.getElementById("premiumRequestsMeta"),
    usageSummaryDetail: document.getElementById("usageSummaryDetail"),
    streamStatus: document.getElementById("streamStatus"),
    authModal: document.getElementById("authModal"),
    authModalTitle: document.getElementById("authModalTitle"),
    authModalDescription: document.getElementById("authModalDescription"),
    authStatusMeta: document.getElementById("authStatusMeta"),
    authCodeSection: document.getElementById("authCodeSection"),
    authUserCode: document.getElementById("authUserCode"),
    authVerificationLink: document.getElementById("authVerificationLink"),
    authConnectedSection: document.getElementById("authConnectedSection"),
    authCredentialId: document.getElementById("authCredentialId"),
    authExpiryText: document.getElementById("authExpiryText"),
    authChatMessagesRemaining: document.getElementById("authChatMessagesRemaining"),
    authPremiumRequestsRemaining: document.getElementById("authPremiumRequestsRemaining"),
    authUsageDetail: document.getElementById("authUsageDetail"),
    authPrimaryButton: document.getElementById("authPrimaryButton"),
    authSecondaryButton: document.getElementById("authSecondaryButton"),
    authModalCloseButton: document.getElementById("authModalCloseButton"),
    sidebar: document.getElementById("sidebar"),
    sidebarOverlay: document.getElementById("sidebarOverlay"),
    menuButton: document.getElementById("menuButton"),
    toggleSidebarButton: document.getElementById("toggleSidebarButton"),
    closeMenuButton: document.getElementById("closeMenuButton"),
};

function createId(prefix) {
    if (window.crypto?.randomUUID) {
        return `${prefix}-${window.crypto.randomUUID()}`;
    }

    return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function persistCredentialEnvelope(envelope) {
    state.copilot.envelope = envelope || "";
    if (state.copilot.envelope) {
        window.localStorage.setItem(CREDENTIAL_STORAGE_KEY, state.copilot.envelope);
        return;
    }

    window.localStorage.removeItem(CREDENTIAL_STORAGE_KEY);
}

function handleUnauthorizedCopilot(message, options = {}) {
    const shouldClearComposerAttachments = Boolean(options.clearComposerAttachments);
    persistCredentialEnvelope("");
    state.copilot.status = "disconnected";
    state.copilot.errorMessage = message;
    resetUsageSnapshot("not_authenticated");
    if (shouldClearComposerAttachments) {
        clearComposerAttachments();
    }
    openAuthModal();
    renderAuthState();
}

function normalizeServerAttachment(attachment) {
    return {
        id: typeof attachment?.id === "string" && attachment.id ? attachment.id : createId("attachment"),
        fileName: typeof attachment?.fileName === "string" && attachment.fileName ? attachment.fileName : "image.jpg",
        mimeType: typeof attachment?.mimeType === "string" && attachment.mimeType ? attachment.mimeType : "image/jpeg",
        byteSize: Number.isFinite(Number(attachment?.byteSize)) ? Number(attachment.byteSize) : 0,
        width: Number.isFinite(Number(attachment?.width)) ? Number(attachment.width) : null,
        height: Number.isFinite(Number(attachment?.height)) ? Number(attachment.height) : null,
        contentUrl: typeof attachment?.contentUrl === "string" ? attachment.contentUrl : "",
    };
}

function normalizeMessageSources(sources) {
    if (!Array.isArray(sources)) {
        return [];
    }

    const normalizedSources = [];
    const seenUrls = new Set();
    sources.forEach((source) => {
        const title = typeof source?.title === "string" ? source.title.trim() : "";
        const url = normalizeSourceUrl(source?.url);
        if (!title || !url || seenUrls.has(url)) {
            return;
        }
        normalizedSources.push({ title, url });
        seenUrls.add(url);
    });
    return normalizedSources;
}

function normalizeSourceUrl(url) {
    if (typeof url !== "string") {
        return "";
    }

    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
        return "";
    }

    try {
        const parsedUrl = new URL(trimmedUrl);
        if (parsedUrl.protocol !== "http:" && parsedUrl.protocol !== "https:") {
            return "";
        }
    } catch {
        return "";
    }

    return trimmedUrl;
}

function normalizeServerMessage(message) {
    return {
        id: typeof message?.id === "string" && message.id ? message.id : createId("message"),
        role: typeof message?.role === "string" && message.role ? message.role : "assistant",
        content: typeof message?.content === "string" ? message.content : "",
        attachments: Array.isArray(message?.attachments) ? message.attachments.map(normalizeServerAttachment) : [],
        sources: normalizeMessageSources(message?.sources),
        status: typeof message?.status === "string" && message.status ? message.status : "complete",
        createdAt: Number.isFinite(Number(message?.createdAt)) ? Number(message.createdAt) : Date.now(),
        updatedAt: Number.isFinite(Number(message?.updatedAt)) ? Number(message.updatedAt) : Date.now(),
    };
}

function normalizeServerSession(session) {
    const messages = Array.isArray(session?.messages)
        ? session.messages.map(normalizeServerMessage)
        : [];

    return {
        id: typeof session?.id === "string" && session.id ? session.id : createId("session"),
        title: typeof session?.title === "string" && session.title.trim() ? session.title : "새 대화",
        messages,
        model: typeof session?.model === "string" && session.model.trim() ? session.model : DEFAULT_MODEL,
        createdAt: Number.isFinite(Number(session?.createdAt)) ? Number(session.createdAt) : Date.now(),
        updatedAt: Number.isFinite(Number(session?.updatedAt)) ? Number(session.updatedAt) : Date.now(),
    };
}

function replaceSession(sessionPayload) {
    const nextSession = normalizeServerSession(sessionPayload);
    const index = state.sessions.findIndex((session) => session.id === nextSession.id);
    if (index === -1) {
        state.sessions.unshift(nextSession);
        return nextSession;
    }

    state.sessions[index] = nextSession;
    return state.sessions[index];
}

function applyConversationStatePayload(payload) {
    const sessions = Array.isArray(payload?.sessions)
        ? payload.sessions.map(normalizeServerSession)
        : [];

    const previousActiveSessionId = state.activeSessionId;
    state.sessions = sessions;
    const requestedActiveSessionId = typeof payload?.activeSessionId === "string"
        ? payload.activeSessionId
        : null;
    state.activeSessionId = sessions.some((session) => session.id === requestedActiveSessionId)
        ? requestedActiveSessionId
        : sessions[0]?.id ?? null;

    if (state.activeSessionId !== previousActiveSessionId) {
        setMessageScrollMode("latest");
    }

    renderSidebar();
    renderMessages();
    populateModelOptions();
}

function buildConversationScopePayload() {
    return {
        credentialEnvelope: state.copilot.envelope || null,
    };
}

async function loadConversationState({ silent = false } = {}) {
    try {
        const payload = await requestJson("/api/conversations/state", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(buildConversationScopePayload()),
        });
        applyConversationStatePayload(payload);
        return state.sessions;
    } catch (error) {
        if (!silent) {
            showToast(error.message || "대화 목록을 불러오지 못했습니다.", "error");
        }

        renderSidebar();
        renderMessages();
        populateModelOptions();
        return state.sessions;
    }
}

async function syncConversationStateSilently() {
    await loadConversationState({ silent: true });
}

function clearConversationState() {
    state.sessions = [];
    state.activeSessionId = null;
    setMessageScrollMode("latest");
    renderSidebar();
    renderMessages();
    populateModelOptions();
}

function summarizeMessageForSidebar(message) {
    const content = typeof message?.content === "string" ? message.content.trim() : "";
    if (content) {
        return truncateText(content.replace(/\s+/g, " "), 60);
    }

    const attachmentCount = Array.isArray(message?.attachments) ? message.attachments.length : 0;
    if (attachmentCount > 0) {
        return attachmentCount === 1 ? "이미지 1개" : `이미지 ${attachmentCount}개`;
    }

    return "대화를 시작해 보세요.";
}

async function persistSessionModel(sessionId, modelId, previousModel) {
    const session = state.sessions.find((item) => item.id === sessionId);
    if (!session) {
        return false;
    }

    try {
        const payload = await requestJson(`/api/conversations/${encodeURIComponent(sessionId)}/model`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                model: modelId,
                ...buildConversationScopePayload(),
            }),
        });
        replaceSession(payload?.session);
        renderSidebar();
        renderMessages();
        populateModelOptions();
        return true;
    } catch (error) {
        session.model = previousModel;
        syncModelSelect();
        showToast(error.message || "대화 모델을 저장하지 못했습니다.", "error");
        return false;
    }
}

async function ensureConversationSession() {
    const existingSession = getActiveSession();
    if (existingSession) {
        return existingSession;
    }

    return createSession({ focus: false, silent: true });
}

function getActiveSession() {
    return state.sessions.find((session) => session.id === state.activeSessionId) ?? null;
}

function getFallbackModel() {
    return state.models[0]?.id ?? DEFAULT_MODEL;
}

function resolveModelId(modelId) {
    const availableModelIds = state.models.map((model) => model.id);
    if (availableModelIds.length === 0) {
        return modelId || DEFAULT_MODEL;
    }

    if (modelId && availableModelIds.includes(modelId)) {
        return modelId;
    }

    if (availableModelIds.includes(DEFAULT_MODEL)) {
        return DEFAULT_MODEL;
    }

    return availableModelIds[0];
}

function syncModelSelect() {
    const session = getActiveSession();
    elements.modelSelect.value = session?.model || getFallbackModel();
}

function truncateText(text, limit = 42) {
    if (text.length <= limit) {
        return text;
    }

    return `${text.slice(0, limit).trim()}...`;
}

function updateSessionTitle(session) {
    const firstUserMessage = session.messages.find(
        (message) => message.role === "user" && (message.content.trim() || message.attachments?.length),
    );
    if (!firstUserMessage) {
        session.title = "새 대화";
        return;
    }

    if (firstUserMessage.content.trim()) {
        session.title = truncateText(firstUserMessage.content.replace(/\s+/g, " "));
        return;
    }

    const attachmentCount = Array.isArray(firstUserMessage.attachments) ? firstUserMessage.attachments.length : 0;
    session.title = attachmentCount > 0 ? `이미지 ${attachmentCount}개` : "새 대화";
}

function promoteSession(sessionId) {
    const index = state.sessions.findIndex((session) => session.id === sessionId);
    if (index <= 0) {
        return;
    }

    const [session] = state.sessions.splice(index, 1);
    state.sessions.unshift(session);
}

async function createSession({ focus = true, silent = false } = {}) {
    if (state.composerAttachments.length > 0) {
        if (!silent) {
            showToast("첨부한 이미지를 먼저 전송하거나 삭제하세요.", "info");
        }
        return getActiveSession();
    }

    const currentSession = getActiveSession();

    try {
        const payload = await requestJson("/api/conversations", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                model: resolveModelId(currentSession?.model || elements.modelSelect.value || DEFAULT_MODEL),
                ...buildConversationScopePayload(),
            }),
        });
        const session = replaceSession(payload?.session);
        state.activeSessionId = typeof payload?.activeSessionId === "string"
            ? payload.activeSessionId
            : session.id;
        setMessageScrollMode("latest");
        renderSidebar();
        renderMessages();
        populateModelOptions();
        if (focus) {
            focusInput();
        }
        return session;
    } catch (error) {
        if (!silent) {
            showToast(error.message || "새 대화를 만들지 못했습니다.", "error");
        }
        return null;
    }
}

async function selectSession(sessionId) {
    const session = state.sessions.find((item) => item.id === sessionId);
    if (!session) {
        return;
    }

    if (state.composerAttachments.length > 0 && state.activeSessionId !== sessionId) {
        showToast("첨부한 이미지를 먼저 전송하거나 삭제하세요.", "info");
        return;
    }

    try {
        await requestJson(`/api/conversations/${encodeURIComponent(sessionId)}/activate`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(buildConversationScopePayload()),
        });
    } catch (error) {
        showToast(error.message || "대화를 열지 못했습니다.", "error");
        return;
    }

    state.activeSessionId = sessionId;
    setMessageScrollMode("latest");
    renderSidebar();
    renderMessages();
    populateModelOptions();

    if (getViewportLayout().usesOverlaySidebar) {
        closeSidebar();
    }
}

function renderSidebar() {
    elements.sessionList.innerHTML = "";

    const fragment = document.createDocumentFragment();
    state.sessions.forEach((session) => {
        const wrapper = document.createElement("div");
        wrapper.className = "session-item-wrapper";

        const button = document.createElement("button");
        button.type = "button";
        button.className = `session-item${session.id === state.activeSessionId ? " active" : ""}`;
        button.setAttribute("role", "listitem");
        button.title = session.title;
        button.setAttribute("aria-label", session.title);

        const abbreviation = document.createElement("span");
        abbreviation.className = "session-abbr";
        abbreviation.textContent = (session.title.trim().charAt(0) || "새").toUpperCase();

        const title = document.createElement("span");
        title.className = "session-title";
        title.textContent = session.title;

        const preview = document.createElement("span");
        preview.className = "session-preview";
        const lastMessage = session.messages.at(-1);
        preview.textContent = lastMessage ? summarizeMessageForSidebar(lastMessage) : "대화를 시작해 보세요.";

        button.append(abbreviation, title, preview);
        button.addEventListener("click", () => {
            void selectSession(session.id);
        });

        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.className = "session-edit-button";
        editButton.setAttribute("aria-label", "대화 제목 수정");
        editButton.title = "제목 수정";
        editButton.innerHTML =
            '<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M12 20h9"></path>' +
            '<path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>' +
            '</svg>';
        editButton.addEventListener("click", (event) => {
            event.stopPropagation();
            event.preventDefault();
            beginSessionTitleEdit(wrapper, session);
        });

        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "session-delete-button";
        deleteButton.setAttribute("aria-label", "대화 삭제");
        deleteButton.title = "대화 삭제";
        deleteButton.innerHTML =
            '<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<polyline points="3 6 5 6 21 6"></polyline>' +
            '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>' +
            '<path d="M10 11v6"></path>' +
            '<path d="M14 11v6"></path>' +
            '<path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"></path>' +
            '</svg>';
        deleteButton.addEventListener("click", (event) => {
            event.stopPropagation();
            event.preventDefault();
            void deleteSession(session.id, session.title);
        });

        wrapper.append(button, editButton, deleteButton);
        fragment.appendChild(wrapper);
    });

    elements.sessionList.appendChild(fragment);
}

function beginSessionTitleEdit(wrapper, session) {
    if (wrapper.classList.contains("editing")) {
        return;
    }
    wrapper.classList.add("editing");

    const titleEl = wrapper.querySelector(".session-title");
    if (!titleEl) {
        wrapper.classList.remove("editing");
        return;
    }

    const originalTitle = session.title;
    const input = document.createElement("input");
    input.type = "text";
    input.className = "session-title-input";
    input.maxLength = 80;
    input.value = originalTitle;
    input.setAttribute("aria-label", "대화 제목");

    titleEl.replaceWith(input);
    input.focus();
    input.select();

    let settled = false;

    const restoreTitleSpan = (titleText) => {
        const span = document.createElement("span");
        span.className = "session-title";
        span.textContent = titleText;
        if (input.parentNode) {
            input.replaceWith(span);
        }
        wrapper.classList.remove("editing");
    };

    const cancel = () => {
        if (settled) {
            return;
        }
        settled = true;
        restoreTitleSpan(originalTitle);
    };

    const commit = async () => {
        if (settled) {
            return;
        }
        const nextTitle = input.value.trim();
        if (!nextTitle) {
            cancel();
            return;
        }
        if (nextTitle === originalTitle) {
            cancel();
            return;
        }
        settled = true;
        input.disabled = true;
        const saved = await persistSessionTitle(session.id, nextTitle, originalTitle);
        if (saved) {
            // renderSidebar 가 다시 호출되며 DOM 이 재구성되므로 별도 처리 불필요.
            return;
        }
        // 실패 시 원복.
        restoreTitleSpan(originalTitle);
    };

    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            void commit();
        } else if (event.key === "Escape") {
            event.preventDefault();
            cancel();
        }
    });
    input.addEventListener("blur", () => {
        void commit();
    });
    input.addEventListener("click", (event) => {
        event.stopPropagation();
    });
}

async function persistSessionTitle(sessionId, nextTitle, previousTitle) {
    const session = state.sessions.find((item) => item.id === sessionId);
    if (!session) {
        return false;
    }

    try {
        const payload = await requestJson(`/api/conversations/${encodeURIComponent(sessionId)}/title`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                title: nextTitle,
                ...buildConversationScopePayload(),
            }),
        });
        replaceSession(payload?.session);
        renderSidebar();
        return true;
    } catch (error) {
        session.title = previousTitle;
        showToast(error.message || "대화 제목을 저장하지 못했습니다.", "error");
        return false;
    }
}

async function deleteSession(sessionId, sessionTitle) {
    const session = state.sessions.find((item) => item.id === sessionId);
    if (!session) {
        return;
    }

    const label = sessionTitle && sessionTitle.trim() ? sessionTitle.trim() : "이 대화";
    const confirmed = window.confirm(`"${label}" 대화를 삭제할까요? 이 작업은 되돌릴 수 없습니다.`);
    if (!confirmed) {
        return;
    }

    if (state.isStreaming && state.activeSessionId === sessionId) {
        showToast("응답을 생성 중인 대화는 중지한 뒤 삭제하세요.", "error");
        return;
    }

    try {
        const payload = await requestJson(`/api/conversations/${encodeURIComponent(sessionId)}/delete`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(buildConversationScopePayload()),
        });
        applyConversationStatePayload(payload);
        showToast("대화를 삭제했습니다.", "info");
    } catch (error) {
        showToast(error.message || "대화를 삭제하지 못했습니다.", "error");
    }
}

function renderMessages() {
    const preserveScroll = shouldPreserveMessageScroll();
    const previousScrollTop = preserveScroll ? elements.messages.scrollTop : 0;
    elements.messages.innerHTML = "";
    const session = getActiveSession();

    if (!session || session.messages.length === 0) {
        syncMessageHistoryJumpButton();
        return;
    }

    const stack = document.createElement("div");
    stack.className = "message-stack";

    session.messages.forEach((message) => {
        stack.appendChild(createMessageElement(message));
    });

    elements.messages.appendChild(stack);

    if (preserveScroll) {
        const maxScrollTop = Math.max(elements.messages.scrollHeight - elements.messages.clientHeight, 0);
        elements.messages.scrollTop = Math.min(previousScrollTop, maxScrollTop);
    } else {
        scrollMessagesToBottom();
    }

    handleMessagesScroll();
}

function renderMessageAttachments(container, attachments) {
    if (!Array.isArray(attachments) || attachments.length === 0) {
        return;
    }

    const grid = document.createElement("div");
    grid.className = "message-attachments";

    attachments.forEach((attachment) => {
        if (!attachment?.contentUrl) {
            return;
        }

        const tile = document.createElement("div");
        tile.className = "message-attachment-tile";

        const image = document.createElement("img");
        image.className = "message-attachment-image";
        image.src = attachment.contentUrl;
        image.alt = attachment.fileName || "첨부 이미지";
        image.loading = "lazy";
        image.addEventListener("error", () => {
            void syncConversationStateSilently();
        }, { once: true });

        const label = document.createElement("span");
        label.className = "message-attachment-label";
        label.textContent = attachment.fileName || "image.jpg";

        tile.append(image, label);
        grid.appendChild(tile);
    });

    if (grid.childElementCount > 0) {
        container.appendChild(grid);
    }
}

function renderMessageSources(container, message) {
    if (message?.role !== "assistant") {
        return;
    }

    const sources = normalizeMessageSources(message?.sources);
    if (sources.length === 0) {
        return;
    }

    const footer = document.createElement("div");
    footer.className = "message-sources";

    const label = document.createElement("span");
    label.className = "message-sources-label";
    label.textContent = "Sources";

    const list = document.createElement("div");
    list.className = "message-sources-list";

    sources.forEach((source) => {
        const link = document.createElement("a");
        link.className = "message-source-link";
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noreferrer noopener";
        link.textContent = source.title;
        list.appendChild(link);
    });

    footer.append(label, list);
    container.appendChild(footer);
}

function shouldRenderStreamFocusAnchor(message) {
    return state.isStreaming && message?.role === "assistant" && message.status === "streaming";
}

function renderMessageStreamFocusAnchor(container, message) {
    if (!shouldRenderStreamFocusAnchor(message)) {
        return;
    }

    const anchor = document.createElement("div");
    anchor.className = "message-stream-anchor";
    anchor.dataset.streamFocusAnchor = "true";
    anchor.setAttribute("aria-hidden", "true");
    container.appendChild(anchor);
}

function renderMessageBody(container, message) {
    container.innerHTML = "";
    renderMessageAttachments(container, message.attachments);

    if (message.content) {
        const content = document.createElement("div");
        content.className = "message-content";
        renderMarkdownInto(content, message.content);
        container.appendChild(content);
    } else if (!(Array.isArray(message.attachments) && message.attachments.length > 0)) {
        const pending = document.createElement("span");
        pending.className = "pending-text";
        pending.textContent = state.isStreaming && message.role === "assistant" ? "생성 중" : "";
        container.appendChild(pending);
    }

    renderMessageStreamFocusAnchor(container, message);
    renderMessageSources(container, message);
}

function createMessageElement(message) {
    const article = document.createElement("article");
    article.className = `message message-${message.role}`;
    article.dataset.messageId = message.id;

    const body = document.createElement("div");
    body.className = "message-body";

    renderMessageBody(body, message);

    article.appendChild(body);
    return article;
}

function updateMessageContent(message, options = {}) {
    if (state.activeSessionId !== getSessionIdByMessage(message.id)) {
        return;
    }

    const { useStreamFocus = false } = options;

    const body = elements.messages.querySelector(`[data-message-id="${message.id}"] .message-body`);
    if (!body) {
        renderMessages();
        if (useStreamFocus) {
            scrollStreamingMessageIntoView(message.id);
        }
        return;
    }

    renderMessageBody(body, message);
    if (useStreamFocus) {
        scrollStreamingMessageIntoView(message.id);
        handleMessagesScroll();
        return;
    }

    if (shouldAutoScrollMessages()) {
        scrollMessagesToBottom();
    }

    handleMessagesScroll();
}

function applyAssistantFailureMessage(session, assistantMessage, message) {
    assistantMessage.content = message || "응답을 가져오지 못했습니다.";
    assistantMessage.status = "error";
    session.updatedAt = Date.now();
    updateMessageContent(assistantMessage);
}

function getSessionIdByMessage(messageId) {
    const session = state.sessions.find((item) => item.messages.some((message) => message.id === messageId));
    return session?.id ?? null;
}

function escapeHtml(value) {
    return value
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function renderInlineHtml(text) {
    const inlineCodeTokens = [];
    const tokenized = text.replace(/`([^`]+)`/g, (_, code) => {
        const token = `@@INLINE_CODE_${inlineCodeTokens.length}@@`;
        inlineCodeTokens.push(code);
        return token;
    });

    let html = escapeHtml(tokenized);
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    html = html.replace(/\n/g, "<br>");
    html = html.replace(/@@INLINE_CODE_(\d+)@@/g, (_, index) => {
        return `<code>${escapeHtml(inlineCodeTokens[Number(index)] ?? "")}</code>`;
    });

    return html;
}

function appendTextBlocks(fragment, text) {
    if (!text) {
        return;
    }

    const paragraphs = text.split(/\n{2,}/);
    paragraphs.forEach((paragraph) => {
        if (!paragraph.trim()) {
            return;
        }

        const element = document.createElement("p");
        element.innerHTML = renderInlineHtml(paragraph);
        fragment.appendChild(element);
    });
}

function createCodeBlockElement(code, language) {
    const wrapper = document.createElement("div");
    wrapper.className = "code-block";

    const header = document.createElement("div");
    header.className = "code-block-header";

    const label = document.createElement("span");
    label.textContent = language || "code";

    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = "copy-button";
    copyButton.textContent = "Copy";
    copyButton.addEventListener("click", async () => {
        try {
            await navigator.clipboard.writeText(code);
            const originalText = copyButton.textContent;
            copyButton.textContent = "Copied";
            window.setTimeout(() => {
                copyButton.textContent = originalText;
            }, 1500);
        } catch {
            showToast("클립보드에 복사하지 못했습니다.", "error");
        }
    });

    header.append(label, copyButton);

    const pre = document.createElement("pre");
    const codeElement = document.createElement("code");
    codeElement.textContent = code.replace(/\n$/, "");
    pre.appendChild(codeElement);

    wrapper.append(header, pre);
    return wrapper;
}

function renderMarkdownInto(container, content) {
    container.innerHTML = "";

    const fragment = document.createDocumentFragment();
    const codeBlockPattern = /```([^\n`]*)\n?([\s\S]*?)```/g;
    let cursor = 0;
    let match;

    while ((match = codeBlockPattern.exec(content)) !== null) {
        appendTextBlocks(fragment, content.slice(cursor, match.index));
        fragment.appendChild(createCodeBlockElement(match[2], match[1].trim()));
        cursor = match.index + match[0].length;
    }

    appendTextBlocks(fragment, content.slice(cursor));
    container.appendChild(fragment);
}

function formatByteSize(byteSize) {
    if (!Number.isFinite(byteSize) || byteSize <= 0) {
        return "0B";
    }
    if (byteSize < 1024) {
        return `${byteSize}B`;
    }
    if (byteSize < 1024 * 1024) {
        return `${(byteSize / 1024).toFixed(1)}KB`;
    }
    return `${(byteSize / (1024 * 1024)).toFixed(1)}MB`;
}

function replaceFileExtensionWithJpg(fileName) {
    if (typeof fileName !== "string" || !fileName.trim()) {
        return "image.jpg";
    }
    return fileName.replace(/\.[^.]+$/, "") + ".jpg";
}

function getComposerAttachmentByLocalId(localId) {
    return state.composerAttachments.find((attachment) => attachment.localId === localId) ?? null;
}

function buildUploadedAttachmentPayload() {
    return state.composerAttachments
        .filter((attachment) => attachment.status === "uploaded" && attachment.attachmentId)
        .map((attachment) => ({ id: attachment.attachmentId }));
}

function buildOptimisticAttachmentPayload() {
    return state.composerAttachments
        .filter((attachment) => attachment.status === "uploaded" && attachment.attachmentId)
        .map((attachment) => ({
            id: attachment.attachmentId,
            fileName: attachment.fileName,
            mimeType: attachment.mimeType || "image/jpeg",
            byteSize: attachment.compressedByteSize || 0,
            width: attachment.width,
            height: attachment.height,
            contentUrl: attachment.contentUrl || attachment.previewObjectUrl || "",
        }));
}

function cleanupComposerAttachmentObjectUrls(attachments = state.composerAttachments) {
    attachments.forEach((attachment) => {
        if (attachment?.previewObjectUrl) {
            URL.revokeObjectURL(attachment.previewObjectUrl);
            attachment.previewObjectUrl = null;
        }
    });
}

function clearComposerAttachments() {
    cleanupComposerAttachmentObjectUrls();
    state.composerAttachments = [];
    if (elements.attachmentInput) {
        elements.attachmentInput.value = "";
    }
    renderComposerAttachments();
    updateComposerControls();
}

function validateSelectedImageFiles(files) {
    if (!Array.isArray(files) || files.length === 0) {
        return "업로드할 이미지가 없습니다.";
    }
    if (state.composerAttachments.length + files.length > MAX_ATTACHMENTS_PER_MESSAGE) {
        return "이미지는 한 번에 최대 5개까지 첨부할 수 있습니다.";
    }
    for (const file of files) {
        if (!(file instanceof File) || !file.type.startsWith("image/")) {
            return "이미지 파일만 첨부할 수 있습니다.";
        }
        if (file.size > MAX_ORIGINAL_IMAGE_BYTES) {
            return "이미지 1개당 15MB 이하만 첨부할 수 있습니다.";
        }
    }
    return null;
}

async function ensureSessionBeforeAttachmentUpload() {
    const hasCredentials = await ensureCopilotCredentials({ interactive: true });
    if (!hasCredentials) {
        focusInput();
        return null;
    }
    const session = await ensureConversationSession();
    if (!session) {
        focusInput();
        return null;
    }
    return session;
}

function openAttachmentPicker() {
    if (state.isStreaming) {
        return;
    }
    elements.attachmentInput?.click();
}

async function handleAttachmentInputChange(event) {
    const fileList = Array.from(event.target?.files || []);
    event.target.value = "";
    await queueSelectedFiles(fileList);
}

function hasImageFilesInDataTransfer(dataTransfer) {
    if (!dataTransfer) {
        return false;
    }

    if (dataTransfer.items && dataTransfer.items.length > 0) {
        return Array.from(dataTransfer.items).some((item) => item.kind === "file" && item.type.startsWith("image/"));
    }

    if (dataTransfer.files && dataTransfer.files.length > 0) {
        return Array.from(dataTransfer.files).some((file) => file.type.startsWith("image/"));
    }

    return false;
}

function handleComposerDragOver(event) {
    event.preventDefault();
    if (hasImageFilesInDataTransfer(event.dataTransfer)) {
        elements.composerDropzone?.classList.add("dragover");
    } else {
        elements.composerDropzone?.classList.remove("dragover");
    }
}

function handleComposerDragEnter(event) {
    event.preventDefault();
    if (hasImageFilesInDataTransfer(event.dataTransfer)) {
        elements.composerDropzone?.classList.add("dragover");
    }
}

function handleComposerDragLeave(event) {
    if (event.currentTarget === event.target) {
        elements.composerDropzone?.classList.remove("dragover");
    }
}

async function handleComposerDrop(event) {
    event.preventDefault();
    elements.composerDropzone?.classList.remove("dragover");
    await queueSelectedFiles(event.dataTransfer?.files);
}

function createComposerAttachmentItem(file) {
    return {
        localId: createId("attachment"),
        fileName: file.name,
        originalFile: file,
        compressedFile: null,
        previewObjectUrl: null,
        status: "queued",
        errorMessage: null,
        originalByteSize: file.size,
        compressedByteSize: null,
        mimeType: null,
        width: null,
        height: null,
        attachmentId: null,
        contentUrl: null,
    };
}

function describeComposerAttachmentStatus(attachment) {
    const sizeLabel = attachment.compressedByteSize ? ` · ${formatByteSize(attachment.compressedByteSize)}` : "";
    switch (attachment.status) {
        case "queued":
            return "대기 중";
        case "compressing":
            return "압축 중";
        case "ready_to_upload":
            return `업로드 준비${sizeLabel}`;
        case "uploading":
            return "업로드 중";
        case "deleting":
            return "삭제 중";
        case "uploaded":
            return `업로드 완료${sizeLabel}`;
        case "failed":
            return attachment.errorMessage || "업로드 실패";
        default:
            return "대기 중";
    }
}

function renderComposerAttachments() {
    if (!elements.composerAttachmentList) {
        return;
    }
    elements.composerAttachmentList.innerHTML = "";

    const fragment = document.createDocumentFragment();
    state.composerAttachments.forEach((attachment) => {
        const isActionDisabled = COMPOSER_ATTACHMENT_BUSY_STATES.has(attachment.status);
        const item = document.createElement("div");
        item.className = "composer-attachment-item";

        let thumb;
        if (attachment.previewObjectUrl) {
            thumb = document.createElement("img");
            thumb.className = "composer-attachment-thumb";
            thumb.src = attachment.previewObjectUrl;
            thumb.alt = attachment.fileName;
        } else {
            thumb = document.createElement("div");
            thumb.className = "composer-attachment-thumb";
        }

        const meta = document.createElement("div");
        meta.className = "composer-attachment-meta";

        const name = document.createElement("span");
        name.className = "composer-attachment-name";
        name.textContent = attachment.fileName;

        const status = document.createElement("span");
        status.className = `composer-attachment-status${attachment.status === "failed" ? " error" : ""}`;
        status.textContent = describeComposerAttachmentStatus(attachment);

        meta.append(name, status);

        const actions = document.createElement("div");
        actions.className = "composer-attachment-actions";

        if (attachment.status === "failed") {
            const retryButton = document.createElement("button");
            retryButton.type = "button";
            retryButton.className = "composer-attachment-retry";
            retryButton.textContent = "재시도";
            retryButton.disabled = isActionDisabled;
            retryButton.addEventListener("click", () => {
                void retryComposerAttachment(attachment.localId);
            });
            actions.appendChild(retryButton);
        }

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "composer-attachment-remove";
        removeButton.textContent = "삭제";
        removeButton.disabled = isActionDisabled;
        removeButton.addEventListener("click", () => {
            void removeComposerAttachment(attachment.localId);
        });
        actions.appendChild(removeButton);

        item.append(thumb, meta, actions);
        fragment.appendChild(item);
    });

    elements.composerAttachmentList.appendChild(fragment);
}

async function queueSelectedFiles(fileList) {
    const files = Array.from(fileList || []);
    const validationMessage = validateSelectedImageFiles(files);
    if (validationMessage) {
        showToast(validationMessage, "error");
        return;
    }

    const session = await ensureSessionBeforeAttachmentUpload();
    if (!session) {
        return;
    }

    const newAttachments = files.map(createComposerAttachmentItem);
    state.composerAttachments.push(...newAttachments);
    renderComposerAttachments();
    updateComposerControls();

    const localIds = newAttachments.map((attachment) => attachment.localId);
    await compressQueuedAttachments(localIds);
    await uploadReadyAttachments(session.id, localIds);
    updateComposerControls();
}

async function compressQueuedAttachments(localIds) {
    for (const localId of localIds) {
        const attachment = getComposerAttachmentByLocalId(localId);
        if (!attachment || attachment.status !== "queued") {
            continue;
        }

        attachment.status = "compressing";
        attachment.errorMessage = null;
        renderComposerAttachments();
        updateComposerControls();

        try {
            const compressed = await compressImageFile(attachment.originalFile);
            if (attachment.previewObjectUrl) {
                URL.revokeObjectURL(attachment.previewObjectUrl);
            }
            attachment.compressedFile = compressed.file;
            attachment.previewObjectUrl = URL.createObjectURL(compressed.file);
            attachment.compressedByteSize = compressed.file.size;
            attachment.mimeType = compressed.file.type;
            attachment.width = compressed.width;
            attachment.height = compressed.height;
            attachment.status = "ready_to_upload";
        } catch (error) {
            attachment.status = "failed";
            attachment.errorMessage = error.message || "이미지를 5MB 이하로 압축하지 못했습니다.";
        }

        renderComposerAttachments();
        updateComposerControls();
    }
}

async function uploadReadyAttachments(sessionId, localIds) {
    for (const localId of localIds) {
        const attachment = getComposerAttachmentByLocalId(localId);
        if (!attachment || attachment.status !== "ready_to_upload" || !attachment.compressedFile) {
            continue;
        }

        attachment.status = "uploading";
        attachment.errorMessage = null;
        renderComposerAttachments();
        updateComposerControls();

        try {
            const payload = await uploadAttachmentItem(sessionId, attachment);
            const uploaded = payload?.attachment ?? payload;
            attachment.status = "uploaded";
            attachment.attachmentId = uploaded?.id ?? null;
            attachment.contentUrl = uploaded?.contentUrl ?? attachment.previewObjectUrl;
            attachment.fileName = uploaded?.fileName ?? attachment.fileName;
            attachment.mimeType = uploaded?.mimeType ?? attachment.mimeType;
            attachment.compressedByteSize = uploaded?.byteSize ?? attachment.compressedByteSize;
            attachment.width = uploaded?.width ?? attachment.width;
            attachment.height = uploaded?.height ?? attachment.height;
        } catch (error) {
            attachment.status = "failed";
            attachment.errorMessage = error.message || "이미지를 업로드하지 못했습니다.";
        }

        renderComposerAttachments();
        updateComposerControls();
    }
}

async function uploadAttachmentItem(sessionId, attachment) {
    const formData = new FormData();
    formData.append("file", attachment.compressedFile, attachment.compressedFile.name);
    formData.append("conversationId", sessionId);
    formData.append("credentialEnvelope", state.copilot.envelope || "");

    const response = await fetch("/api/uploads/images", {
        method: "POST",
        body: formData,
    });
    updateCredentialEnvelopeFromResponse(response);

    if (!response.ok) {
        const error = await extractErrorResponse(response);
        if (response.status === 401) {
            handleUnauthorizedCopilot(error.message);
        }
        throw new Error(error.message);
    }

    return response.json();
}

async function removeComposerAttachment(localId) {
    const attachment = getComposerAttachmentByLocalId(localId);
    if (!attachment) {
        return;
    }

    if (COMPOSER_ATTACHMENT_BUSY_STATES.has(attachment.status)) {
        showToast("이미지 처리가 끝난 뒤 삭제하세요.", "info");
        return;
    }

    const previousStatus = attachment.status;
    const shouldDeleteUploadedAttachment = previousStatus === "uploaded" && Boolean(attachment.attachmentId);
    attachment.status = "deleting";
    renderComposerAttachments();
    updateComposerControls();

    if (shouldDeleteUploadedAttachment) {
        try {
            await requestJson(`/api/uploads/images/${encodeURIComponent(attachment.attachmentId)}`, {
                method: "DELETE",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    conversationId: getActiveSession()?.id,
                    credentialEnvelope: state.copilot.envelope,
                }),
            });
        } catch (error) {
            attachment.status = previousStatus;
            renderComposerAttachments();
            updateComposerControls();
            if (error.code === "copilot_login_required") {
                handleUnauthorizedCopilot(error.message);
            }
            showToast(error.message || "첨부 이미지를 삭제하지 못했습니다.", "error");
            return;
        }
    }

    if (attachment.previewObjectUrl) {
        URL.revokeObjectURL(attachment.previewObjectUrl);
    }
    state.composerAttachments = state.composerAttachments.filter((item) => item.localId !== localId);
    renderComposerAttachments();
    updateComposerControls();
}

async function retryComposerAttachment(localId) {
    const attachment = getComposerAttachmentByLocalId(localId);
    if (!attachment) {
        return;
    }

    const session = await ensureSessionBeforeAttachmentUpload();
    if (!session) {
        return;
    }

    attachment.status = "queued";
    attachment.errorMessage = null;
    attachment.compressedFile = null;
    attachment.attachmentId = null;
    attachment.contentUrl = null;
    attachment.compressedByteSize = null;
    renderComposerAttachments();
    await compressQueuedAttachments([localId]);
    await uploadReadyAttachments(session.id, [localId]);
}

async function readExifOrientation(file) {
    if (!(file instanceof File) || !/jpe?g$/i.test(file.type)) {
        return 1;
    }

    const view = new DataView(await file.arrayBuffer());
    if (view.byteLength < 4 || view.getUint16(0, false) !== 0xFFD8) {
        return 1;
    }

    let offset = 2;
    while (offset < view.byteLength) {
        if (view.getUint8(offset) !== 0xFF) {
            break;
        }

        const marker = view.getUint8(offset + 1);
        offset += 2;
        if (marker === 0xD9 || marker === 0xDA) {
            break;
        }

        const segmentLength = view.getUint16(offset, false);
        if (segmentLength < 2) {
            break;
        }

        if (marker === 0xE1 && offset + segmentLength <= view.byteLength) {
            const exifOffset = offset + 2;
            if (view.getUint32(exifOffset, false) !== 0x45786966) {
                return 1;
            }
            const tiffOffset = exifOffset + 6;
            const littleEndian = view.getUint16(tiffOffset, false) === 0x4949;
            const firstIfdOffset = view.getUint32(tiffOffset + 4, littleEndian);
            const ifdOffset = tiffOffset + firstIfdOffset;
            const entryCount = view.getUint16(ifdOffset, littleEndian);
            for (let index = 0; index < entryCount; index += 1) {
                const entryOffset = ifdOffset + 2 + (index * 12);
                if (view.getUint16(entryOffset, littleEndian) === 0x0112) {
                    return view.getUint16(entryOffset + 8, littleEndian);
                }
            }
            return 1;
        }

        offset += segmentLength;
    }

    return 1;
}

async function loadImageBitmapFromFile(file) {
    if (window.createImageBitmap) {
        try {
            return await window.createImageBitmap(file, { imageOrientation: "none" });
        } catch {
            return window.createImageBitmap(file);
        }
    }

    return new Promise((resolve, reject) => {
        const objectUrl = URL.createObjectURL(file);
        const image = new Image();
        image.onload = () => {
            URL.revokeObjectURL(objectUrl);
            resolve(image);
        };
        image.onerror = () => {
            URL.revokeObjectURL(objectUrl);
            reject(new Error("이미지 파일을 처리할 수 없습니다. 다른 파일을 선택하세요."));
        };
        image.src = objectUrl;
    });
}

function applyOrientationTransform(context, orientation, width, height) {
    switch (orientation) {
        case 2:
            context.translate(width, 0);
            context.scale(-1, 1);
            break;
        case 3:
            context.translate(width, height);
            context.rotate(Math.PI);
            break;
        case 4:
            context.translate(0, height);
            context.scale(1, -1);
            break;
        case 5:
            context.rotate(0.5 * Math.PI);
            context.scale(1, -1);
            break;
        case 6:
            context.rotate(0.5 * Math.PI);
            context.translate(0, -height);
            break;
        case 7:
            context.rotate(0.5 * Math.PI);
            context.translate(width, -height);
            context.scale(-1, 1);
            break;
        case 8:
            context.rotate(-0.5 * Math.PI);
            context.translate(-width, 0);
            break;
        default:
            break;
    }
}

function drawImageToCanvas(bitmap, dimensionLimit, orientation = 1) {
    const sourceWidth = Number(bitmap.width || bitmap.naturalWidth || 0);
    const sourceHeight = Number(bitmap.height || bitmap.naturalHeight || 0);
    if (!sourceWidth || !sourceHeight) {
        throw new Error("이미지 파일을 처리할 수 없습니다. 다른 파일을 선택하세요.");
    }

    const scale = Math.min(1, dimensionLimit / Math.max(sourceWidth, sourceHeight));
    const drawWidth = Math.max(1, Math.round(sourceWidth * scale));
    const drawHeight = Math.max(1, Math.round(sourceHeight * scale));
    const rotate90 = [5, 6, 7, 8].includes(orientation);

    const canvas = document.createElement("canvas");
    canvas.width = rotate90 ? drawHeight : drawWidth;
    canvas.height = rotate90 ? drawWidth : drawHeight;

    const context = canvas.getContext("2d", { alpha: false });
    if (!context) {
        throw new Error("이미지 파일을 처리할 수 없습니다. 다른 파일을 선택하세요.");
    }

    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    applyOrientationTransform(context, orientation, drawWidth, drawHeight);
    context.drawImage(bitmap, 0, 0, sourceWidth, sourceHeight, 0, 0, drawWidth, drawHeight);

    return {
        canvas,
        width: canvas.width,
        height: canvas.height,
    };
}

function encodeCanvasToJpeg(canvas, quality) {
    return new Promise((resolve) => {
        canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality);
    });
}

async function compressImageFile(file) {
    const orientation = await readExifOrientation(file);
    const bitmap = await loadImageBitmapFromFile(file);

    try {
        for (const dimensionLimit of [INITIAL_IMAGE_MAX_DIMENSION, SECONDARY_IMAGE_MAX_DIMENSION]) {
            const { canvas, width, height } = drawImageToCanvas(bitmap, dimensionLimit, orientation);
            for (let quality = INITIAL_JPEG_QUALITY; quality >= MIN_JPEG_QUALITY; quality -= JPEG_QUALITY_STEP) {
                const blob = await encodeCanvasToJpeg(canvas, quality);
                if (!blob) {
                    continue;
                }
                if (blob.size <= TARGET_COMPRESSED_IMAGE_BYTES) {
                    return {
                        file: new File([blob], replaceFileExtensionWithJpg(file.name), {
                            type: "image/jpeg",
                            lastModified: Date.now(),
                        }),
                        width,
                        height,
                    };
                }
            }
        }
    } finally {
        bitmap.close?.();
    }

    throw new Error("이미지를 5MB 이하로 압축하지 못했습니다.");
}

function updateComposerControls() {
    const label = state.isStreaming ? "중지" : "보내기";
    const hasText = Boolean(elements.promptInput.value.trim());
    const hasUploadedAttachments = buildUploadedAttachmentPayload().length > 0;
    const hasBusyAttachments = state.composerAttachments.some((attachment) => COMPOSER_ATTACHMENT_BUSY_STATES.has(attachment.status));
    elements.sendButtonText.textContent = label;
    elements.sendButton.setAttribute("aria-label", label);
    elements.sendButton.classList.toggle("danger", state.isStreaming);
    elements.sendButton.disabled = !state.isStreaming && ((!hasText && !hasUploadedAttachments) || hasBusyAttachments);
    if (elements.attachmentButton) {
        elements.attachmentButton.disabled = state.isStreaming;
    }
    syncMessageHistoryJumpButton();
}

function setStreamingState(isStreaming) {
    state.isStreaming = Boolean(isStreaming);
    updateComposerControls();
    syncComposerStatus();
}

function clearComposerStatusTimer() {
    if (state.composerStatus.resetTimerId === null) {
        return;
    }

    window.clearTimeout(state.composerStatus.resetTimerId);
    state.composerStatus.resetTimerId = null;
}

function renderComposerStatus() {
    elements.streamStatus.textContent = state.composerStatus.message;
    elements.streamStatus.dataset.tone = state.composerStatus.tone;
}

function setComposerStatus(message, tone = "ready", options = {}) {
    clearComposerStatusTimer();

    state.composerStatus.message = message;
    state.composerStatus.tone = tone;
    renderComposerStatus();

    if (!Number.isFinite(options.persistMs) || options.persistMs <= 0) {
        return;
    }

    state.composerStatus.resetTimerId = window.setTimeout(() => {
        state.composerStatus.resetTimerId = null;
        if (state.isStreaming) {
            return;
        }

        state.composerStatus.message = COMPOSER_READY_MESSAGE;
        state.composerStatus.tone = "ready";
        renderComposerStatus();
    }, options.persistMs);
}

function syncComposerStatus() {
    if (state.isStreaming) {
        setComposerStatus(COMPOSER_STREAMING_MESSAGE, "pending");
        return;
    }

    if (state.composerStatus.resetTimerId !== null) {
        return;
    }

    setComposerStatus(COMPOSER_READY_MESSAGE, "ready");
}

function syncViewportHeightVar() {
    const viewportHeight = Math.max(
        Math.round(window.visualViewport?.height ?? window.innerHeight),
        1,
    );
    document.documentElement.style.setProperty("--app-height", `${viewportHeight}px`);

    if (document.activeElement === elements.promptInput) {
        scheduleScrollMessagesToBottom(3);
    }

    handleMessagesScroll();
}

function getViewportLayout() {
    const width = Math.round(window.visualViewport?.width ?? window.innerWidth);
    const height = Math.max(
        Math.round(window.visualViewport?.height ?? window.innerHeight),
        1,
    );
    const usesOverlaySidebar = width <= MOBILE_VIEWPORT_MAX_WIDTH;

    return {
        width,
        height,
        usesOverlaySidebar,
        isCompactUsage: usesOverlaySidebar || height <= COMPACT_VIEWPORT_MAX_HEIGHT,
    };
}

function syncUsageSummaryLayout(viewport = getViewportLayout()) {
    const isCompactUsage = viewport.isCompactUsage;

    if (state.ui.isCompactUsage !== isCompactUsage) {
        state.ui.isCompactUsage = isCompactUsage;
        state.ui.isUsageSummaryCollapsed = isCompactUsage;
    }

    if (!isCompactUsage) {
        state.ui.isUsageSummaryCollapsed = false;
    }

    elements.usageSummary.dataset.compact = isCompactUsage ? "true" : "false";
    elements.usageSummary.dataset.collapsed = state.ui.isUsageSummaryCollapsed ? "true" : "false";
    elements.usageSummaryToggle.hidden = !isCompactUsage;
    elements.usageSummaryPanel.hidden = false;
    elements.usageSummaryPanel.setAttribute("aria-hidden", String(isCompactUsage && state.ui.isUsageSummaryCollapsed));
    elements.usageSummaryToggle.setAttribute("aria-expanded", String(!state.ui.isUsageSummaryCollapsed));
    const toggleLabel = state.ui.isUsageSummaryCollapsed ? "사용량 요약 펼치기" : "사용량 요약 접기";
    elements.usageSummaryToggle.setAttribute("aria-label", toggleLabel);
    elements.usageSummaryToggle.title = toggleLabel;
}

function syncSidebarLayout(viewport = getViewportLayout()) {
    const isDesktopCollapsed = !viewport.usesOverlaySidebar && state.ui.isSidebarCollapsed;
    elements.appShell?.classList.toggle("sidebar-collapsed", isDesktopCollapsed);

    if (elements.toggleSidebarButton) {
        const label = isDesktopCollapsed ? "사이드바 펼치기" : "사이드바 접기";
        elements.toggleSidebarButton.setAttribute("aria-label", label);
        elements.toggleSidebarButton.title = label;
        elements.toggleSidebarButton.setAttribute("aria-pressed", String(isDesktopCollapsed));
    }
}

function setDesktopSidebarCollapsed(nextCollapsed) {
    state.ui.isSidebarCollapsed = Boolean(nextCollapsed);
    window.localStorage.setItem(SIDEBAR_COLLAPSE_STORAGE_KEY, String(state.ui.isSidebarCollapsed));
    syncSidebarLayout();
}

function toggleSidebar() {
    const viewport = getViewportLayout();
    if (viewport.usesOverlaySidebar) {
        if (elements.sidebar?.classList.contains("open")) {
            closeSidebar();
            return;
        }

        openSidebar();
        return;
    }

    setDesktopSidebarCollapsed(!state.ui.isSidebarCollapsed);
}

function clearSidebarOverlayTimer() {
    if (state.ui.sidebarOverlayTimerId === null) {
        return;
    }

    window.clearTimeout(state.ui.sidebarOverlayTimerId);
    state.ui.sidebarOverlayTimerId = null;
}

function syncResponsiveLayout() {
    const viewport = getViewportLayout();
    syncUsageSummaryLayout(viewport);
    syncSidebarLayout(viewport);

    if (!viewport.usesOverlaySidebar) {
        closeSidebar({ immediate: true });
    }

    handleMessagesScroll();
}

function adjustTextareaHeight() {
    elements.promptInput.style.height = "auto";
    elements.promptInput.style.height = `${Math.min(elements.promptInput.scrollHeight, 220)}px`;
}

function setMessageScrollMode(mode) {
    state.scroll.mode = mode === "history" ? "history" : "latest";
}

function shouldPreserveMessageScroll() {
    return state.scroll.mode === "history" && !state.isStreaming;
}

function shouldAutoScrollMessages() {
    return state.scroll.mode === "latest";
}

function isMessagesNearBottom() {
    const remaining = elements.messages.scrollHeight - elements.messages.clientHeight - elements.messages.scrollTop;
    return remaining <= MESSAGE_SCROLL_BOTTOM_THRESHOLD;
}

function getPreviousUserMessageJumpTarget() {
    const userMessages = Array.from(elements.messages.querySelectorAll(".message-user[data-message-id]"));
    const containerRect = elements.messages.getBoundingClientRect();
    if (!containerRect.height) {
        return null;
    }

    const viewportTop = containerRect.top + MESSAGE_HISTORY_VIEWPORT_OFFSET;
    const firstVisibleIndex = userMessages.findIndex((messageElement) => {
        return messageElement.getBoundingClientRect().bottom > viewportTop;
    });

    if (firstVisibleIndex === -1) {
        return userMessages.at(-1) ?? null;
    }

    if (firstVisibleIndex === 0) {
        return null;
    }

    return userMessages[firstVisibleIndex - 1];
}

function syncMessageHistoryJumpButton() {
    if (!elements.messageHistoryJumpButton || !elements.messagePanelToolbar) {
        return;
    }

    const target = getPreviousUserMessageJumpTarget();
    const hasTarget = Boolean(target);

    elements.messagePanelToolbar.hidden = !hasTarget;
    if (!hasTarget) {
        elements.messageHistoryJumpButton.disabled = true;
        return;
    }

    elements.messageHistoryJumpButton.disabled = state.isStreaming;
    const buttonTitle = state.isStreaming
        ? "응답 생성 중에는 사용할 수 없습니다."
        : "이전 질문으로 이동";
    elements.messageHistoryJumpButton.title = buttonTitle;
    elements.messageHistoryJumpButton.setAttribute("aria-label", buttonTitle);
}

function jumpToPreviousUserMessage() {
    if (state.isStreaming) {
        return;
    }

    const target = getPreviousUserMessageJumpTarget();
    if (!target) {
        syncMessageHistoryJumpButton();
        return;
    }

    setMessageScrollMode("history");

    const containerRect = elements.messages.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    elements.messages.scrollTop = Math.max(
        0,
        elements.messages.scrollTop + (targetRect.top - containerRect.top) - MESSAGE_HISTORY_TOP_OFFSET,
    );

    window.requestAnimationFrame(handleMessagesScroll);
}

function handleMessagesScroll() {
    if (!elements.messages) {
        return;
    }

    if (state.isStreaming || isMessagesNearBottom()) {
        setMessageScrollMode("latest");
    } else {
        setMessageScrollMode("history");
    }

    syncMessageHistoryJumpButton();
}

function scrollMessagesToBottom() {
    elements.messages.scrollTop = elements.messages.scrollHeight;
}

function scrollStreamingMessageIntoView(messageId) {
    const anchor = elements.messages.querySelector(
        `[data-message-id="${messageId}"] [data-stream-focus-anchor="true"]`,
    );
    if (!anchor) {
        scrollMessagesToBottom();
        return;
    }

    const containerRect = elements.messages.getBoundingClientRect();
    if (!containerRect.height) {
        return;
    }

    const anchorRect = anchor.getBoundingClientRect();
    const bottomInset = Number.parseFloat(window.getComputedStyle(elements.messages).scrollPaddingBottom) || 0;
    const visibleBottom = containerRect.bottom - bottomInset;

    if (anchorRect.bottom > visibleBottom) {
        elements.messages.scrollTop = Math.min(
            elements.messages.scrollHeight,
            elements.messages.scrollTop + (anchorRect.bottom - visibleBottom),
        );
        return;
    }

    if (anchorRect.top < containerRect.top) {
        elements.messages.scrollTop = Math.max(
            0,
            elements.messages.scrollTop - (containerRect.top - anchorRect.top),
        );
    }
}

function scheduleScrollMessagesToBottom(frameCount = 2) {
    if (!shouldAutoScrollMessages()) {
        return;
    }

    let remainingFrames = Math.max(frameCount, 1);

    const scrollOnNextFrame = () => {
        scrollMessagesToBottom();
        remainingFrames -= 1;
        if (remainingFrames > 0) {
            window.requestAnimationFrame(scrollOnNextFrame);
        }
    };

    window.requestAnimationFrame(scrollOnNextFrame);
}

function focusInput() {
    elements.promptInput.focus({ preventScroll: true });
}

function setPromptValue(value) {
    elements.promptInput.value = value;
    adjustTextareaHeight();
    updateComposerControls();
    focusInput();
}

function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    elements.toastContainer.appendChild(toast);

    window.setTimeout(() => {
        toast.remove();
    }, 4200);
}

function describeTimestamp(timestamp) {
    if (!timestamp || Number.isNaN(Number(timestamp))) {
        return "-";
    }

    return new Date(Number(timestamp) * 1000).toLocaleString("ko-KR", {
        dateStyle: "medium",
        timeStyle: "short",
    });
}

function normalizeUsageQuantity(value) {
    const quantity = value === null || value === undefined ? NaN : Number(value);
    return Number.isFinite(quantity) ? quantity : null;
}

function normalizeUsagePlan(value) {
    if (typeof value === "string") {
        const normalized = value.trim();
        return normalized || null;
    }

    if (typeof value === "number" && Number.isFinite(value)) {
        return String(value);
    }

    return null;
}

function normalizeUsageMetric(metric) {
    const remaining = normalizeUsageQuantity(metric?.remaining);
    const used = normalizeUsageQuantity(metric?.used);
    const total = normalizeUsageQuantity(metric?.total);
    const unlimited = Boolean(metric?.unlimited);
    const hasQuotaBasis = remaining !== null || used !== null || total !== null || unlimited;
    return {
        remaining,
        used,
        total,
        plan: normalizeUsagePlan(metric?.plan),
        unlimited,
        status: hasQuotaBasis ? "available" : "missing",
    };
}

function normalizeUsageStatus(status) {
    return USAGE_STATUS_VALUES.includes(status) ? status : "unavailable";
}

function normalizeUsageReason(reason, status) {
    if (typeof reason === "string" && Object.prototype.hasOwnProperty.call(USAGE_SUMMARY_REASON_MESSAGES, reason)) {
        return reason;
    }

    if (status === "ok") {
        return "copilot_usage_ok";
    }

    if (status === "partial") {
        return "copilot_usage_partial";
    }

    return "copilot_usage_unavailable";
}

function normalizeUsageSnapshot(snapshot) {
    const status = normalizeUsageStatus(snapshot?.status);
    const reason = normalizeUsageReason(snapshot?.reason, status);

    return {
        ...createEmptyUsageSnapshot(reason),
        status,
        reason,
        detail: null,
        source: typeof snapshot?.source === "string" ? snapshot.source : null,
        accessTypeSku: typeof snapshot?.accessTypeSku === "string" ? snapshot.accessTypeSku : null,
        fetchedAt: Number.isFinite(Number(snapshot?.fetchedAt)) ? Number(snapshot.fetchedAt) : 0,
        chatMessages: normalizeUsageMetric(snapshot?.chatMessages),
        premiumRequests: normalizeUsageMetric(snapshot?.premiumRequests),
    };
}

function formatUsageCount(value, { minimumFractionDigits = 0, maximumFractionDigits = 2 } = {}) {
    if (!Number.isFinite(Number(value))) {
        return null;
    }

    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits,
        maximumFractionDigits,
    }).format(Number(value));
}

function formatUsagePercent(value) {
    if (!Number.isFinite(Number(value))) {
        return null;
    }

    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(Number(value));
}

function formatUsageRemaining(metric) {
    if (metric?.unlimited) {
        return "무제한";
    }
    return formatUsageCount(metric?.remaining) ?? "-";
}

function isUnlimitedChatMessages(snapshot, metric) {
    return Boolean(metric?.unlimited) || Boolean(
        snapshot?.source === "copilot_user_api"
        && metric?.status === "available"
        && Number(metric?.remaining) === 0
        && Number.isFinite(snapshot?.premiumRequests?.remaining),
    );
}

function shouldHidePremiumUsage(snapshot, metric) {
    return Boolean(
        snapshot?.accessTypeSku === "free_limited_copilot"
        && metric?.status !== "available",
    );
}

function resolvePremiumUsagePercent(metric) {
    const total = normalizeUsageQuantity(metric?.total);
    if (total === null || total <= 0) {
        return null;
    }

    const used = normalizeUsageQuantity(metric?.used);
    if (used !== null) {
        return Math.min(100, Math.max(0, (used / total) * 100));
    }

    const remaining = normalizeUsageQuantity(metric?.remaining);
    if (remaining === null) {
        return null;
    }

    return Math.min(100, Math.max(0, ((total - remaining) / total) * 100));
}

function getPremiumUsagePresentation(snapshot, metric) {
    const usedPercent = resolvePremiumUsagePercent(metric);
    if (usedPercent !== null) {
        const formattedPercent = formatUsagePercent(usedPercent) ?? "0.00";
        const usedText = formatUsageCount(metric?.used);
        const totalText = formatUsageCount(metric?.total);
        const usageCountsText = usedText && totalText ? ` (${usedText} / ${totalText})` : "";
        return {
            primaryValue: `${formattedPercent}% 사용`,
            title: `Premium requests ${formattedPercent}% 사용${usageCountsText}`,
            usedPercent,
        };
    }

    const remainingText = formatUsageCount(metric?.remaining);
    const totalText = formatUsageCount(metric?.total);
    const titleParts = [];

    if (Number.isFinite(metric?.remaining) && metric.remaining <= 0) {
        titleParts.push("소진");
    } else {
        titleParts.push(formatUsageRemaining(metric) === "-" ? "확인됨" : `${formatUsageRemaining(metric)} 남음`);
    }

    if (remainingText) {
        titleParts.push(`${remainingText}개 남음`);
    } else if (totalText) {
        titleParts.push(`총 ${totalText}`);
    }

    return {
        primaryValue: titleParts[0] ?? "확인됨",
        title: titleParts.length === 0 ? "Premium requests server snapshot" : `Premium requests ${titleParts.join(", ")}`,
        usedPercent,
    };
}

function formatPremiumUsagePrimaryValue(snapshot, metric) {
    return getPremiumUsagePresentation(snapshot, metric).primaryValue;
}

function describePremiumUsageTitle(snapshot, metric) {
    const presentation = getPremiumUsagePresentation(snapshot, metric);
    if (!presentation.title) {
        return "Premium requests server snapshot";
    }

    return presentation.title;
}

function getUsageVisual(metricKey, snapshot, metric) {
    const config = USAGE_VISUAL_CONFIG[metricKey];

    if (metric.status !== "available") {
        const isConnected = state.copilot.status === "connected";
        return {
            badge: isConnected ? "대기" : "로그인 필요",
            level: "missing",
            ratio: 0.12,
            title: isConnected ? "사용량 스냅샷 대기 중" : "GitHub Copilot 로그인 필요",
        };
    }

    if (metricKey === "chatMessages" && isUnlimitedChatMessages(snapshot, metric)) {
        return {
            badge: "무제한",
            level: "unlimited",
            ratio: 1,
            title: "Included chat messages",
        };
    }

    if (metricKey === "premiumRequests") {
        const premiumPresentation = getPremiumUsagePresentation(snapshot, metric);
        if (premiumPresentation.usedPercent !== null) {
            let level = "high";
            if (premiumPresentation.usedPercent >= 85) {
                level = "low";
            } else if (premiumPresentation.usedPercent >= 50) {
                level = "medium";
            }

            return {
                badge: formatPremiumUsagePrimaryValue(snapshot, metric),
                level,
                ratio: premiumPresentation.usedPercent / 100,
                title: describePremiumUsageTitle(snapshot, metric),
            };
        }

        if (Number.isFinite(metric.remaining) && metric.remaining <= 0) {
            return {
                badge: "소진",
                level: "empty",
                ratio: 0,
                title: describePremiumUsageTitle(snapshot, metric),
            };
        }

        return {
            badge: formatPremiumUsagePrimaryValue(snapshot, metric),
            level: "indeterminate",
            ratio: 0.18,
            title: describePremiumUsageTitle(snapshot, metric),
        };
    }

    if (!Number.isFinite(metric.remaining)) {
        return {
            badge: "확인됨",
            level: "indeterminate",
            ratio: 0.18,
            title: "Chat messages server snapshot",
        };
    }

    if (metric.remaining <= 0) {
        return {
            badge: "소진",
            level: "empty",
            ratio: 0,
            title: `잔여 수량 0`,
        };
    }

    if (metric.remaining <= config.low) {
        return {
            badge: "주의",
            level: "low",
            ratio: Math.max(0.16, metric.remaining / config.high),
            title: `잔여 수량 ${formatUsageRemaining(metric)}`,
        };
    }

    if (metric.remaining <= config.medium) {
        return {
            badge: "보통",
            level: "medium",
            ratio: Math.max(0.34, metric.remaining / config.high),
            title: `잔여 수량 ${formatUsageRemaining(metric)}`,
        };
    }

    return {
        badge: "여유",
        level: "high",
        ratio: Math.min(1, Math.max(0.58, metric.remaining / config.high)),
        title: `잔여 수량 ${formatUsageRemaining(metric)}`,
    };
}

function applyUsageMetricVisual(cardElement, badgeElement, fillElement, metaElement, metricKey, snapshot, metric) {
    const visual = getUsageVisual(metricKey, snapshot, metric);
    cardElement.dataset.level = visual.level;
    cardElement.title = visual.title;
    badgeElement.textContent = visual.badge;
    badgeElement.setAttribute("aria-label", `${metricKey} 상태 ${visual.badge}`);
    fillElement.style.width = `${Math.max(0, Math.min(100, visual.ratio * 100)).toFixed(2)}%`;
    metaElement.textContent = describeUsageMetric(snapshot, metricKey, metric);
}

function describeUsageMetric(snapshot, metricKey, metric) {
    if (metric.status === "available") {
        return "server snapshot";
    }

    if (state.copilot.status !== "connected") {
        return "로그인 필요";
    }

    if (snapshot.status === "partial") {
        return "일부 응답 누락";
    }

    return "조회 불가";
}

function describeUsageSummary(snapshot) {
    if (state.copilot.status !== "connected") {
        return USAGE_SUMMARY_REASON_MESSAGES.not_authenticated;
    }

    if (snapshot.status === "ok") {
        return `마지막 갱신: ${describeTimestamp(snapshot.fetchedAt)}`;
    }

    return USAGE_SUMMARY_REASON_MESSAGES[snapshot.reason] || USAGE_SUMMARY_REASON_MESSAGES.copilot_usage_unavailable;
}

function applyUsageSnapshot(snapshot) {
    state.copilot.usage = normalizeUsageSnapshot(snapshot);
}

function resetUsageSnapshot(reason = "not_authenticated") {
    state.copilot.usage = createEmptyUsageSnapshot(reason);
}

function renderUsageSummary() {
    const usage = normalizeUsageSnapshot(state.copilot.usage);
    state.copilot.usage = usage;
    const hidePremiumUsage = shouldHidePremiumUsage(usage, usage.premiumRequests);
    const authPremiumRow = elements.authPremiumRequestsRemaining?.parentElement;

    elements.usageSummary.dataset.status = usage.status;
    applyUsageMetricVisual(
        elements.chatMessagesCard,
        elements.chatMessagesRemaining,
        elements.chatMessagesChartFill,
        elements.chatMessagesMeta,
        "chatMessages",
        usage,
        usage.chatMessages,
    );
    elements.premiumRequestsCard.hidden = hidePremiumUsage;
    if (authPremiumRow) {
        authPremiumRow.hidden = hidePremiumUsage;
    }
    if (!hidePremiumUsage) {
        applyUsageMetricVisual(
            elements.premiumRequestsCard,
            elements.premiumRequestsRemaining,
            elements.premiumRequestsChartFill,
            elements.premiumRequestsMeta,
            "premiumRequests",
            usage,
            usage.premiumRequests,
        );
    }
    elements.usageSummaryDetail.textContent = describeUsageSummary(usage);

    elements.authChatMessagesRemaining.textContent = formatUsageRemaining(usage.chatMessages);
    if (!hidePremiumUsage) {
        elements.authPremiumRequestsRemaining.textContent = formatPremiumUsagePrimaryValue(usage, usage.premiumRequests);
    }
    elements.authUsageDetail.textContent = describeUsageSummary(usage);
    syncUsageSummaryLayout();
}

function isAbortError(error) {
    return error?.name === "AbortError";
}

function delay(ms, signal) {
    return new Promise((resolve, reject) => {
        const timer = window.setTimeout(resolve, ms);
        if (!signal) {
            return;
        }

        signal.addEventListener(
            "abort",
            () => {
                window.clearTimeout(timer);
                reject(new DOMException("Aborted", "AbortError"));
            },
            { once: true },
        );
    });
}

function resolvePollDelayMs(payload) {
    const retryAfter = Number(payload?.retryAfter);
    if (Number.isFinite(retryAfter)) {
        return Math.max(Math.ceil(retryAfter * 1000), 1000);
    }

    const nextPollAt = Number(payload?.nextPollAt);
    if (Number.isFinite(nextPollAt)) {
        return Math.max(Math.ceil(nextPollAt * 1000 - Date.now()), 1000);
    }

    const intervalSeconds = Number(payload?.interval);
    if (Number.isFinite(intervalSeconds) && intervalSeconds > 0) {
        return Math.max(Math.ceil(intervalSeconds * 1000), 1000);
    }

    return 5000;
}

function updateCredentialEnvelopeFromResponse(response) {
    const refreshedEnvelope = response.headers.get("X-Copilot-Credential-Envelope");
    if (!refreshedEnvelope) {
        return;
    }

    persistCredentialEnvelope(refreshedEnvelope);
    state.copilot.status = "connected";
    renderAuthState();
}

function resolveServerErrorMessage(code, fallback = "요청을 처리하지 못했습니다.") {
    if (typeof code !== "string" || !code) {
        return fallback;
    }

    if (code === "copilot_upstream_error" && fallback) {
        return fallback;
    }

    return SERVER_ERROR_MESSAGES[code] || fallback;
}

function extractServerErrorCode(payload) {
    return typeof payload?.code === "string" && SERVER_ERROR_MESSAGES[payload.code] ? payload.code : "";
}

async function extractErrorResponse(response) {
    const fallback = { message: "요청을 처리하지 못했습니다.", code: "" };
    const contentType = response.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
        try {
            const payload = await response.json();
            const payloadMessage =
                typeof payload?.message === "string" && payload.message.trim()
                    ? payload.message.trim()
                    : fallback.message;
            const code = extractServerErrorCode(payload);
            return {
                message: resolveServerErrorMessage(code, payloadMessage),
                code: typeof payload?.code === "string" ? payload.code : code,
            };
        } catch {
            return fallback;
        }
    }

    await response.text();
    return fallback;
}

function populateModelOptions() {
    elements.modelSelect.innerHTML = "";
    const models = state.models.length > 0 ? state.models : [{ id: DEFAULT_MODEL }];
    const activeModel = resolveModelId(getActiveSession()?.model || DEFAULT_MODEL);

    models.forEach((model) => {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.id;
        option.selected = model.id === activeModel;
        elements.modelSelect.appendChild(option);
    });

    const session = getActiveSession();
    if (session) {
        session.model = activeModel;
    }
    syncModelSelect();
}

function normalizeModels(payload) {
    const rawModels = Array.isArray(payload?.data)
        ? payload.data
        : Array.isArray(payload?.models)
            ? payload.models
            : Array.isArray(payload)
                ? payload
                : [];

    return rawModels
        .map((item) => {
            if (typeof item === "string") {
                return { id: item };
            }

            if (item && typeof item.id === "string") {
                return { id: item.id };
            }

            if (item && typeof item.model_name === "string") {
                return { id: item.model_name };
            }

            return null;
        })
        .filter(Boolean);
}

async function loadModels() {
    elements.modelSelect.disabled = true;
    populateModelOptions();

    try {
        const response = await fetch("/api/models");
        if (!response.ok) {
            throw new Error((await extractErrorResponse(response)).message);
        }

        const payload = await response.json();
        state.models = normalizeModels(payload);
        state.sessions.forEach((session) => {
            session.model = resolveModelId(session.model);
        });
    } catch (error) {
        state.models = [{ id: DEFAULT_MODEL }];
        state.sessions.forEach((session) => {
            session.model = DEFAULT_MODEL;
        });
        showToast(error.message || "모델 목록을 불러오지 못했습니다.", "error");
    } finally {
        populateModelOptions();
        elements.modelSelect.disabled = false;
    }
}

function extractStreamText(payload) {
    const choice = payload?.choices?.[0];
    const delta = choice?.delta ?? choice?.message ?? null;

    if (!delta) {
        return "";
    }

    if (typeof delta.content === "string") {
        return delta.content;
    }

    if (Array.isArray(delta.content)) {
        return delta.content
            .map((part) => {
                if (typeof part === "string") {
                    return part;
                }

                if (part && typeof part.text === "string") {
                    return part.text;
                }

                if (part && typeof part.content === "string") {
                    return part.content;
                }

                return "";
            })
            .join("");
    }

    if (typeof delta.reasoning_content === "string") {
        return delta.reasoning_content;
    }

    return "";
}

function extractAssistantSources(payload) {
    if (!payload || typeof payload !== "object" || payload.type !== "assistant_sources") {
        return [];
    }

    return normalizeMessageSources(payload.sources);
}

function extractAssistantStatus(payload) {
    if (!payload || typeof payload !== "object" || payload.type !== "assistant_status") {
        return "";
    }

    return typeof payload.status === "string" ? payload.status : "";
}

function extractStreamError(payload) {
    if (!payload || typeof payload !== "object") {
        return "";
    }

    return resolveServerErrorMessage(extractServerErrorCode(payload), "");
}

function processSseEvent(eventText, onChunk, onSources, onStatus) {
    const dataLines = eventText
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart());

    if (dataLines.length === 0) {
        return { finished: false, error: "" };
    }

    const data = dataLines.join("\n");
    if (data === "[DONE]") {
        return { finished: true, error: "" };
    }

    let payload;
    try {
        payload = JSON.parse(data);
    } catch {
        return { finished: false, error: "" };
    }

    const error = extractStreamError(payload);
    if (error) {
        return { finished: false, error };
    }

    const sources = extractAssistantSources(payload);
    if (sources.length > 0) {
        onSources(sources);
    }

    const status = extractAssistantStatus(payload);
    if (status) {
        onStatus(status);
    }

    const chunk = extractStreamText(payload);
    if (chunk) {
        onChunk(chunk);
    }

    return { finished: false, error: "" };
}

async function consumeChatStream(stream, session, assistantMessage) {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    const appendChunk = (chunk) => {
        if (state.composerStatus.message !== COMPOSER_STREAMING_MESSAGE) {
            setComposerStatus(COMPOSER_STREAMING_MESSAGE, "pending");
        }
        assistantMessage.content += chunk;
        session.updatedAt = Date.now();
        updateMessageContent(assistantMessage, { useStreamFocus: true });
    };

    const applySources = (sources) => {
        assistantMessage.sources = sources;
        session.updatedAt = Date.now();
        updateMessageContent(assistantMessage, { useStreamFocus: true });
    };

    const applyAssistantStatus = (status) => {
        const message = ASSISTANT_STATUS_MESSAGES[status];
        if (!message) {
            return;
        }

        setComposerStatus(message, "pending");
    };

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                break;
            }

            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split(/\r?\n\r?\n/);
            buffer = events.pop() ?? "";

            for (const eventText of events) {
                const result = processSseEvent(eventText, appendChunk, applySources, applyAssistantStatus);
                if (result.error) {
                    throw new Error(result.error);
                }
                if (result.finished) {
                    return;
                }
            }
        }

        buffer += decoder.decode();
        if (buffer.trim()) {
            const result = processSseEvent(buffer, appendChunk, applySources, applyAssistantStatus);
            if (result.error) {
                throw new Error(result.error);
            }
        }
    } finally {
        reader.releaseLock();
    }
}

async function extractErrorMessage(response) {
    return (await extractErrorResponse(response)).message;
}

function openAuthModal() {
    state.copilot.modalOpen = true;
    renderAuthState();
}

function resetLoginFlowState() {
    state.copilot.isAuthorizing = false;
    state.copilot.loginId = null;
    state.copilot.loginIntervalMs = 5000;
    state.copilot.loginExpiresAt = 0;
    state.copilot.userCode = "";
    state.copilot.verificationUri = "";
    state.copilot.verificationUriComplete = "";
    state.copilot.loginAbortController = null;
}

function cancelCopilotAuthorization() {
    state.copilot.loginAbortController?.abort();
    resetLoginFlowState();
    if (!state.copilot.envelope) {
        state.copilot.status = "disconnected";
    }
    renderAuthState();
}

function closeAuthModal({ cancelAuthorization = false } = {}) {
    if (cancelAuthorization && state.copilot.isAuthorizing) {
        cancelCopilotAuthorization();
    }
    state.copilot.modalOpen = false;
    renderAuthState();
}

function renderAuthState() {
    const copilot = state.copilot;
    const button = elements.authStatusButton;
    const statusText = elements.authStatusText;
    let nextStatusLabel = "로그인 필요";

    button.disabled = copilot.status === "checking";
    button.classList.remove("connected", "pending");

    if (copilot.status === "checking") {
        nextStatusLabel = "상태 확인 중";
    } else if (copilot.isAuthorizing) {
        nextStatusLabel = "인증 진행 중";
        button.classList.add("pending");
    } else if (copilot.status === "connected") {
        nextStatusLabel = "연결됨";
        button.classList.add("connected");
    } else {
        nextStatusLabel = "로그인 필요";
    }

    statusText.textContent = nextStatusLabel;
    button.setAttribute("aria-label", `GitHub Copilot 상태: ${nextStatusLabel}`);
    button.title = `GitHub Copilot 상태: ${nextStatusLabel}`;
    renderUsageSummary();

    elements.authModal.classList.toggle("hidden", !copilot.modalOpen);
    elements.authModal.setAttribute("aria-hidden", String(!copilot.modalOpen));
    elements.authCodeSection.classList.add("hidden");
    elements.authConnectedSection.classList.add("hidden");
    elements.authSecondaryButton.classList.remove("hidden");

    if (copilot.isAuthorizing) {
        elements.authModalTitle.textContent = "GitHub Copilot 인증 진행 중";
        elements.authModalDescription.textContent = "GitHub 로그인 창에서 코드를 입력하면 현재 브라우저 세션에만 Copilot 자격 정보가 연결됩니다.";
        elements.authStatusMeta.textContent = `코드 만료 시각: ${describeTimestamp(copilot.loginExpiresAt)}`;
        elements.authCodeSection.classList.remove("hidden");
        elements.authUserCode.textContent = copilot.userCode || "-";
        elements.authVerificationLink.href = copilot.verificationUriComplete || copilot.verificationUri || "#";
        elements.authPrimaryButton.textContent = "GitHub 열기";
        elements.authSecondaryButton.textContent = "취소";
        return;
    }

    if (copilot.status === "connected") {
        elements.authModalTitle.textContent = "GitHub Copilot 연결됨";
        elements.authModalDescription.textContent = "암호화된 Copilot 자격 정보가 현재 브라우저 세션에 바인딩되어 있으며, 서버 요청 시마다 복호화해 LiteLLM 호출에만 사용합니다.";
        elements.authStatusMeta.textContent = copilot.ephemeralSecret
            ? "서버 임시 시크릿으로 보호 중입니다. COPILOT_ENVELOPE_SECRET을 설정하면 재시작 후에도 유지할 수 있습니다."
            : "브라우저 저장소에는 암호화된 credential envelope만 저장됩니다.";
        elements.authConnectedSection.classList.remove("hidden");
        elements.authCredentialId.textContent = copilot.credentialId || "-";
        elements.authExpiryText.textContent = describeTimestamp(copilot.copilotTokenExpiresAt);
        elements.authPrimaryButton.textContent = "닫기";
        elements.authSecondaryButton.textContent = "연결 해제";
        return;
    }

    elements.authModalTitle.textContent = copilot.errorMessage ? "GitHub Copilot 다시 연결" : "GitHub Copilot 연결";
    elements.authModalDescription.textContent = copilot.errorMessage || "메시지를 보내기 전에 GitHub 로그인을 완료하면, 현재 브라우저에서 Copilot 계정으로 채팅 요청을 전송합니다.";
    elements.authStatusMeta.textContent = copilot.ephemeralSecret
        ? "현재 서버 임시 시크릿을 사용 중입니다. 서버를 재시작하면 다시 로그인해야 합니다."
        : "브라우저 저장소에 암호화된 envelope를 저장하므로 같은 세션에서 다시 사용할 수 있습니다.";
    elements.authPrimaryButton.textContent = "로그인 시작";
    elements.authSecondaryButton.textContent = "닫기";
}

async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    updateCredentialEnvelopeFromResponse(response);
    if (!response.ok) {
        const error = await extractErrorResponse(response);
        const failure = new Error(error.message);
        failure.code = error.code;
        throw failure;
    }
    return response.json();
}

function applyConnectedCopilotState(payload) {
    state.copilot.status = "connected";
    state.copilot.errorMessage = "";
    state.copilot.credentialId = payload?.credentialId ?? state.copilot.credentialId;
    state.copilot.copilotTokenExpiresAt = payload?.copilotTokenExpiresAt ?? state.copilot.copilotTokenExpiresAt;
    state.copilot.needsRefresh = Boolean(payload?.needsRefresh);
    state.copilot.ephemeralSecret = Boolean(payload?.ephemeralSecret ?? state.copilot.ephemeralSecret);
    applyUsageSnapshot(payload?.usage);
    renderAuthState();
}

async function refreshCopilotStatus({ preserveVisualState = false, silent = false } = {}) {
    if (!preserveVisualState) {
        state.copilot.status = "checking";
        renderAuthState();
    }

    try {
        const payload = await requestJson("/api/copilot/status", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                credentialEnvelope: state.copilot.envelope || null,
            }),
        });

        state.copilot.ephemeralSecret = Boolean(payload?.ephemeralSecret);
        if (payload?.authenticated) {
            applyConnectedCopilotState(payload);
            return;
        }

        if (payload?.shouldClearEnvelope) {
            persistCredentialEnvelope("");
        }

        state.copilot.status = "disconnected";
        state.copilot.credentialId = null;
        state.copilot.copilotTokenExpiresAt = 0;
        state.copilot.needsRefresh = false;
        applyUsageSnapshot(payload?.usage);
        if (payload?.shouldClearEnvelope) {
            state.copilot.errorMessage = resolveServerErrorMessage(
                extractServerErrorCode(payload),
                "GitHub Copilot 자격 정보를 다시 확인하세요.",
            );
        }
    } catch (error) {
        if (!preserveVisualState) {
            state.copilot.status = state.copilot.envelope ? "connected" : "disconnected";
        }
        if (!silent) {
            showToast(error.message || "GitHub Copilot 상태를 확인하지 못했습니다.", "error");
        }
    }

    renderAuthState();
}

function openVerificationWindow(url) {
    if (!url) {
        return;
    }

    window.open(url, "_blank", "noopener,noreferrer");
}

async function pollCopilotAuthorization(initialLoginId, signal, initialDelayMs = 0) {
    let loginId = initialLoginId;
    let waitMs = initialDelayMs;

    while (true) {
        if (waitMs > 0) {
            await delay(waitMs, signal);
        }

        if (signal.aborted) {
            throw new DOMException("Aborted", "AbortError");
        }

        const payload = await requestJson("/api/copilot/login/poll", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ loginId }),
            signal,
        });

        if (payload?.status === "complete") {
            return payload;
        }

        if (typeof payload?.loginId === "string" && payload.loginId) {
            loginId = payload.loginId;
            state.copilot.loginId = loginId;
        }

        state.copilot.loginIntervalMs = resolvePollDelayMs(payload);
        state.copilot.loginExpiresAt = payload?.expiresAt || state.copilot.loginExpiresAt;
        renderAuthState();
        waitMs = state.copilot.loginIntervalMs;
    }
}

async function beginCopilotAuthorization() {
    openAuthModal();
    state.copilot.errorMessage = "";
    state.copilot.isAuthorizing = true;
    const loginAbortController = new AbortController();
    state.copilot.loginAbortController = loginAbortController;
    renderAuthState();

    try {
        const startPayload = await requestJson("/api/copilot/login/start", {
            method: "POST",
        });

        state.copilot.loginId = startPayload.loginId;
        state.copilot.userCode = startPayload.userCode;
        state.copilot.verificationUri = startPayload.verificationUri;
        state.copilot.verificationUriComplete = startPayload.verificationUriComplete || "";
        state.copilot.loginExpiresAt = startPayload.expiresAt || 0;
        state.copilot.loginIntervalMs = resolvePollDelayMs(startPayload);
        renderAuthState();

        openVerificationWindow(state.copilot.verificationUriComplete || state.copilot.verificationUri);
        const pollPayload = await pollCopilotAuthorization(
            startPayload.loginId,
            loginAbortController.signal,
            state.copilot.loginIntervalMs,
        );

        persistCredentialEnvelope(pollPayload.credentialEnvelope || "");
        resetLoginFlowState();
        applyConnectedCopilotState(pollPayload);
        await loadConversationState({ silent: true });
        showToast("GitHub Copilot 계정이 현재 브라우저에 연결되었습니다.", "info");
        return true;
    } catch (error) {
        resetLoginFlowState();
        if (isAbortError(error)) {
            renderAuthState();
            return false;
        }

        state.copilot.status = state.copilot.envelope ? "connected" : "disconnected";
        state.copilot.errorMessage = error.message || "GitHub 로그인에 실패했습니다.";
        renderAuthState();
        showToast(state.copilot.errorMessage, "error");
        return false;
    }
}

async function ensureCopilotCredentials({ interactive = false } = {}) {
    if (state.copilot.envelope && state.copilot.status === "connected") {
        return true;
    }

    if (!interactive) {
        return false;
    }

    if (!state.copilot.authPromise) {
        state.copilot.authPromise = beginCopilotAuthorization().finally(() => {
            state.copilot.authPromise = null;
        });
    } else {
        openAuthModal();
    }

    return state.copilot.authPromise;
}

async function disconnectCopilot() {
    try {
        await requestJson("/api/copilot/logout", {
            method: "POST",
        });
        persistCredentialEnvelope("");
        clearComposerAttachments();
        resetLoginFlowState();
        state.copilot.status = "disconnected";
        state.copilot.errorMessage = "";
        state.copilot.credentialId = null;
        state.copilot.copilotTokenExpiresAt = 0;
        state.copilot.needsRefresh = false;
        resetUsageSnapshot();
        clearConversationState();
        await loadConversationState({ silent: true });
        closeAuthModal();
        showToast("GitHub Copilot 연결을 해제했습니다.", "info");
    } catch (error) {
        showToast(error.message || "GitHub Copilot 연결을 해제하지 못했습니다.", "error");
    }
}

async function handleAuthPrimaryAction() {
    if (state.copilot.isAuthorizing) {
        openVerificationWindow(state.copilot.verificationUriComplete || state.copilot.verificationUri);
        return;
    }

    if (state.copilot.status === "connected") {
        closeAuthModal();
        return;
    }

    await ensureCopilotCredentials({ interactive: true });
}

async function handleAuthSecondaryAction() {
    if (state.copilot.isAuthorizing) {
        closeAuthModal({ cancelAuthorization: true });
        return;
    }

    if (state.copilot.status === "connected") {
        await disconnectCopilot();
        return;
    }

    closeAuthModal();
}

async function sendPrompt() {
    const content = elements.promptInput.value.trim();
    const uploadedAttachments = buildUploadedAttachmentPayload();
    const optimisticAttachments = buildOptimisticAttachmentPayload();
    if (!content && uploadedAttachments.length === 0) {
        return;
    }

    if (state.composerAttachments.some((attachment) => COMPOSER_ATTACHMENT_BUSY_STATES.has(attachment.status))) {
        showToast("이미지 업로드가 끝난 뒤 전송하세요.", "info");
        return;
    }

    const hasCredentials = await ensureCopilotCredentials({ interactive: true });
    if (!hasCredentials) {
        focusInput();
        return;
    }

    const session = await ensureConversationSession();
    if (!session) {
        focusInput();
        return;
    }

    const userMessage = {
        id: createId("message"),
        role: "user",
        content,
        attachments: optimisticAttachments,
        status: "complete",
    };

    session.messages.push(userMessage);
    session.updatedAt = Date.now();
    updateSessionTitle(session);
    promoteSession(session.id);

    const assistantMessage = {
        id: createId("message"),
        role: "assistant",
        content: "",
        sources: [],
        status: "streaming",
    };

    session.messages.push(assistantMessage);
    session.updatedAt = Date.now();

    setMessageScrollMode("latest");
    state.abortController = new AbortController();
    setStreamingState(true);
    renderSidebar();
    renderMessages();
    let shouldRefreshUsage = false;
    let shouldResyncConversation = false;
    let didPersistTurn = false;
    let didReceiveResponse = false;

    try {
        const response = await fetch(`/api/conversations/${encodeURIComponent(session.id)}/messages`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                content,
                attachments: uploadedAttachments,
                model: session.model || DEFAULT_MODEL,
                credentialEnvelope: state.copilot.envelope,
                tools: CHAT_TOOLS_PAYLOAD,
                tool_choice: CHAT_TOOL_CHOICE,
                parallel_tool_calls: false,
            }),
            signal: state.abortController.signal,
        });
        didReceiveResponse = true;

        updateCredentialEnvelopeFromResponse(response);
        if (!response.ok) {
            const error = await extractErrorResponse(response);
            if (response.status === 401) {
                persistCredentialEnvelope("");
                state.copilot.status = "disconnected";
                state.copilot.errorMessage = error.message;
                resetUsageSnapshot("not_authenticated");
                openAuthModal();
                renderAuthState();
            }
            shouldResyncConversation = true;
            throw new Error(error.message);
        }

        didPersistTurn = true;
        elements.promptInput.value = "";
        adjustTextareaHeight();
        clearComposerAttachments();

        if (!response.body) {
            throw new Error("스트리밍 응답을 받을 수 없습니다.");
        }

        await consumeChatStream(response.body, session, assistantMessage);
        shouldResyncConversation = didPersistTurn;
        shouldRefreshUsage = true;
    } catch (error) {
        if (state.abortController?.signal.aborted) {
            shouldResyncConversation = true;
            setComposerStatus(COMPOSER_ABORTED_MESSAGE, "stopped", { persistMs: COMPOSER_STATUS_HOLD_MS });
            showToast("응답 생성을 중단했습니다.", "info");
        } else {
            shouldResyncConversation = true;
            if (!didPersistTurn) {
                elements.promptInput.value = content;
                adjustTextareaHeight();
            }
            if (!assistantMessage.content) {
                applyAssistantFailureMessage(
                    session,
                    assistantMessage,
                    error.message || "응답을 가져오지 못했습니다.",
                );
            }
            if (state.copilot.status !== "connected") {
                resetUsageSnapshot("not_authenticated");
            }
        }
    } finally {
        if (!assistantMessage.content) {
            session.messages = session.messages.filter((message) => message.id !== assistantMessage.id);
        }

        session.updatedAt = Date.now();
        state.abortController = null;
        setStreamingState(false);
        renderSidebar();
        renderMessages();
        focusInput();

        if (shouldResyncConversation) {
            await syncConversationStateSilently();
        }

        if (shouldRefreshUsage && state.copilot.envelope && state.copilot.status === "connected") {
            await refreshCopilotStatus({ preserveVisualState: true, silent: true });
        }
    }
}

function handleComposerSubmit(event) {
    event.preventDefault();

    if (state.isStreaming) {
        state.abortController?.abort();
        return;
    }

    void sendPrompt();
}

function setSidebarOverlayOpen(isOpen, options = {}) {
    if (!elements.sidebarOverlay) {
        return;
    }

    clearSidebarOverlayTimer();

    if (isOpen) {
        elements.sidebarOverlay.classList.add("open");
        elements.sidebarOverlay.style.opacity = "1";
        return;
    }

    elements.sidebarOverlay.style.opacity = "0";

    if (options.immediate) {
        elements.sidebarOverlay.classList.remove("open");
        return;
    }

    state.ui.sidebarOverlayTimerId = window.setTimeout(() => {
        state.ui.sidebarOverlayTimerId = null;
        if (elements.sidebar?.classList.contains("open")) {
            elements.sidebarOverlay.style.opacity = "1";
            return;
        }

        elements.sidebarOverlay.classList.remove("open");
    }, SIDEBAR_OVERLAY_HIDE_DELAY_MS);
}

function openSidebar() {
    if (elements.sidebar) {
        elements.sidebar.classList.add("open");
    }

    setSidebarOverlayOpen(true);
}

function closeSidebar(options = {}) {
    if (elements.sidebar) {
        elements.sidebar.classList.remove("open");
    }

    setSidebarOverlayOpen(false, options);
}

function bindEvents() {
    if (elements.menuButton) elements.menuButton.addEventListener("click", toggleSidebar);
    if (elements.toggleSidebarButton) elements.toggleSidebarButton.addEventListener("click", toggleSidebar);
    if (elements.closeMenuButton) elements.closeMenuButton.addEventListener("click", closeSidebar);
    if (elements.sidebarOverlay) elements.sidebarOverlay.addEventListener("click", closeSidebar);

    elements.newChatButton.addEventListener("click", () => {
        void createSession();
    });

    elements.attachmentButton?.addEventListener("click", openAttachmentPicker);
    elements.attachmentInput?.addEventListener("change", (event) => {
        void handleAttachmentInputChange(event);
    });
    elements.composerDropzone?.addEventListener("dragover", handleComposerDragOver);
    elements.composerDropzone?.addEventListener("dragenter", handleComposerDragEnter);
    elements.composerDropzone?.addEventListener("dragleave", handleComposerDragLeave);
    elements.composerDropzone?.addEventListener("drop", (event) => {
        void handleComposerDrop(event);
    });

    elements.modelSelect.addEventListener("change", async (event) => {
        const session = getActiveSession();
        if (!session) {
            return;
        }

        const previousModel = session.model;
        session.model = resolveModelId(event.target.value);
        syncModelSelect();
        await persistSessionModel(session.id, session.model, previousModel);
    });

    elements.composerForm.addEventListener("submit", handleComposerSubmit);
    elements.messages.addEventListener("scroll", handleMessagesScroll, { passive: true });
    elements.messageHistoryJumpButton?.addEventListener("click", jumpToPreviousUserMessage);
    elements.authStatusButton.addEventListener("click", async () => {
        if (state.copilot.status === "connected") {
            openAuthModal();
            return;
        }

        await ensureCopilotCredentials({ interactive: true });
    });
    elements.authPrimaryButton.addEventListener("click", handleAuthPrimaryAction);
    elements.authSecondaryButton.addEventListener("click", handleAuthSecondaryAction);
    elements.authModalCloseButton.addEventListener("click", () => {
        closeAuthModal({ cancelAuthorization: state.copilot.isAuthorizing });
    });
    elements.authModal.addEventListener("click", (event) => {
        if (event.target === elements.authModal) {
            closeAuthModal({ cancelAuthorization: state.copilot.isAuthorizing });
        }
    });

    elements.promptInput.addEventListener("input", () => {
        adjustTextareaHeight();
        updateComposerControls();
    });

    elements.promptInput.addEventListener("focus", () => {
        scheduleScrollMessagesToBottom(3);
    });

    elements.promptInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            handleComposerSubmit(event);
        }
    });

    elements.usageSummaryToggle.addEventListener("click", () => {
        if (!state.ui.isCompactUsage) {
            return;
        }

        state.ui.isUsageSummaryCollapsed = !state.ui.isUsageSummaryCollapsed;
        syncUsageSummaryLayout();
    });

    window.addEventListener("resize", syncResponsiveLayout);
    window.visualViewport?.addEventListener("resize", syncViewportHeightVar);
    window.visualViewport?.addEventListener("scroll", syncViewportHeightVar);

    window.addEventListener("beforeunload", () => {
        clearComposerStatusTimer();
        clearSidebarOverlayTimer();
        cleanupComposerAttachmentObjectUrls();
        state.abortController?.abort();
        state.copilot.loginAbortController?.abort();
    });
}

async function init() {
    bindEvents();
    syncViewportHeightVar();
    renderComposerAttachments();
    adjustTextareaHeight();
    updateComposerControls();
    syncComposerStatus();
    syncResponsiveLayout();
    populateModelOptions();
    renderAuthState();
    await refreshCopilotStatus();
    await loadConversationState({ silent: true });
    await loadModels();
    state.sessions.forEach((session) => {
        session.model = resolveModelId(session.model);
    });
    populateModelOptions();
    if (state.sessions.length === 0) {
        await createSession({ focus: false, silent: true });
    }
}

init();
