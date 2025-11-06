# -*- coding: utf-8 -*-
"""
config.py - 환경설정

역할:
1. .env 파일 로드
2. Jira/OpenAI/Milvus 연결 정보 관리
3. 환경변수 검증

사용 예시:
    from src.config import JIRA_BASE_URL, MILVUS_HOST, assert_env
    
    assert_env()  # 필수값 검증
    print(MILVUS_HOST)  # 3.36.185.140
"""

import os
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import requests

# .env 로드 (Jira 디렉토리의 .env 파일)
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# ─────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────
def clean_base_url(raw: Optional[str]) -> str:
    """URL 정리"""
    s = (raw or "").strip()
    s = s.split()[0] if s else s
    return s.rstrip("/") if s else s

# ─────────────────────────────────────────────────────────
# Jira 설정
# ─────────────────────────────────────────────────────────
JIRA_BASE_URL: str = clean_base_url(os.getenv("JIRA_BASE_URL"))
JIRA_EMAIL: str = (os.getenv("JIRA_EMAIL") or "").strip()
JIRA_API_TOKEN: str = (os.getenv("JIRA_API_TOKEN") or "").strip()

# ─────────────────────────────────────────────────────────
# OpenAI 설정
# ─────────────────────────────────────────────────────────
OPENAI_API_KEY: str = (os.getenv("OPENAI_API_KEY") or "").strip()

# Embedding
EMBED_MODEL: str = (os.getenv("EMBED_MODEL") or "text-embedding-3-small").strip()
EMBED_DIM: int = int(os.getenv("EMBED_DIM", "1536"))
EMBED_BATCH_SIZE: int = int(os.getenv("EMBED_BATCH_SIZE", "50"))

# Chat
CHAT_MODEL: str = (os.getenv("CHAT_MODEL") or "gpt-4o-mini").strip()
CHAT_TEMPERATURE: float = float(os.getenv("CHAT_TEMPERATURE", "0.2"))
CHAT_MAX_TOKENS: int = int(os.getenv("CHAT_MAX_TOKENS", "2048"))

# ─────────────────────────────────────────────────────────
# Milvus 설정 (AWS EC2)
# ─────────────────────────────────────────────────────────
MILVUS_HOST: str = (os.getenv("MILVUS_HOST") or "3.36.185.140").strip()
MILVUS_PORT: int = int(os.getenv("MILVUS_PORT", "19530"))
MILVUS_COLLECTION: str = (os.getenv("MILVUS_COLLECTION") or "jira_issues").strip()

# ─────────────────────────────────────────────────────────
# 공용 객체
# ─────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.trust_env = False

AUTH = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)

HDR_JSON = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

TIMEOUT = 30

# ─────────────────────────────────────────────────────────
# 검증
# ─────────────────────────────────────────────────────────
def assert_env(strict: bool = False) -> None:
    """
    환경변수 검증
    
    Args:
        strict: True면 Jira API 실제 호출로 인증 확인
    """
    problems = []
    
    # Jira
    if not JIRA_BASE_URL:
        problems.append("JIRA_BASE_URL이 비어 있습니다.")
    if not JIRA_EMAIL:
        problems.append("JIRA_EMAIL이 비어 있습니다.")
    if not JIRA_API_TOKEN:
        problems.append("JIRA_API_TOKEN이 비어 있습니다.")
    
    # OpenAI (경고만)
    if not OPENAI_API_KEY:
        print("[경고] OPENAI_API_KEY가 없습니다. LLM 기능 비활성화됩니다.")
    
    # Milvus (경고만)
    if MILVUS_HOST == "3.36.185.140":
        print(f"[정보] Milvus 연결: {MILVUS_HOST}:{MILVUS_PORT}")
    
    if problems:
        raise SystemExit("환경변수 오류:\n- " + "\n- ".join(problems))
    
    # strict 모드: Jira 인증 확인
    if strict:
        url = f"{JIRA_BASE_URL}/rest/api/3/myself"
        try:
            r = SESSION.get(url, headers=HDR_JSON, auth=AUTH, timeout=30)
        except Exception as e:
            raise SystemExit(f"[Jira 연결 실패] {e}")
        
        if r.status_code != 200:
            raise SystemExit(
                f"[Jira 인증 실패] {r.status_code}\n"
                "→ .env 파일의 JIRA_EMAIL/JIRA_API_TOKEN을 확인하세요."
            )
        
        user = r.json()
        print(f"[Jira 인증 성공] {user.get('displayName')} ({user.get('emailAddress')})")

# ─────────────────────────────────────────────────────────
# 디버그
# ─────────────────────────────────────────────────────────
def debug_print() -> None:
    """현재 설정 출력"""
    print("=== 환경 설정 ===")
    print(f"JIRA_BASE_URL: {JIRA_BASE_URL}")
    print(f"JIRA_EMAIL: {JIRA_EMAIL}")
    print(f"OPENAI_API_KEY: {'설정됨' if OPENAI_API_KEY else '없음'}")
    print(f"EMBED_MODEL: {EMBED_MODEL} (dim={EMBED_DIM})")
    print(f"CHAT_MODEL: {CHAT_MODEL}")
    print(f"MILVUS: {MILVUS_HOST}:{MILVUS_PORT} / {MILVUS_COLLECTION}")


# 모듈 로드 시 기본 검증
if __name__ != "__main__":
    try:
        assert_env(strict=False)
    except SystemExit as e:
        print(f"[설정 오류] {e}")