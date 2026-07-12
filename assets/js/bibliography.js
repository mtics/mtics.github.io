// Bibliography interactions are initialized here so rendered author data never
// needs to be interpolated into executable inline JavaScript.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-bib-disclosure]").forEach((control) => {
    const activate = (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();

      const links = control.closest(".links");
      const scope = links?.parentElement;
      const panelId = control.getAttribute("aria-controls");
      const panel = panelId ? document.getElementById(panelId) : null;
      if (!links || !scope || !panel) return;

      const shouldOpen = control.getAttribute("aria-expanded") !== "true";
      scope.querySelectorAll(".abstract.hidden.open, .award.hidden.open, .bibtex.hidden.open").forEach((openPanel) => {
        openPanel.classList.remove("open");
      });
      links.querySelectorAll("[data-bib-disclosure]").forEach((otherControl) => {
        otherControl.setAttribute("aria-expanded", "false");
      });

      panel.classList.toggle("open", shouldOpen);
      control.setAttribute("aria-expanded", String(shouldOpen));
    };

    control.addEventListener("click", activate);
    if (control.tagName === "A") {
      control.addEventListener("keydown", (event) => {
        if (event.key === " ") {
          event.preventDefault();
          control.click();
        }
      });
    }
  });

  document.querySelectorAll("[data-more-authors-toggle]").forEach((button) => {
    const collapsed = button.querySelector("[data-more-authors-collapsed]");
    const expanded = button.querySelector("[data-more-authors-expanded]");
    if (!collapsed || !expanded) return;

    button.addEventListener("click", () => {
      const shouldExpand = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(shouldExpand));
      button.setAttribute("aria-label", shouldExpand ? "Hide additional authors" : `Show ${collapsed.textContent.trim()}`);
      collapsed.hidden = shouldExpand;
      expanded.hidden = !shouldExpand;
    });
  });
});
