# -*- coding: utf-8 -*-
"""
하이브리드 검색: 이슈 유형 필터 우선

실제 Jira 이슈 유형:
- 에픽, 백엔드, 작업, 버그, 에이전트, 프론트엔드, PDF분석, 하위 작업

동작:
1. "백엔드 이슈에서 로그인" → 백엔드만 검색
2. 결과 부족 → 전체 검색
3. 리랭킹 적용
"""

import re
from typing import List, Dict, Any, Optional


# ============================================================
# 실제 이슈 유형 매핑
# ============================================================

ISSUE_TYPE_MAP = {
    # 백엔드
    "백엔드": "백엔드",
    "be": "백엔드",
    "backend": "백엔드",
    "서버": "백엔드",
    "api": "백엔드",
    
    # 프론트엔드
    "프론트엔드": "프론트엔드",
    "프론트": "프론트엔드",
    "fe": "프론트엔드",
    "frontend": "프론트엔드",
    "ui": "프론트엔드",
    "화면": "프론트엔드",
    
    # 버그
    "버그": "버그",
    "bug": "버그",
    "오류": "버그",
    "에러": "버그",
    "error": "버그",
    
    # 작업
    "작업": "작업",
    "task": "작업",
    "태스크": "작업",
    
    # 에픽
    "에픽": "에픽",
    "epic": "에픽",
    
    # PDF분석
    "pdf": "PDF분석",
    "pdf분석": "PDF분석",
    "문서": "PDF분석",
    "document": "PDF분석",
    
    # 에이전트
    "에이전트": "에이전트",
    "agent": "에이전트",
    "봇": "에이전트",
    
    # 하위 작업
    "하위": "하위 작업",
    "하위작업": "하위 작업",
    "subtask": "하위 작업",
    "서브태스크": "하위 작업"
}


def detect_issue_type(query: str) -> Optional[str]:
    """
    쿼리에서 이슈 유형 감지
    
    예시:
        "백엔드 이슈에서 로그인" → "백엔드"
        "FE 작업 찾아줘" → "프론트엔드"
    """
    query_lower = query.lower()
    tokens = re.findall(r'[\w가-힣]+', query_lower)
    
    for token in tokens:
        if token in ISSUE_TYPE_MAP:
            return ISSUE_TYPE_MAP[token]
    
    return None


def extract_search_query(query: str, detected_type: Optional[str] = None) -> str:
    """
    검색 쿼리에서 이슈 유형 키워드 제거
    
    예시:
        "백엔드 이슈에서 로그인" → "로그인"
    """
    if not detected_type:
        return query
    
    # 제거할 키워드
    remove_keywords = []
    for kw, mapped_type in ISSUE_TYPE_MAP.items():
        if mapped_type == detected_type:
            remove_keywords.append(kw)
    
    remove_keywords.extend([
        "이슈", "에서", "찾아", "줘", "보여", "검색",
        "관련", "대한", "있는", "해줘"
    ])
    
    tokens = re.findall(r'[\w가-힣]+', query.lower())
    filtered = [t for t in tokens if t not in remove_keywords]
    
    return " ".join(filtered)


# ============================================================
# 리랭킹
# ============================================================

def extract_keywords(query: str) -> List[str]:
    """키워드 추출"""
    stopwords = {
        "이슈", "는", "은", "이", "가", "를", "을", "에", "의",
        "관련", "대한", "찾아", "검색", "보여", "해줘", "줘"
    }
    
    tokens = re.findall(r'[\w가-힣]+', query.lower())
    return [t for t in tokens if t not in stopwords and len(t) >= 2]


def calculate_keyword_score(query_keywords: List[str], result: Dict[str, Any]) -> float:
    """키워드 매칭 점수"""
    score = 0.0
    
    summary = (result.get("summary") or "").lower()
    issuetype = (result.get("issuetype") or "").lower()
    labels = [l.lower() for l in (result.get("labels") or [])]
    
    for kw in query_keywords:
        summary_words = re.findall(r'[\w가-힣]+', summary)
        if kw in summary_words:
            score += 0.6
        elif kw in summary:
            score += 0.4
        
        if kw in issuetype:
            score += 0.3
        
        for label in labels:
            if kw in label:
                score += 0.2
                break
    
    return score


def rerank_results(query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """리랭킹"""
    if not results:
        return results
    
    keywords = extract_keywords(query)
    if not keywords:
        return results
    
    for result in results:
        vector_score = result.get("_score", 0.0)
        keyword_score = calculate_keyword_score(keywords, result)
        final_score = vector_score * 0.5 + keyword_score * 0.5
        
        result["_rerank_score"] = final_score
        result["_keyword_score"] = keyword_score
        result["_vector_score"] = vector_score
    
    return sorted(results, key=lambda x: x["_rerank_score"], reverse=True)


# ============================================================
# 하이브리드 검색 메인
# ============================================================

def search_hybrid(store, query: str, topk: int = 5, 
                  min_filtered_results: int = 3) -> List[Dict[str, Any]]:
    """
    하이브리드 검색: 이슈 유형 필터 우선
    
    동작:
        1. 이슈 유형 감지 ("백엔드", "버그" 등)
        2. 감지되면 해당 유형만 검색
        3. 결과 부족하면 전체 검색
        4. 리랭킹 적용
    
    예시:
        "백엔드 이슈에서 로그인"
        → 백엔드만 검색 → 5개 이상이면 반환
        → 3개 미만이면 전체 검색
    """
    print(f"\n[검색] {query}")
    
    # 이슈 유형 감지
    detected_type = detect_issue_type(query)
    
    if detected_type:
        print(f"[유형 감지] '{detected_type}' 필터 적용")
        
        # 검색 쿼리 정제
        search_query = extract_search_query(query, detected_type)
        print(f"[검색어] '{search_query}'")
        
        # 필터링 검색
        all_results = store.search(
            query=search_query or query,
            topk=topk * 3
        )
        
        # 수동 필터링 (이슈 유형 체크)
        filtered = [
            r for r in all_results 
            if r.get("issuetype") == detected_type
        ]
        
        print(f"[필터 결과] {len(filtered)}개")
        
        # 충분하면 반환
        if len(filtered) >= min_filtered_results:
            print(f"[✅ 성공] {detected_type} 이슈만 반환")
            reranked = rerank_results(search_query or query, filtered)
            return reranked[:topk]
        else:
            print(f"[⚠️ 부족] 전체 검색 전환")
    else:
        print("[일반 검색]")
    
    # 전체 검색
    all_results = store.search(query, topk=topk * 3)
    print(f"[전체 결과] {len(all_results)}개")
    
    # 리랭킹
    reranked = rerank_results(query, all_results)
    return reranked[:topk]