"""LangGraph based CLI orchestrating Jira CRUD bot with OpenAI + FAISS ranking."""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple, TypedDict

import faiss  # type: ignore
import numpy as np
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from jira_client import Issue, JiraClient, JiraClientError, normalize_issue_type

Intent = Literal["create", "update", "delete", "search", "explain", "chit_chat", "unknown"]


class BotState(TypedDict, total=False):
    messages: List[BaseMessage]
    user_input: str
    intent: Intent
    slots: Dict[str, Any]
    nlu_raw: Dict[str, Any]
    response: str
    pending: Dict[str, Any]


class NLUResult(BaseModel):
    intent: Intent = Field(description="Detected user intent for Jira workflow")
    project: Optional[str] = Field(default=None, description="Jira project key")
    issue_type: Optional[str] = Field(default=None, description="Issue type name")
    summary: Optional[str] = None
    description: Optional[str] = None
    issue_key: Optional[str] = Field(default=None, description="Existing issue key")
    due_date: Optional[str] = Field(default=None, description="Due date in YYYY-MM-DD")
    priority: Optional[str] = None
    keyword: Optional[str] = Field(default=None, description="Search keyword")
    keywords: Optional[List[str]] = Field(default=None, description="Search keywords")
    use_adf: Optional[bool] = Field(default=None, description="Whether to send description as ADF")

    class Config:
        extra = "allow"


@dataclass
class Services:
    jira: JiraClient
    chat_llm: ChatOpenAI
    explain_llm: ChatOpenAI
    chit_chat_llm: ChatOpenAI
    nlu_llm: Any
    embeddings: OpenAIEmbeddings


@dataclass
class ValidationResult:
    can_proceed: bool
    message: Optional[str] = None
    available_types: List[str] | None = None
    project_id: Optional[str] = None


NLU_SYSTEM_PROMPT = """당신은 Jira CLI 봇의 NLU 모듈입니다. 사용자의 발화를 기반으로 intent와 슬롯을 JSON 구조로 채워야 합니다.
가능한 intent는 create, update, delete, search, explain, chit_chat, unknown 입니다.
- 이슈 키는 대문자 프로젝트키-번호 형태(HINTON-123)
- 프로젝트 키, 이슈 유형, 요약, 설명, 마감일(YYYY-MM-DD), 우선순위, 키워드 등을 추출하세요.
- 사용자가 단순 인사나 잡담이면 intent를 chit_chat 으로 설정하세요.
- 확실하지 않으면 intent를 unknown 으로 설정하세요."""

ASSISTANT_SYSTEM_PROMPT = """당신은 Jira CRUD 작업을 돕는 한국어 챗봇입니다. 답변은 간결하지만 친절하게 작성하세요."""

EXPLAIN_SYSTEM_PROMPT = """당신은 Jira 이슈를 설명해주는 도우미입니다. 제공된 이슈 정보와 사용자 질의를 참고하여:
1) 왜 해당 이슈가 관련성이 높은지
2) 현재 상태와 핵심 요약
3) 추천 다음 액션
을 순서대로 bullet 없이 짧은 문단으로 설명하세요."""


def load_services() -> Services:
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")

    base_url = os.getenv("JIRA_BASE_URL")
    email = os.getenv("JIRA_EMAIL")
    token = os.getenv("JIRA_API_TOKEN")
    chat_model_name = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    embed_model_name = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

    missing = [name for name, value in [
        ("JIRA_BASE_URL", base_url),
        ("JIRA_EMAIL", email),
        ("JIRA_API_TOKEN", token),
        ("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY")),
    ] if not value]
    if missing:
        missing_vars = ", ".join(missing)
        print(f"환경변수 누락: {missing_vars}", file=sys.stderr)
        sys.exit(1)

    jira = JiraClient(base_url, email, token)
    chat_llm = ChatOpenAI(model=chat_model_name, temperature=0.2)
    explain_llm = ChatOpenAI(model=chat_model_name, temperature=0.3)
    chit_chat_llm = ChatOpenAI(model=chat_model_name, temperature=0.6)
    nlu_llm = chat_llm.with_structured_output(NLUResult, method="function_calling")
    embeddings = OpenAIEmbeddings(model=embed_model_name)

    return Services(
        jira=jira,
        chat_llm=chat_llm,
        explain_llm=explain_llm,
        chit_chat_llm=chit_chat_llm,
        nlu_llm=nlu_llm,
        embeddings=embeddings,
    )


