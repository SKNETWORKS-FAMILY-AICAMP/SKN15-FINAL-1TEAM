// panel.js
// -------------------------------
// Chrome Extension Chat Panel Script
// (크롬 확장 프로그램 챗봇 패널 스크립트)
// -------------------------------

const statusEl = document.getElementById("status");
const messagesEl = document.getElementById("messages");
const promptEl = document.getElementById("prompt");
const uploadBtn = document.getElementById("uploadPdfBtn");
const pdfInput = document.getElementById("pdfInput");
const historyBtn = document.getElementById("historyBtn");
const historyPopup = document.getElementById("historyPopup");
const historyList = document.getElementById("historyList");

// Format a step entry object or string (단계 객체/문자열 포맷)
function formatStepEntry(step) {
  if (!step) return "";
  if (typeof step === "string") return step.trim();
  if (typeof step === "object") {
    const baseParts = [];
    if (typeof step.instruction === "string") baseParts.push(step.instruction.trim());
    if (typeof step.title === "string") baseParts.push(step.title.trim());
    if (typeof step.description === "string") baseParts.push(step.description.trim());
    if (typeof step.step === "string") baseParts.push(step.step.trim());
    if (typeof step.summary === "string") baseParts.push(step.summary.trim());
    if (typeof step.result === "string") baseParts.push(step.result.trim());

    let base = baseParts.filter(Boolean).join(" ").trim();
    if (!base) base = (typeof step.step === "string" ? step.step.trim() : "");
    if (!base) base = JSON.stringify(step);

    // Action hint -> parentheses, not trailing tokens
    let hint = "";
    if (Array.isArray(step.actions) && step.actions.length) {
      hint = step.actions.map(a => String(a).trim()).filter(Boolean).join("/");
    } else if (typeof step.action === "string" && step.action.trim()) {
      hint = step.action.trim();
    }
    if (hint) {
      return `${base} (${hint})`;
    }
    return base;
  }
  return String(step);
}

// Get circled number characters (①, ②, …) (원형 번호 문자 얻기)
function getCircledNumber(index) {
  const circled = [
    "\u2460","\u2461","\u2462","\u2463","\u2464",
    "\u2465","\u2466","\u2467","\u2468","\u2469",
    "\u246A","\u246B","\u246C","\u246D","\u246E"
  ];
  if (index >= 1 && index <= circled.length) return circled[index - 1];
  return `${index}.`;
}

// Read recent chat history for context (이전 대화 일부를 가져오기)
function getRecentHistory(limit = 6, excludeLatestText = null) {
  try {
    const sessionKey = `chat-${sessionId}`;
    const existing = JSON.parse(localStorage.getItem(sessionKey) || "[]");
    if (!Array.isArray(existing) || existing.length === 0) return [];
    const sliced = existing.slice(-limit);
    if (excludeLatestText) {
      return sliced.filter(m => !(m && m.role === 'user' && typeof m.text === 'string' && m.text.trim() === excludeLatestText.trim()));
    }
    return sliced;
  } catch {
    return [];
  }
}

// Remove any leading numeric marker from a label (중복 번호 제거)
function stripLeadingMarker(str) {
  if (!str || typeof str !== 'string') return str;
  try {
    // Remove circled numbers (①-⑳) or leading digits with separators
    return str.replace(/^[\s\(\[]*(?:[①-⑳]|\d+)[\)\].:\-\s]+/u, '').trim();
  } catch {
    return str;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  try {
    // Load persisted chat history (저장된 채팅 불러오기)
    if (chrome?.storage?.local) {
      loadChatHistory();
    }
    statusEl.textContent = "연결 완료";

    // Web Guide Mode Toggle Button (웹 가이드 모드 토글 버튼)
    const webGuideBtn = document.getElementById("webGuideButton");
    if (webGuideBtn) {
      webGuideBtn.addEventListener("click", () => {
        webGuideBtn.classList.toggle("active");
        const active = webGuideBtn.classList.contains("active");
        webGuideBtn.textContent = active ? "챗봇 모드로 전환" : "웹 가이드 모드로 전환";
        toggleWebGuideMode(active);
      });
    }

    // Capture modes simplified: Only combined HTML+Image via getPageData
    // (캡처 모드 단일화: getPageData로 HTML+이미지 통합 수집만 사용)

  } catch (e) {
    console.error("초기화 에러:", e);
    statusEl.textContent = "에러";
  }
});

let sessionId = Date.now().toString();
// Track an active web‑guide session to allow step continuation
let currentGuideSession = null;

