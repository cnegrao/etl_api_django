import json
import logging
import time

import psycopg2
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from drf_spectacular.utils import OpenApiExample, extend_schema
from psycopg2.extras import execute_values
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import APIKeyAuthentication
from .serializers import BatchSerializer

# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# BULK CONFIG
#
# Quantidade máxima de registros enviada em cada comando SQL.
#
# Não significa quantidade máxima permitida no lote.
# Um lote maior será automaticamente dividido em páginas.
# ============================================================

SAMPLE_PAGE_SIZE = 1000
VALUE_PAGE_SIZE = 5000


# ============================================================
# DATABASE
# ============================================================

def get_conn():
    """
    Abre conexão direta com PostgreSQL / Supabase.

    A transação é controlada explicitamente pela view:
        conn.commit()
        conn.rollback()
    """
    return psycopg2.connect(settings.PG_URL)


# ============================================================
# JSON
# ============================================================

def to_json(data) -> str:
    """
    Serializa estruturas Python para JSON compatível com
    PostgreSQL JSON/JSONB.

    DjangoJSONEncoder trata também tipos como date/Decimal.
    """
    return json.dumps(
        data,
        cls=DjangoJSONEncoder,
    )


# ============================================================
# STAGE ID RESERVATION
# ============================================================

def reserve_stage_ids(cur, quantity: int) -> list[int]:
    """
    Reserva antecipadamente IDs da sequence pertencente à PK
    de public.etl_stage_sampleintake.

    Motivo:
    --------
    Precisamos conhecer individualmente o ID de cada sample
    ANTES de inserir os seus valores.

    Dessa forma podemos:

        sample A -> stage_id 1001
        sample B -> stage_id 1002
        sample C -> stage_id 1003

    e depois construir todos os registros de values em memória.

    Isso elimina a necessidade de:

        INSERT sample RETURNING id

    para cada sample individualmente.

    IMPORTANTE:
    Sequence PostgreSQL não sofre rollback.
    Portanto, em caso de rollback podem existir gaps nos IDs.
    Isso é comportamento normal e correto do PostgreSQL.
    """

    if quantity <= 0:
        return []

    cur.execute(
        """
        SELECT nextval(
            pg_get_serial_sequence(
                'public.etl_stage_sampleintake',
                'id'
            )
        )
        FROM generate_series(1, %s);
        """,
        (quantity,),
    )

    stage_ids = [
        row[0]
        for row in cur.fetchall()
    ]

    # --------------------------------------------------------
    # Sanidade / proteção
    # --------------------------------------------------------

    if len(stage_ids) != quantity:
        raise RuntimeError(
            "Quantidade de IDs reservados diferente da "
            "quantidade de samples. "
            f"Esperado={quantity}, "
            f"Recebido={len(stage_ids)}"
        )

    if any(stage_id is None for stage_id in stage_ids):
        raise RuntimeError(
            "Não foi possível obter a sequence da coluna "
            "public.etl_stage_sampleintake.id."
        )

    if len(set(stage_ids)) != quantity:
        raise RuntimeError(
            "A sequence retornou IDs duplicados para "
            "etl_stage_sampleintake."
        )

    return stage_ids


# ============================================================
# BUILD BULK ROWS
# ============================================================

def build_bulk_rows(
    batch_id: int,
    partner_id: int,
    partner_batch_id: str,
    samples,
    stage_ids,
):
    """
    Constrói em memória:

        sample_rows
        value_rows

    Cada sample já recebe antecipadamente seu stage_id.

    Retorna também a quantidade de samples que vieram sem
    resultados.
    """

    if len(samples) != len(stage_ids):
        raise RuntimeError(
            "Quantidade de samples diferente da quantidade "
            "de stage_ids."
        )

    sample_rows = []
    value_rows = []

    samples_without_results = 0

    for stage_id, sample in zip(stage_ids, samples):

        # ====================================================
        # SAMPLE
        # ====================================================

        sample_rows.append(
            (
                stage_id,
                batch_id,
                partner_id,
                partner_batch_id,

                sample.get("partner_record_id"),

                sample["sample_code"],
                sample.get("sampling_date"),
                sample["year"],
                sample.get("stage", 1),
                sample.get("lab_number"),

                sample.get("company_external_code"),
                sample.get("laboratory_external_code"),
                sample.get("trial_external_code"),

                to_json(sample.get("extra")),
                to_json(sample),
            )
        )

        # ====================================================
        # RESULTS
        # ====================================================

        results = sample.get("results", [])

        if not results:
            samples_without_results += 1

            logger.warning(
                "Amostra sem resultados: sample_code=%s",
                sample.get("sample_code"),
            )

            continue

        # ====================================================
        # VALUES
        # ====================================================

        for result in results:

            indicator = result.get("indicator")

            # Preserva a mesma regra da view anterior:
            # resultado sem indicator não é persistido.
            if not indicator:
                continue

            value_rows.append(
                (
                    stage_id,
                    indicator,
                    result.get("method"),
                    result.get("unit"),
                    result.get("value_numeric"),
                    result.get("value_text"),
                    to_json(result.get("extra")),
                )
            )

    return (
        sample_rows,
        value_rows,
        samples_without_results,
    )


