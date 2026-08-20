const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("../desktop/node_modules/jsdom");

const dom = new JSDOM(
  `<!doctype html><html><body>
    <main>
      <a class="video-card" href="/video/7662743312826191150">
        <img src="https://p.douyin.com/cover.jpeg" alt="">
        <span>Original caption</span>
      </a>
    </main>
    <script id="aix-douyin-download-results" type="application/json">[
      {
        "aweme_id": "7662743312826191150",
        "description": "Captured original caption",
        "download_url": "https://v.douyin.com/video.mp4",
        "download_urls": ["https://v.douyin.com/video.mp4"]
      }
    ]</script>
  </body></html>`,
  {
    url: "https://www.douyin.com/user/source",
    runScripts: "outside-only",
  },
);

const messages = [];
const stored = {};
dom.window.chrome = {
  runtime: {
    lastError: null,
    sendMessage(message, callback) {
      messages.push(message);
      if (message.type === "dyna:get-active-selection") {
        callback({
          ok: true,
          session: {
            id: "selection-1",
            source_url: "https://www.douyin.com/user/source",
            status: "selecting",
          },
        });
      } else {
        callback({ ok: true });
      }
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
  const selectButton = dom.window.document.querySelector(".dyna-batch-card-select");
  assert.ok(selectButton, "the Douyin card receives a Dyna selection button");
  selectButton.click();

  setTimeout(() => {
    assert.equal(
      dom.window.document.querySelector(".dyna-batch-count").textContent,
      "Đã chọn 1/50",
    );
    dom.window.document.querySelector(".dyna-batch-send").click();

    setTimeout(() => {
      const complete = messages.find((message) => message.type === "dyna:complete-selection");
      assert.ok(complete, "the selected batch is sent to Dyna");
      assert.equal(complete.sessionId, "selection-1");
      assert.equal(complete.items.length, 1);
      assert.equal(complete.items[0].video_id, "7662743312826191150");
      assert.equal(complete.items[0].description, "Captured original caption");
      assert.equal(complete.items[0].download_url, "https://v.douyin.com/video.mp4");
      console.log("Douyin batch selection simulation passed.");
    }, 450);
  }, 0);
}, 0);
