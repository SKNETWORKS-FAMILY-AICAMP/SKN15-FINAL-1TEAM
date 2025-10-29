# -*- coding: utf-8 -*-
"""
jira_client.py - Jira REST API 클라이언트

역할:
1. 프로젝트/이슈 타입 조회
2. 이슈 생성/수정/삭제
3. JQL 검색
4. 에러 처리 및 레이트 리밋 대응

연결:
- config.py → JIRA_BASE_URL, AUTH, SESSION 사용
- graph.py → 모든 Jira 작업에서 호출
"""

from typing import List, Dict, Any, Optional
import requests

from .config import JIRA_BASE_URL, HDR_JSON, AUTH, SESSION, TIMEOUT


# ─────────────────────────────────────────────────────────
# 프로젝트 및 이슈 타입 조회
# ─────────────────────────────────────────────────────────
def list_projects(limit: int = 50) -> List[Dict[str, Any]]:
    """
    접근 가능한 프로젝트 목록 조회
    
    Args:
        limit: 최대 반환 개수
    
    Returns:
        [{"key": "KAN", "name": "칸반 프로젝트", ...}, ...]
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/project/search"
    params = {"maxResults": limit}
    
    try:
        resp = SESSION.get(url, headers=HDR_JSON, params=params, auth=AUTH, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("values", [])
    except Exception as e:
        print(f"[경고] 프로젝트 목록 조회 실패: {e}")
        return []


def list_issue_types(limit: int = 50) -> List[Dict[str, Any]]:
    """
    사용 가능한 이슈 타입 목록 조회
    
    Returns:
        [{"name": "Task", "subtask": False, ...}, ...]
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issuetype"
    
    try:
        resp = SESSION.get(url, headers=HDR_JSON, auth=AUTH, timeout=TIMEOUT)
        resp.raise_for_status()
        types = resp.json()
        return types[:limit]
    except Exception as e:
        print(f"[경고] 이슈 타입 조회 실패: {e}")
        return []


# ─────────────────────────────────────────────────────────
# JQL 검색
# ─────────────────────────────────────────────────────────
def jql_search(
    jql: str,
    fields: Optional[List[str]] = None,
    max_results: int = 50
) -> List[Dict[str, Any]]:
    """
    JQL 검색
    
    Args:
        jql: JQL 쿼리 (예: "project = KAN AND status = Open")
        fields: 반환할 필드 리스트
        max_results: 최대 결과 수
    
    Returns:
        이슈 리스트
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/search"
    
    body = {
        "jql": jql,
        "maxResults": max_results
    }
    
    if fields:
        body["fields"] = fields
    
    try:
        resp = SESSION.post(url, headers=HDR_JSON, json=body, auth=AUTH, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("issues", [])
    except Exception as e:
        print(f"[경고] JQL 검색 실패: {e}")
        return []


# ─────────────────────────────────────────────────────────
# 이슈 생성
# ─────────────────────────────────────────────────────────
def create_issue(
    project_key: str,
    summary: str,
    issue_type: str = "Task",
    description: Optional[str] = None,
    priority_name: Optional[str] = None,
    labels: Optional[List[str]] = None,
    parent_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    이슈 생성
    
    Args:
        project_key: 프로젝트 키 (예: "KAN")
        summary: 이슈 제목
        issue_type: 이슈 타입 (예: "Task", "Bug", "Subtask")
        description: 이슈 설명
        priority_name: 우선순위 (Highest/High/Medium/Low)
        labels: 라벨 리스트
        parent_key: 부모 이슈 키 (Subtask용)
    
    Returns:
        {"ok": True, "key": "KAN-123", ...}
    
    Raises:
        requests.HTTPError: API 호출 실패
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"
    
    # 기본 필드
    fields: Dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type}
    }
    
    # 선택 필드
    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}]
                }
            ]
        }
    
    if priority_name:
        fields["priority"] = {"name": priority_name}
    
    if labels:
        fields["labels"] = labels
    
    # Subtask: 부모 지정
    if parent_key:
        fields["parent"] = {"key": parent_key}
    
    body = {"fields": fields}
    
    try:
        resp = SESSION.post(url, headers=HDR_JSON, json=body, auth=AUTH, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        
        return {
            "ok": True,
            "key": data.get("key"),
            "id": data.get("id"),
            "self": data.get("self")
        }
    
    except requests.HTTPError as e:
        error_msg = e.response.text if e.response else str(e)
        return {
            "ok": False,
            "error": f"HTTP {e.response.status_code}" if e.response else "Unknown",
            "detail": error_msg
        }


# ─────────────────────────────────────────────────────────
# 이슈 수정
# ─────────────────────────────────────────────────────────
def update_issue(
    issue_key: str,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    issue_type: Optional[str] = None,
    priority_name: Optional[str] = None,
    replace_labels: Optional[List[str]] = None,
    assignee: Optional[str] = None
) -> Dict[str, Any]:
    """
    이슈 수정

    Args:
        issue_key: 이슈 키 (예: "KAN-123")
        summary: 새 제목
        description: 새 설명
        issue_type: 새 이슈 타입
        priority_name: 새 우선순위
        replace_labels: 라벨 전체 교체
        assignee: 담당자 (이메일 또는 이름, "me"는 현재 사용자, None은 할당 해제)

    Returns:
        {"ok": True, "status": 204}

    Raises:
        requests.HTTPError: API 호출 실패
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"

    fields: Dict[str, Any] = {}

    if summary:
        fields["summary"] = summary

    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}]
                }
            ]
        }

    if issue_type:
        fields["issuetype"] = {"name": issue_type}

    if priority_name:
        fields["priority"] = {"name": priority_name}

    if replace_labels is not None:
        fields["labels"] = replace_labels

    # 담당자 처리
    if assignee is not None:
        if assignee == "" or assignee.lower() == "unassigned":
            # 할당 해제
            fields["assignee"] = None
            print("[담당자] 할당 해제")
        elif assignee == "me":
            # 현재 사용자
            from .config import JIRA_EMAIL
            user = find_jira_user(JIRA_EMAIL)
            if user:
                fields["assignee"] = {"accountId": user["accountId"]}
                print(f"[담당자] 나에게 할당 → {user['displayName']}")
        else:
            # 사용자 검색
            user = find_jira_user(assignee)
            if user:
                fields["assignee"] = {"accountId": user["accountId"]}
                print(f"[담당자] {assignee} → {user['displayName']} ({user['accountId'][:8]}...)")
            else:
                print(f"[경고] 사용자 '{assignee}'를 찾을 수 없습니다.")

    if not fields:
        return {"ok": True, "status": 204, "noop": True}

    body = {"fields": fields}

    try:
        resp = SESSION.put(url, headers=HDR_JSON, json=body, auth=AUTH, timeout=TIMEOUT)
        resp.raise_for_status()

        return {
            "ok": True,
            "status": resp.status_code,
            "key": issue_key
        }

    except requests.HTTPError as e:
        # 403 에러 특별 처리
        if e.response and e.response.status_code == 403:
            return {
                "ok": False,
                "error": "Permission Denied",
                "detail": "이슈 수정 권한이 없습니다. 프로젝트 관리자에게 문의하세요."
            }

        error_msg = e.response.text if e.response else str(e)
        return {
            "ok": False,
            "error": f"HTTP {e.response.status_code}" if e.response else "Unknown",
            "detail": error_msg
        }


