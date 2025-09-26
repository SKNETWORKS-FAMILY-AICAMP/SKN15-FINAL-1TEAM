chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "getPageContent") {
    const bodyText = document.body ? document.body.innerText : "";
    sendResponse({ content: bodyText });
  }
  return true; // ✅ 응답 채널 유지 (안전)
});
