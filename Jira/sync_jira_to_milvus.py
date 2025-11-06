#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jira → Milvus 전체 동기화 스크립트

역할:
1. Jira에서 모든 프로젝트의 이슈를 가져옴
2. Milvus에 벡터 임베딩과 함께 저장
3. 최초 데이터 로딩 또는 전체 재동기화 시 사용

사용법:
    python sync_jira_to_milvus.py
    python sync_jira_to_milvus.py --project KAN  # 특정 프로젝트만
    python sync_jira_to_milvus.py --max 100      # 최대 100개만
"""

import argparse
from core.jira import jira_client
from core.milvus_client import milvus_client


def sync_all_issues(project_key=None, max_results=None):
    """
    Jira의 모든 이슈를 Milvus에 동기화

    Args:
        project_key: 특정 프로젝트만 동기화 (None이면 전체)
        max_results: 최대 이슈 개수 (None이면 전체)
    """
    print("=" * 60)
    print("🔄 Jira → Milvus 동기화 시작")
    print("=" * 60)

    try:
        # 1. 프로젝트 목록 가져오기
        projects = jira_client.get_projects()

        if project_key:
            projects = [p for p in projects if p['key'] == project_key]
            if not projects:
                print(f"❌ 프로젝트 '{project_key}'를 찾을 수 없습니다.")
                return
            print(f"📂 프로젝트: {project_key}")
        else:
            print(f"📂 전체 프로젝트: {len(projects)}개")
            for p in projects:
                print(f"   • {p['key']}: {p['name']}")

        # 2. 각 프로젝트별로 이슈 가져오기
        total_synced = 0

        for project in projects:
            proj_key = project['key']
            print(f"\n📥 [{proj_key}] 이슈 가져오는 중...")

            # JQL 쿼리 생성
            jql = f"project = {proj_key} ORDER BY created DESC"

            # 이슈 검색
            issues = jira_client.search_issues(
                jql=jql,
                max_results=max_results or 1000  # 기본 1000개
            )

            if not issues:
                print(f"   ⚠️  이슈가 없습니다.")
                continue

            print(f"   ✅ {len(issues)}개 이슈 발견")

            # 3. Milvus에 저장
            print(f"   💾 Milvus에 저장 중...")
            success = milvus_client.upsert_issues(issues)

            if success:
                total_synced += len(issues)
                print(f"   ✅ {len(issues)}개 이슈 동기화 완료")
            else:
                print(f"   ❌ 동기화 실패")

        # 4. 완료 메시지
        print("\n" + "=" * 60)
        print(f"✅ 동기화 완료! 총 {total_synced}개 이슈")
        print("=" * 60)

        # 5. Milvus 통계 출력
        stats = milvus_client.get_stats()
        if stats:
            print(f"\n📊 Milvus 통계:")
            print(f"   • 컬렉션: {stats.get('name')}")
            print(f"   • 총 이슈 수: {stats.get('count')}")

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description="Jira 이슈를 Milvus에 동기화합니다."
    )
    parser.add_argument(
        "--project",
        type=str,
        help="동기화할 프로젝트 키 (예: KAN, TEST). 생략하면 전체 프로젝트 동기화"
    )
    parser.add_argument(
        "--max",
        type=int,
        help="프로젝트당 최대 이슈 개수 (생략하면 전체)"
    )

    args = parser.parse_args()

    # 동기화 실행
    sync_all_issues(
        project_key=args.project,
        max_results=args.max
    )


if __name__ == "__main__":
    main()
