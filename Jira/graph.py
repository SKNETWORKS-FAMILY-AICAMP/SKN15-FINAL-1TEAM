# -*- coding: utf-8 -*-
"""
graph.py - LangGraph 워크플로우

설계:
Entry → IntentParsing(NLU) → Route → DBQuery/Clarify/NeedApprove/Execute

노드:
- Entry: 초기화 (Milvus 자동 동기화)
- IntentParsing: NLU로 의도/슬롯 추출
- Route: 룰베이스 라우팅 (필수값 체크)
- DBQuery: Milvus 검색 (검색/후보 제시)
- Clarify: 필수 슬롯 누락 시
- NeedApprove: 승인 카드 제시
- Execute: Jira API 실행

연결:
- nlu.py → extract_intent_slots, validate_slots
- milvus_client.py → MilvusStore, sync_issues_to_milvus
- jira_client.py → create_issue, update_issue, delete_issue
- utils.py → norm_issue_type, suggest_labels
"""

from typing import TypedDict, Literal, Dict, Any, Optional
from langgraph.graph import StateGraph, END

from .nlu import extract_intent_slots, validate_slots, get_available_types
from .milvus_client import MilvusStore, sync_issues_to_milvus
from .jira_client import (
    list_projects, list_issue_types,
    create_issue, update_issue, delete_issue
)
from .utils import norm_issue_type, suggest_labels


# ─────────────────────────────────────────────────────────
# State 정의
# ─────────────────────────────────────────────────────────
class State(TypedDict, total=False):
    # 입력
    utter: str
    approve: bool
    
    # 중간 상태
    intent: str
    slots: Dict[str, Any]
    candidates: list  # 검색 결과 (후보)
    
    # 출력
    stage: Literal["entry", "intent_parsing", "route", "db_query", "clarify", "need_approve", "execute", "done"]
    agent_output: Dict[str, Any]


# 전역 Milvus 스토어
_store: Optional[MilvusStore] = None


def get_store() -> MilvusStore:
    """Milvus 스토어 싱글톤"""
    global _store
    if _store is None:
        _store = MilvusStore()
    return _store


# ─────────────────────────────────────────────────────────
# 노드: Entry
# ─────────────────────────────────────────────────────────
def node_entry(state: State) -> State:
    """
    진입점: Milvus 자동 동기화 체크

    최초 실행 시 자동으로 프로젝트 동기화
    """
    try:
        store = get_store()

        # 최초 실행 시 자동 동기화
        chunk_count = store.count()

        if chunk_count == 0:
            print("\n" + "="*60)
            print("🔄 자동 동기화 시작")
            print("="*60)
            print("첫 실행 감지 → Milvus 초기화 중...")

            # 접근 가능한 프로젝트 자동 수집
            print("\n[1/3] 프로젝트 목록 수집 중...")
            projects = list_projects(limit=10)
            project_keys = [p.get("key") for p in projects]

            if not project_keys:
                print("❌ 접근 가능한 프로젝트가 없습니다.")
                print("\n해결 방법:")
                print("1. Jira 계정에 프로젝트 접근 권한이 있는지 확인")
                print("2. .env 파일의 JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN 확인")
                print("3. 수동 동기화: python -m src.cli sync --projects KAN TEST")
                print("="*60 + "\n")
            else:
                print(f"✅ {len(project_keys)}개 프로젝트 발견: {', '.join(project_keys)}")

                print(f"\n[2/3] 이슈 수집 및 임베딩 중... (시간이 걸릴 수 있습니다)")
                count = sync_issues_to_milvus(project_keys, store, full_sync=True)

                print(f"\n[3/3] 동기화 완료 확인...")
                final_count = store.count()

                print("\n" + "="*60)
                if final_count > 0:
                    print(f"✅ 동기화 성공!")
                    print(f"📊 {count}개 이슈 → {final_count}개 청크 저장됨")
                else:
                    print(f"⚠️  동기화 완료했지만 데이터가 없습니다.")
                    print(f"\n가능한 원인:")
                    print(f"1. OpenAI API 키 없음 → 임베딩 실패")
                    print(f"2. 프로젝트에 이슈가 없음")
                    print(f"3. Jira API 응답 오류")
                    print(f"\n진단 실행: python diagnose_milvus.py")
                print("="*60 + "\n")

        else:
            print(f"✅ Milvus 준비됨 ({chunk_count}개 청크)")

    except Exception as e:
        print("\n" + "="*60)
        print(f"❌ Milvus 초기화 실패: {e}")
        print("="*60)
        print("\n진단 방법:")
        print("1. python diagnose_milvus.py")
        print("2. 문제 해결 가이드: cat MILVUS_TROUBLESHOOTING.md")
        print("3. 수동 동기화: python -m src.cli sync")
        print("="*60 + "\n")

        import traceback
        traceback.print_exc()

    state["stage"] = "intent_parsing"
    return state