function resetGuideSession() {
  if (currentGuideSession) {
    try { currentGuideSession.continuePrompt?.remove(); } catch {}
    if (currentGuideSession.tabId) {
      try {
        chrome.tabs.sendMessage(currentGuideSession.tabId, { action: 'removeGuides' }, () => {});
      } catch {}
    }
  }
  currentGuideSession = null;
}

function showGuideContinuePrompt(text = '다음 안내 보기') {
  if (!currentGuideSession) return;
  if (currentGuideSession.continuePrompt) {
    try { currentGuideSession.continuePrompt.remove(); } catch {}
    currentGuideSession.continuePrompt = null;
  }
  const wrapper = document.createElement('div');
  wrapper.className = 'msg bot guide-next';
  const info = document.createElement('div');
  info.textContent = '다음 안내가 필요하면 버튼을 눌러주세요.';
  const btn = document.createElement('button');
  btn.textContent = text;
  btn.className = 'guide-next-btn';
  btn.style.marginTop = '6px';
  btn.style.padding = '6px 12px';
  btn.style.borderRadius = '6px';
  btn.style.border = '1px solid #2d68f5';
  btn.style.background = '#2d68f5';
  btn.style.color = '#fff';
  btn.style.cursor = 'pointer';
  btn.addEventListener('click', () => {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = '안내 불러오는 중...';
    continueGuideFlow().finally(() => {
      try { wrapper.remove(); } catch {}
      if (currentGuideSession) currentGuideSession.continuePrompt = null;
    });
  });
  wrapper.appendChild(info);
  wrapper.appendChild(btn);
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  currentGuideSession.continuePrompt = wrapper;
}

// --------------------------------------
// Message rendering & history (메시지/히스토리)
// --------------------------------------

// Add message to chat view (채팅 메시지 추가)
function addMessage(text, role = "user", isTemp = false) {
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;
  msg.textContent = text;
  if (isTemp) {
    msg.classList.add("loading");
    msg.dataset.temp = "true";
  }
  messagesEl.appendChild(msg);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  if (!isTemp) {
    saveMessageToHistory(text, role);
  }
  return msg;
}

// Save message to localStorage (로컬 저장소에 메시지 저장)
function saveMessageToHistory(text, role) {
  const sessionKey = `chat-${sessionId}`;
  const existing = JSON.parse(localStorage.getItem(sessionKey) || "[]");
  existing.push({ text, role });
  localStorage.setItem(sessionKey, JSON.stringify(existing));

  if (existing.length === 1 && role === "user") {
    localStorage.setItem(`${sessionKey}-title`, text.slice(0, 30));
  }
}

// Update temporary/loading message (임시/로딩 메시지 업데이트)
function updateMessage(msgEl, newContent, options = {}) {
  if (!msgEl) return;
  const { stream = true, delay = 18 } = options;

  const finish = () => {
    // Render markdown for bot replies
    if (msgEl.classList.contains('bot')) {
      msgEl.innerHTML = markdownToHtml(newContent || '');
    } else {
      msgEl.textContent = newContent;
    }
    saveMessageToHistory(newContent, "bot");
  };

  if (msgEl.dataset && msgEl.dataset.temp === "true") {
    delete msgEl.dataset.temp;
    msgEl.classList.remove("loading");

    if (!stream || !newContent) {
      finish();
      return;
    }

    msgEl.textContent = "";
    let index = 0;
    const timer = setInterval(() => {
      index += 1;
      msgEl.textContent = newContent.slice(0, index);
      if (index >= newContent.length) {
        clearInterval(timer);
        saveMessageToHistory(newContent, "bot");
      }
    }, delay);
    return;
  }

  if (msgEl.classList.contains('bot')) {
    msgEl.innerHTML = markdownToHtml(newContent || '');
  } else {
    msgEl.textContent = newContent;
  }
}

