from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

API_KEY = "A7F3K9P2X6M4Q8Z1"


class APIKeyUser:
    is_authenticated = True

    def __str__(self):
        return "api-key-user"


class APIKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        key = request.headers.get("X-API-Key")

        if not key:
            raise AuthenticationFailed("API key não enviada")

        if key != API_KEY:
            raise AuthenticationFailed("API key inválida")

        return (APIKeyUser(), None)

    def authenticate_header(self, request):
        return "X-API-Key"
