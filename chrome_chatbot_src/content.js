console.log('Content script loaded (콘텐츠 스크립트 로드됨)');

/* ---------------------------
   1️⃣ Inject styles once (중복 방지로 스타일 1회만 주입)
--------------------------- */
if (!window.GUIDE_STYLES) {
  window.GUIDE_STYLES = `
    .guide-overlay {
      position: fixed;
      top: 0; left: 0;
      width: 100vw; height: 100vh;
      background: rgba(0, 0, 0, 0.35);
      z-index: 1000000;
      pointer-events: none; /* allow page scroll/click through */
    }
    .guide-highlight {
      position: absolute;
      border: 3px solid #2196F3;
      box-sizing: border-box;
      border-radius: 8px;
      z-index: 1000001;
      pointer-events: none;
      animation: pulse 1.4s ease-in-out infinite;
    }
    .guide-label {
      position: absolute;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 6px;
      background: #2196F3;
      color: #fff;
      font-size: 14px;
      font-weight: 600;
      line-height: 1.2;
      z-index: 1000003;
      pointer-events: none;
      box-shadow: 0 4px 14px rgba(0,0,0,0.25);
      max-width: 60vw;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .guide-label.above::after,
    .guide-label.below::after {
      content: "";
      position: absolute;
      left: 16px;
      border: 6px solid transparent;
    }
    .guide-label.above::after { /* label above the target: point down */
      top: 100%;
      border-top-color: #2196F3;
      margin-top: 0px;
    }
    .guide-label.below::after { /* label below the target: point up */
      bottom: 100%;
      border-bottom-color: #2196F3;
      margin-bottom: 0px;
    }
    .guide-label .num {
      width: 24px; height: 24px;
      display: inline-flex;
      align-items: center; justify-content: center;
      border-radius: 50%;
      background: rgba(255,255,255,0.2);
      font-size: 13px;
    }
    @keyframes pulse {
      0% { transform: scale(1); opacity: 1; }
      50% { transform: scale(1.03); opacity: 0.9; }
      100% { transform: scale(1); opacity: 1; }
    }
  `;
}

if (!document.getElementById("guide-style-tag")) {
  const styleTag = document.createElement('style');
  styleTag.id = "guide-style-tag";
  styleTag.textContent = window.GUIDE_STYLES;
  document.head.appendChild(styleTag);
}

// Hook URL changes (pushState/replaceState/popstate) to clear stale overlays
(() => {
  if (window.__guideHistoryHooked) return; window.__guideHistoryHooked = true;
  try {
    const wrap = (fnName) => {
      const orig = history[fnName];
      if (typeof orig !== 'function') return;
      history[fnName] = function() {
        const ret = orig.apply(this, arguments);
        try { removeGuides(); } catch {}
        return ret;
      };
    };
    wrap('pushState'); wrap('replaceState');
    window.addEventListener('popstate', () => { try { removeGuides(); } catch {} }, true);
  } catch {}
})();

/* ---------------------------
   2️⃣ Cleanup helpers (정리 유틸 함수)
--------------------------- */
function removeGuides() {
  document
    .querySelectorAll('.guide-overlay,.guide-highlight,.guide-badge,.guide-tooltip,.guide-canvas')
    .forEach(n => n.remove());
  if (window.__guideTimeout) {
    clearTimeout(window.__guideTimeout);
    window.__guideTimeout = null;
  }
  if (window.__guideRecalc) {
    window.removeEventListener('scroll', window.__guideRecalc, true);
    window.removeEventListener('resize', window.__guideRecalc, true);
    window.__guideRecalc = null;
  }
  if (window.__guideRafId) { try { cancelAnimationFrame(window.__guideRafId); } catch {}; window.__guideRafId = null; }
  if (Array.isArray(window.__guideExtraScrollTargets)) {
    try { window.__guideExtraScrollTargets.forEach(el => { try { el.removeEventListener('scroll', window.__guideRecalc, true); } catch {} }); } catch {}
    window.__guideExtraScrollTargets = [];
  }
  if (window.__guideClickHandler) {
    try { document.removeEventListener('click', window.__guideClickHandler, { capture: true, once: true }); } catch {}
    window.__guideClickHandler = null;
  }
  if (window.__guideEscHandler) {
    try { window.removeEventListener('keydown', window.__guideEscHandler, { once: true }); } catch {}
    window.__guideEscHandler = null;
  }
  window.__guideAnchors = null;
  window.__guideState = null;
}

