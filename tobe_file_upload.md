# 이미지 첨부 기능 To-Be 구현 설계

## 문서 목적

이 문서는 현재 저장소에 이미지 첨부 기능을 추가하기 위한 최종 구현 설계 문서다. 목표는 다음과 같다.

1. 어떤 AI 에이전트가 읽어도 그대로 구현 순서를 따라갈 수 있어야 한다.
2. 현재 코드베이스의 구조와 충돌하지 않아야 한다.
3. 클라이언트 UI 에서 이미지 선택과 압축이 시작되는 시점부터, 서버 디스크 저장, 대화 저장, LiteLLM 멀티모달 호출까지 전체 흐름을 하나의 설계로 고정해야 한다.

이 문서는 구현 문서다. 아이디어 메모가 아니다. 모호한 선택지는 남기지 않고, 실제로 구현할 방식을 명시한다.

## 고정 요구사항

이 문서에서 구현 대상으로 확정하는 요구사항은 아래와 같다.

1. 첨부 가능 파일은 이미지 파일만 허용한다.
2. 원본 파일 기준으로 이미지 1개당 15MB 이하만 선택 가능하다.
3. 한 번의 메시지에 최대 5개까지 첨부 가능하다.
4. PC 에서는 파일 선택과 드래그 앤 드롭 둘 다 지원한다.
5. 모바일에서는 파일 입력을 통해 갤러리 선택을 지원한다.
6. 클라이언트는 업로드 전에 이미지를 압축한다.
7. 압축 목표는 이미지 1개당 5MB 이하이다.
8. 업로드 API 는 채팅 API 와 분리한다.
9. 업로드된 이미지는 서버 디스크에 저장한다.
10. 첨부 파일 보관 기간은 대화 TTL 과 동일한 7일이다.
11. 텍스트 없이 이미지만 있는 메시지도 허용한다.
12. 모델 호출은 LiteLLM 을 통해 GitHub Copilot API 로 멀티모달 요청을 보낸다.

## 비목표

이번 설계에서 일부러 제외하는 범위는 아래와 같다.

1. PDF, 문서, 오디오, 비디오 첨부
2. 다중 사용자 공유 첨부 링크
3. CDN 또는 외부 오브젝트 스토리지 전환
4. 이미지 편집 기능
5. 메시지 전송 이후 첨부 교체

## 현재 코드 기준 영향 파일

현재 저장소에서 직접 수정 대상이 되는 핵심 파일은 아래와 같다.

1. `requirements.txt`
2. `main.py`
3. `services/conversation_service.py`
4. `services/copilot_chat.py`
5. `static/index.html`
6. `static/style.css`
7. `static/app.js`

새로 추가할 파일은 아래를 권장한다.

1. `services/image_attachment_service.py`
2. `tobe_file_upload.md` 이 문서

## 최종 아키텍처 요약

구현 구조는 아래처럼 세 층으로 나눈다.

1. 클라이언트는 파일 선택, 드래그 앤 드롭, 미리보기, 압축, 업로드, 전송 전 삭제, 업로드 실패 재시도를 담당한다.
2. 서버 업로드 계층은 이미지 검증, 디스크 저장, 첨부 메타데이터 저장, 미리보기 URL 발급, 파일 삭제를 담당한다.
3. 채팅 계층은 첨부 ID 를 메시지와 연결하고, 저장된 이미지를 읽어서 LiteLLM 멀티모달 메시지로 변환해 모델을 호출한다.

핵심 원칙은 아래와 같다.

1. 브라우저는 원본 이미지를 업로드 전에 압축한다.
2. 서버는 클라이언트 압축 결과를 신뢰하지 않고 다시 검증한다.
3. 서버 디스크 경로를 LLM 에 그대로 보내지 않는다.
4. LLM 호출 직전에 서버가 이미지 파일을 읽어 base64 data URL 로 변환한다.
5. 과거 메시지에 첨부된 이미지는 이후 턴마다 원본 base64 를 반복 전송하지 않는다.
6. 실제 이미지 바이너리는 현재 턴의 새 사용자 메시지에 대해서만 멀티모달 파트로 첨부한다.
7. 이전 턴의 첨부 이미지는 텍스트 요약으로만 history 에 포함한다.

## 가장 중요한 설계 결정

### 결정 1. 첨부 업로드 전에 대화 세션을 먼저 확보한다.

현재 앱은 `sendPrompt()` 시점에 `ensureConversationSession()` 으로 세션을 지연 생성한다. 첨부 업로드 API 를 분리하면, 업로드 파일이 어느 대화에 속하는지 먼저 알아야 한다.

따라서 구현 규칙을 아래처럼 고정한다.

1. 사용자가 첫 이미지를 선택하거나 드롭하는 순간 활성 세션이 없으면 `ensureConversationSession()` 을 먼저 호출한다.
2. 업로드된 이미지 레코드는 업로드 시점부터 특정 `conversation_id` 에 속한다.
3. 아직 메시지로 전송되지 않은 첨부는 `message_id = NULL` 상태로 보관한다.

이 선택의 장점은 아래와 같다.

1. 업로드 파일의 TTL 을 대화와 쉽게 묶을 수 있다.
2. 첨부 소유권 검증이 단순해진다.
3. 대화별 첨부 cleanup 이 쉬워진다.

### 결정 2. 클라이언트는 압축된 결과물만 업로드한다.

원본과 압축본을 둘 다 서버에 저장하지 않는다. 이번 구현에서는 서버 디스크에 압축 완료된 업로드본만 저장한다.

이 선택의 이유는 아래와 같다.

1. 디스크 사용량이 줄어든다.
2. TTL 삭제 대상이 단순해진다.
3. LLM 에 넘길 파일과 실제 저장 파일이 동일해져 구현이 단순해진다.

원본 크기 15MB 검사는 클라이언트가 먼저 하고, 서버는 업로드본 크기만 검증한다.

### 결정 3. 모델에는 새 턴의 이미지 파일만 실제 이미지 파트로 전달한다.

현재 앱은 전체 visible history 를 매번 모델에 다시 보낸다. 이미지가 포함된 이전 메시지를 매 턴마다 다시 base64 로 보내면 요청 크기와 비용이 폭증한다.

따라서 아래 규칙을 고정한다.

1. 현재 전송 중인 새 사용자 메시지의 첨부 이미지만 `image_url` 파트로 보낸다.
2. 이전 대화에 존재하는 사용자 첨부 이미지는 history 에서 텍스트 요약으로만 재구성한다.
3. 텍스트 요약 형식은 서버가 일관되게 생성한다.

