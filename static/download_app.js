(function () {
    const config = window.DOWNLOAD_APP_CONFIG || {};
    const artifacts = config.artifacts || {};

    function applyArtifactCard(slug) {
        const card = document.querySelector(`[data-artifact-card="${slug}"]`);
        if (!card) {
            return;
        }

        const artifact = artifacts[slug] || {};
        const descriptionEl = card.querySelector('[data-role="description"]');
        const downloadEl = card.querySelector('[data-role="download"]');
        const disabledEl = card.querySelector('[data-role="disabled"]');
        const metaEl = card.querySelector('[data-role="meta"]');

        if (descriptionEl) {
            descriptionEl.textContent = artifact.description || "";
        }

        const available = Boolean(artifact.available && artifact.downloadUrl);
        if (downloadEl) {
            downloadEl.hidden = !available;
            if (available) {
                downloadEl.href = artifact.downloadUrl;
                downloadEl.textContent = `${artifact.label || slug} 다운로드`;
            } else {
                downloadEl.removeAttribute("href");
            }
        }

        if (disabledEl) {
            disabledEl.hidden = available;
        }

        if (metaEl) {
            metaEl.textContent = available
                ? `현재 파일: ${artifact.filename || ""}`
                : "서버에 파일이 배치되면 다운로드가 활성화됩니다.";
        }
    }

    ["apk", "plist", "ipa"].forEach(applyArtifactCard);

    const iosInstallButton = document.getElementById("iosInstallButton");
    const iosInstallDisabled = document.getElementById("iosInstallDisabled");
    const iosInstallMeta = document.getElementById("iosInstallMeta");
    const hasIosInstall = Boolean(config.iosInstallUrl);

    if (iosInstallButton) {
        iosInstallButton.hidden = !hasIosInstall;
        if (hasIosInstall) {
            iosInstallButton.href = config.iosInstallUrl;
        } else {
            iosInstallButton.removeAttribute("href");
        }
    }

    if (iosInstallDisabled) {
        iosInstallDisabled.hidden = hasIosInstall;
    }

    if (iosInstallMeta) {
        iosInstallMeta.textContent = hasIosInstall
            ? "Safari에서 열면 iOS 직접 설치 흐름을 시작할 수 있습니다."
            : "manifest 파일이 준비되면 설치 링크가 활성화됩니다.";
    }
})();