# ============================================================
# API
# ============================================================

class ReceiveBatchView(APIView):

    authentication_classes = [APIKeyAuthentication]
    serializer_class = BatchSerializer

    # ========================================================
    # OPENAPI / SWAGGER
    # ========================================================

    @extend_schema(
        request=BatchSerializer,

        responses={

            # ------------------------------------------------
            # Novo batch
            # ------------------------------------------------

            201: {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "example": "RECEIVED",
                    },
                    "internal_batch_id": {
                        "type": "integer",
                        "example": 123,
                    },
                    "partner_batch_id": {
                        "type": "string",
                        "example":
                            "BATCH-TEST-20260313-001",
                    },
                    "received_samples": {
                        "type": "integer",
                        "example": 2,
                    },
                },
            },

            # ------------------------------------------------
            # Batch já recebido
            # ------------------------------------------------

            200: {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "example": "ALREADY_RECEIVED",
                    },
                    "internal_batch_id": {
                        "type": "integer",
                        "example": 123,
                    },
                    "partner_batch_id": {
                        "type": "string",
                        "example":
                            "BATCH-TEST-20260313-001",
                    },
                },
            },

            # ------------------------------------------------
            # Payload inválido
            # ------------------------------------------------

            400: {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "example": "ERROR",
                    },
                    "errors": {
                        "type": "object",
                    },
                },
            },

            # ------------------------------------------------
            # Erro interno
            # ------------------------------------------------

            500: {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "example": "ERROR",
                    },
                    "message": {
                        "type": "string",
                        "example":
                            "Erro interno ao processar lote.",
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

                    "partner_batch_id":
                        "BATCH-TEST-20260313-001",

                    "samples": [

                        # =====================================
                        # SAMPLE 1
                        # =====================================

                        {
                            "partner_record_id":
                                "REC-0001",

                            "sample_code":
                                "SAMPLE-0001",

                            "sampling_date":
                                "2026-03-12",

                            "year":
                                2026,

                            "stage":
                                1,

                            "lab_number":
                                "LAB-12345",

                            "company_external_code":
                                "COMP-001",

                            "laboratory_external_code":
                                "LABEXT-001",

                            "trial_external_code":
                                "TRIAL-001",

                            "extra": {
                                "farm":
                                    "Fazenda Teste",

                                "city":
                                    "Brasília",

                                "source":
                                    "manual_test",
                            },

                            "results": [

                                {
                                    "indicator":
                                        "pH",

                                    "method":
                                        "EPA-9045",

                                    "unit":
                                        "pH",

                                    "value_numeric":
                                        6.5,

                                    "value_text":
                                        "",

                                    "extra": {
                                        "remark":
                                            "resultado preliminar"
                                    },
                                },

                                {
                                    "indicator":
                                        "Organic Matter",

                                    "method":
                                        "Walkley-Black",

                                    "unit":
                                        "%",

                                    "value_numeric":
                                        3.2,

                                    "value_text":
                                        "",

                                    "extra": {
                                        "replicate": 1
                                    },
                                },
                            ],
                        },

                        # =====================================
                        # SAMPLE 2
                        # =====================================

                        {
                            "partner_record_id":
                                "REC-0002",

                            "sample_code":
                                "SAMPLE-0002",

                            "sampling_date":
                                "2026-03-11",

                            "year":
                                2026,

                            "stage":
                                2,

                            "lab_number":
                                "LAB-12346",

                            "company_external_code":
                                "COMP-001",

                            "laboratory_external_code":
                                "LABEXT-001",

                            "trial_external_code":
                                "TRIAL-002",

                            "extra": {
                                "farm":
                                    "Fazenda Teste 2",

                                "city":
                                    "Goiânia",
                            },

                            "results": [

                                {
                                    "indicator":
                                        "Potassium",

                                    "method":
                                        "Mehlich-1",

                                    "unit":
                                        "mg/dm3",

                                    "value_numeric":
                                        82.4,

                                    "value_text":
                                        None,

                                    "extra": {
                                        "status":
                                            "ok"
                                    },
                                },

                                {
                                    "indicator":
                                        "Observation",

                                    "method":
                                        None,

                                    "unit":
                                        None,

                                    "value_numeric":
                                        None,

                                    "value_text":
                                        "Amostra com coloração escura",

                                    "extra": {
                                        "analyst_note":
                                            "texto livre"
                                    },
                                },
                            ],
                        },
                    ],
                },

                request_only=True,
            ),
        ],
    )
    # ========================================================
    # POST
    # ========================================================
    def post(self, request):

        total_started = time.perf_counter()

        # ====================================================
        # 1. VALIDATION
        # ====================================================

        validation_started = time.perf_counter()

        serializer = self.serializer_class(
            data=request.data
        )

        if not serializer.is_valid():

            validation_seconds = (
                time.perf_counter()
                - validation_started
            )

            logger.warning(
                "Payload inválido. validation=%.3fs errors=%s",
                validation_seconds,
                serializer.errors,
            )

            return Response(
                {
                    "status": "ERROR",
                    "errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        validation_seconds = (
            time.perf_counter()
            - validation_started
        )

        data = serializer.validated_data

        partner_id = data["partner_id"]
        partner_batch_id = data["partner_batch_id"]
        samples = data["samples"]

        # ====================================================
        # DATABASE
        # ====================================================

        conn = None

        try:

            # =================================================
            # 2. CONNECTION
            # =================================================

            connection_started = time.perf_counter()

            conn = get_conn()

            # psycopg2 já utiliza False por padrão,
            # mas deixamos explícito porque este endpoint
            # exige atomicidade do lote.
            conn.autocommit = False

            connection_seconds = (
                time.perf_counter()
                - connection_started
            )

            # =================================================
            # CURSOR
            # =================================================

            with conn.cursor() as cur:

                # =============================================
                # 3. BATCH / IDEMPOTENCY
                # =============================================

                batch_started = time.perf_counter()

                # ---------------------------------------------
                # IMPORTANTE
                #
                # Isto NÃO faz UPDATE.
                #
                # Para batch novo:
                #     INSERT normal.
                #
                # Para batch repetido:
                #     DO NOTHING.
                #
                # A constraint:
                #
                # uq_etl_batch_partner_batch
                #
                # é a proteção definitiva contra concorrência.
                # ---------------------------------------------

                cur.execute(
                    """
                    INSERT INTO public.etl_batch (
                        partner_id,
                        partner_batch_id,
                        total_samples,
                        raw_payload
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    ON CONFLICT ON CONSTRAINT
                        uq_etl_batch_partner_batch
                    DO NOTHING
                    RETURNING id;
                    """,
                    (
                        partner_id,
                        partner_batch_id,
                        len(samples),
                        to_json(request.data),
                    ),
                )

                batch_row = cur.fetchone()

                batch_seconds = (
                    time.perf_counter()
                    - batch_started
                )

                # =============================================
                # 3.1 BATCH JÁ EXISTE
                # =============================================

                if batch_row is None:

                    duplicate_lookup_started = (
                        time.perf_counter()
                    )

                    cur.execute(
                        """
                        SELECT id
                        FROM public.etl_batch
                        WHERE partner_id = %s
                        AND partner_batch_id = %s
                        LIMIT 1;
                        """,
                        (
                            partner_id,
                            partner_batch_id,
                        ),
                    )

                    existing_row = cur.fetchone()

                    duplicate_lookup_seconds = (
                        time.perf_counter()
                        - duplicate_lookup_started
                    )

                    if existing_row is None:
                        raise RuntimeError(
                            "Batch entrou em conflito de "
                            "unicidade, mas não foi encontrado "
                            "em public.etl_batch."
                        )

                    existing_batch_id = existing_row[0]

                    # Nenhuma gravação desta request deve ser
                    # persistida.
                    conn.rollback()

                    total_seconds = (
                        time.perf_counter()
                        - total_started
                    )

                    logger.info(
                        "Batch já recebido | "
                        "partner_id=%s | "
                        "partner_batch_id=%s | "
                        "batch_id=%s | "
                        "validation=%.3fs | "
                        "connection=%.3fs | "
                        "batch_check=%.3fs | "
                        "duplicate_lookup=%.3fs | "
                        "total=%.3fs",
                        partner_id,
                        partner_batch_id,
                        existing_batch_id,
                        validation_seconds,
                        connection_seconds,
                        batch_seconds,
                        duplicate_lookup_seconds,
                        total_seconds,
                    )

                    return Response(
                        {
                            "status":
                                "ALREADY_RECEIVED",

                            "internal_batch_id":
                                existing_batch_id,

                            "partner_batch_id":
                                partner_batch_id,
                        },
                        status=status.HTTP_200_OK,
                    )

                # =============================================
                # BATCH NOVO
                # =============================================

                batch_id = batch_row[0]

                logger.info(
                    "Lote criado: %s (%s)",
                    batch_id,
                    partner_batch_id,
                )

                # =============================================
                # 4. RESERVA IDS DOS SAMPLES
                # =============================================

                reserve_started = time.perf_counter()

                stage_ids = reserve_stage_ids(
                    cur,
                    len(samples),
                )

                reserve_seconds = (
                    time.perf_counter()
                    - reserve_started
                )

                # =============================================
                # 5. MONTA REGISTROS EM MEMÓRIA
                # =============================================

                build_started = time.perf_counter()

                (
                    sample_rows,
                    value_rows,
                    samples_without_results,
                ) = build_bulk_rows(
                    batch_id=batch_id,
                    partner_id=partner_id,
                    partner_batch_id=partner_batch_id,
                    samples=samples,
                    stage_ids=stage_ids,
                )

                build_seconds = (
                    time.perf_counter()
                    - build_started
                )

                # =============================================
                # 6. BULK INSERT SAMPLES
                # =============================================

                sample_bulk_started = (
                    time.perf_counter()
                )

                if sample_rows:

                    execute_values(
                        cur,
                        """
                        INSERT INTO
                            public.etl_stage_sampleintake
                        (
                            id,
                            batch_id,
                            partner_id,
                            partner_batch_id,
                            partner_record_id,
                            sample_code,
                            sampling_date,
                            year,
                            stage,
                            lab_number,
                            company_external_code,
                            laboratory_external_code,
                            trial_external_code,
                            extra,
                            raw_payload
                        )
                        VALUES %s;
                        """,
                        sample_rows,
                        page_size=SAMPLE_PAGE_SIZE,
                    )

                sample_bulk_seconds = (
                    time.perf_counter()
                    - sample_bulk_started
                )

                # =============================================
                # 7. BULK INSERT VALUES
                # =============================================

                value_bulk_started = (
                    time.perf_counter()
                )

                if value_rows:

                    execute_values(
                        cur,
                        """
                        INSERT INTO
                            public.etl_stage_sampleintake_value
                        (
                            stage_sampleintake_id,
                            indicator,
                            method,
                            unit,
                            value_numeric,
                            value_text,
                            extra
                        )
                        VALUES %s;
                        """,
                        value_rows,
                        page_size=VALUE_PAGE_SIZE,
                    )

                value_bulk_seconds = (
                    time.perf_counter()
                    - value_bulk_started
                )

            # =================================================
            # 8. COMMIT
            # =================================================

            commit_started = time.perf_counter()

            conn.commit()

            commit_seconds = (
                time.perf_counter()
                - commit_started
            )

            # =================================================
            # 9. TOTAL / LOG PERFORMANCE
            # =================================================

            total_seconds = (
                time.perf_counter()
                - total_started
            )

            logger.info(
                "Batch processado | "
                "batch_id=%s | "
                "partner_batch_id=%s | "
                "samples=%s | "
                "values=%s | "
                "samples_sem_resultados=%s | "
                "validation=%.3fs | "
                "connection=%.3fs | "
                "batch_insert=%.3fs | "
                "reserve_ids=%.3fs | "
                "build_rows=%.3fs | "
                "samples_bulk=%.3fs | "
                "values_bulk=%.3fs | "
                "commit=%.3fs | "
                "total=%.3fs",
                batch_id,
                partner_batch_id,
                len(sample_rows),
                len(value_rows),
                samples_without_results,
                validation_seconds,
                connection_seconds,
                batch_seconds,
                reserve_seconds,
                build_seconds,
                sample_bulk_seconds,
                value_bulk_seconds,
                commit_seconds,
                total_seconds,
            )

            # =================================================
            # 10. RESPONSE
            # =================================================

            return Response(
                {
                    "status": "RECEIVED",
                    "internal_batch_id": batch_id,
                    "partner_batch_id":
                        partner_batch_id,
                    "received_samples":
                        len(sample_rows),
                },
                status=status.HTTP_201_CREATED,
            )

        # ====================================================
        # ERROR
        # ====================================================

        except Exception as exc:

            if conn:
                try:
                    conn.rollback()
                except Exception:
                    logger.exception(
                        "Erro adicional durante rollback."
                    )

            total_seconds = (
                time.perf_counter()
                - total_started
            )

            # Detalhe completo fica no servidor.
            logger.exception(
                "Erro ao processar lote ETL | "
                "partner_id=%s | "
                "partner_batch_id=%s | "
                "elapsed=%.3fs",
                partner_id,
                partner_batch_id,
                total_seconds,
            )

            # Durante desenvolvimento é útil receber detalhe.
            # Em produção não devemos expor erro interno do DB.
            if settings.DEBUG:
                message = (
                    f"Erro inesperado: {str(exc)}"
                )
            else:
                message = (
                    "Erro interno ao processar lote."
                )

            return Response(
                {
                    "status": "ERROR",
                    "message": message,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ====================================================
        # CLOSE CONNECTION
        # ====================================================

        finally:

            if conn:

                try:
                    conn.close()

                except Exception:
                    logger.exception(
                        "Erro ao fechar conexão PostgreSQL."
                    )