# ─────────────────────────────────────────────────────────
# 노드: Intent Parsing
# ─────────────────────────────────────────────────────────
def node_intent_parsing(state: State) -> State:
    """
    의도 파싱: NLU로 의도/슬롯 추출
    """
    parsed = extract_intent_slots(state["utter"], use_llm=True)
    
    state["intent"] = parsed.get("intent", "search")
    state["slots"] = parsed.get("slots", {})
    
    # ✅ 타입 정규화 (NLU에서 하지 않음, 여기서 처리)
    if state["slots"].get("issue_type"):
        available_types = get_available_types()
        normalized = norm_issue_type(
            state["slots"]["issue_type"],
            available_types=available_types,
            use_llm=False  # 퍼지 매칭만 (LLM 이미 1번 호출됨)
        )
        if normalized:
            state["slots"]["issue_type"] = normalized
    
    state["stage"] = "route"
    return state


# ─────────────────────────────────────────────────────────
# 노드: Route
# ─────────────────────────────────────────────────────────
def node_route(state: State) -> State:
    """
    룰베이스 라우팅: 필수값 체크 → 분기 결정
    """
    intent = state["intent"]
    slots = state["slots"]
    approve = state.get("approve", False)
    
    # 필수 슬롯 검증
    missing = validate_slots(intent, slots)
    
    if missing:
        # Clarify 필요
        state["stage"] = "clarify"
        state["agent_output"] = {
            "stage": "clarify",
            "need": missing,
            "hint_projects": [{"key": p.get("key"), "name": p.get("name")} 
                             for p in list_projects(5)],
            "hint_types": [t.get("name") for t in list_issue_types(10)]
        }
        return state
    
    # 검색 의도 → DB 쿼리
    if intent == "search":
        state["stage"] = "db_query"
        return state
    
    # 생성/수정/삭제 → 승인 필요
    if intent in ("create", "update", "delete") and not approve:
        state["stage"] = "need_approve"
        return state
    
    # 승인됨 → 실행
    state["stage"] = "execute"
    return state


# ─────────────────────────────────────────────────────────
# 노드: DB Query
# ─────────────────────────────────────────────────────────
def node_db_query(state: State) -> State:
    """
    Milvus 검색
    """
    store = get_store()
    intent = state["intent"]
    slots = state["slots"]
    
    # 검색 쿼리 생성
    query = (slots.get("summary") or slots.get("description") or state["utter"]).strip()
    project = slots.get("project_key")
    status = slots.get("status")
    topk = int(slots.get("count", 5))
    
    # 검색 실행
    results = store.search_smart(
        query=state["utter"],
        topk=state["slots"].get("count", 5)
    )
    
    state["candidates"] = results
    
    # 검색 의도면 완료
    if intent == "search":
        state["stage"] = "done"
        state["agent_output"] = {
            "stage": "done",
            "intent": "search",
            "results": results
        }
        return state
    
    # 기타 (미래 확장용)
    state["stage"] = "done"
    return state


