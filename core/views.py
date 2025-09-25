# core/views.py
from django.http import HttpResponse, FileResponse
from django.shortcuts import render
from django.utils.encoding import escape_uri_path

from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from PyPDF2 import PdfReader

import io, csv, time


def home(request):
    return render(request, "home.html")


def download_page(request):
    return render(request, "download.html")


def test_page(request):
    """
    TEST: PDF 업로드 페이지
    - GET : 업로드 폼
    - POST: 파일 저장 후, PDF 미리보기 + 텍스트 일부 추출
    템플릿: templates/test.html
    """
    ctx = {}
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            ctx["error"] = "업로드할 PDF 파일을 선택해 주세요."
        else:
            name_lower = upload.name.lower()
            ctype = (upload.content_type or "").lower()
            is_pdf = name_lower.endswith(".pdf") or "pdf" in ctype
            if not is_pdf:
                ctx["error"] = "PDF 파일만 업로드할 수 있어요 (.pdf)."
            else:
                # 저장 (media/uploads/..)
                safe_name = upload.name.replace("/", "_").replace("\\", "_")
                ts = timezone.now().strftime("%Y%m%d-%H%M%S")
                saved_path = default_storage.save(f"uploads/{ts}-{safe_name}", upload)
                file_url = settings.MEDIA_URL + saved_path

                ctx.update({
                    "saved_name": safe_name,
                    "file_url": file_url,
                    "size_kb": f"{upload.size/1024:.1f}",
                })

                # 텍스트 추출 (앞 3페이지)
                try:
                    with default_storage.open(saved_path, "rb") as f:
                        reader = PdfReader(f)
                        text = ""
                        for i in range(min(3, len(reader.pages))):
                            text += (reader.pages[i].extract_text() or "") + "\n"
                        if len(text) > 5000:
                            text = text[:5000] + "\n... (생략)"
                        ctx["extracted_text"] = text.strip()
                        ctx["page_count"] = len(reader.pages)
                except Exception as e:
                    ctx["error_extract"] = f"PDF 텍스트 추출 실패: {e}"

    return render(request, "test.html", ctx)


def result_page(request):
    """
    결과 확인 페이지.
    /result/?status=test 로 접근하면 업로드 화면(test_page)을 그대로 렌더링.
    그 외에는 result.html을 렌더링.
    """
    status = request.GET.get("status", "")
    if status == "test":
        # ✅ 업로드 화면을 이 경로에서 보여주기
        return test_page(request)

    return render(request, "result.html", {"status": status})


def download_file_api(request):
    """
    (데모) 동적으로 CSV 생성하여 내려주기
    """
    time.sleep(1)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "name", "score"])
    writer.writerow([1, "Alice", 92])
    writer.writerow([2, "Bob", 88])
    writer.writerow([3, "Carol", 95])

    csv_bytes = buffer.getvalue().encode("utf-8-sig")
    resp = HttpResponse(csv_bytes, content_type="text/csv")
    resp["Content-Disposition"] = (
        f"attachment; filename*=UTF-8''{escape_uri_path('report.csv')}"
    )
    return resp
