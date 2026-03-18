import json
import logging

import psycopg2
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import APIKeyAuthentication
from .serializers import BatchSerializer

# -------------------------------------------------------
# LOGGING CONFIG
# -------------------------------------------------------
logger = logging.getLogger(__name__)


def get_conn():
    """
    Abre conexão com o PostgreSQL (Supabase / destino)
    """
    return psycopg2.connect(settings.PG_URL)


def to_json(data) -> str:
    """
    Serializa qualquer objeto Python em JSON seguro
    """
    return json.dumps(data, cls=DjangoJSONEncoder)


class ReceiveBatchView(APIView):
    authentication_classes = [APIKeyAuthentication]
    serializer_class = BatchSerializer

    @extend_schema(
        request=BatchSerializer,
        responses={
            201: {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "example": "RECEIVED"},
                    "internal_batch_id": {"type": "integer", "example": 123},
                    "partner_batch_id": {
                        "type": "string",
                        "example": "BATCH-TEST-20260313-001"
                    },
                    "received_samples": {"type": "integer", "example": 2},
                },
            },
            400: {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "example": "ERROR"},
                    "errors": {"type": "object"},
                },
            },
            500: {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "example": "ERROR"},
                    "message": {
                        "type": "string",
                        "example": "Erro inesperado: detalhe do erro"
                    },
                },
            },
        },
        examples=[
            OpenApiExample(
                name="Exemplo de payload batch",
                summary="Exemplo completo de envio",
                value={
                    "partner_id": 10,
                    "partner_batch_id": "BATCH-TEST-20260313-001",
                    "samples": [
                        {
                            "partner_record_id": "REC-0001",
                            "sample_code": "SAMPLE-0001",
                            "sampling_date": "2026-03-12",
                            "year": 2026,
                            "stage": 1,
                            "lab_number": "LAB-12345",
                            "company_external_code": "COMP-001",
                            "laboratory_external_code": "LABEXT-001",
                            "trial_external_code": "TRIAL-001",
                            "extra": {
                                "farm": "Fazenda Teste",
                                "city": "Brasília",
                                "source": "manual_test"
                            },
                            "results": [
                                {
                                    "indicator": "pH",
                                    "method": "EPA-9045",
                                    "unit": "pH",
                                    "value_numeric": 6.5,
                                    "value_text": "",
                                    "extra": {
                                        "remark": "resultado preliminar"
                                    }
                                },
                                {
                                    "indicator": "Organic Matter",
                                    "method": "Walkley-Black",
                                    "unit": "%",
                                    "value_numeric": 3.2,
                                    "value_text": "",
                                    "extra": {
                                        "replicate": 1
                                    }
                                }
                            ]
                        },
                        {
                            "partner_record_id": "REC-0002",
                            "sample_code": "SAMPLE-0002",
                            "sampling_date": "2026-03-11",
                            "year": 2026,
                            "stage": 2,
                            "lab_number": "LAB-12346",
                            "company_external_code": "COMP-001",
                            "laboratory_external_code": "LABEXT-001",
                            "trial_external_code": "TRIAL-002",
                            "extra": {
                                "farm": "Fazenda Teste 2",
                                "city": "Goiânia"
                            },
                            "results": [
                                {
                                    "indicator": "Potassium",
                                    "method": "Mehlich-1",
                                    "unit": "mg/dm3",
                                    "value_numeric": 82.4,
                                    "value_text": None,
                                    "extra": {
                                        "status": "ok"
                                    }
                                },
                                {
                                    "indicator": "Observation",
                                    "method": None,
                                    "unit": None,
                                    "value_numeric": None,
                                    "value_text": "Amostra com coloração escura",
                                    "extra": {
                                        "analyst_note": "texto livre"
                                    }
                                }
                            ]
                        }
                    ]
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        serializer = BatchSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"Payload inválido: {serializer.errors}")
            return Response(
                {"status": "ERROR", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        partner_id = data["partner_id"]
        partner_batch_id = data["partner_batch_id"]
        samples = data["samples"]

        conn = None
        try:
            conn = get_conn()
            cur = conn.cursor()

            # -------------------------------------------------------
            # 1️⃣ Cria registro do batch
            # -------------------------------------------------------
            cur.execute(
                """
                INSERT INTO etl_batch (partner_id, partner_batch_id, total_samples, raw_payload)
                VALUES (%s, %s, %s, %s)
                RETURNING id;
                """,
                (partner_id, partner_batch_id, len(
                    samples), to_json(request.data)),
            )
            batch_id = cur.fetchone()[0]
            logger.info(f"Lote criado: {batch_id} ({partner_batch_id})")

            # -------------------------------------------------------
            # 2️⃣ Insere cada amostra e seus valores
            # -------------------------------------------------------
            for sample in samples:
                cur.execute(
                    """
                    INSERT INTO etl_stage_sampleintake (
                        batch_id, partner_id, partner_batch_id, partner_record_id,
                        sample_code, sampling_date, year, stage, lab_number,
                        company_external_code, laboratory_external_code, trial_external_code,
                        extra, raw_payload
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id;
                    """,
                    (
                        batch_id,
                        partner_id,
                        partner_batch_id,
                        sample.get("partner_record_id"),
                        sample["sample_code"],
                        sample.get("sampling_date"),
                        sample["year"],
                        sample["stage"],
                        sample.get("lab_number"),
                        sample.get("company_external_code"),
                        sample.get("laboratory_external_code"),
                        sample.get("trial_external_code"),
                        to_json(sample.get("extra")),
                        to_json(sample),
                    ),
                )
                stage_id = cur.fetchone()[0]

                results = sample.get("results", [])
                if not results:
                    logger.warning(
                        f"Amostra sem resultados: {sample.get('sample_code')}")
                    continue

                # -------------------------------------------------------
                # 3️⃣ Insere valores analíticos
                # -------------------------------------------------------
                cur.executemany(
                    """
                    INSERT INTO etl_stage_sampleintake_value (
                        stage_sampleintake_id, indicator, method, unit,
                        value_numeric, value_text, extra
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s);
                    """,
                    [
                        (
                            stage_id,
                            v.get("indicator"),
                            v.get("method"),
                            v.get("unit"),
                            v.get("value_numeric"),
                            v.get("value_text"),
                            to_json(v.get("extra")),
                        )
                        for v in results
                        if v.get("indicator")
                    ],
                )

            conn.commit()

            return Response(
                {
                    "status": "RECEIVED",
                    "internal_batch_id": batch_id,
                    "partner_batch_id": partner_batch_id,
                    "received_samples": len(samples),
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.exception("Erro ao processar lote ETL")
            if conn:
                conn.rollback()
            return Response(
                {"status": "ERROR", "message": f"Erro inesperado: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        finally:
            if conn:
                cur.close()
                conn.close()
