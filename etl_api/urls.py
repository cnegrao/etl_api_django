from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

urlpatterns = [
    path('api/v1/', include('api_etl.urls')),
]


urlpatterns = [
    path("api/schema/", SpectacularAPIView.as_view(permission_classes=[AllowAny]), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema", permission_classes=[AllowAny])),
    path("api/v1/", include("api_etl.urls")),
]
