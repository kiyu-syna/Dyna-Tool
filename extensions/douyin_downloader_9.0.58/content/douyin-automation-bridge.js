(() => {
  "use strict";

  const EVENT_NAME = "aix:douyin-video-ready";
  const ERROR_EVENT_NAME = "aix:douyin-video-error";
  const RESULT_ELEMENT_ID = "aix-douyin-download-result";
  const RESULTS_ELEMENT_ID = "aix-douyin-download-results";
  const BRIDGE_VERSION = "1.2.0";
  const results = new Map();
  let lastResult = null;

  function firstUrl(value) {
    if (typeof value === "string" && /^https?:\/\//i.test(value)) return value;
    if (Array.isArray(value)) {
      for (const item of value) {
        const url = firstUrl(item);
        if (url) return url;
      }
    }
    if (value && typeof value === "object") {
      return firstUrl(value.url_list || value.urlList || value.url || value.uri);
    }
    return "";
  }

  function filenamePart(value) {
    return String(value || "")
      .replace(/[\\/:*?\"<>|\x00-\x1f]/g, "_")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 100);
  }

  function numericValue(value) {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function resolutionValue(rate) {
    const width = numericValue(rate?.width);
    const height = numericValue(rate?.height);
    if (width > 0 && height > 0) return Math.min(width, height);

    const label = String(
      rate?.resolution || rate?.gearName || rate?.gear_name || rate?.qualityType || ""
    );
    const match = label.match(/(\d+)[pP]/);
    if (match) return numericValue(match[1]);
    if (/\b8k\b/i.test(label)) return 4320;
    if (/\b4k\b/i.test(label)) return 2160;
    return 0;
  }

  function selectHighestResolutionRate(rates) {
    const candidates = rates
      .filter((rate) => rate && typeof rate === "object")
      .map((rate) => ({
        rate,
        url: firstUrl(rate.playApi || rate.play_api || rate.playAddr || rate.play_addr),
        resolution: resolutionValue(rate),
        dataSize: numericValue(rate.dataSize || rate.data_size || rate.size),
        bitRate: numericValue(rate.bitRate || rate.bit_rate),
      }))
      .filter((candidate) => candidate.url);

    candidates.sort((left, right) =>
      right.resolution - left.resolution ||
      right.dataSize - left.dataSize ||
      right.bitRate - left.bitRate
    );
    return candidates[0] || null;
  }

  function getVideoUrl(video) {
    if (!video || typeof video !== "object") return "";

    const rates = video.bitRateList || video.bit_rate_list || video.bit_rate || [];
    if (Array.isArray(rates) && rates.length) {
      const highestRate = selectHighestResolutionRate(rates);
      if (highestRate?.url) return highestRate.url;
    }

    return firstUrl(
      video.playApi ||
      video.play_api ||
      video.playAddr ||
      video.play_addr ||
      video.downloadAddr ||
      video.download_addr
    );
  }

  function toResult(aweme, source) {
    if (!aweme || typeof aweme !== "object") return null;

    const video = aweme.video || aweme.videoInfo || aweme.video_info;
    const rates = video?.bitRateList || video?.bit_rate_list || video?.bit_rate || [];
    const selectedRate = Array.isArray(rates)
      ? selectHighestResolutionRate(rates)
      : null;
    const downloadUrl = selectedRate?.url || getVideoUrl(video);
    if (!downloadUrl) return null;

    const awemeId = String(aweme.awemeId || aweme.aweme_id || aweme.id || "");
    const videoId = String(
      video?.playAddrFileHash ||
      video?.play_addr_file_hash ||
      video?.vid ||
      video?.videoId ||
      video?.video_id ||
      ""
    );
    const description = filenamePart(aweme.desc || aweme.title || awemeId || videoId || "douyin_video");
    const author = aweme.author || aweme.authorInfo || aweme.author_info || {};
    const statistics = aweme.statistics || aweme.stats || {};
    const key = `${awemeId}:${videoId}:${downloadUrl}`;

    return {
      key,
      status: "success",
      source,
      aweme_id: awemeId || null,
      video_id: videoId || null,
      source_url: firstUrl(aweme.share_url || aweme.shareUrl) || location.href,
      description: String(aweme.desc || aweme.title || ""),
      create_time: numericValue(aweme.create_time || aweme.createTime),
      duration_ms: numericValue(video?.duration || video?.duration_ms || aweme.duration),
      like_count: numericValue(statistics.digg_count || statistics.like_count),
      play_count: numericValue(statistics.play_count),
      author_uid: String(author.sec_uid || author.secUid || author.uid || ""),
      author_nickname: String(author.nickname || author.name || ""),
      download_url: downloadUrl,
      resolution: selectedRate?.resolution || resolutionValue(video),
      width: numericValue(selectedRate?.rate?.width || video?.width),
      height: numericValue(selectedRate?.rate?.height || video?.height),
      bit_rate: selectedRate?.bitRate || 0,
      data_size: selectedRate?.dataSize || numericValue(video?.playAddrSize || video?.play_addr_size),
      selection_method: selectedRate ? "highest_resolution_menu_item" : "default_play_url",
      filename: `${description}.mp4`,
      // The original extension sends the URL to chrome.downloads.download without custom headers.
      headers_required: false,
      headers: {},
      referer: location.href,
      user_agent: navigator.userAgent,
      detected_at: new Date().toISOString()
    };
  }

  function updateDom(result) {
    let element = document.getElementById(RESULT_ELEMENT_ID);
    if (!element) {
      element = document.createElement("script");
      element.id = RESULT_ELEMENT_ID;
      element.type = "application/json";
      (document.documentElement || document).appendChild(element);
    }
    element.textContent = JSON.stringify(result);
  }

  function updateResultsDom() {
    let element = document.getElementById(RESULTS_ELEMENT_ID);
    if (!element) {
      element = document.createElement("script");
      element.id = RESULTS_ELEMENT_ID;
      element.type = "application/json";
      (document.documentElement || document).appendChild(element);
    }
    element.textContent = JSON.stringify([...results.values()].slice(-250));
  }

  function publish(result) {
    if (!result || results.has(result.key)) return;
    results.set(result.key, result);
    lastResult = result;
    updateDom(result);
    updateResultsDom();
    window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: result }));
  }

  function reportError(message, source) {
    const error = {
      status: "error",
      source,
      aweme_id: null,
      video_id: null,
      download_url: null,
      filename: null,
      headers_required: false,
      headers: {},
      error: message,
      detected_at: new Date().toISOString()
    };
    updateDom(error);
    window.dispatchEvent(new CustomEvent(ERROR_EVENT_NAME, { detail: error }));
    return error;
  }

  function inspect(value, source) {
    if (!value || typeof value !== "object") return 0;

    const seen = new WeakSet();
    const queue = [value];
    let found = 0;
    let visited = 0;

    while (queue.length && visited++ < 15000) {
      const current = queue.shift();
      if (!current || typeof current !== "object" || seen.has(current)) continue;
      seen.add(current);

      const result = toResult(current, source);
      if (result) {
        publish(result);
        found++;
      }

      for (const child of Array.isArray(current) ? current : Object.values(current)) {
        if (child && typeof child === "object") queue.push(child);
      }
    }
    return found;
  }

  function inspectJsonText(text, source) {
    if (typeof text !== "string" || !text.trim()) return 0;
    try {
      return inspect(JSON.parse(text), source);
    } catch {
      try {
        return inspect(JSON.parse(decodeURIComponent(text)), source);
      } catch {
        return 0;
      }
    }
  }

  function inspectDocument() {
    let found = 0;
    const renderData = document.querySelector("#RENDER_DATA");
    if (renderData) found += inspectJsonText(renderData.textContent || "", "render_data");

    found += inspect(window.__INITIAL_STATE__, "initial_state");
    found += inspect(window.__INIT_PROPS__, "init_props");

    for (const video of document.querySelectorAll("video")) {
      const url = video.currentSrc || video.src || video.querySelector("source")?.src;
      if (!url || url.startsWith("blob:")) continue;
      const result = {
        key: `dom:${url}`,
        status: "success",
        source: "video_element",
        aweme_id: null,
        video_id: null,
        download_url: url,
        filename: `${filenamePart(document.title || "douyin_video")}.mp4`,
        headers_required: false,
        headers: {},
        referer: location.href,
        user_agent: navigator.userAgent,
        detected_at: new Date().toISOString()
      };
      publish(result);
      found++;
    }
    return found;
  }

  function inspectResponseUrl(url, body, source) {
    if (!/\/(aweme|feed|detail|search|discover|recommend)\b/i.test(url || "")) return;
    inspectJsonText(body, `${source}:${url}`);
  }

  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    const response = await originalFetch.apply(this, args);
    const request = args[0];
    const url = typeof request === "string" ? request : request?.url || response.url;
    const contentType = response.headers.get("content-type") || "";
    if (response.ok && /application\/json/i.test(contentType)) {
      response.clone().text().then((body) => inspectResponseUrl(url, body, "fetch")).catch(() => {});
    }
    return response;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__aixDouyinRequestUrl = url;
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", function () {
      const contentType = this.getResponseHeader("content-type") || "";
      if (this.status >= 200 && this.status < 300 && /application\/json/i.test(contentType)) {
        const url = this.__aixDouyinRequestUrl || this.responseURL;
        if (this.responseType === "json" && this.response) {
          inspect(this.response, `xhr:${url}`);
        } else if (!this.responseType || this.responseType === "text") {
          inspectResponseUrl(url, this.responseText, "xhr");
        }
      }
    }, { once: true });
    return originalSend.apply(this, args);
  };

  window.__AixDouyinAutomation = {
    version: BRIDGE_VERSION,
    health: () => ({
      status: "ready",
      version: BRIDGE_VERSION,
      result_count: results.size,
      page_url: location.href
    }),
    getLatest: () => lastResult,
    getAll: () => [...results.values()],
    getByAwemeId: (awemeId) => {
      const expectedId = String(awemeId || "");
      return [...results.values()].reverse().find(
        (item) => item.status === "success" && String(item.aweme_id || "") === expectedId
      ) || null;
    },
    rescan: () => {
      const found = inspectDocument();
      return found ? lastResult : reportError("No direct Douyin video URL was found on this page yet.", "rescan");
    }
  };

  window.addEventListener("aix:douyin-rescan", () => window.__AixDouyinAutomation.rescan());
  document.addEventListener("DOMContentLoaded", inspectDocument, { once: true });
  let rescanTimer = null;
  new MutationObserver(() => {
    clearTimeout(rescanTimer);
    rescanTimer = setTimeout(inspectDocument, 200);
  }).observe(document, { childList: true, subtree: true });
})();
