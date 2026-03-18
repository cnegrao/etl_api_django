from django.urls import path

from .views import ReceiveBatchView

urlpatterns = [
    path("lab-intake/batch/", ReceiveBatchView.as_view(), name="receive_batch"),
]
