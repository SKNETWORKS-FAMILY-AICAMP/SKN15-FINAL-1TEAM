# -*- coding: utf-8 -*-
"""
nlu.py - 자연어 이해 (NLU)

역할:
1. 사용자 발화 → 의도(intent) + 슬롯(slots) 추출
2. OpenAI GPT로 JSON 파싱
3. 동적 이슈 타입 힌트 제공

연결:
- config.py → OPENAI_API_KEY, CHAT_MODEL 사용
- jira_client.py → list_issue_types로 실제 타입 조회
- utils.py → norm_issue_type으로 타입 정규화
- graph.py → Intent Parsing 노드에서 호출
"""

import json
from typing import Dict, Any, Optional, List
from openai import OpenAI

from .config import OPENAI_API_KEY, CHAT_MODEL, CHAT_TEMPERATURE, CHAT_MAX_TOKENS


# OpenAI 클라이언트
_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# 타입 캐싱 (API 호출 최소화)
_CACHED_TYPES: Optional[List[str]] = None


def get_available_types() -> List[str]:
    """
    사용 가능한 이슈 타입 조회 (캐싱)
    
    Returns:
        타입 이름 리스트 (예: ["Task", "버그", "웹 가이드"])
    """
    global _CACHED_TYPES
    
    if _CACHED_TYPES is None:
        from .jira_client import list_issue_types
        
        try:
            types = list_issue_types(limit=50)
            _CACHED_TYPES = [t["name"] for t in types]
        except Exception as e:
            print(f"[경고] 타입 조회 실패: {e}")
            _CACHED_TYPES = ["Task", "Bug", "Story", "Epic", "Subtask"]  # 폴백
    
    return _CACHED_TYPES


def build_intent_prompt(utter: str, include_types: bool = True) -> str:
    """
    의도 파싱 프롬프트 생성
    
    Args:
        utter: 사용자 발화
        include_types: 실제 이슈 타입 포함 여부
    
    Returns:
        프롬프트 문자열
    """
    # 실제 이슈 타입 힌트
    available_types = get_available_types() if include_types else ['작업', '버그', '문서']
    types_str = ', '.join(available_types[:10])

    # 개선된 프롬프트
    prompt = f"""당신은 Jira 이슈 관리 에이전트입니다.
사용자 발화를 분석하여 다음 JSON 형식으로 반환하세요:

{{
  "intent": "create|search|update|delete",
  "slots": {{
    "project_key": "프로젝트 키 (예: KAN, HIN)",
    "issue_key": "이슈 키 (예: KAN-123)",
    "summary": "이슈 제목",
    "description": "상세 설명",
    "issue_type": "이슈 타입",
    "priority": "우선순위 (High, Medium, Low)",
    "assignee": "담당자 이름",
    "labels": ["라벨1", "라벨2"]
  }}
}}

**중요 규칙**:
1. 이슈 키(KAN-123)가 명시되면 반드시 issue_key에 추출
2. "담당자", "할당", "assign" 키워드가 있으면 assignee 추출
3. "라벨", "label", "태그" 키워드가 있으면 labels 배열로 추출
4. update 의도일 때는 변경할 필드만 추출

**담당자 추출 예시**:
- "담당자 최민석으로" → {{"assignee": "최민석"}}
- "김철수에게 할당" → {{"assignee": "김철수"}}
- "나에게 할당" → {{"assignee": "me"}}
- "할당 해제" → {{"assignee": null}}

**라벨 추출 예시**:
- "라벨 bug api 추가" → {{"labels": ["bug", "api"]}}
- "태그 urgent로" → {{"labels": ["urgent"]}}
- "labels backend test" → {{"labels": ["backend", "test"]}}

**이슈 타입**:
사용 가능한 이슈 타입: {types_str}

사용자 발화: "{utter}"

JSON만 반환하세요:"""
    
    return prompt