권장 요약 형식은 아래와 같다.

```text
[사용자가 이미지 3개를 첨부함: IMG_1001.jpg, IMG_1002.jpg, IMG_1003.jpg]
```

이 방식으로 history 정보는 유지하면서 요청 payload 폭증을 방지한다.

### 결정 4. 비전 지원 여부는 DB 쓰기 전에 선검사한다.

이미지 첨부가 있는 메시지는 `begin_turn()` 으로 사용자 메시지 row 와 assistant placeholder row 를 먼저 저장한 뒤, 그 다음 모델 호출을 시작하는 구조로 구현하면 안 된다. 그 순서에서는 선택 모델이 vision 을 지원하지 않을 때 단순 검증 오류가 영구 대화 기록과 첨부 상태 변경으로 남는다.

따라서 구현 규칙을 아래처럼 고정한다.

1. 이미지 첨부가 있는 경우 `POST /api/conversations/{conversation_id}/messages` 안에서 `begin_turn()` 호출 전에 vision 지원 여부를 먼저 검사한다.
2. 검사 기준은 provider model 기준 `litellm.supports_vision(model=provider_model)` 이다.
3. 비전 미지원이면 `chat_model_not_vision_capable` 오류를 즉시 반환하고, user message row, assistant placeholder row, attachment status 변경을 포함한 어떤 DB 쓰기도 하지 않는다.
4. `CopilotChatService` 내부의 vision 검사는 방어적 중복 검증으로 유지할 수 있지만, 영구 저장 경로에서는 선검사가 주 경로다.

이 선택의 장점은 아래와 같다.

1. 첨부 메시지 전송 실패가 검증 오류로만 끝난다.
2. 실패 시 orphan assistant placeholder 나 잘못 연결된 attachment row 가 남지 않는다.
3. 구현자가 롤백 로직을 별도로 고민하지 않아도 된다.

## 최종 사용자 흐름

### 데스크톱 흐름

1. 사용자가 입력창 옆 첨부 버튼을 누른다.
2. 파일 선택 창에서 이미지 1개 이상 최대 5개까지 선택한다.
3. 또는 탐색기/갤러리에서 이미지를 입력 영역 위로 드래그 앤 드롭한다.
4. 클라이언트는 원본 개수와 크기와 MIME 을 먼저 검사한다.
5. 각 이미지를 순차 압축한다.
6. 각 이미지의 썸네일과 압축 결과 크기를 표시한다.
7. 압축 성공한 이미지를 업로드 API 로 개별 업로드한다.
8. 업로드 성공하면 각 첨부 항목은 `attachmentId` 를 가진 상태가 된다.
9. 사용자는 텍스트를 입력하거나, 텍스트 없이 첨부만 둔 상태에서 보내기를 누른다.
10. 서버는 첨부 ID 를 현재 사용자 메시지와 연결하고 LLM 멀티모달 호출을 시작한다.
11. 답변 스트리밍은 기존과 동일하게 SSE 로 동작한다.

### 모바일 흐름

1. 사용자가 첨부 버튼을 누른다.
2. 모바일 브라우저의 파일 선택 또는 갤러리 선택이 열린다.
3. 모바일 사진의 EXIF orientation 을 읽어 올바른 방향으로 보정한 뒤 압축한다.
4. 이후 흐름은 데스크톱과 동일하다.

### 전송 전 삭제 흐름

1. 사용자가 업로드 완료된 첨부 항목의 삭제 버튼을 누른다.
2. 클라이언트는 해당 항목을 즉시 비활성화한다.
3. 서버에 `DELETE /api/uploads/images/{attachment_id}` 를 호출한다.
4. 서버는 디스크 파일과 메타데이터를 모두 삭제한다.
5. 클라이언트는 리스트에서 항목을 제거한다.

### 업로드 실패 재시도 흐름

1. 압축 또는 업로드에 실패한 항목은 `failed` 상태로 남긴다.
2. 사용자는 재시도 버튼을 누른다.
3. 클라이언트는 해당 파일의 원본 `File` 객체로 다시 압축 또는 업로드를 수행한다.

## 클라이언트 UI 상세 설계

### `static/index.html` 에 추가할 요소

현재 composer 영역에 아래 요소를 추가한다.

```html
<form id="composerForm" class="composer" autocomplete="off">
    <div id="composerDropzone" class="composer-dropzone">
        <div id="composerAttachmentList" class="composer-attachment-list"></div>

        <div class="composer-inner">
            <input
                id="attachmentInput"
                type="file"
                accept="image/*"
                multiple
                hidden
            />

            <button
                id="attachmentButton"
                class="ghost-button attachment-button"
                type="button"
                aria-label="이미지 첨부"
                title="이미지 첨부"
            >
                첨부
            </button>

            <textarea
                id="promptInput"
                rows="1"
                placeholder="메시지를 입력하세요. Shift+Enter로 줄바꿈"
                aria-label="메시지 입력"
            ></textarea>

            <button id="sendButton" class="primary-button send-button flex-btn" type="submit">
                <span id="sendButtonText" class="btn-text">보내기</span>
            </button>
        </div>
    </div>

    <div class="composer-meta">
        <p id="streamStatus" class="stream-status" data-tone="ready" role="status" aria-live="polite" aria-atomic="true">전송 준비 완료</p>
    </div>
</form>
```

### 첨부 리스트 UI 규칙

각 첨부 항목은 아래 정보를 보여준다.

1. 썸네일
2. 파일명
3. 압축 후 크기
4. 상태 텍스트
5. 삭제 버튼
6. 실패 시 재시도 버튼

상태는 아래 값만 사용한다.

1. `queued`
2. `compressing`
3. `ready_to_upload`
4. `uploading`
5. `uploaded`
6. `failed`

### 드래그 앤 드롭 동작 규칙

1. 드롭 대상은 `#composerDropzone` 전체다.
2. `dragover` 이벤트에서는 `preventDefault()` 를 호출한다.
3. 이미지 파일이 하나라도 포함되어 있으면 하이라이트 스타일을 적용한다.
4. `drop` 이벤트에서는 최대 5개까지 잘라서 처리하지 않는다. 5개 초과면 전체 동작을 거부하고 토스트를 띄운다.
5. 비이미지 파일이 포함되어 있으면 전체 동작을 거부하고 토스트를 띄운다.

### 보내기 버튼 활성화 규칙

현재는 텍스트가 없으면 보내기가 사실상 무의미하다. 첨부 추가 후에는 아래 규칙으로 변경한다.

보내기 가능 조건:

