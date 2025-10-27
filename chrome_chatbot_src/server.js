/**
 * server.js
 * Express server for web guide/chat endpoints using OpenAI
 * (OpenAI를 이용한 웹 가이드/챗 엔드포인트용 Express 서버)
 */

require("dotenv").config();
const express = require("express");
const cors = require("cors");
const app = express();

/* -----------------------------
 * Env & OpenAI Setup (환경 변수 및 OpenAI 설정)
 * ----------------------------- */
const openaiKey = process.env.OPENAI_API_KEY;
if (!openaiKey) {
  console.error("ERROR: OPENAI_API_KEY is not set.");
  process.exit(1);
}
if (openaiKey.length < 20) {
  console.error("ERROR: OPENAI_API_KEY seems too short.");
  process.exit(1);
}

const openaiVisionModel = process.env.OPENAI_VISION_MODEL || "gpt-4o";
const OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions";

/* -----------------------------
 * Middlewares (미들웨어)
 * ----------------------------- */
app.use(cors());
app.use(express.json({ limit: "20mb" }));

// Use node-fetch via dynamic import (동적 임포트로 node-fetch 사용)
const fetch = (...args) => import("node-fetch").then(({ default: fetch }) => fetch(...args));

function normalizeLocalContext(rawContext) {
  const fallbackNow = new Date();
  const resolved = typeof Intl !== "undefined" && Intl.DateTimeFormat
    ? Intl.DateTimeFormat().resolvedOptions?.() || {}
    : {};

  const fallback = {
    iso: fallbackNow.toISOString(),
    epochMs: fallbackNow.getTime(),
    timeZone: resolved.timeZone || "UTC",
    locale: resolved.locale || "en-US",
    approxLocation: null,
    display: null,
    timeZoneName: null
  };

  const ctx = { ...fallback };
  const source = rawContext && typeof rawContext === "object" ? rawContext : {};

  const pickString = (...values) => {
    for (const value of values) {
      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }
    }
    return null;
  };

  const isoCandidate = pickString(
    source.iso,
    source.datetime,
    source.local_datetime_iso,
    source.local_time_iso
  );
  const timestampCandidate = (() => {
    const candidates = [source.timestamp_ms, source.timestamp, source.epoch_ms, source.epoch];
    for (const val of candidates) {
      if (typeof val === "number" && Number.isFinite(val)) return val;
      if (typeof val === "string" && val.trim() && !Number.isNaN(Number(val))) {
        return Number(val);
      }
    }
    return null;
  })();

  if (isoCandidate && !Number.isNaN(Date.parse(isoCandidate))) {
    const parsed = new Date(isoCandidate);
    ctx.iso = parsed.toISOString();
    ctx.epochMs = parsed.getTime();
  } else if (timestampCandidate !== null) {
    const parsed = new Date(Number(timestampCandidate));
    ctx.iso = parsed.toISOString();
    ctx.epochMs = parsed.getTime();
  }

  const timeZoneCandidate = pickString(source.time_zone, source.timezone, source.tz);
  if (timeZoneCandidate) ctx.timeZone = timeZoneCandidate;

  const localeCandidate = pickString(source.locale, source.language, source.lang);
  if (localeCandidate) ctx.locale = localeCandidate;

  const locationCandidate = pickString(source.approx_location, source.location, source.city);
  if (locationCandidate) ctx.approxLocation = locationCandidate;

  const displayCandidate = pickString(source.display, source.formatted, source.readable);
  if (displayCandidate) ctx.display = displayCandidate;

  const timeZoneNameCandidate = pickString(source.time_zone_name, source.timezone_name);
  if (timeZoneNameCandidate) ctx.timeZoneName = timeZoneNameCandidate;

  if (!ctx.approxLocation && ctx.timeZone) {
    const parts = ctx.timeZone.split("/");
    if (parts.length >= 2) {
      ctx.approxLocation = parts.slice(1).join(" / ").replace(/_/g, " ");
    }
  }

  if (!ctx.display) {
    try {
      ctx.display = new Intl.DateTimeFormat(ctx.locale || undefined, {
        dateStyle: "full",
        timeStyle: "long",
        timeZone: ctx.timeZone || undefined
      }).format(new Date(ctx.iso));
    } catch {
      try {
        ctx.display = new Intl.DateTimeFormat("en-US", {
          dateStyle: "full",
          timeStyle: "long",
          timeZone: ctx.timeZone || undefined
        }).format(new Date(ctx.iso));
      } catch {
        ctx.display = ctx.iso;
      }
    }
  }

  if (!ctx.timeZoneName && ctx.timeZone) {
    try {
      const parts = new Intl.DateTimeFormat(ctx.locale || undefined, {
        timeZone: ctx.timeZone || undefined,
        timeZoneName: "long"
      }).formatToParts(new Date(ctx.iso));
      const tzPart = parts.find((p) => p?.type === "timeZoneName");
      ctx.timeZoneName = tzPart?.value || null;
    } catch {
      ctx.timeZoneName = null;
    }
  }

  const dateOnly = ctx.iso.slice(0, 10);
  const timeOnly = ctx.iso.slice(11, 19);
  const lines = [
    `local_datetime_iso=${ctx.iso}`,
    `local_epoch_ms=${ctx.epochMs}`,
    ctx.timeZone ? `local_timezone=${ctx.timeZone}` : null,
    ctx.timeZoneName ? `local_timezone_name=${ctx.timeZoneName}` : null,
    `local_date=${dateOnly}`,
    `local_time=${timeOnly}`,
    ctx.display ? `local_display=${ctx.display}` : null,
    ctx.approxLocation ? `approx_location=${ctx.approxLocation}` : null,
    ctx.locale ? `system_locale=${ctx.locale}` : null
  ].filter(Boolean);

  const locationSummary = ctx.approxLocation || ctx.timeZoneName || ctx.timeZone || "the assistant's current locale";
  const displaySummary = ctx.display || ctx.iso;
  const systemPrompt = `Local context: the system clock reads ${displaySummary}${ctx.timeZone ? ` (${ctx.timeZone})` : ""}. Base all references to "today", "now", or similar on this clock (ISO ${ctx.iso}). When the user asks about the current date, time, timezone, or your location, answer using this context and mention the relevant details. Unless the user specifies otherwise, treat the assistant as operating near ${locationSummary}.`;

  return {
    ...ctx,
    userBlock: lines.length ? `[LOCAL_CONTEXT]\n${lines.join("\n")}\n[END_LOCAL_CONTEXT]` : null,
    systemPrompt
  };
}

