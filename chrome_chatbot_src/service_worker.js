// 확장 설치 직후: 모든 창에서 사이드패널 쓰도록 설정
chrome.runtime.onInstalled.addListener(async () => {
  // 사이드패널을 확장 아이콘으로도 열 수 있게 설정
  chrome.sidePanel.setOptions({ path: "panel.html", enabled: true });

  // 현재 모든 탭에서 바로 열고 싶다면:
  const tabs = await chrome.tabs.query({ currentWindow: true });
  for (const tab of tabs) {
    try {
      await chrome.sidePanel.open({ tabId: tab.id });
    } catch (e) {
      // 일부 페이지는 제한될 수 있으니 무시
    }
  }
});

// 탭이 활성화될 때마다 자동으로 열기 (원하면 특정 도메인만)
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    await chrome.sidePanel.setOptions({ tabId, path: "panel.html", enabled: true });
    await chrome.sidePanel.open({ tabId });
  } catch (e) {
    // 권한/페이지 제한 등 무시
  }
});

// 특정 URL에서만 자동 오픈 (예: Figma)
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" && tab.url && tab.url.includes("figma.com/design")) {
    try {
      await chrome.sidePanel.setOptions({ tabId, path: "panel.html", enabled: true });
      await chrome.sidePanel.open({ tabId });
    } catch (e) {}
  }
});

// ------------------------
// 메시지 리스너 통합
// ------------------------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "getTabs") {
    chrome.tabs.query({}, (tabs) => {
      sendResponse({
        tabs: tabs.map(t => ({ id: t.id, title: t.title, url: t.url, favIcon: t.favIconUrl }))
      });
    });
    return true; // 비동기 응답
  }

  // 이미지 전용/HTML 전용 캡처 모드는 제거하고,
  // 통합 수집(getPageData)만 지원합니다.

  // 기존 getPageData 통합
  if (msg.action === "getPageData") {
    (async () => {
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab || !tab.id) return sendResponse(null);
        if (!tab.url || tab.url.startsWith("chrome://")) {
          sendResponse({ error: "Chrome does not allow inspecting this page." });
          return;
        }

        // content.js가 주입되어 있지 않다면 주입
        try {
          await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ['content.js'] });
        } catch (e) { /* 이미 주입되어 있을 수 있음 */ }

        const htmlInfo = await new Promise((resolve) => {
          chrome.tabs.sendMessage(tab.id, { action: 'getPageHTML' }, (resp) => {
            if (chrome.runtime.lastError) return resolve(null);
            resolve(resp || null);
          });
        });

        // Avoid auto-expanding menus/panels. Some sites open modals/menus; keep user's UI untouched.
        // If needed later, this can be turned on behind a user toggle.

        const elementsInfo = await new Promise((resolve) => {
          chrome.tabs.sendMessage(tab.id, { action: 'findElements', deep: true }, (resp) => {
            if (chrome.runtime.lastError) return resolve({ elements: [] });
            resolve(resp || { elements: [] });
          });
        });

        const stateInfo = await new Promise((resolve) => {
          chrome.tabs.sendMessage(tab.id, { action: 'getPageState' }, (resp) => {
            if (chrome.runtime.lastError) return resolve(null);
            resolve(resp || null);
          });
        });

        let screenshotError = null;
        const screenshot = await new Promise((resolve) => {
          chrome.tabs.captureVisibleTab(tab.windowId ?? null, { format: 'png' }, (d) => {
            if (chrome.runtime.lastError) {
              screenshotError = chrome.runtime.lastError.message;
              resolve(null);
              return;
            }
            resolve(d || null);
          });
        });

        sendResponse({
          url: tab.url || '',
          title: stateInfo?.title || null,
          html: htmlInfo?.html || null,
          viewport: htmlInfo?.viewport || null,
          scroll: { x: htmlInfo?.scrollX || 0, y: htmlInfo?.scrollY || 0 },
          elements: elementsInfo?.elements || [],
          state: stateInfo || null,
          screenshot,
          screenshotError
        });
      } catch (e) {
        console.error('[sw] getPageData error', e);
        sendResponse(null);
      }
    })();
    return true; // async 응답
  }
});


// Node.js 서버로 전달
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "sendToServer") {
    fetch("http://localhost:3000/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: msg.type, data: msg.payload })
    })
    .then(() => console.log("데이터 서버로 전송 완료:", msg.type))
    .catch(err => console.error("전송 에러:", err));
  }
});
