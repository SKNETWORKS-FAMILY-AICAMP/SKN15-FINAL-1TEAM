# -*- coding: utf-8 -*-
"""
utils.py - 공통 유틸리티

역할:
1. 이슈 타입 정규화 (한글 → 영문)
2. ADF ↔ 평문 변환
3. 에러 메시지 파싱
4. 라벨 자동 제안

연결:
- jira_client.py → norm_issue_type, adf_paragraph 사용
- milvus_client.py → adf_to_text로 설명 추출
- graph.py → suggest_labels로 자동 라벨
"""

from typing import Any, Dict, Optional, List
import requests

# ─────────────────────────────────────────────────────────
# 이슈 타입 정규화 (동적 매칭)
# ─────────────────────────────────────────────────────────

# 한글 ↔ 한글/영문 매핑 (양방향)
# Jira에 "버그", "에픽", "웹 가이드" 등 한글로 등록되어 있음
TYPE_ALIASES = {
    # 버그 관련
    "bug": "버그", "버그": "버그", "오류": "버그", "에러": "버그",
    
    # 에픽 관련
    "epic": "에픽", "에픽": "에픽",
    
    # 태스크 관련
    "task": "Task", "테스크": "Task", "작업": "Task", "태스크": "Task",
    
    # 스토리 관련
    "story": "Story", "스토리": "Story", "사용자스토리": "Story",
    
    # 서브태스크 관련
    "subtask": "Subtask", "하위작업": "Subtask", "하위태스크": "Subtask",
    "서브태스크": "Subtask", "서브작업": "Subtask",
    
    # 웹 가이드 관련
    "webguide": "웹 가이드", "웹가이드": "웹 가이드", "웹": "웹 가이드", 
    "가이드": "웹 가이드", "guide": "웹 가이드",
    
    # 에이전트 관련
    "agent": "에이전트", "에이전트": "에이전트", "봇": "에이전트", "챗봇": "에이전트",
    
    # PDF 분석 관련
    "pdf": "PDF분석", "pdf분석": "PDF분석", "피디에프": "PDF분석",
    "피디에프분석": "PDF분석", "문서분석": "PDF분석",
    
    # 미팅 관련
    "meeting": "미팅", "미팅": "미팅", "회의": "미팅", "논의": "미팅",
    
    # 프론트엔드 관련
    "frontend": "프론트엔드", "프론트엔드": "프론트엔드", "프론트": "프론트엔드",
    "fe": "프론트엔드", "front": "프론트엔드",
    
    # 백엔드 관련
    "backend": "백엔드", "백엔드": "백엔드", "백": "백엔드", 
    "be": "백엔드", "back": "백엔드",
}


def fuzzy_match_issue_type(user_input: str, available_types: List[str]) -> Optional[str]:
    """
    사용자 입력을 실제 Jira 이슈 타입과 퍼지 매칭
    
    매칭 전략:
    1. 별칭 테이블 조회
    2. 정확 매치
    3. 정규화 매치
    4. 부분 문자열 매치
    5. 편집 거리 (Levenshtein) 매칭
    
    Args:
        user_input: 사용자 입력
        available_types: Jira 실제 타입 리스트
    
    Returns:
        가장 유사한 타입 또는 None
    """
    if not user_input or not available_types:
        return None
    
    inp_original = user_input.strip()
    inp = inp_original.lower().replace(" ", "").replace("-", "").replace("_", "")
    
    # 1. 별칭 테이블 조회
    standard_name = TYPE_ALIASES.get(inp)
    if standard_name and standard_name in available_types:
        return standard_name
    
    # 2. 정확 매치 (원본 그대로)
    if inp_original in available_types:
        return inp_original
    
    # 3. 정규화 매치
    for atype in available_types:
        atype_norm = atype.lower().replace(" ", "").replace("-", "").replace("_", "")
        if inp == atype_norm:
            return atype
    
    # 4. 부분 매치 (포함 여부)
    best_match = None
    best_score = 0
    
    for atype in available_types:
        atype_norm = atype.lower().replace(" ", "").replace("-", "").replace("_", "")
        
        # 포함 관계
        if inp in atype_norm or atype_norm in inp:
            score = min(len(inp), len(atype_norm)) / max(len(inp), len(atype_norm))
            if score > best_score:
                best_score = score
                best_match = atype
    
    if best_score > 0.5:
        return best_match
    
    # 5. 편집 거리 (Levenshtein Distance)
    def levenshtein_distance(s1: str, s2: str) -> int:
        """문자열 간 편집 거리 계산"""
        if len(s1) < len(s2):
            return levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]
    
    # 편집 거리 기반 매칭
    for atype in available_types:
        atype_norm = atype.lower().replace(" ", "").replace("-", "").replace("_", "")
        
        distance = levenshtein_distance(inp, atype_norm)
        max_len = max(len(inp), len(atype_norm))
        
        # 유사도 = 1 - (거리 / 최대길이)
        similarity = 1 - (distance / max_len) if max_len > 0 else 0
        
        if similarity > best_score and similarity > 0.6:  # 60% 이상 유사
            best_score = similarity
            best_match = atype
    
    return best_match if best_score > 0.6 else None


