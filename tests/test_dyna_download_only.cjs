const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const integrationSource = fs.readFileSync(path.resolve(
  __dirname,
  "..",
  "extensions",
  "douyin_downloader_9.0.58",
  "content",
  "dyna-original-integration.js",
), "utf8");
const integrationCss = fs.readFileSync(path.resolve(
  __dirname,
  "..",
  "extensions",
  "douyin_downloader_9.0.58",
  "content",
  "dyna-integration.css",
), "utf8");
assert.match(
  integrationSource,
  /dyna-download-only"[^>]*>Chỉ tải video<\/button>[\s\S]*dyna-cancel/,
);
assert.match(integrationCss, /\.dyna-download-only\s*\{[^}]*margin-right:\s*auto/);
assert.match(integrationCss, /\.dyna-download-only\s*\{[^}]*linear-gradient/);

let runtimeListener = null;
let downloadOptions = null;
let fetchCalled = false;
const stored = {};

const context = {
  chrome: {
    runtime: {
      id: "dyna-test-extension",
      lastError: null,
      onMessage: {
        addListener(listener) {
          runtimeListener = listener;
        },
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
    downloads: {
      download(options, callback) {
        downloadOptions = options;
        callback(91);
      },
      search(_query, callback) {
        callback([]);
      },
      onChanged: {
        addListener() {},
      },
    },
    tabs: {
      sendMessage() {},
    },
  },
  fetch() {
    fetchCalled = true;
    throw new Error("download-only must not contact Dyna");
  },
  Date,
  Error,
  JSON,
  Map,
  Number,
  Promise,
  Set,
  String,
  console,
};

const sourcePath = path.resolve(
  __dirname,
  "..",
  "extensions",
  "douyin_downloader_9.0.58",
  "dyna-background.js",
);
vm.runInNewContext(fs.readFileSync(sourcePath, "utf8"), context);
assert.equal(typeof runtimeListener, "function");

new Promise((resolve, reject) => {
  const keepAlive = runtimeListener(
    {
      type: "dyna:start-download-only",
      metadata: {
        aweme_id: "7662743312826191150",
        download_url: "https://v.douyin.com/video.mp4",
        filename: "My Douyin video.mp4",
      },
    },
    { tab: { id: 7 } },
    (response) => {
      try {
        assert.equal(response.ok, true);
        assert.equal(response.downloadId, 91);
        assert.equal(fetchCalled, false);
        assert.deepEqual(
          JSON.parse(JSON.stringify(downloadOptions)),
          {
            url: "https://v.douyin.com/video.mp4",
            filename: "Dyna/Chi tai video/My Douyin video.mp4",
            conflictAction: "uniquify",
            saveAs: false,
          },
        );
        resolve();
      } catch (error) {
        reject(error);
      }
    },
  );
  assert.equal(keepAlive, true);
}).then(() => {
  console.log("Dyna download-only background simulation passed.");
});