1. 스트리밍 중이 아니다.
2. 텍스트가 있거나 `uploaded` 상태 첨부가 1개 이상 있다.
3. `uploading` 상태 첨부가 없다.
4. `compressing` 상태 첨부가 없다.

## 클라이언트 상태 모델 상세 설계

`static/app.js` 의 전역 상태에 아래 구조를 추가한다.

```javascript
const MAX_ATTACHMENTS_PER_MESSAGE = 5;
const MAX_ORIGINAL_IMAGE_BYTES = 15 * 1024 * 1024;
const TARGET_COMPRESSED_IMAGE_BYTES = 5 * 1024 * 1024;
const INITIAL_IMAGE_MAX_DIMENSION = 2048;
const SECONDARY_IMAGE_MAX_DIMENSION = 1600;
const INITIAL_JPEG_QUALITY = 0.86;
const MIN_JPEG_QUALITY = 0.55;
const JPEG_QUALITY_STEP = 0.08;

state.composerAttachments = [];
```

첨부 항목의 객체 구조는 아래로 고정한다.

```javascript
{
    localId: "att_local_xxx",
    fileName: "IMG_1001.JPG",
    originalFile: File,
    compressedFile: File | null,
    previewObjectUrl: "blob:..." | null,
    status: "queued" | "compressing" | "ready_to_upload" | "uploading" | "uploaded" | "failed",
    errorMessage: "" | null,
    originalByteSize: 12500000,
    compressedByteSize: 4100000 | null,
    mimeType: "image/jpeg" | null,
    width: 1600 | null,
    height: 1200 | null,
    attachmentId: "att_xxx" | null,
    contentUrl: "/api/conversations/..." | null
}
```

## 클라이언트 함수 상세 설계

### `static/app.js` 에 추가할 DOM 참조

`elements` 객체에 아래 속성을 추가한다.

1. `attachmentInput`
2. `attachmentButton`
3. `composerAttachmentList`
4. `composerDropzone`

### 반드시 추가할 함수 목록

함수명은 아래 이름을 그대로 사용하는 것을 권장한다.

1. `openAttachmentPicker()`
2. `handleAttachmentInputChange(event)`
3. `handleComposerDragOver(event)`
4. `handleComposerDragEnter(event)`
5. `handleComposerDragLeave(event)`
6. `handleComposerDrop(event)`
7. `queueSelectedFiles(fileList)`
8. `validateSelectedImageFiles(files)`
9. `ensureSessionBeforeAttachmentUpload()`
10. `compressQueuedAttachments()`
11. `compressImageFile(file)`
12. `loadImageBitmapFromFile(file)`
13. `drawImageToCanvas(bitmap, dimensionLimit)`
14. `encodeCanvasToJpeg(canvas, quality)`
15. `uploadReadyAttachments(sessionId)`
16. `uploadAttachmentItem(sessionId, attachmentItem)`
17. `removeComposerAttachment(localId)`
18. `retryComposerAttachment(localId)`
19. `renderComposerAttachments()`
20. `buildUploadedAttachmentPayload()`
21. `cleanupComposerAttachmentObjectUrls()`
22. `clearComposerAttachments()`
23. `summarizeMessageForSidebar(message)`

### 파일 선택 처리 규칙

`queueSelectedFiles(fileList)` 는 아래 순서로 동작한다.

1. `FileList` 를 배열로 변환한다.
2. 현재 `state.composerAttachments.length` 와 합쳐서 총 개수가 5개를 초과하면 전체를 거절한다.
3. 각 파일에 대해 `type.startsWith("image/")` 검사를 한다.
4. 각 파일에 대해 `file.size <= 15MB` 검사를 한다.
5. 통과한 파일만 `state.composerAttachments` 에 `queued` 상태로 넣는다.
6. 활성 세션이 없으면 `ensureConversationSession()` 을 호출한다.
7. 순차 압축을 시작한다.

권장 실패 메시지는 아래와 같다.

1. 개수 초과: `이미지는 한 번에 최대 5개까지 첨부할 수 있습니다.`
2. 형식 오류: `이미지 파일만 첨부할 수 있습니다.`
3. 원본 크기 초과: `이미지 1개당 15MB 이하만 첨부할 수 있습니다.`

### 클라이언트 압축 알고리즘

압축 알고리즘은 아래 순서로 구현한다.

1. 입력 `File` 을 이미지로 디코드한다.
2. EXIF orientation 이 존재하면 캔버스에 그리기 전에 회전/반전을 적용한다.
3. 긴 변 기준으로 `INITIAL_IMAGE_MAX_DIMENSION` 이하가 되도록 캔버스에 리사이즈한다.
4. 캔버스를 JPEG 로 인코딩한다.
5. 결과가 5MB 이하이면 성공 처리한다.
6. 5MB 초과이면 JPEG 품질을 `0.86 -> 0.78 -> 0.70 -> 0.62 -> 0.55` 순으로 낮추며 재시도한다.
7. 그래도 5MB 초과이면 해상도 한도를 `1600px` 로 낮추고 다시 같은 품질 루프를 돈다.
8. 그래도 실패하면 해당 파일은 실패 처리한다.

압축 출력 형식은 모두 `image/jpeg` 로 고정한다.

이 선택을 고정하는 이유는 아래와 같다.

1. 구현이 단순하다.
2. 사진 이미지에서 압축률이 높다.
3. 서버와 LLM 전달 포맷이 일관된다.

부작용은 아래와 같다.

1. 원본 PNG 투명 배경은 흰색 배경으로 평탄화된다.
2. 파일 확장자와 MIME 이 `jpg/jpeg` 로 통일된다.

투명 PNG 처리 규칙은 아래처럼 고정한다.

1. 캔버스 배경을 흰색으로 채운 뒤 이미지를 그린다.
2. JPEG 로 내보낸다.

모바일 EXIF 처리 규칙은 아래처럼 고정한다.

1. 아이폰/모바일 사진의 orientation 메타데이터를 읽는다.
2. orientation 값에 맞는 회전/반전 변환을 캔버스에 적용한다.
3. 보정된 결과를 기준으로 리사이즈와 JPEG 인코딩을 수행한다.
4. EXIF 메타데이터는 압축 결과물에 유지하지 않는다.

### 압축 함수 의사코드

```javascript
async function compressImageFile(file) {
    const bitmap = await loadImageBitmapFromFile(file);

    for (const dimensionLimit of [2048, 1600]) {
        const canvas = drawImageToCanvas(bitmap, dimensionLimit);

        for (let quality = 0.86; quality >= 0.55; quality -= 0.08) {
            const blob = await encodeCanvasToJpeg(canvas, quality);
            if (!blob) {
                continue;
            }

            if (blob.size <= TARGET_COMPRESSED_IMAGE_BYTES) {
                return new File(
                    [blob],
                    replaceFileExtensionWithJpg(file.name),
                    { type: "image/jpeg", lastModified: Date.now() },
                );
            }
        }
    }

    throw new Error("이미지를 5MB 이하로 압축하지 못했습니다.");
}
```

