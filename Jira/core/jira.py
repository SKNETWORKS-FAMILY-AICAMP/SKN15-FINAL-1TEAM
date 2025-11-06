"""
Jira REST API 래퍼
"""
import requests
from typing import List, Dict, Optional
from core.config import JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
from core.utils import format_jira_issue


class JiraClient:
    """Jira REST API 클라이언트"""

    def __init__(self):
        # base_url이 /로 끝나지 않으면 추가
        self.base_url = JIRA_BASE_URL if JIRA_BASE_URL.endswith('/') else f"{JIRA_BASE_URL}/"
        self.auth = (JIRA_EMAIL, JIRA_API_TOKEN)
        self.headers = {"Content-Type": "application/json"}

    def search_issues(self, jql: str, max_results: int = 50) -> List[Dict]:
        """이슈 검색"""
        url = f"{self.base_url}rest/api/3/search/jql"
        params = {
            "jql": jql,
            "maxResults": max_results,
            "fields": "summary,status,assignee,created,updated,issuetype,priority,duedate,description"
        }

        try:
            response = requests.get(url, auth=self.auth, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            issues = data.get("issues", [])

            return [format_jira_issue(issue) for issue in issues]

        except requests.RequestException as e:
            print(f"[Jira] 검색 오류: {e}")
            return []

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: Optional[str] = None,
        issuetype: str = "Task",
        assignee: Optional[str] = None,
        priority: Optional[str] = None,
        duedate: Optional[str] = None
    ) -> Dict:
        """
        이슈 생성

        Args:
            project_key: 프로젝트 키 (예: TEST, SKN, KAN)
            summary: 제목
            description: 설명 (선택)
            issuetype: 이슈 유형 (예: 작업, 버그, 스토리)
            assignee: 담당자 이메일 또는 displayName (선택)
            priority: 중요도 (예: High, Medium, Low) (선택)
            duedate: 마감 날짜 YYYY-MM-DD 형식 (선택)
        """
        url = f"{self.base_url}/rest/api/3/issue"

        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issuetype}
            }
        }

        if description:
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}]
                    }
                ]
            }

        if assignee:
            # 담당자 설정 (accountId 또는 displayName)
            payload["fields"]["assignee"] = {"displayName": assignee}

        if priority:
            payload["fields"]["priority"] = {"name": priority}

        if duedate:
            # 마감 날짜 (YYYY-MM-DD 형식)
            payload["fields"]["duedate"] = duedate

        try:
            response = requests.post(
                url,
                auth=self.auth,
                headers=self.headers,
                json=payload,
                timeout=10
            )

            if response.status_code == 201:
                data = response.json()
                return {"ok": True, "key": data.get("key")}
            else:
                return {"ok": False, "detail": response.text}

        except requests.RequestException as e:
            return {"ok": False, "detail": str(e)}

    def update_issue(self, issue_key: str, fields: Dict) -> Dict:
        """이슈 수정"""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"

        payload = {"fields": {}}

        if "summary" in fields:
            payload["fields"]["summary"] = fields["summary"]

        if "description" in fields:
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": fields["description"]}]
                    }
                ]
            }

        try:
            response = requests.put(
                url,
                auth=self.auth,
                headers=self.headers,
                json=payload,
                timeout=10
            )

            if response.status_code == 204:
                return {"ok": True, "key": issue_key}
            else:
                return {"ok": False, "detail": response.text}

        except requests.RequestException as e:
            return {"ok": False, "detail": str(e)}

    def delete_issue(self, issue_key: str) -> Dict:
        """이슈 삭제"""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"

        try:
            response = requests.delete(url, auth=self.auth, timeout=10)

            if response.status_code == 204:
                return {"ok": True, "key": issue_key}
            else:
                return {"ok": False, "detail": response.text}

        except requests.RequestException as e:
            return {"ok": False, "detail": str(e)}

    def get_projects(self) -> List[Dict]:
        """프로젝트 목록"""
        url = f"{self.base_url}/rest/api/3/project"

        try:
            response = requests.get(url, auth=self.auth, timeout=10)
            response.raise_for_status()

            projects = response.json()
            return [{"key": p.get("key"), "name": p.get("name")} for p in projects]

        except requests.RequestException as e:
            print(f"[Jira] 프로젝트 조회 오류: {e}")
            return []

    def get_issue_types(self):

        project_issue_map = {}
        start_at = 0
        max_results = 50  # 한 페이지에 50개씩 가져오기


        while True:
            # 1. /search 엔드포인트를 사용해 '페이지' 단위로 프로젝트를 가져옵니다.
            url = f"{JIRA_BASE_URL}/rest/api/3/project/search?startAt={start_at}&maxResults={max_results}&expand=issueTypes"    
            try:
                response = requests.get(url, auth=self.auth, timeout=10)
                response.raise_for_status()
                data = response.json()

                # 2. 현재 페이지의 프로젝트 목록(values)을 순회합니다.
                projects_on_page = data.get("values", [])
                if not projects_on_page:
                    break  # 더 이상 가져올 프로젝트가 없으면 중지

                for project in projects_on_page:
                    project_key = project.get('key')
                    
                    # 3. API 응답에 이미 포함된 'issueTypes'를 바로 사용합니다. (추가 API 호출 없음)
                    issue_types = [it.get('name') for it in project.get("issueTypes", []) if it.get('name')]
                    
                    project_issue_map[project_key] = issue_types

                # 4. 마지막 페이지인지 확인합니다.
                if data.get("isLast", True):
                    break  # 마지막 페이지면 루프 종료
                else:
                    # 5. 다음 페이지를 가져오기 위해 startAt을 업데이트합니다.
                    start_at += len(projects_on_page)

            except requests.RequestException as e:
                break  # 오류 발생 시 중지

        return project_issue_map

    def get_all_issue_types(self) -> List[str]:
        """모든 이슈 타입 목록"""
        url = f"{self.base_url}/rest/api/3/issuetype"

        try:
            response = requests.get(url, auth=self.auth, timeout=10)
            response.raise_for_status()

            data = response.json()
            return [it["name"] for it in data if not it.get("subtask", False)]

        except requests.RequestException as e:
            print(f"[Jira] 전체 이슈 타입 조회 오류: {e}")
            return ["Task", "Bug", "Story"]


# 전역 인스턴스
jira_client = JiraClient()
