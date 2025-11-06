#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jira Agent - Node Functions

LangGraph 워크플로의 노드 함수들
"""

import json
from typing import Dict, Any

from core.agent_utils import AgentState, openai_client, get_project_metadata
from core.config import CHAT_MODEL
from core.jira import jira_client
from core.milvus_client import milvus_client
from core.executors import build_milvus_filter
from core.executors import execute_search, execute_create, execute_update, execute_delete


# ─────────────────────────────────────────────────────────
# 노드 함수들
# ─────────────────────────────────────────────────────────

def parse_intent_node(state: AgentState) -> AgentState:
    """
    사용자 입력 파싱 노드

    LLM을 사용해 사용자 의도와 슬롯 추출
    중단된 작업이 있으면 계속할지 새 작업인지 판단
    """
    user_input = state["user_input"]
    history = state.get("history", [])
    current_stage = state.get("stage")

    print(f"\n[NODE: parse_intent] 입력: {user_input}, 현재 stage: {current_stage}")

    # ─────────────────────────────────────────────────────────
    # 1. 중단된 작업이 있는 경우 - 계속할지 새 작업인지 판단
    # ─────────────────────────────────────────────────────────
    if current_stage in ["int_candidate", "approve", "clarify", "check_slots"]:
        candidate_issues = state.get("candidate_issues", [])
        slots = state.get("slots", {})
        missing_fields = state.get("missing_fields", [])

        # 컨텍스트 정보 구성
        context_info = ""
        if current_stage == "int_candidate":
            context_info = f"후보 이슈 {len(candidate_issues)}개 중 선택 대기"
        elif current_stage == "approve":
            context_info = f"{state.get('intent', '')} 작업 승인 대기"
        elif current_stage == "clarify":
            context_info = f"누락된 정보 입력 대기: {', '.join(missing_fields)}"
        elif current_stage == "check_slots":
            context_info = f"{state.get('intent', '')} 작업의 슬롯 검증 진행 중"

        decision_prompt = f"""현재 상황:
- 대기 중인 작업: {current_stage}
- 작업 컨텍스트: {context_info}
- 현재 슬롯: {json.dumps(slots, ensure_ascii=False)}
- 사용자 입력: "{user_input}"

판단:
1. 사용자가 이전 작업을 계속하려는 것인가?
   - int_candidate 단계: 숫자 입력(예: "1", "2") 또는 이슈 키(예: "KAN-1")
   - approve 단계: 승인 의사(예: "yes", "확인", "승인", "ok") 또는 거부(예: "no", "취소")
   - clarify 단계: 요청된 정보 제공(예: 프로젝트 키, 이슈 타입 등)
   - check_slots 단계: clarify에서 정보를 업데이트한 직후, 자동으로 슬롯 재검증 진행 중 (항상 continue)
   → "continue"

2. 아니면 완전히 새로운 Jira 작업을 시작하려는 것인가?
   - 명시적 취소/재시작 요청(예: "취소", "다시 처음부터", "새로 시작")
   - 전혀 다른 프로젝트/작업 언급(예: "다른 프로젝트에서 검색해줘")
   → "new_task"

