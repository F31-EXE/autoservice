from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("imports/", include("apps.workorders.urls")),
    path("recommendations/", include("apps.recommendations.urls")),
    path("outreach/", include("apps.outreach.urls")),
    path("messaging/", include("apps.messaging.urls")),
    path("", include("apps.accounts.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