/* ============================================================
 * POST /chat
 * - General chat & "Web Guide" overlay/information mode
 * - 일반 챗봇 및 "웹 가이드" 오버레이/정보 모드
 * ============================================================ */
app.post("/chat", async (req, res) => {
  const payload = req.body || {};
  const userMessage = payload.message || "";
  console.log("[chat] request:", userMessage?.slice?.(0, 200));

  try {
    // Explicit mode flag from client (클라이언트에서 명시하는 모드 플래그)
    const mode = payload.mode === 'web-guide' ? 'web-guide' : 'chat';
    const isWebGuide = mode === 'web-guide';

    // Respect client hint for guide type
    const clientGuideType = (typeof payload.guideType === 'string') ? payload.guideType.toLowerCase() : null;
    const guideType = isWebGuide ? (clientGuideType === 'info' ? 'info' : 'overlay') : null;
    const wantsOverlay = isWebGuide && guideType === 'overlay';
    const localContext = normalizeLocalContext(payload.local_context || payload.localContext || null);

    // System prompt describes how the assistant should behave
    // (어시스턴트의 동작 방식을 지시하는 시스템 프롬프트)
    let systemPrompt;
    if (isWebGuide) {
    systemPrompt = [
      "당신은 웹 UI 어시스턴트입니다.",
      "사용자의 요청과 함께 제공된 HTML, 요소 메타데이터, 스크린샷 정보를 분석하여 사용자의 목표를 이해하세요.",
      "사용자가 특정 동작(예: 클릭, 설정, 이동)을 요청하면, 단계별로 간결하고 명확한 실행 가이드를 제시하세요.",
      "각 단계는 실제 웹페이지의 UI 요소에 정밀하게 대응해야 하며, 가능한 경우 DOM 요소의 텍스트와 좌표 정보를 활용해 위치를 정확히 지정하세요.",
      "여러 핵심 단계(최대 3개 내외)에 대해 각각 오버레이를 반환해도 됩니다. 우선순위가 높은 순서로 제시하세요.",
      "오버레이 좌표는 스크린샷 기준으로 계산하고, DOM 요소의 사각형과 정밀히 일치해야 합니다.",
      "DOM 요소의 실제 텍스트(innerText, aria-label 등)를 기준으로 매칭하고, 유사도는 대소문자 구분 없이 계산하세요. 동일하거나 유사한 텍스트가 여러 개일 경우, 화면에 가장 잘 보이는(뷰포트 내) 요소를 선택하세요.",
      "작거나 클릭 불가능한 영역은 제외하고, 버튼(button), 링크(a), 메뉴 항목(role=button/menuitem 등) 같은 명확한 인터랙션 요소만 선택하세요.",
      "JSON 응답에는 explanation_md(설명), steps(단계 배열), overlays(좌표 정보 배열), target_indexes(요소 인덱스 배열), coord_space(좌표계 문자열)가 반드시 포함되어야 합니다.",
      "각 단계의 수는 3~7개 이내로 유지하며, 단계마다 오버레이를 통해 실제 인터랙션 가능한 요소를 가리키세요.",
      "이미 로그인되어 있거나 해당 페이지 상태가 이미 요청된 목표와 일치하는 경우, 불필요한 단계를 생략하세요.",
      "요소 매칭이 불확실할 경우, overlay를 비워두고 explanation_md에 ‘확인 질문’을 짧게 포함하세요.",
      "사용자의 최근 메시지와 직전 컨텍스트를 고려해, 이미 수행한 단계를 반복하지 말고 그다음 의미 있는 단계부터 안내를 이어가세요.",
      "설명은 가능한 간결한 Markdown 형식을 사용하세요."
    ].join(" ");
    systemPrompt += " 좌표 원칙: coord_space='screenshot'으로, x/y/width/height는 스크린샷 픽셀 단위입니다.";
    systemPrompt += " 스크린샷은 캡처 시점의 뷰포트(window.scrollX, window.scrollY) 영역을 잘라낸 것입니다. 즉, document 좌표 (left,top)의 요소를 가리키려면 screenshot.x = left - scroll.x, screenshot.y = top - scroll.y 로 계산하세요.";
    systemPrompt += " width,height는 DOM 사각형 크기를 그대로 사용합니다. step_index를 채우고, label은 텍스트만 포함하세요(접두 번호 금지).";
    systemPrompt += " target_indexes는 요소 요약과 가능한 정확히 매칭되도록 선택하되, 오버레이 좌표가 그 요소 사각형과 일치하는지 교차검증하세요.";
    systemPrompt += " 사이드 패널, 확장 UI, 또는 화면 밖(off-screen) 요소는 제외하세요.";
    systemPrompt += " explanation_md와 steps에는 JSON을 넣지 말고, 사람 읽는 한 줄 요약만 작성하세요. overlays/target_indexes는 JSON으로만 message 바디에서 반환하세요.";
    systemPrompt += " 스크롤이 움직인 상태일 경우, 움직인 스크롤 좌표를 반영해서 초기 오버레이 위치에 더해서 반영하여 표시하세요.";
    systemPrompt += " 여러 단계의 작업을 수행할 때는, 사용자가 페이지를 이동하면 다음 단계의 오버레이를 새 HTML에 맞춰 이어서 표시하세요.";
    systemPrompt += " 모든 좌표는 상대좌표가 아닌 절대 페이지(document) 기준 픽셀 단위로 계산해야 합니다.";
  } else {
    // Safe default for regular chat mode
    systemPrompt = [
      'You are a helpful, concise assistant.',
      'Answer directly based on the user question.',
      'If page context is provided, summarize or explain it clearly.'
    ].join(' ');
  }


    // Build messages for OpenAI request (OpenAI 요청 메시지 구성)
    const userContent = [];
    const trimmedMessage = (userMessage || "").toString().trim();
    if (trimmedMessage) {
      userContent.push({ type: "text", text: trimmedMessage });
    }

    if (localContext?.userBlock) {
      userContent.push({ type: "text", text: localContext.userBlock });
    }

    // Optional: page text or HTML snippets (페이지 텍스트/HTML 스니펫 추가)
    if (typeof payload.content === "string" && payload.content.trim()) {
      userContent.push({
        type: "text",
        text: "[PAGE_TEXT_SNIPPET_START]\n" + payload.content.slice(0, 10000) + "\n[PAGE_TEXT_SNIPPET_END]"
      });
    }

    if (typeof payload.html === "string" && payload.html.trim()) {
      userContent.push({
        type: "text",
        text: "[PAGE_HTML_SNIPPET_START]\n" + payload.html.slice(0, 15000) + "\n[PAGE_HTML_SNIPPET_END]"
      });
    }

    // Page state hints to avoid redundant steps (로그인/네비게이션 중복 방지용 상태 정보)
    if (payload.page_state) {
      const st = payload.page_state || {};
      const lines = [];
      if (st.currentSectionGuess) lines.push(`current_section_guess=${st.currentSectionGuess}`);
      if (typeof st.loggedInGuess === 'boolean') lines.push(`logged_in_guess=${st.loggedInGuess}`);
      if (typeof st.sidebarOpenGuess === 'boolean') lines.push(`sidebar_open_guess=${st.sidebarOpenGuess}`);
      if (st.h1) lines.push(`h1=${st.h1}`);
      if (st.breadcrumb) lines.push(`breadcrumb=${st.breadcrumb}`);
      if (st.host) lines.push(`host=${st.host}`);
      userContent.push({
        type: 'text',
        text: "[PAGE_STATE]\n" + lines.join('\n') + "\nRules: If logged_in_guess is true, do not instruct to login. If current_section_guess already matches the requested area, skip redundant navigation (e.g., don't say 'click EC2' when already in EC2). Prefer target_indexes whose text closely matches the user's actual instruction keywords; avoid assuming default actions. If the user asks about an error, prioritise diagnosing the visible error on the page and propose concrete fixes before suggesting unrelated actions.\n[END_PAGE_STATE]"
      });
    }

    // Optional: element metadata summary (요소 메타데이터 요약)
    if (Array.isArray(payload.elements) && payload.elements.length) {
      const elementSummary = payload.elements
        .slice(0, 120)
        .map((el, idx) => {
          const label = (el.text || "").replace(/\s+/g, " ").trim().slice(0, 80);
          const rect = el.rect || {};
          const role = el.role ? ` role=${el.role}` : '';
          const id = el.id ? ` id=${el.id}` : '';
          const cls = el.class ? ` class=${(el.class||'').toString().split(/\s+/).slice(0,2).join('.')}` : '';
          const sel = el.selector ? ` selector=${el.selector}` : '';
          return `${idx}. <${el.tag || "unknown"}${role}${id}${cls}${sel}> "${label}" @(${Math.round(rect.left || rect.x || 0)},${Math.round(rect.top || rect.y || 0)},${Math.round(rect.width || 0)}x${Math.round(rect.height || 0)})`;
        })
        .join("\n");
      userContent.push({
        type: "text",
        text: "[ELEMENT_SUMMARY_START]\n" + elementSummary + "\n[ELEMENT_SUMMARY_END]"
      });
    }

    // Optional: screenshot (스크린샷)
    if (payload.screenshot) {
      userContent.push({ type: "image_url", image_url: { url: payload.screenshot } });
    }

    if (!userContent.length) {
      userContent.push({ type: "text", text: "No user prompt provided." });
    }

    // Provide screen/page layout so the model avoids side panel regions
    if (isWebGuide) {
      const vp = payload.page_viewport_rect || null;
      const dpr = typeof payload.device_pixel_ratio === 'number' ? payload.device_pixel_ratio : null;
      const shot = payload.screenshot_info || null;
      const scroll = payload.scroll || { x: 0, y: 0 };
      const lines = [];
      if (vp) lines.push(`page_viewport_css=(x:${vp.x||0},y:${vp.y||0},w:${vp.width||0},h:${vp.height||0})`);
      if (dpr) lines.push(`device_pixel_ratio=${dpr}`);
      if (shot && (shot.width || shot.height)) lines.push(`screenshot_pixels=(w:${shot.width||0},h:${shot.height||0})`);
      if (typeof scroll.x === 'number' || typeof scroll.y === 'number') lines.push(`scroll=(x:${scroll.x||0},y:${scroll.y||0})`);
      if (lines.length) {
        userContent.push({
          type: 'text',
          text: "[SCREEN_LAYOUT]\n" + lines.join("\n") + "\nRules: Return overlays with coord_space='screenshot'. For each overlay: x = element.left - scroll.x, y = element.top - scroll.y, width = element.width, height = element.height (all in screenshot pixels = viewport_css * device_pixel_ratio). Clip overlays to page_viewport_css and avoid side panels. Ensure target_indexes match the chosen elements. If an error/alert suggests an alternative path (e.g., change region), include that target as an additional overlay step.\n[END_SCREEN_LAYOUT]"
        });
      }
    }

    // Explicit guide-type instruction (가이드 타입 명시)
    if (isWebGuide) {
      if (guideType === "info") {
        userContent.push({
          type: "text",
          text: "Guide type: info. Respond with a JSON object containing explanation_md and steps that summarise the current site. overlays and target_indexes must be empty arrays."
        });
      } else {
        userContent.push({
          type: "text",
          text: "Guide type: overlay. Return a JSON object with explanation_md, steps, overlays, and target_indexes highlighting actionable UI elements."
        });
      }
    }

    const messages = [];
    if (typeof systemPrompt === 'string' && systemPrompt.trim()) {
      messages.push({ role: 'system', content: systemPrompt });
    }
    if (localContext?.systemPrompt) {
      messages.push({ role: 'system', content: localContext.systemPrompt });
    }

    // Include brief conversation history if provided (요청에 과거 대화 포함 시 사용)
    if (Array.isArray(payload.history) && payload.history.length) {
      const trimmedHistory = payload.history.slice(-8);
      for (const turn of trimmedHistory) {
        const role = turn?.role === 'assistant' ? 'assistant' : 'user';
        const text = (turn?.text || '').toString().trim();
        if (!text) continue;
        messages.push({ role, content: text.slice(0, 1200) });
      }
    }

    messages.push({ role: "user", content: userContent });

    console.log("[chat] context summary:", {
      mode: isWebGuide ? "web-guide" : "chat",
      htmlLength: typeof payload.html === "string" ? payload.html.length : 0,
      elementCount: Array.isArray(payload.elements) ? payload.elements.length : 0,
      hasScreenshot: Boolean(payload.screenshot),
      guideType: isWebGuide ? guideType : null
    });

    // Build OpenAI request body (OpenAI 요청 본문 구성)
    const body = {
      model: openaiVisionModel,
      messages,
      max_tokens: 1500,
      temperature: 0.0
    };
    if (isWebGuide) {
      // Enforce JSON object response in web-guide mode (웹 가이드 모드에서 JSON 강제)
      body.response_format = { type: "json_object" };
    }

    // Send request to OpenAI (OpenAI로 요청 전송)
    const openaiRes = await fetch(OPENAI_CHAT_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${openaiKey}`,
      },
      body: JSON.stringify(body),
    });

    const openaiStatus = openaiRes.status;
    const openaiData = await openaiRes.json().catch(err => {
      console.error("OpenAI JSON parse error:", err);
      return null;
    });

    if (!openaiRes.ok) {
      console.error("OpenAI error response:", openaiStatus, openaiData);
      // 화면/클라이언트에 보이는 문자열은 한국어
      return res.status(502).json({
        reply: `OpenAI 오류: ${openaiStatus}`,
        message: openaiData?.error?.message || null,
        raw: openaiData
      });
    }

    console.log("[chat] OpenAI status:", openaiStatus);

    // Extract content safely (응답 콘텐츠 안전하게 추출)
    const choice = Array.isArray(openaiData?.choices) ? openaiData.choices[0] : null;
    let content = choice?.message?.content ?? choice?.message ?? null;

    // If array of parts, join texts (파트 배열이면 텍스트 결합)
    if (Array.isArray(content)) {
      content = content
        .map((part) => {
          if (typeof part === "string") return part;
          if (part && typeof part.text === "string") return part.text;
          return "";
        })
        .filter(Boolean)
        .join("\n\n");
    }

    if (!content && typeof openaiData === "string") {
      content = openaiData;
    }

    if (content) {
      console.log("[chat] OpenAI content preview:", String(content).slice(0, 200));
    }

    // Branch parsing behavior by mode
    let parsed = null;
    if (isWebGuide) {
      // Parse as JSON if possible; else fallback to explanation_md (가능하면 JSON 파싱, 실패 시 explanation_md로 반환)
      if (typeof content === "object" && content !== null) {
        parsed = content;
      } else if (typeof content === "string") {
        try {
          parsed = JSON.parse(content);
        } catch (e) {
          const m = content.match(/\{[\s\S]*\}/);
          if (m) {
            try {
              parsed = JSON.parse(m[0]);
            } catch {
              parsed = { explanation_md: content };
            }
          } else {
            parsed = { explanation_md: content };
          }
        }
      } else {
        parsed = { explanation_md: "OpenAI에서 사용할 수 있는 응답을 받지 못했습니다." };
      }
    } else {
      // Chat mode: return plain text reply
      parsed = { reply: typeof content === 'string' ? content : JSON.stringify(content) };
    }

    if (isWebGuide) {
      // Ensure steps is an array (steps 배열 보정)
      if (!Array.isArray(parsed.steps) && typeof parsed.explanation_md === "string") {
        parsed.steps = [parsed.explanation_md.split("\n").slice(0, 4).join(" ").slice(0, 300)];
      }
    }

    // Normalize overlays (오버레이 좌표 정규화)
    if (isWebGuide && Array.isArray(parsed.overlays)) {
      parsed.overlays = parsed.overlays.map(o => ({
        x: Number(o.x ?? 0),
        y: Number(o.y ?? 0),
        width: Number((o.width ?? o.w ?? 0)),
        height: Number((o.height ?? o.h ?? 0)),
        label: String(o.label || ''),
        step_index: Number.isInteger(o.step_index) ? o.step_index : undefined
      }));
    }

    // Web‑guide fallback: synthesize targets from steps + element summary if none provided
    if (isWebGuide && wantsOverlay) {
      const coordSpace = typeof parsed.coord_space === 'string' ? parsed.coord_space.toLowerCase() : null;
      const hasTargets = (Array.isArray(parsed.target_indexes) && parsed.target_indexes.length > 0) ||
                         (Array.isArray(parsed.overlays) && parsed.overlays.length > 0);
      const elList = Array.isArray(payload.elements) ? payload.elements : [];
      const useScreenshotSpace = coordSpace === 'screenshot';
      // 1) If the model returned target_indexes, prefer the model's own overlays when they look reasonable;
      //    otherwise rebuild overlays from DOM rects for those targets.
      if (!useScreenshotSpace && Array.isArray(parsed.target_indexes) && parsed.target_indexes.length > 0 && elList.length) {
        const stepLen = Array.isArray(parsed.steps) ? parsed.steps.length : parsed.target_indexes.length;
        const rebuilt = new Array(stepLen).fill(null);
        for (let i = 0; i < parsed.target_indexes.length; i++) {
          const idx = parsed.target_indexes[i];
          if (!Number.isInteger(idx)) continue;
          const el = elList[idx] || {}; const r = el.rect || {};
          const labelText = (typeof parsed.steps?.[i] === 'string')
            ? parsed.steps[i]
            : (parsed.steps?.[i]?.instruction || parsed.steps?.[i]?.title || parsed.steps?.[i]?.description || parsed.steps?.[i]?.step || "");
          // decide if model overlay looks reasonable
          const modelOv = Array.isArray(parsed.overlays) ? parsed.overlays[i] : null;
          let keepModel = false;
          if (modelOv && typeof modelOv.width === 'number' && typeof modelOv.height === 'number') {
            const vp = payload.page_viewport_rect || {};
            const tooWide = vp.width ? modelOv.width > vp.width * 0.8 : false;
            const tooTall = vp.height ? modelOv.height > vp.height * 0.8 : false;
            const nearTop = typeof modelOv.y === 'number' ? modelOv.y < ((payload.scroll?.y||0) + 80) : false;
            keepModel = !(tooWide || tooTall || nearTop);
          }
          rebuilt[i] = keepModel ? {
            x: Number(modelOv.x || 0),
            y: Number(modelOv.y || 0),
            width: Number(modelOv.width || 0),
            height: Number(modelOv.height || 0),
            label: labelText,
            step_index: i
          } : {
            x: Number(r.left ?? r.x ?? 0),
            y: Number(r.top ?? r.y ?? 0),
            width: Number(r.width ?? 0),
            height: Number(r.height ?? 0),
            label: labelText,
            step_index: i
          };
        }
        // If we rebuilt at least one, replace overlays array
        if (rebuilt.some(Boolean)) {
          parsed.overlays = rebuilt.map(o => o || null);
        }
      }

      // 2) If we still have no valid targets, synthesize from steps + element summary
      if (!useScreenshotSpace && !hasTargets && elList.length && Array.isArray(parsed.steps) && parsed.steps.length) {
        const normalize = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
        const tokenize = (s) => normalize(s)
          .replace(/[*_`~!@#$%^&()\[\]{};:,\.?<>\-\/=+|\\]/g, " ")
          .split(/\s+/)
          .filter(w => w && w.length >= 2 && !['click','press','select','choose','open','go','the','and','for','to','in','on','of','with','버튼','클릭','선택','누르','열','에서','하기','합니다','하기','으로','으로','및'].includes(w));

        // Extract quoted phrases (예: '인스턴스 시작')
        const extractPhrases = (s) => {
          const ph = [];
          const re = /["'“”‘’]([^"'“”‘’]+)["'“”‘’]/g;
          let m; while ((m = re.exec(String(s))) !== null) { ph.push(normalize(m[1])); }
          return ph;
        };

        const layout = payload.page_state?.layout || {};
        const contentLeft = typeof layout.content_left === 'number' ? layout.content_left : null;
        const vp = payload.page_viewport_rect || null;

        const parseRgb = (rgb) => {
          if (!rgb || typeof rgb !== 'string') return null;
          const m = rgb.replace(/\s+/g,'').match(/^rgba?\((\d+),(\d+),(\d+)(?:,(\d*\.?\d+))?\)$/i);
          if (!m) return null; const r = +m[1], g = +m[2], b = +m[3];
          return { r, g, b };
        };
        const rgbToHsl = ({r,g,b}) => {
          r/=255; g/=255; b/=255; const max=Math.max(r,g,b), min=Math.min(r,g,b);
          let h,s,l=(max+min)/2; if(max===min){h=s=0;} else{const d=max-min; s=l>0.5?d/(2-max-min):d/(max+min);
            switch(max){case r:h=(g-b)/d+(g<b?6:0);break;case g:h=(b-r)/d+2;break;case b:h=(r-g)/d+4;break;} h*=60;}
          return {h,s,l};
        };

        const scored = (stepText) => {
          const words = tokenize(stepText);
          const phrases = extractPhrases(stepText);
          let best = { idx: -1, score: 0 };
          // If phrases exist, require candidates to include at least one phrase
          const phraseMatches = new Set();
          if (phrases.length) {
            for (let i = 0; i < elList.length; i++) {
              const txt = normalize(elList[i]?.text || "");
              if (!txt) continue;
              if (phrases.some(p => p && txt.includes(p))) phraseMatches.add(i);
            }
          }

          for (let i = 0; i < elList.length; i++) {
            const el = elList[i] || {};
            const txt = normalize(el.text || "");
            if (!txt) continue;
            // If phrases exist and this candidate does not contain them, skip
            if (phrases.length && !phraseMatches.has(i)) continue;
            let s = 0;
            for (const w of words) { if (txt.includes(w)) s += 2; }
            for (const p of phrases) { if (p && txt.includes(p)) s += 6; }
            if (el.is_button || /button|menuitem/i.test(el.role || "") || /button/i.test(el.tag || "")) s += 2.5;
            if (el.visible === false) s -= 2.5;
            const r = el.rect || {};
            if ((r.width || 0) >= 40 && (r.height || 0) >= 24) s += 0.5;
            if (el.in_nav) s -= 3; // strong penalty for nav/sidebar
            if (el.in_header) s -= 3.5; // strong penalty for top header/account bar
            if (contentLeft !== null && typeof r.x === 'number') {
              if (r.x < contentLeft + 10) s -= 2; // penalize left of main content
              else s += 0.7; // slight preference for inside main content
            }
            // Prefer elements reasonably within viewport (center bias)
            if (vp && typeof r.x === 'number' && typeof r.y === 'number') {
              const cx = r.x + (r.width||0)/2;
              const cy = r.y + (r.height||0)/2;
              const inH = cx >= 0 && cx <= (vp.width||0) + (payload.scroll?.x||0) + 20;
              const inV = cy >= (payload.scroll?.y||0) - 100 && cy <= (payload.scroll?.y||0) + (vp.height||0) + 100;
              if (inH && inV) s += 0.5;
              // penalize header band near top of page
              const topBand = (payload.scroll?.y || 0) + 120;
              if (cy < topBand) s -= 4.0;
            }
            // Prefer larger clickable area up to a cap; penalize huge containers
            const area = (r.width||0) * (r.height||0);
            if (area) s += Math.min(area / 30000, 1.2);
            if (vp && (r.width > (vp.width||0) * 0.8 || r.height > (vp.height||0) * 0.8)) s -= 3.0;

            // Visual style cues: orange-ish CTA boost
            const bg = el?.style?.bg || '';
            const rgb = parseRgb(bg); if (rgb) {
              const {h,s,l} = rgbToHsl(rgb);
              if (!Number.isNaN(h) && s >= 0.45 && l >= 0.30 && l <= 0.78 && h >= 20 && h <= 50) {
                s += 2.0; // orange button boost
              }
            }
            // Keyword-specific boost (start/launch/시작) only if the user asked for it
            const userWantsStart = /\b(start|launch|시작|인스턴스\s*시작|launch\s*instance|create\s*instance)\b/i.test(trimmedMessage || '');
            if (userWantsStart && /\b(start|launch|시작)\b/i.test(stepText) && /시작|launch|start/i.test(txt)) s += 1.2;
            // If instruction mentions button/click, prefer button-like; penalize others
            const mentionsButton = /\b(button|버튼|click|클릭)\b/i.test(stepText);
            if (mentionsButton && !(el.is_button || /button|menuitem/i.test(el.role || "") || /button/i.test(el.tag || ""))) s -= 4.0;
            if (s > best.score) best = { idx: i, score: s };
          }
          return best;
        };

        // Initialize arrays aligned to steps length
        const stepLen = parsed.steps.length;
        parsed.target_indexes = new Array(stepLen).fill(null);
        parsed.overlays = new Array(stepLen).fill(null);
        const used = new Set();
        const max = Math.min(6, stepLen);
        for (let i = 0; i < max; i++) {
          const step = parsed.steps[i];
          const stepText = typeof step === 'string' ? step : (step?.title || step?.description || step?.step || "");
          const best = scored(stepText);
          if (best.idx >= 0 && best.score >= 2.5 && !used.has(best.idx)) {
            used.add(best.idx);
            parsed.target_indexes[i] = best.idx;
            const el = elList[best.idx] || {};
            const rr = el.rect || {};
            parsed.overlays[i] = ({
              x: Number(rr.left ?? rr.x ?? 0),
              y: Number(rr.top ?? rr.y ?? 0),
              width: Number(rr.width ?? 0),
              height: Number(rr.height ?? 0),
              label: stepText,
              step_index: i
            });
          }
        }
      }
      
      // 3) If overlays exist but target_indexes are missing, map overlays to nearest DOM element
      if ((!Array.isArray(parsed.target_indexes) || parsed.target_indexes.length === 0) &&
          Array.isArray(parsed.overlays) && parsed.overlays.length > 0 && elList.length) {
        try {
          const scrollX = Number(payload?.scroll?.x || 0);
          const scrollY = Number(payload?.scroll?.y || 0);
          const iou = (a, b) => {
            const ax2 = a.x + a.width, ay2 = a.y + a.height;
            const bx2 = b.x + b.width, by2 = b.y + b.height;
            const x1 = Math.max(a.x, b.x), y1 = Math.max(a.y, b.y);
            const x2 = Math.min(ax2, bx2), y2 = Math.min(ay2, by2);
            const iw = Math.max(0, x2 - x1), ih = Math.max(0, y2 - y1);
            const inter = iw * ih; const ua = a.width * a.height + b.width * b.height - inter;
            return ua > 0 ? inter / ua : 0;
          };
          const toDoc = (ov) => ({ x: Number(ov.x||0), y: Number(ov.y||0), width: Number(ov.width||0), height: Number(ov.height||0) });
          const toDocFromViewport = (ov) => ({ x: Number(ov.x||0) + scrollX, y: Number(ov.y||0) + scrollY, width: Number(ov.width||0), height: Number(ov.height||0) });

          parsed.target_indexes = new Array(parsed.overlays.length).fill(null);
          const repaired = new Array(parsed.overlays.length).fill(null);
          for (let i = 0; i < parsed.overlays.length; i++) {
            const ov = parsed.overlays[i]; if (!ov) continue;
            const candA = toDoc(ov);            // assume document-space coords
            const candB = toDocFromViewport(ov); // assume viewport-space coords
            let best = { idx: -1, score: -1, rect: null };
            for (let j = 0; j < elList.length; j++) {
              const r = elList[j]?.rect || {};
              const er = { x: Number((r.left ?? r.x ?? 0)), y: Number((r.top ?? r.y ?? 0)), width: Number(r.width||0), height: Number(r.height||0) };
              const s = Math.max(iou(candA, er), iou(candB, er));
              if (s > best.score) best = { idx: j, score: s, rect: er };
            }
            if (best.idx >= 0 && best.score > 0) {
              parsed.target_indexes[i] = best.idx;
              repaired[i] = { x: best.rect.x, y: best.rect.y, width: best.rect.width, height: best.rect.height, step_index: i, label: (parsed.steps?.[i] || '') };
            }
          }
          if (repaired.some(Boolean)) {
            parsed.overlays = repaired.map((r, i) => r || toDocFromViewport(parsed.overlays[i]));
          }
        } catch (e) {
          console.warn('[server] overlay->element mapping failed:', e?.message || e);
        }
      }
    }

    // Correction pass: only if current picks look wrong (nav/header/huge/left-of-content)
    if (isWebGuide && wantsOverlay && Array.isArray(parsed.steps) && Array.isArray(payload.elements) && payload.elements.length) {
      const coordSpace = typeof parsed.coord_space === 'string' ? parsed.coord_space.toLowerCase() : null;
      if (coordSpace !== 'screenshot') {
      try {
        const elList = payload.elements;
        const layout = payload.page_state?.layout || {};
        const contentLeft = typeof layout.content_left === 'number' ? layout.content_left : null;

        const normalize = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
        const tokenize = (s) => normalize(s)
          .replace(/[*_`~!@#$%^&()\[\]{};:,\.?<>\-\/=+|\\]/g, " ")
          .split(/\s+/)
          .filter(w => w && w.length >= 2 && !['click','press','select','choose','open','go','the','and','for','to','in','on','of','with','버튼','클릭','선택','누르','열','에서','하기','합니다','하기','으로','및'].includes(w));
        const extractPhrases = (s) => {
          const ph = []; const re = /["'“”‘’]([^"'“”‘’]+)["'“”‘’]/g; let m;
          while ((m = re.exec(String(s))) !== null) ph.push(normalize(m[1]));
          return ph;
        };

        const scoreEl = (el, words, phrases) => {
          const txt = normalize(el?.text || "");
          if (!txt) return -1e9;
          let s = 0;
          for (const w of words) if (txt.includes(w)) s += 2;
          for (const p of phrases) if (p && txt.includes(p)) s += 6;
          if (el.is_button || /button|menuitem/i.test(el.role || "") || /button/i.test(el.tag || "")) s += 2;
          const r = el.rect || {};
          if ((r.width || 0) >= 40 && (r.height || 0) >= 24) s += 0.5;
          if (el.in_nav) s -= 3;
          if (contentLeft !== null && typeof r.x === 'number') {
            if (r.x < contentLeft + 10) s -= 2; else s += 0.7;
          }
          const area = (r.width||0) * (r.height||0);
          if (area) s += Math.min(area / 30000, 1.2);
          return s;
        };

        const ensureOverlayFor = (idx, labelText) => {
          const el = elList[idx] || {}; const r = el.rect || {};
          return {
            x: Number(r.left ?? r.x ?? 0),
            y: Number(r.top ?? r.y ?? 0),
            width: Number(r.width ?? 0),
            height: Number(r.height ?? 0),
            label: labelText
          };
        };

        // Only correct entries that currently point to nav/aside or far left of content
        const stepCount = Math.min(parsed.steps.length, 10);
        parsed.target_indexes = Array.isArray(parsed.target_indexes) ? parsed.target_indexes : [];
        parsed.overlays = Array.isArray(parsed.overlays) ? parsed.overlays : [];

        // Determine if any current target is suspicious; if none are, skip heavy correction
        const suspicious = (i) => {
          const ov = parsed.overlays?.[i];
          const tIdx = parsed.target_indexes?.[i];
          const el = Number.isInteger(tIdx) ? elList[tIdx] : null;
          const r = (ov || {}).width && (ov || {}).height ? ov : (el?.rect || {});
          if (!r) return false;
          const tooWide = vp && r.width > (vp.width||0) * 0.8;
          const tooTall = vp && r.height > (vp.height||0) * 0.8;
          const leftOfMain = contentLeft !== null && typeof r.x === 'number' && r.x < contentLeft + 10;
          const isNav = Boolean(el?.in_nav); const isHeader = Boolean(el?.in_header);
          return tooWide || tooTall || leftOfMain || isNav || isHeader;
        };

        let needCorrection = false;
        for (let i = 0; i < stepCount; i++) { if (suspicious(i)) { needCorrection = true; break; } }
        if (!needCorrection) { /* keep current selections */ throw new Error('skip-correction'); }

        for (let i = 0; i < stepCount; i++) {
          const step = parsed.steps[i];
          const labelText = typeof step === 'string' ? step : (step?.instruction || step?.title || step?.description || step?.step || "");
          const words = tokenize(labelText); const phrases = extractPhrases(labelText);

          const currentIdx = parsed.target_indexes[i];
          const currentEl = Number.isInteger(currentIdx) ? elList[currentIdx] : null;
          const currentRect = currentEl?.rect || {};
          const currentInNav = Boolean(currentEl?.in_nav || (contentLeft !== null && typeof currentRect.x === 'number' && currentRect.x < contentLeft + 10));

          // Find best non-nav candidate
          let best = { idx: -1, score: -1e9 };
          for (let j = 0; j < elList.length; j++) {
            const s = scoreEl(elList[j], words, phrases);
            if (s > best.score) best = { idx: j, score: s };
          }

          // If current points to nav or is missing/weak, replace
          const currentScore = currentEl ? scoreEl(currentEl, words, phrases) : -1e9;
          const shouldReplace = currentInNav || currentScore < 2.5;
          if (best.idx >= 0 && (shouldReplace || best.score - currentScore > 0.8)) {
            parsed.target_indexes[i] = best.idx;
            const ov = ensureOverlayFor(best.idx, labelText);
            ov.step_index = i;
            parsed.overlays[i] = ov;
          } else if (Number.isInteger(currentIdx) && !parsed.overlays[i]) {
            // Ensure overlay present when we only had target_indexes
            const ov = ensureOverlayFor(currentIdx, labelText);
            ov.step_index = i;
            parsed.overlays[i] = ov;
          }
        }
      } catch (e) {
        if (String(e?.message) !== 'skip-correction') {
          console.warn('[server] correction pass failed:', e?.message);
        }
      }
      }
    }

    // If not overlay mode, force empty overlays/targets (오버레이 모드가 아니면 배열 비우기)
    if (isWebGuide && !wantsOverlay) {
      parsed.overlays = [];
      parsed.target_indexes = [];
    }

    console.log("[chat] parsed summary:", {
      explanationLength: typeof parsed?.explanation_md === "string" ? parsed.explanation_md.length : 0,
      steps: Array.isArray(parsed?.steps) ? parsed.steps.length : 0,
      overlays: Array.isArray(parsed?.overlays) ? parsed.overlays.length : 0,
      targetIndexes: Array.isArray(parsed?.target_indexes) ? parsed.target_indexes.length : 0
    });
    if (Array.isArray(parsed?.overlays) && parsed.overlays.length) {
      console.log("[chat] overlay preview:", parsed.overlays.slice(0, 3));
    }

    // Attach guide_type for client-side rendering decisions (클라이언트 표시 제어용 guide_type 포함)
    if (isWebGuide) {
      parsed.guide_type = guideType || "info";
      if (!parsed.coord_space && payload.screenshot) {
        parsed.coord_space = 'screenshot';
      }
    }

    // Send final JSON (최종 JSON 반환)
    res.json(parsed);

  } catch (err) {
    console.error("[chat] handler failed:", err);
    // 화면/클라이언트에 보이는 문자열은 한국어
    res.status(500).json({ reply: "서버 오류가 발생했습니다." });
  }
});

/* ============================================================
 * POST /image
 * - Image captioning using a vision model
 * - 비전 모델을 사용한 이미지 설명 생성
 * ============================================================ */
app.post("/image", async (req, res) => {
  const { image } = req.body;
  console.log("[image] request received");

  try {
    // Quick size guard (간단한 페이로드 크기 제한)
    if (typeof image === "string" && image.length > 2_000_000) {
      return res.status(413).json({ reply: "이미지 크기가 너무 큽니다. 전송 전에 압축해 주세요." });
    }

    const response = await fetch(OPENAI_CHAT_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${openaiKey}`,
      },
      body: JSON.stringify({
        model: openaiVisionModel,
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "이미지를 설명해줘" },
              { type: "image_url", image_url: { url: image } },
            ],
          },
        ],
      }),
    });

    const data = await response.json();
    if (!response.ok) {
      return res
        .status(502)
        .json({ reply: `OpenAI 이미지 처리 오류: ${response.status} ${data?.error?.message || JSON.stringify(data)}` });
    }

    const choice = data.choices?.[0]?.message?.content;
    if (!choice) return res.status(500).json({ reply: "OpenAI에서 이미지 설명을 반환하지 않았습니다." });

    // 화면/클라이언트에 보이는 문자열은 한국어
    res.json({ reply: choice });
  } catch (err) {
    console.error("[image] handler failed:", err);
    res.status(500).json({ reply: "서버 오류가 발생했습니다." });
  }
});

/* ============================================================
 * POST /log
 * - Mirror logging endpoint used by the extension for debugging
 * - 확장 프로그램 디버깅용 로그 미러 엔드포인트
 * ============================================================ */
app.post("/log", (req, res) => {
  console.log("=== DATA RECEIVED ===");
  console.log("TYPE:", req.body.type);

  if (req.body.type === "html") {
    console.log(req.body.data?.slice(0, 1000));
  } else if (req.body.type === "image") {
    console.log(req.body.data?.slice(0, 200));
  } else if (req.body.type === "html+image") {
    console.log("HTML:", req.body.data?.html?.slice(0, 500));
    console.log("IMAGE:", req.body.data?.screenshot?.slice(0, 200));
  } else {
    try {
      console.log("DATA:", JSON.stringify(req.body, null, 2));
    } catch (e) {
      console.log("DATA:", req.body);
    }
  }

  console.log("=== END ===\n");
  res.sendStatus(200);
});

/* -----------------------------
 * Start Server (서버 시작)
 * ----------------------------- */
app.listen(3000, () =>
  console.log(`Server running at http://localhost:3000 (vision model: ${openaiVisionModel})`)
);
