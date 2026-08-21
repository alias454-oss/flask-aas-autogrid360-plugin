// static/scroll.js
(function () {
  "use strict";

  const STORAGE_KEY = "autogrid360:post-scroll";
  const MAX_AGE_MS = 5 * 60 * 1000;

  function clearSavedPosition() {
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (_error) {
      // Storage may be unavailable; scroll restoration is optional enhancement.
    }
  }

  function savePosition(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented) {
      return;
    }

    const method = event.submitter?.formMethod || form.method;
    const target = event.submitter?.formTarget || form.target;
    if (method.toLowerCase() !== "post") {
      return;
    }
    if (form.hasAttribute("data-no-scroll-restore")) {
      return;
    }
    if (target && target !== "_self") {
      return;
    }

    try {
      window.sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          y: window.scrollY,
          savedAt: Date.now(),
        })
      );
    } catch (_error) {
      // Storage may be unavailable; allow the normal form submission to continue.
    }
  }

  function restorePosition() {
    let saved;
    try {
      const raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return;
      }
      window.sessionStorage.removeItem(STORAGE_KEY);
      saved = JSON.parse(raw);
    } catch (_error) {
      clearSavedPosition();
      return;
    }

    if (
      !saved ||
      !Number.isFinite(saved.y) ||
      !Number.isFinite(saved.savedAt) ||
      Date.now() - saved.savedAt > MAX_AGE_MS
    ) {
      return;
    }

    const maxY = Math.max(
      0,
      document.documentElement.scrollHeight - window.innerHeight
    );
    const targetY = Math.min(Math.max(0, saved.y), maxY);

    window.requestAnimationFrame(function () {
      window.scrollTo({ left: 0, top: targetY, behavior: "auto" });
    });
  }

  document.addEventListener("submit", savePosition);
  window.addEventListener("pageshow", restorePosition);
})();