def build_app(services: Services):
    graph = StateGraph(BotState)

    def node_nlu(state: BotState) -> Dict[str, Any]:
        messages = list(state.get("messages", []))
        nlu_messages: List[BaseMessage] = [SystemMessage(content=NLU_SYSTEM_PROMPT)] + messages
        result = services.nlu_llm.invoke(nlu_messages)
        slots = result.model_dump(exclude_none=True)
        intent = slots.pop("intent", "unknown")

        if "keywords" in slots and slots.get("keywords") and not slots.get("keyword"):
            slots["keyword"] = " ".join(slots["keywords"])

        pending = state.get("pending", {}) or {}
        if pending.get("awaiting_issue_key"):
            # Force router to stay on pending intent until issue key arrives
            intent = pending.get("intent", intent)
            merged_slots = dict(pending.get("slots", {}))
            merged_slots.update(slots)
            slots = merged_slots
        else:
            slots = {**(pending.get("slots", {})), **slots} if pending.get("intent") == intent else slots

        slots = _normalize_slots(slots)
        return {"intent": intent, "slots": slots, "nlu_raw": result.model_dump()}

    def router(state: BotState) -> str:
        intent = state.get("intent", "unknown")
        if intent in {"create", "update", "delete", "search", "explain", "chit_chat"}:
            return intent
        return "chit_chat"

    def node_create(state: BotState) -> Dict[str, Any]:
        slots = _normalize_slots(state.get("slots", {}))
        project = slots.get("project")
        issue_type = slots.get("issue_type")
        summary = slots.get("summary")
        description = slots.get("description")
        use_adf = bool(slots.get("use_adf"))

        missing: List[str] = []
        if not project:
            missing.append("프로젝트")
        if not issue_type:
            missing.append("이슈 유형")
        if not summary:
            missing.append("요약")
        if missing:
            text = "생성을 위해 " + ", ".join(missing) + " 값을 알려주세요."
            return _reply(state, text, pending={"intent": "create", "slots": slots})

        normalized_project = project.strip().upper()
        normalized_issue_type = normalize_issue_type(issue_type)
        slots["project"] = normalized_project
        slots["issue_type"] = normalized_issue_type

        validation = _validate_project_and_issue_type(
            services,
            normalized_project,
            normalized_issue_type,
        )
        if not validation.can_proceed:
            text = validation.message or "프로젝트/이슈 유형을 확인할 수 없습니다."
            available_types = validation.available_types or []
            if available_types:
                text += "\n사용 가능한 이슈 유형: " + ", ".join(sorted(available_types))
            return _reply(state, text, pending={"intent": "create", "slots": slots})
        warning_text = validation.message

        try:
            issue = services.jira.create_issue(
                project_key=normalized_project,
                project_id=validation.project_id,
                issue_type=normalized_issue_type,
                summary=summary,
                description=description,
                use_adf=use_adf,
            )
        except JiraClientError as exc:
            text = f"❌ 이슈 생성 실패: {exc}"
            return _reply(state, text, pending={"intent": "create", "slots": slots})

        lines: List[str] = []
        if warning_text:
            lines.append(f"⚠️ {warning_text}")
        lines.extend(
            [
                "✅ 이슈가 생성되었습니다:",
                f"- 키: {issue.key}",
                f"- 프로젝트: {issue.project}",
                f"- 유형: {issue.issue_type}",
                f"- 요약: {issue.summary}",
            ]
        )
        return _reply(state, "\n".join(lines), pending={})

    def node_update(state: BotState) -> Dict[str, Any]:
        slots = _normalize_slots(dict(state.get("slots", {})))
        pending = state.get("pending", {}) or {}
        issue_key = slots.get("issue_key")

        fields_to_update = {
            key: slots.get(key)
            for key in ("summary", "description", "due_date", "priority")
            if slots.get(key) is not None
        }
        use_adf = bool(slots.get("use_adf"))

        if not issue_key:
            project = slots.get("project")
            keyword = _keyword_from_slots(slots)
            if not project:
                return _reply(
                    state,
                    "수정 후보를 찾으려면 프로젝트 채널이름이 필요합니다 (예: 프로젝트=HINTON).",
                    pending={"intent": "update", "slots": slots},
                )
            try:
                candidates = _collect_candidates(services, project, keyword, limit=60)
            except JiraClientError as exc:
                return _reply(
                    state,
                    f"❌ 이슈 조회 실패: {exc}",
                    pending={"intent": "update", "slots": slots},
                )
            ranked = _faiss_topk(services, state.get("user_input", keyword or ""), candidates, k=5)
            if not ranked:
                return _reply(
                    state,
                    "조건에 맞는 이슈를 찾지 못했습니다.",
                    pending={},
                )
            text = _format_issue_list("[수정 후보 Top-5]", ranked)
            new_pending = {
                "intent": "update",
                "slots": slots,
                "candidate_keys": [issue.key for issue in ranked],
                "awaiting_issue_key": True,
            }
            return _reply(state, text + "\n키=HINTON-123 형태로 수정할 이슈를 알려주세요.", pending=new_pending)

        if not fields_to_update:
            reminder = "수정할 필드를 알려주세요 (요약/설명/마감/우선순위 등)."
            return _reply(
                state,
                reminder,
                pending={"intent": "update", "slots": slots, "awaiting_issue_key": False},
            )

        try:
            updated = services.jira.update_issue(
                issue_key,
                summary=fields_to_update.get("summary"),
                description=fields_to_update.get("description"),
                due_date=fields_to_update.get("due_date"),
                priority=fields_to_update.get("priority"),
                use_adf=use_adf,
            )
        except JiraClientError as exc:
            return _reply(
                state,
                f"❌ 이슈 수정 실패: {exc}",
                pending={"intent": "update", "slots": slots},
            )

        text = "✅ 이슈가 수정되었습니다:\n" + _format_issue_summary(updated)
        return _reply(state, text, pending={})

    def node_delete(state: BotState) -> Dict[str, Any]:
        slots = _normalize_slots(dict(state.get("slots", {})))
        issue_key = slots.get("issue_key")

        if not issue_key:
            project = slots.get("project")
            keyword = _keyword_from_slots(slots)
            if not project:
                return _reply(
                    state,
                    "삭제 후보를 찾으려면 프로젝트 채널이름이 필요합니다.",
                    pending={"intent": "delete", "slots": slots},
                )
            try:
                candidates = _collect_candidates(services, project, keyword, limit=60)
            except JiraClientError as exc:
                return _reply(
                    state,
                    f"❌ 이슈 조회 실패: {exc}",
                    pending={"intent": "delete", "slots": slots},
                )
            ranked = _faiss_topk(services, state.get("user_input", keyword or ""), candidates, k=5)
            if not ranked:
                return _reply(
                    state,
                    "조건에 맞는 이슈가 없습니다.",
                    pending={},
                )
            text = _format_issue_list("[삭제 후보 Top-5]", ranked)
            pending = {
                "intent": "delete",
                "slots": slots,
                "candidate_keys": [issue.key for issue in ranked],
                "awaiting_issue_key": True,
            }
            return _reply(state, text + "\n키=HINTON-123 형태로 삭제할 이슈를 확정해주세요.", pending=pending)

        try:
            services.jira.delete_issue(issue_key)
        except JiraClientError as exc:
            return _reply(
                state,
                f"❌ 이슈 삭제 실패: {exc}",
                pending={"intent": "delete", "slots": slots},
            )

        return _reply(state, f"🗑️ {issue_key} 이슈를 삭제했습니다.", pending={})

    def node_search(state: BotState) -> Dict[str, Any]:
        slots = _normalize_slots(state.get("slots", {}))
        project = slots.get("project")
        keyword = _keyword_from_slots(slots)
        if not project:
            return _reply(
                state,
                "검색하려면 프로젝트 채널이름이 필요합니다.",
                pending={"intent": "search", "slots": slots},
            )
        try:
            candidates = _collect_candidates(services, project, keyword, limit=60)
        except JiraClientError as exc:
            return _reply(
                state,
                f"❌ 이슈 검색 실패: {exc}",
                pending={"intent": "search", "slots": slots},
            )
        ranked = _faiss_topk(services, state.get("user_input", keyword or ""), candidates, k=5)
        if not ranked:
            return _reply(state, "조건에 맞는 이슈가 없습니다.", pending={})
        text = _format_issue_list("[검색 Top-5]", ranked)
        return _reply(state, text, pending={})

    def node_explain(state: BotState) -> Dict[str, Any]:
        slots = _normalize_slots(state.get("slots", {}))
        project = slots.get("project")
        keyword = _keyword_from_slots(slots)
        try:
            candidates = _collect_candidates(services, project, keyword, limit=60)
        except JiraClientError as exc:
            return _reply(
                state,
                f"❌ 이슈 검색 실패: {exc}",
                pending={},
            )
        ranked = _faiss_topk(services, state.get("user_input", keyword or ""), candidates, k=1)
        if not ranked:
            return _reply(state, "관련 이슈를 찾지 못했습니다.", pending={})
        target = ranked[0]
        try:
            detailed = services.jira.get_issue(target.key)
        except JiraClientError:
            detailed = target

        explain_prompt = _build_explain_prompt(detailed, state.get("user_input", ""))
        messages = [SystemMessage(content=EXPLAIN_SYSTEM_PROMPT), HumanMessage(content=explain_prompt)]
        answer = services.explain_llm.invoke(messages)
        text = "[가장 관련 높은 이슈]\n" + _format_issue_summary(detailed) + "\n\n" + answer.content.strip()
        return _reply(state, text, pending={})

    def node_chit_chat(state: BotState) -> Dict[str, Any]:
        messages = list(state.get("messages", []))
        convo = [SystemMessage(content=ASSISTANT_SYSTEM_PROMPT)] + messages
        response = services.chit_chat_llm.invoke(convo)
        return _reply(state, response.content.strip(), pending={})

    graph.add_node("nlu", node_nlu)
    graph.add_node("create", node_create)
    graph.add_node("update", node_update)
    graph.add_node("delete", node_delete)
    graph.add_node("search", node_search)
    graph.add_node("explain", node_explain)
    graph.add_node("chit_chat", node_chit_chat)

    graph.set_entry_point("nlu")
    graph.add_conditional_edges(
        "nlu",
        router,
        {
            "create": "create",
            "update": "update",
            "delete": "delete",
            "search": "search",
            "explain": "explain",
            "chit_chat": "chit_chat",
        },
    )
    graph.add_edge("create", END)
    graph.add_edge("update", END)
    graph.add_edge("delete", END)
    graph.add_edge("search", END)
    graph.add_edge("explain", END)
    graph.add_edge("chit_chat", END)

    return graph.compile()