답변을 JSON 형식으로:
{{
    "decision": "continue" or "new_task",
    "reason": "판단 근거"
}}
"""

        try:
            response = openai_client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[{"role": "user", "content": decision_prompt}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            decision_result = json.loads(response.choices[0].message.content)
            decision = decision_result.get("decision", "continue")

            print(f"[NODE: parse_intent] 판단: {decision} - {decision_result.get('reason', '')}")

            if decision == "new_task":
                # 상태 초기화하고 새 작업으로 파싱
                print("[NODE: parse_intent] 상태 초기화 후 새 작업 시작")
                state["stage"] = None
                state["candidate_issues"] = []
                state["slots"] = {}
                state["missing_fields"] = []
                # 아래로 계속해서 새 의도 파싱
            else:
                # 기존 작업 계속 - stage 유지하고 리턴
                print(f"[NODE: parse_intent] 기존 작업 계속: {current_stage}")
                return state

        except Exception as e:
            print(f"[NODE: parse_intent] 판단 오류: {e}, 기본적으로 계속으로 처리")
            # 오류 시 안전하게 계속으로 처리
            return state

    # ─────────────────────────────────────────────────────────
    # 2. 새 작업 의도 파싱 (기존 코드)
    # ─────────────────────────────────────────────────────────
    print(f"[NODE: parse_intent] 새 작업 파싱 시작")

    # 캐시된 프로젝트 메타데이터 가져오기 (첫 호출 시에만 Jira API 호출)
    try:
        project_keys, project_issue_types = get_project_metadata()

        project_list = ", ".join(project_keys) if project_keys else "프로젝트 키가 없음"

        # 프로젝트별 이슈 타입을 문자열로 포맷팅
        issue_types_str = ""
        for proj, types in project_issue_types.items():
            issue_types_str += f"\n      • {proj}: {', '.join(types)}"

        if not issue_types_str:
            issue_types_str = "\n      (이슈 타입 정보 없음)"

    except Exception as e:
        print(f"[WARN] 메타데이터 조회 실패: {e}")
        project_list = "연결 되지 않음"
        issue_types_str = "\n      (이슈 타입 정보 없음)"
        project_keys = []

    # 대화 이력 구성
    context = ""
    if history:
        context = "\n\n**최근 대화 이력:**\n"
        for i, h in enumerate(history[-4:], 1): # 최대 4개
            context += f"{i}. 사용자: {h.get('user', '')}\n"
            context += f"   응답: {h.get('response', '')}\n"

    system_prompt = f"""당신은 Jira 이슈 관리 어시스턴트입니다.
    사용자의 요청을 분석하여 다음을 JSON 형식으로 반환하세요:

    1. intent: 작업 의도
       - search: 이슈 검색
       - create: 이슈 생성
       - update: 이슈 수정
       - delete: 이슈 삭제
       - explain: Jira 사용법/기능 설명 요청 (예: "지라 사용법 알려줘", "이슈 생성 방법은?")
       - unknown: 파악 불가

    2. slots: 추출된 정보
       - project_key: 프로젝트 키 (사용 가능: {project_list})
       - summary: 이슈 제목
       - description: 이슈 설명
       - assignee: 담당자 이름
       - status: 이슈 상태 (해야 할 일, 진행 중, 완료)
       - priority: 중요도 (High, Medium, Low)
       - duedate: 마감일 (YYYY-MM-DD)
       - issuetype: 이슈 유형 (프로젝트별 사용 가능한 이슈 타입:{issue_types_str})
       - keyword: 검색 키워드
       - issue_key: 이슈 키 (예: {project_keys[0] if project_keys else 'KAN'}-1)
       - limit: 검색 결과 개수 (예: "3개" -> 3, "5개" -> 5)
       - explain_topic: 설명이 필요한 주제 (예: "이슈 생성", "검색 방법", "전반적인 사용법")

    3. confidence: 확신도 (0-1)
    4. missing_fields: 필수 필드 중 누락된 것

    **Intent 분류 예시:**
    - "지라 사용법 알려줘" -> intent: "explain", slots: {{"explain_topic": "전반적인 사용법"}}
    - "이슈 생성하는 방법은?" -> intent: "explain", slots: {{"explain_topic": "이슈 생성 방법"}}
    - "KAN 프로젝트에서 버그 찾아줘" -> intent: "search", slots: {{"project_key": "KAN", "keyword": "버그"}}
    - "테스트 이슈 만들어줘" -> intent: "create", slots: {{"summary": "테스트 이슈"}}

    **중요: project_key 설정 규칙**
    - "{project_list[0] if project_keys else 'KAN'} 프로젝트에서 찾아줘" -> project_key: "{project_list[0] if project_keys else 'KAN'}" 설정
    - "담당자 최민석인 이슈 찾아줘" -> project_key 설정 안 함 (전체 프로젝트 검색)
    - "{project_list[0] if project_keys else 'KAN'}에서 버그 찾아줘" -> project_key: "{project_list[0] if project_keys else 'KAN'}" 설정

    **생성(create)의 필수 필드:**
    - project_key, summary, issuetype

    **자연어 슬롯 파싱 예시:**
    - "KAN, 테스트 이슈, 작업" -> {{"project_key": "KAN", "summary": "테스트 이슈", "issuetype": "작업"}}
    - "TEST 프로젝트에 버그 리포트를 버그로 만들어줘" -> {{"project_key": "TEST", "summary": "버그 리포트", "issuetype": "버그"}}
    - "담당자 최민석인 이슈 3개 찾아줘" -> {{"assignee": "최민석", "limit": 3}}

    **응답 형식 (JSON만):**
    {{
        "intent": "search",
        "slots": {{}},
        "confidence": 0.9,
        "missing_fields": []
    }}
    {context}
    """

    try:
        response = openai_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"사용자 요청: {user_input}"}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )

        parsed = json.loads(response.choices[0].message.content)

        # 기존 슬롯과 병합 (clarify에서 돌아온 경우)
        existing_slots = state.get("slots", {})
        merged_slots = {**existing_slots, **parsed.get("slots", {})}

        state["intent"] = parsed.get("intent", "explain_method")
        state["slots"] = merged_slots
        state["confidence"] = parsed.get("confidence", 0.0)
        state["missing_fields"] = parsed.get("missing_fields", [])

        print(f"[NODE: parse_intent] 의도: {state['intent']}, 슬롯: {merged_slots}")

    except Exception as e:
        print(f"[NODE: parse_intent] 오류: {e}")
        state["intent"] = "unknown"
        state["confidence"] = 0.0

    return state


def explain_method_node(state: AgentState) -> AgentState:
    """
    기능 설명 노드

    Jira 사용법이나 기능에 대한 설명 제공
    unknown 의도일 때는 기본 안내 메시지 표시
    """
    intent = state.get("intent")
    slots = state.get("slots", {})
    explain_topic = slots.get("explain_topic", "전반적인 사용법")

    print(f"\n[NODE: explain_method] 의도: {intent}, 주제: {explain_topic}")

    # unknown 의도일 때는 기본 안내 메시지
    if intent == "unknown":
        state["response"] = """👋 안녕하세요! Jira 이슈 관리 어시스턴트입니다.