# ─────────────────────────────────────────────────────────
# 노드: Clarify
# ─────────────────────────────────────────────────────────
def node_clarify(state: State) -> State:
    """
    Clarify 종단 노드 (CLI가 재호출)
    """
    return state


# ─────────────────────────────────────────────────────────
# 노드: Need Approve
# ─────────────────────────────────────────────────────────
def node_need_approve(state: State) -> State:
    """
    승인 필요 단계 (생성/수정/삭제)

    - 후보가 있으면 표시
    - 없으면 자동 검색 시도
    - 승인 카드 생성
    """
    intent = state["intent"]
    slots = state["slots"]
    candidates = state.get("candidates", [])

    # ============================================================
    # 삭제/수정 시 자동 후보 검색 (핵심!)
    # ============================================================
    if intent in ["delete", "update"]:
        issue_key = slots.get("issue_key")

        # 이슈 키가 없거나 후보가 없으면 자동 검색
        if (not issue_key or not is_valid_issue_key(issue_key)) and not candidates:
            # 검색 쿼리 생성
            query_parts = []

            if slots.get("project_key"):
                query_parts.append(slots["project_key"])

            if slots.get("summary"):
                query_parts.append(slots["summary"])

            if slots.get("issue_type"):
                query_parts.append(slots["issue_type"])

            # 쿼리 없으면 원래 발화 사용
            query = " ".join(query_parts) if query_parts else state.get("utter", "")

            # 검색 실행
            if query:
                print(f"[자동 검색] '{query}'로 후보 검색 중...")
                store = get_store()
                candidates = store.search(query, topk=5)
                state["candidates"] = candidates

                if candidates:
                    print(f"[후보 발견] {len(candidates)}개")
                else:
                    print(f"[후보 없음] 검색 결과 없음")

    # ============================================================
    # 승인 카드 생성
    # ============================================================
    card = {
        "title": f"[승인 필요] {intent.upper()} 요청",
        "action": intent,
        "danger_level": "high" if intent == "delete" else ("medium" if intent == "update" else "low"),
        "target": {
            "project_key": slots.get("project_key"),
            "issue_key": slots.get("issue_key")
        }
    }

    # ============================================================
    # intent별 추가 정보
    # ============================================================
    if intent == "create":
        card["summary"] = slots.get("summary")
        card["description"] = slots.get("description")

        # 자동 라벨 제안
        auto_labels = suggest_labels(
            slots.get("summary", ""),
            slots.get("description", "")
        )
        card["auto_labels"] = auto_labels

        card["changes"] = {
            "issue_type": slots.get("issue_type"),
            "priority": slots.get("priority"),
            "assignee": slots.get("assignee"),
            "labels": slots.get("labels", [])
        }

    elif intent == "update":
        card["summary"] = slots.get("summary")
        card["description"] = slots.get("description")

        card["changes"] = {
            "summary": slots.get("summary"),
            "description": bool(slots.get("description")),
            "issue_type": slots.get("issue_type"),
            "priority": slots.get("priority"),
            "assignee": slots.get("assignee"),      # 추가
            "labels": slots.get("labels")            # 추가
        }

    elif intent == "delete":
        card["summary"] = slots.get("summary")
        card["description"] = slots.get("description")

    # ============================================================
    # 후보 표시 (핵심!)
    # ============================================================
    if candidates:
        card["candidates"] = candidates
        # 후보는 display_approval_card에서 표시됨

    state["stage"] = "need_approve"
    state["agent_output"] = {
        "stage": "need_approve",
        "card": card
    }

    return state


def is_valid_issue_key(key: str) -> bool:
    """
    유효한 이슈 키인지 확인

    Args:
        key: 이슈 키 (예: KAN-123)

    Returns:
        True if valid
    """
    if not key:
        return False

    import re
    # 형식: 대문자-숫자 (예: KAN-123, HIN-45)
    pattern = r'^[A-Z]+-\d+$'
    return bool(re.match(pattern, key))


