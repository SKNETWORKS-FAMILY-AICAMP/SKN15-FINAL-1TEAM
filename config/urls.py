from django.contrib import admin
from django.urls import path
from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.home, name="home"),
    path("download/", views.download_page, name="download"),
    path("result/", views.result_page, name="result"),
    path("api/download-file/", views.download_file_api, name="download_file_api"),
]
from django.contrib import admin
from django.urls import path
from core import views

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.home, name="home"),
    path("download/", views.download_page, name="download"),
    path("result/", views.result_page, name="result"),
    path("test/", views.test_page, name="test"),               # ✅ TEST 업로드 페이지
    path("api/download-file/", views.download_file_api, name="download_file_api"),
]

# 개발 서버에서 업로드 파일 서빙
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
