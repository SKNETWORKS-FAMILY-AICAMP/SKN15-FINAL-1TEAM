# core/views.py
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.utils.encoding import escape_uri_path
from django.urls import reverse

from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone

from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from PyPDF2 import PdfReader
import io, csv, time

from .models import IntegrationSettings


# -------------------------------------------------------------------
# 공통: 인증 폼 위젯 스타일 설정 (template에서 as_widget(attrs=...) 안 써도 됨)
# -------------------------------------------------------------------
def _style_auth_form(form):
    """로그인/회원가입 폼 위젯 공통 스타일"""
    field_placeholders = {
        "username": "아이디",
        "password": "비밀번호",
        "password1": "비밀번호",
        "password2": "비밀번호 확인",
    }
    for name, placeholder in field_placeholders.items():
        if name in form.fields:
            form.fields[name].widget.attrs.update({
                "class": "form-input",
                "placeholder": placeholder,
                "autocomplete": "off",
            })


# -------------------------------------------------------------------
# 기본/랜딩
# -------------------------------------------------------------------
def home(request):
    return render(request, "home.html")


# -------------------------------------------------------------------
# 다운로드 / 캘린더 (로그인 필요)
# -------------------------------------------------------------------
@login_required
def download_page(request):
    # 로그인하지 않은 경우 settings.LOGIN_URL 로 이동
    return render(request, "download.html")


@login_required
def calendar_page(request):
    # 간단한 플레이스홀더 페이지
    return render(request, "calendar.html")


# -------------------------------------------------------------------
# 사용자 관리 페이지
# -------------------------------------------------------------------
@login_required
def user_management(request):
    query = (request.GET.get("q") or "").strip()
    users_qs = User.objects.order_by("-date_joined")
    if query:
        users_qs = users_qs.filter(
            Q(username__icontains=query) | Q(email__icontains=query)
        )
    users = list(users_qs)
    integration_settings = IntegrationSettings.load()
    invite_error_code = request.GET.get("invite_error") or ""
    invite_error_messages = {
        "missing_email": "이메일을 입력해 주세요.",
        "invalid_email": "유효한 이메일 주소를 입력해 주세요.",
    }
    return render(request, "user_management.html", {
        "users": users,
        "total_users": len(users),
        "settings": integration_settings,
        "integrations_saved": request.GET.get("saved") == "1",
        "invite_sent": request.GET.get("invite") == "1",
        "invite_error_code": invite_error_code,
        "invite_error_message": invite_error_messages.get(invite_error_code, ""),
        "search_query": query,
    })


@login_required
def admin_integrations_update(request):
    if request.method != "POST":
        return redirect("user_management")

    settings_obj = IntegrationSettings.load()

    def cleaned(name):
        return (request.POST.get(name) or "").strip()

    settings_obj.jira_key = cleaned("jira_key")
    settings_obj.jira_email = cleaned("jira_email")
    settings_obj.jira_url = cleaned("jira_url")
    settings_obj.google_token = cleaned("google_token")
    settings_obj.openai_token = cleaned("openai_token")
    settings_obj.save()

    return redirect(f"{reverse('user_management')}?saved=1")


@login_required
def admin_invite(request):
    if request.method != "POST":
        return redirect("user_management")

    email = (request.POST.get("email") or "").strip().lower()
    role = (request.POST.get("role") or "member").strip().lower()

    if not email:
        return redirect(f"{reverse('user_management')}?invite_error=missing_email")
    try:
        validate_email(email)
    except ValidationError:
        return redirect(f"{reverse('user_management')}?invite_error=invalid_email")

    username_base = email.split("@")[0] if "@" in email else email
    username = username_base
    suffix = 1
    while not username:
        username = f"user{timezone.now().strftime('%Y%m%d%H%M%S')}"

    while User.objects.filter(username=username).exclude(email=email).exists():
        suffix += 1
        username = f"{username_base}{suffix}"

    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "username": username,
            "is_staff": role == "admin",
            "is_superuser": False,
        },
    )

    if not created:
        # Update role if existing user
        is_admin = role == "admin"
        user.is_staff = is_admin
        if not is_admin:
            user.is_superuser = False
        user.save(update_fields=["is_staff", "is_superuser"])

    # TODO: 이메일 전송 로직은 추후 연결
    return redirect(f"{reverse('user_management')}?invite=1")


# -------------------------------------------------------------------
# 결과 페이지
# - /result/?status=test 일 때는 테스트 업로드 페이지를 그대로 렌더
# -------------------------------------------------------------------
def result_page(request):
    status = request.GET.get("status", "")
    if status == "test":
        return test_page(request)
    return render(request, "result.html", {"status": status})


# -------------------------------------------------------------------
# (예시) 동적 CSV 파일 다운로드 API
# -------------------------------------------------------------------
def download_file_api(request):
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


# -------------------------------------------------------------------
# TEST: PDF 업로드
# - GET  : 업로드 폼
# - POST : 파일 저장 후 정보(파일명/크기/페이지수)만 전달
#         (미리보기/텍스트 추출은 템플릿에서 원치 않으면 빼도 OK)
# -------------------------------------------------------------------
def test_page(request):
    ctx = {}
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            ctx["error"] = "업로드할 PDF 파일을 선택해 주세요."
        else:
            # PDF 여부 확인
            name_lower = upload.name.lower()
            ctype = (upload.content_type or "").lower()
            is_pdf = name_lower.endswith(".pdf") or "pdf" in ctype
            if not is_pdf:
                ctx["error"] = "PDF 파일만 업로드할 수 있어요 (.pdf)."
            else:
                # 저장
                safe_name = upload.name.replace("/", "_").replace("\\", "_")
                ts = timezone.now().strftime("%Y%m%d-%H%M%S")
                saved_path = default_storage.save(f"uploads/{ts}-{safe_name}", upload)
                file_url = settings.MEDIA_URL + saved_path

                ctx.update({
                    "saved_name": safe_name,
                    "file_url": file_url,
                    "size_kb": f"{upload.size/1024:.1f}",
                })

                # (선택) 페이지 수만 읽어오기
                try:
                    with default_storage.open(saved_path, "rb") as f:
                        reader = PdfReader(f)
                        ctx["page_count"] = len(reader.pages)
                except Exception:
                    pass

    return render(request, "test.html", ctx)


# -------------------------------------------------------------------
# 인증: 회원가입 / 로그인 / 로그아웃
# -------------------------------------------------------------------
def signup_view(request):
    """회원가입"""
    next_url = request.GET.get("next") or request.POST.get("next") or "/"
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        _style_auth_form(form)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect(next_url)
    else:
        form = UserCreationForm()
        _style_auth_form(form)

    return render(request, "signup.html", {"form": form, "next": next_url})


def login_view(request):
    """로그인"""
    next_url = request.GET.get("next") or request.POST.get("next") or "/"
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        _style_auth_form(form)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(next_url)
    else:
        form = AuthenticationForm(request)
        _style_auth_form(form)

    return render(request, "login.html", {"form": form, "next": next_url})


def logout_view(request):
    """로그아웃"""
    logout(request)
    return redirect(getattr(settings, "LOGOUT_REDIRECT_URL", "/"))
