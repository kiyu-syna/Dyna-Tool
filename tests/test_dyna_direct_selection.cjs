const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("../desktop/node_modules/jsdom");

const videoId = "7662743312826191150";
const sourceUrl = "https://www.douyin.com/user/source";
const dom = new JSDOM(
  `<!doctype html><html><body>
    <div class="profile-tabs">
      <button><span>作品</span><b>74</b></button>
      <button><span>推荐</span></button>
      <button><span>喜欢</span></button>
    </div>
    <div class="video-surface">
      <a class="video-card" href="/video/${videoId}">
        <img src="https://p3-pc-sign.douyinpic.com/cover.jpeg" alt="">
        <span>Original caption</span>
      </a>
      <div class="dy-download-wrapper" data-video-id="${videoId}">
        <button
          class="dy-download-button"
          data-dyna-integrated="true"
          data-dyna-video-id="${videoId}"
        ></button>
      </div>
    </div>
  </body></html>`,
  { url: sourceUrl, runScripts: "outside-only" },
);

const messages = [];
const stored = {};
dom.window.chrome = {
  runtime: {
    lastError: null,
    sendMessage(message, callback) {
      messages.push(message);
      if (message.type === "dyna:create-selection") {
        callback({
          ok: true,
          session: {
            id: "selection-direct",
            source_url: sourceUrl,
            status: "selecting",
          },
        });
        return;
      }
      callback({ ok: true, session: null });
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
dom.window.document.querySelector(".dy-download-button").getBoundingClientRect = () => ({
  width: 40,
  height: 40,
  top: 10,
  right: 50,
  bottom: 50,
  left: 10,
  x: 10,
  y: 10,
  toJSON() {
    return this;
  },
});

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
  const directButton = dom.window.document.querySelector(".dyna-batch-direct-start");
  assert.ok(directButton, "the profile tab bar receives the direct selection button");
  assert.equal(directButton.textContent, "Chọn nhiều video & đăng Dyna tùy chỉnh");
  directButton.click();

  setTimeout(() => {
    const createMessage = messages.find((message) => message.type === "dyna:create-selection");
    assert.equal(createMessage.sourceUrl, sourceUrl);
    assert.equal(directButton.textContent, "Đang chọn video (0/50)");

    const card = dom.window.document.querySelector(".video-card");
    const surface = dom.window.document.querySelector(".video-surface");
    const selectButton = surface.querySelector(".dyna-batch-card-select");
    assert.ok(selectButton);
    assert.equal(
      selectButton.parentElement,
      surface,
      "the selection control shares the stable overlay host used by the Dyna download logo",
    );
    assert.equal(
      selectButton.classList.contains("viewer-suppressed"),
      false,
      "a download logo on a profile card is not mistaken for the full video viewer",
    );
    const preview = dom.window.document.createElement("video");
    preview.muted = true;
    card.replaceChildren(preview);

    setTimeout(() => {
      assert.ok(
        surface.querySelector(".dyna-batch-card-select"),
        "the selection control stays on the stable overlay when Douyin swaps in a preview video",
      );
      console.log("Direct Douyin selection and preview replacement simulation passed.");
      dom.window.close();
    }, 260);
  }, 0);
}, 0);
