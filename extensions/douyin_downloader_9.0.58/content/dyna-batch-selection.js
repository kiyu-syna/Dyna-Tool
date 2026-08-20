(() => {
  "use strict";

  const ROOT_ID = "dyna-batch-selection";
  const RESULT_LIST_ID = "aix-douyin-download-results";
  const STORAGE_PREFIX = "dyna:selection:";
  const MAX_VIDEOS = 50;
  const decoratedAnchors = new WeakSet();
  const selected = new Map();
  let session = null;
  let sending = false;
  let directStarting = false;
  let scanTimer = null;

  function sendMessage(message) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (!response?.ok) {
          reject(new Error(response?.error || "Dyna extension không phản hồi."));
          return;
        }
        resolve(response);
      });
    });
  }

  function storageGet(key) {
    return new Promise((resolve) => {
      chrome.storage.local.get(key, (value) => resolve(value?.[key]));
    });
  }

  function storageSet(key, value) {
    return new Promise((resolve) => {
      chrome.storage.local.set({ [key]: value }, resolve);
    });
  }

  function storageRemove(key) {
    return new Promise((resolve) => chrome.storage.local.remove(key, resolve));
  }

  function storageKey() {
    return session ? `${STORAGE_PREFIX}${session.id}` : "";
  }

  function videoIdFromHref(value) {
    try {
      const url = new URL(String(value || ""), location.href);
      return String(
        url.pathname.match(/\/video\/([A-Za-z0-9_-]+)/)?.[1]
          || url.searchParams.get("modal_id")
          || url.searchParams.get("aweme_id")
          || "",
      ).replace(/_modal$/, "");
    } catch {
      return "";
    }
  }

  function profilePath(value) {
    try {
      const path = new URL(String(value || ""), location.href).pathname;
      return path.match(/^\/user\/[^/?#]+/)?.[0] || "";
    } catch {
      return "";
    }
  }

  function isExpectedProfile() {
    if (!session) return false;
    const expected = profilePath(session.source_url);
    return Boolean(
      expected
      && (
        profilePath(location.href) === expected
        || currentViewedVideoId()
      )
    );
  }

  function isVisible(element) {
    if (!(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return (
      rect.width > 0
      && rect.height > 0
      && style.display !== "none"
      && style.visibility !== "hidden"
    );
  }

  function currentViewedVideoId() {
    const fromUrl = videoIdFromHref(location.href);
    if (fromUrl) return fromUrl;
    const activeOriginalButton = [...document.querySelectorAll(
      '.dy-download-button[data-dyna-integrated="true"][data-dyna-video-id]',
    )].find((button) => {
      const wrapperVideoId = String(
        button.closest(".dy-download-wrapper[data-video-id]")?.dataset.videoId || "",
      );
      return wrapperVideoId.endsWith("_modal") && isVisible(button);
    });
    return String(activeOriginalButton?.dataset.dynaVideoId || "").replace(/_modal$/, "");
  }

  function anchorForVideo(videoId) {
    return [...document.querySelectorAll("a[href]")].find(
      (candidate) => videoIdFromHref(candidate.href) === videoId,
    ) || null;
  }

  function currentProfileUrl() {
    const path = profilePath(location.href);
    return path ? `https://www.douyin.com${path}` : "";
  }

  function findProfileTabBar() {
    if (!currentProfileUrl()) return null;
    const worksLabels = [...document.querySelectorAll("span, a, button, [role='tab']")]
      .filter((element) => /^作品(?:\s*\d+)?$/.test(String(element.textContent || "").trim()));
    for (const label of worksLabels) {
      let candidate = label.parentElement;
      for (let depth = 0; candidate && depth < 5; depth += 1) {
        const text = String(candidate.textContent || "").replace(/\s+/g, " ").trim();
        if (
          text.includes("推荐")
          && text.includes("喜欢")
          && text.length <= 100
          && candidate.childElementCount <= 16
        ) {
          return candidate;
        }
        candidate = candidate.parentElement;
      }
    }
    return null;
  }

  function renderDirectStartButton(button = document.querySelector(".dyna-batch-direct-start")) {
    if (!(button instanceof HTMLButtonElement)) return;
    const activeHere = Boolean(
      session
      && profilePath(session.source_url) === profilePath(location.href),
    );
    button.disabled = directStarting;
    button.classList.toggle("active", activeHere);
    button.textContent = directStarting
      ? "Đang kết nối Dyna…"
      : activeHere
        ? `Đang chọn video (${selected.size}/${MAX_VIDEOS})`
        : "Chọn nhiều video & đăng Dyna tùy chỉnh";
  }

  function ensureDirectStartButton() {
    const toolbar = findProfileTabBar();
    if (!toolbar) return;
    let button = toolbar.querySelector(":scope > .dyna-batch-direct-start");
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "dyna-batch-direct-start";
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        void startDirectSelection();
      });
      toolbar.appendChild(button);
    }
    renderDirectStartButton(button);
  }

  async function startDirectSelection() {
    if (directStarting) return;
    if (session && isExpectedProfile()) {
      showToast("Dyna đang ở chế độ chọn video trên trang này.", "success");
      return;
    }
    const sourceUrl = currentProfileUrl();
    if (!sourceUrl) {
      showToast("Hãy mở một trang cá nhân Douyin trước.", "error");
      return;
    }
    directStarting = true;
    renderDirectStartButton();
    try {
      const response = await sendMessage({
        type: "dyna:create-selection",
        sourceUrl,
      });
      await activate(response.session);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error), "error");
    } finally {
      directStarting = false;
      renderDirectStartButton();
    }
  }

  function bridgeResults() {
    const node = document.getElementById(RESULT_LIST_ID);
    if (!node?.textContent) return [];
    try {
      const parsed = JSON.parse(node.textContent);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function resultFor(videoId) {
    return bridgeResults().reverse().find(
      (item) => String(item?.aweme_id || "").replace(/_modal$/, "") === videoId,
    ) || null;
  }

  function textFromCard(anchor) {
    if (!(anchor instanceof Element)) return "";
    const card = anchor.closest("li, article, [data-e2e], [class*='card'], [class*='item']") || anchor.parentElement;
    return String(
      anchor.getAttribute("aria-label")
        || anchor.getAttribute("title")
        || card?.querySelector("[title]")?.getAttribute("title")
        || card?.textContent
        || "",
    ).replace(/\s+/g, " ").trim().slice(0, 10000);
  }

  function thumbnailFromCard(anchor) {
    if (!(anchor instanceof Element)) return "";
    const image = anchor.querySelector("img") || anchor.parentElement?.querySelector("img");
    const candidates = [
      image?.src,
      image?.currentSrc,
      image?.getAttribute("data-src"),
      image?.getAttribute("data-lazy-src"),
    ];
    return String(candidates.find((value) => /^https?:\/\//i.test(String(value || ""))) || "").trim();
  }

  function metadataFor(videoId, anchor) {
    const captured = resultFor(videoId) || {};
    const downloadUrls = Array.isArray(captured.download_urls)
      ? captured.download_urls.filter((value) => /^https?:\/\//i.test(String(value || "")))
      : [];
    if (captured.download_url && !downloadUrls.includes(captured.download_url)) {
      downloadUrls.unshift(captured.download_url);
    }
    return {
      video_id: videoId,
      source_url: `https://www.douyin.com/video/${videoId}`,
      description: String(captured.description || textFromCard(anchor) || ""),
      author_uid: String(captured.author_uid || ""),
      author_nickname: String(captured.author_nickname || ""),
      create_time: Number(captured.create_time || 0),
      duration_ms: Number(captured.duration_ms || 0),
      like_count: Number(captured.like_count || 0),
      play_count: Number(captured.play_count || 0),
      thumbnail_url: String(captured.thumbnail_url || thumbnailFromCard(anchor) || ""),
      download_url: String(captured.download_url || downloadUrls[0] || ""),
      download_urls: downloadUrls.slice(0, 12),
      referer: String(captured.referer || location.href),
      user_agent: String(captured.user_agent || navigator.userAgent),
      content_type: "video",
    };
  }

  function createRoot() {
    let root = document.getElementById(ROOT_ID);
    if (root) return root;
    root = document.createElement("div");
    root.id = ROOT_ID;
    root.innerHTML = `
      <div class="dyna-batch-bar" hidden>
        <div class="dyna-batch-brand"><strong>Dyna</strong><span>Chọn video Douyin</span></div>
        <span class="dyna-batch-count">Đã chọn 0/${MAX_VIDEOS}</span>
        <button class="dyna-batch-clear" type="button">Bỏ chọn tất cả</button>
        <button class="dyna-batch-review" type="button">Xem danh sách</button>
        <button class="dyna-batch-send" type="button" disabled>Gửi sang Dyna</button>
      </div>
      <button class="dyna-batch-viewer-select" type="button" hidden></button>
      <aside class="dyna-batch-drawer" hidden aria-label="Danh sách video đã chọn">
        <header><div><strong>Video đã chọn</strong><small>Thứ tự này sẽ được giữ khi về Dyna</small></div><button type="button" aria-label="Đóng">×</button></header>
        <div class="dyna-batch-list"></div>
      </aside>
      <div class="dyna-batch-toast" hidden></div>
    `;
    (document.body || document.documentElement).appendChild(root);
    root.querySelector(".dyna-batch-clear").addEventListener("click", clearSelection);
    root.querySelector(".dyna-batch-review").addEventListener("click", () => {
      const drawer = root.querySelector(".dyna-batch-drawer");
      drawer.hidden = !drawer.hidden;
      renderDrawer();
    });
    root.querySelector(".dyna-batch-drawer header button").addEventListener("click", () => {
      root.querySelector(".dyna-batch-drawer").hidden = true;
    });
    root.querySelector(".dyna-batch-send").addEventListener("click", completeSelection);
    root.querySelector(".dyna-batch-viewer-select").addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const videoId = event.currentTarget.dataset.videoId;
      if (videoId) void toggleVideo(videoId, anchorForVideo(videoId));
    });
    return root;
  }

  function showToast(message, tone = "") {
    const toast = createRoot().querySelector(".dyna-batch-toast");
    toast.textContent = message;
    toast.className = `dyna-batch-toast ${tone}`.trim();
    toast.hidden = false;
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => {
      toast.hidden = true;
    }, 4200);
  }

  function orderedItems() {
    return [...selected.values()].sort(
      (left, right) => Number(left.selected_order) - Number(right.selected_order),
    );
  }

  async function persist() {
    if (!session) return;
    await storageSet(storageKey(), {
      session_id: session.id,
      source_url: session.source_url,
      updated_at: new Date().toISOString(),
      items: orderedItems(),
    });
  }

  function render() {
    const root = createRoot();
    const count = selected.size;
    const viewedVideoId = currentViewedVideoId();
    const viewerButton = root.querySelector(".dyna-batch-viewer-select");
    const viewedItem = viewedVideoId ? selected.get(viewedVideoId) : null;
    root.querySelector(".dyna-batch-count").textContent = `Đã chọn ${count}/${MAX_VIDEOS}`;
    root.querySelector(".dyna-batch-send").disabled = count === 0 || sending;
    root.querySelector(".dyna-batch-send").textContent = sending
      ? "Đang gửi…"
      : `Gửi ${count || ""} video sang Dyna`.replace("  ", " ");
    for (const button of document.querySelectorAll(".dyna-batch-card-select")) {
      const item = selected.get(button.dataset.videoId);
      button.classList.toggle("viewer-suppressed", Boolean(viewedVideoId));
      button.classList.toggle("selected", Boolean(item));
      const buttonText = item ? String(item.selected_order) : "";
      if (button.textContent !== buttonText) button.textContent = buttonText;
      button.setAttribute("aria-label", item ? "Bỏ chọn video" : "Chọn video bằng Dyna");
    }
    viewerButton.hidden = !viewedVideoId;
    viewerButton.dataset.videoId = viewedVideoId;
    viewerButton.classList.toggle("selected", Boolean(viewedItem));
    viewerButton.textContent = viewedItem
      ? `${viewedItem.selected_order} · Đã chọn`
      : "+ Chọn video này";
    viewerButton.setAttribute(
      "aria-label",
      viewedItem ? "Bỏ chọn video đang xem" : "Chọn video đang xem bằng Dyna",
    );
    renderDirectStartButton();
    renderDrawer();
  }

  function removeSelectionControls() {
    for (const button of document.querySelectorAll(".dyna-batch-card-select")) {
      const anchor = button.__dynaSourceAnchor || button.closest("a");
      if (anchor) {
        decoratedAnchors.delete(anchor);
      }
      button.__dynaOverlayHost?.classList.remove("dyna-batch-card-anchor");
      button.remove();
    }
    const root = createRoot();
    root.querySelector(".dyna-batch-bar").hidden = true;
    root.querySelector(".dyna-batch-drawer").hidden = true;
    root.querySelector(".dyna-batch-viewer-select").hidden = true;
  }

  function renderDrawer() {
    const root = createRoot();
    const list = root.querySelector(".dyna-batch-list");
    if (!list) return;
    list.replaceChildren();
    const items = orderedItems();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "dyna-batch-empty";
      empty.textContent = "Chưa chọn video nào.";
      list.appendChild(empty);
      return;
    }
    for (const item of items) {
      const row = document.createElement("article");
      row.innerHTML = `
        <span class="dyna-batch-order">${item.selected_order}</span>
        ${item.thumbnail_url ? `<img src="${item.thumbnail_url.replace(/"/g, "&quot;")}" alt="">` : "<span class=\"dyna-batch-placeholder\">▶</span>"}
        <div><strong>${String(item.description || `Video ${item.video_id}`).replace(/[<&]/g, (value) => value === "<" ? "&lt;" : "&amp;")}</strong><small>${item.video_id}${item.download_url ? "" : " · đang chờ dữ liệu tải"}</small></div>
        <button type="button" aria-label="Bỏ video">×</button>
      `;
      row.querySelector("button").addEventListener("click", () => removeVideo(item.video_id));
      list.appendChild(row);
    }
  }

  async function removeVideo(videoId) {
    selected.delete(videoId);
    orderedItems().forEach((item, index) => {
      item.selected_order = index + 1;
    });
    await persist();
    render();
  }

  async function clearSelection() {
    selected.clear();
    await persist();
    render();
  }

  async function toggleVideo(videoId, anchor) {
    if (selected.has(videoId)) {
      await removeVideo(videoId);
      return;
    }
    if (selected.size >= MAX_VIDEOS) {
      showToast(`Mỗi lần chỉ được chọn tối đa ${MAX_VIDEOS} video.`, "error");
      return;
    }
    selected.set(videoId, {
      ...metadataFor(videoId, anchor),
      selected_order: selected.size + 1,
    });
    await persist();
    render();
  }

  function originalOverlayHost(videoId, anchor) {
    const wrapper = [...document.querySelectorAll(".dy-download-wrapper[data-video-id]")].find(
      (candidate) => (
        !String(candidate.dataset.videoId || "").endsWith("_modal")
        && String(candidate.dataset.videoId || "").replace(/_modal$/, "") === videoId
      ),
    );
    return wrapper?.parentElement || anchor;
  }

  function selectionButtonsFor(videoId) {
    return [...document.querySelectorAll(".dyna-batch-card-select")].filter(
      (button) => button.dataset.videoId === videoId,
    );
  }

  function decorate(anchor) {
    if (!(anchor instanceof HTMLAnchorElement)) return;
    const videoId = videoIdFromHref(anchor.href);
    if (!videoId) return;
    const host = originalOverlayHost(videoId, anchor);
    const existingButtons = selectionButtonsFor(videoId);
    const existing = existingButtons[0];
    for (const staleButton of existingButtons) {
      if (staleButton !== existing) staleButton.remove();
    }
    if (existing) {
      decoratedAnchors.add(anchor);
      if (existing.__dynaOverlayHost !== host) {
        existing.__dynaOverlayHost?.classList.remove("dyna-batch-card-anchor");
        host.classList.add("dyna-batch-card-anchor");
        host.appendChild(existing);
        existing.__dynaOverlayHost = host;
      }
      existing.__dynaSourceAnchor = anchor;
      return;
    }
    decoratedAnchors.add(anchor);
    host.classList.add("dyna-batch-card-anchor");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dyna-batch-card-select";
    button.dataset.videoId = videoId;
    button.__dynaOverlayHost = host;
    button.__dynaSourceAnchor = anchor;
    button.setAttribute("aria-label", "Chọn video bằng Dyna");
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      void toggleVideo(videoId, anchorForVideo(videoId) || button.__dynaSourceAnchor);
    }, true);
    host.appendChild(button);
  }

  function scan(root = document) {
    ensureDirectStartButton();
    if (!session || !isExpectedProfile()) return;
    for (const anchor of root.querySelectorAll?.(
      'a[href*="/video/"], a[href*="modal_id="], a[href*="aweme_id="]',
    ) || []) decorate(anchor);
    render();
  }

  async function restore() {
    const saved = await storageGet(storageKey());
    const updated = saved?.updated_at ? new Date(saved.updated_at).getTime() : 0;
    if (
      saved?.session_id !== session.id
      || !Array.isArray(saved?.items)
      || Date.now() - updated > 7 * 24 * 60 * 60 * 1000
    ) {
      await storageRemove(storageKey());
      return;
    }
    selected.clear();
    for (const item of saved.items.slice(0, MAX_VIDEOS)) {
      if (item?.video_id) selected.set(String(item.video_id), item);
    }
  }

  async function completeSelection() {
    if (!session || sending || !selected.size) return;
    sending = true;
    render();
    try {
      window.dispatchEvent(new Event("aix:douyin-rescan"));
      await new Promise((resolve) => setTimeout(resolve, 350));
      const items = orderedItems().map((item) => {
        const anchor = [...document.querySelectorAll("a[href]")].find(
          (candidate) => videoIdFromHref(candidate.href) === item.video_id,
        );
        return {
          ...item,
          ...(anchor ? metadataFor(item.video_id, anchor) : {}),
          selected_order: item.selected_order,
        };
      });
      await sendMessage({
        type: "dyna:complete-selection",
        sessionId: session.id,
        items,
      });
      await storageRemove(storageKey());
      removeSelectionControls();
      showToast(`Đã gửi ${items.length} video sang Dyna.`, "success");
      session = null;
      selected.clear();
      renderDirectStartButton();
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : String(error),
        "error",
      );
    } finally {
      sending = false;
      if (session) render();
    }
  }

  async function activate(nextSession) {
    if (!nextSession?.id || session?.id === nextSession.id) return;
    if (profilePath(nextSession.source_url) !== profilePath(location.href)) return;
    session = nextSession;
    await restore();
    const root = createRoot();
    root.querySelector(".dyna-batch-bar").hidden = false;
    scan();
    showToast("Dyna đã bật chế độ chọn video. Tích vào các video bạn muốn đăng.", "success");
  }

  async function checkActiveSession() {
    try {
      const response = await sendMessage({ type: "dyna:get-active-selection" });
      if (response.session) {
        if (session?.id === response.session.id) {
          if (isExpectedProfile()) scan();
          return;
        }
        await activate(response.session);
      } else if (session) {
        session = null;
        selected.clear();
        removeSelectionControls();
        renderDirectStartButton();
      }
    } catch {
      // Dyna may be closed while the user browses normally.
      if (session && isExpectedProfile()) scan();
    }
  }

  function scheduleScan() {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(() => scan(), 180);
  }

  const observer = new MutationObserver((mutations) => {
    if (!globalThis.document?.documentElement) return;
    const ownRoot = document.getElementById(ROOT_ID);
    if (mutations.some((mutation) => !ownRoot?.contains(mutation.target))) {
      scheduleScan();
    }
  });
  function start() {
    createRoot();
    observer.observe(document.documentElement, { childList: true, subtree: true });
    scan();
    void checkActiveSession();
    setInterval(checkActiveSession, 2000);
  }

  if (document.documentElement) start();
  else addEventListener("DOMContentLoaded", start, { once: true });
})();