// Return circled number like ① ② ③ (원형 번호 반환)
function getCircledNumber(index) {
  const circled = [
    "\u2460","\u2461","\u2462","\u2463","\u2464",
    "\u2465","\u2466","\u2467","\u2468","\u2469",
    "\u246A","\u246B","\u246C","\u246D","\u246E"
  ];
  if (index >= 1 && index <= circled.length) return circled[index - 1];
  return String(index);
}

/**
 * Render overlays from rects and labels
 * (좌표/라벨로 안내 오버레이 렌더링)
 * @param {Array<{x:number,y:number,width:number,height:number,label?:string}>} overlays
 */
function showGuides(overlays, opts = {}) {
  console.log('Showing guides for elements:', overlays);
  removeGuides();

  if (!Array.isArray(overlays) || overlays.length === 0) {
    return;
  }

  // Helper: choose best viewport rect among multiple coordinate conventions
  const dpr = Number(opts.dpr || window.devicePixelRatio || 1);
  const vw = window.innerWidth || document.documentElement.clientWidth || 0;
  const vh = window.innerHeight || document.documentElement.clientHeight || 0;
  const capturedSX = Number((opts && opts.scroll && typeof opts.scroll.x === 'number') ? opts.scroll.x : 0);
  const capturedSY = Number((opts && opts.scroll && typeof opts.scroll.y === 'number') ? opts.scroll.y : 0);
  // Expose captured scroll for diagnostics
  try { window.__guideCapturedScroll = { x: capturedSX, y: capturedSY }; } catch {}
  const coordSpace = String(opts.coord_space || 'document').toLowerCase();
  const vp = { width: vw, height: vh };
  const shot = opts.screenshot_info || null;
  try { window.__lastScreenshotInfo = shot || null; } catch {}
  // Use CURRENT viewport size for mapping because the screenshot background is rendered at 100% of current viewport
  const scaleX = (shot && shot.width) ? (shot.width / vp.width) : dpr;
  const scaleY = (shot && shot.height) ? (shot.height / vp.height) : dpr;
  // Helper to get current scroll and delta vs captured
  const getScrollState = () => {
    const winX = window.scrollX || 0;
    const winY = window.scrollY || 0;
    const docX = (document.scrollingElement && document.scrollingElement.scrollLeft) || 0;
    const docY = (document.scrollingElement && document.scrollingElement.scrollTop) || 0;
    // Use whichever reflects movement (inner scrolling containers on some sites bubble to document.scrollingElement)
    const curX = Math.max(winX, docX);
    const curY = Math.max(winY, docY);
    return { sx: curX, sy: curY, dx: curX - capturedSX, dy: curY - capturedSY };
  };

  // Send initial scroll info to the server terminal
  try {
    chrome.runtime?.sendMessage?.({
      action: 'sendToServer',
      type: 'scroll_info',
      payload: {
        captured: { x: capturedSX, y: capturedSY },
        current: { x: window.scrollX || 0, y: window.scrollY || 0 },
        delta: { dx: (window.scrollX||0) - capturedSX, dy: (window.scrollY||0) - capturedSY },
        coord_space: coordSpace,
        screenshot_pixels: shot ? { width: shot.width, height: shot.height } : null,
        viewport_css: { width: vw, height: vh, dpr }
      }
    });
  } catch {}

  const intersectionScore = (l, t, w, h) => {
    const x1 = Math.max(0, l), y1 = Math.max(0, t);
    const x2 = Math.min(vw, l + w), y2 = Math.min(vh, t + h);
    const iw = Math.max(0, x2 - x1), ih = Math.max(0, y2 - y1);
    const area = iw * ih;
    const tooHuge = w > vw * 1.5 || h > vh * 1.5;
    const tooTiny = w < 2 || h < 2;
    return (tooHuge || tooTiny) ? 0 : area;
  };

  const bestViewportRect = (ov) => {
    const ox = Number(ov.x ?? ov.left ?? 0);
    const oy = Number(ov.y ?? ov.top ?? 0);
    const ow = Math.max(1, Math.round(Number(ov.width || 0)));
    const oh = Math.max(1, Math.round(Number(ov.height || 0)));
    const { sx, sy, dx, dy } = getScrollState();
    // Deterministic mapping when coord_space is declared
    if (coordSpace === 'screenshot') {
      // Two hypotheses:
      // H1: model used full-page screenshot coords -> subtract captured scroll
      // H2: model used viewport-cropped screenshot coords -> do not subtract
      const h1 = {
        l: Math.round(ox / scaleX - capturedSX - dx),
        t: Math.round(oy / scaleY - capturedSY - dy),
        w: Math.round(ow / scaleX),
        h: Math.round(oh / scaleY)
      };
      const h2 = {
        l: Math.round(ox / scaleX - dx),
        t: Math.round(oy / scaleY - dy),
        w: Math.round(ow / scaleX),
        h: Math.round(oh / scaleY)
      };
      const s1 = intersectionScore(h1.l, h1.t, h1.w, h1.h);
      const s2 = intersectionScore(h2.l, h2.t, h2.w, h2.h);
      const pick = s2 > s1 ? h2 : h1;
      // clamp to viewport bounds
      pick.l = Math.max(-vw, Math.min(vw, pick.l));
      pick.t = Math.max(-vh, Math.min(vh, pick.t));
      pick.w = Math.max(1, Math.min(vw, pick.w));
      pick.h = Math.max(1, Math.min(vh, pick.h));
      return pick;
    } else if (coordSpace === 'viewport') {
      return { l: Math.round(ox), t: Math.round(oy), w: ow, h: oh };
    } else if (coordSpace === 'document') {
      return { l: Math.round(ox - sx), t: Math.round(oy - sy), w: ow, h: oh };
    }
    // Fallback: try multiple interpretations and choose the best
    const cand = [
      { l: Math.round(ox - sx), t: Math.round(oy - sy), w: ow, h: oh },
      { l: Math.round(ox),      t: Math.round(oy),      w: ow, h: oh },
      { l: Math.round(ox / dpr - sx), t: Math.round(oy / dpr - sy), w: Math.round(ow / dpr), h: Math.round(oh / dpr) },
      { l: Math.round(ox / dpr),      t: Math.round(oy / dpr),      w: Math.round(ow / dpr), h: Math.round(oh / dpr) }
    ];
    let best = cand[0], bestScore = intersectionScore(cand[0].l, cand[0].t, cand[0].w, cand[0].h);
    for (let i = 1; i < cand.length; i++) {
      const s = intersectionScore(cand[i].l, cand[i].t, cand[i].w, cand[i].h);
      if (s > bestScore) { best = cand[i]; bestScore = s; }
    }
    return best;
  };

  // Keep state for live recalc on scroll/resize
  window.__guideState = { overlays: JSON.parse(JSON.stringify(overlays)) };
  if (!Array.isArray(window.__guideExtraScrollTargets)) window.__guideExtraScrollTargets = [];
  window.__guideAnchors = [];

  // If first target appears off-screen, scroll toward it
  const first = overlays[0];
  const firstView = bestViewportRect(first);
  const needScroll = (() => {
    try {
      return (firstView.t < 40) || (firstView.t + firstView.h > vh - 40);
    } catch { return false; }
  })();

   const renderAll = () => {
     // Dimmed full-screen overlay background (반투명 전체 배경 오버레이)
    const overlay = document.createElement('div');
    overlay.className = 'guide-overlay';
    if (coordSpace === 'screenshot' && opts.screenshot) {
      overlay.style.backgroundImage = `url(${opts.screenshot})`;
      overlay.style.backgroundRepeat = 'no-repeat';
      const bgW = Math.round((shot && shot.width) ? (shot.width / scaleX) : vw);
      const bgH = Math.round((shot && shot.height) ? (shot.height / scaleY) : vh);
      overlay.style.backgroundSize = `${bgW}px ${bgH}px`;
      // Shift background to the captured scroll window
      overlay.style.backgroundPosition = `${-capturedSX}px ${-capturedSY}px`;
    }
    document.body.appendChild(overlay);
    // Allow full interaction with page (scroll/click-through). Provide ESC to close.
    const onEsc = (e) => { if (e.key === 'Escape') removeGuides(); };
    window.addEventListener('keydown', onEsc, { once: true });
    window.__guideEscHandler = onEsc;
    // Report a click as step completion to panel for continuation
    const clickHandler = (ev) => {
      if (!ev || !ev.isTrusted) return; // ignore synthetic clicks
      if (ev.defaultPrevented) return;
      try {
        chrome.runtime?.sendMessage?.({ action: 'guideStepCompleted', session_id: (opts && opts.session_id) || window.__guideSessionId || null, step_index: (opts && typeof opts.step_index==='number') ? opts.step_index : 0 });
      } catch {}
      // Dismiss current overlays immediately so new page/step can render fresh
      try { setTimeout(removeGuides, 10); } catch {}
    };
    document.addEventListener('click', clickHandler, { capture: true, once: true });
    window.__guideClickHandler = clickHandler;

    const recalc = () => {
      try {
        const overlayEl = document.querySelector('.guide-overlay');
        if (!overlayEl) return;
        const list = (window.__guideState && window.__guideState.overlays) ? window.__guideState.overlays : null;
        if (!Array.isArray(list)) return;
        // Optionally log when scroll delta changes by >= 2px
        try {
          const curDx = (window.scrollX - capturedSX);
          const curDy = (window.scrollY - capturedSY);
          const prev = window.__guidePrevDxDy || { dx: null, dy: null };
          if (prev.dx === null || Math.abs(curDx - prev.dx) >= 2 || Math.abs(curDy - prev.dy) >= 2) {
            chrome.runtime?.sendMessage?.({ action: 'sendToServer', type: 'scroll_info_tick', payload: { captured: { x: capturedSX, y: capturedSY }, current: { x: window.scrollX, y: window.scrollY }, delta: { dx: curDx, dy: curDy } } });
            window.__guidePrevDxDy = { dx: curDx, dy: curDy };
          }
        } catch {}
        const children = Array.from(overlayEl.querySelectorAll('.guide-highlight'));
        const labels = Array.from(overlayEl.querySelectorAll('.guide-label'));
        for (let i = 0; i < list.length; i++) {
          const ov = list[i] || {};
          let left, top, w, h;
          // Prefer anchor element tracking if available (handles inner scrolling and iframes)
          try {
            const a = window.__guideAnchors ? window.__guideAnchors[i] : null;
            if (a && a.el) {
              const r = a.el.getBoundingClientRect();
              if (a.iframeEl) {
                const ir = a.iframeEl.getBoundingClientRect();
                left = Math.round(ir.left + r.left); top = Math.round(ir.top + r.top); w = Math.round(r.width); h = Math.round(r.height);
              } else {
                left = Math.round(r.left); top = Math.round(r.top); w = Math.round(r.width); h = Math.round(r.height);
              }
            }
          } catch {}
          if (!isFinite(left) || !isFinite(top)) {
            const best = bestViewportRect(ov);
            left = best.l; top = best.t; w = best.w; h = best.h;
          }
          const hl = children[i]; const label = labels[i]; if (!hl || !label) continue;
          hl.style.left = `${left}px`;
          hl.style.top = `${top}px`;
          hl.style.width = `${w}px`;
          hl.style.height = `${h}px`;
          let labelLeft = Math.max(8, left);
          let labelTop = top - 40;
          let positionClass = 'above';
          if (labelTop < 8) { labelTop = top + h + 8; positionClass = 'below'; }
          label.classList.toggle('above', positionClass === 'above');
          label.classList.toggle('below', positionClass === 'below');
          try {
            const vw2 = window.innerWidth || document.documentElement.clientWidth || 0;
            const lw = label.offsetWidth || 0;
            if (labelLeft + lw + 8 > vw2) labelLeft = Math.max(8, vw2 - lw - 8);
          } catch {}
          label.style.left = `${labelLeft}px`;
          label.style.top = `${Math.max(8, labelTop)}px`;
        }
      } catch {}
    };

    // Initial DOM creation (guard against null state)
    const initList = (window.__guideState && Array.isArray(window.__guideState.overlays))
      ? window.__guideState.overlays
      : [];
    initList.forEach((ov, idx) => {
      if (!ov || typeof ov !== 'object') return;
      const best = bestViewportRect(ov);
      const left = best.l, top = best.t, w = best.w, h = best.h;
      if (!(isFinite(left)&&isFinite(top)&&isFinite(w)&&isFinite(h))) return;
      if (w < 2 || h < 2) return;

      // Highlight
      const hl = document.createElement('div');
      hl.className = 'guide-highlight';
      hl.style.left = `${left}px`;
      hl.style.top = `${top}px`;
      hl.style.width = `${w}px`;
      hl.style.height = `${h}px`;
      overlay.appendChild(hl);

      // Label
      const label = document.createElement('div');
      label.className = 'guide-label';
      const num = document.createElement('span');
      num.className = 'num';
      num.textContent = getCircledNumber(idx + 1);
      const text = document.createElement('span');
      text.className = 'text';
      text.textContent = (ov.label || `단계 ${idx + 1}`).toString();
      label.appendChild(num); label.appendChild(text);

      let labelLeft = Math.max(8, left);
      let labelTop = top - 40;
      let positionClass = 'above';
      if (labelTop < 8) { labelTop = top + h + 8; positionClass = 'below'; }
      label.classList.add(positionClass);
      overlay.appendChild(label);
      try {
        const vw = window.innerWidth || document.documentElement.clientWidth || 0;
        const lw = label.offsetWidth || 0;
        if (labelLeft + lw + 8 > vw) labelLeft = Math.max(8, vw - lw - 8);
      } catch {}
      label.style.left = `${labelLeft}px`;
      label.style.top = `${Math.max(8, labelTop)}px`;

      // Save a stable anchor if available
      try {
        window.__guideAnchors = window.__guideAnchors || [];
        let anchor = null, withinIframe = false, iframeEl = null;
        if (typeof ov.anchor_selector === 'string' && ov.anchor_selector) {
          try { anchor = document.querySelector(ov.anchor_selector); } catch {}
        }
        if (!anchor) {
          const cx = Math.max(1, Math.min((window.innerWidth||0)-2, left + w/2));
          const cy = Math.max(1, Math.min((window.innerHeight||0)-2, top + h/2));
          let hit = document.elementFromPoint(cx, cy);
          if (hit && hit.tagName === 'IFRAME') {
            const r = hit.getBoundingClientRect();
            try {
              const ax = cx - r.left, ay = cy - r.top; const innerDoc = hit.contentDocument; const innerWin = hit.contentWindow;
              if (innerDoc && innerWin) { const innerEl = innerDoc.elementFromPoint(Math.max(1, Math.min(innerWin.innerWidth-2, ax)), Math.max(1, Math.min(innerWin.innerHeight-2, ay))); if (innerEl) { anchor = innerEl; withinIframe = true; iframeEl = hit; } }
            } catch {}
          }
          if (!anchor) anchor = hit;
        }
        window.__guideAnchors[idx] = { el: anchor, withinIframe, iframeEl };
      } catch {}
    });

    // Attach live recalc listeners for both coordinate spaces
    window.__guideRecalc = () => recalc();
    window.addEventListener('scroll', window.__guideRecalc, { capture: true, passive: true });
    document.addEventListener('scroll', window.__guideRecalc, { capture: true, passive: true });
    try { document.scrollingElement && document.scrollingElement.addEventListener('scroll', window.__guideRecalc, { capture: true, passive: true }); } catch {}

    // Also bind to nearest scrollable ancestors of target regions (AWS pages often use inner scrollers)
    try {
      const isScrollable = (el) => {
        try {
          const cs = getComputedStyle(el);
          const over = (cs.overflow + cs.overflowY + cs.overflowX).toLowerCase();
          const flag = /auto|scroll|overlay/.test(over);
          return flag && (el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth);
        } catch { return false; }
      };
      const addScrollTarget = (el) => {
        try {
          if (!el) return;
          window.__guideExtraScrollTargets = window.__guideExtraScrollTargets || [];
          if (window.__guideExtraScrollTargets.indexOf(el) !== -1) return;
          el.addEventListener('scroll', window.__guideRecalc, { capture: true, passive: true });
          window.__guideExtraScrollTargets.push(el);
        } catch {}
      };
      const vrects = (window.__guideState.overlays || []).map(ov => bestViewportRect(ov));
      vrects.forEach(r => {
        const cx = Math.max(1, Math.min((window.innerWidth||0)-2, (r.l||0) + (r.w||0)/2));
        const cy = Math.max(1, Math.min((window.innerHeight||0)-2, (r.t||0) + (r.h||0)/2));
        let el = document.elementFromPoint(cx, cy);
        let hops = 0;
        while (el && hops < 8) { if (isScrollable(el)) { addScrollTarget(el); break; } el = el.parentElement; hops++; }
      });
    } catch {}
    window.addEventListener('resize', window.__guideRecalc, { capture: true, passive: true });

    // Start rAF tick to follow any scroll/animation in nested contexts
    try {
      const tick = () => { try { recalc(); } catch {} ; window.__guideRafId = requestAnimationFrame(tick); };
      window.__guideRafId = requestAnimationFrame(tick);
    } catch {}

    // Auto remove after 15s (15초 후 자동 제거)
    if (window.__guideTimeout) clearTimeout(window.__guideTimeout);
    window.__guideTimeout = setTimeout(removeGuides, 15000);
  };

  if (needScroll) {
    // Render first so recalc listeners are attached, then scroll; overlay will follow
    renderAll();
    try {
      const cur = getScrollState();
      const centerOffset = Math.round((vh / 2) - (firstView.h / 2));
      const targetY = Math.max(0, Math.round(cur.sy + firstView.t - centerOffset));
      window.scrollTo({ top: targetY, behavior: 'smooth' });
    } catch {}
  } else {
    renderAll();
  }
}