def llm_match_issue_type(user_input: str, available_types: List[str]) -> Optional[str]:
    """
    LLM으로 이슈 타입 매칭 (퍼지 매칭 실패 시 사용)
    
    비용 최적화:
    - 편집 거리 실패 시에만 호출
    - 짧은 프롬프트 + 낮은 max_tokens
    - temperature=0 (결정론적)
    
    Args:
        user_input: 사용자 입력
        available_types: Jira 실제 타입 리스트
    
    Returns:
        매칭된 타입 또는 None
    
    예시:
        available = ["버그", "Code Review", "디자인 리뷰"]
        llm_match_issue_type("코드리뷰", available) → "Code Review"
    """
    # OpenAI 클라이언트 동적 import (선택적 기능)
    try:
        from openai import OpenAI
        from .config import OPENAI_API_KEY, CHAT_MODEL
        
        if not OPENAI_API_KEY:
            return None
        
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        return None
    
    # 프롬프트 (간결하게)
    types_str = ", ".join([f'"{t}"' for t in available_types])
    prompt = f"""사용자 입력: "{user_input}"
가능한 타입: {types_str}

가장 적합한 타입 하나만 반환. 없으면 "NONE".
타입명만 반환, 설명 금지."""
    
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=30
        )
        
        result = resp.choices[0].message.content.strip().strip('"')
        
        # 유효성 검증
        if result == "NONE" or result not in available_types:
            return None
        
        return result
    
    except Exception as e:
        print(f"[LLM 매칭 실패] {e}")
        return None


def norm_issue_type(s: Optional[str], 
                    available_types: Optional[List[str]] = None,
                    use_llm: bool = False) -> Optional[str]:
    """
    이슈 타입 정규화 (다단계 매칭)
    
    매칭 전략 (순서대로):
    1. 별칭 테이블 조회 (즉시)
    2. 퍼지 매칭 (편집 거리 포함)
    3. LLM 보조 (선택적, use_llm=True 시)
    4. 폴백 (원본 반환)
    
    Args:
        s: 사용자 입력
        available_types: Jira 실제 타입 리스트 (권장)
        use_llm: True면 퍼지 매칭 실패 시 LLM 사용 (비용 발생)
    
    Returns:
        정규화된 타입 또는 None
    
    예시:
        types = ["버그", "에픽", "웹 가이드", "Code Review"]
        
        # 별칭 테이블 (무료)
        norm_issue_type("bug", types) → "버그"
        
        # 편집 거리 (무료)
        norm_issue_type("웹가이드", types) → "웹 가이드"
        
        # LLM 보조 (비용 발생)
        norm_issue_type("코드리뷰", types, use_llm=True) → "Code Review"
    """
    if s is None:
        return None
    
    # 1. 타입 리스트가 있으면 퍼지 매칭 (권장)
    if available_types:
        match = fuzzy_match_issue_type(s, available_types)
        if match:
            return match
        
        # 2. LLM 보조 (선택적)
        if use_llm:
            llm_match = llm_match_issue_type(s, available_types)
            if llm_match:
                return llm_match
    
    # 3. 폴백: 별칭 테이블 직접 조회
    inp = s.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    standard = TYPE_ALIASES.get(inp)
    
    if standard:
        return standard
    
    # 4. 원본 그대로 반환
    return s.strip() if s.strip() else None


# ─────────────────────────────────────────────────────────
# 에러 메시지 파싱
# ─────────────────────────────────────────────────────────
def parse_err(r: requests.Response) -> str:
    """
    Jira API 에러를 읽기 쉽게 변환
    
    Args:
        r: requests.Response 객체
    
    Returns:
        정리된 에러 메시지
    
    예시:
        parse_err(response)
        → "project: Project is required | summary: Field is required"
    """
    try:
        j = r.json()
    except Exception:
        return f"{r.status_code} {r.text[:300]}"
    
    msgs = []
    
    # errorMessages 배열
    for m in (j.get("errorMessages") or []):
        msgs.append(m)
    
    # errors 딕셔너리
    for k, v in (j.get("errors") or {}).items():
        msgs.append(f"{k}: {v}")
    
    return " | ".join(msgs) if msgs else f"{r.status_code} {r.text[:300]}"