def _collect_candidates(services: Services, project: Optional[str], keyword: Optional[str], *, limit: int = 60) -> List[Issue]:
    return services.jira.search_issues(project_key=project, keyword=keyword, limit=limit)


def _keyword_from_slots(slots: Dict[str, Any]) -> Optional[str]:
    keyword = slots.get("keyword")
    if not keyword and slots.get("keywords"):
        keyword = " ".join(slots["keywords"])
    if isinstance(keyword, str):
        return keyword
    return None


def _validate_project_and_issue_type(
    services: Services,
    project_key: str,
    issue_type: str,
) -> ValidationResult:
    try:
        meta = services.jira.get_create_meta(project_key)
    except JiraClientError as exc:
        return ValidationResult(
            can_proceed=True,
            message=f"프로젝트 정보를 가져오지 못했습니다: {exc}",
            available_types=[],
            project_id=None,
        )

    projects = meta.get("projects") if isinstance(meta, dict) else None
    if not projects:
        return ValidationResult(
            can_proceed=True,
            message=(
                "프로젝트 채널 이름을 확인할 수 없어 Jira 응답 기준으로 계속 시도합니다. "
                "필요하면 프로젝트 채널 이름 또는 권한을 확인해주세요."
            ),
            available_types=[],
            project_id=None,
        )

    project_meta = None
    for item in projects:
        if isinstance(item, dict) and item.get("key") == project_key:
            project_meta = item
            break
    if not project_meta:
        return ValidationResult(
            can_proceed=True,
            message=(
                "프로젝트 채널 이름 정보를 확인할 수 없어 Jira 응답 기준으로 계속 시도합니다. "
                "필요하면 프로젝트 채널 이름 또는 권한을 확인해주세요."
            ),
            available_types=[],
            project_id=None,
        )

    issuetypes = project_meta.get("issuetypes")
    available_types = [it.get("name") for it in issuetypes or [] if isinstance(it, dict) and it.get("name")]

    if available_types and issue_type not in available_types:
        return ValidationResult(
            can_proceed=False,
            message=f'"{issue_type}" 유형은 프로젝트 {project_key}에서 사용할 수 없습니다.',
            available_types=available_types,
            project_id=project_meta.get("id") if isinstance(project_meta, dict) else None,
        )

    return ValidationResult(
        can_proceed=True,
        message=None,
        available_types=available_types,
        project_id=project_meta.get("id") if isinstance(project_meta, dict) else None,
    )


