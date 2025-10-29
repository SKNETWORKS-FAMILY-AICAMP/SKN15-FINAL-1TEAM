# -*- coding: utf-8 -*-
"""
cli.py - 대화형 CLI

역할:
1. LangGraph 워크플로우 기반 대화형 인터페이스
2. Milvus 자동 동기화 (첫 실행 시)
3. 세션 컨텍스트 관리 (기본 프로젝트, 후보 선택 등)
4. Clarify 단계 슬롯 수집

사용:
- 채팅:   python -m src.cli chat
- 동기화: python -m src.cli sync --projects KAN HIN
- 카탈로그: python -m src.cli projects / types

개선점:
- FAISS 제거, Milvus 전용
- LangGraph 워크플로우 사용
- 자동 동기화 (첫 실행 시)
- 세션 컨텍스트 (기본 프로젝트, 부모 선택 등)
- Clarify 단계 슬롯 지시어 입력
- pick N으로 후보 선택
"""

import argparse
import json
import re
from typing import List, Dict, Any, Optional

from .graph import build_graph, get_store
from .milvus_client import sync_issues_to_milvus
from .utils import norm_issue_type
from .jira_client import list_projects as jl_projects, list_issue_types as jl_types


# ─────────────────────────────────────────────────────────
# 세션 컨텍스트: 기본 프로젝트/후보/최근 카드 상태
# ─────────────────────────────────────────────────────────
class SessionCtx:
    def __init__(self):
        self.default_project: Optional[str] = None
        self.last_card: Optional[Dict[str, Any]] = None
        self.last_action: Optional[str] = None   # "delete" | "update" | "create_parent"
        self.last_candidates: List[Dict[str, Any]] = []
        self.parent_key_picked: Optional[str] = None  # Subtask 부모 선택 결과


ctx = SessionCtx()


# ─────────────────────────────────────────────────────────
# 간단 슬롯 파서 (Clarify 단계에서 한 줄씩 입력받아 병합)
# ─────────────────────────────────────────────────────────
SLOT_PATTERNS = [
    ("summary", re.compile(r"^(?:summary|제목|요약)\s+(.*)$", re.I)),
    ("project_key", re.compile(r"^(?:project|프로젝트)\s+([A-Z][A-Z0-9]{1,9})$", re.I)),
    ("issue_type", re.compile(r"^(?:type|유형|이슈\s*유형)\s+(.*)$", re.I)),
    ("priority", re.compile(r"^(?:priority|우선순위)\s+(Highest|High|Medium|Low)$", re.I)),
    ("labels", re.compile(r"^(?:labels?|라벨)\s+(.+)$", re.I)),
    ("issue_key", re.compile(r"^(?:key|키)\s+([A-Z][A-Z0-9]{1,9}-\d+)$", re.I)),
    ("count", re.compile(r"^(?:count|갯수|개수)\s+(\d+)$", re.I)),
    ("description", re.compile(r"^(?:description|desc|설명)\s+(.*)$", re.I)),
]


def parse_slot_line(line: str) -> Dict[str, Any]:
    """슬롯 지시어 한 줄 파싱 → {'slot_name': value}"""
    line = line.strip()
    out: Dict[str, Any] = {}

    # 패턴 매칭 시도
    for name, pat in SLOT_PATTERNS:
        m = pat.match(line)
        if m:
            val = m.group(1).strip()
            if name == "labels":
                toks = re.split(r"[,\s]+", val)
                out[name] = [t for t in toks if t]
            elif name == "count":
                out[name] = int(val)
            elif name == "issue_type":
                out[name] = norm_issue_type(val)
            else:
                out[name] = val
            return out

    # 간단한 프로젝트 키 입력 (예: "TEST", "KAN")
    if re.match(r'^[A-Z][A-Z0-9]{1,9}$', line):
        return {"project_key": line}

    # 간단한 이슈 키 입력 (예: "KAN-123")
    if re.match(r'^[A-Z][A-Z0-9]{1,9}-\d+$', line):
        return {"issue_key": line}

    return {}


