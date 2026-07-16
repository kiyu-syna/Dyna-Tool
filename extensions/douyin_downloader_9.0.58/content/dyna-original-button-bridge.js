(() => {
  "use strict";

  const REQUEST_EVENT = "dyna:original-download-request";
  const ORIGINAL_BUTTON_SELECTOR = ".dy-download-button";
  const DYNA_EXTENSION_ID = "ibdfeimkglcmdejppabkaidpippniiob";
  const DYNA_LOGO_URL = `chrome-extension://${DYNA_EXTENSION_ID}/assets/dyna-button-logo.png`;
  const hookedButtons = new WeakSet();

  function normalizeUrl(value) {
    const url = String(value || "").trim();
    if (url.startsWith("//")) return `https:${url}`;
    return /^https?:\/\//i.test(url) ? url : "";
  }

  function baseVideoId(value) {
    return String(value || "").replace(/_modal$/, "").trim();
  }

  function sourceUrlFor(videoId) {
    try {
      const url = new URL(location.href);
      const currentId = url.searchParams.get("modal_id")
        || url.searchParams.get("aweme_id")
        || url.pathname.match(/\/video\/(\d+)/)?.[1]
        || "";
      if (!currentId && videoId) url.searchParams.set("modal_id", videoId);
      return url.href;
    } catch {
      return location.href;
    }
  }

  function originalVideoOptions(button) {
    const videoData = button?.__videoData;
    if (!videoData || typeof videoData.getMediaList !== "function") return [];
    let mediaList;
    try {
      mediaList = videoData.getMediaList();
    } catch {
      return [];
    }
    if (!Array.isArray(mediaList)) return [];
    return mediaList
      .filter((item) => item?.type === "video" && normalizeUrl(item?.url))
      .map((item) => ({
        url: normalizeUrl(item.url),
        filename: String(item.filename || ""),
        resolution: String(item.resolution || item.originalResolution || ""),
        width: Number(item.width || 0),
        height: Number(item.height || 0),
        size: Number(item.size || 0),
        format: String(item.format || "mp4"),
        has_sound: item.hasSound !== false,
      }));
  }

  function decorate(button) {
    const videoData = button?.__videoData;
    const videoId = baseVideoId(videoData?.id);
    const options = originalVideoOptions(button);
    if (!videoId || videoId === "0" || !options.length) return;

    button.dataset.dynaIntegrated = "true";
    button.dataset.dynaVideoId = videoId;
    if (!button.dataset.dynaState) button.title = "Tải và đăng bằng Dyna";
    button.setAttribute("aria-label", "Tải và đăng video bằng Dyna");
    if (!button.querySelector(".dyna-original-logo")) {
      const state = String(button.dataset.dynaState || "");
      const status = state === "success" ? "✓" : state === "error" ? "!" : state === "active" ? "…" : "";
      button.innerHTML = `
        <div class="dy-download-button-inner dyna-original-button-inner">
          <img class="dyna-original-logo" src="${DYNA_LOGO_URL}" alt="" aria-hidden="true">
          <span class="dyna-original-status" aria-hidden="true"${status ? "" : " hidden"}>${status}</span>
        </div>
      `;
    }

    if (hookedButtons.has(button)) return;
    hookedButtons.add(button);
    button.addEventListener("click", (event) => {
      const currentData = button.__videoData;
      const currentVideoId = baseVideoId(currentData?.id || button.dataset.dynaVideoId);
      const currentOptions = originalVideoOptions(button);
      const selectedVideo = currentOptions[0];
      if (!currentVideoId || !selectedVideo) return;

      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      window.postMessage({
        type: REQUEST_EVENT,
        payload: {
          aweme_id: currentVideoId,
          video_id: currentVideoId,
          original_button_id: String(currentData?.id || currentVideoId),
          source_url: sourceUrlFor(currentVideoId),
          description: selectedVideo.filename,
          filename: selectedVideo.filename,
          download_url: selectedVideo.url,
          resolution: selectedVideo.resolution,
          width: selectedVideo.width,
          height: selectedVideo.height,
          data_size: selectedVideo.size,
          has_sound: selectedVideo.has_sound,
          selection_method: "original_extension_getMediaList_highest_resolution",
          available_video_qualities: currentOptions.length,
        },
      }, "*");
    }, true);
  }

  function scan(root = document) {
    if (root instanceof Element && root.matches(ORIGINAL_BUTTON_SELECTOR)) decorate(root);
    for (const button of root.querySelectorAll?.(ORIGINAL_BUTTON_SELECTOR) || []) decorate(button);
  }

  function start() {
    scan();
    const root = document.documentElement;
    if (root) {
      new MutationObserver((mutations) => {
        for (const mutation of mutations) {
          for (const node of mutation.addedNodes) {
            if (node instanceof Element) scan(node);
          }
        }
      }).observe(root, { childList: true, subtree: true });
    }
    setInterval(scan, 1000);
  }

  if (document.documentElement) start();
  else addEventListener("DOMContentLoaded", start, { once: true });
})();
