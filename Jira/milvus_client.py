# -*- coding: utf-8 -*-
"""
milvus_client.py - Milvus 벡터 DB 클라이언트 (최종 버전)

설계:
- Milvus를 단일 저장소로 사용 (RDB 없음)
- 메타데이터 + 임베딩 모두 Milvus에 저장
- 청크 단위 저장 (긴 설명 대응)
- 임베딩 대상: summary + description (+ 선택적 태그)

AWS 연결:
- Host: 3.36.185.140 (EC2 Public IP)
- Port: 19530
- Collection: jira_issues

연결:
- config.py → MILVUS_HOST, EMBED_MODEL, JIRA_BASE_URL
- utils.py → adf_to_text로 설명 평문 변환
- jira_fetch_all.py → 전체 이슈 수집
- graph.py → Entry 노드에서 자동 동기화
"""

from typing import List, Dict, Any, Optional
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility
from openai import OpenAI
import json
import hashlib
import time
from .search_hybrid import search_hybrid


from .config import (
    OPENAI_API_KEY, EMBED_MODEL, EMBED_DIM,
    MILVUS_HOST, MILVUS_PORT, MILVUS_COLLECTION, JIRA_BASE_URL
)

_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ─────────────────────────────────────────────────────────
# 컨텐츠 처리
# ─────────────────────────────────────────────────────────
def build_content(summary: str, description_plain: str,
                  tag: Optional[Dict[str, str]] = None) -> str:
    """
    임베딩용 텍스트 생성 (제목 + 설명 + 선택적 태그)
    
    Args:
        summary: 이슈 제목
        description_plain: ADF → 평문 변환된 설명
        tag: 선택적 태그 dict (예: {"key": "KAN-13", "priority": "High"})
    
    Returns:
        임베딩할 텍스트
    
    예시:
        build_content("버그 수정", "500 에러 발생", {"key": "KAN-13"})
        → "버그 수정\n\n500 에러 발생\n\n[tags] key:KAN-13"
    """
    base = ((summary or "") + "\n\n" + (description_plain or "")).strip()
    
    if tag:
        tag_line = "[tags] " + " ".join(f"{k}:{v}" for k, v in tag.items() if v)
        return (base + "\n\n" + tag_line).strip()
    
    return base


def chunk_text(text: str, max_len: int = 1200, overlap: int = 200) -> List[str]:
    """
    텍스트 청크 분할 (오버랩 적용)
    
    Args:
        text: 원본 텍스트
        max_len: 청크 최대 길이 (기본 1200자)
        overlap: 청크 간 오버랩 (기본 200자)
    
    Returns:
        청크 리스트
    """
    if len(text) <= max_len:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + max_len
        chunk = text[start:end]
        chunks.append(chunk)
        
        # ✅ 버그 수정: 마지막 청크 처리
        if end >= len(text):
            break
        
        start = end - overlap
    
    return chunks


def compute_hash(text: str) -> str:
    """
    컨텐츠 해시 계산 (재색인 판단용)
    
    Args:
        text: 해시할 텍스트
    
    Returns:
        SHA256 해시 (64자)
    """
    return hashlib.sha256(text.encode()).hexdigest()


