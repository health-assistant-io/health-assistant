package io.healthassistant.bridge

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class BridgeStatus(
    val status: String,
    @SerialName("integration_id") val integrationId: String? = null,
    @SerialName("last_synced_at") val lastSyncedAt: String? = null,
    val cursor: String? = null,
    @SerialName("latest_sdks") val latestSdks: Map<String, String>? = null,
)

@Serializable
data class MetricMappingRequest(
    val name: String,
    val code: String? = null,
)

@Serializable
data class MapRequestPayload(
    @SerialName("unmapped_metrics") val unmappedMetrics: List<MetricMappingRequest>,
)

@Serializable
data class MappedMetric(
    @SerialName("original_name") val originalName: String,
    val action: String,
    @SerialName("existing_biomarker_id") val existingBiomarkerId: String? = null,
    @SerialName("new_biomarker_name") val newBiomarkerName: String? = null,
    @SerialName("new_biomarker_code") val newBiomarkerCode: String? = null,
    @SerialName("new_biomarker_coding_system") val newBiomarkerCodingSystem: String? = null,
)

@Serializable
data class MapResponsePayload(
    val mappings: List<MappedMetric> = emptyList(),
)

@Serializable
data class ReferenceRange(
    val low: Double? = null,
    val high: Double? = null,
)

@Serializable
data class ClientRecord(
    val type: String,
    @SerialName("biomarker_id") val biomarkerId: String? = null,
    val code: String? = null,
    @SerialName("coding_system") val codingSystem: String = "custom",
    val name: String,
    val value: Double? = null,
    @SerialName("value_string") val valueString: String? = null,
    val unit: String? = null,
    val timestamp: String? = null,
    @SerialName("reference_range") val referenceRange: ReferenceRange? = null,
    val interpretation: String? = null,
    val performer: String? = null,
)

@Serializable
data class ClientExaminationRecord(
    val id: String? = null,
    val date: String? = null,
    @SerialName("lab_name") val labName: String? = null,
    val notes: String? = null,
    @SerialName("patient_notes") val patientNotes: String? = null,
    val category: String? = null,
    val diagnoses: List<String> = emptyList(),
    val impressions: String? = null,
    val records: List<ClientRecord>? = null,
)

@Serializable
data class SyncPayload(
    @SerialName("client_version") val clientVersion: String,
    @SerialName("source_system") val sourceSystem: String,
    val cursor: String? = null,
    val records: List<ClientRecord>? = null,
    val examinations: List<ClientExaminationRecord>? = null,
)

@Serializable
data class SyncResponse(
    val success: Boolean,
    @SerialName("metrics_synced") val metricsSynced: Int? = null,
    val message: String? = null,
    val error: String? = null,
)