// --------------------------------------
// Send flow (메시지 전송 흐름)
// --------------------------------------
async function handleSend() {
  const text = promptEl.value.trim();
  if (!text) return;

  resetGuideSession();

  addMessage(text, "user");
  promptEl.value = "";
  const loadingMsg = addMessage(".", "bot", true);

  let dots = 1;
  const interval = setInterval(() => {
    dots = (dots % 3) + 1;
    loadingMsg.textContent = "" + ".".repeat(dots);
  }, 500);

  // Web Guide mode (웹 가이드 모드)
  const webGuideButton = document.getElementById('webGuideButton');
  if (webGuideButton && webGuideButton.classList.contains('active')) {
    try {
      const pageData = await new Promise((resolve) => {
        chrome.runtime.sendMessage({ action: "getPageData" }, resolve);
      });

      // Compute screenshot dimensions if present
      async function getImageSize(dataUrl) {
        return new Promise((resolve) => {
          try {
            if (!dataUrl || typeof dataUrl !== 'string') return resolve(null);
            const img = new Image();
            img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
            img.onerror = () => resolve(null);
            img.src = dataUrl;
          } catch { resolve(null); }
        });
      }

      const shotInfo = await getImageSize(pageData?.screenshot);

      const payload = {
        mode: 'web-guide',
        guideType: 'overlay',
        message: text,
        screenshot: pageData?.screenshot || null,
        screenshot_info: shotInfo || null,
        url: pageData?.url || "",
        title: pageData?.title || document.title || "",
        html: pageData?.html || null,
        elements: pageData?.elements || [],
        history: getRecentHistory(8, text),
        page_viewport_rect: pageData?.viewport
          ? { x: 0, y: 0, width: Number(pageData.viewport.width||0), height: Number(pageData.viewport.height||0) }
          : null,
        device_pixel_ratio: Number(pageData?.viewport?.dpr || window.devicePixelRatio || 1),
        scroll: pageData?.scroll || { x: 0, y: 0 },
        page_state: pageData?.state || null
      };

      console.log("payload preview", { message: payload.message, url: payload.url });

      const res = await safeFetch("http://localhost:3000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      clearInterval(interval);

      // safeFetch returns { ok, status, json, text } (safeFetch 반환 구조)
      let parsed = null;
      if (res.json) {
        parsed = res.json;
      } else if (res.text) {
        try { parsed = JSON.parse(res.text); } catch (e) { parsed = { explanation_md: res.text }; }
      } else {
        parsed = { explanation_md: "서버로부터 응답을 받지 못했습니다." };
      }

      console.log("Parsed response from server:", parsed);

      const explanation = typeof parsed.explanation_md === "string" ? parsed.explanation_md.trim() : "";
      const rawSteps = Array.isArray(parsed.steps) ? parsed.steps : [];
      const formattedSteps = rawSteps.map(formatStepEntry);
      // De-duplicate identical step lines to avoid repetitive guidance
      const seen = new Set();
      const numberedSteps = formattedSteps
        .map((step, idx) => step ? `${getCircledNumber(idx + 1)} ${step}` : "")
        .filter(s => {
          const key = s.replace(/\s+/g,' ').trim().toLowerCase();
          if (!key || seen.has(key)) return false; seen.add(key); return true;
        });

      const messageSections = [];
      if (explanation) messageSections.push(explanation);
      if (numberedSteps.length) messageSections.push(numberedSteps.join("\n"));

      if (!messageSections.length) {
        const fallback = parsed.reply || parsed.result || parsed.message || "";
        if (fallback) {
          messageSections.push(Array.isArray(fallback) ? fallback.join("\n") : String(fallback));
        }
      }

  const finalText = messageSections.filter(Boolean).join("\n\n") || "응답이 없습니다.";
      updateMessage(loadingMsg, finalText, { stream: false });

      // Build overlays to render (하이라이트 오버레이 생성)
      const overlaysToRender = [];
      let overlayCoordSpace = (typeof parsed.coord_space === 'string') ? parsed.coord_space.toLowerCase() : null;

      const safeLabel = (val, idx) => {
        try {
          let s = '';
          if (typeof val === 'string') s = val;
          else if (val && typeof val === 'object') s = formatStepEntry(val);
          s = String(s || '').trim();
          if (/^\s*[\[{]/.test(s)) return `단계 ${idx+1}`;
          if (s.length > 80) s = s.slice(0,77) + '…';
          return s || `단계 ${idx+1}`;
        } catch { return `단계 ${idx+1}`; }
      };

      // Step-level target_index support (우선 처리)
      const stepTargets = [];
      if (Array.isArray(rawSteps)) {
        rawSteps.forEach((st, i) => {
          if (st && typeof st === 'object' && Number.isInteger(st.target_index)) {
            stepTargets.push({ elIndex: st.target_index, stepIndex: i });
          }
        });
      }

      if (stepTargets.length > 0 && pageData?.elements?.length) {
        stepTargets.forEach(({ elIndex, stepIndex }, order) => {
          const el = pageData.elements[elIndex];
          if (!el || !el.rect) return;
          const rect = el.rect || {};
          const labelText = stripLeadingMarker(safeLabel(rawSteps[stepIndex], order));
          overlaysToRender.push({
            x: Number(rect.left ?? rect.x ?? 0),
            y: Number(rect.top ?? rect.y ?? 0),
            width: Number(rect.width ?? 0),
            height: Number(rect.height ?? 0),
            label: labelText
          });
        });
        overlayCoordSpace = 'document';

      // Else prefer target_indexes (DOM-aligned) over raw overlays
      } else if (Array.isArray(parsed.target_indexes) && parsed.target_indexes.length > 0 && pageData?.elements?.length) {
        parsed.target_indexes.forEach((idx, i) => {
          const el = pageData.elements[idx];
          if (!el || !el.rect) return;
          const rect = el.rect || {};
          const labelText = stripLeadingMarker(safeLabel(rawSteps[i], i));
          overlaysToRender.push({
            x: Number(rect.left ?? rect.x ?? 0),
            y: Number(rect.top ?? rect.y ?? 0),
            width: Number(rect.width ?? 0),
            height: Number(rect.height ?? 0),
            // Numbering is rendered by content.js; pass plain text only
            label: labelText
          });
        });
        overlayCoordSpace = 'document';
      } else if (Array.isArray(parsed.overlays) && parsed.overlays.length > 0) {
        // If the server/model declares screenshot-space, pass through as-is and
        // let content.js convert from screenshot px -> CSS viewport using dpr.
        if (overlayCoordSpace === 'screenshot') {
          parsed.overlays.forEach((ov, idx) => {
            if (!ov || typeof ov !== 'object') return;
            const sidx = Number.isInteger(ov?.step_index) ? ov.step_index : idx;
            const labelText = stripLeadingMarker(safeLabel((ov && ov.label) || formattedSteps[sidx] || rawSteps[sidx], sidx));
            // Try to attach a stable selector anchor when target_indexes are known
            let anchorSelector = "";
            try {
              if (Array.isArray(parsed.target_indexes) && pageData?.elements?.length) {
                const elIdx = parsed.target_indexes[sidx];
                const el = pageData.elements[elIdx];
                if (el && el.selector) anchorSelector = el.selector;
              } else if (Array.isArray(pageData?.elements)) {
                // Fallback: choose element with highest IoU against overlay rect
                const r = { x: Number(ov.x||0), y: Number(ov.y||0), w: Number(ov.width||0), h: Number(ov.height||0) };
                let best = { sel: "", score: 0 };
                const iou = (a,b)=>{ const ax2=a.x+a.w, ay2=a.y+a.h, bx2=b.x+b.w, by2=b.y+b.h; const x1=Math.max(a.x,b.x), y1=Math.max(a.y,b.y), x2=Math.min(ax2,bx2), y2=Math.min(ay2,by2); const iw=Math.max(0,x2-x1), ih=Math.max(0,y2-y1); const inter=iw*ih; const ua=a.w*a.h+b.w*b.h-inter; return ua>0? inter/ua:0; };
                pageData.elements.forEach((e) => {
                  const er = (e && e.rect) ? e.rect : {};
                  const bx = Number((er.left !== undefined && er.left !== null) ? er.left : (er.x || 0));
                  const by = Number((er.top !== undefined && er.top !== null) ? er.top : (er.y || 0));
                  const bw = Number(er.width || 0);
                  const bh = Number(er.height || 0);
                  const b = { x: bx, y: by, w: bw, h: bh };
                  const s = iou(r, b);
                  if (s > best.score) { best = { sel: e.selector || "", score: s }; }
                });
                if (best.score>0.15) anchorSelector = best.sel;
              }
            } catch {}
            overlaysToRender.push({
              x: Number(ov.x ?? ov.left ?? 0),
              y: Number(ov.y ?? ov.top ?? 0),
              width: Number(ov.width ?? ov.w ?? 0),
              height: Number(ov.height ?? ov.h ?? 0),
              label: labelText,
              anchor_selector: anchorSelector
            });
          });
        } else {
        // Heuristic: when the model returns raw overlays without target_indexes,
        // treat them as viewport-relative and add current scroll offsets so
        // content.js (which expects page/document coordinates) renders correctly.
        const sx = Number(pageData?.scroll?.x || 0);
        const sy = Number(pageData?.scroll?.y || 0);
        parsed.overlays.forEach((ov, idx) => {
          if (!ov || typeof ov !== 'object') return;
          const sidx = Number.isInteger(ov?.step_index) ? ov.step_index : idx;
          const labelText = stripLeadingMarker(safeLabel((ov && ov.label) || formattedSteps[sidx] || rawSteps[sidx], sidx));
          overlaysToRender.push({
            x: Number(ov.x ?? ov.left ?? 0) + sx,
            y: Number(ov.y ?? ov.top ?? 0) + sy,
            width: Number(ov.width ?? ov.w ?? 0),
            height: Number(ov.height ?? ov.h ?? 0),
            // Numbering is rendered by content.js; pass plain text only
            label: labelText
          });
        });
        }
      }

      // Deduplicate overlays with same geometry/label
      const seenKeys = new Set();
      const uniqueOverlays = [];
      overlaysToRender.forEach((ov) => {
        const key = `${Math.round(ov.x)}|${Math.round(ov.y)}|${Math.round(ov.width)}|${Math.round(ov.height)}|${ov.label||''}`;
        if (seenKeys.has(key)) return;
        seenKeys.add(key);
        uniqueOverlays.push(ov);
      });
      const finalOverlays = uniqueOverlays;

      if (finalOverlays.length) {
        const guideSessionId = Date.now().toString() + Math.random().toString(36).slice(2, 7);
        currentGuideSession = { id: guideSessionId, message: text, stepIndex: 0, last: parsed, prevUrl: pageData?.url || '', continuePrompt: null, isFetching: false };
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          if (tabs && tabs[0]) {
            currentGuideSession.tabId = tabs[0].id;
            chrome.tabs.sendMessage(tabs[0].id, {
              action: "renderOverlays",
              overlays: finalOverlays,
              coord_space: overlayCoordSpace || 'document',
              dpr: Number(pageData?.viewport?.dpr || window.devicePixelRatio || 1),
              scroll: { x: Number(pageData?.scroll?.x || 0), y: Number(pageData?.scroll?.y || 0) },
              viewport: pageData?.viewport || null,
              screenshot_info: shotInfo || null,
              screenshot: pageData?.screenshot || null,
              session_id: guideSessionId,
              step_index: 0
            });
          }
        });
        showGuideContinuePrompt('다음 안내 보기');
      }
    } catch (err) {
      console.error("웹 가이드 처리 실패:", err);
      clearInterval(interval);
      updateMessage(loadingMsg, "서버 송신/처리 중 오류가 발생했습니다.");
    }
    return;
  }

  // ------------------------------
  // Default Chatbot mode (일반 챗봇 모드)
  // ------------------------------
  try {
    // In chatbot mode, allow page-related questions without overlays
    // Decide if the user is asking about the current page/site
    const isPageInfoQuery = (t) => {
      const s = String(t || '').toLowerCase();
      const hints = [
        // English
        'what is on this page', 'summarize this page', 'explain this page', 'what does this page', 'about this site', 'describe the page', 'summarize the site',
        // Korean
        '이 페이지', '이 사이트', '페이지 내용', '사이트 내용', '요약', '설명', '구조', '무엇이 있', '무엇이 들어', '어떤 내용'
      ];
      return hints.some(k => s.includes(k));
    };

    let chatPayload = { mode: 'chat', message: text, history: getRecentHistory(8, text) };

    if (isPageInfoQuery(text)) {
      // Fetch page context to enrich the chatbot response (no overlays in chat mode)
      const pageData = await new Promise((resolve) => {
        chrome.runtime.sendMessage({ action: "getPageData" }, resolve);
      });
      chatPayload = {
        mode: 'chat',
        message: text,
        screenshot: pageData?.screenshot || null,
        url: pageData?.url || "",
        html: pageData?.html || null,
        elements: pageData?.elements || [],
        history: getRecentHistory(8, text)
      };
    }

    const res = await safeFetch("http://localhost:3000/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(chatPayload)
    });
    clearInterval(interval);

    if (!res.ok) {
      const errorText = res.json?.message || res.json?.reply || res.text || `status ${res.status}`;
      updateMessage(loadingMsg, `서버 오류: ${errorText}`, { stream: false });
      return;
    }

    let botText = "";
    if (res.json) {
      const data = res.json;
      // Chat mode returns { reply }
      botText = (typeof data.reply === 'string' && data.reply.trim())
        ? data.reply
        : (data.result || data.message || "");
    } else if (res.text) {
      botText = res.text;
    }

    updateMessage(loadingMsg, botText || "응답이 없습니다.", { stream: false });
  } catch (e) {
    clearInterval(interval);
    updateMessage(loadingMsg, "서버 연결에 문제가 있습니다.");
  }

}

