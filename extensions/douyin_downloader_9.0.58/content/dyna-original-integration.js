(() => {
  "use strict";

  const ROOT_ID = "dyna-douyin-integration";
  const REQUEST_EVENT = "dyna:original-download-request";
  const TERMINAL_STATUSES = new Set(["completed", "cancelled", "ignored"]);
  const FAILED_PREFIX = "failed";
  const activeJobs = new Map();
  let currentMetadata = null;
  let selectedProfileId = "";

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

  function baseVideoId(value) {
    return String(value || "").replace(/_modal$/, "").trim();
  }

  function jobKey(profileId, videoId) {
    return `${profileId}:${videoId}`;
  }

  function platformLabels(platforms) {
    const labels = [];
    if (platforms?.tiktok) labels.push("TikTok");
    if (platforms?.youtube) labels.push("YouTube");
    if (platforms?.facebook) labels.push("Facebook");
    return labels;
  }

  function createRoot() {
    const existing = document.getElementById(ROOT_ID);
    if (existing) return existing;
    const root = document.createElement("div");
    root.id = ROOT_ID;
    root.innerHTML = `
      <div class="dyna-modal-backdrop" hidden>
        <section class="dyna-modal" role="dialog" aria-modal="true" aria-label="Tải và đăng video bằng Dyna">
          <header>
            <div><strong>Dyna</strong><small>Chọn Profile đăng video</small></div>
            <button class="dyna-close" type="button" aria-label="Đóng">×</button>
          </header>
          <div class="dyna-video-info"><strong></strong><span></span></div>
          <div class="dyna-profile-list"><div class="dyna-loading">Đang kết nối Dyna…</div></div>
          <footer>
            <button class="dyna-download-only" type="button">Chỉ tải video</button>
            <button class="dyna-cancel" type="button">Hủy</button>
            <button class="dyna-confirm" type="button" disabled>Tải và đăng</button>
          </footer>
        </section>
      </div>
      <div class="dyna-toast" hidden><span></span><button type="button" hidden>Gửi lại</button></div>
    `;
    (document.body || document.documentElement).appendChild(root);
    root.querySelector(".dyna-close").addEventListener("click", closeModal);
    root.querySelector(".dyna-cancel").addEventListener("click", closeModal);
    root.querySelector(".dyna-modal-backdrop").addEventListener("click", (event) => {
      if (event.target.classList.contains("dyna-modal-backdrop")) closeModal();
    });
    root.querySelector(".dyna-confirm").addEventListener("click", startDownload);
    root.querySelector(".dyna-download-only").addEventListener("click", startDownloadOnly);
    return root;
  }

  function closeModal() {
    createRoot().querySelector(".dyna-modal-backdrop").hidden = true;
  }

  function setOriginalButtonState(buttonId, title, state = "") {
    const expected = baseVideoId(buttonId);
    for (const wrapper of document.querySelectorAll(".dy-download-wrapper[data-video-id]")) {
      if (baseVideoId(wrapper.dataset.videoId) !== expected) continue;
      const button = wrapper.querySelector('.dy-download-button[data-dyna-integrated="true"]');
      if (!button) continue;
      button.dataset.dynaState = state;
      button.title = title;
      const status = button.querySelector(".dyna-original-status");
      if (status) {
        status.textContent = state === "success" ? "✓" : state === "error" ? "!" : state === "active" ? "…" : "";
        status.hidden = !status.textContent;
      }
    }
  }

  function showToast(message, kind = "info", retryDownloadId = 0) {
    const toast = createRoot().querySelector(".dyna-toast");
    toast.hidden = false;
    toast.dataset.kind = kind;
    toast.querySelector("span").textContent = message;
    const retry = toast.querySelector("button");
    retry.hidden = !retryDownloadId;
    retry.onclick = retryDownloadId
      ? async () => {
          retry.disabled = true;
          try {
            await sendMessage({ type: "dyna:retry-handoff", downloadId: retryDownloadId });
            showToast("Đã gửi lại video vào Dyna.", "success");
          } catch (error) {
            showToast(error.message, "error", retryDownloadId);
          } finally {
            retry.disabled = false;
          }
        }
      : null;
    clearTimeout(showToast.timer);
    if (!retryDownloadId) {
      showToast.timer = setTimeout(() => {
        toast.hidden = true;
      }, kind === "error" ? 7000 : 4500);
    }
  }

  function normalizeRequest(payload) {
    const videoId = baseVideoId(payload?.aweme_id || payload?.video_id);
    const downloadUrl = String(payload?.download_url || "").trim();
    const sourceUrl = String(payload?.source_url || location.href).trim();
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(videoId)) throw new Error("Nút gốc không trả về ID video hợp lệ.");
    if (!/^https?:\/\//i.test(downloadUrl)) throw new Error("Nút gốc không trả về link tải video hợp lệ.");
    if (!/^https?:\/\/([^/]+\.)?douyin\.com(?:\/|$)/i.test(sourceUrl)) throw new Error("Trang nguồn Douyin không hợp lệ.");
    return {
      ...payload,
      aweme_id: videoId,
      video_id: videoId,
      original_button_id: String(payload.original_button_id || videoId),
      source_url: sourceUrl,
      download_url: downloadUrl,
      filename: String(payload.filename || `${videoId}.mp4`),
      description: String(payload.description || ""),
    };
  }

  async function openModal(metadata) {
    currentMetadata = metadata;
    selectedProfileId = "";
    const root = createRoot();
    const info = root.querySelector(".dyna-video-info");
    info.querySelector("strong").textContent = `Video ${metadata.aweme_id}`;
    const quality = metadata.resolution ? ` · ${metadata.resolution}` : "";
    info.querySelector("span").textContent = `${metadata.description || "Không có mô tả"}${quality}`;
    root.querySelector(".dyna-confirm").disabled = true;
    root.querySelector(".dyna-download-only").disabled = false;
    root.querySelector(".dyna-download-only").textContent = "Chỉ tải video";
    root.querySelector(".dyna-profile-list").innerHTML = '<div class="dyna-loading">Đang kết nối Dyna…</div>';
    root.querySelector(".dyna-modal-backdrop").hidden = false;
    try {
      const response = await sendMessage({ type: "dyna:get-profiles" });
      renderProfiles(response.profiles || []);
    } catch (error) {
      const empty = document.createElement("div");
      empty.className = "dyna-empty";
      empty.textContent = error.message;
      root.querySelector(".dyna-profile-list").replaceChildren(empty);
    }
  }

  function renderProfiles(profiles) {
    const root = createRoot();
    const list = root.querySelector(".dyna-profile-list");
    list.replaceChildren();
    if (!profiles.length) {
      const empty = document.createElement("div");
      empty.className = "dyna-empty";
      empty.textContent = "Dyna chưa có Profile nào.";
      list.appendChild(empty);
      return;
    }
    for (const profile of profiles) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "dyna-profile";
      row.disabled = !profile.available;
      row.dataset.profileId = String(profile.id);
      const number = document.createElement("span");
      number.className = "dyna-profile-number";
      number.textContent = String(profile.id);
      const copy = document.createElement("span");
      copy.className = "dyna-profile-copy";
      const name = document.createElement("strong");
      name.textContent = String(profile.name || `Profile ${profile.id}`);
      const detail = document.createElement("small");
      detail.textContent = profile.available
        ? platformLabels(profile.platforms).join(" · ")
        : String(profile.reason || "Không khả dụng");
      copy.append(name, detail);
      row.append(number, copy, document.createElement("i"));
      row.addEventListener("click", () => {
        selectedProfileId = String(profile.id);
        for (const item of list.querySelectorAll(".dyna-profile")) item.classList.toggle("selected", item === row);
        root.querySelector(".dyna-confirm").disabled = false;
      });
      list.appendChild(row);
    }
  }

  async function startDownload() {
    if (!currentMetadata || !selectedProfileId) return;
    const metadata = currentMetadata;
    const profileId = selectedProfileId;
    const confirm = createRoot().querySelector(".dyna-confirm");
    confirm.disabled = true;
    confirm.textContent = "Đang bắt đầu…";
    try {
      const response = await sendMessage({
        type: "dyna:start-download",
        profileId,
        metadata,
        pageUrl: metadata.source_url,
      });
      const record = {
        profileId,
        videoId: String(response.videoId),
        buttonId: metadata.original_button_id,
        downloadId: response.downloadId,
        pollTimer: 0,
      };
      activeJobs.set(jobKey(record.profileId, record.videoId), record);
      setOriginalButtonState(record.buttonId, `Chrome đang tải cho Profile ${profileId}`, "active");
      closeModal();
      showToast(`Đang tải video gốc cho Profile ${profileId}.`, "success");
    } catch (error) {
      setOriginalButtonState(metadata.original_button_id, error.message, "error");
      showToast(error.message, "error");
    } finally {
      confirm.disabled = false;
      confirm.textContent = "Tải và đăng";
    }
  }

  async function startDownloadOnly() {
    if (!currentMetadata) return;
    const metadata = currentMetadata;
    const button = createRoot().querySelector(".dyna-download-only");
    button.disabled = true;
    button.textContent = "Đang tải…";
    try {
      await sendMessage({
        type: "dyna:start-download-only",
        metadata,
      });
      closeModal();
      showToast("Chrome đã bắt đầu tải video vào thư mục Dyna.", "success");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Chỉ tải video";
    }
  }

  function statusText(job) {
    const status = String(job?.status || "");
    const labels = {
      importing: "Dyna đang nhận file",
      downloaded: "Dyna đã nhận file",
      waiting_caption: "Đang chờ caption",
      caption_ready: "Đã có caption",
      uploading: "Dyna đang đăng",
      completed: "Đăng thành công",
      cancelled: "Đã hủy",
      ignored: "Đã bỏ qua",
    };
    if (status.startsWith(FAILED_PREFIX)) return "Đăng gặp lỗi";
    return labels[status] || "Đã xếp hàng Dyna";
  }

  function startPolling(record) {
    clearInterval(record.pollTimer);
    const poll = async () => {
      try {
        const response = await sendMessage({
          type: "dyna:get-job-status",
          profileId: record.profileId,
          videoId: record.videoId,
        });
        const job = response.job;
        const status = String(job?.status || "");
        const state = status === "completed" ? "success" : status.startsWith(FAILED_PREFIX) ? "error" : "active";
        setOriginalButtonState(record.buttonId, statusText(job), state);
        if (TERMINAL_STATUSES.has(status) || status.startsWith(FAILED_PREFIX)) {
          clearInterval(record.pollTimer);
          if (status === "completed") showToast(`Profile ${record.profileId} đã đăng video thành công.`, "success");
          else showToast(job?.last_error || statusText(job), "error");
        }
      } catch {
        // Download handoff notifications report connection errors and expose retry.
      }
    };
    void poll();
    record.pollTimer = setInterval(poll, 3000);
  }

  addEventListener("message", (event) => {
    if (event.source !== window || event.data?.type !== REQUEST_EVENT) return;
    try {
      void openModal(normalizeRequest(event.data.payload));
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== "dyna:job-update") return;
    const key = jobKey(String(message.profileId), String(message.videoId));
    const record = activeJobs.get(key) || {
      profileId: String(message.profileId),
      videoId: String(message.videoId),
      buttonId: String(message.videoId),
      downloadId: Number(message.downloadId || 0),
      pollTimer: 0,
    };
    activeJobs.set(key, record);
    if (message.ok) {
      setOriginalButtonState(record.buttonId, "Đã gửi vào hàng đợi Dyna", "active");
      showToast(message.message || "Đã chuyển video vào Dyna.", "success");
      startPolling(record);
    } else {
      clearInterval(record.pollTimer);
      setOriginalButtonState(record.buttonId, message.message || "Gửi Dyna thất bại", "error");
      showToast(message.message || "Không gửi được video vào Dyna.", "error", message.downloadId || 0);
    }
  });

  createRoot();
})();