### 업로드 호출 규칙

압축 성공 후 업로드는 파일별 개별 요청으로 전송한다. 한 요청에 여러 파일을 보내지 않는다.

이유는 아래와 같다.

1. 실패한 파일만 재시도하기 쉽다.
2. UI 상태가 단순하다.
3. 서버 에러를 파일별로 표시하기 쉽다.

업로드 시점에 반드시 활성 세션 ID 를 포함한다.

## 업로드 API 상세 설계

### 엔드포인트 1. 이미지 업로드

경로:

```text
POST /api/uploads/images
```

요청 형식:

`multipart/form-data`

필드:

1. `file`: 압축된 이미지 바이너리
2. `conversationId`: 업로드가 속할 대화 ID
3. `credentialEnvelope`: 현재 브라우저의 credential envelope

응답 형식:

```json
{
  "attachment": {
    "id": "att_xxx",
    "conversationId": "conv_xxx",
    "fileName": "IMG_1001.jpg",
    "mimeType": "image/jpeg",
    "byteSize": 4123123,
    "width": 1600,
    "height": 1200,
    "createdAt": 1770000000.0,
    "contentUrl": "/api/conversations/conv_xxx/attachments/att_xxx/content?token=..."
  }
}
```

### 엔드포인트 2. 업로드 첨부 삭제

경로:

```text
DELETE /api/uploads/images/{attachment_id}
```

요청 body:

```json
{
  "conversationId": "conv_xxx",
  "credentialEnvelope": "..."
}
```

응답 형식:

```json
{
  "deleted": true
}
```

### 엔드포인트 3. 첨부 이미지 콘텐츠 제공

경로:

```text
GET /api/conversations/{conversation_id}/attachments/{attachment_id}/content?token=...
```

이 엔드포인트는 아래 상황에 필요하다.

1. 업로드 직후 첨부 썸네일 표시
2. 대화 복원 후 이전 첨부 이미지 재표시
3. 이미지 메시지 렌더링

## 업로드 API 보안 규칙

### 인증 범위

업로드와 삭제와 콘텐츠 제공은 모두 현재 브라우저 세션과 대화 소유권을 확인해야 한다.

업로드/삭제에서는 기존 `credentialEnvelope` 기반 인증 해석을 재사용한다. 콘텐츠 제공은 이미지 태그 요청이라 커스텀 헤더를 보내기 어렵기 때문에 signed URL 방식을 사용한다.

인증 실패 규칙은 아래처럼 고정한다.

1. `POST /api/uploads/images` 는 `credentialEnvelope` 가 없거나 만료되면 즉시 401 로 거절한다.
2. `DELETE /api/uploads/images/{attachment_id}` 도 `credentialEnvelope` 가 없거나 만료되면 즉시 401 로 거절한다.
3. 이 경우 오류 코드는 기존 채팅 API 와 동일하게 `copilot_login_required` 또는 세션 해석 과정에서 발생하는 기존 auth error code 를 재사용한다.
4. 익명 세션 상태에서는 업로드 성공이나 첨부 삭제를 허용하지 않는다.

이 규칙을 두는 이유는 아래와 같다.

1. Copilot 자격이 없는 상태에서 서버 디스크에 orphan 업로드가 쌓이는 것을 방지한다.
2. 채팅 전송 API 와 인증 계약을 일관되게 유지한다.

### signed URL 규칙

`contentUrl` 은 DB 에 저장하지 않고 응답 시마다 동적으로 생성한다.

토큰 규칙:

1. 토큰에는 `attachment_id`, `conversation_id`, `exp` 를 담는다.
2. HMAC 서명은 현재 브라우저의 `session_secret` 을 사용한다.
3. 유효 시간은 10분으로 한다.
4. 이미지 요청 시 서버는 쿠키의 session_secret 과 token 둘 다 검증한다.

이 구조의 의미는 아래와 같다.

1. URL 이 유출되어도 다른 브라우저 세션에서 재사용하기 어렵다.
2. 이미지 `<img src>` 로 직접 접근할 수 있다.
3. credential envelope 를 query string 에 실을 필요가 없다.

signed URL 재발급 규칙은 아래처럼 고정한다.

1. `contentUrl` 은 영속 값이 아니라 단기 표시용 값이다.
2. 서버가 conversation state 또는 message payload 를 반환할 때마다 각 attachment 의 새 `contentUrl` 을 다시 생성해 내려준다.
3. 프론트는 기존에 캐시한 `contentUrl` 을 장기 보관하지 않고, 서버 payload 로 받은 최신 값으로 항상 덮어쓴다.
4. 이미지 렌더 중 401 또는 만료로 인한 로드 실패가 발생하면 프론트는 조용히 conversation state 를 재조회해 최신 `contentUrl` 을 받아 다시 렌더한다.

이 규칙을 문서에 고정하는 이유는 아래와 같다.

1. 10분 이상 열린 세션에서도 복원 이미지가 깨지지 않는다.
2. 구현자가 `contentUrl` 을 DB 필드처럼 오해하지 않게 된다.

## 서버 저장 구조 상세 설계

### 디스크 저장 경로

이미지 저장 루트 디렉터리는 아래 상수로 고정한다.

```text
<repo_root>/.uploads/images/
```

권장 실제 저장 경로 형식:

```text
.uploads/images/2026/05/12/att_xxx.jpg
```

경로 구성 규칙:

1. 파일명은 사용자 원본 이름을 쓰지 않는다.
2. 파일명은 무조건 attachment ID 기반으로 생성한다.
3. 확장자는 저장 MIME 에 맞춰 `.jpg` 로 고정한다.

### 서버에 저장하는 것은 압축본만이다.

서버는 클라이언트가 보낸 압축본만 디스크에 저장한다. 원본을 따로 저장하지 않는다.

## 데이터베이스 스키마 상세 설계

현재 대화 기록 DB 는 `services/conversation_service.py` 내부 `ChatHistoryRepository` 가 관리한다. 첨부 메타데이터도 같은 SQLite DB 에 둔다.

### 추가 테이블

`conversation_attachments` 테이블을 추가한다.

```sql
CREATE TABLE IF NOT EXISTS conversation_attachments (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT,
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(message_id) REFERENCES conversation_messages(id) ON DELETE SET NULL
)
```