// Make helpers globally callable (전역에서 호출 가능하도록 노출)
window.showGuides = showGuides;
window.removeGuides = removeGuides;

/* ---------------------------
   3️⃣ Element info extractor (엘리먼트 정보 추출 함수)
--------------------------- */
/**
 * Get element info and absolute rect (엘리먼트 기본 정보와 절대 좌표 반환)
 * @param {Element} el
 * @param {string} description
 */
function getCssSelector(el) {
  try {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return `#${el.id}`;
    const cls = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0,2);
    const tag = (el.tagName || '').toLowerCase();
    if (cls.length) return `${tag}.${cls.join('.')}`;
    const parent = el.parentElement;
    if (!parent) return tag;
    const idx = Array.from(parent.children).indexOf(el) + 1;
    return `${(parent.tagName||'').toLowerCase()} > ${tag}:nth-child(${idx})`;
  } catch { return ''; }
}

function getElementInfo(el, description) {
  if (!el) return { description, element: null };
  try {
    const rect = el.getBoundingClientRect();
    const role = el.getAttribute('role') || '';
    const aria = el.getAttribute('aria-label') || '';
    const title = el.getAttribute('title') || '';
    const nameAttr = el.getAttribute('name') || '';
    const id = el.id || '';
    const className = (el.className || '').toString().trim().slice(0, 120);
    const text = (el.innerText || el.value || aria || title || '').toString().trim();
    return {
      description: description || '',
      tag: (el.tagName || '').toLowerCase(),
      text: text.slice(0, 160),
      role,
      id,
      class: className,
      selector: getCssSelector(el),
      rect: {
        left: rect.left + window.scrollX,
        top: rect.top + window.scrollY,
        width: rect.width,
        height: rect.height
      }
    };
  } catch (e) {
    return { description, element: null };
  }
}

