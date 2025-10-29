# -*- coding: utf-8 -*-
"""
jira_fetch_all.py - 전체 이슈 수집 (페이지네이션)

역할:
1. 프로젝트별 전체 이슈 수집
2. 신규 API (nextPageToken) + 구형 API (startAt) 지원
3. 레이트 리밋 대응 (429 에러 처리)
4. Milvus 동기화에 사용

버그 수정:
- maxResults=0 방지 (hard_limit 정확히 도달 시)
- rate_sleep 파라미터 일관성 유지

연결:
- config.py → JIRA_BASE_URL, AUTH, SESSION 사용
- milvus_client.py → sync_issues_to_milvus에서 호출
"""

import time
from typing import List, Dict, Any, Optional

from .config import JIRA_BASE_URL, HDR_JSON, AUTH, SESSION, TIMEOUT


def fetch_all_issues_by_project(
    project_key: str,
    fields: Optional[List[str]] = None,
    page_size: int = 100,
    hard_limit: Optional[int] = None,
    rate_sleep: float = 0.8
) -> List[Dict[str, Any]]:
    """
    프로젝트 내 전체 이슈 수집 (신규 API 기반)
    
    신규 API (/rest/api/3/search/jql):
    - nextPageToken 기반 페이지네이션
    - 더 안정적이고 빠름
    
    구형 API (/rest/api/3/search) 폴백:
    - startAt 기반 페이지네이션
    - 신규 API 실패 시 자동 전환
    
    Args:
        project_key: 프로젝트 키 (예: "KAN")
        fields: 수집할 필드 리스트 (None이면 기본 필드)
        page_size: 페이지당 크기 (1~100 권장)
        hard_limit: 최대 수집 개수 (None이면 전체)
        rate_sleep: 429 에러 시 대기 시간 (초)
    
    Returns:
        이슈 리스트 [{"key": "KAN-1", "fields": {...}}, ...]
    
    예시:
        # 전체 수집
        issues = fetch_all_issues_by_project("KAN")
        
        # 필드 지정 + 최대 500개
        issues = fetch_all_issues_by_project(
            "KAN",
            fields=["summary", "description", "status"],
            hard_limit=500
        )
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    jql = f"project = {project_key} ORDER BY updated DESC"
    
    base_body: Dict[str, Any] = {
        "jql": jql,
        "maxResults": page_size
    }
    
    if fields:
        base_body["fields"] = fields
    
    issues: List[Dict[str, Any]] = []
    next_token: Optional[str] = None
    
    print(f"[수집 시작] {project_key} 프로젝트")
    
    while True:
        # 하드 리밋 체크
        if hard_limit is not None and len(issues) >= hard_limit:
            print(f"[하드 리밋] {hard_limit}개 도달")
            break
        
        # 요청 바디 구성
        body = dict(base_body)
        if next_token:
            body["nextPageToken"] = next_token
        
        # 하드 리밋 고려한 페이지 크기 조정
        if hard_limit is not None:
            remain = max(0, hard_limit - len(issues))
            body["maxResults"] = max(1, min(page_size, remain)) if remain else 1
        
        # API 요청
        resp = SESSION.post(url, headers=HDR_JSON, json=body, auth=AUTH, timeout=TIMEOUT)
        
        # 레이트 리밋 처리
        if resp.status_code == 429:
            print(f"[레이트 리밋] {rate_sleep}초 대기...")
            time.sleep(rate_sleep)
            continue
        
        # 신규 API 실패 → 구형 API 폴백
        if resp.status_code >= 400:
            print(f"[신규 API 실패] {resp.status_code}, 구형 API 사용")
            return _fetch_all_legacy(project_key, fields, page_size, hard_limit, rate_sleep)
        
        resp.raise_for_status()
        data = resp.json()
        
        # 배치 수집
        batch = data.get("issues", [])
        issues.extend(batch)
        
        print(f"[진행] {len(issues)}개 수집됨...")
        
        # 다음 페이지 토큰 확인
        next_token = data.get("nextPageToken")
        if not next_token or not batch:
            break
    
    print(f"[완료] 총 {len(issues)}개 수집")
    return issues


def _fetch_all_legacy(
    project_key: str,
    fields: Optional[List[str]],
    page_size: int,
    hard_limit: Optional[int],
    rate_sleep: float = 0.8
) -> List[Dict[str, Any]]:
    """
    구형 API 폴백 (startAt 기반 페이지네이션)
    
    신규 API 실패 시 자동 호출됨
    
    Args:
        project_key: 프로젝트 키
        fields: 수집할 필드
        page_size: 페이지 크기
        hard_limit: 최대 개수
        rate_sleep: 레이트 리밋 시 대기 시간 (메인 함수와 동일)
    
    Returns:
        이슈 리스트
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/search"
    jql = f"project = {project_key} ORDER BY updated DESC"
    
    base_body = {
        "jql": jql,
        "maxResults": page_size,
        "startAt": 0
    }
    
    if fields:
        base_body["fields"] = fields
    
    issues: List[Dict[str, Any]] = []
    start_at = 0
    
    print(f"[구형 API] {project_key} 프로젝트")
    
    while True:
        # 하드 리밋 체크
        if hard_limit is not None and len(issues) >= hard_limit:
            break
        
        # 요청 바디
        body = dict(base_body)
        body["startAt"] = start_at
        
        # ✅ 버그 수정: maxResults가 0이 되지 않도록 보장
        if hard_limit is not None:
            remain = max(0, hard_limit - len(issues))
            if remain == 0:  # 정확히 도달하면 종료
                break
            body["maxResults"] = max(1, min(page_size, remain))
        
        # API 요청
        resp = SESSION.post(url, headers=HDR_JSON, json=body, auth=AUTH, timeout=TIMEOUT)
        
        # ✅ 레이트 리밋: 파라미터로 받은 값 사용
        if resp.status_code == 429:
            print(f"[레이트 리밋] {rate_sleep}초 대기...")
            time.sleep(rate_sleep)
            continue
        
        resp.raise_for_status()
        data = resp.json()
        
        # 배치 수집
        batch = data.get("issues", [])
        issues.extend(batch)
        
        print(f"[진행] {len(issues)}개 수집됨...")
        
        # 다음 페이지 체크
        total = data.get("total", 0)
        start_at += len(batch)
        
        if start_at >= total or not batch:
            break
    
    print(f"[완료] 총 {len(issues)}개 수집")
    return issues