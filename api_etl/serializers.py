from rest_framework import serializers


class SampleResultSerializer(serializers.Serializer):
    indicator = serializers.CharField(max_length=100)
    method = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True)
    unit = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True)
    value_numeric = serializers.FloatField(required=False, allow_null=True)
    value_text = serializers.CharField(
        max_length=200, required=False, allow_null=True, allow_blank=True)
    extra = serializers.JSONField(required=False)


class SampleSerializer(serializers.Serializer):
    partner_record_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True)
    sample_code = serializers.CharField(max_length=60)
    sampling_date = serializers.DateField(required=False, allow_null=True)
    year = serializers.IntegerField()
    stage = serializers.IntegerField(default=1)
    lab_number = serializers.CharField(
        max_length=50, required=True)  # ✅ agora obrigatório!
    company_external_code = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True)
    laboratory_external_code = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True)
    trial_external_code = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True)
    extra = serializers.JSONField(required=False)
    results = SampleResultSerializer(many=True)


class BatchSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    partner_batch_id = serializers.CharField(max_length=100)
    samples = SampleSerializer(many=True)