# ─────────────────────────────────────────────────────────
# 노드: Execute
# ─────────────────────────────────────────────────────────
def node_execute(state: State) -> State:
    """
    Jira API 실행
    """
    intent = state["intent"]
    slots = state["slots"]
    
    try:
        if intent == "create":
            result = _execute_create(slots)
        elif intent == "update":
            result = _execute_update(slots)
        elif intent == "delete":
            result = _execute_delete(slots)
        else:
            result = {"error": "알 수 없는 의도"}
        
        state["agent_output"] = {
            "stage": "done",
            "intent": intent,
            "result": result
        }
    
    except Exception as e:
        state["agent_output"] = {
            "stage": "done",
            "intent": intent,
            "error": str(e)
        }
    
    state["stage"] = "done"
    return state


def _execute_create(slots: Dict[str, Any]) -> Dict[str, Any]:
    """이슈 생성 실행"""
    auto_labels = suggest_labels(
        slots.get("summary", ""),
        slots.get("description", "")
    )
    
    return create_issue(
        project_key=slots["project_key"],
        summary=slots["summary"],
        issue_type=slots.get("issue_type", "Task"),
        description=slots.get("description"),
        priority_name=slots.get("priority"),
        labels=sorted(list(set((slots.get("labels") or []) + auto_labels))),
        parent_key=slots.get("parent_key")
    )


def _execute_update(slots: Dict[str, Any]) -> Dict[str, Any]:
    """이슈 수정 실행"""
    changes = {}

    if slots.get("summary"):
        changes["summary"] = slots["summary"]
    if slots.get("description"):
        changes["description"] = slots["description"]
    if slots.get("issue_type"):
        changes["issue_type"] = slots["issue_type"]
    if slots.get("priority"):
        changes["priority_name"] = slots["priority"]
    if slots.get("labels"):
        changes["replace_labels"] = slots["labels"]
    if "assignee" in slots:  # None도 처리하기 위해 in 사용
        changes["assignee"] = slots["assignee"]

    if not changes:
        return {"ok": True, "status": 204, "noop": True}

    return update_issue(slots["issue_key"], **changes)


def _execute_delete(slots: Dict[str, Any]) -> Dict[str, Any]:
    """이슈 삭제 실행"""
    return delete_issue(slots["issue_key"], delete_subtasks=False)


# ─────────────────────────────────────────────────────────
# 노드: Done
# ─────────────────────────────────────────────────────────
def node_done(state: State) -> State:
    """완료 종단 노드"""
    return state


# ─────────────────────────────────────────────────────────
# 라우터
# ─────────────────────────────────────────────────────────
def route_after_route(state: State) -> str:
    """Route 노드 이후 분기"""
    return state["stage"]


# ─────────────────────────────────────────────────────────
# 그래프 빌드
# ─────────────────────────────────────────────────────────
def build_graph():
    """LangGraph 그래프 빌드"""
    g = StateGraph(State)
    
    # 노드 추가
    g.add_node("entry", node_entry)
    g.add_node("intent_parsing", node_intent_parsing)
    g.add_node("route", node_route)
    g.add_node("db_query", node_db_query)
    g.add_node("clarify", node_clarify)
    g.add_node("need_approve", node_need_approve)
    g.add_node("execute", node_execute)
    g.add_node("done", node_done)
    
    # 진입점
    g.set_entry_point("entry")
    
    # 엣지
    g.add_edge("entry", "intent_parsing")
    g.add_edge("intent_parsing", "route")
    
    # Route → 조건부 분기
    g.add_conditional_edges(
        "route",
        route_after_route,
        {
            "clarify": "clarify",
            "db_query": "db_query",
            "need_approve": "need_approve",
            "execute": "execute"
        }
    )
    
    # 종단 노드들
    g.add_edge("clarify", END)
    g.add_edge("need_approve", END)
    g.add_edge("db_query", "done")
    g.add_edge("execute", "done")
    g.add_edge("done", END)
    
    return g.compile()