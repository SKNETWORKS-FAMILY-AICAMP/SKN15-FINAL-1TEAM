#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI Server for Jira Agent
"""
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any, List
import sys
from pathlib import Path

# core 패키지를 import 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent_v2 import jira_agent
from core.jira import jira_client
from core.milvus_client import milvus_client
from core.config import WEBHOOK_URL, WEBHOOK_AUTO_REGISTER

# FastAPI 앱 생성
app = FastAPI(
    title="Jira Agent API",
    description="LangGraph 기반 Jira Agent REST API",
    version="2.0.0"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 실제 운영에서는 특정 도메인만 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────
# Startup Event: 웹훅 자동 등록
# ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 실행되는 이벤트"""
    print("=" * 60)
    print("  Jira Agent 서버 시작 중...")
    print("=" * 60)

    # 웹훅 자동 등록
    if WEBHOOK_AUTO_REGISTER and WEBHOOK_URL:
        print(f"\n[Webhook] 자동 등록 시작: {WEBHOOK_URL}")
        success = jira_client.register_webhook(WEBHOOK_URL)

        if success:
            print("[Webhook] ✅ 웹훅 등록 완료")
        else:
            print("[Webhook] ⚠️  웹훅 등록 실패 (수동으로 등록 필요)")
    else:
        if not WEBHOOK_AUTO_REGISTER:
            print("[Webhook] 자동 등록이 비활성화되어 있습니다.")
        if not WEBHOOK_URL:
            print("[Webhook] WEBHOOK_URL이 설정되지 않았습니다.")

    print("=" * 60)
    print("  ✅ 서버 시작 완료")
    print("=" * 60)


# ─────────────────────────────────────────────────────────
# Request/Response Models
# ─────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """채팅 요청 모델"""
    message: str
    session_id: Optional[str] = "default"

    class Config:
        json_schema_extra = {
            "example": {
                "message": "KAN 프로젝트의 이슈를 검색해줘",
                "session_id": "user123"
            }
        }


class ChatResponse(BaseModel):
    """채팅 응답 모델"""
    stage: str
    response: str
    message: str
    session_id: str
    data: Optional[Any] = None
    missing_fields: Optional[List[str]] = None

    class Config:
        json_schema_extra = {
            "example": {
                "stage": "done",
                "response": "검색 결과를 찾았습니다.",
                "message": "검색 결과를 찾았습니다.",
                "session_id": "user123",
                "data": None,
                "missing_fields": []
            }
        }


# ─────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "service": "Jira Agent API",
        "version": "2.0.0",
        "status": "running",
        "endpoints": {
            "chat": "/chat",
            "health": "/health",
            "docs": "/docs"
        }
    }


@app.get("/health")
async def health_check():
    """헬스 체크 엔드포인트"""
    return {
        "status": "healthy",
        "agent": "ready"
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    채팅 엔드포인트

    사용자 메시지를 받아 Jira Agent를 통해 처리하고 응답을 반환합니다.

    Args:
        request: 채팅 요청 (message, session_id)

    Returns:
        ChatResponse: 에이전트 처리 결과

    Examples:
        >>> POST /chat
        >>> {
        >>>   "message": "KAN 프로젝트의 이슈를 검색해줘",
        >>>   "session_id": "user123"
        >>> }
    """
    try:
        # Jira Agent 처리
        result = jira_agent.process(
            user_input=request.message,
            session_id=request.session_id
        )

        # 응답 생성
        return ChatResponse(
            stage=result.get("stage", "done"),
            response=result.get("response", result.get("message", "")),
            message=result.get("message", result.get("response", "")),
            session_id=result.get("session_id", request.session_id),
            data=result.get("data"),
            missing_fields=result.get("missing_fields", [])
        )

    except Exception as e:
        # 에러 처리
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal Server Error",
                "message": str(e),
                "session_id": request.session_id
            }
        )


@app.post("/webhook/jira")
async def jira_webhook(webhook_data: dict):
    """
    Jira 웹훅 엔드포인트

    Jira에서 이슈 생성/수정/삭제 이벤트 발생 시 호출되어 Milvus를 자동으로 동기화합니다.

    지원 이벤트:
    - jira:issue_created
    - jira:issue_updated
    - jira:issue_deleted

    Args:
        webhook_data: Jira 웹훅 페이로드

    Returns:
        성공/실패 상태
    """
    try:
        webhook_event = webhook_data.get("webhookEvent")
        issue_data = webhook_data.get("issue", {})
        issue_key = issue_data.get("key")

        print(f"[WEBHOOK] 이벤트: {webhook_event}, 이슈: {issue_key}")

        if webhook_event == "jira:issue_deleted":
            # 삭제 이벤트: Milvus에서도 삭제
            print(f"[WEBHOOK] 이슈 삭제 시작: {issue_key}")

            success = milvus_client.delete_by_issue_key(issue_key)

            if success:
                print(f"[WEBHOOK] ✅ Milvus에서 이슈 삭제 완료: {issue_key}")
                return {"status": "success", "message": f"Issue {issue_key} deleted from Milvus"}
            else:
                print(f"[WEBHOOK] ❌ Milvus 삭제 실패: {issue_key}")
                return {"status": "error", "message": f"Failed to delete {issue_key} from Milvus"}

        elif webhook_event in ["jira:issue_created", "jira:issue_updated"]:
            # 생성/수정 이벤트: Jira에서 최신 데이터 가져와서 Milvus 업데이트
            print(f"[WEBHOOK] 이슈 동기화 시작: {issue_key}")

            # Jira에서 최신 이슈 데이터 가져오기
            jira_issue = jira_client.get_issue(issue_key)

            if jira_issue:
                # Milvus에 UPSERT
                success = milvus_client.upsert_issues([jira_issue])

                if success:
                    print(f"[WEBHOOK] ✅ Milvus 동기화 완료: {issue_key}")
                    return {"status": "success", "message": f"Issue {issue_key} synced to Milvus"}
                else:
                    print(f"[WEBHOOK] ❌ Milvus 동기화 실패: {issue_key}")
                    return {"status": "error", "message": f"Failed to sync {issue_key} to Milvus"}
            else:
                print(f"[WEBHOOK] ❌ Jira 이슈 조회 실패: {issue_key}")
                return {"status": "error", "message": f"Failed to fetch issue {issue_key} from Jira"}

        else:
            # 지원하지 않는 이벤트
            print(f"[WEBHOOK] 지원하지 않는 이벤트: {webhook_event}")
            return {"status": "ignored", "message": f"Event {webhook_event} not supported"}

    except Exception as e:
        print(f"[WEBHOOK] 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Webhook processing failed",
                "message": str(e)
            }
        )


# ─────────────────────────────────────────────────────────
# 서버 실행
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":

    print("=" * 60)
    print("  Jira Agent FastAPI Server")
    print("=" * 60)
    print("  Endpoints:")
    print("    - POST /chat         : 채팅 메시지 처리")
    print("    - POST /webhook/jira : Jira 웹훅 (자동 동기화)")
    print("    - GET  /health       : 헬스 체크")
    print("    - GET  /docs         : API 문서 (Swagger UI)")
    print("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
