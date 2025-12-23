import secrets

from django.db import models
from django.utils import timezone


class ETLBatch(models.Model):
    """
    Controla os lotes recebidos via POST (batch de amostras).
    """
    partner_id = models.IntegerField()
    partner_batch_id = models.CharField(max_length=100)
    total_samples = models.IntegerField()
    raw_payload = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "etl_batch"
        indexes = [
            models.Index(fields=["partner_id", "partner_batch_id"]),
        ]

    def __str__(self):
        return f"Lote {self.partner_batch_id} ({self.partner_id})"


class ETLStageSampleIntake(models.Model):
    """
    Representa cada amostra recebida via API (antes de normalizar).
    """
    batch = models.ForeignKey(ETLBatch, on_delete=models.CASCADE)
    partner_id = models.IntegerField()
    partner_batch_id = models.CharField(max_length=100)
    partner_record_id = models.CharField(max_length=100, null=True, blank=True)
    sample_code = models.CharField(max_length=60)
    sampling_date = models.DateField(null=True, blank=True)
    year = models.IntegerField()
    stage = models.IntegerField(default=1)
    lab_number = models.CharField(max_length=50, null=True, blank=True)
    company_external_code = models.CharField(
        max_length=100, null=True, blank=True)
    laboratory_external_code = models.CharField(
        max_length=100, null=True, blank=True)
    trial_external_code = models.CharField(
        max_length=100, null=True, blank=True)
    extra = models.JSONField(null=True, blank=True)
    raw_payload = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "etl_stage_sampleintake"
        indexes = [
            models.Index(fields=["partner_id", "partner_batch_id"]),
            models.Index(fields=["sample_code", "year", "lab_number"]),
        ]

    def __str__(self):
        return f"{self.sample_code} ({self.year})"


class ETLStageSampleIntakeValue(models.Model):
    """
    Valores analíticos recebidos (mensurandos, unidades, métodos, etc.)
    """
    stage_sampleintake = models.ForeignKey(
        ETLStageSampleIntake, on_delete=models.CASCADE)
    indicator_code = models.CharField(max_length=100)
    method_code = models.CharField(max_length=100, null=True, blank=True)
    unit_code = models.CharField(max_length=100, null=True, blank=True)
    value_numeric = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True)
    value_text = models.CharField(max_length=200, null=True, blank=True)
    extra = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "etl_stage_sampleintake_value"
        indexes = [
            models.Index(fields=["indicator_code"]),
            models.Index(fields=["method_code"]),
            models.Index(fields=["unit_code"]),
        ]

    def __str__(self):
        return f"{self.indicator_code}: {self.value_numeric or self.value_text}"


class APIKey(models.Model):
    """
    Armazena chaves de API para autenticação dos parceiros.
    """
    name = models.CharField(max_length=100, unique=True)
    key = models.CharField(max_length=128, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    allowed_ips = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "etl_apikey"

    def __str__(self):
        return f"{self.name} ({'ativo' if self.is_active else 'inativo'})"

    @staticmethod
    def generate_key():
        """Gera uma nova chave segura."""
        return secrets.token_hex(32)