/* ---------------------------
   4️⃣ Runtime message listener (런타임 메시지 리스너)
--------------------------- */
if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log('[content.js] Message:', request.action);

  if (request.action === 'removeGuides') {
    try { removeGuides(); } catch {}
    sendResponse && sendResponse({ ok: true });
    return true;
  }

  // Get page plain text (페이지의 순수 텍스트 가져오기)
  if (request.action === "getPageContent") {
    const bodyText = document.body ? document.body.innerText : "";
    sendResponse({ content: bodyText });
    return true;
  }

  // Get full HTML + viewport/scroll (전체 HTML과 뷰포트/스크롤 정보)
  if (request.action === "getPageHTML") {
    try {
      const html = document.documentElement ? document.documentElement.outerHTML : '';
      const viewport = { width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio || 1 };
      const scrollX = window.scrollX || 0;
      const scrollY = window.scrollY || 0;
      sendResponse({ html: html.slice(0, 200000), viewport, scrollX, scrollY });
    } catch (e) {
      console.warn('getPageHTML error', e);
      sendResponse({ html: null });
    }
    return true;
  }

  // Get simple page state (간단한 페이지 상태 수집)
  if (request.action === "getPageState") {
    try {
      const title = document.title || '';
      const h1 = (document.querySelector('h1')?.innerText || '').trim().slice(0, 120);
      const bodyText = (document.body?.innerText || '').toLowerCase();
      const htmlText = (document.documentElement?.innerHTML || '').toLowerCase();
      const host = (location && location.hostname) ? location.hostname : '';

      const loggedOutHints = ['sign in', '로그인', 'signin'];
      const loggedInHints = ['sign out', '로그아웃', 'account id', '계정 id', '계정', 'my account', 'billing', '결제'];
      const hasLoggedIn = loggedInHints.some(k => bodyText.includes(k) || htmlText.includes(k));
      const hasLoggedOut = loggedOutHints.some(k => bodyText.includes(k) || htmlText.includes(k));
      let loggedInGuess = hasLoggedIn && !hasLoggedOut;
      // AWS-specific hints: presence of account id pattern or region selector
      try {
        const accountIdPattern = /\b\d{4}-\d{4}-\d{4}\b/;
        if (accountIdPattern.test(bodyText) || accountIdPattern.test(htmlText)) loggedInGuess = true;
        const awscAccount = document.querySelector('#awsc-nav-account-menu, [data-testid="awsc-nav-account-menu-button"], [aria-label*="account" i]');
        const regionSel = document.querySelector('[data-testid*="region" i], [aria-label*="region" i]');
        if (awscAccount || regionSel) loggedInGuess = true;
      } catch {}

      // Sidebar guess: visible <nav> or <aside> taking some width
      let sidebarOpenGuess = false;
      let navRight = 0;
      try {
        const navs = Array.from(document.querySelectorAll('nav, aside,[role="navigation"]'));
        const vw = window.innerWidth || 0;
        sidebarOpenGuess = navs.some(el => {
          const r = el.getBoundingClientRect();
          const open = r.width > 140 && r.height > 200 && r.left < vw * 0.4;
          if (open) navRight = Math.max(navRight, r.right + window.scrollX);
          return open;
        });
      } catch {}

      // Section guess from breadcrumbs or h1/h2
      const h2 = (document.querySelector('h2')?.innerText || '').trim().slice(0, 120);
      const breadcrumb = (document.querySelector('nav[aria-label*="breadcrumb" i]')?.innerText || '').trim().slice(0, 160);
      const currentSectionGuess = breadcrumb || h1 || h2 || '';

      // Main content left edge (가능하면 main 역할 기준)
      let contentLeft = 0;
      try {
        const mainEl = document.querySelector('main, [role="main"]');
        if (mainEl) {
          const mr = mainEl.getBoundingClientRect();
          contentLeft = Math.max(0, Math.round(mr.left + window.scrollX));
        } else if (navRight) {
          contentLeft = Math.round(navRight + 8);
        }
      } catch {}

      sendResponse({
        title,
        h1,
        h2,
        breadcrumb,
        host,
        loggedInGuess,
        sidebarOpenGuess,
        currentSectionGuess,
        layout: { nav_right: navRight, content_left: contentLeft }
      });
    } catch (e) {
      console.warn('getPageState error', e);
      sendResponse(null);
    }
    return true;
  }

  // Find actionable elements (클릭/입력 가능한 요소 수집)
  if (request.action === "findElements") {
    try {
      const deep = !!request?.deep;
      const elements = [];

      const pushElement = (el, baseX = 0, baseY = 0, view = window) => {
        try {
          const rect = el.getBoundingClientRect();
          if (rect.width <= 1 || rect.height <= 1) return;
          let bg = '', fg = '', br = 0, visible = true;
          try {
            const cs = (view || window).getComputedStyle(el);
            bg = cs.backgroundColor || '';
            fg = cs.color || '';
            br = parseFloat(cs.borderRadius || '0') || 0;
            const disp = cs.display || '';
            const vis = cs.visibility || '';
            const op = parseFloat(cs.opacity || '1');
            visible = (disp !== 'none' && vis !== 'hidden' && op > 0.05);
          } catch {}

          const tag = (el.tagName || '').toLowerCase();
          const type = (el.getAttribute('type') || '').toLowerCase();
          const cls = (el.className || '').toString().toLowerCase();
          const roleAttr = (el.getAttribute('role') || '').toLowerCase();
          const isButton = (
            tag === 'button' || type === 'submit' || type === 'button' || roleAttr === 'button' || /\b(btn|button|cta)\b/.test(cls)
          );
          const inNav = !!el.closest('nav, aside, [role="navigation"]');
          const inHeader = !!el.closest('header, [role="banner"]');

          const info = getElementInfo(el);
          elements.push({
            tag: info.tag,
            text: info.text,
            role: info.role,
            id: info.id,
            class: info.class,
            selector: info.selector,
            is_button: isButton,
            in_nav: inNav,
            in_header: inHeader,
            style: { bg, fg, br },
            visible,
            rect: {
              x: baseX + rect.left + (view?.scrollX || 0),
              y: baseY + rect.top + (view?.scrollY || 0),
              width: rect.width,
              height: rect.height
            }
          });
        } catch {}
      };

      const collectInRoot = (root, baseX = 0, baseY = 0, view = window) => {
        try {
          const list = (root || document).querySelectorAll(`
            button, [role="button"], a[role="button"], a[href],
            input[type="submit"], input[type="button"],
            select, [role="combobox"],
            [data-testid*="button" i], [data-testid*="cta" i],
            [class*="button" i], [class*="btn" i]
          `);
          list.forEach(el => pushElement(el, baseX, baseY, view));

          if (deep) {
            // Shadow DOM
            const all = (root || document).querySelectorAll('*');
            all.forEach(node => {
              try { if (node.shadowRoot) collectInRoot(node.shadowRoot, baseX, baseY, view); } catch {}
            });
            // Same-origin iframes
            const iframes = (root || document).querySelectorAll('iframe');
            iframes.forEach(iframe => {
              try {
                const doc = iframe.contentDocument; const win = iframe.contentWindow;
                if (!doc || !win) return;
                const r = iframe.getBoundingClientRect();
                collectInRoot(doc, baseX + r.left + (view?.scrollX || 0), baseY + r.top + (view?.scrollY || 0), win);
              } catch {}
            });
          }
        } catch {}
      };

      collectInRoot(document, 0, 0, window);
      sendResponse({ elements });
    } catch (e) {
      console.error('findElements error', e);
      sendResponse({ elements: [] });
    }
    return true;
  }

  // Try to expand menus/panels so hidden buttons appear (메뉴/패널 펼치기)
  if (request.action === 'expandUI') {
    try {
      const triggers = Array.from(document.querySelectorAll(`
        button[aria-haspopup], [role="button"][aria-haspopup],
        button[aria-expanded="false"], [role="button"][aria-expanded="false"],
        button:has(svg), button:has(span),
        [data-testid*="menu" i], [data-testid*="overflow" i]
      `));
      let count = 0; const MAX = 5;
      for (const el of triggers) {
        if (count >= MAX) break;
        try {
          el.scrollIntoView({ block: 'center', inline: 'center' });
          el.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
          el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
          el.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
          count++;
        } catch {}
      }
      setTimeout(() => sendResponse({ expanded: count }), 120);
    } catch (e) {
      console.warn('expandUI error', e);
      setTimeout(() => sendResponse({ expanded: 0 }), 50);
    }
    return true;
  }

  // Render overlays (오버레이 렌더링)
  if (request.action === "renderOverlays") {
    try {
      const overlays = request.overlays || [];
      removeGuides();
      if (typeof showGuides === 'function') {
        console.log('[content.js] Calling showGuides with overlays:', overlays);
        showGuides(overlays, {
          coord_space: request.coord_space,
          dpr: request.dpr,
          scroll: request.scroll,
          viewport: request.viewport || null,
          screenshot_info: request.screenshot_info || null,
          screenshot: request.screenshot || null
        });
        sendResponse({ ok: true, count: overlays.length });
      } else {
        console.warn('showGuides is not defined');
        sendResponse({ ok: false });
      }
    } catch (e) {
      console.error('renderOverlays error', e);
      sendResponse({ ok: false });
    }
    return true;
  }

  return true;
});
} else {
  try { console.warn('[content.js] chrome.runtime unavailable in this frame'); } catch {}
}
