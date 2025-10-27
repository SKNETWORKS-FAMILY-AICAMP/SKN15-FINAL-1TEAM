## 크롬 챗봇
### 폴더 개요
- manifest.json에서 사이드 패널 확장으로 선언되어 Chrome 114+ 버전에서 동작합니다.
- panel.html/css/js가 사이드 패널 UI, content.js가 웹페이지 분석 및 하이라이트 오버레이, service_worker.js가 탭 이벤트 처리 및 메시지 브릿지를 담당합니다.
- server.js는 OpenAI API와 통신하는 로컬 백엔드입니다. 확장은 기본으로 http://localhost:3000에 연결하므로 서버가 켜져 있어야 합니다.
### 사전 준비
- Node.js 18 이상 권장.
- OpenAI API Key를 발급받아 .env 파일에 저장합니다. 
- 의존성 설치: npm install
### 백엔드 실행
- 로컬 서버를 켭니다: node server.js
- 정상 기동 시 콘솔에 Server running at http://localhost:3000이 표시됩니다. (에러가 나면 API 키 설정을 다시 확인하세요.)
### 크롬 확장 프로그램 등록
- Chrome 주소창에 chrome://extensions/ 입력 → 오른쪽 상단 개발자 모드 활성화.
- 압축해제된 확장 프로그램을 로드 버튼 → 이 폴더(예: chrome-sidechat) 선택.
- 설치 직후 서비스 워커가 사이드 패널을 자동으로 열어줍니다. 새 탭에서도 패널이 유지됩니다.
- 패널이 보이지 않으면 브라우저 우측 상단 사이드 패널 아이콘(또는 Ctrl+Shift+S)으로 “MS Side Chat”을 선택하세요.
### 사용 흐름
- 패널에서 메시지를 입력하면 로컬 서버 /chat 엔드포인트를 통해 OpenAI 응답을 받아옵니다.
- 웹 가이드 모드 버튼으로 페이지 분석/하이라이트 기능을 토글할 수 있습니다. 하이라이트는 content.js가 DOM을 읽어 생성합니다.
- PDF 업로드, 히스토리, 테마 토글 등은 panel.js에서 처리합니다. 대화 내용은 chrome.storage.local에 저장됩니다.
### 문제 해결 팁
- 패널이 “Connecting…”에서 멈추면 node server.js가 실행 중인지, 포트 3000이 다른 프로그램과 충돌하지 않는지 확인하세요.
- 사이드 패널이 자동으로 열리지 않으면 chrome://extensions에서 확장을 다시 로드하거나 Chrome을 재시작합니다.
- API 요청이 401/403이면 .env의 API 키 권한과 철자를 다시 점검하세요.
- 서버 주소를 변경해야 한다면 panel.js 안의 safeFetch("http://localhost:3000/…") 부분을 원하는 도메인으로 수정합니다.