무엇을 도와드릴까요?

**제가 할 수 있는 일:**
  • 📝 이슈 생성 - "KAN 프로젝트에 버그 이슈 만들어줘"
  • 🔍 이슈 검색 - "담당자 최민석인 이슈 찾아줘"
  • ✏️  이슈 수정 - "KAN-1 이슈의 담당자를 홍길동으로 변경해줘"
  • 🗑️  이슈 삭제 - "KAN-5 이슈 삭제해줘"
  • 📚 사용법 설명 - "이슈 생성 방법 알려줘"

편하게 말씀해주세요!"""
        state["message"] = state["response"]
        state["stage"] = "done"
        print(f"[NODE: explain_method] unknown 의도 -> 기본 안내 메시지")
        return state

    # explain 의도일 때는 LLM으로 설명 생성
    system_prompt = """당신은 Jira 이슈 관리 전문가입니다.
    사용자가 요청한 Jira 기능에 대해 친절하고 명확하게 설명해주세요.

    설명 시 포함할 내용:
    1. 해당 기능의 목적과 사용 시기
    2. 구체적인 사용 방법 (단계별)
    3. 실제 사용 예시
    4. 주의사항이나 팁

    간결하고 이해하기 쉽게 작성하되, 너무 길지 않게 (5-10문장) 설명해주세요.
    마크다운 형식을 사용해도 좋습니다.
    """

    user_prompt = f"Jira에서 '{explain_topic}'에 대해 설명해주세요."

    try:
        response = openai_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7
        )

        explanation = response.choices[0].message.content

        state["response"] = f"📚 **{explain_topic}**\n\n{explanation}\n\n궁금한 점이 더 있으시면 말씀해주세요!"
        state["message"] = state["response"]
        state["stage"] = "done"

        print(f"[NODE: explain_method] 설명 생성 완료")

    except Exception as e:
        print(f"[NODE: explain_method] 오류: {e}")
        state["response"] = f"❌ 설명을 생성하는 중 오류가 발생했습니다: {e}"
        state["message"] = state["response"]
        state["stage"] = "done"

    return state


def check_slots_node(state: AgentState) -> AgentState:
    """
    슬롯 검증 노드

    의도별 필수 필드가 모두 채워졌는지 확인
    누락 시 clarify, 완료 시 curd_check로 이동

    수정/삭제 시 issue_key가 없지만 다른 정보(project_key, keyword 등)가 있으면
    find_candidates로 이동하여 후보군을 찾아서 제시
    """
    intent = state["intent"]
    slots = state["slots"]

    print(f"\n[NODE: check_slots] 의도: {intent}, 슬롯: {slots}")

    missing = []

    # 생성: 필수 필드 체크
    if intent == "create":
        required = ["project_key", "summary", "issuetype"]
        missing = [f for f in required if not slots.get(f)]

    # 수정: issue_key + 수정할 필드 최소 1개 필요
    elif intent == "update":
        if not slots.get("issue_key"):
            # issue_key가 없지만 다른 검색 조건이 있으면 find_candidates로
            has_search_criteria = any([
                slots.get("project_key"),
                slots.get("keyword"),
                slots.get("assignee"),
                slots.get("priority"),
                slots.get("issue_type")
            ])

            if has_search_criteria:
                # 후보군 찾기로 이동
                state["stage"] = "find_candidates"
                state["missing_fields"] = []
                print(f"[NODE: check_slots] issue_key 없지만 검색 조건 있음 -> find_candidates")
                return state
            else:
                # 검색 조건도 없으면 issue_key 요청
                missing = ["issue_key"]

        # issue_key는 있는데 수정할 필드가 하나도 없으면
        if not missing:  # issue_key는 있음
            update_fields = ["summary", "description", "assignee", "priority", "status", "duedate"]
            has_update_field = any([slots.get(f) for f in update_fields])

            if not has_update_field:
                # 수정할 내용이 없음
                missing = ["update_fields"]
                print(f"[NODE: check_slots] 수정할 필드가 없음 -> clarify")

    # 삭제: issue_key만 필요
    elif intent == "delete":
        if not slots.get("issue_key"):
            # issue_key가 없지만 다른 검색 조건이 있으면 find_candidates로
            has_search_criteria = any([
                slots.get("project_key"),
                slots.get("keyword"),
                slots.get("assignee"),
                slots.get("priority"),
                slots.get("issue_type")
            ])

            if has_search_criteria:
                # 후보군 찾기로 이동
                state["stage"] = "find_candidates"
                state["missing_fields"] = []
                print(f"[NODE: check_slots] issue_key 없지만 검색 조건 있음 -> find_candidates")
                return state
            else:
                # 검색 조건도 없으면 issue_key 요청
                missing = ["issue_key"]

    # 검색: 필수 필드 없음 (선택적으로 keyword, project_key 등 사용)
    elif intent == "search":
        missing = []

    # 누락된 필드가 있으면 clarify로
    if missing:
        state["stage"] = "clarify"
        state["missing_fields"] = missing
        print(f"[NODE: check_slots] 누락 필드: {missing} -> clarify")
    else:
        # 모든 필드가 채워졌으면 CURD_check로 이동 (실제 Jira 데이터 검증)
        state["stage"] = "curd_check"
        state["missing_fields"] = []
        print(f"[NODE: check_slots] 모든 필드 OK -> curd_check")

    return state


def clarify_node(state: AgentState) -> AgentState:
    """
    정보 요청 및 파싱 노드

    두 가지 모드로 동작:
    1. 첫 호출 (missing_fields 있음): 메시지 생성하고 END
    2. 두 번째 호출 (user_input 있음): LLM으로 파싱해서 슬롯 채우기
    """
    intent = state.get("intent")
    slots = state.get("slots", {})
    missing = state.get("missing_fields", [])
    user_input = state.get("user_input", "")

    print(f"\n[NODE: clarify] 의도: {intent}, 누락 필드: {missing}, 입력: {user_input}")

    # 캐시된 프로젝트 메타데이터 가져오기
    try:
        project_keys, project_issue_types = get_project_metadata()
        project_list = ", ".join(project_keys)
    except:
        project_keys = []
        project_issue_types = {}
        project_list = "없습니다."

    # ─────────────────────────────────────────────────────────
    # Case 1: 사용자 입력이 있고, 이미 응답이 생성되었으면 LLM으로 파싱
    # (response가 있으면 이미 메시지를 보여줬다는 의미 → 두 번째 호출)
    # ─────────────────────────────────────────────────────────
    existing_response = state.get("response", "")
    if user_input and missing and existing_response:
        print(f"[NODE: clarify] LLM 파싱 시작: {user_input}")

        # 누락된 필드 설명
        field_descriptions = []
        for field in missing:
            if field == "project_key":
                field_descriptions.append(f"- project_key: 프로젝트 키 (사용 가능: {project_list})")
            elif field == "issuetype":
                project_key = slots.get("project_key")
                if project_key and project_key in project_issue_types:
                    types = ", ".join(project_issue_types[project_key])
                    field_descriptions.append(f"- issuetype: 이슈 유형 ({project_key}: {types})")
                else:
                    field_descriptions.append(f"- issuetype: 이슈 유형")
            elif field == "issue_key":
                example = f"{project_keys[0]}-1" if project_keys else "KAN-1"
                field_descriptions.append(f"- issue_key: 이슈 키 (예: {example})")
            elif field == "summary":
                field_descriptions.append(f"- summary: 이슈 제목")
            elif field == "description":
                field_descriptions.append(f"- description: 이슈 설명")
            elif field == "assignee":
                field_descriptions.append(f"- assignee: 담당자 이름")
            elif field == "priority":
                field_descriptions.append(f"- priority: 중요도 (High, Medium, Low)")
            elif field == "duedate":
                field_descriptions.append(f"- duedate: 마감일 (YYYY-MM-DD)")
            else:
                field_descriptions.append(f"- {field}")

        parse_prompt = f"""사용자가 누락된 정보를 제공했습니다.

