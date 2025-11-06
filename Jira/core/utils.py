"""
공통 유틸
"""
from typing import Dict, Any


def remove_empty_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """빈 필드 제거"""
    return {k: v for k, v in data.items() if v is not None and v != ""}


def format_jira_issue(issue: Dict) -> Dict:
    """Jira 이슈 포맷"""
    fields = issue.get("fields", {})
    issue_key = issue.get("key", "")

    # 이슈 키에서 프로젝트 추출 (예: KAN-4 -> KAN)
    project = issue_key.split("-")[0] if "-" in issue_key else None

    # 설명 추출 (ADF 또는 텍스트)
    description = None
    if fields.get("description"):
        desc_raw = fields["description"]
        if isinstance(desc_raw, dict):
            # ADF 형식 - 간단히 텍스트 추출
            description = extract_text_from_adf(desc_raw)
        else:
            description = str(desc_raw)

    return {
        "key": issue_key,
        "summary": fields.get("summary"),
        "status": fields.get("status", {}).get("name"),
        "assignee": fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
        "project": project,
        "issuetype": fields.get("issuetype", {}).get("name"),
        "priority": fields.get("priority", {}).get("name") if fields.get("priority") else None,
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "duedate": fields.get("duedate"),
        "description": description,
    }


def extract_text_from_adf(adf: Dict) -> str:
    """ADF에서 텍스트 추출"""
    if not adf or not isinstance(adf, dict):
        return ""

    texts = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            if "content" in node:
                for child in node["content"]:
                    walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(adf)
    return " ".join(texts)[:200]  # 처음 200자만
