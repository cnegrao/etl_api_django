from django.apps import AppConfig


class ApiEtlConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api_etl'

    def ready(self):
        import api_etl.schema
