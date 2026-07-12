(() => {
  const ninjaKeys = document.querySelector("ninja-keys");
  if (!Array.isArray(ninjaKeys?.data)) return;

  // al_search emits inline collection items without a handler. Keep those
  // announcements on the News page, but do not expose them as inert actions.
  ninjaKeys.data = ninjaKeys.data.filter(
    (item) => item.section !== "News" || typeof item.handler === "function",
  );
})();