추가 인덱스:

```sql
CREATE INDEX IF NOT EXISTS idx_conversation_attachments_conversation
ON conversation_attachments (conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_conversation_attachments_message
ON conversation_attachments (message_id, created_at);

CREATE INDEX IF NOT EXISTS idx_conversation_attachments_scope_status
ON conversation_attachments (scope_id, status, created_at);
```

### 상태 값 규칙

`conversation_attachments.status` 는 아래 세 값만 허용한다.

1. `uploaded`
2. `attached`
3. `deleted`

실제 삭제는 디스크 삭제 후 레코드 삭제를 원칙으로 하므로, `deleted` 상태는 필요 시 중간 디버깅용으로만 잠깐 쓸 수 있다. 최종 구현에서는 바로 삭제해도 된다.

권장 방식은 아래다.

1. 정상 업로드 직후 `uploaded`
2. 메시지 전송 성공 전 `uploaded`
3. `begin_turn` 에서 메시지와 연결되면 `attached`
4. 사용자가 제거하거나 TTL cleanup 되면 레코드 삭제

## 서버 서비스 구조 상세 설계

### 새 파일 `services/image_attachment_service.py`

이 파일을 새로 추가한다.

책임은 아래로 고정한다.

1. 업로드 파일 검증
2. 이미지 디코드 검증
3. JPEG 저장
4. 디스크 경로 생성
5. 콘텐츠 URL 생성
6. signed token 검증
7. 첨부 파일 읽기
8. 디스크 파일 삭제

이 서비스는 DB 쓰기를 직접 하지 않고, 메타데이터 저장과 메시지 연결은 `ConversationService` 를 통해 수행한다.

### `ConversationService` 에 추가할 책임

1. 첨부 메타데이터 레코드 생성
2. 업로드된 첨부를 메시지와 연결
3. 대화 상태 payload 에 attachments 포함
4. 만료 대화 정리 시 첨부 파일 경로 목록 반환 또는 직접 삭제 호출

### `CopilotChatService` 에 추가할 책임

1. 현재 턴의 첨부 이미지들을 LiteLLM 멀티모달 메시지로 변환
2. 과거 첨부 이미지는 텍스트 요약으로 변환
3. 선택 모델이 vision 입력을 지원하는지 검사

## `main.py` 상세 변경 설계

### 추가 import

`main.py` 에 아래 import 가 추가된다.

1. `UploadFile`
2. `File`
3. `Form`
4. `services.image_attachment_service.ImageAttachmentService`

### 새 request model

현재 `ConversationMessageRequest` 는 `content: str` 만 받는다. 이를 아래처럼 변경한다.

```python
class ConversationAttachmentRef(BaseModel):
    id: str


class ConversationMessageRequest(BaseModel):
    content: str = ""
    attachments: list[ConversationAttachmentRef] = []
    model: str | None = None
    credentialEnvelope: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None
```

중요 규칙:

1. `content` 는 빈 문자열 허용
2. `attachments` 는 비어 있을 수 있음
3. 둘 다 비어 있으면 거절

### 새 endpoint

아래 세 endpoint 를 추가한다.

1. `POST /api/uploads/images`
2. `DELETE /api/uploads/images/{attachment_id}`
3. `GET /api/conversations/{conversation_id}/attachments/{attachment_id}/content`

### 기존 메시지 전송 endpoint 수정

`POST /api/conversations/{conversation_id}/messages` 는 아래 순서로 바뀐다.

1. `content` 와 `attachments` 를 함께 검증한다.
2. 첨부 ID 목록을 추출한다.
3. 첨부 ID 가 1개 이상이면 provider model 기준으로 vision 지원 여부를 선검사한다.
4. vision 미지원이면 `chat_model_not_vision_capable` 오류를 즉시 반환하고 DB 를 변경하지 않는다.
5. `conversation_service.begin_turn(..., content=..., attachment_ids=...)` 호출
6. 반환된 turn 객체를 `chat_service.stream_chat_completion()` 으로 전달

## `ConversationService` 상세 변경 설계

### `validate_message_content` 교체

현재 함수:

```python
def validate_message_content(self, content: str) -> str:
```

아래 함수로 교체한다.

```python
def validate_message_input(self, content: Any, attachments: Any) -> tuple[str, list[str]]:
```

검증 규칙:

1. `content` 가 문자열이 아니면 400
2. `attachments` 가 리스트가 아니면 400
3. `content.strip()` 결과와 첨부 개수가 모두 비어 있으면 400
4. 첨부 ID 개수는 1 이상 5 이하만 허용
5. 각 첨부 ref 는 `{id: <non-empty-string>}` 이어야 함

권장 오류 코드와 메시지:

1. `conversation_message_required`: `보낼 메시지 또는 이미지를 추가하세요.`
2. `conversation_attachments_invalid`: `첨부 이미지 형식이 올바르지 않습니다.`
3. `conversation_attachments_limit_exceeded`: `이미지는 한 번에 최대 5개까지 전송할 수 있습니다.`

### `ConversationTurn` 구조 변경

현재는 `visible_messages` 안에 바로 새 user 메시지를 넣어서 반환한다. 첨부 최적화와 과거 history 처리 방식을 위해 아래 구조로 바꾼다.

```python
@dataclass(slots=True)
class ConversationTurn:
    model: str
    prior_messages: list[dict[str, Any]]
    new_user_message: dict[str, Any]
    assistant_message_id: str
```

규칙:

1. `prior_messages` 는 현재 턴 직전까지의 visible history 를 담는다.
2. `new_user_message` 는 이번 턴의 새 사용자 메시지와 첨부 정보를 담는다.
3. `CopilotChatService` 는 `prior_messages` 와 `new_user_message` 를 조합해 provider messages 를 만든다.

### `begin_turn` 변경 규칙

함수 시그니처를 아래처럼 바꾼다.

```python
def begin_turn(
    self,
    scope_id: str,
    conversation_id: str,
    *,
    content: str,
    attachment_ids: list[str],
    model: str | None,
) -> ConversationTurn:
```

동작 순서:

1. 대화 존재 확인
2. 기존 빈 assistant streaming 메시지 제거
3. `attachment_ids` 가 모두 현재 `scope_id` 와 `conversation_id` 에 속하는지 확인
4. `message_id IS NULL` 상태인 첨부만 허용
5. 새 user message row 생성
6. 새 assistant placeholder row 생성
7. 해당 attachment rows 의 `message_id` 를 새 user message ID 로 업데이트
8. 해당 attachment rows 의 `status` 를 `attached` 로 변경
9. 제목 갱신 시 텍스트가 없고 첨부만 있으면 `이미지 N개` 형식으로 제목 후보 생성
10. `ConversationTurn` 반환

