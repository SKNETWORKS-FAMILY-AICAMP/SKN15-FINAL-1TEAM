#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jira Agent - Routing and Graph

라우팅 함수와 LangGraph 워크플로 구성
"""

from langgraph.graph import StateGraph, END

from core.agent_utils import AgentState
from core.nodes import (
    parse_intent_node,
    explain_method_node,
    check_slots_node,
    clarify_node,
    find_candidates_node,
    int_candidate_node,
    curd_check_node,
    approve_node,
    execute_node
)


# ─────────────────────────────────────────────────────────
# 라우팅 함수들
# ─────────────────────────────────────────────────────────

def route_after_parse(state: AgentState) -> str:
    """파싱 후 라우팅"""
    stage = state.get("stage")

    # 중단된 작업이 있으면 해당 노드로 복귀
    if stage in ["int_candidate", "approve", "clarify", "check_slots"]:
        print(f"[ROUTE] 중단된 작업 복귀: {stage}")
        return stage

    # 새 작업이면 intent 기반 라우팅
    intent = state.get("intent", "unknown")

    # unknown이나 explain 의도는 모두 explain_method로 (안내 메시지 표시)
    if intent in ["unknown", "explain"]:
        return "explain_method"
    elif intent in ["search", "create", "update", "delete"]:
        return "check_slots"
    else:
        # 기타 예외 상황도 explain_method로 (안내 메시지)
        return "explain_method"


def route_after_check(state: AgentState) -> str:
    """슬롯 체크 후 라우팅"""
    stage = state["stage"]

    if stage == "clarify":
        return "clarify"
    elif stage == "curd_check":
        return "curd_check"
    elif stage == "find_candidates":
        return "find_candidates"
    else:
        # 기타 상황은 execute로
        return "execute"


def route_after_curd_check(state: AgentState) -> str:
    """CURD 검증 후 라우팅"""
    stage = state["stage"]

    if stage == "clarify":
        # 검증 실패 시 다시 clarify로
        return "clarify"
    elif stage == "execute":
        # 검색(search)은 바로 실행
        return "execute"
    elif stage == "approve":
        # 생성/수정/삭제는 승인 절차
        return "approve"
    else:
        # 기본: execute
        return "execute"


def route_after_find_candidates(state: AgentState) -> str:
    """후보 찾기 후 라우팅"""
    stage = state["stage"]

    if stage == "clarify":
        # 후보 없음 -> issue_key 직접 입력
        return "clarify"
    elif stage == "curd_check":
        # 후보 1개 자동 선택 -> 검증
        return "curd_check"
    elif stage == "int_candidate":
        # 후보 여러 개 → 사용자 선택 대기 (END로 중단)
        return "done"
    else:
        # 기본: done
        return "done"


def route_after_int_candidate(state: AgentState) -> str:
    """후보 선택 중단 후 라우팅"""
    stage = state["stage"]

    if stage == "check_slots":
        # 선택 완료 -> check_slots로 재검증
        return "check_slots"
    elif stage == "int_candidate":
        # 잘못된 입력 -> 다시 입력 대기 (END로 중단)
        return "done"
    elif stage == "clarify":
        # 오류 발생 -> clarify
        return "clarify"
    else:
        # 기본: check_slots
        return "check_slots"


def route_after_clarify(state: AgentState) -> str:
    """Clarify 노드 후 라우팅"""
    missing_fields = state.get("missing_fields", [])

    if missing_fields:
        # 아직 누락된 필드가 있음 -> END (다시 입력 받기)
        return "done"
    else:
        # 모든 필드 완료 -> check_slots로 자동 진행
        return "check_slots"


def route_after_approve(state: AgentState) -> str:
    """Approve 노드 후 라우팅"""
    stage = state.get("stage")

    if stage == "execute":
        # yes 승인 -> execute로
        return "execute"
    elif stage == "approve":
        # 잘못된 입력 -> 다시 approve (END)
        return "done"
    elif stage == "done":
        # no 거부 -> END
        return "done"
    else:
        # 기본: done
        return "done"


# ─────────────────────────────────────────────────────────
# 그래프 구성
# ─────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """LangGraph 워크플로 구성"""

    # 워크플로 생성
    workflow = StateGraph(AgentState)

    # 노드 추가
    workflow.add_node("parse", parse_intent_node)
    workflow.add_node("explain_method", explain_method_node)
    workflow.add_node("check_slots", check_slots_node)
    workflow.add_node("clarify", clarify_node)
    workflow.add_node("find_candidates", find_candidates_node)
    workflow.add_node("int_candidate", int_candidate_node)
    workflow.add_node("curd_check", curd_check_node)
    workflow.add_node("approve", approve_node)
    workflow.add_node("execute", execute_node)

    # 시작 노드: parse
    workflow.set_entry_point("parse")

    # 조건부 엣지
    workflow.add_conditional_edges(
        "parse", route_after_parse,
        {
            "check_slots": "check_slots",
            "explain_method": "explain_method",
            "int_candidate": "int_candidate",
            "approve": "approve",
            "clarify": "clarify"
        }
    )

    workflow.add_conditional_edges(
        "check_slots",
        route_after_check,
        {
            "clarify": "clarify",
            "curd_check": "curd_check",
            "find_candidates": "find_candidates",
            "execute": "execute"
        }
    )

    workflow.add_conditional_edges(
        "find_candidates",
        route_after_find_candidates,
        {
            "clarify": "clarify",
            "curd_check": "curd_check",
            "int_candidate": "int_candidate",
            "done": END  # 후보 여러 개 → 사용자 선택 대기
        }
    )

    workflow.add_conditional_edges(
        "int_candidate",
        route_after_int_candidate,
        {
            "check_slots": "check_slots",
            "clarify": "clarify",
            "done": END
        }
    )

    workflow.add_conditional_edges(
        "curd_check",
        route_after_curd_check,
        {
            "clarify": "clarify",
            "approve": "approve",
            "execute": "execute"
        }
    )

    workflow.add_conditional_edges(
        "clarify",
        route_after_clarify,
        {
            "check_slots": "check_slots",
            "done": END
        }
    )

    workflow.add_conditional_edges(
        "approve",
        route_after_approve,
        {
            "execute": "execute",
            "done": END
        }
    )

    # 종료 엣지
    workflow.add_edge("execute", END)
    workflow.add_edge("explain_method", END)

    return workflow