def _normalize_slots(slots: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(slots, dict):
        return {}
    normalized = dict(slots)
    alias_map = {
        "project": ["project_key", "projectKey"],
        "issue_type": ["type", "issueType"],
        "issue_key": ["key", "issueKey"],
        "due_date": ["due", "dueDate"],
        "priority": ["priority_name", "priorityName"],
        "summary": ["title"],
        "description": ["details", "body"],
        "keyword": ["query", "search_term", "searchTerm"],
    }
    for target, aliases in alias_map.items():
        if normalized.get(target):
            continue
        for alias in aliases:
            value = slots.get(alias)
            if value:
                normalized[target] = value
                break

    listable = ["project", "issue_type", "issue_key", "priority", "summary", "description", "keyword", "due_date"]
    for key in listable:
        value = normalized.get(key)
        if isinstance(value, list):
            normalized[key] = " ".join(str(item) for item in value)
    return normalized


def _faiss_topk(
    services: Services,
    query: str,
    candidates: Sequence[Issue],
    k: int = 5,
) -> List[Issue]:
    if not candidates:
        return []
    texts = [f"{issue.key} :: {issue.summary or ''}" for issue in candidates]
    vectors = services.embeddings.embed_documents(texts)
    if not vectors:
        return list(candidates)[:k]
    matrix = np.array(vectors, dtype="float32")
    faiss.normalize_L2(matrix)

    query_text = query or ""
    query_vec = np.array([services.embeddings.embed_query(query_text)], dtype="float32")
    faiss.normalize_L2(query_vec)

    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)
    top_k = min(k, len(candidates))
    scores, indices = index.search(query_vec, top_k)

    ranking: List[Tuple[Issue, float]] = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        ranking.append((candidates[idx], float(score)))

    ranking.sort(key=lambda item: item[1], reverse=True)
    return [issue for issue, _ in ranking]