### message payload 구조 변경

UI 복원을 위해 `_message_row_to_payload()` 결과에 `attachments` 배열을 포함해야 한다.

최종 payload 예시는 아래와 같다.

```json
{
  "id": "msg_xxx",
  "role": "user",
  "content": "",
  "attachments": [
    {
      "id": "att_xxx",
      "fileName": "IMG_1001.jpg",
      "mimeType": "image/jpeg",
      "byteSize": 4123123,
      "width": 1600,
      "height": 1200,
      "contentUrl": "/api/conversations/conv_xxx/attachments/att_xxx/content?token=..."
    }
  ],
  "status": "complete",
  "createdAt": 1770000000.0,
  "updatedAt": 1770000000.0
}
```

### history payload 조립 규칙

`get_state_payload()` 와 `get_conversation_payload()` 시점에 attachments 를 함께 로드해서 messages 에 병합한다.

권장 메서드 추가:

1. `_load_attachments_by_message_ids()`
2. `_attachment_row_to_payload()`
3. `_build_attachment_content_url()`

## `ImageAttachmentService` 상세 설계

### 추가 의존성

`requirements.txt` 에 아래 패키지를 추가한다.

1. `python-multipart`
2. `Pillow`

### 서비스 상수

```python
MAX_UPLOADED_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg"}
UPLOADS_ROOT = BASE_DIR / ".uploads" / "images"
ATTACHMENT_URL_TTL_SECONDS = 600
```

서버는 업로드본 기준으로 5MB 제한을 다시 확인한다. 클라이언트가 실패 없이 5MB 근처로 맞췄더라도 서버에서 반드시 재검증한다.

### 업로드 처리 순서

1. multipart `file` 유무 검사
2. 파일 크기 검사
3. `conversationId` 와 소유권 검사
4. `Pillow` 로 실제 이미지 디코드 검사
5. JPEG 로 재저장 가능한지 확인
6. 디렉터리 생성
7. attachment ID 생성
8. 디스크 저장
9. 실제 width, height, byte_size 계산
10. ConversationService 를 통해 metadata row 저장
11. signed content URL 생성
12. 응답 반환

### 서버가 업로드본을 다시 JPEG 로 저장할지 여부

이번 설계에서는 서버가 업로드된 JPEG 를 그대로 저장하는 것을 기본으로 한다. 다만 MIME 검증과 실제 디코드를 위해 Pillow 를 통과시킨다.

정책:

1. 클라이언트 출력이 `image/jpeg` 가 아니면 서버는 거절한다.
2. 실제 디코드가 안 되면 거절한다.
3. 클라이언트는 반드시 JPEG 압축본만 업로드한다.

즉, 서버 저장 포맷은 `image/jpeg` 한 종류만 지원한다.

이 선택을 고정하는 이유는 아래와 같다.

1. 처리 경로가 단순해진다.
2. 서버와 LLM 전송 포맷이 일치한다.
3. 확장자와 MIME 이 고정되어 cleanup 이 단순해진다.

### 첨부 삭제 처리 순서

1. attachment row 조회
2. 현재 scope 와 conversation 소유권 확인
3. `message_id IS NULL` 인 업로드 대기 첨부만 삭제 허용
4. 디스크 파일 삭제
5. metadata row 삭제

메시지에 이미 연결된 첨부는 삭제 API 로 제거하지 않는다. 메시지 일부를 사후 수정하는 기능은 이번 범위 밖이다.

### 콘텐츠 제공 처리 순서

1. signed token 검증
2. 현재 브라우저 session cookie 확인
3. attachment row 조회
4. `conversation_id` 와 `attachment_id` 일치 검증
5. 파일 존재 확인
6. `FileResponse` 또는 바이너리 응답 반환

응답 헤더 권장값:

1. `Content-Type: image/jpeg`
2. `Cache-Control: private, max-age=300`

## `CopilotChatService` 상세 설계

### 모델 vision 지원 체크

LLM 호출 전에 선택 모델이 vision 입력을 지원하는지 확인한다.

규칙:

1. 현재 턴의 새 사용자 메시지에 첨부가 없으면 vision 체크를 건너뛴다.
2. 첨부가 있으면 provider model 기준으로 `litellm.supports_vision(model=provider_model)` 를 우선 시도한다.
3. 영구 저장 경로인 `POST /api/conversations/{conversation_id}/messages` 에서는 이 검사를 `begin_turn()` 전에 먼저 수행한다.
4. `CopilotChatService` 내부 검사는 방어적 이중 체크로 유지한다.
5. 지원하지 않으면 `CopilotChatRequestError(code="chat_model_not_vision_capable", message="선택한 모델은 이미지 첨부를 지원하지 않습니다. 다른 모델을 선택하세요.")` 를 발생시킨다.

### 멀티모달 메시지 조립 규칙

새로 추가할 권장 메서드:

1. `build_provider_messages(prior_messages, new_user_message)`
2. `_build_history_message_content(message)`
3. `_build_current_user_message_content(message)`
4. `_attachment_to_data_url(attachment)`
5. `_build_attachment_summary_text(attachments)`

### provider history 조립 규칙

`prior_messages` 처리 규칙은 아래와 같다.

1. assistant 메시지는 기존처럼 문자열 `content` 만 쓴다.
2. user 메시지 중 attachments 가 없는 경우 기존처럼 문자열 `content` 만 쓴다.
3. user 메시지 중 attachments 가 있는 경우 아래 텍스트를 합쳐 문자열 content 로 만든다.

예시:

```text
이전에 보낸 설명 텍스트

[사용자가 이미지 2개를 첨부함: IMG_1001.jpg, IMG_1002.jpg]
```

### 현재 턴 user 메시지 조립 규칙

`new_user_message` 는 아래 형식으로 조립한다.

1. 텍스트가 있으면 첫 번째 content part 로 `{type: "text", text: <content>}` 추가
2. 첨부 이미지마다 `{type: "image_url", image_url: {url: <data-url>}}` 추가
3. 텍스트가 없고 첨부만 있어도 유효하다

예시:

```python
{
    "role": "user",
    "content": [
        {"type": "text", "text": "이 이미지 설명해줘"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
    ],
}
```

### data URL 생성 규칙

1. 디스크에서 JPEG 바이너리를 읽는다.
2. base64 인코딩한다.
3. 아래 형식으로 만든다.

```text
data:image/jpeg;base64,<base64-encoded-bytes>
```

