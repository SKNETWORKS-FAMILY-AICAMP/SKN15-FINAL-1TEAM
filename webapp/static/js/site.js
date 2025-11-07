// SB: sidebar controller script
(function () {
  const storageKey = "sb:sidebar-state";
  const focusSelector = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type='hidden'])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "[tabindex]:not([tabindex='-1'])"
  ].join(",");

  const body = document.body;
  const toggle = document.querySelector("[data-sb-toggle]");
  const sidebar = document.querySelector("[data-sb-sidebar]");
  const overlay = document.querySelector("[data-sb-overlay]");
  const focusContainer = sidebar ? sidebar.querySelector("[data-sb-focus-container]") : null;

  if (!toggle || !sidebar) {
    return;
  }

  let isOpen = false;
  let lastActiveElement = null;
  const mediaQuery = window.matchMedia("(max-width: 768px)");

  const readStorage = () => {
    try {
      return localStorage.getItem(storageKey);
    } catch (_) {
      return null;
    }
  };

  const writeStorage = (state) => {
    try {
      localStorage.setItem(storageKey, state ? "open" : "closed");
    } catch (_) {
      /* ignore */
    }
  };

  const getFocusable = () =>
    Array.from(sidebar.querySelectorAll(focusSelector)).filter((el) => {
      if (el.hasAttribute("disabled")) return false;
      if (el.getAttribute("aria-hidden") === "true") return false;
      if (el.tabIndex < 0) return false;
      if (el.offsetParent === null && getComputedStyle(el).position !== "fixed") return false;
      return true;
    });

  const clampFocus = (event) => {
    if (!isOpen || event.key !== "Tab") return;
    const focusable = getFocusable();
    if (focusable.length === 0) {
      event.preventDefault();
      (focusContainer || sidebar).focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;

    if (event.shiftKey) {
      if (active === first || active === sidebar) {
        event.preventDefault();
        last.focus();
      }
    } else if (active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const syncAria = (state) => {
    toggle.setAttribute("aria-expanded", state ? "true" : "false");
    sidebar.setAttribute("aria-hidden", state ? "false" : "true");
    toggle.setAttribute("aria-label", state ? "사이드바 닫기" : "사이드바 열기");
  };

  const showOverlay = (state) => {
    if (!overlay) return;
    if (state) {
      overlay.removeAttribute("hidden");
    } else {
      overlay.setAttribute("hidden", "");
    }
  };

  const focusInitial = () => {
    const target =
      sidebar.querySelector("[data-sb-focus-initial]") ||
      focusContainer ||
      sidebar;
    window.requestAnimationFrame(() => {
      target.focus();
    });
  };

  const openSidebar = ({ skipStorage = false } = {}) => {
    isOpen = true;
    body.classList.add("sb-open");
    syncAria(true);
    showOverlay(true);
    sidebar.addEventListener("keydown", clampFocus);
    focusInitial();
    if (!skipStorage) writeStorage(true);
  };

  const closeSidebar = ({ restoreFocus = false, skipStorage = false } = {}) => {
    isOpen = false;
    body.classList.remove("sb-open");
    syncAria(false);
    showOverlay(false);
    sidebar.removeEventListener("keydown", clampFocus);
    if (restoreFocus) {
      const hasLast = lastActiveElement instanceof HTMLElement && document.contains(lastActiveElement);
      (hasLast ? lastActiveElement : toggle).focus();
    }
    lastActiveElement = null;
    if (!skipStorage) writeStorage(false);
  };

  const applyState = (state, options = {}) => {
    if (state) {
      openSidebar(options);
    } else {
      closeSidebar(options);
    }
  };

  const initialState = (() => {
    const stored = readStorage();
    if (stored === "open") return true;
    if (stored === "closed") return false;
    return mediaQuery.matches ? false : true;
  })();

  body.classList.add("sb-no-animate");
  applyState(initialState, { skipStorage: true });
  window.requestAnimationFrame(() => {
    body.classList.remove("sb-no-animate");
  });

  toggle.addEventListener("click", () => {
    lastActiveElement = document.activeElement;
    applyState(!isOpen, { restoreFocus: true });
  });

  overlay?.addEventListener("click", () => {
    applyState(false, { restoreFocus: true });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen) {
      event.preventDefault();
      applyState(false, { restoreFocus: true });
    }
  });

  const handleMediaChange = () => {
    body.classList.add("sb-no-animate");
    applyState(isOpen, { skipStorage: true });
    window.requestAnimationFrame(() => {
      body.classList.remove("sb-no-animate");
    });
  };

  if (typeof mediaQuery.addEventListener === "function") {
    mediaQuery.addEventListener("change", handleMediaChange);
  } else if (typeof mediaQuery.addListener === "function") {
    mediaQuery.addListener(handleMediaChange);
  }
})();
