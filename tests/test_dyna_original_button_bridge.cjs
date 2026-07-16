const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class MockElement {
  constructor(mediaList, id) {
    this.dataset = {};
    this.innerHTML = "<img alt=download>";
    this.title = "";
    this.listeners = {};
    this.__videoData = {
      id,
      getMediaList: () => mediaList,
    };
  }

  matches(selector) {
    return selector === ".dy-download-button";
  }

  querySelectorAll() {
    return [];
  }

  querySelector() {
    return null;
  }

  setAttribute(name, value) {
    this[name] = value;
  }

  addEventListener(type, handler, capture) {
    this.listeners[type] = { handler, capture };
  }
}

const videoButton = new MockElement([
  {
    type: "video",
    url: "https://v.douyin.com/high.mp4",
    filename: "author-video",
    resolution: "1080p",
    format: "mp4",
    hasSound: true,
  },
  {
    type: "video",
    url: "https://v.douyin.com/low.mp4",
    filename: "author-video",
    resolution: "720p",
    format: "mp4",
    hasSound: true,
  },
], "7662743312826191150_modal");
const imageButton = new MockElement([
  { type: "image", url: ["https://p.douyin.com/1.jpeg"] },
], "7662000000000000000");
const posted = [];

const context = {
  console,
  URL,
  Element: MockElement,
  MutationObserver: class {
    observe() {}
  },
  document: {
    documentElement: {},
    querySelectorAll: () => [videoButton, imageButton],
  },
  location: {
    href: "https://www.douyin.com/?recommend=1",
  },
  setInterval: () => 1,
  addEventListener: () => {},
};
context.window = {
  postMessage: (message) => posted.push(message),
};

const sourcePath = path.resolve(
  __dirname,
  "..",
  "extensions",
  "douyin_downloader_9.0.58",
  "content",
  "dyna-original-button-bridge.js",
);
vm.runInNewContext(fs.readFileSync(sourcePath, "utf8"), context, { filename: sourcePath });

assert.equal(videoButton.dataset.dynaIntegrated, "true");
assert.equal(videoButton.dataset.dynaVideoId, "7662743312826191150");
assert.match(videoButton.innerHTML, /assets\/dyna-button-logo\.png/);
assert.doesNotMatch(videoButton.innerHTML, /dyna-original-mark/);
assert.equal(videoButton.listeners.click.capture, true);
assert.equal(imageButton.dataset.dynaIntegrated, undefined);
assert.equal(imageButton.listeners.click, undefined);

const eventState = { prevented: false, stopped: false, immediate: false };
videoButton.listeners.click.handler({
  preventDefault: () => { eventState.prevented = true; },
  stopPropagation: () => { eventState.stopped = true; },
  stopImmediatePropagation: () => { eventState.immediate = true; },
});

assert.deepEqual(eventState, { prevented: true, stopped: true, immediate: true });
assert.equal(posted.length, 1);
assert.equal(posted[0].type, "dyna:original-download-request");
assert.equal(posted[0].payload.aweme_id, "7662743312826191150");
assert.equal(posted[0].payload.download_url, "https://v.douyin.com/high.mp4");
assert.equal(posted[0].payload.resolution, "1080p");
assert.equal(posted[0].payload.available_video_qualities, 2);
assert.equal(
  posted[0].payload.selection_method,
  "original_extension_getMediaList_highest_resolution",
);

console.log("Original Douyin button bridge simulation passed.");
