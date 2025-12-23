from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from .models import APIKey


class APIKeyAuthentication(BaseAuthentication):
    """
    Autenticação simples baseada em cabeçalho Authorization: Api-Key <key>
    """

    keyword = "Api-Key"

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith(self.keyword):
            return None  # sem header -> DRF tenta outras autenticações

        key_value = auth_header[len(self.keyword):].strip()

        try:
            api_key = APIKey.objects.get(key=key_value, is_active=True)
        except APIKey.DoesNotExist:
            raise exceptions.AuthenticationFailed(
                "Invalid or inactive API key")

        # Atualiza último uso
        api_key.last_used_at = timezone.now()
        api_key.save(update_fields=["last_used_at"])

        # Autenticação não tem user real, mas você pode retornar um placeholder
        return (None, api_key)