현재 작업: {intent}
기존 슬롯: {json.dumps(slots, ensure_ascii=False)}
누락된 필드:
{chr(10).join(field_descriptions)}

사용자 입력: "{user_input}"

사용자 입력에서 누락된 필드 값을 추출하세요.
- 여러 개 입력되었으면 모두 추출
- 입력되지 않은 필드는 null로

JSON 형식으로 답변:
{{
    "project_key": "추출된 값 or null",
    "issuetype": "추출된 값 or null",
    "issue_key": "추출된 값 or null",
    "summary": "추출된 값 or null",
    "description": "추출된 값 or null",
    "assignee": "추출된 값 or null",
    "status": "추출된 값 or null (해야 할 일/진행 중/완료)",
    "priority": "추출된 값 or null",
    "duedate": "추출된 값 or null"
}}
"""

        try:
            response = openai_client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[{"role": "user", "content": parse_prompt}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            parsed = json.loads(response.choices[0].message.content)
            print(f"[NODE: clarify] 파싱 결과: {parsed}")

            # 슬롯 업데이트 (null이 아닌 값만)
            for field, value in parsed.items():
                if value and value != "null":
                    slots[field] = value
                    print(f"[NODE: clarify] 슬롯 업데이트: {field} = {value}")

            state["slots"] = slots

            # 다시 필수 필드 체크
            required_fields = []
            new_missing = []

            if intent == "create":
                required_fields = ["project_key", "summary", "issuetype"]
                new_missing = [f for f in required_fields if not slots.get(f)]

            elif intent == "update":
                # update는 issue_key + 수정할 필드 1개 이상 필요
                if not slots.get("issue_key"):
                    new_missing.append("issue_key")
                else:
                    # issue_key는 있는데 수정할 필드 체크
                    update_fields = ["summary", "description", "assignee", "priority", "status", "duedate"]
                    has_update_field = any([slots.get(f) for f in update_fields])
                    if not has_update_field:
                        new_missing.append("update_fields")

            elif intent == "delete":
                required_fields = ["issue_key"]
                new_missing = [f for f in required_fields if not slots.get(f)]

            elif intent == "search":
                # 검색은 필수 필드 없음
                pass

            if new_missing:
                # 여전히 누락된 필드가 있음 → clarify 유지, END로
                print(f"[NODE: clarify] 여전히 누락: {new_missing}")
                state["stage"] = "clarify"
                state["missing_fields"] = new_missing
                state["response"] = "✅ 정보를 업데이트했습니다."
                state["message"] = state["response"]
            else:
                # 모든 필드 채워짐 → check_slots로 자동 진행 (END 안 감)
                print(f"[NODE: clarify] 모든 필드 완료 -> check_slots로 자동 진행")
                state["stage"] = "clarify"  # clarify 유지 (routing에서 판단)
                state["missing_fields"] = []  # 비어있으면 route_after_clarify가 check_slots로
                state["response"] = "✅ 정보가 완료되었습니다."
                state["message"] = state["response"]

            return state

        except Exception as e:
            print(f"[NODE: clarify] 파싱 오류: {e}")
            # 오류 시 다시 clarify로 (다음 입력 대기)
            state["stage"] = "clarify"
            state["response"] = f"❌ 정보를 이해하지 못했습니다. 다시 입력해주세요."
            state["message"] = state["response"]
            return state

    # ─────────────────────────────────────────────────────────
    # Case 2: 첫 호출 - 메시지 생성하고 END
    # ─────────────────────────────────────────────────────────
    response = f"💬 **{intent}** 작업을 위해 다음 정보가 필요합니다:\n\n"

    for field in missing:
        if field == "project_key":
            response += f"  • 프로젝트 키 (사용 가능: {project_list})\n"

        elif field == "issuetype":
            # 이미 project_key가 있으면 해당 프로젝트의 이슈 타입만 표시
            project_key = slots.get("project_key")
            if project_key and project_key in project_issue_types:
                types = ", ".join(project_issue_types[project_key])
                response += f"  • 이슈 유형 ({project_key} 프로젝트: {types})\n"
            else:
                response += f"  • 이슈 유형 (프로젝트를 먼저 지정해주세요)\n"

        elif field == "issue_key":
            example = f"{project_keys[0]}-1" if project_keys else "KAN-1"
            response += f"  • 이슈 키 (예: {example})\n"

        elif field == "update_fields":
            # update 작업에서 수정할 필드 요청
            response += f"  • 수정할 내용 (예: 담당자를 홍길동으로, 상태를 완료로, 제목을 새 제목으로)\n"
            response += f"    - 가능한 필드: 제목(summary), 설명(description), 담당자(assignee), 상태(status), 우선순위(priority), 마감일(duedate)\n"

        elif field == "summary":
            response += f"  • 이슈 제목\n"

        elif field == "description":
            response += f"  • 이슈 설명\n"

        elif field == "assignee":
            response += f"  • 담당자 이름\n"

        elif field == "priority":
            response += f"  • 중요도 (High, Medium, Low)\n"

        elif field == "duedate":
            response += f"  • 마감일 (YYYY-MM-DD)\n"

        else:
            response += f"  • {field}\n"

    response += "\n입력해주세요:"

    state["response"] = response
    state["message"] = response
    state["stage"] = "clarify"

    return state


def find_candidates_node(state: AgentState) -> AgentState:
    """
    후보 이슈 찾기 노드

    수정/삭제 시 issue_key가 명시되지 않았지만 다른 검색 조건이 있을 때,
    Milvus에서 후보군을 찾아서 사용자에게 선택하도록 제시

    후보가 1개면 자동으로 선택, 여러 개면 사용자 선택 요청,
    없으면 clarify로 이동
    """
    intent = state.get("intent")
    slots = state.get("slots", {})

    print(f"\n[NODE: find_candidates] 후보 검색: intent={intent}, slots={slots}")

    # Milvus에서 검색
    keyword = slots.get("keyword", "")
    filter_expr = build_milvus_filter(slots)

    print(f"[NODE: find_candidates] Milvus 검색: keyword='{keyword}', filter={filter_expr}")

    try:
        results = milvus_client.search(
            query_text=keyword if keyword else "이슈",
            filter_expr=filter_expr,
            limit=10  # 최대 10개 후보
        )

        if not results or len(results) == 0:
            # 후보가 없음 -> issue_key 직접 입력 요청
            print(f"[NODE: find_candidates] 후보 없음 -> clarify")
            state["response"] = "❌ 조건에 맞는 이슈를 찾을 수 없습니다.\n\n이슈 키를 직접 입력해주세요 (예: KAN-1):"
            state["message"] = state["response"]
            state["stage"] = "clarify"
            state["missing_fields"] = ["issue_key"]
            state["candidate_issues"] = []
            return state

        elif len(results) == 1:
            # 후보가 1개 -> 자동 선택
            selected = results[0]
            issue_key = selected.get("key")
            print(f"[NODE: find_candidates] 후보 1개 자동 선택: {issue_key}")

            state["slots"]["issue_key"] = issue_key
            state["candidate_issues"] = []

            # curd_check로 이동하여 검증
            state["stage"] = "curd_check"
            return state

        else:
            # 후보가 여러 개 -> 사용자 선택 요청 (int_candidate로 이동)
            print(f"[NODE: find_candidates] 후보 {len(results)}개 발견 -> int_candidate로 이동")

            response = f"🔍 {len(results)}개의 이슈를 찾았습니다. 어떤 이슈를 "
            response += "수정" if intent == "update" else "삭제"
            response += "하시겠습니까?\n\n"

            for i, result in enumerate(results, 1):
                response += f"[{i}] {result['key']}: {result['summary']}\n"
                response += f"    - 프로젝트: {result['project']}, 상태: {result['status']}\n"
                if result.get('assignee'):
                    response += f"    - 담당자: {result['assignee']}\n"
                response += "\n"

            response += "번호를 입력하거나, 이슈 키를 직접 입력해주세요:"

            state["response"] = response
            state["message"] = response
            state["stage"] = "int_candidate"  # int_candidate로 이동
            state["candidate_issues"] = results
            return state

    except Exception as e:
        print(f"[NODE: find_candidates] 오류: {e}")
        state["response"] = f"❌ 이슈 검색 중 오류가 발생했습니다: {e}\n\n이슈 키를 직접 입력해주세요:"
        state["message"] = state["response"]
        state["stage"] = "clarify"
        state["missing_fields"] = ["issue_key"]
        state["candidate_issues"] = []
        return state


def int_candidate_node(state: AgentState) -> AgentState:
    """
    후보 선택 중단 노드

    find_candidates에서 여러 후보를 보여준 후,
    사용자가 번호나 issue_key를 입력하면 이 노드에서 처리

    REST API 방식:
    - 첫 호출: 후보 목록만 제시하고 END (대기)
    - 두 번째 호출: 사용자 선택 처리 후 END
    """
    user_input = state.get("user_input", "")
    candidate_issues = state.get("candidate_issues", [])

    print(f"\n[NODE: int_candidate] 사용자 선택 처리: {user_input}")

    if not candidate_issues:
        # 후보가 없으면 clarify로
        state["stage"] = "clarify"
        state["missing_fields"] = ["issue_key"]
        state["response"] = "이슈를 선택할 수 없습니다. 이슈 키를 직접 입력해주세요:"
        state["message"] = state["response"]
        return state

    user_choice = user_input.strip()

    # 숫자인 경우 (1, 2, 3, ...)
    if user_choice.isdigit():
        idx = int(user_choice) - 1
        if 0 <= idx < len(candidate_issues):
            selected = candidate_issues[idx]
            issue_key = selected.get("key")
            print(f"[NODE: int_candidate] 번호 선택: {issue_key}")

            # 선택된 issue_key를 슬롯에 저장
            state["slots"]["issue_key"] = issue_key
            state["candidate_issues"] = []

            # check_slots로 이동 (다음 요청 시 실행됨)
            state["stage"] = "check_slots"
            state["response"] = f"✅ {issue_key} 선택되었습니다."
            state["message"] = state["response"]
            print(f"[NODE: int_candidate] check_slots로 설정 (다음 요청에서 실행)")
            return state
        else:
            # 잘못된 번호 - 다시 입력 대기 (END)
            state["stage"] = "int_candidate"
            state["response"] = f"❌ 잘못된 번호입니다. 1-{len(candidate_issues)} 사이의 숫자를 입력해주세요."
            state["message"] = state["response"]
            return state
    else:
        # issue_key 직접 입력 (예: KAN-1)
        print(f"[NODE: int_candidate] 직접 입력: {user_choice}")

        state["slots"]["issue_key"] = user_choice
        state["candidate_issues"] = []

        # check_slots로 이동 (다음 요청 시 실행됨)
        state["stage"] = "check_slots"
        state["response"] = f"✅ {user_choice} 선택되었습니다."
        state["message"] = state["response"]
        print(f"[NODE: int_candidate] check_slots로 설정 (다음 요청에서 실행)")
        return state


def curd_check_node(state: AgentState) -> AgentState:
    """
    CURD 데이터 검증 노드

    실제 Jira 데이터와 대조하여 유효성 검증:
    - 생성: project_key, issue_key가 실제로 존재하는지
    - 검색 : project_key가 입력으로 들어왔을 때, 없는 project_key인 경우 아니라고 말해주기 위해서
    - 수정/삭제: issue_key가 실제로 존재하는지

    검증 실패 시 clarify로, 성공 시 execute(검색) 또는 approve(생성/수정/삭제)로 이동
    """
    intent = state.get("intent")
    slots = state.get("slots", {})

    print(f"\n[NODE: curd_check] 의도: {intent}, 슬롯: {slots}")

    # 캐시된 프로젝트 메타데이터 가져오기
    try:
        project_keys, _ = get_project_metadata()
    except Exception as e:
        print(f"[NODE: curd_check] 메타데이터 조회 실패: {e}")
        project_keys = []

    # 1. 생성/검색: project_key 검증
    if intent in ["create", "search"]:
        project_key = slots.get("project_key")

        # 검색의 경우 project_key가 없어도 OK (전체 검색)
        if intent == "search" and not project_key:
            print(f"[NODE: curd_check] 검색: project_key 없음 (전체 검색) -> execute")
            state["stage"] = "execute"
            return state

        # project_key가 있는 경우 검증
        if project_key:
            if project_key not in project_keys:
                # 존재하지 않는 프로젝트
                print(f"[NODE: curd_check] 프로젝트 '{project_key}' 존재하지 않음 -> clarify")

                project_list = ", ".join(project_keys) if project_keys else "사용 가능한 프로젝트가 없습니다"
                state["response"] = f"❌ 프로젝트 '{project_key}'가 존재하지 않습니다.\n\n사용 가능한 프로젝트: {project_list}\n\n다시 입력해주세요:"
                state["message"] = state["response"]
                state["stage"] = "clarify"
                state["missing_fields"] = ["project_key"]

                # 잘못된 project_key 제거
                slots.pop("project_key", None)
                state["slots"] = slots

                return state
            else:
                print(f"[NODE: curd_check] 프로젝트 '{project_key}' 검증 OK")

        # 검증 통과: 검색은 바로 execute, 생성은 approve로
        if intent == "search":
            state["stage"] = "execute"
            print(f"[NODE: curd_check] 검색 검증 완료 -> execute")
        else:  # create
            state["stage"] = "approve"
            print(f"[NODE: curd_check] 생성 검증 완료 -> approve")

    # 2. 수정/삭제: issue_key 검증
    elif intent in ["update", "delete"]:
        issue_key = slots.get("issue_key")

        if not issue_key:
            print(f"[NODE: curd_check] issue_key 누락 -> clarify")
            state["stage"] = "clarify"
            state["missing_fields"] = ["issue_key"]
            return state

        # Milvus에서 먼저 검색 (빠름)
        try:
            print(f"[NODE: curd_check] Milvus에서 '{issue_key}' 검색 중...")
            milvus_results = milvus_client.search(
                query_text=issue_key,
                filter_expr=f"issue_key == '{issue_key}'",
                limit=1
            )

            # Milvus에서 찾으면 바로 승인으로
            if milvus_results and len(milvus_results) > 0:
                # issue_key가 정확히 일치하는지 확인
                found = any(r.get("issue_key") == issue_key for r in milvus_results)

                if found:
                    print(f"[NODE: curd_check] 이슈 '{issue_key}' Milvus에서 발견 -> approve")
                    state["stage"] = "approve"
                    return state

            # Milvus에서 못 찾으면 Jira API로 확인 (최신 이슈일 수도 있음)
            print(f"[NODE: curd_check] Milvus에서 없음, Jira API로 확인 중...")
            jql = f"key = {issue_key}"
            jira_results = jira_client.search_issues(jql=jql, max_results=1)

            if jira_results and len(jira_results) > 0:
                print(f"[NODE: curd_check] 이슈 '{issue_key}' Jira에서 발견 -> approve")
                state["stage"] = "approve"
            else:
                # 이슈가 존재하지 않음
                print(f"[NODE: curd_check] 이슈 '{issue_key}' 존재하지 않음 -> clarify")

                state["response"] = f"❌ 이슈 '{issue_key}'를 찾을 수 없습니다.\n\n올바른 이슈 키를 입력해주세요:"
                state["message"] = state["response"]
                state["stage"] = "clarify"
                state["missing_fields"] = ["issue_key"]

                # 잘못된 issue_key 제거
                slots.pop("issue_key", None)
                state["slots"] = slots

                return state

        except Exception as e:
            print(f"[NODE: curd_check] 이슈 조회 오류: {e}")

            # 에러 발생 시 clarify로 (이슈가 없거나 권한 없음)
            state["response"] = f"❌ 이슈 '{issue_key}'를 조회할 수 없습니다.\n\n다시 확인해주세요:"
            state["message"] = state["response"]
            state["stage"] = "clarify"
            state["missing_fields"] = ["issue_key"]

            # 잘못된 issue_key 제거
            slots.pop("issue_key", None)
            state["slots"] = slots

            return state

    return state


def approve_node(state: AgentState) -> AgentState:
    """
    승인 요청 노드

    생성/수정/삭제 작업에 대한 승인 요청

    두 가지 모드:
    1. 첫 호출: 승인 메시지 생성 후 END
    2. 두 번째 호출: yes/no 처리
    """
    intent = state["intent"]
    slots = state["slots"]
    user_input = state.get("user_input", "").strip().lower()
    existing_response = state.get("response", "")

    print(f"\n[NODE: approve] 의도: {intent}, 입력: {user_input}")

    # ─────────────────────────────────────────────────────────
    # Case 1: 사용자 입력이 있고 승인 메시지가 이미 표시된 경우 yes/no 처리
    # (기존 response에 "승인"이라는 단어가 포함되어 있으면 이미 승인 메시지를 표시한 것)
    # ─────────────────────────────────────────────────────────
    if user_input and existing_response and "승인" in existing_response:
        if user_input in ["yes", "y", "예", "네", "승인"]:
            print(f"[NODE: approve] 승인됨 -> execute")
            state["stage"] = "execute"
            return state
        elif user_input in ["no", "n", "아니오", "취소"]:
            print(f"[NODE: approve] 거부됨 -> done")
            state["stage"] = "done"
            state["response"] = "❌ 작업이 취소되었습니다."
            state["message"] = state["response"]
            return state
        else:
            # 잘못된 입력 - 다시 물어봄
            print(f"[NODE: approve] 잘못된 입력: {user_input}")
            state["stage"] = "approve"
            # 기존 메시지 유지
            return state

    # ─────────────────────────────────────────────────────────
    # Case 2: 첫 호출 - 승인 메시지 생성
    # ─────────────────────────────────────────────────────────

    if intent == "create":
        response = "⚠️ 이슈 생성을 승인해주세요:\n\n"
        response += f"  • 프로젝트: {slots.get('project_key')}\n"
        response += f"  • 제목: {slots.get('summary')}\n"
        response += f"  • 유형: {slots.get('issuetype')}\n"

        if slots.get("description"):
            response += f"  • 설명: {slots.get('description')}\n"
        if slots.get("assignee"):
            response += f"  • 담당자: {slots.get('assignee')}\n"
        if slots.get("priority"):
            response += f"  • 중요도: {slots.get('priority')}\n"
        if slots.get("duedate"):
            response += f"  • 마감일: {slots.get('duedate')}\n"

        response += "\n✅ 승인: yes | ❌ 취소: no"

    elif intent == "update":
        issue_key = slots.get("issue_key")
        response = f"⚠️ {issue_key} 이슈를 수정하시겠습니까?\n\n"

        changes = []
        if slots.get("summary"):
            changes.append(f"  • 제목: {slots.get('summary')}")
        if slots.get("description"):
            changes.append(f"  • 설명: {slots.get('description')}")
        if slots.get("assignee"):
            changes.append(f"  • 담당자: {slots.get('assignee')}")
        if slots.get("priority"):
            changes.append(f"  • 중요도: {slots.get('priority')}")

        if changes:
            response += "변경 내용:\n" + "\n".join(changes)

        response += "\n\n✅ 승인: yes | ❌ 취소: no"

    elif intent == "delete":
        issue_key = slots.get("issue_key")
        response = f"⚠️ {issue_key} 이슈를 삭제하시겠습니까?\n"
        response += "⚠️ 이 작업은 되돌릴 수 없습니다!\n\n"
        response += "✅ 승인: yes | ❌ 취소: no"

    state["response"] = response
    state["message"] = response
    state["stage"] = "approve"

    return state


def execute_node(state: AgentState) -> AgentState:
    """
    작업 실행 노드

    실제 Jira/Milvus 작업 수행
    실패 시 오류 메시지 반환
    """

    intent = state["intent"]
    slots = state["slots"]

    print(f"\n[NODE: execute] 실행: {intent}")

    try:
        if intent == "search":
            result = execute_search(slots)
        elif intent == "create":
            result = execute_create(slots)
        elif intent == "update":
            result = execute_update(slots)
        elif intent == "delete":
            result = execute_delete(slots)
        else:
            result = {
                "response": "이해하지 못했습니다. 다시 말씀해주세요.",
                "message": "이해하지 못했습니다.",
                "ok": False
            }

        state["response"] = result.get("response", "")
        state["message"] = result.get("message", result.get("response", ""))
        state["data"] = result.get("data")
        state["stage"] = "done"

    except Exception as e:
        print(f"[NODE: execute] 오류 발생: {e}")
        import traceback
        traceback.print_exc()

        error_msg = f"❌ 작업 실행 중 오류가 발생했습니다: {str(e)}"
        state["response"] = error_msg
        state["message"] = error_msg
        state["data"] = None
        state["stage"] = "done"

    return state
