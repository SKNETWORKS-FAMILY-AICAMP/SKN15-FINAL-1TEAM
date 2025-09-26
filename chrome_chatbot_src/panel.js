const statusEl = document.getElementById("status");
const messagesEl = document.getElementById("messages");
const promptEl = document.getElementById("prompt");
const uploadBtn = document.getElementById("uploadPdfBtn");
const pdfInput = document.getElementById("pdfInput");
const historyBtn = document.getElementById("historyBtn");
const historyPopup = document.getElementById("historyPopup");
const historyList = document.getElementById("historyList");

document.addEventListener("DOMContentLoaded", () => {
  try {
    if (chrome?.storage?.local) {
      loadChatHistory();
    }
    statusEl.textContent = "연결됨";
  } catch (e) {
    console.error("초기화 에러:", e);
    statusEl.textContent = "에러";
  }
});



let sessionId = Date.now().toString();

// 메시지 추가 + 저장
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
  // 히스토리에 저장 (임시 메시지는 저장 안 함)
  if (!isTemp) {
    saveMessageToHistory(text, role);
  }
  return msg;
}


// 메시지 저장 + 제목 저장
function saveMessageToHistory(text, role) {
  const sessionKey = `chat-${sessionId}`;
  const existing = JSON.parse(localStorage.getItem(sessionKey) || "[]");
  existing.push({ text, role });
  localStorage.setItem(sessionKey, JSON.stringify(existing));

  if (existing.length === 1 && role === "user") {
    localStorage.setItem(`${sessionKey}-title`, text.slice(0, 30));
  }
}

// 임시 메시지 업데이트
function updateMessage(msgEl, newContent) {
  if (msgEl && msgEl.dataset.temp === "true") {
    msgEl.textContent = newContent;
    delete msgEl.dataset.temp;
    msgEl.classList.remove("loading");

    // ✅ 실제 답변으로 히스토리에 저장
    saveMessageToHistory(newContent, "bot");
  }
}


// 메시지 전송
async function handleSend() {
  const text = promptEl.value.trim();
  if (!text) return;

  addMessage(text, "user");
  promptEl.value = "";
  const loadingMsg = addMessage("생성중.", "bot", true);

  let dots = 1;
  const interval = setInterval(() => {
    dots = (dots % 3) + 1;
    loadingMsg.textContent = "생성중" + ".".repeat(dots);
  }, 500);

  try {
    const response = await fetch("http://localhost:3000/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text })
    });
    const data = await response.json();
    clearInterval(interval);
    updateMessage(loadingMsg, data.reply);
  } catch (e) {
    clearInterval(interval);
    updateMessage(loadingMsg, "서버 연결에 문제가 있어요.");
  }
}

// 엔터 전송 / Shift+Enter 줄바꿈
promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});

// 입력창 자동 높이 조절
const baseHeight = 40;
const maxHeight = 180;
function adjustHeight() {
  promptEl.style.height = "auto";
  promptEl.style.height = Math.min(promptEl.scrollHeight, 180) + "px";
}
promptEl.addEventListener("input", adjustHeight);
promptEl.addEventListener("keyup", adjustHeight);



// PDF 업로드
uploadBtn.addEventListener("click", () => pdfInput.click());
pdfInput.addEventListener("change", () => {
  const file = pdfInput.files[0];
  if (file) {
    addMessage(`📄 선택된 PDF: ${file.name}`, "user");
  }
});

// 홈페이지 이동
document.getElementById("goHomeBtn").addEventListener("click", () => {
  window.open("https://ej-homepage.com", "_blank");
});

// 테마 전환
document.getElementById("toggleThemeBtn").addEventListener("click", () => {
  document.body.classList.toggle("dark-theme");
  document.body.classList.toggle("light-theme");
});

// 채팅 재시작
document.getElementById("resetChatBtn").addEventListener("click", () => {
  sessionId = Date.now().toString();
  messagesEl.innerHTML = "";
  addMessage("새로운 대화를 시작합니다.", "bot");
});

// 히스토리 불러오기
function loadFromHistory(id) {
  const saved = localStorage.getItem(id);
  if (saved) {
    messagesEl.innerHTML = "";
    const messages = JSON.parse(saved);
    messages.forEach(m => addMessage(m.text, m.role));
    sessionId = id.replace("chat-", "");
  }
}

// 히스토리 목록 렌더링(선택 가능하게)
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

// 히스토리 팝업 토글
historyBtn.addEventListener("click", () => {
  const isVisible = historyPopup.style.display === "block";
  historyPopup.style.display = isVisible ? "none" : "block";
  if (!isVisible) renderHistoryList();
});

