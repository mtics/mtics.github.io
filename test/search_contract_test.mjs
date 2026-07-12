import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const siteRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

function readSiteFile(relativePath) {
  const absolutePath = path.join(siteRoot, relativePath);
  assert.equal(
    fs.existsSync(absolutePath),
    true,
    `${relativePath} must be provided by the site`,
  );
  return fs.readFileSync(absolutePath, "utf8");
}

function classList() {
  const classes = new Set();
  return {
    add: (...names) => names.forEach((name) => classes.add(name)),
    contains: (name) => classes.has(name),
    remove: (...names) => names.forEach((name) => classes.delete(name)),
  };
}

test("bibliography search debounces with a callable and filters only the latest input", () => {
  const source = readSiteFile("assets/js/bibsearch.js");

  assert.doesNotMatch(
    source,
    /setTimeout\s*\(\s*filterItems\s*\(/,
    "passing filterItems(...) to setTimeout invokes it immediately and triggers CSP string evaluation",
  );

  const publications = [
    { innerText: "Federated recommendation", classList: classList() },
    { innerText: "Privacy preserving systems", classList: classList() },
    { innerText: "Graph learning", classList: classList() },
  ];
  const inputListeners = new Map();
  const input = {
    value: "",
    addEventListener(type, listener) {
      inputListeners.set(type, listener);
    },
  };
  let domReady;
  let publicationFilterRuns = 0;
  const document = {
    addEventListener(type, listener) {
      if (type === "DOMContentLoaded") domReady = listener;
    },
    getElementById(id) {
      assert.equal(id, "bibsearch");
      return input;
    },
    querySelectorAll(selector) {
      if (selector === ".bibliography, .unloaded") return publications;
      if (selector === ".bibliography > li") {
        publicationFilterRuns += 1;
        return publications;
      }
      if (selector === "h2.bibliography") return [];
      throw new Error(`Unexpected selector: ${selector}`);
    },
  };
  const timers = new Map();
  let nextTimerId = 0;
  const context = {
    CSS: { highlights: undefined },
    clearTimeout(timerId) {
      timers.delete(timerId);
    },
    document,
    setTimeout(callback, delay) {
      assert.equal(
        typeof callback,
        "function",
        "setTimeout must receive a function under a strict CSP",
      );
      const timerId = ++nextTimerId;
      timers.set(timerId, { callback, delay });
      return timerId;
    },
    window: {
      addEventListener() {},
      location: { hash: "" },
    },
  };

  const executableSource = source.replace(
    /^\s*import[\s\S]*?from\s*["'][^"']+["'];?/,
    "",
  );
  vm.runInNewContext(executableSource, context, {
    filename: "assets/js/bibsearch.js",
  });
  assert.equal(typeof domReady, "function");
  domReady();

  assert.equal(publicationFilterRuns, 1, "initial hash filtering runs once");
  input.value = "federated";
  inputListeners.get("input").call(input);
  input.value = "privacy";
  inputListeners.get("input").call(input);

  assert.equal(
    publicationFilterRuns,
    1,
    "input must not filter before the debounce expires",
  );
  assert.equal(timers.size, 1, "the second input must cancel the first timer");

  const [{ callback, delay }] = timers.values();
  assert.equal(delay, 300);
  callback();

  assert.equal(publicationFilterRuns, 2, "only the final input is filtered");
  assert.equal(publications[0].classList.contains("unloaded"), true);
  assert.equal(publications[1].classList.contains("unloaded"), false);
  assert.equal(publications[2].classList.contains("unloaded"), true);
});

test("a hash update cancels pending input before applying the hash filter", () => {
  const source = readSiteFile("assets/js/bibsearch.js");
  const publications = [
    { innerText: "Federated recommendation", classList: classList() },
    { innerText: "Privacy preserving systems", classList: classList() },
  ];
  const inputListeners = new Map();
  const windowListeners = new Map();
  const timers = new Map();
  const input = {
    value: "",
    addEventListener(type, listener) {
      inputListeners.set(type, listener);
    },
  };
  let domReady;
  let nextTimerId = 0;
  const context = {
    CSS: { highlights: undefined },
    clearTimeout(timerId) {
      timers.delete(timerId);
    },
    document: {
      addEventListener(type, listener) {
        if (type === "DOMContentLoaded") domReady = listener;
      },
      getElementById(id) {
        assert.equal(id, "bibsearch");
        return input;
      },
      querySelectorAll(selector) {
        if (selector === ".bibliography, .unloaded") return publications;
        if (selector === ".bibliography > li") return publications;
        if (selector === "h2.bibliography") return [];
        throw new Error(`Unexpected selector: ${selector}`);
      },
    },
    setTimeout(callback, delay) {
      const timerId = ++nextTimerId;
      timers.set(timerId, { callback, delay });
      return timerId;
    },
    window: {
      addEventListener(type, listener) {
        windowListeners.set(type, listener);
      },
      location: { hash: "" },
    },
  };
  const executableSource = source.replace(
    /^\s*import[\s\S]*?from\s*["'][^"']+["'];?/,
    "",
  );

  vm.runInNewContext(executableSource, context, {
    filename: "assets/js/bibsearch.js",
  });
  domReady();

  input.value = "federated";
  inputListeners.get("input").call(input);
  assert.equal(timers.size, 1, "input schedules a pending filter");

  context.window.location.hash = "#PRIVACY";
  windowListeners.get("hashchange")();

  const callbacksStillPending = Array.from(
    timers.values(),
    ({ callback }) => callback,
  );
  timers.clear();
  callbacksStillPending.forEach((callback) => callback());

  assert.deepEqual(
    {
      pendingAfterHash: callbacksStillPending.length,
      inputValue: input.value,
      visiblePublications: publications
        .filter(({ classList }) => !classList.contains("unloaded"))
        .map(({ innerText }) => innerText),
    },
    {
      pendingAfterHash: 0,
      inputValue: "PRIVACY",
      visiblePublications: ["Privacy preserving systems"],
    },
  );

  context.window.location.hash = "#%E0%A4%A";
  assert.doesNotThrow(
    () => windowListeners.get("hashchange")(),
    "a malformed percent-encoded hash must not disable bibliography search",
  );
  assert.equal(input.value, "%E0%A4%A");
});

test("global search removes non-navigable News while preserving working actions", () => {
  const include = readSiteFile("_includes/plugins/al_search_assets.liquid");
  const filterSource = readSiteFile("assets/js/search-result-filter.js");
  const pluginTagPosition = include.indexOf("{% al_search_assets %}");
  const filterAssetPosition = include.indexOf(
    "/assets/js/search-result-filter.js",
  );

  assert.notEqual(
    pluginTagPosition,
    -1,
    "the al_search assets must still be rendered",
  );
  assert.ok(
    filterAssetPosition > pluginTagPosition,
    "the result filter must run after al_search populates ninja.data",
  );

  const opened = [];
  const ninja = {
    data: [
      { id: "news-inline", section: "News", title: "Inline announcement" },
      {
        id: "news-linked",
        section: "News",
        title: "Linked announcement",
        handler: () => opened.push("news"),
      },
      {
        id: "nav-publications",
        section: "Navigation",
        title: "Publications",
        handler: () => opened.push("publications"),
      },
    ],
  };

  vm.runInNewContext(
    filterSource,
    {
      document: {
        querySelector(selector) {
          assert.equal(selector, "ninja-keys");
          return ninja;
        },
      },
    },
    { filename: "assets/js/search-result-filter.js" },
  );

  assert.deepEqual(
    Array.from(ninja.data, ({ id }) => id),
    ["news-linked", "nav-publications"],
    "handler-less inline News must not remain selectable",
  );
  ninja.data.forEach(({ handler }) => handler());
  assert.deepEqual(
    opened,
    ["news", "publications"],
    "remaining results must still open through their handlers",
  );
});
