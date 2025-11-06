#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jira Agent - Utilities

공통 유틸리티, 타입 정의, OpenAI 클라이언트 등
"""

from typing import TypedDict, Literal, Optional, List, Dict, Any
from openai import OpenAI

from core.config import OPENAI_API_KEY, CHAT_MODEL
from core.jira import jira_client


# ─────────────────────────────────────────────────────────
# 타입 정의
# ─────────────────────────────────────────────────────────

Intent = Literal["search", "create", "update", "delete", "explain", "unknown"]
Stage = Literal["idle", "parse", "clarify", "curd_check", "find_candidates", "approve", "execute", "done"]

class AgentState(TypedDict):
    """에이전트 상태"""
    # 입력
    user_input: str
    session_id: str

    # 대화 이력
    history: List[Dict[str, str]]

    # 파싱 결과
    intent: Intent
    slots: Dict[str, Any]  # 동적으로 모든 필드 포함 가능 (project_key, summary, issue_key, keyword, etc.)
    confidence: float

    # 상태 관리
    stage: Stage
    missing_fields: List[str]

    # 후보 이슈 (수정/삭제 시 여러 후보가 있을 경우)
    candidate_issues: Optional[List[Dict[str, Any]]]

    # 최종 응답
    response: str
    message: str
    data: Optional[Dict[str, Any]]


# ─────────────────────────────────────────────────────────
# OpenAI 클라이언트
# ─────────────────────────────────────────────────────────

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# 프로젝트 메타데이터 캐시 (한 번만 조회)
_project_metadata_cache = None

def get_project_metadata():
    """
    프로젝트 목록과 이슈 타입을 캐싱하여 반환

    첫 호출 시에만 Jira API를 호출하고, 이후에는 캐시된 데이터 사용
    """
    global _project_metadata_cache

    if _project_metadata_cache is None:
        try:
            projects = jira_client.get_projects()
            project_keys = [p['key'] for p in projects]
            project_issue_types = jira_client.get_issue_types()

            _project_metadata_cache = (project_keys, project_issue_types)
            print(f"[CACHE] 프로젝트 메타데이터 캐시 생성: {len(project_keys)}개 프로젝트")
        except Exception as e:
            print(f"[ERROR] 프로젝트 메타데이터 조회 실패: {e}")
            return ([], {})

    return _project_metadata_cache
