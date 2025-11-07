from django.contrib import admin
from django.urls import path
from core import views

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),

    # 기존 페이지
    path("", views.home, name="home"),
    path("download/", views.download_page, name="download"),  # ✅ 로그인 필요 (view에서 보호)
    path("result/", views.result_page, name="result"),
    path("test/", views.test_page, name="test"),

    # 신규: 인증/캘린더
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("calendar/", views.calendar_page, name="calendar"),  # ✅ 로그인 필요 (view에서 보호)
    path("users/", views.user_management, name="user_management"),
    path("admin/integrations/update/", views.admin_integrations_update, name="admin_integrations_update"),
    path("admin/invite/", views.admin_invite, name="admin_invite"),
    
    # 예시 다운로드 API (그대로)
    path("api/download-file/", views.download_file_api, name="download_file_api"),
]

# 개발 서버에서 media 서빙 (PDF 업로드 미리보기/다운로드에 필요)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