// 선택 삭제 기능
document.getElementById("deleteSelectedBtn").addEventListener("click", () => {
  const checkboxes = historyList.querySelectorAll("input[type='checkbox']:checked");
  checkboxes.forEach(cb => {
    localStorage.removeItem(cb.value);
    localStorage.removeItem(`${cb.value}-title`);
  });
  renderHistoryList();
});

// 전체 삭제 기능
document.getElementById("deleteAllBtn").addEventListener("click", () => {
  Object.keys(localStorage)
    .filter(k => k.startsWith("chat-"))
    .forEach(k => localStorage.removeItem(k));
  renderHistoryList();
});

// 히스토리 팝업 외 클릭 시 자동 닫힘
document.addEventListener("click", (e) => {
  const isInside = historyPopup.contains(e.target) || historyBtn.contains(e.target);
  if (!isInside) {
    historyPopup.style.display = "none";
  }
});


// 탭 정보 요청
chrome.runtime.sendMessage({ action: "getTabs" }, function(response) {
  const tabList = document.getElementById("tabList");
  if (!tabList) return; // tabList 없으면 그냥 스킵

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

// 페이지 스크린샷 요청해서 서버로
function captureAndExplain() {
  chrome.runtime.sendMessage({ action: "captureScreen" }, (res) => {
    if (chrome.runtime.lastError) {
      console.error("캡처 실패:", chrome.runtime.lastError.message);
      displayReply("❌ 화면 캡처 권한이 없거나 실패했습니다.");
      return;
    }
    if (res?.image) {
      fetch("http://localhost:3000/image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: res.image })
      })
      .then(r => r.json())
      .then(data => displayReply(data.reply))
      .catch(() => displayReply("❌ 서버 요청 실패"));
    }
  });
}

// 사이드 패널에 페이지 요약 결과 보여주기
function displayReply(text) {
  const msg = document.createElement("div");
  msg.className = "msg bot";
  msg.textContent = text;
  document.getElementById("messages").appendChild(msg);
}

// ✅ 새로 만든 함수 (자동 실행 말고 필요할 때 호출)
function requestPageContent() {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) {
      displayReply("❌ 현재 활성 탭이 없습니다.");
      return;
    }

    chrome.tabs.sendMessage(tabs[0].id, { action: "getPageContent" }, (response) => {
      if (chrome.runtime.lastError) {
        console.warn("메시지 전달 실패:", chrome.runtime.lastError.message);
        displayReply("❌ content.js 응답을 받지 못했어요. 이 페이지에서는 실행이 제한될 수 있습니다.");
        return;
      }

      if (response?.content) {
        fetch("http://localhost:3000/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: `다음 페이지 내용을 요약해줘:\n\n${response.content}` })
        })
        .then(res => res.json())
        .then(data => displayReply(data.reply))
        .catch(() => displayReply("❌ 서버 요청 중 오류가 발생했어요."));
      } else {
        displayReply("❌ 페이지 내용을 가져오지 못했어요. 다른 탭에서 다시 시도해보세요.");
      }
    });
  });
}

// 페이지 내용 추출한거 현재 탭에 메시지 보내기
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  chrome.tabs.sendMessage(tabs[0].id, { action: "getPageContent" }, (response) => {
    // ✅ content.js 실행 안 된 경우 (권한 없는 페이지 등)
    if (chrome.runtime.lastError) {
      console.error("메시지 전달 실패:", chrome.runtime.lastError);
      displayReply("❌ content.js 응답을 받지 못했어요. 이 페이지에서는 실행이 제한될 수 있습니다.");
      return;
    }

    if (response?.content) {
      fetch("http://localhost:3000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: `다음 페이지 내용을 요약해줘:\n\n${response.content}` })
      })
      .then(res => res.json())
      .then(data => {
        const reply = data.reply;
        displayReply(reply); // 사이드패널에 표시하는 함수
      })
      .catch(err => {
        console.error("요약 요청 실패:", err);
        displayReply("❌ 서버 요청 중 오류가 발생했어요.");
      });
    } else {
      // ✅ content.js는 실행됐는데 bodyText를 못 받은 경우
      displayReply("❌ 페이지 내용을 가져오지 못했어요. 다른 탭에서 다시 시도해보세요.");
    }
  });
});


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



// lasrt error 확인
chrome.tabs.query({ active: true, currentWindow: true }, function(tabs) {
  chrome.tabs.sendMessage(tabs[0].id, { action: "getPageContent" }, function(response) {
    if (response?.content) {
      fetch("http://localhost:3000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: `지금 내가 보고 있는 페이지 내용을 설명해줘:\n\n${response.content}`
        })
      })
      .then(res => res.json())
      .then(data => {
        const reply = data.reply;
        displayReply(reply); // 사이드패널에 표시
        saveMessage(reply); // ✅ 응답 저장
      });
    } else {
      displayReply("❌ 페이지 내용을 가져오지 못했어요. 다른 탭에서 다시 시도해보세요.");
    }
  });
});
