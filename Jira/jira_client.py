"""Jira REST client wrapper used by the LangGraph CLI bot.

This module keeps the Jira specific logic in one place so that
`graph_cli.py` can stay focused on orchestration / ranking / CLI UX.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import logging
import requests

__all__ = [
    "Issue",
    "JiraClient",
    "JiraClientError",
    "adf_to_text",
    "normalize_issue_type",
    "to_adf",
]

logger = logging.getLogger(__name__)


KOREAN_ISSUE_TYPE_MAP = {
    "작업": "Task",
    "버그": "Bug",
    "스토리": "Story",
    "서브태스크": "Sub-task",
    "서브 태스크": "Sub-task",
    "에픽": "Epic",
    "스파이크": "Spike",
}


@dataclass
class Issue:
    """Minimal issue representation for ranking and rendering."""

    key: str
    summary: str
    description: Optional[str]
    status: Optional[str]
    priority: Optional[str]
    due_date: Optional[str]
    assignee: Optional[str]
    project: Optional[str]
    issue_type: Optional[str]
    raw: Dict[str, Any]

    def short_label(self) -> str:
        return f"{self.key} · {self.summary}" if self.summary else self.key


class JiraClientError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class JiraClient:
    """Thin wrapper around Jira REST API with opinionated helpers."""

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        timeout: Tuple[float, float] = (5.0, 20.0),
        session: Optional[requests.Session] = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not email:
            raise ValueError("email is required")
        if not api_token:
            raise ValueError("api_token is required")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.auth = (email, api_token)
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Public API

    def get_issue(self, issue_key: str, *, fields: Optional[Sequence[str]] = None) -> Issue:
        params = {"fields": ",".join(fields)} if fields else None
        data = self._request("GET", f"/rest/api/3/issue/{issue_key}", params=params)
        return self._parse_issue(data)

    def search_issues(
        self,
        *,
        project_key: Optional[str] = None,
        keyword: Optional[str] = None,
        jql: Optional[str] = None,
        limit: int = 50,
        fields: Optional[Sequence[str]] = None,
    ) -> List[Issue]:
        if limit <= 0:
            return []

        fields = list(fields) if fields else [
            "summary",
            "description",
            "status",
            "priority",
            "duedate",
            "assignee",
            "project",
            "issuetype",
        ]
        payload = {
            "jql": self._build_search_jql(project_key, keyword, jql),
            "maxResults": min(limit, 100),
            "fields": fields,
        }

        try:
            data = self._request("POST", "/rest/api/3/search", json=payload)
        except JiraClientError as exc:
            if exc.status_code == 405:
                params = {
                    "jql": payload["jql"],
                    "maxResults": payload["maxResults"],
                    "fields": ",".join(fields),
                }
                data = self._request("GET", "/rest/api/3/search", params=params)
            else:
                raise

        issues_payload = data.get("issues", []) if isinstance(data, dict) else []
        return [self._parse_issue(raw) for raw in issues_payload]

    def create_issue(
        self,
        *,
        project_key: str,
        project_id: Optional[str] = None,
        issue_type: str,
        summary: str,
        description: Optional[str] = None,
        use_adf: bool = False,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Issue:
        project_payload: Dict[str, Any]
        if project_id:
            project_payload = {"id": project_id}
        else:
            project_payload = {"key": project_key}

        fields: Dict[str, Any] = {
            "project": project_payload,
            "issuetype": {"name": normalize_issue_type(issue_type)},
            "summary": summary,
        }
        if description:
            fields["description"] = to_adf(description) if use_adf else description
        if extra_fields:
            fields.update(extra_fields)

        data = self._request("POST", "/rest/api/3/issue", json={"fields": fields})
        issue_key = data.get("key") if isinstance(data, dict) else None
        if not issue_key:
            raise JiraClientError("Failed to create issue: missing key in response", payload=data)
        return self.get_issue(issue_key)

    def update_issue(
        self,
        issue_key: str,
        *,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        due_date: Optional[str] = None,
        priority: Optional[str] = None,
        use_adf: bool = False,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Issue:
        fields: Dict[str, Any] = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = to_adf(description) if use_adf else description
        if due_date is not None:
            fields["duedate"] = due_date
        if priority is not None:
            fields["priority"] = {"name": priority}
        if extra_fields:
            fields.update(extra_fields)

        if not fields:
            raise ValueError("No fields provided for update")

        self._request("PUT", f"/rest/api/3/issue/{issue_key}", json={"fields": fields})
        return self.get_issue(issue_key)

    def delete_issue(self, issue_key: str) -> None:
        self._request("DELETE", f"/rest/api/3/issue/{issue_key}")

    def get_create_meta(self, project_key: str) -> Dict[str, Any]:
        params = {
            "projectKeys": project_key,
            "expand": "projects.issuetypes.fields",
        }
        return self._request("GET", "/rest/api/3/issue/createmeta", params=params)

    # ------------------------------------------------------------------
    # Internal helpers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise JiraClientError(f"Request failed: {exc}") from exc

        if response.status_code >= 400:
            message = self._pretty_err(response)
            raise JiraClientError(message, response.status_code, payload=_safe_json(response))

        if not response.content:
            return None

        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type:
            return response.json()
        return response.text

    def _build_search_jql(self, project_key: Optional[str], keyword: Optional[str], jql: Optional[str]) -> str:
        clauses: List[str] = []
        if project_key:
            clauses.append(f'project = "{project_key}"')
        if keyword:
            safe = keyword.replace("\"", "\\\"")
            clauses.append(f'(summary ~ "{safe}" OR description ~ "{safe}")')
        if jql:
            clauses.append(f"({jql})")

        query = " AND ".join(clauses)
        order = "ORDER BY updated DESC"
        return f"{query} {order}".strip() if query else order

    def _parse_issue(self, raw_issue: Dict[str, Any]) -> Issue:
        fields = raw_issue.get("fields", {}) if isinstance(raw_issue, dict) else {}
        description = fields.get("description")
        if isinstance(description, dict):
            description_text = adf_to_text(description)
        else:
            description_text = description

        status = self._safe_get(fields, "status", "name")
        priority = self._safe_get(fields, "priority", "name")
        assignee = self._safe_get(fields, "assignee", "displayName")
        project = self._safe_get(fields, "project", "key")
        issue_type = self._safe_get(fields, "issuetype", "name")

        return Issue(
            key=raw_issue.get("key"),
            summary=fields.get("summary"),
            description=description_text,
            status=status,
            priority=priority,
            due_date=fields.get("duedate"),
            assignee=assignee,
            project=project,
            issue_type=issue_type,
            raw=raw_issue,
        )

    @staticmethod
    def _safe_get(obj: Dict[str, Any], *path: str) -> Optional[str]:
        cur: Any = obj
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
        if isinstance(cur, str) or cur is None:
            return cur
        return str(cur)

    @staticmethod
    def _pretty_err(response: requests.Response) -> str:
        payload = _safe_json(response)
        if isinstance(payload, dict):
            messages: List[str] = []
            errors = payload.get("errorMessages")
            if isinstance(errors, Iterable):
                messages.extend(str(item) for item in errors)
            field_errors = payload.get("errors")
            if isinstance(field_errors, dict):
                messages.extend(f"{field}: {msg}" for field, msg in field_errors.items())
            if messages:
                joined = "; ".join(messages)
                return f"{response.status_code}: {joined}"
        text = response.text.strip()
        if text:
            return f"{response.status_code}: {text}"
        return f"{response.status_code}: {response.reason}"


# ----------------------------------------------------------------------
# ADF helpers


def to_adf(text: str) -> Dict[str, Any]:
    """Convert plain text into a simple Atlassian Document Format payload."""
    paragraphs = text.splitlines()
    content: List[Dict[str, Any]] = []
    for line in paragraphs:
        if not line:
            content.append({"type": "paragraph", "content": []})
            continue
        content.append(
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": line,
                    }
                ],
            }
        )
    if not content:
        content.append({"type": "paragraph", "content": []})
    return {"version": 1, "type": "doc", "content": content}


def adf_to_text(adf: Any) -> Optional[str]:
    """Convert a subset of ADF back to text for CLI rendering."""
    if not isinstance(adf, dict):
        return None
    if adf.get("type") != "doc":
        return None

    lines: List[str] = []

    def handle_nodes(nodes: Iterable[Dict[str, Any]], prefix: str = "") -> None:
        for node in nodes:
            node_type = node.get("type")
            if node_type == "paragraph":
                lines.append(prefix + _inline_text(node.get("content", [])))
            elif node_type == "bulletList":
                for item in node.get("content", []):
                    if not isinstance(item, dict):
                        continue
                    lines.append(prefix + "- " + _inline_text(item.get("content", [])))
                    sub = item.get("content", [])
                    handle_nodes([sub_node for sub_node in sub if isinstance(sub_node, dict)], prefix + "  ")
            elif node_type == "orderedList":
                index = 1
                for item in node.get("content", []):
                    if not isinstance(item, dict):
                        continue
                    lines.append(prefix + f"{index}. " + _inline_text(item.get("content", [])))
                    index += 1
            elif node_type == "heading":
                lines.append(prefix + _inline_text(node.get("content", [])))

    def _inline_text(items: Iterable[Dict[str, Any]]) -> str:
        buffer: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "text":
                buffer.append(str(item.get("text", "")))
            elif itype == "hardBreak":
                buffer.append("\n")
        return "".join(buffer).strip()

    handle_nodes(adf.get("content", []))
    return "\n".join(line for line in lines if line is not None)


def normalize_issue_type(issue_type: str) -> str:
    if not issue_type:
        raise ValueError("issue_type is required")
    normalized = issue_type.strip()
    return KOREAN_ISSUE_TYPE_MAP.get(normalized, normalized)


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None