# ─────────────────────────────────────────────────────────
# Milvus Store
# ─────────────────────────────────────────────────────────
class MilvusStore:
    """
    Milvus Only 스토어
    
    AWS 연결: 3.36.185.140:19530
    
    스키마:
    - chunk_id: VARCHAR(100) - PK, 형식: "issue_key#chunk_index"
    - issue_key: VARCHAR(50) - 이슈 키 (그룹핑)
    - project_key: VARCHAR(20)
    - issue_type: VARCHAR(50)
    - status: VARCHAR(50)
    - priority: VARCHAR(20)
    - assignee: VARCHAR(100)
    - summary: VARCHAR(500) - 표시용
    - description_plain: VARCHAR(5000) - 전체 설명
    - labels_json: VARCHAR(500) - JSON 문자열
    - updated_ts: INT64 - epoch ms
    - content_hash: VARCHAR(64) - SHA256
    - source_uri: VARCHAR(200) - Jira 링크
    - chunk_index: INT32
    - chunk_text: VARCHAR(2000) - 실제 임베딩된 청크
    - embedding: FLOAT_VECTOR(1536)
    """

    def search_smart(self, query: str, topk: int = 5):
        return search_hybrid(self, query, topk)
    
    def __init__(self, host: str = MILVUS_HOST, port: int = MILVUS_PORT,
                 collection_name: str = MILVUS_COLLECTION):
        self.host = host
        self.port = port
        self.collection_name = collection_name
        self.collection: Optional[Collection] = None
        self.is_connected = False
    
    def connect(self):
        """Milvus 서버 연결"""
        if self.is_connected:
            return
        
        try:
            connections.connect(
                alias="default",
                host=self.host,
                port=str(self.port)
            )
            self.is_connected = True
            print(f"[Milvus] 연결: {self.host}:{self.port}")
        except Exception as e:
            raise RuntimeError(f"Milvus 연결 실패: {e}")
    
    def create_collection_if_not_exists(self):
        """컬렉션 생성 (프로덕션 레디 스키마)"""
        self.connect()
        
        if utility.has_collection(self.collection_name):
            self.collection = Collection(self.collection_name)
            print(f"[Milvus] 기존 컬렉션: {self.collection_name}")
            return
        
        # 프로덕션 스키마 정의
        fields = [
            # PK
            FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            
            # 파티션 키
            FieldSchema(name="project_key", dtype=DataType.VARCHAR, max_length=20),
            
            # 식별자
            FieldSchema(name="local_id", dtype=DataType.VARCHAR, max_length=50),  # 내부 ID
            FieldSchema(name="issue_key", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="chunk_index", dtype=DataType.INT32),
            
            # Delta Sync용
            FieldSchema(name="content_hash", dtype=DataType.VARCHAR, max_length=64),  # ✅ 필수
            
            # 표시용
            FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=500),  # ✅ 필수
            FieldSchema(name="description_plain", dtype=DataType.VARCHAR, max_length=5000),  # ✅ 전체 본문
            
            # 필터용 (메타데이터)
            FieldSchema(name="issue_type", dtype=DataType.VARCHAR, max_length=50),  # ✅ 필수
            FieldSchema(name="status", dtype=DataType.VARCHAR, max_length=50),  # ✅ 필수
            FieldSchema(name="priority", dtype=DataType.VARCHAR, max_length=20),  # ✅ 필수
            FieldSchema(name="assignee_norm", dtype=DataType.VARCHAR, max_length=100),  # ✅ 필수 (정규화)
            FieldSchema(name="labels_json", dtype=DataType.VARCHAR, max_length=500),  # ✅ 필수 (JSON)
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=50),  # ✅ 필수
            
            # 컨텍스트/RAG용
            FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=2000),  # ✅ 필수
            
            # UX/신뢰용
            FieldSchema(name="source_uri", dtype=DataType.VARCHAR, max_length=200),  # 권장
            FieldSchema(name="updated_ts", dtype=DataType.INT64),  # 권장 (정렬)
            
            # 벡터
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBED_DIM)
        ]
        
        schema = CollectionSchema(fields=fields, description="Jira 이슈 벡터 스토어 (프로덕션)")
        self.collection = Collection(name=self.collection_name, schema=schema)
        
        # HNSW 인덱스 (COSINE 유사도)
        index_params = {
            "index_type": "HNSW",
            "metric_type": "COSINE",
            "params": {"M": 16, "efConstruction": 200}
        }
        self.collection.create_index(field_name="embedding", index_params=index_params)
        
        # 파티션 생성 준비 (동적 파티셔닝)
        # 프로젝트별로 파티션 생성 가능
        
        print(f"[Milvus] 컬렉션 생성: {self.collection_name} (프로덕션 스키마)")
        print(f"  - PK: chunk_id")
        print(f"  - 파티션 키: project_key")
        print(f"  - 인덱스: HNSW+COSINE")
    
    def embed_batch(self, texts: List[str], batch_size: int = 50) -> List[List[float]]:
        """배치 임베딩 (OpenAI API)"""
        if not _client:
            raise RuntimeError("OPENAI_API_KEY 필요")
        
        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            resp = _client.embeddings.create(model=EMBED_MODEL, input=batch)
            embeddings.extend([d.embedding for d in resp.data])
            print(f"  임베딩: {min(i+batch_size, len(texts))}/{len(texts)}")
        
        return embeddings
    
    def insert_issues(self, issues: List[Dict[str, Any]], use_tag: bool = True):
        """
        이슈 배치 삽입 (프로덕션 스키마)
        
        Args:
            issues: 이슈 리스트 (key, project, summary, desc, ...)
            use_tag: 태그 라인 추가 여부 (기본 True)
        """
        if not issues:
            return
        
        self.create_collection_if_not_exists()
        
        all_chunks = []
        
        for it in issues:
            summary = it.get("summary", "")
            desc_plain = it.get("desc", "")
            issue_key = it.get("key", "")
            
            # local_id (내부 식별자, 없으면 issue_key 사용)
            local_id = it.get("local_id", issue_key)
            
            # 전체 컨텐츠 (해시 계산용)
            full_content = f"{summary}\n\n{desc_plain}"
            content_hash = compute_hash(full_content)
            
            # 담당자 정규화 (이메일 → 이름, 또는 소문자)
            assignee_raw = it.get("assignee", "")
            assignee_norm = assignee_raw.split("@")[0] if "@" in assignee_raw else assignee_raw.lower()
            
            # 카테고리 자동 추출 (라벨 기반 또는 타입 기반)
            labels = it.get("labels", [])
            if "backend" in labels or "api" in labels:
                category = "backend"
            elif "frontend" in labels or "ui" in labels:
                category = "frontend"
            elif "bug" in labels:
                category = "bug"
            else:
                category = it.get("issuetype", "task").lower()
            
            # 태그 추가 (선택)
            tag = None
            if use_tag:
                tag = {
                    "key": issue_key,
                    "project": it.get("project"),
                    "priority": it.get("priority"),
                    "status": it.get("status")
                }
            
            # 임베딩용 컨텐츠
            embed_content = build_content(summary, desc_plain, tag)
            
            # 청크 분할
            chunks = chunk_text(embed_content, max_len=1200, overlap=200)
            
            for idx, chunk in enumerate(chunks):
                all_chunks.append({
                    "chunk_id": f"{local_id}#{idx}",  # ✅ local_id 사용 (불변)
                    "project_key": it.get("project", ""),
                    "local_id": local_id,
                    "issue_key": issue_key,
                    "chunk_index": idx,
                    "content_hash": content_hash,
                    "summary": summary[:500],
                    "description_plain": desc_plain[:5000],  # ✅ 전체 본문 저장
                    "issue_type": it.get("issuetype", ""),
                    "status": it.get("status", ""),
                    "priority": it.get("priority", ""),
                    "assignee_norm": assignee_norm[:100],
                    "labels_json": json.dumps(labels, ensure_ascii=False),
                    "category": category,
                    "chunk_text": chunk,
                    "source_uri": f"{JIRA_BASE_URL}/browse/{issue_key}",
                    "updated_ts": int(time.time() * 1000),
                    "embedding": None  # 나중에 할당
                })
        
        print(f"[Milvus] {len(issues)}개 이슈 → {len(all_chunks)}개 청크")
        
        # 임베딩 생성
        texts = [c["chunk_text"] for c in all_chunks]
        embeddings = self.embed_batch(texts)
        
        for c, emb in zip(all_chunks, embeddings):
            c["embedding"] = emb
        
        # 데이터 준비 (필드 순서 맞춤)
        data = [
            [c["chunk_id"] for c in all_chunks],
            [c["project_key"] for c in all_chunks],
            [c["local_id"] for c in all_chunks],
            [c["issue_key"] for c in all_chunks],
            [c["chunk_index"] for c in all_chunks],
            [c["content_hash"] for c in all_chunks],
            [c["summary"] for c in all_chunks],
            [c["description_plain"] for c in all_chunks],  # ✅ 추가
            [c["issue_type"] for c in all_chunks],
            [c["status"] for c in all_chunks],
            [c["priority"] for c in all_chunks],
            [c["assignee_norm"] for c in all_chunks],
            [c["labels_json"] for c in all_chunks],
            [c["category"] for c in all_chunks],
            [c["chunk_text"] for c in all_chunks],
            [c["source_uri"] for c in all_chunks],
            [c["updated_ts"] for c in all_chunks],
            [c["embedding"] for c in all_chunks]
        ]
        
        self.collection.insert(data)
        self.collection.flush()
        
        print(f"[Milvus] {len(all_chunks)}개 청크 삽입 완료")
    
    def update_issue(self, issue_key: str, issue_data: Dict[str, Any]):
        """이슈 업데이트 (삭제 후 재삽입)"""
        self.create_collection_if_not_exists()
        
        # 기존 청크 삭제
        self.collection.delete(f'issue_key == "{issue_key}"')
        self.collection.flush()
        
        # 재삽입
        self.insert_issues([issue_data])
    
    def delete_issue(self, issue_key: str):
        """이슈 삭제"""
        if not self.collection:
            return
        
        self.collection.delete(f'issue_key == "{issue_key}"')
        self.collection.flush()
        print(f"[Milvus] {issue_key} 삭제")
    
    def search(self, query: str, topk: int = 5,
               project: Optional[str] = None,
               status: Optional[str] = None,
               priority: Optional[str] = None,
               category: Optional[str] = None,
               assignee: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        벡터 검색 (다양한 메타 필터 지원)
        
        Args:
            query: 검색 쿼리
            topk: 반환 개수
            project: 프로젝트 필터
            status: 상태 필터 (예: "In Progress")
            priority: 우선순위 필터 (예: "High")
            category: 카테고리 필터 (예: "backend")
            assignee: 담당자 필터 (정규화된 이름)
        
        Returns:
            이슈 리스트 (청크 중복 제거, 점수 정렬)
        
        예시:
            # 기본 검색
            results = store.search("로그인 버그", topk=5)
            
            # 필터 조합
            results = store.search(
                "API 오류",
                project="KAN",
                status="In Progress",
                category="backend",
                assignee="choi"
            )
        """
        if not self.collection:
            self.create_collection_if_not_exists()
        
        self.collection.load()
        
        # 쿼리 임베딩
        resp = _client.embeddings.create(model=EMBED_MODEL, input=[query])
        qv = resp.data[0].embedding
        
        # 필터 표현식 구성
        filters = []
        if project:
            filters.append(f'project_key == "{project}"')
        if status:
            filters.append(f'status == "{status}"')
        if priority:
            filters.append(f'priority == "{priority}"')
        if category:
            filters.append(f'category == "{category}"')
        if assignee:
            # ✅ 동일한 정규화 로직 적용
            assignee_normalized = assignee.split("@")[0] if "@" in assignee else assignee
            filters.append(f'assignee_norm like "%{assignee_normalized.lower()}%"')
        
        expr = " && ".join(filters) if filters else None
        
        # 검색
        results = self.collection.search(
            data=[qv],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=topk * 3,  # 청크 중복 고려
            expr=expr,
            output_fields=[
                "issue_key", "project_key", "summary",
                "issue_type", "status", "priority", "assignee_norm",
                "labels_json", "category", "source_uri", "updated_ts"
            ]
        )
        
        # 청크 → 이슈 중복 제거 (최고 점수만)
        seen = {}
        for hit in results[0]:
            key = hit.entity.get("issue_key")
            score = float(hit.distance)
            
            if key not in seen or score > seen[key]["_score"]:
                labels = json.loads(hit.entity.get("labels_json") or "[]")
                
                seen[key] = {
                    "key": key,
                    "project": hit.entity.get("project_key"),
                    "summary": hit.entity.get("summary"),
                    "issuetype": hit.entity.get("issue_type"),
                    "status": hit.entity.get("status"),
                    "priority": hit.entity.get("priority"),
                    "assignee": hit.entity.get("assignee_norm"),
                    "labels": labels,
                    "category": hit.entity.get("category"),
                    "source_uri": hit.entity.get("source_uri"),
                    "updated_ts": hit.entity.get("updated_ts"),
                    "_score": score
                }
        
        # 점수 정렬 후 topk
        out = sorted(seen.values(), key=lambda x: x["_score"], reverse=True)
        return out[:topk]
    
    def delete_by_project(self, project_key: str):
        """프로젝트별 삭제"""
        if not self.collection:
            return
        
        expr = f'project_key == "{project_key}"'
        self.collection.delete(expr)
        self.collection.flush()
        print(f"[Milvus] {project_key} 삭제")
    
    def count(self) -> int:
        """전체 청크 수"""
        if not self.collection:
            return 0
        return self.collection.num_entities


# ─────────────────────────────────────────────────────────
# Jira → Milvus 동기화 (Delta Sync)
# ─────────────────────────────────────────────────────────
def sync_issues_to_milvus(project_keys: List[str], store: MilvusStore, 
                          full_sync: bool = False) -> int:
    """
    Jira 이슈를 Milvus에 동기화 (Delta Sync 지원)
    
    Args:
        project_keys: 프로젝트 키 리스트
        store: MilvusStore 인스턴스
        full_sync: True면 전체 재동기화 (기본 False, Delta Sync)
    
    Returns:
        동기화된 이슈 개수
    
    Delta Sync 동작:
    1. Jira에서 전체 이슈 수집
    2. Milvus에서 기존 content_hash 조회
    3. 해시 비교로 변경 감지
    4. 변경된 이슈만 재임베딩/업데이트
    """
    from .jira_fetch_all import fetch_all_issues_by_project
    from .utils import adf_to_text
    
    store.create_collection_if_not_exists()
    store.collection.load()
    
    total_synced = 0
    
    for proj in project_keys:
        print(f"\n[{proj}] 이슈 수집...")
        
        # 1. Jira에서 전체 이슈 수집
        issues = fetch_all_issues_by_project(
            project_key=proj,
            fields=[
                "summary", "description", "assignee", "priority",
                "labels", "issuetype", "status", "updated", "project"
            ],
            page_size=100,
            hard_limit=None
        )
        
        print(f"[{proj}] {len(issues)}개 수집")
        
        # 2. 기존 해시 조회 (Delta Sync)
        existing_hashes = {}
        if not full_sync:
            try:
                # Milvus에서 프로젝트의 모든 issue_key + content_hash 조회
                query_expr = f'project_key == "{proj}"'
                results = store.collection.query(
                    expr=query_expr,
                    output_fields=["issue_key", "content_hash"],
                    limit=100000  # 충분히 큰 값
                )
                
                # issue_key별 최신 해시 저장
                for r in results:
                    existing_hashes[r["issue_key"]] = r["content_hash"]
                
                print(f"[{proj}] 기존 {len(existing_hashes)}개 이슈 해시 조회")
            except Exception as e:
                print(f"[경고] 해시 조회 실패, 전체 동기화 진행: {e}")
                full_sync = True
        
        # 3. 변경 감지
        new_issues = []
        updated_issues = []
        unchanged_count = 0
        
        for it in issues:
            f = it.get("fields", {}) or {}
            key = it.get("key")
            
            summary = f.get("summary") or ""
            desc_plain = adf_to_text(f.get("description"), max_len=3000)
            
            # 해시 계산
            content = f"{summary}\n\n{desc_plain}"
            new_hash = compute_hash(content)
            
            # 변경 감지
            if full_sync or key not in existing_hashes:
                # 신규 이슈
                new_issues.append({
                    "key": key,
                    "local_id": key,  # RDB 없으면 issue_key 사용
                    "project": (f.get("project") or {}).get("key") or proj,
                    "summary": summary,
                    "desc": desc_plain,
                    "assignee": (f.get("assignee") or {}).get("displayName") or "",
                    "priority": (f.get("priority") or {}).get("name") or "",
                    "labels": f.get("labels") or [],
                    "issuetype": (f.get("issuetype") or {}).get("name") or "",
                    "status": (f.get("status") or {}).get("name") or "",
                    "updated": f.get("updated") or ""
                })
            elif existing_hashes[key] != new_hash:
                # 변경된 이슈
                updated_issues.append({
                    "key": key,
                    "local_id": key,
                    "project": (f.get("project") or {}).get("key") or proj,
                    "summary": summary,
                    "desc": desc_plain,
                    "assignee": (f.get("assignee") or {}).get("displayName") or "",
                    "priority": (f.get("priority") or {}).get("name") or "",
                    "labels": f.get("labels") or [],
                    "issuetype": (f.get("issuetype") or {}).get("name") or "",
                    "status": (f.get("status") or {}).get("name") or "",
                    "updated": f.get("updated") or ""
                })
            else:
                # 변경 없음
                unchanged_count += 1
        
        # 4. 동기화 실행
        print(f"[{proj}] 신규: {len(new_issues)}개, 변경: {len(updated_issues)}개, 유지: {unchanged_count}개")
        
        # 신규 이슈 삽입
        if new_issues:
            print(f"[{proj}] {len(new_issues)}개 신규 이슈 삽입 중...")
            store.insert_issues(new_issues, use_tag=True)
        
        # 변경된 이슈 업데이트 (삭제 후 재삽입)
        if updated_issues:
            print(f"[{proj}] {len(updated_issues)}개 변경 이슈 업데이트 중...")
            for issue in updated_issues:
                store.update_issue(issue["key"], issue)
        
        # 삭제된 이슈 처리 (선택적)
        if not full_sync and existing_hashes:
            current_keys = {it.get("key") for it in issues}
            deleted_keys = set(existing_hashes.keys()) - current_keys
            
            if deleted_keys:
                print(f"[{proj}] {len(deleted_keys)}개 삭제된 이슈 제거 중...")
                for key in deleted_keys:
                    store.delete_issue(key)
        
        total_synced += len(new_issues) + len(updated_issues)
    
    return total_synced