# ─────────────────────────────────────────────────────────
# Clarify 컨텍스트: 필요한 슬롯 모이면 실행
# ─────────────────────────────────────────────────────────
class Pending:
    def __init__(self):
        self.active = False
        self.intent: Optional[str] = None   # create/update/delete/search
        self.slots: Dict[str, Any] = {}
        self.need: List[str] = []
    
    def start(self, intent: str, slots: Dict[str, Any], need: List[str]):
        self.active = True
        self.intent = intent
        self.slots = dict(slots or {})
        self.need = list(need or [])
    
    def clear(self):
        self.__init__()  # reset
    
    def merge_slots(self, s: Dict[str, Any]):
        if not s:
            return
        for k, v in s.items():
            self.slots[k] = v
        # count 기본값
        if "count" not in self.slots:
            self.slots["count"] = 5
    
    def missing(self) -> List[str]:
        if not self.intent:
            return []
        
        req = []
        if self.intent == "create":
            for k in ("project_key", "summary"):
                if not self.slots.get(k):
                    req.append(k)
        elif self.intent == "update":
            if not self.slots.get("issue_key"):
                req.append("issue_key")
        elif self.intent == "delete":
            if not self.slots.get("issue_key"):
                req.append("issue_key 또는 검색조건")
        
        return req


# ─────────────────────────────────────────────────────────
# 보조: 카탈로그 출력
# ─────────────────────────────────────────────────────────
def pretty_projects(limit: int = 10):
    arr = jl_projects(limit=limit)
    out = [{"key": x.get("key"), "name": x.get("name")} for x in arr]
    print(json.dumps(out, ensure_ascii=False, indent=2))