# ─────────────────────────────────────────────────────────
# 이슈 삭제
# ─────────────────────────────────────────────────────────
def delete_issue(
    issue_key: str,
    delete_subtasks: bool = False
) -> Dict[str, Any]:
    """
    이슈 삭제

    Args:
        issue_key: 이슈 키 (예: "KAN-123")
        delete_subtasks: 하위 작업도 함께 삭제 여부

    Returns:
        {"ok": True, "status": 204}

    Raises:
        requests.HTTPError: API 호출 실패
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    params = {"deleteSubtasks": "true" if delete_subtasks else "false"}

    try:
        resp = SESSION.delete(url, headers=HDR_JSON, params=params, auth=AUTH, timeout=TIMEOUT)
        resp.raise_for_status()

        return {
            "ok": True,
            "status": resp.status_code,
            "key": issue_key
        }

    except requests.HTTPError as e:
        # 403 에러 특별 처리
        if e.response and e.response.status_code == 403:
            return {
                "ok": False,
                "error": "Permission Denied",
                "detail": (
                    "❌ 이슈 삭제 권한이 없습니다.\n\n"
                    "해결 방법:\n"
                    "1. Jira 웹 → 프로젝트 설정\n"
                    "2. 권한 (Permissions) → 역할 (Roles)\n"
                    "3. 자신의 역할에 '이슈 삭제' 권한 추가\n\n"
                    "또는 프로젝트 관리자에게 권한을 요청하세요."
                )
            }

        # 404 에러 (이슈 없음)
        if e.response and e.response.status_code == 404:
            return {
                "ok": False,
                "error": "Not Found",
                "detail": f"❌ 이슈 '{issue_key}'를 찾을 수 없습니다.\n이슈 키를 확인해주세요."
            }

        error_msg = e.response.text if e.response else str(e)
        return {
            "ok": False,
            "error": f"HTTP {e.response.status_code}" if e.response else "Unknown",
            "detail": error_msg
        }


# ─────────────────────────────────────────────────────────
# 헬퍼: 이슈 타입 ID 조회
# ─────────────────────────────────────────────────────────
def get_issue_type_id(type_name: str) -> Optional[str]:
    """
    이슈 타입 이름 → ID 변환

    Args:
        type_name: 이슈 타입 이름 (예: "Task")

    Returns:
        타입 ID 또는 None
    """
    types = list_issue_types(100)

    for t in types:
        if t.get("name", "").lower() == type_name.lower():
            return t.get("id")

    return None


# ─────────────────────────────────────────────────────────
# 헬퍼: 사용자 검색 및 라벨 조회
# ─────────────────────────────────────────────────────────
def find_jira_user(name_or_email: str) -> Optional[Dict[str, Any]]:
    """
    Jira 사용자 검색

    Args:
        name_or_email: 사용자 이름 또는 이메일

    Returns:
        {"accountId": "...", "displayName": "...", "emailAddress": "..."}
        또는 None
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/user/search"
    params = {"query": name_or_email}

    try:
        resp = SESSION.get(url, headers=HDR_JSON, params=params, auth=AUTH, timeout=10)
        resp.raise_for_status()
        users = resp.json()

        if users:
            # 첫 번째 매칭 사용자 반환
            return users[0]
        else:
            return None

    except Exception as e:
        print(f"[오류] 사용자 검색 실패: {e}")
        return None


def get_issue_labels(issue_key: str) -> List[str]:
    """
    이슈의 기존 라벨 조회

    Args:
        issue_key: 이슈 키 (예: "KAN-123")

    Returns:
        라벨 리스트 (예: ["bug", "api"])
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    params = {"fields": "labels"}

    try:
        resp = SESSION.get(url, headers=HDR_JSON, params=params, auth=AUTH, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("fields", {}).get("labels", [])

    except Exception as e:
        print(f"[오류] 라벨 조회 실패 ({issue_key}): {e}")
        return []