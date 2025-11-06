#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Milvus Client - 벡터 DB 관리

역할:
1. Milvus 컬렉션 생성
2. 이슈 데이터 임베딩 및 저장 (UPSERT)
3. 하이브리드 검색 (벡터 + 메타데이터 필터)
"""

from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility
)
from openai import OpenAI
from typing import List, Dict, Optional
from core.config import (
    MILVUS_HOST,
    MILVUS_PORT,
    MILVUS_COLLECTION,
    OPENAI_API_KEY,
    EMBED_MODEL,
    EMBED_DIM
)


class MilvusClient:
    """Milvus 벡터 DB 클라이언트"""

    def __init__(self):
        """초기화 및 연결"""
        self.collection_name = MILVUS_COLLECTION
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.connect()

        # 컬렉션이 없으면 자동 생성
        if not utility.has_collection(self.collection_name):
            print(f"ℹ️  컬렉션이 존재하지 않아 생성합니다: {self.collection_name}")
            self.create_collection()
        else:
            print(f"ℹ️  기존 컬렉션 사용: {self.collection_name}")

    def connect(self):
        """Milvus 서버 연결"""
        try:
            connections.connect(
                alias="default",
                host=MILVUS_HOST,
                port=MILVUS_PORT
            )
            print(f"✅ Milvus 연결 성공: {MILVUS_HOST}:{MILVUS_PORT}")
        except Exception as e:
            print(f"❌ Milvus 연결 실패: {e}")
            raise

    def create_collection(self, drop_existing: bool = False):
        """
        컬렉션 생성

        Args:
            drop_existing: 기존 컬렉션 삭제 여부
        """
        # 기존 컬렉션 삭제
        if drop_existing and utility.has_collection(self.collection_name):
            utility.drop_collection(self.collection_name)
            print(f"🗑️  기존 컬렉션 삭제: {self.collection_name}")

        # 이미 존재하는 경우
        if utility.has_collection(self.collection_name):
            print(f"ℹ️  컬렉션이 이미 존재합니다: {self.collection_name}")
            return Collection(self.collection_name)

        # 필드 정의
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="issue_key", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="project_key", dtype=DataType.VARCHAR, max_length=20),
            FieldSchema(name="issue_type", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=2000),
            FieldSchema(name="assignee", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="priority", dtype=DataType.VARCHAR, max_length=20),
            FieldSchema(name="status", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="duedate", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="created", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="updated", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBED_DIM)
        ]

        # 스키마 생성
        schema = CollectionSchema(
            fields=fields,
            description="Jira Issues Collection",
            enable_dynamic_field=False
        )

        # 컬렉션 생성
        collection = Collection(
            name=self.collection_name,
            schema=schema
        )

        # 인덱스 생성 (벡터 검색 최적화)
        index_params = {
            "metric_type": "L2",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128}
        }
        collection.create_index(
            field_name="embedding",
            index_params=index_params
        )

        print(f"✅ 컬렉션 생성 완료: {self.collection_name}")
        print(f"   - 필드 수: {len(fields)}")
        print(f"   - 임베딩 차원: {EMBED_DIM}")

        return collection

    def get_embedding(self, text: str) -> List[float]:
        """
        텍스트를 임베딩으로 변환

        Args:
            text: 임베딩할 텍스트

        Returns:
            임베딩 벡터 (1536 차원)
        """
        try:
            response = self.openai_client.embeddings.create(
                model=EMBED_MODEL,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"❌ 임베딩 생성 실패: {e}")
            return [0.0] * EMBED_DIM

    def prepare_embedding_text(self, issue: Dict) -> str:
        """
        이슈 데이터를 임베딩용 텍스트로 변환

        규칙: project_key | summary | description

        Args:
            issue: 이슈 데이터

        Returns:
            임베딩용 텍스트
        """
        project_key = issue.get("project", "") or ""
        summary = issue.get("summary", "") or ""
        description = issue.get("description", "") or ""

        return f"{project_key} | {summary} | {description}"

    def upsert_issues(self, issues: List[Dict]) -> bool:
        """
        이슈 데이터를 Milvus에 저장 (UPSERT)

        Args:
            issues: 이슈 데이터 리스트

        Returns:
            성공 여부
        """
        if not issues:
            print("⚠️  저장할 이슈가 없습니다.")
            return False

        try:
            collection = Collection(self.collection_name)
            collection.load()  # 컬렉션을 메모리에 로드

            # 기존 데이터 삭제 (issue_key 기반)
            issue_keys = [issue.get("key") for issue in issues]
            expr = f"issue_key in {issue_keys}"

            try:
                collection.delete(expr)
            except Exception as e:
                print(f"[WARN] 기존 데이터 삭제 실패 (무시): {e}")
                pass  # 데이터가 없으면 무시

            # 데이터 준비
            data = []
            for issue in issues:
                # 임베딩 텍스트 생성
                embed_text = self.prepare_embedding_text(issue)
                embedding = self.get_embedding(embed_text)

                data.append({
                    "issue_key": issue.get("key", "")[:50],
                    "project_key": (issue.get("project") or "")[:20],
                    "issue_type": (issue.get("issuetype") or "")[:50],
                    "summary": (issue.get("summary") or "")[:500],
                    "description": (issue.get("description") or "")[:2000],
                    "assignee": (issue.get("assignee") or "")[:100],
                    "priority": (issue.get("priority") or "NaN")[:20],
                    "status": (issue.get("status") or "")[:50],
                    "duedate": (issue.get("duedate") or "NaN")[:50],
                    "created": (issue.get("created") or "")[:50],
                    "updated": (issue.get("updated") or "")[:50],
                    "embedding": embedding
                })

            # 삽입
            collection.insert(data)
            collection.flush()

            print(f"✅ {len(issues)}개 이슈 저장 완료")
            return True

        except Exception as e:
            print(f"❌ 이슈 저장 실패: {e}")
            return False

    def delete_by_issue_key(self, issue_key: str) -> bool:
        """
        issue_key로 이슈 삭제

        Args:
            issue_key: 삭제할 이슈 키 (예: KAN-5)

        Returns:
            성공 여부
        """
        try:
            collection = Collection(self.collection_name)
            collection.load()

            # 삭제 표현식
            expr = f'issue_key == "{issue_key}"'

            # 삭제 실행
            collection.delete(expr)
            collection.flush()

            print(f"✅ 이슈 삭제 완료: {issue_key}")
            return True

        except Exception as e:
            print(f"❌ 이슈 삭제 실패: {e}")
            return False

    def search(
        self,
        query_text: str,
        filter_expr: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict]:
        """
        하이브리드 검색 (벡터 + 메타데이터 필터)

        Args:
            query_text: 검색 쿼리
            filter_expr: 메타데이터 필터 표현식
            limit: 결과 개수

        Returns:
            검색 결과 리스트
        """
        try:
            collection = Collection(self.collection_name)
            collection.load()

            # 쿼리 임베딩
            query_embedding = self.get_embedding(query_text)

            # 검색 파라미터
            search_params = {
                "metric_type": "L2",
                "params": {"nprobe": 10}
            }

            # 검색 실행
            results = collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=search_params,
                limit=limit,
                expr=filter_expr,
                output_fields=[
                    "issue_key", "project_key", "issue_type",
                    "summary", "description", "assignee",
                    "priority", "status", "duedate", "created", "updated"
                ]
            )

            # 결과 포맷팅
            formatted_results = []
            for hits in results:
                for hit in hits:
                    formatted_results.append({
                        "key": hit.entity.get("issue_key"),
                        "project": hit.entity.get("project_key"),
                        "issuetype": hit.entity.get("issue_type"),
                        "summary": hit.entity.get("summary"),
                        "description": hit.entity.get("description"),
                        "assignee": hit.entity.get("assignee"),
                        "priority": hit.entity.get("priority"),
                        "status": hit.entity.get("status"),
                        "duedate": hit.entity.get("duedate"),
                        "created": hit.entity.get("created"),
                        "updated": hit.entity.get("updated"),
                        "score": hit.distance
                    })

            return formatted_results

        except Exception as e:
            print(f"❌ 검색 실패: {e}")
            return []

    def get_stats(self) -> Dict:
        """컬렉션 통계 조회"""
        try:
            collection = Collection(self.collection_name)
            collection.load()

            stats = {
                "name": self.collection_name,
                "count": collection.num_entities,
                "schema": str(collection.schema)
            }

            return stats

        except Exception as e:
            print(f"❌ 통계 조회 실패: {e}")
            return {}

    def get_unique_projects(self) -> List[str]:
        """
        Milvus에서 유니크한 프로젝트 키 목록 반환

        Returns:
            프로젝트 키 리스트 (예: ["KAN", "TEST", "SKN"])
        """
        try:
            collection = Collection(self.collection_name)
            collection.load()

            # 모든 project_key 조회
            results = collection.query(
                expr="id > 0",  # 모든 데이터
                output_fields=["project_key"],
                limit=10000  # 충분히 큰 값
            )

            # 유니크한 project_key 추출
            unique_projects = list(set([r["project_key"] for r in results if r.get("project_key")]))
            unique_projects.sort()  # 정렬

            print(f"[Milvus] 프로젝트 키 목록: {unique_projects}")
            return unique_projects

        except Exception as e:
            print(f"❌ 프로젝트 목록 조회 실패: {e}")
            return []

    def get_issue_types_by_project(self) -> Dict[str, List[str]]:
        """
        프로젝트별 이슈 타입 목록 반환

        Returns:
            {project_key: [issue_types]} 형식
            예: {"KAN": ["작업", "버그", "스토리"], "TEST": ["작업", "버그"]}
        """
        try:
            collection = Collection(self.collection_name)
            collection.load()

            # 모든 project_key와 issue_type 조회
            results = collection.query(
                expr="id > 0",
                output_fields=["project_key", "issue_type"],
                limit=10000
            )

            # 프로젝트별로 이슈 타입 그룹화
            project_types = {}
            for r in results:
                project = r.get("project_key")
                issue_type = r.get("issue_type")

                if project and issue_type:
                    if project not in project_types:
                        project_types[project] = set()
                    project_types[project].add(issue_type)

            # set을 list로 변환하고 정렬
            result = {k: sorted(list(v)) for k, v in project_types.items()}

            print(f"[Milvus] 프로젝트별 이슈 타입: {result}")
            return result

        except Exception as e:
            print(f"❌ 이슈 타입 조회 실패: {e}")
            return {}


# 전역 인스턴스
milvus_client = MilvusClient()