## 현재 코드와 연결되는 상세 구현 순서

### 1단계. 의존성 추가

`requirements.txt` 에 아래 두 줄을 추가한다.

```text
python-multipart
Pillow
```

### 2단계. 업로드 서비스 추가

`services/image_attachment_service.py` 를 생성한다.

필수 public 메서드 권장안:

1. `store_uploaded_image(...)`
2. `delete_uploaded_image(...)`
3. `open_attachment_file(...)`
4. `build_attachment_content_url(...)`
5. `validate_attachment_content_token(...)`
6. `delete_file_if_exists(path)`
7. `attachment_to_data_url(path)`

### 3단계. DB 스키마와 repository 메서드 추가

`services/conversation_service.py` 의 `_initialize()` 에 attachments table 과 index 를 추가한다.

필수 repository 메서드 권장안:

1. `create_attachment_record(...)`
2. `get_pending_attachment(...)`
3. `get_pending_attachments(...)`
4. `delete_pending_attachment(...)`
5. `attach_attachments_to_message(...)`
6. `_load_attachments_by_message_ids(...)`
7. `_build_message_payloads_with_attachments(...)`
8. `list_expired_attachment_paths(...)`

### 4단계. 메시지 입력 검증 변경

`validate_message_content()` 를 `validate_message_input()` 로 교체한다.

### 5단계. `begin_turn()` 변경

`attachment_ids` 를 받도록 바꾸고, row 연결 로직을 넣는다.

### 6단계. `main.py` 업로드 endpoint 추가

업로드, 삭제, 콘텐츠 제공 endpoint 를 추가한다.

### 7단계. `main.py` 메시지 endpoint 수정

`ConversationMessageRequest` 를 확장하고 `begin_turn()` 호출을 변경한다.

### 8단계. `CopilotChatService` 멀티모달 조립 추가

현재 `stream_chat_completion()` 이 `messages` 를 그대로 provider 로 전달하는 부분을, `build_provider_messages()` 결과를 전달하도록 바꾼다.

### 9단계. `static/index.html` 첨부 UI 추가

첨부 버튼, 숨김 파일 입력, 첨부 리스트, 드롭존을 추가한다.

### 10단계. `static/style.css` 스타일 추가

아래 요소에 대한 스타일을 추가한다.

1. `.composer-dropzone`
2. `.composer-dropzone.dragover`
3. `.composer-attachment-list`
4. `.composer-attachment-item`
5. `.composer-attachment-thumb`
6. `.composer-attachment-meta`
7. `.composer-attachment-remove`
8. `.composer-attachment-retry`
9. `.message-attachments`
10. `.message-attachment-image`

### 11단계. `static/app.js` 첨부 상태 관리 추가

기존 `sendPrompt()` 와 `renderMessages()` 를 중심으로 상태를 확장한다.

반드시 수정할 기존 함수:

1. `normalizeServerMessage()`
2. `updateSessionTitle()`
3. `renderSidebar()`
4. `renderMessages()`
5. `createMessageElement()`
6. `sendPrompt()`
7. `updateComposerControls()`
8. `bindEvents()`

## 프론트 기존 함수 변경 상세

### `normalizeServerMessage()`

현재 `content` 문자열만 다룬다. `attachments` 를 추가한다.

권장 구조:

```javascript
function normalizeServerMessage(message) {
    return {
        id: typeof message?.id === "string" && message.id ? message.id : createId("message"),
        role: typeof message?.role === "string" && message.role ? message.role : "assistant",
        content: typeof message?.content === "string" ? message.content : "",
        attachments: Array.isArray(message?.attachments) ? message.attachments.map(normalizeServerAttachment) : [],
        status: typeof message?.status === "string" && message.status ? message.status : "complete",
        createdAt: Number.isFinite(Number(message?.createdAt)) ? Number(message.createdAt) : Date.now(),
        updatedAt: Number.isFinite(Number(message?.updatedAt)) ? Number(message.updatedAt) : Date.now(),
    };
}
```

### `updateSessionTitle()`

제목 생성 규칙:

1. 첫 user 메시지에 텍스트가 있으면 기존처럼 텍스트 우선
2. 텍스트가 없고 attachments 가 있으면 `이미지 N개`
3. 둘 다 없으면 `새 대화`

### `renderSidebar()`

마지막 메시지 preview 생성 규칙:

1. 텍스트가 있으면 텍스트 preview
2. 텍스트가 없고 attachments 가 있으면 `이미지 N개`
3. 둘 다 없으면 `대화를 시작해 보세요.`

### `createMessageElement()`

렌더링 순서는 아래로 고정한다.

1. 첨부 이미지 grid
2. 본문 텍스트 markdown
3. assistant pending 표시

첨부만 있는 user 메시지도 빈 bubble 로 보이지 않게 해야 한다.

### `sendPrompt()`

핵심 변경 규칙:

1. `content.trim()` 과 업로드 완료 첨부 개수를 함께 본다.
2. 둘 다 없으면 return
3. 업로드 중 첨부가 있으면 return
4. 요청 body 에 `attachments` 추가
5. optimistic user message 에도 attachments 메타데이터를 넣는다.
6. 전송 성공 후 composer attachments 를 비운다.
7. 전송 실패 시 optimistic attachment 상태를 state sync 로 복구한다.

요청 body 예시:

```json
{
  "content": "",
  "attachments": [
    {"id": "att_x1"},
    {"id": "att_x2"}
  ],
  "model": "gpt-5.4",
  "credentialEnvelope": "...",
  "tools": [...],
  "tool_choice": "auto",
  "parallel_tool_calls": false
}
```

## LLM 호출 payload 상세 예시

### 경우 1. 텍스트와 이미지 2개를 함께 보낸 첫 턴

provider 에 전달되는 messages 예시:

```json
[
  {
    "role": "user",
    "content": [
      {"type": "text", "text": "이 이미지들 차이를 설명해줘"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]
  }
]
```

### 경우 2. 다음 턴에서 텍스트 질문만 이어가는 경우

이전 첨부 이미지는 다시 base64 로 넣지 않고 요약 문자열로 보낸다.

```json
[
  {
    "role": "user",
    "content": "이 이미지들 차이를 설명해줘\n\n[사용자가 이미지 2개를 첨부함: IMG_1001.jpg, IMG_1002.jpg]"
  },
  {
    "role": "assistant",
    "content": "두 이미지는 ..."
  },
  {
    "role": "user",
    "content": "그럼 두 번째 이미지의 핵심 문제만 다시 말해줘"
  }
]
```

## TTL cleanup 상세 설계

