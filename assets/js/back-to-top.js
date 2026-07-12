(() => {
  const initializeBackToTop = () => {
    const button = document.getElementById("back-to-top");
    if (!button) return;

    const updateVisibility = () => {
      button.hidden = window.scrollY < 1;
    };

    button.addEventListener("click", () => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
    });
    window.addEventListener("scroll", updateVisibility, { passive: true });
    updateVisibility();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeBackToTop, { once: true });
  } else {
    initializeBackToTop();
  }
})();
