(() => {
  "use strict";

  const API_BASE = "http://127.0.0.1:8765/api/extension";
  const PENDING_PREFIX = "dyna:pending:";
  const JOB_PREFIX = "dyna:job:";
  const handlingDownloads = new Set();

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

  function downloadsSearch(query) {
    return new Promise((resolve, reject) => {
      chrome.downloads.search(query, (items) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else resolve(items || []);
      });
    });
  }

  function downloadsStart(options) {
    return new Promise((resolve, reject) => {
      chrome.downloads.download(options, (downloadId) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else if (!Number.isInteger(downloadId)) reject(new Error("Chrome không tạo được lượt tải video."));
        else resolve(downloadId);
      });
    });
  }

  function safePart(value, fallback) {
    const cleaned = String(value || "")
      .replace(/[\\/:*?"<>|\x00-\x1f]/g, "_")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 90);
    return cleaned || fallback;
  }

  async function dynaRequest(path, options = {}) {
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        method: options.method || "GET",
        headers: {
          "X-Dyna-Extension-Id": chrome.runtime.id,
          ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch (error) {
      throw new Error("Không kết nối được Dyna. Hãy mở ứng dụng Dyna trước.");
    }

    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { detail: text || `HTTP ${response.status}` };
    }
    if (!response.ok) throw new Error(data.detail || `Dyna trả về HTTP ${response.status}`);
    return data;
  }

  function notifyTab(tabId, payload) {
    if (!Number.isInteger(tabId)) return;
    chrome.tabs.sendMessage(tabId, { type: "dyna:job-update", ...payload }, () => {
      void chrome.runtime.lastError;
    });
  }

  function pendingKey(downloadId) {
    return `${PENDING_PREFIX}${downloadId}`;
  }

  function jobKey(profileId, videoId) {
    return `${JOB_PREFIX}${profileId}:${videoId}`;
  }

  async function handoffDownload(downloadId, record) {
    const [download] = await downloadsSearch({ id: downloadId });
    if (!download || download.state !== "complete" || !download.filename) {
      throw new Error("Chrome chưa cung cấp file video đã tải hoàn tất.");
    }

    const metadata = record.metadata || {};
    const payload = {
      profile_id: String(record.profileId),
      video_id: String(metadata.aweme_id || metadata.video_id || ""),
      file_path: download.filename,
      source_url: String(metadata.source_url || record.pageUrl || "").slice(0, 4000),
      description: String(metadata.description || "").slice(0, 10000),
      create_time: Number(metadata.create_time || 0),
      duration_ms: Number(metadata.duration_ms || 0),
      like_count: Number(metadata.like_count || 0),
      play_count: Number(metadata.play_count || 0),
      author_uid: String(metadata.author_uid || "").slice(0, 256),
      author_nickname: String(metadata.author_nickname || "").slice(0, 512),
      download_url: String(metadata.download_url || "").slice(0, 8000),
      download_id: downloadId,
    };
    const result = await dynaRequest("/jobs", { method: "POST", body: payload });
    const saved = {
      profileId: payload.profile_id,
      videoId: payload.video_id,
      downloadId,
      filename: download.filename,
      status: result.job?.status || "importing",
      handedOffAt: new Date().toISOString(),
    };
    await storageSet(jobKey(payload.profile_id, payload.video_id), saved);
    await storageRemove(pendingKey(downloadId));
    notifyTab(record.tabId, {
      ok: true,
      stage: "queued",
      profileId: payload.profile_id,
      videoId: payload.video_id,
      message: result.duplicate
        ? "Video đã có trong hàng đợi Dyna."
        : "Đã tải xong và chuyển vào hàng đợi Dyna.",
      job: result.job || null,
    });
    return { ...result, filename: download.filename };
  }

  async function startDynaDownload(message, sender) {
    const profileId = String(message.profileId || "").trim();
    const metadata = message.metadata || {};
    const videoId = String(metadata.aweme_id || metadata.video_id || "").trim();
    const url = String(metadata.download_url || "").trim();
    if (!/^\d+$/.test(profileId)) throw new Error("Profile Dyna không hợp lệ.");
    if (!/^[A-Za-z0-9_-]+$/.test(videoId)) throw new Error("Không xác định được ID video Douyin.");
    if (!/^https?:\/\//i.test(url)) throw new Error("Extension chưa lấy được link tải video.");

    await dynaRequest("/health");
    const profileFolder = safePart(`Profile ${profileId}`, `Profile_${profileId}`);
    const requestedName = safePart(metadata.filename, `${videoId}.mp4`);
    const filename = `Dyna/${profileFolder}/${requestedName.toLowerCase().endsWith(".mp4") ? requestedName : `${requestedName}.mp4`}`;
    const downloadId = await downloadsStart({
      url,
      filename,
      conflictAction: "uniquify",
      saveAs: false,
    });
    const record = {
      downloadId,
      profileId,
      metadata,
      pageUrl: String(message.pageUrl || sender?.tab?.url || ""),
      tabId: sender?.tab?.id,
      state: "in_progress",
      createdAt: new Date().toISOString(),
    };
    await storageSet(pendingKey(downloadId), record);
    const [currentDownload] = await downloadsSearch({ id: downloadId });
    if (currentDownload?.state === "complete" || currentDownload?.state === "interrupted") {
      void processFinishedDownload(
        downloadId,
        currentDownload.state,
        currentDownload.error || "",
      );
    }
    return { ok: true, downloadId, videoId, profileId };
  }

  async function processFinishedDownload(downloadId, state, downloadError = "") {
    if (handlingDownloads.has(downloadId)) return;
    handlingDownloads.add(downloadId);
    try {
      const key = pendingKey(downloadId);
      const record = await storageGet(key);
      if (!record) return;
      if (state === "interrupted") {
        record.state = "interrupted";
        record.error = downloadError || "Chrome đã dừng tải video.";
        await storageSet(key, record);
        notifyTab(record.tabId, {
          ok: false,
          stage: "download_failed",
          profileId: record.profileId,
          videoId: record.metadata?.aweme_id || record.metadata?.video_id || "",
          message: record.error,
        });
        return;
      }
      try {
        await handoffDownload(downloadId, record);
      } catch (error) {
        record.state = "handoff_failed";
        record.error = error instanceof Error ? error.message : String(error);
        await storageSet(key, record);
        notifyTab(record.tabId, {
          ok: false,
          stage: "handoff_failed",
          profileId: record.profileId,
          videoId: record.metadata?.aweme_id || record.metadata?.video_id || "",
          downloadId,
          message: record.error,
        });
      }
    } finally {
      handlingDownloads.delete(downloadId);
    }
  }

  chrome.downloads.onChanged.addListener((change) => {
    const state = change.state?.current;
    if (state !== "complete" && state !== "interrupted") return;
    void processFinishedDownload(change.id, state, change.error?.current || "");
  });

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!String(message?.type || "").startsWith("dyna:")) return undefined;
    void (async () => {
      switch (message.type) {
        case "dyna:health":
          return dynaRequest("/health");
        case "dyna:get-profiles":
          return dynaRequest("/profiles");
        case "dyna:start-download":
          return startDynaDownload(message, sender);
        case "dyna:get-job-status":
          return dynaRequest(`/jobs/${encodeURIComponent(message.profileId)}/${encodeURIComponent(message.videoId)}`);
        case "dyna:retry-handoff": {
          const record = await storageGet(pendingKey(Number(message.downloadId)));
          if (!record) throw new Error("Không tìm thấy lượt tải cần gửi lại.");
          return handoffDownload(Number(message.downloadId), record);
        }
        default:
          throw new Error("Lệnh Dyna extension không được hỗ trợ.");
      }
    })().then(
      (result) => sendResponse({ ok: true, ...result }),
      (error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }),
    );
    return true;
  });
})();