def pretty_issue_types(limit: int = 10):
    arr = jl_types(limit=limit)
    out = [{"name": x.get("name"), "subtask": x.get("subtask")} for x in arr]
    print(json.dumps(out, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────────────────
# 동기화 커맨드
# ─────────────────────────────────────────────────────────
def do_sync(args):
    """Milvus 수동 동기화"""
    store = get_store()
    
    projects = args.projects
    if not projects:
        # 프로젝트 지정 안하면 접근 가능한 모든 프로젝트
        all_projects = jl_projects(limit=100)
        projects = [p.get("key") for p in all_projects if p.get("key")]
        print(f"[자동 감지] {len(projects)}개 프로젝트: {projects}")
    
    if not projects:
        print("[오류] 동기화할 프로젝트가 없습니다.")
        return
    
    full_sync = args.full
    count = sync_issues_to_milvus(projects, store, full_sync=full_sync)
    print(f"[동기화 완료] {count}개 이슈 색인됨")


# ─────────────────────────────────────────────────────────
# 메인: 채팅 모드
# ─────────────────────────────────────────────────────────
def do_chat(args):
    """대화형 CLI"""
    # LangGraph 워크플로우 빌드
    graph = build_graph()
    store = get_store()
    
    print("=== Jira Agent CLI (Milvus + LangGraph) ===")
    print("명령을 입력하세요. (예: 'KAN 프로젝트에 로그인 버그 검색')")
    print()
    print("특수 명령:")
    print("  /projects - 프로젝트 목록")
    print("  /types - 이슈 타입 목록")
    print("  /sync - Milvus 재동기화")
    print("  quit/exit - 종료")
    print()
    print("세션 컨텍스트:")
    print("  'KAN으로 하자', 'KAN으로 진행' - 기본 프로젝트 설정")
    print()
    print("Clarify 단계:")
    print("  'summary 로그인 에러', 'priority High', 'labels bug api' 등으로 슬롯 채우기")
    print()
    print("승인 카드:")
    print("  후보가 있으면 'pick N'으로 선택, 없으면 y/n으로 승인")
    print()
    
    # 첫 실행 확인 (자동 동기화는 graph의 entry 노드에서 처리)
    count = store.count()
    if count > 0:
        print(f"[Milvus 준비됨] {count}개 이슈 색인됨\n")
    else:
        print("[Milvus 초기화] 첫 대화 시 자동으로 동기화됩니다...\n")
    
    pending = Pending()
    
    while True:
        try:
            line = input("> ").strip()
            if not line:
                continue
            if line.lower() in ("quit", "exit"):
                break
            
            # ─────────────────────────────────────────
            # 0) 특수 명령
            # ─────────────────────────────────────────
            if line.startswith("/"):
                cmd = line[1:].lower().split()[0]
                
                if cmd == "projects":
                    pretty_projects(limit=20)
                    continue
                
                elif cmd == "types":
                    pretty_issue_types(limit=20)
                    continue
                
                elif cmd == "sync":
                    print("[수동 동기화] 프로젝트 자동 감지 중...")
                    all_projects = jl_projects(limit=100)
                    project_keys = [p.get("key") for p in all_projects if p.get("key")]
                    if project_keys:
                        count = sync_issues_to_milvus(project_keys, store, full_sync=True)
                        print(f"[완료] {count}개 이슈 색인됨")
                    else:
                        print("[경고] 접근 가능한 프로젝트가 없습니다.")
                    continue
                
                else:
                    print(f"[알 수 없는 명령] {cmd}")
                    continue
            
            # ─────────────────────────────────────────
            # 1) 부모(Subtask) 선택 후 요약 입력 처리
            # ─────────────────────────────────────────
            if ctx.parent_key_picked:
                sl = parse_slot_line(line)
                
                if "summary" in sl:
                    # 슬롯 지시어로 요약 입력
                    pj = ctx.default_project or sl.get("project_key", "")
                    sm = sl["summary"]
                    utter = f"{pj} 프로젝트에서 {ctx.parent_key_picked} 아래에 '{sm}' Subtask 생성"
                else:
                    # 일반 자연어 입력
                    pj = ctx.default_project or ""
                    utter = f"{pj} 프로젝트에서 {ctx.parent_key_picked} 아래에 '{line}' Subtask 생성"
                
                # 승인 카드 요청
                out = graph.invoke({"utter": utter, "approve": False})
                res = out.get("agent_output", {})
                stage = res.get("stage")
                
                if stage == "need_approve":
                    card = res.get("card", {})
                    print("\n[승인 카드]")
                    print(json.dumps(card, ensure_ascii=False, indent=2))
                    yn = input("실행할까요? (y/n): ").strip().lower()
                    
                    if yn == "y":
                        out = graph.invoke({"utter": utter, "approve": True})
                        res = out.get("agent_output", {})
                        print("\n[결과]")
                        print(json.dumps(res, ensure_ascii=False, indent=2))
                    else:
                        print("취소되었습니다.")
                else:
                    print("\n[결과]")
                    print(json.dumps(res, ensure_ascii=False, indent=2))
                
                # 부모 선택 상태 해제
                ctx.parent_key_picked = None
                continue
            
            # ─────────────────────────────────────────
            # 2) pick N - 후보 선택
            # ─────────────────────────────────────────
            m = re.match(r"^pick\s+(\d+)$", line.strip(), re.I)
            if m and ctx.last_candidates:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(ctx.last_candidates):
                    picked = ctx.last_candidates[idx]
                    key = picked.get("key")
                    
                    if ctx.last_action == "delete":
                        # 삭제 승인
                        utter = f"{key} 삭제"
                        out = graph.invoke({"utter": utter, "approve": True})
                        res = out.get("agent_output", {})
                        print("\n[결과]")
                        print(json.dumps(res, ensure_ascii=False, indent=2))
                        ctx.last_card = None
                        ctx.last_candidates = []
                        ctx.last_action = None
                    
                    elif ctx.last_action == "update":
                        # 수정할 키 지정 후 변경사항 입력 대기
                        print(f"[선택됨] {key}")
                        print("이제 수정할 내용을 입력하세요. (예: 'summary 새 제목', 'priority High')")
                        # pending 모드로 전환
                        pending.start("update", {"issue_key": key}, [])
                        ctx.last_card = None
                        ctx.last_candidates = []
                        ctx.last_action = None
                    
                    elif ctx.last_action == "create_parent":
                        # Subtask 부모 선택
                        ctx.parent_key_picked = key
                        print(f"[부모 선택됨] {key}")
                        print("이제 Subtask 요약을 입력하세요. (예: 'summary 로그인 폼 검증', 또는 자연어)")
                        ctx.last_card = None
                        ctx.last_candidates = []
                        ctx.last_action = None
                    
                    else:
                        print(f"[선택됨] {key}")
                        ctx.last_card = None
                        ctx.last_candidates = []
                        ctx.last_action = None
                else:
                    print(f"[오류] 범위를 벗어남: 1~{len(ctx.last_candidates)}")
                
                continue
            
            # ─────────────────────────────────────────
            # 3) 기본 프로젝트 설정
            # ─────────────────────────────────────────
            m = re.search(r'\b([A-Z][A-Z0-9]{1,9})\s*(?:으?로|로)\s*(?:하자|진행|설정)', line, re.I)
            if m:
                ctx.default_project = m.group(1)
                print(f"[세션] 기본 프로젝트: {ctx.default_project}")
                continue
            
            # ─────────────────────────────────────────
            # 4) Clarify/Pending 모드
            # ─────────────────────────────────────────
            if pending.active:
                # 취소
                if line.lower() in ("cancel", "취소", "그만"):
                    print("❌ 취소되었습니다.")
                    pending.clear()
                    continue

                # 슬롯 지시어 파싱 시도
                added = parse_slot_line(line)

                # 슬롯 지시어가 아니면 자연어로 NLU 파싱 시도
                if not added:
                    print("[자연어 파싱] NLU로 슬롯 추출 중...")
                    from .nlu import extract_intent_slots

                    parsed = extract_intent_slots(line, use_llm=True)
                    parsed_slots = parsed.get("slots", {})

                    # 기존 pending 슬롯과 병합
                    for k, v in parsed_slots.items():
                        if v and k not in pending.slots:
                            added[k] = v

                    if added:
                        print(f"[추출됨] {added}")

                # 기본 프로젝트 자동 주입
                if ctx.default_project and "project_key" not in pending.slots and "project_key" not in added:
                    if line.lower().startswith((
                        "summary", "제목", "요약",
                        "type", "유형",
                        "priority", "우선순위",
                        "labels", "라벨",
                        "key", "키",
                        "count", "개수", "갯수",
                        "description", "설명"
                    )):
                        added["project_key"] = ctx.default_project

                if added:
                    pending.merge_slots(added)
                    need = pending.missing()

                    if need:
                        print(f"[Clarify 진행중] 아직 필요한 슬롯: {need}")
                        print(f"💡 현재 입력된 슬롯: {pending.slots}")
                        continue

                    # 필수 충족 → 승인 카드 표시
                    utter = synthesize_utterance(pending.intent, pending.slots)
                    out = graph.invoke({"utter": utter, "approve": False})
                    res = out.get("agent_output", {})
                    stage = res.get("stage")

                    if stage == "need_approve":
                        card = res.get("card", {})
                        display_approval_card(card)

                        cands = card.get("candidates") or []
                        if cands:
                            ctx.last_card = card
                            ctx.last_candidates = cands
                            act = card.get("action")
                            ctx.last_action = "create_parent" if act == "create" else act

                            yn = input("> ").strip().lower()

                            # 숫자 입력 처리
                            if yn.isdigit():
                                yn = f"pick {yn}"

                            if yn.startswith("pick "):
                                idx = int(yn.split()[1]) - 1
                                if 0 <= idx < len(cands):
                                    selected = cands[idx]
                                    print(f"\n✅ 선택: {selected['key']} - {selected['summary']}")

                                    new_state = {
                                        "utter": f"{act} {selected['key']}",
                                        "approve": True,
                                        "intent": act,
                                        "slots": {"issue_key": selected["key"]}
                                    }
                                    out = graph.invoke(new_state)
                                    res = out.get("agent_output", {})
                                    display_result(res)
                                    pending.clear()
                                    continue

                            if yn == "n":
                                print("❌ 취소되었습니다.")
                                pending.clear()
                                continue

                        yn = input("\n실행할까요? (y/n): ").strip().lower()
                        if yn != "y":
                            print("❌ 취소되었습니다.")
                            pending.clear()
                            continue

                        out = graph.invoke({"utter": utter, "approve": True})
                        res = out.get("agent_output", {})

                    display_result(res)
                    pending.clear()
                    continue

                # 아무것도 추출 안됨
                print("⚠️ 입력을 이해하지 못했습니다. 다시 입력하거나 '취소'를 입력하세요.")
                continue
            
            # ─────────────────────────────────────────
            # 5) 일반 입력: LangGraph 실행
            # ─────────────────────────────────────────
            state = {"utter": line, "approve": False}
            out = graph.invoke(state)
            res = out.get("agent_output", {})
            stage = res.get("stage")
            
            if stage == "clarify":
                # Clarify 모드 시작
                intent_guess = guess_intent_from_hints(line)
                pending.start(intent_guess, {}, need=res.get("need") or [])
                
                print("\n[Clarify] 필요한 슬롯:", res.get("need"))
                print("  프로젝트 후보:", res.get("hint_projects"))
                print("  이슈유형 후보:", res.get("hint_types"))
                print()
                print("예시: 'project KAN', 'summary 로그인 에러', 'type Task', 'priority High', 'labels bug api'")
                continue
            
            if stage == "need_approve":
                card = res.get("card", {})

                # 간결한 승인 카드 표시
                display_approval_card(card)

                cands = card.get("candidates") or []
                if cands:
                    ctx.last_card = card
                    ctx.last_candidates = cands
                    act = card.get("action")
                    ctx.last_action = "create_parent" if act == "create" else act

                    yn = input("> ").strip().lower()

                    # 숫자만 입력한 경우 pick으로 처리
                    if yn.isdigit():
                        yn = f"pick {yn}"

                    # pick 처리
                    if yn.startswith("pick "):
                        idx = int(yn.split()[1]) - 1
                        if 0 <= idx < len(cands):
                            selected = cands[idx]
                            print(f"\n✅ 선택: {selected['key']} - {selected['summary']}")

                            # 재실행
                            new_state = {
                                "utter": f"{act} {selected['key']}",
                                "approve": True,
                                "intent": act,
                                "slots": {"issue_key": selected["key"]}
                            }
                            out = graph.invoke(new_state)
                            res = out.get("agent_output", {})
                            display_result(res)
                            continue
                        else:
                            print(f"❌ 잘못된 번호입니다. 1-{len(cands)} 사이로 입력하세요.")
                            continue

                    if yn == "n":
                        print("❌ 취소되었습니다.")
                        continue

                    # y 또는 기타 입력은 그냥 진행
                    out = graph.invoke({"utter": line, "approve": True})
                    res = out.get("agent_output", {})
                    display_result(res)
                    continue

                yn = input("\n실행할까요? (y/n): ").strip().lower()
                if yn != "y":
                    print("❌ 취소되었습니다.")
                    continue

                out = graph.invoke({"utter": line, "approve": True})
                res = out.get("agent_output", {})

            # 결과 출력
            display_result(res)
        
        except KeyboardInterrupt:
            print("\n종료합니다.")
            break
        
        except Exception as e:
            # 그래프/요청 에러가 나도 CLI가 죽지 않도록
            print(f"\n[에러] {e}")
            import traceback
            traceback.print_exc()
            continue


# ─────────────────────────────────────────────────────────
# 보조: 사용자 친화적 출력
# ─────────────────────────────────────────────────────────
def display_approval_card(card: Dict[str, Any]):
    """승인 카드를 읽기 쉽게 표시"""
    action = card.get("action", "").upper()
    target = card.get("target", {})
    project_key = target.get("project_key")
    issue_key = target.get("issue_key")

    # 헤더
    icon = "🗑️" if action == "DELETE" else ("✏️" if action == "UPDATE" else "➕")
    print(f"\n{icon} {action} 요청")
    print("=" * 60)

    # 타겟
    if issue_key:
        print(f"📌 대상: {issue_key}")
    elif project_key:
        print(f"📁 프로젝트: {project_key}")

    # 변경 사항
    changes = card.get("changes", {})
    if changes:
        print("\n📝 변경 내용:")
        for key, value in changes.items():
            if value:
                display_key = {
                    "issue_type": "타입",
                    "priority": "우선순위",
                    "assignee": "담당자",
                    "labels": "라벨",
                    "summary": "제목"
                }.get(key, key)
                print(f"  • {display_key}: {value}")

    # 요약/설명
    summary = card.get("summary")
    description = card.get("description")
    if summary:
        print(f"\n📄 제목: {summary}")
    if description:
        desc_preview = description[:100] + "..." if len(description) > 100 else description
        print(f"📋 설명: {desc_preview}")

    # 후보
    candidates = card.get("candidates", [])
    if candidates:
        print(f"\n🔍 후보 {len(candidates)}개:")
        for i, cand in enumerate(candidates, 1):
            score = cand.get("_rerank_score") or cand.get("_score")
            score_str = f" (점수: {score:.2f})" if score else ""
            print(f"  [{i}] {cand.get('key')}  {cand.get('summary')}{score_str}")
        print("\n💡 숫자를 입력하여 선택하거나, y/n으로 진행하세요.")

    print("=" * 60)


def display_result(result: Dict[str, Any]):
    """결과를 읽기 쉽게 표시"""
    stage = result.get("stage")
    intent = result.get("intent", "").upper()

    # 검색 결과
    if intent == "SEARCH":
        results = result.get("results", [])
        if results:
            print(f"\n🔍 검색 결과 ({len(results)}개)")
            print("=" * 60)
            for i, item in enumerate(results, 1):
                key = item.get("key")
                summary = item.get("summary")
                status = item.get("status")
                priority = item.get("priority")
                issuetype = item.get("issuetype")

                print(f"\n[{i}] {key}")
                print(f"  제목: {summary}")
                print(f"  상태: {status} | 타입: {issuetype} | 우선순위: {priority}")
            print("=" * 60)
        else:
            print("\n❌ 검색 결과가 없습니다.")
        return

    # 생성/수정/삭제 결과
    res = result.get("result", {})
    ok = res.get("ok")

    if ok:
        # 성공
        if intent == "CREATE":
            key = res.get("key")
            print(f"\n✅ 이슈 생성 성공: {key}")
            print(f"🔗 https://hinton.atlassian.net/browse/{key}")

        elif intent == "UPDATE":
            if res.get("noop"):
                print("\n⚠️ 변경 사항이 없습니다.")
            else:
                key = res.get("key") or result.get("intent_data", {}).get("issue_key")
                print(f"\n✅ 이슈 수정 성공: {key}")

        elif intent == "DELETE":
            key = res.get("key") or result.get("intent_data", {}).get("issue_key")
            print(f"\n✅ 이슈 삭제 성공: {key}")

    else:
        # 실패
        error = res.get("error", "Unknown")
        detail = res.get("detail", "")
        print(f"\n❌ {intent} 실패")
        print(f"오류: {error}")
        if detail:
            print(f"\n상세:")
            print(detail)


# ─────────────────────────────────────────────────────────
# 보조: 의도 추정 & 자연어 합성
# ─────────────────────────────────────────────────────────
def guess_intent_from_hints(text: str) -> str:
    """발화에서 의도 추정"""
    t = text.lower()
    if any(w in t for w in ["삭제", "지워", "delete", "remove", "정리"]):
        return "delete"
    if any(w in t for w in ["수정", "변경", "update", "edit"]):
        return "update"
    if any(w in t for w in ["검색", "찾아", "search", "show", "보여줘"]):
        return "search"
    return "create"


def synthesize_utterance(intent: str, slots: Dict[str, Any]) -> str:
    """슬롯을 가지고 자연어 한 줄 생성"""
    if intent == "create":
        pj = slots.get("project_key") or ctx.default_project or ""
        it = slots.get("issue_type", "Task") or "Task"
        sm = slots.get("summary", "")
        pr = slots.get("priority")
        lb = slots.get("labels")
        desc = slots.get("description")
        
        parts = [f"{pj} 프로젝트에 '{sm}' {it} 생성"]
        if pr:
            parts.append(f"우선순위 {pr}")
        if lb:
            parts.append(f"라벨 {' '.join(lb)}")
        if desc:
            parts.append(f"설명 {desc}")
        
        return ", ".join(parts)
    
    if intent == "update":
        k = slots.get("issue_key", "")
        sm = slots.get("summary")
        pr = slots.get("priority")
        it = slots.get("issue_type")
        lb = slots.get("labels")
        desc = slots.get("description")
        
        parts = [f"{k} 수정"]
        if sm:
            parts.append(f"summary {sm}")
        if pr:
            parts.append(f"priority {pr}")
        if it:
            parts.append(f"type {it}")
        if lb:
            parts.append(f"labels {' '.join(lb)}")
        if desc:
            parts.append(f"description {desc}")
        
        return ", ".join(parts)
    
    if intent == "delete":
        k = slots.get("issue_key")
        if k:
            return f"{k} 삭제"
        
        q = slots.get("summary") or "최근 테스트 이슈"
        pj = slots.get("project_key") or ctx.default_project or ""
        
        if pj:
            return f"{pj} 프로젝트에서 {q} 관련 이슈 삭제"
        return f"{q} 관련 이슈 삭제"
    
    # search
    q = slots.get("summary") or slots.get("description") or "최근 이슈"
    c = slots.get("count", 5)
    pj = slots.get("project_key") or ctx.default_project or ""
    
    if pj:
        return f"{pj} 프로젝트에서 {q} {c}개 검색"
    return f"{q} {c}개 검색"


# ─────────────────────────────────────────────────────────
# 엔트리포인트
# ─────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Jira Agent CLI (Milvus + LangGraph)")
    sub = ap.add_subparsers(dest="cmd")
    
    # 카탈로그: projects
    ap_projects = sub.add_parser("projects", help="프로젝트 목록 보기")
    ap_projects.add_argument("--limit", type=int, default=10, help="표시 개수")
    
    def cmd_projects(args):
        pretty_projects(limit=args.limit)
    
    ap_projects.set_defaults(func=cmd_projects)
    
    # 카탈로그: types
    ap_types = sub.add_parser("types", help="이슈 유형 목록 보기")
    ap_types.add_argument("--limit", type=int, default=10, help="표시 개수")
    
    def cmd_types(args):
        pretty_issue_types(limit=args.limit)
    
    ap_types.set_defaults(func=cmd_types)
    
    # 동기화
    ap_sync = sub.add_parser("sync", help="Milvus 동기화")
    ap_sync.add_argument(
        "--projects",
        nargs="+",
        help="프로젝트 키들 (예: KAN HIN). 생략 시 자동 감지"
    )
    ap_sync.add_argument(
        "--full",
        action="store_true",
        help="전체 동기화 (기존 데이터 삭제 후 재구축)"
    )
    ap_sync.set_defaults(func=do_sync)
    
    # 채팅
    ap_chat = sub.add_parser("chat", help="대화형 CLI")
    ap_chat.set_defaults(func=do_chat)
    
    args = ap.parse_args()
    
    if not hasattr(args, "func"):
        ap.print_help()
        return
    
    args.func(args)


if __name__ == "__main__":
    main()