def extract_intent_slots(utter: str, use_llm: bool = True) -> Dict[str, Any]:
    """
    사용자 발화에서 의도와 슬롯 추출
    
    Args:
        utter: 사용자 발화
        use_llm: True면 LLM 사용, False면 룰베이스 폴백
    
    Returns:
        {"intent": "...", "slots": {...}}
    
    예시:
        extract_intent_slots("KAN 프로젝트에 로그인 버그 생성")
        → {
            "intent": "create",
            "slots": {
                "project_key": "KAN",
                "summary": "로그인 버그",
                "issue_type": "버그"
            }
        }
    """
    # LLM 사용
    if use_llm and _client:
        try:
            return _extract_with_llm(utter)
        except Exception as e:
            print(f"[LLM 실패] {e}, 룰베이스 폴백")
            return _extract_with_rules(utter)
    
    # 룰베이스 폴백
    return _extract_with_rules(utter)


def _extract_with_llm(utter: str) -> Dict[str, Any]:
    """LLM 기반 의도 추출 (내부 함수)"""
    prompt = build_intent_prompt(utter, include_types=True)
    
    resp = _client.chat.completions.create(
        model=CHAT_MODEL,
        response_format={"type": "json_object"},
        temperature=CHAT_TEMPERATURE,
        max_tokens=CHAT_MAX_TOKENS,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    raw = resp.choices[0].message.content
    data = json.loads(raw)
    
    # 기본값 설정
    data.setdefault("intent", "search")
    data.setdefault("slots", {})
    
    slots = data["slots"]
    
    # count 기본값
    if not slots.get("count"):
        slots["count"] = 5
    
    # ✅ 정규화 제거: NLU는 추출만, 정규화는 상위 레이어(graph.py)에서
    # LLM이 이미 available_types 힌트를 받았으므로 정확한 타입 반환
    # 불필요한 이중 LLM 호출 방지
    
    return data


def _extract_with_rules(utter: str) -> Dict[str, Any]:
    """룰베이스 의도 추출 (폴백)"""
    import re
    
    intent = "search"  # 기본값
    
    # 의도 감지
    if any(w in utter for w in ["생성", "만들", "추가", "create"]):
        intent = "create"
    elif any(w in utter for w in ["수정", "변경", "update", "edit"]):
        intent = "update"
    elif any(w in utter for w in ["삭제", "제거", "delete", "remove"]):
        intent = "delete"
    
    slots = {}
    
    # 프로젝트 키 추출
    m = re.search(r'\b([A-Z][A-Z0-9]{1,9})\b', utter)
    if m:
        slots["project_key"] = m.group(1)
    
    # 이슈 키 추출
    m = re.search(r'\b([A-Z][A-Z0-9]{1,9}-\d+)\b', utter)
    if m:
        slots["issue_key"] = m.group(1)
    
    # 개수 추출
    m = re.search(r'(\d+)\s*개', utter)
    if m:
        slots["count"] = int(m.group(1))
    else:
        slots["count"] = 5
    
    return {"intent": intent, "slots": slots}


def validate_slots(intent: str, slots: Dict[str, Any]) -> List[str]:
    """
    슬롯 유효성 검증 (필수 항목 체크)
    
    Args:
        intent: 의도
        slots: 슬롯 딕셔너리
    
    Returns:
        누락된 필수 슬롯 리스트
    
    예시:
        validate_slots("create", {"summary": "버그"})
        → ["project_key"]
    """
    missing = []
    
    if intent == "create":
        if not slots.get("project_key"):
            missing.append("project_key")
        if not slots.get("summary"):
            missing.append("summary")
    
    elif intent == "update":
        if not slots.get("issue_key"):
            missing.append("issue_key")
    
    elif intent == "delete":
        # issue_key 없으면 검색 조건 필요
        if not slots.get("issue_key"):
            if not (slots.get("summary") or slots.get("description") or slots.get("project_key")):
                missing.append("issue_key 또는 검색조건")
    
    return missing