def _format_issue_list(title: str, issues: Sequence[Issue]) -> str:
    lines = [title]
    for idx, issue in enumerate(issues, start=1):
        summary = (issue.summary or "(요약 없음)").replace("\n", " ")
        status = issue.status or "None"
        priority = issue.priority or "None"
        due = issue.due_date or "None"
        lines.append(f"{idx}. {issue.key} · {summary} · 상태:{status} · 우선:{priority} · 마감:{due}")
    return "\n".join(lines)


def _format_issue_summary(issue: Issue) -> str:
    summary = (issue.summary or "(요약 없음)").replace("\n", " ")
    status = issue.status or "None"
    priority = issue.priority or "None"
    due = issue.due_date or "None"
    assignee = issue.assignee or "None"
    lines = [
        f"- {issue.key} · {summary}",
        f"- 상태:{status} · 우선:{priority} · 마감:{due}",
        f"- 담당:{assignee}",
    ]
    return "\n".join(lines)


def _build_explain_prompt(issue: Issue, user_query: str) -> str:
    description = issue.description or "(설명 없음)"
    template = textwrap.dedent(
        f"""
        사용자 요청: {user_query}
        이슈 키: {issue.key}
        프로젝트: {issue.project}
        이슈 유형: {issue.issue_type}
        요약: {issue.summary}
        상태: {issue.status}
        우선순위: {issue.priority}
        마감일: {issue.due_date}
        담당자: {issue.assignee}
        설명: {description}
        """
    ).strip()
    return template


def _reply(state: BotState, text: str, *, pending: Dict[str, Any]) -> Dict[str, Any]:
    messages = list(state.get("messages", []))
    messages.append(AIMessage(content=text))
    return {"messages": messages, "response": text, "pending": pending}


def run_cli() -> None:
    services = load_services()
    app = build_app(services)

    print("Jira CRUD 봇 (LangGraph + OpenAI) - CLI")
    print("종료: Ctrl+C")

    history: List[BaseMessage] = []
    pending: Dict[str, Any] = {}

    while True:
        try:
            user_text = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\n안녕히 가세요!")
            break
        user_text = user_text.strip()
        if not user_text:
            continue

        human_msg = HumanMessage(content=user_text)
        state: BotState = {
            "messages": history + [human_msg],
            "user_input": user_text,
            "pending": pending,
        }
        result = app.invoke(state)
        response = result.get("response", "")
        if response:
            print(response)
        history = result.get("messages", history + [human_msg])
        pending = result.get("pending", {})


if __name__ == "__main__":
    run_cli()
