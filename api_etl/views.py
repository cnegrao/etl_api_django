import json
import logging

import psycopg2
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
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
    from rest_framework.permissions import AllowAny
    permission_classes = [AllowAny]

    # permission_classes = [IsAuthenticated]
    """
    Endpoint principal: POST /api/v1/lab-intake/batch/
    Recebe um lote de amostras e grava em:
        - etl_batch
        - etl_stage_sampleintake
        - etl_stage_sampleintake_value
    """

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