현재 대화 정리는 `ConversationService` 내부 opportunistic cleanup 흐름으로 수행된다. 첨부도 같은 경로에서 정리한다.

정리 규칙:

1. 만료 대화를 조회한다.
2. 만료 대화에 속한 첨부들의 `storage_path` 목록을 구한다.
3. 파일 삭제를 먼저 시도한다.
4. 이후 대화 row 삭제를 수행한다.
5. 파일이 이미 없는 경우는 무시한다.

이 구현에서 중요한 점은 아래와 같다.

1. orphan file 이 남지 않아야 한다.
2. cleanup 실패가 사용자 요청 전체를 실패시키면 안 된다.
3. 파일 삭제 예외는 warning 로그만 남기고 나머지 cleanup 은 계속한다.

## 오류 코드 상세 설계

권장 오류 코드와 사용자 메시지는 아래로 고정한다.

1. `attachment_upload_required`: `업로드할 이미지가 없습니다.`
2. `attachment_image_only`: `이미지 파일만 첨부할 수 있습니다.`
3. `attachment_file_too_large`: `이미지 1개당 5MB 이하 업로드만 허용됩니다.`
4. `attachment_decode_failed`: `이미지 파일을 처리할 수 없습니다. 다른 파일을 선택하세요.`
5. `attachment_not_found`: `첨부 이미지를 찾을 수 없습니다. 다시 업로드하세요.`
6. `attachment_access_denied`: `이 이미지에 접근할 수 없습니다.`
7. `attachment_already_attached`: `이미 전송에 사용된 첨부 이미지입니다. 다시 업로드하세요.`
8. `chat_model_not_vision_capable`: `선택한 모델은 이미지 첨부를 지원하지 않습니다. 다른 모델을 선택하세요.`
9. `conversation_message_required`: `보낼 메시지 또는 이미지를 추가하세요.`
10. `conversation_attachments_limit_exceeded`: `이미지는 한 번에 최대 5개까지 전송할 수 있습니다.`

## 테스트 계획

### 서버 테스트

새 테스트 파일 권장안:

1. `test_scripts/test_image_upload_api.py`
2. `test_scripts/test_message_attachments.py`
3. `test_scripts/test_multimodal_chat_payload.py`
4. `test_scripts/test_attachment_ttl_cleanup.py`

테스트해야 할 시나리오:

1. JPEG 업로드 성공
2. 비이미지 업로드 거절
3. 5MB 초과 업로드 거절
4. 다른 conversation 의 첨부 삭제 거절
5. pending attachment 를 메시지에 정상 연결
6. 첨부만 있는 메시지 전송 성공
7. 첨부가 있는 경우 vision 미지원 모델 거절
8. 새 턴 이미지가 data URL 로 provider payload 에 포함되는지 확인
9. 과거 이미지가 summary text 로만 history 에 포함되는지 확인
10. TTL cleanup 시 디스크 파일이 같이 삭제되는지 확인

### 수동 QA 체크리스트

1. 데스크톱에서 이미지 1개 선택 후 압축과 업로드가 되는지
2. 데스크톱에서 이미지 5개 드래그 앤 드롭이 되는지
3. 6개 드롭 시 전체가 거절되는지
4. 15MB 초과 원본 선택 시 클라이언트에서 거절되는지
5. 텍스트 없이 이미지 1개만 보내기가 되는지
6. 이미지 2개와 텍스트를 함께 보내기가 되는지
7. 업로드 후 전송 전에 삭제가 되는지
8. 새로고침 후 대화 복원 시 이미지가 다시 보이는지
9. 스트리밍 중에는 첨부 상태와 입력 제어가 깨지지 않는지
10. 7일 지난 대화 cleanup 후 디스크 파일도 삭제되는지

## 구현 순서 체크리스트

아래 순서로 구현하면 충돌이 가장 적다.

1. `requirements.txt` 에 의존성 추가
2. `services/image_attachment_service.py` 추가
3. `services/conversation_service.py` 에 attachment table 과 repository 메서드 추가
4. `main.py` 업로드/삭제/콘텐츠 endpoint 추가
5. `main.py` 메시지 request model 확장
6. `ConversationService.validate_message_input()` 와 `begin_turn(... attachment_ids)` 구현
7. `CopilotChatService.build_provider_messages()` 구현
8. `static/index.html` composer 첨부 UI 추가
9. `static/style.css` 첨부 관련 스타일 추가
10. `static/app.js` 파일 선택, 압축, 업로드, 삭제, 전송 상태 추가
11. 대화 복원 UI 렌더링 수정
12. 테스트 추가

## 구현 시 주의사항

1. 이미지 콘텐츠 제공 URL 은 DB 에 저장하지 말고 응답 시 생성한다.
2. `credentialEnvelope` 를 query string 에 노출하지 않는다.
3. 서버 디스크 경로 문자열을 프론트에 직접 노출하지 않는다.
4. 이전 이미지 첨부를 매 턴마다 base64 로 재전송하지 않는다.
5. 업로드는 성공했지만 메시지 전송 전에 사용자가 제거한 파일은 즉시 삭제한다.
6. 업로드는 완료됐지만 메시지로 연결되지 않은 첨부도 대화 TTL cleanup 으로 정리되어야 한다.
7. `sendPrompt()` 는 기존 텍스트 전용 흐름을 깨지 않도록 최소 변경으로 확장한다.
8. SSE 응답 계약은 바꾸지 않는다.
9. assistant 메시지 저장 구조는 그대로 유지한다.
10. user 메시지의 `content` 와 `attachments` 는 동시에 비어 있을 수 없다.

## 구현 완료 기준

아래 조건을 모두 만족하면 이번 기능 구현은 완료로 본다.

1. 사용자가 데스크톱과 모바일에서 이미지를 첨부할 수 있다.
2. 클라이언트가 업로드 전에 이미지를 5MB 이하 목표로 압축한다.
3. 업로드 API 가 채팅 API 와 분리되어 있다.
4. 서버는 첨부를 디스크에 저장하고 메타데이터를 SQLite 에 저장한다.
5. 텍스트 없이 이미지 메시지 전송이 가능하다.
6. 대화 복원 후 이전 이미지가 화면에 다시 렌더링된다.
7. 현재 턴의 이미지는 LiteLLM 멀티모달 요청으로 전달된다.
8. 이전 턴의 이미지는 요약 텍스트로만 history 에 포함된다.
9. 7일 TTL cleanup 시 첨부 디스크 파일도 같이 삭제된다.
10. 기존 텍스트 채팅, SSE 스트리밍, 대화 제목/삭제/복원 기능이 유지된다.
