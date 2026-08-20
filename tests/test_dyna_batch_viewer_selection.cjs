const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("../desktop/node_modules/jsdom");

const videoId = "7662743312826191150";
const dom = new JSDOM(
  `<!doctype html><html><body>
    <a class="video-card" href="/video/${videoId}">
      <img src="https://p3-pc-sign.douyinpic.com/cover.jpeg" alt="">
      <span>Original caption</span>
    </a>
    <script id="aix-douyin-download-results" type="application/json">[
      {
        "aweme_id": "${videoId}",
        "description": "Captured original caption",
        "thumbnail_url": "https://p3-pc-sign.douyinpic.com/captured.jpeg",
        "download_url": "https://v.douyin.com/video.mp4",
        "download_urls": ["https://v.douyin.com/video.mp4"]
      }
    ]</script>
  </body></html>`,
  {
    url: `https://www.douyin.com/user/source?modal_id=${videoId}`,
    runScripts: "outside-only",
  },
);

const stored = {};
dom.window.chrome = {
  runtime: {
    lastError: null,
    sendMessage(message, callback) {
      callback({
        ok: true,
        session: message.type === "dyna:get-active-selection"
          ? {
              id: "selection-viewer",
              source_url: "https://www.douyin.com/user/source",
              status: "selecting",
            }
          : undefined,
      });
    },
  },
  storage: {
    local: {
      get(key, callback) {
        callback({ [key]: stored[key] });
      },
      set(value, callback) {
        Object.assign(stored, value);
        callback();
      },
      remove(key, callback) {
        delete stored[key];
        callback();
      },
    },
  },
};
dom.window.setInterval = () => 1;

const sourcePath = path.resolve(
  __dirname,
  "..",
  "extensions",
  "douyin_downloader_9.0.58",
  "content",
  "dyna-batch-selection.js",
);
dom.window.eval(fs.readFileSync(sourcePath, "utf8"));

setTimeout(() => {
  const cardButton = dom.window.document.querySelector(".dyna-batch-card-select");
  const viewerButton = dom.window.document.querySelector(".dyna-batch-viewer-select");
  assert.ok(cardButton?.classList.contains("viewer-suppressed"));
  assert.equal(viewerButton.hidden, false);
  assert.equal(viewerButton.dataset.videoId, videoId);
  assert.equal(viewerButton.textContent, "+ Chọn video này");

  viewerButton.click();
  setTimeout(() => {
    assert.equal(
      dom.window.document.querySelector(".dyna-batch-count").textContent,
      "Đã chọn 1/50",
    );
    assert.equal(viewerButton.textContent, "1 · Đã chọn");
    assert.equal(stored["dyna:selection:selection-viewer"].items[0].thumbnail_url,
      "https://p3-pc-sign.douyinpic.com/captured.jpeg");
    console.log("Douyin viewer selection simulation passed.");
    dom.window.close();
  }, 0);
}, 0);
