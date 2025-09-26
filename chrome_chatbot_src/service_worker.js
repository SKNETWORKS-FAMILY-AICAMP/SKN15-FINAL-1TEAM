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

// 탭 정보 가져오기
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "getTabs") {
    chrome.tabs.query({}, function(tabs) {
      const tabInfo = tabs.map(tab => ({
        id: tab.id,
        title: tab.title,
        url: tab.url,
        favIcon: tab.favIconUrl
      }));
      sendResponse({ tabs: tabInfo });
    });
    return true; // 비동기 응답을 위해 필요
  }
});

// 화면 캡쳐 인식
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "captureScreen") {
    chrome.tabs.captureVisibleTab(null, { format: "png" }, (dataUrl) => {
      sendResponse({ image: dataUrl });
    });
    return true; // 비동기 응답
  }
});