# ─────────────────────────────────────────────────────────
# ADF (Atlassian Document Format) 변환
# ─────────────────────────────────────────────────────────
def adf_paragraph(text: Optional[str]) -> Dict[str, Any]:
    """
    평문 → ADF 변환 (Jira description 필드용)
    
    Args:
        text: 평문 문자열
    
    Returns:
        ADF JSON 객체
    
    예시:
        adf_paragraph("버그 수정 필요")
        → {"type": "doc", "version": 1, "content": [...]}
    """
    if not text:
        return {
            "type": "doc",
            "version": 1,
            "content": []
        }
    
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": text
                    }
                ]
            }
        ]
    }


def adf_to_text(adf: Any, max_len: int = 5000) -> str:
    """
    ADF → 평문 변환 (임베딩/검색용)
    
    Args:
        adf: ADF dict 또는 문자열
        max_len: 최대 길이
    
    Returns:
        평문 문자열
    
    예시:
        adf = {"type": "doc", "content": [...]}
        adf_to_text(adf)
        → "버그 수정이 필요합니다.\n\n재현 단계: ..."
    """
    if not adf:
        return ""
    
    # 이미 문자열이면 그대로
    if isinstance(adf, str):
        return adf[:max_len] if len(adf) > max_len else adf
    
    # ADF가 아니면 빈 문자열
    if not isinstance(adf, dict) or adf.get("type") != "doc":
        return ""
    
    out = []
    
    def walk(node: Any):
        """재귀적으로 텍스트 수집"""
        if isinstance(node, dict):
            node_type = node.get("type")
            
            # 텍스트 노드
            if node_type == "text" and "text" in node:
                out.append(node["text"])
            
            # 자식 노드 탐색
            if "content" in node and isinstance(node["content"], list):
                for child in node["content"]:
                    walk(child)
            
            # 문단 뒤 개행
            if node_type in ("paragraph", "heading", "bulletList", "orderedList"):
                out.append("\n")
        
        elif isinstance(node, list):
            for item in node:
                walk(item)
    
    walk(adf)
    
    text = "".join(out).strip()
    
    # 최대 길이 제한
    if len(text) > max_len:
        text = text[:max_len] + " ..."
    
    return text


# ─────────────────────────────────────────────────────────
# 라벨 자동 제안
# ─────────────────────────────────────────────────────────
BASIC_KEYWORDS = {
    "bug": ["bug", "에러", "오류", "예외", "exception", "error", "fix", "hotfix"],
    "api": ["api", "endpoint", "rest", "graphql", "요청", "응답", "request", "response"],
    "ui": ["ui", "ux", "화면", "버튼", "컴포넌트", "component", "layout", "design"],
    "backend": ["서버", "server", "db", "database", "쿼리", "query", "service", "repository", "백엔드"],
    "frontend": ["프론트", "프론트엔드", "react", "vue", "angular", "클라이언트", "client"],
    "urgent": ["긴급", "급함", "hotfix", "critical", "🔥", "asap"],
    "test": ["테스트", "test", "unit", "integration", "e2e"],
    "docs": ["문서", "documentation", "readme", "가이드", "guide", "웹가이드"],
    "meeting": ["미팅", "회의", "meeting", "논의", "discussion"],
    "ai": ["에이전트", "agent", "llm", "gpt", "ai", "인공지능"],
    "pdf": ["pdf", "피디에프", "문서분석", "파일"],
}


def suggest_labels(summary: str, description: str = "") -> List[str]:
    """
    키워드 기반 라벨 자동 제안
    
    Args:
        summary: 이슈 제목
        description: 이슈 설명
    
    Returns:
        제안된 라벨 리스트 (정렬됨)
    
    예시:
        suggest_labels("로그인 API 버그 수정", "서버 응답 에러")
        → ["api", "backend", "bug"]
    """
    labs = set()
    
    # 제목 + 설명
    text = (summary or "") + " " + (description or "")
    text_lower = text.lower()
    
    # 키워드 매칭
    for label, keywords in BASIC_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            labs.add(label)
    
    return sorted(list(labs))


# ─────────────────────────────────────────────────────────
# 검색 결과 요약
# ─────────────────────────────────────────────────────────
def summarize_search_results(results: List[Dict[str, Any]]) -> str:
    """
    검색 결과를 한 줄 요약
    
    Args:
        results: 이슈 딕셔너리 리스트
    
    Returns:
        요약 문자열
    
    예시:
        summarize_search_results([...])
        → "유형 분포(Task:3, Bug:2), 대표 제목: 로그인 수정, API 개선"
    """
    if not results:
        return "검색 결과 없음"
    
    from collections import Counter
    
    # 이슈 타입 카운트
    types = Counter([r.get("issuetype") for r in results if r.get("issuetype")])
    top3_types = ", ".join([f"{k}:{v}" for k, v in types.most_common(3)])
    
    # 대표 제목
    titles = ", ".join([
        r.get("summary", "")[:30] 
        for r in results[:3]
    ])
    
    return f"유형 분포({top3_types}), 대표 제목: {titles}"