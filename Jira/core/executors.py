#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jira Agent - Execution Functions

실제 Jira/Milvus 작업 실행 함수들
"""

from typing import Dict, Optional

from core.jira import jira_client
from core.milvus_client import milvus_client


# ─────────────────────────────────────────────────────────
# 실행 함수들
# ─────────────────────────────────────────────────────────

def build_milvus_filter(slots: Dict) -> Optional[str]:
    """슬롯에서 Milvus 필터 표현식 생성"""
    filters = []

    if slots.get("project_key"):
        filters.append(f"project_key == '{slots['project_key']}'")

    if slots.get("priority"):
        filters.append(f"priority == '{slots['priority']}'")

    if slots.get("issue_type"):
        filters.append(f"issue_type == '{slots['issue_type']}'")

    if slots.get("assignee"):
        filters.append(f"assignee == '{slots['assignee']}'")

    return " && ".join(filters) if filters else None


def execute_search(slots: Dict) -> Dict:
    """검색 실행"""
    keyword = slots.get("keyword", "")
    limit = slots.get("limit", 10)

    # 숫자 추출 (예: "3개" -> 3)
    if isinstance(limit, str):
        import re
        match = re.search(r'\d+', limit)
        limit = int(match.group()) if match else 10

    # Milvus 하이브리드 검색
    filter_expr = build_milvus_filter(slots)

    print(f"[SEARCH] keyword: '{keyword}', filter: {filter_expr}, limit: {limit}")

    results = milvus_client.search(
        query_text=keyword if keyword else "이슈",
        filter_expr=filter_expr,
        limit=max(limit, 50)
    )

    if results:
        display_results = results[:limit]
        response = f"🔍 {len(results)}개의 이슈를 찾았습니다 (상위 {len(display_results)}개 표시):\n\n"
        for i, result in enumerate(display_results, 1):
            response += f"[{i}] {result['key']}: {result['summary']}\n"
            response += f"    - 프로젝트: {result['project']}, 상태: {result['status']}\n"

            priority = result.get('priority', 'NaN')
            duedate = result.get('duedate', 'NaN')
            response += f"    - 우선순위: {priority}, 마감일: {duedate}\n"

            if result.get('assignee'):
                response += f"    - 담당자: {result['assignee']}\n"
    else:
        response = "검색 결과가 없습니다."

    return {
        "response": response,
        "message": response,
        "data": {"results": results}
    }


def execute_create(slots: Dict) -> Dict:
    """이슈 생성 실행"""
    result = jira_client.create_issue(
        project_key=slots.get("project_key"),
        summary=slots.get("summary"),
        description=slots.get("description"),
        issuetype=slots.get("issuetype", "작업"),
        assignee=slots.get("assignee"),
        priority=slots.get("priority"),
        duedate=slots.get("duedate")
    )

    if result.get("ok"):
        issue_key = result.get("key")
        response = f"✅ 이슈 생성 완료: {issue_key}"

        # Milvus 동기화
        jql = f"key = {issue_key}"
        issues = jira_client.search_issues(jql, max_results=1)
        if issues:
            milvus_client.upsert_issues(issues)
            response += "\n(Milvus 동기화 완료)"

        data = {"key": issue_key, "issue": issues[0] if issues else None}
    else:
        response = f"❌ 이슈 생성 실패: {result.get('detail')}"
        data = None

    return {
        "response": response,
        "message": response,
        "data": data
    }


def execute_update(slots: Dict) -> Dict:
    """이슈 수정 실행"""
    issue_key = slots.get("issue_key")

    fields = {}
    if slots.get("summary"):
        fields["summary"] = slots["summary"]
    if slots.get("description"):
        fields["description"] = slots["description"]

    result = jira_client.update_issue(issue_key, fields)

    if result.get("ok"):
        response = f"✅ {issue_key} 수정 완료"

        # Milvus 동기화
        jql = f"key = {issue_key}"
        issues = jira_client.search_issues(jql, max_results=1)
        if issues:
            milvus_client.upsert_issues(issues)
            response += "\n(Milvus 동기화 완료)"

        data = {"key": issue_key, "issue": issues[0] if issues else None}
    else:
        response = f"❌ 수정 실패: {result.get('detail')}"
        data = None

    return {
        "response": response,
        "message": response,
        "data": data
    }


def execute_delete(slots: Dict) -> Dict:
    """이슈 삭제 실행"""
    issue_key = slots.get("issue_key")

    result = jira_client.delete_issue(issue_key)

    if result.get("ok"):
        response = f"✅ {issue_key} 삭제 완료"
        data = {"key": issue_key}
    else:
        response = f"❌ 삭제 실패: {result.get('detail')}"
        data = None

    return {
        "response": response,
        "message": response,
        "data": data
    }