// Minimal Markdown renderer (안전한 최소 마크다운 렌더러)
function markdownToHtml(md) {
  if (!md || typeof md !== 'string') return '';
  const esc = (s) => s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  const lines = md.split(/\r?\n/);
  const out = [];
  let inOl = false, inUl = false;
  const flushLists = () => {
    if (inOl) { out.push('</ol>'); inOl = false; }
    if (inUl) { out.push('</ul>'); inUl = false; }
  };
  for (let raw of lines) {
    const line = raw.trimEnd();
    const ol = line.match(/^\s*(\d+)\.\s+(.*)$/);
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ol) {
      if (!inOl) { flushLists(); out.push('<ol>'); inOl = true; }
      out.push('<li>' + inlineFormat(esc(ol[2])) + '</li>');
      continue;
    }
    if (ul) {
      if (!inUl) { flushLists(); out.push('<ul>'); inUl = true; }
      out.push('<li>' + inlineFormat(esc(ul[1])) + '</li>');
      continue;
    }
    flushLists();
    if (line === '') { out.push('<br/>'); continue; }
    out.push('<p>' + inlineFormat(esc(line)) + '</p>');
  }
  flushLists();
  return out.join('');
}

function inlineFormat(s) {
  // bold **text** and italic *text* (non-greedy)
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1<\/strong>');
  s = s.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1<\/em>');
  // inline code `code`
  s = s.replace(/`([^`]+)`/g, '<code>$1<\/code>');
  return s;
}

// Send on Enter / newline with Shift+Enter (엔터 전송 / 쉬프트+엔터 줄바꿈)
promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});

// Auto-resize input area (입력창 높이 자동 조절)
const baseHeight = 40;
const maxHeight = 180;
function adjustHeight() {
  promptEl.style.height = "auto";
  promptEl.style.height = Math.min(promptEl.scrollHeight, maxHeight) + "px";
}
promptEl.addEventListener("input", adjustHeight);
promptEl.addEventListener("keyup", adjustHeight);

// PDF upload (PDF 업로드)
uploadBtn.addEventListener("click", () => pdfInput.click());
pdfInput.addEventListener("change", () => {
  const file = pdfInput.files[0];
  if (file) {
    addMessage(`선택한 PDF: ${file.name}`, "user");
  }
});

// Homepage button (홈페이지 버튼)
document.getElementById("goHomeBtn").addEventListener("click", () => {
  window.open("https://ej-homepage.com", "_blank");
});

// Theme toggle (테마 토글)
document.getElementById("toggleThemeBtn").addEventListener("click", () => {
  document.body.classList.toggle("dark-theme");
  document.body.classList.toggle("light-theme");
});

// Reset chat (채팅 초기화)
document.getElementById("resetChatBtn").addEventListener("click", () => {
  sessionId = Date.now().toString();
  messagesEl.innerHTML = "";
  addMessage("새 대화가 시작되었습니다.", "bot");
});

// Load a session from history (히스토리에서 세션 로드)
function loadFromHistory(id) {
  const saved = localStorage.getItem(id);
  if (saved) {
    messagesEl.innerHTML = "";
    const messages = JSON.parse(saved);
    messages.forEach(m => addMessage(m.text, m.role));
    sessionId = id.replace("chat-", "");
  }
}

// Render history list (히스토리 목록 렌더링)
function renderHistoryList() {
  historyList.innerHTML = "";
  Object.keys(localStorage)
    .filter(k => k.startsWith("chat-") && !k.endsWith("-title"))
    .forEach(id => {
      const title = localStorage.getItem(`${id}-title`) || id.slice(-6);
      const wrapper = document.createElement("div");
      wrapper.className = "history-item";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = id;

      const btn = document.createElement("button");
      btn.textContent = title;
      btn.addEventListener("click", () => {
        loadFromHistory(id);
        historyPopup.style.display = "none";
      });

      wrapper.appendChild(checkbox);
      wrapper.appendChild(btn);
      historyList.appendChild(wrapper);
    });
}

// Toggle history popup (히스토리 팝업 토글)
historyBtn.addEventListener("click", () => {
  const isVisible = historyPopup.style.display === "block";
  historyPopup.style.display = isVisible ? "none" : "block";
  if (!isVisible) renderHistoryList();
});

// Delete selected histories (선택 삭제)
document.getElementById("deleteSelectedBtn").addEventListener("click", () => {
  const checkboxes = historyList.querySelectorAll("input[type='checkbox']:checked");
  checkboxes.forEach(cb => {
    localStorage.removeItem(cb.value);
    localStorage.removeItem(`${cb.value}-title`);
  });
  renderHistoryList();
});

// Delete all histories (전체 삭제)
document.getElementById("deleteAllBtn").addEventListener("click", () => {
  Object.keys(localStorage)
    .filter(k => k.startsWith("chat-"))
    .forEach(k => localStorage.removeItem(k));
  renderHistoryList();
});

// Close history popup when clicking outside (바깥 클릭 시 히스토리 팝업 닫기)
document.addEventListener("click", (e) => {
  const isInside = historyPopup.contains(e.target) || historyBtn.contains(e.target);
  if (!isInside) {
    historyPopup.style.display = "none";
  }
});

// Request open tabs info (열린 탭 정보 요청)
chrome.runtime.sendMessage({ action: "getTabs" }, function(response) {
  const tabList = document.getElementById("tabList");
  if (!tabList) return;

  if (response?.tabs) {
    response.tabs.forEach(tab => {
      const item = document.createElement("div");
      item.className = "tab-item";
      item.innerHTML = `
        <img src="${tab.favIcon}" width="16" height="16" />
        <strong>${tab.title}</strong><br />
        <small>${tab.url}</small>
      `;
      tabList.appendChild(item);
    });
  }
});

// Legacy capture helpers removed (기존 캡처 헬퍼 제거)

// Robust fetch helper (안전한 fetch 헬퍼) : returns { ok, status, json, text }
async function safeFetch(url, opts) {
  try {
    const res = await fetch(url, opts);
    const ct = res.headers.get('content-type') || '';
    let json = null;
    let text = null;
    if (ct.includes('application/json')) {
      try { json = await res.json(); } catch (e) { text = await res.text(); }
    } else {
      const body = await res.text();
      text = body;
      try { json = JSON.parse(body); } catch (e) { /* not json */ }
    }
    return { ok: res.ok, status: res.status, json, text };
  } catch (err) {
    return { ok: false, status: 0, error: err };
  }
}

// resizeDataUrl helper removed (통합 수집만 사용하므로 불필요)

// Simple UI reply helper (간단 응답 출력 유틸)
function displayReply(text) {
  const msg = document.createElement("div");
  msg.className = "msg bot";
  msg.textContent = text;
  document.getElementById("messages").appendChild(msg);
}

// Load chat history from chrome.storage (크롬 스토리지에서 히스토리 로드)
function loadChatHistory() {
  chrome.storage.local.get("chatHistory", (data) => {
    const messages = data.chatHistory || [];
    messages.forEach(msg => displayReply(msg));
  });
}
function saveMessage(text) {
  chrome.storage.local.get("chatHistory", (data) => {
    const messages = data.chatHistory || [];
    messages.push(text);
    chrome.storage.local.set({ chatHistory: messages });
  });
}

// requestPageContent removed (텍스트 전용 요약 경로 제거)

// Web guide mode stub (웹 가이드 모드 상태 전환 처리용)
function toggleWebGuideMode(isActive) {
  console.log("웹 가이드 모드:", isActive ? "활성화" : "비활성화");
  // 필요 시 추가 로직 구현 (e.g., 컨텐츠 스크립트 알림 등)
  if (!isActive) {
    resetGuideSession();
  }
}

// Listen continuation messages from content.js
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.action === 'guideStepCompleted') {
    if (currentGuideSession && (!msg.session_id || msg.session_id === currentGuideSession.id)) {
      showGuideContinuePrompt('다음 안내 보기');
    }
  }
});

async function continueGuideFlow() {
  if (!currentGuideSession || currentGuideSession.isFetching) return;
  currentGuideSession.isFetching = true;
  const stepDone = currentGuideSession.stepIndex || 0;
  const last = currentGuideSession.last || {};
  if (currentGuideSession.continuePrompt) {
    try { currentGuideSession.continuePrompt.remove(); } catch {}
    currentGuideSession.continuePrompt = null;
  }
  const loadingMsg = addMessage(".", "bot", true);
  let dots = 1; const interval = setInterval(() => { dots = (dots % 3) + 1; loadingMsg.textContent = "" + ".".repeat(dots); }, 500);
  try {
    const activeTab = await new Promise(res=>chrome.tabs.query({active:true,currentWindow:true},t=>res((t&&t[0])||null)));
    if (currentGuideSession) currentGuideSession.tabId = activeTab?.id || currentGuideSession.tabId;
    await waitForTabComplete(activeTab?.id, 2500);
    // Poll getPageData until URL or HTML length changes (SPA 대응)
    let pageData = null; let tries = 0; const prevUrl = currentGuideSession.prevUrl || '';
    while (tries < 6) {
      pageData = await new Promise((resolve) => { chrome.runtime.sendMessage({ action: "getPageData" }, resolve); });
      if (!pageData) { await new Promise(r=>setTimeout(r, 200)); tries++; continue; }
      const urlChanged = !!prevUrl && pageData.url && pageData.url !== prevUrl;
      const htmlLen = (pageData.html && pageData.html.length) || 0;
      const prevHtmlLen = (last && last.html && last.html.length) || 0;
      if (urlChanged || Math.abs(htmlLen - prevHtmlLen) > 500) break;
      await new Promise(r=>setTimeout(r, 250)); tries++;
    }
    currentGuideSession.prevUrl = pageData?.url || currentGuideSession.prevUrl || '';
    // Compute screenshot natural size (needed for precise overlay scaling)
    const shotInfo = await (async (dataUrl)=>new Promise((resolve)=>{ try{ if(!dataUrl) return resolve(null); const img=new Image(); img.onload=()=>resolve({ width: img.naturalWidth, height: img.naturalHeight }); img.onerror=()=>resolve(null); img.src=dataUrl; }catch{ resolve(null);} })) (pageData?.screenshot);
    const payload = {
      mode: 'web-guide', guideType: 'overlay', message: currentGuideSession.message,
      screenshot: pageData?.screenshot || null, url: pageData?.url || "",
      html: pageData?.html || null, elements: pageData?.elements || [],
      history: getRecentHistory(8, currentGuideSession.message),
      page_viewport_rect: pageData?.viewport ? { x:0, y:0, width:Number(pageData.viewport.width||0), height:Number(pageData.viewport.height||0) } : null,
      device_pixel_ratio: Number(pageData?.viewport?.dpr || window.devicePixelRatio || 1),
      scroll: pageData?.scroll || {x:0,y:0}, page_state: pageData?.state || null,
      progress: {
        last_step_done: stepDone,
        prev_overlays: last.overlays || [],
        prev_targets: last.target_indexes || [],
        prev_url: currentGuideSession.prevUrl || "",
        page_url: pageData?.url || ""
      }
    };
    let res = await safeFetch("http://localhost:3000/chat", { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!res.ok && res.status === 429) {
      updateMessage(loadingMsg, '잠시 후 계속합니다...(429)', { stream:false });
      await new Promise(r=>setTimeout(r, 22000));
      res = await safeFetch("http://localhost:3000/chat", { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    }
    clearInterval(interval);
    let parsed = res.json || (res.text && (()=>{try{return JSON.parse(res.text)}catch{return {explanation_md:res.text}}})());
    parsed = parsed || { explanation_md: "다음 단계가 없습니다." };
    const explanation = typeof parsed.explanation_md === 'string' ? parsed.explanation_md.trim() : '';
    const rawSteps = Array.isArray(parsed.steps) ? parsed.steps : [];
    const formattedSteps = rawSteps.map(formatStepEntry);
    const botText = [explanation, formattedSteps.length ? formattedSteps.map((s,i)=>`${getCircledNumber(i+1)} ${s}`).join('\n') : ''].filter(Boolean).join('\n\n');
    updateMessage(loadingMsg, botText || '다음 안내가 없습니다.', { stream: false });

    if (Array.isArray(parsed.overlays) && parsed.overlays.length) {
      currentGuideSession.stepIndex = stepDone + 1; currentGuideSession.last = parsed;
      await sendOverlaysWithRetry(activeTab?.id, { action:'renderOverlays', overlays: parsed.overlays, coord_space: parsed.coord_space || 'screenshot', dpr: Number(pageData?.viewport?.dpr || window.devicePixelRatio || 1), scroll: pageData?.scroll || {x:0,y:0}, viewport: pageData?.viewport || null, screenshot_info: shotInfo || null, screenshot: pageData?.screenshot || null, session_id: currentGuideSession.id, step_index: currentGuideSession.stepIndex });
      showGuideContinuePrompt('다음 안내 보기');
    } else {
      resetGuideSession();
    }
  } catch (e) {
    clearInterval(interval); updateMessage(loadingMsg, '다음 단계 요청 중 오류가 발생했습니다.');
  } finally {
    currentGuideSession && (currentGuideSession.isFetching = false);
  }
}

function waitForTabComplete(tabId, timeoutMs=2000){
  return new Promise((resolve)=>{
    if(!tabId){ resolve(); return; }
    let done=false; const timer=setTimeout(()=>{ if(!done){ done=true; try{chrome.tabs.onUpdated.removeListener(listener);}catch{} resolve(); } }, timeoutMs);
    const listener=(id,info)=>{ if(id===tabId && info.status==='complete'){ if(!done){ done=true; clearTimeout(timer); try{chrome.tabs.onUpdated.removeListener(listener);}catch{} resolve(); } } };
    try{ chrome.tabs.onUpdated.addListener(listener);}catch{ resolve(); }
  });
}

async function sendOverlaysWithRetry(tabId, message){
  if(!tabId) return;
  try {
    chrome.tabs.sendMessage(tabId, { action: 'removeGuides' }, () => {});
  } catch {}
  for(let i=0;i<6;i++){
    const ok = await new Promise((resolve)=>{
      try{
        chrome.tabs.sendMessage(tabId, message, (resp)=>{
          if(chrome.runtime.lastError){ resolve(false);} else { resolve(true);} });
      }catch{ resolve(false); }
    });
    if(ok) return;
    try{ await chrome.scripting.executeScript({ target:{ tabId, allFrames: true }, files:['content.js'] }); }catch{}
    await new Promise(r=>setTimeout(r, 180));
  }
}
