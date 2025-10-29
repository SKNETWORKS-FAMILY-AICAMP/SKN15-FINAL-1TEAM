# -*- coding: utf-8 -*-
"""
agent.py - 에이전트 (Deprecated - graph.py 사용 권장)

역할:
이 파일은 하위 호환성을 위해 유지되지만, 실제로는 graph.py의 LangGraph 워크플로우를 사용해야 합니다.
새로운 프로젝트에서는 이 파일 대신 graph.py를 직접 사용하세요.

마이그레이션:
- Agent().handle(utter, approve) → graph.invoke({"utter": utter, "approve": approve})
- FaissStore → MilvusStore (자동 처리됨)

연결:
- graph.py → 모든 워크플로우 로직
- milvus_client.py → 벡터 검색
"""

from typing import Dict, Any, Optional
import warnings

from .graph import build_graph, get_store


class Agent:
    """
    레거시 에이전트 래퍼 (하위 호환성용)
    
    새 코드에서는 graph.py의 build_graph()를 직접 사용하세요.
    
    사용 예시 (레거시):
        agent = Agent()
        result = agent.handle("KAN 프로젝트 검색")
    
    권장 사용법:
        from .graph import build_graph
        graph = build_graph()
        result = graph.invoke({"utter": "KAN 프로젝트 검색", "approve": False})
    """
    
    def __init__(self, store=None):
        """
        Args:
            store: Deprecated. Milvus 스토어는 자동으로 관리됩니다.
        """
        if store is not None:
            warnings.warn(
                "store 파라미터는 더 이상 사용되지 않습니다. "
                "Milvus 스토어는 graph.py에서 자동으로 관리됩니다.",
                DeprecationWarning
            )
        
        self._graph = build_graph()
        self.store = get_store()  # 호환성용
    
    def handle(self, utter: str, approve: bool = False) -> Dict[str, Any]:
        """
        메인 엔트리 (Deprecated)
        
        Args:
            utter: 사용자 발화
            approve: 승인 여부
        
        Returns:
            {"stage": "...", "agent_output": {...}}에서 agent_output만 반환
        
        Note:
            이 메서드는 하위 호환성을 위해 유지됩니다.
            새 코드에서는 graph.invoke()를 직접 사용하세요.
        """
        warnings.warn(
            "Agent.handle()은 deprecated입니다. "
            "graph.invoke(state)를 직접 사용하세요.",
            DeprecationWarning
        )
        
        # LangGraph 워크플로우 실행
        state = {
            "utter": utter,
            "approve": approve
        }
        
        result = self._graph.invoke(state)
        
        # 레거시 포맷으로 변환
        return result.get("agent_output", {})


# 하위 호환성 함수들
def summarize_project_from_issues(items):
    """Deprecated: 검색 결과 요약"""
    if not items:
        return ""
    from collections import Counter
    cnt = Counter([it.get("issuetype") for it in items if it.get("issuetype")])
    tops = ", ".join([f"{k}:{v}" for k, v in cnt.most_common(3)])
    titles = ", ".join([it.get("summary", "") for it in items[:3]])
    return f"이슈 유형 분포({tops}), 대표 제목: {titles}"


def build_approval_card(intent: str, slots: dict, preview: dict) -> dict:
    """Deprecated: 승인 카드 생성 (graph.py에서 처리)"""
    warnings.warn(
        "build_approval_card()는 deprecated입니다. "
        "graph.py의 node_need_approve()를 사용하세요.",
        DeprecationWarning
    )
    
    card = {
        "title": f"[승인 필요] {intent.upper()} 요청",
        "action": intent,
        "danger_level": "high" if intent == "delete" else ("medium" if intent == "update" else "low"),
        "target": {"project_key": slots.get("project_key"), "issue_key": slots.get("issue_key")},
        "auto_labels": (preview or {}).get("labels_auto"),
        "summary": slots.get("summary"),
        "description": slots.get("description"),
    }
    
    if intent == "create":
        card["changes"] = {
            "issue_type": slots.get("issue_type", "Task"),
            "priority": slots.get("priority"),
            "labels": sorted(list(set((slots.get("labels") or []) + ((preview or {}).get("labels_auto") or [])))),
        }
        if preview and "candidates" in preview:
            card["candidates"] = preview["candidates"]
    
    elif intent == "update":
        card["changes"] = (preview or {}).get("changes", {})
        if preview and "candidates" in preview:
            card["candidates"] = preview["candidates"]
    
    elif intent == "delete":
        card["candidates"] = (preview or {}).get("candidates", [])
    
    return card