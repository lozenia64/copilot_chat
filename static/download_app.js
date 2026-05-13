(function () {
    const config = window.DOWNLOAD_APP_CONFIG || {};
    const artifacts = config.artifacts || {};
    const root = document.documentElement;
    let viewportHeightFrame = null;

    function syncViewportHeight() {
        if (viewportHeightFrame !== null) {
            cancelAnimationFrame(viewportHeightFrame);
        }

        viewportHeightFrame = requestAnimationFrame(() => {
            const viewportHeight = window.visualViewport
                ? window.visualViewport.height
                : window.innerHeight;

            root.style.setProperty("--download-app-height", `${Math.round(viewportHeight)}px`);
            viewportHeightFrame = null;
        });
    }

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
    }

    syncViewportHeight();
    window.addEventListener("resize", syncViewportHeight);
    window.addEventListener("orientationchange", syncViewportHeight);
    if (window.visualViewport) {
        window.visualViewport.addEventListener("resize", syncViewportHeight);
    }

    ["apk"].forEach(applyArtifactCard);

    const iosInstallButton = document.getElementById("iosInstallButton");
    const iosInstallDisabled = document.getElementById("iosInstallDisabled");
    const iosInstallMeta = document.getElementById("iosInstallMeta");
    const iosPlistLink = document.getElementById("iosPlistLink");
    const iosIpaLink = document.getElementById("iosIpaLink");
    const plistArtifact = artifacts.plist || {};
    const ipaArtifact = artifacts.ipa || {};
    const hasIosInstall = Boolean(config.iosInstallUrl);
    const hasPlistDownload = Boolean(plistArtifact.available && plistArtifact.downloadUrl);
    const hasIpaDownload = Boolean(ipaArtifact.available && ipaArtifact.downloadUrl);

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

    if (iosPlistLink) {
        iosPlistLink.hidden = !hasPlistDownload;
        if (hasPlistDownload) {
            iosPlistLink.href = plistArtifact.downloadUrl;
        } else {
            iosPlistLink.removeAttribute("href");
        }
    }

    if (iosIpaLink) {
        iosIpaLink.hidden = !hasIpaDownload;
        if (hasIpaDownload) {
            iosIpaLink.href = ipaArtifact.downloadUrl;
        } else {
            iosIpaLink.removeAttribute("href");
        }
    }
})();
