#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jira Agent - LangGraph 기반 (v2)

LangGraph를 사용한 상태 머신 기반 Jira 에이전트
나중에 Google Agent와 통합 가능하도록 설계

역할:
1. 사용자 의도 파악 (검색/생성/수정/삭제)
2. 슬롯 필링 (필수 정보 수집)
3. 승인 확인 (위험한 작업)
4. Jira/Milvus 연동
"""

from typing import Dict

from langgraph.checkpoint.memory import MemorySaver

# 패키지 내부에서 import할 때와 직접 실행할 때를 구분
try:
    from core.routing import build_graph
except ModuleNotFoundError:
    from routing import build_graph


# ─────────────────────────────────────────────────────────
# Jira Agent 클래스
# ─────────────────────────────────────────────────────────

class JiraAgent:
    """LangGraph 기반 Jira Agent"""

    def __init__(self):
        """초기화"""
        self.workflow = build_graph()
        self.checkpointer = MemorySaver()

        # Checkpointer만 사용 (interrupt_before 제거)
        # 각 노드가 END로 종료되면서 자동으로 중단됨
        self.app = self.workflow.compile(checkpointer=self.checkpointer)

        print("✅ JiraAgent (LangGraph) 초기화 완료")
        print("   - Checkpointer를 통한 상태 저장/복원")

    def process(self, user_input: str, session_id: str = "default") -> Dict:
        """
        메시지 처리 (모든 라우팅은 LangGraph에 위임)

        Root Router가 설치된 그래프에 단순히 입력을 전달하고 결과를 받습니다.

        Args:
            user_input: 사용자 입력
            session_id: 세션 ID

        Returns:
            응답 딕셔너리
        """
        config = {"configurable": {"thread_id": session_id}}

        # 새 입력만 준비
        inputs = {"user_input": user_input}

        try:
            print(f"[AGENT] 그래프 실행: user_input='{user_input}', session_id={session_id}")

            final_state = self.app.invoke(inputs, config=config)

            # 최종 결과 반환
            return {
                "stage": final_state.get("stage", "done"),
                "message": final_state.get("message", ""),
                "response": final_state.get("response", ""),
                "data": final_state.get("data"),
                "missing_fields": final_state.get("missing_fields", []),
                "session_id": session_id
            }

        except Exception as e:
            print(f"[ERROR] 그래프 실행 오류: {e}")
            import traceback
            traceback.print_exc()
            return {
                "stage": "done",
                "message": f"오류가 발생했습니다: {str(e)}",
                "response": f"오류가 발생했습니다: {str(e)}",
                "session_id": session_id
            }

# 전역 인스턴스
jira_agent = JiraAgent()

