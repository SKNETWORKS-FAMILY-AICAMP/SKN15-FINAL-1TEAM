# Jira Agent

LangGraph 기반 Jira 이슈 관리 에이전트

## 주요 기능

- 🔍 **자연어 검색**: Milvus 벡터 DB를 통한 의미 기반 이슈 검색
- ✏️ **CRUD 작업**: 이슈 생성, 수정, 삭제
- 🤖 **대화형 인터페이스**: 부족한 정보 자동 요청 및 수집
- 🔗 **실시간 동기화**: Jira 웹훅을 통한 Milvus 자동 동기화
- 🌐 **REST API**: FastAPI 기반 HTTP 엔드포인트

## 기술 스택

- **LangGraph**: 상태 머신 기반 워크플로
- **FastAPI**: REST API 서버
- **Milvus**: 벡터 데이터베이스
- **OpenAI**: LLM 및 임베딩
- **Jira API**: Atlassian Jira 연동

## 빠른 시작

### 1. 환경 변수 설정

```bash
cp .env.example .env
# .env 파일을 열어서 실제 값으로 수정
```

### 2. Docker Compose로 실행

```bash
# Milvus + Jira Agent 전체 실행
docker-compose up -d

# 로그 확인
docker-compose logs -f jira-agent
```

### 3. 초기 데이터 동기화

```bash
# Jira 이슈를 Milvus에 동기화
docker-compose exec jira-agent python sync_jira_to_milvus.py
```

### 4. API 사용

서버가 실행되면 다음 URL에서 접근 가능:
- API 서버: http://localhost:8000
- API 문서: http://localhost:8000/docs
- Milvus: localhost:19530

## API 엔드포인트

### POST `/chat`
채팅 인터페이스로 Jira Agent와 대화

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "KAN 프로젝트 이슈 검색해줘",
    "session_id": "user123"
  }'
```

### POST `/webhook/jira`
Jira 웹훅 엔드포인트 (자동 동기화)

Jira 설정에서 웹훅 URL 등록:
```
http://your-server:8000/webhook/jira
```

### GET `/health`
헬스 체크

```bash
curl http://localhost:8000/health
```

## 로컬 개발

Docker 없이 로컬에서 실행:

```bash
# 1. 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. 패키지 설치
pip install -r requirements.txt

# 3. Milvus 실행 (Docker)
docker-compose up -d milvus

# 4. 서버 실행
python api/server.py
```

## 프로젝트 구조

```
Jira/
├── api/
│   └── server.py           # FastAPI 서버
├── core/
│   ├── agent_v2.py         # Jira Agent 메인
│   ├── routing.py          # LangGraph 라우팅
│   ├── nodes.py            # 노드 함수들
│   ├── agent_utils.py      # 타입 정의 & 유틸
│   ├── config.py           # 설정
│   ├── jira.py             # Jira 클라이언트
│   ├── milvus_client.py    # Milvus 클라이언트
│   └── utils.py            # 공통 유틸
├── sync_jira_to_milvus.py  # 초기 동기화 스크립트
├── Dockerfile              # Docker 이미지
├── docker-compose.yml      # 전체 스택 실행
├── requirements.txt        # Python 패키지
└── .env.example            # 환경 변수 예시
```

## LangGraph 워크플로

```
parse → intent/stage 분석
  ↓
  ├─ unknown/explain → explain_method → END
  │
  └─ CRUD → check_slots → 슬롯 검증
              ↓
              ├─ 부족 → clarify → 정보 수집 → END or check_slots
              │
              ├─ 모호 → find_candidates → 후보 검색
              │            ↓
              │            ├─ 0개 → clarify
              │            ├─ 1개 → curd_check (자동)
              │            └─ 여러개 → int_candidate → END
              │
              └─ 완료 → curd_check → 존재 확인
                          ↓
                          ├─ search → execute → END
                          └─ create/update/delete → approve
                                                      ↓
                                                      ├─ yes → execute → END
                                                      └─ no → END
```

## 사용 예시

### 검색
```
User: KAN 프로젝트의 이슈를 검색해줘
Agent: 🔍 5개의 이슈를 찾았습니다...
```

### 생성 (정보 부족)
```
User: 이슈 만들어줘
Agent: 프로젝트 키, 이슈 제목, 이슈 유형이 필요합니다.
User: KAN 프로젝트에 API 테스트 작업 만들어줘
Agent: 이슈 생성을 승인해주세요: ...
User: yes
Agent: ✅ 이슈 생성 완료: KAN-14
```

### 수정
```
User: KAN-5 상태를 진행 중으로 변경해줘
Agent: 수정을 승인해주세요: ...
User: yes
Agent: ✅ 이슈 수정 완료
```

## 웹훅 설정

Jira에서 웹훅 설정:

1. Jira → 설정 → 시스템 → 웹훅
2. 웹훅 URL: `http://your-server:8000/webhook/jira`
3. 이벤트 선택:
   - Issue Created
   - Issue Updated
   - Issue Deleted

## 라이센스

MIT License
