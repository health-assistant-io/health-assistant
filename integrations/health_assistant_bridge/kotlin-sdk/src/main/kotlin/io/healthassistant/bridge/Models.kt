package io.healthassistant.bridge

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// --- Read envelope (the universal read-path wrapper) ---

@Serializable
data class ReadEnvelope<T>(
    val data: List<T> = emptyList(),
    val cursor: String? = null,
    @SerialName("cached_at") val cachedAt: String? = null,
)

// --- Push/sync payload models ---

@Serializable
data class BridgeStatus(
    val status: String,
    @SerialName("integration_id") val integrationId: String? = null,
    @SerialName("last_synced_at") val lastSyncedAt: String? = null,
    val cursor: String? = null,
    @SerialName("latest_sdks") val latestSdks: Map<String, String>? = null,
    /** The server's frontend/PWA origin (e.g. `http://host:3000`). May be
     *  loopback (`localhost:PORT`) — the caller rewrites it to the connection's
     *  reachable host. Null on older servers that don't advertise it. */
    @SerialName("frontend_base_url") val frontendBaseUrl: String? = null,
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

@Serializable
data class DocumentSummary(
    val id: String,
    val filename: String,
    val status: String? = null,
    val progress: Int? = null,
    @SerialName("external_id") val externalId: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    /** MIME guessed from the filename (`null` if unknown). */
    @SerialName("content_type") val contentType: String? = null,
    /** Size of the stored file in bytes (`null` if the file is missing). */
    @SerialName("file_size") val fileSize: Long? = null,
    /** Owning examination id (`null` for orphan documents). */
    @SerialName("examination_id") val examinationId: String? = null,
)

// --- Read-path models (mirror the bridge's wire shapes) ---

/** A biomarker definition row from `GET /biomarkers`. */
@Serializable
data class BiomarkerSummary(
    val id: String,
    val name: String,
    val slug: String? = null,
    val code: String? = null,
    @SerialName("coding_system") val codingSystem: String? = null,
    val unit: String? = null,
    @SerialName("is_telemetry") val isTelemetry: Boolean = false,
    @SerialName("reference_range_min") val referenceRangeMin: Double? = null,
    @SerialName("reference_range_max") val referenceRangeMax: Double? = null,
    @SerialName("value_type") val valueType: String? = null,
    /** Markdown education text about the biomarker (LLM-generated). */
    val info: String? = null,
)

/** An observation row from `GET /observations` / `GET /observations/latest`.
 *  FHIR + telemetry rows share the same shape (the server normalizes). */
@Serializable
data class ObservationPoint(
    val id: String? = null,
    @SerialName("effective_datetime") val effectiveDatetime: String? = null,
    /** The FHIR code object: `{coding:[{system,code}],text}`. */
    val code: CodeableConcept? = null,
    @SerialName("value_quantity") val valueQuantity: Quantity? = null,
    @SerialName("value_string") val valueString: String? = null,
    @SerialName("value_codeable_concept") val valueCodeableConcept: CodeableConcept? = null,
    @SerialName("reference_range") val referenceRange: ReferenceRange? = null,
    val interpretation: String? = null,
    @SerialName("biomarker_id") val biomarkerId: String? = null,
    @SerialName("biomarker_slug") val biomarkerSlug: String? = null,
    @SerialName("biomarker_value_type") val biomarkerValueType: String? = null,
    @SerialName("raw_value") val rawValue: Double? = null,
    @SerialName("normalized_value") val normalizedValue: Double? = null,
    @SerialName("normalized_unit") val normalizedUnit: String? = null,
    @SerialName("relative_score") val relativeScore: Double? = null,
    @SerialName("examination_id") val examinationId: String? = null,
    @SerialName("document_id") val documentId: String? = null,
)

/** A FHIR `{coding:[{system,code,display}],text}` shape — used by
 *  [ObservationPoint.code] and [ObservationPoint.valueCodeableConcept]. */
@Serializable
data class CodeableConcept(
    val coding: List<Coding> = emptyList(),
    val text: String? = null,
)

@Serializable
data class Coding(
    val system: String? = null,
    val code: String? = null,
    val display: String? = null,
)

/** A FHIR `{value,unit,system,code}` quantity. */
@Serializable
data class Quantity(
    val value: Double? = null,
    val unit: String? = null,
    val system: String? = null,
    val code: String? = null,
)

/** An examination row from `GET /examinations` (list) or `GET /examinations/{id}`
 *  (detail — the detail response also carries `diagnoses` + `impressions`). */
@Serializable
data class ExaminationSummary(
    val id: String,
    @SerialName("examination_date") val examinationDate: String? = null,
    val notes: String? = null,
    @SerialName("patient_notes") val patientNotes: String? = null,
    @SerialName("extraction_status") val extractionStatus: String? = null,
    val diagnoses: List<String>? = null,
    val impressions: String? = null,
    val category: String? = null,
    @SerialName("lab_name") val labName: String? = null,
    @SerialName("external_id") val externalId: String? = null,
)

/** `GET /documents/{id}/text` — the OCR-extracted Markdown of a document,
 *  capped at 512 KiB server-side (`truncated` flags the cut). */
@Serializable
data class DocumentText(
    val id: String,
    @SerialName("extracted_text") val extractedText: String? = null,
    val status: String? = null,
    val truncated: Boolean = false,
)

// --- Phase 3: clinical-record read models ---

/** A patient medication from `GET /medications`. The `code` field is the FHIR
 *  CodeableConcept (text + optional catalog_id). */
@Serializable
data class Medication(
    val id: String,
    val status: String? = null,
    val intent: String? = null,
    val code: Map<String, kotlinx.serialization.json.JsonElement> = emptyMap(),
    @SerialName("start_date") val startDate: String? = null,
    @SerialName("end_date") val endDate: String? = null,
    val dosage: String? = null,
    val frequency: kotlinx.serialization.json.JsonElement? = null,
    val reason: String? = null,
    val note: String? = null,
    @SerialName("examination_id") val examinationId: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/** A patient allergy from `GET /allergies`. */
@Serializable
data class Allergy(
    val id: String,
    @SerialName("clinical_status") val clinicalStatus: String? = null,
    @SerialName("verification_status") val verificationStatus: String? = null,
    val category: String? = null,
    val criticality: String? = null,
    val code: Map<String, kotlinx.serialization.json.JsonElement> = emptyMap(),
    @SerialName("onset_date") val onsetDate: String? = null,
    @SerialName("resolved_date") val resolvedDate: String? = null,
    @SerialName("last_occurrence") val lastOccurrence: String? = null,
    val note: String? = null,
    val reactions: kotlinx.serialization.json.JsonElement? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/** A patient immunization from `GET /vaccines`. */
@Serializable
data class Vaccine(
    val id: String,
    val status: String? = null,
    @SerialName("vaccine_code") val vaccineCode: Map<String, kotlinx.serialization.json.JsonElement> = emptyMap(),
    @SerialName("administered_at") val administeredAt: String? = null,
    @SerialName("dose_number") val doseNumber: String? = null,
    @SerialName("lot_number") val lotNumber: String? = null,
    val manufacturer: String? = null,
    val location: String? = null,
    val note: String? = null,
    @SerialName("examination_id") val examinationId: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/** A flat clinical-event list item from `GET /clinical-events`. The detail
 *  endpoint `GET /clinical-events/{id}` returns the rich `to_dict()` shape
 *  with type_details/observations/examinations/anatomy_links — decode that
 *  with `kotlinx.serialization.json.JsonElement` or a per-field decoder. */
@Serializable
data class ClinicalEvent(
    val id: String,
    @SerialName("patient_id") val patientId: String? = null,
    @SerialName("type_id") val typeId: String? = null,
    @SerialName("type_name") val typeName: String? = null,
    @SerialName("type_slug") val typeSlug: String? = null,
    @SerialName("type_icon") val typeIcon: String? = null,
    @SerialName("type_color") val typeColor: String? = null,
    val status: String? = null,
    val title: String? = null,
    val description: String? = null,
    @SerialName("onset_date") val onsetDate: String? = null,
    @SerialName("resolved_date") val resolvedDate: String? = null,
    @SerialName("coding_system") val codingSystem: String? = null,
    val code: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/** A doctor (tenant address book) from `GET /doctors`. */
@Serializable
data class Doctor(
    val id: String,
    val name: String,
    @SerialName("specialty_concept_id") val specialtyConceptId: String? = null,
    val specialty: String? = null,
    @SerialName("license_number") val licenseNumber: String? = null,
    val email: String? = null,
    val phone: String? = null,
    val telecom: kotlinx.serialization.json.JsonElement? = null,
    val address: kotlinx.serialization.json.JsonElement? = null,
    @SerialName("office_number") val officeNumber: String? = null,
    @SerialName("office_details") val officeDetails: String? = null,
)

// =========================================================================
// Phase 6: mutation ack shapes (returned by POST/PUT/DELETE on clinical records)
// =========================================================================

/** Generic ack returned by DELETE paths (`{"id": "...", "deleted": true,
 *  "message": "..."}`). */
@Serializable
data class DeleteResult(
    val id: String? = null,
    val deleted: Boolean = true,
    val message: String? = null,
)

// =========================================================================
// Phase 7: notifications (inbox + preferences + triggers)
// =========================================================================

/** One row in the owner's inbox. The full notification envelope is nested
 *  under [notification]; the per-recipient state (read/dismissed) lives at
 *  the top level. */
@Serializable
data class NotificationItem(
    @SerialName("recipient_id") val recipientId: String,
    val status: String? = null,
    @SerialName("read_at") val readAt: String? = null,
    @SerialName("dismissed_at") val dismissedAt: String? = null,
    val notification: NotificationEnvelope? = null,
)

/** The immutable notification event nested inside an inbox item. Carries the
 *  title/body/payload the mobile app renders. Fields beyond the common ones
 *  are accessible via [raw] when needed (e.g. source_ref, payload). */
@Serializable
data class NotificationEnvelope(
    val id: String,
    val title: String,
    val body: String? = null,
    val type: String? = null,
    val category: String? = null,
    val severity: String? = null,
    val source: String? = null,
    val payload: kotlinx.serialization.json.JsonElement? = null,
    @SerialName("patient_id") val patientId: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
)

/** `GET /notifications/unread-count` response. */
@Serializable
data class UnreadCount(
    @SerialName("unread_count") val unreadCount: Int,
)

/** `POST /notifications/read-all` response. */
@Serializable
data class ReadAllResult(
    val status: String,
    @SerialName("marked_read") val markedRead: Int = 0,
)

/** Generic `{"status": "success"}` ack (mark-read / mark-dismissed / etc). */
@Serializable
data class StatusAck(val status: String)

/** One entry in the preferences hub listing. */
@Serializable
data class NotificationKind(
    @SerialName("kind_id") val kindId: String,
    val label: String? = null,
    val mutable: Boolean = true,
    val group: String? = null,
    val enabled: Boolean? = null,
)

/** `PUT /notifications/preferences/{kind_id}` ack. */
@Serializable
data class PreferenceToggleResult(
    val status: String,
    @SerialName("kind_id") val kindId: String,
    val enabled: Boolean,
    val label: String? = null,
    val mutable: Boolean? = null,
)

/** A biomarker-threshold trigger (`GET/POST /notifications/triggers`). */
@Serializable
data class NotificationTrigger(
    val id: String,
    @SerialName("rule_type") val ruleType: String,
    @SerialName("biomarker_id") val biomarkerId: String? = null,
    val operator: String? = null,
    val value: Double? = null,
    val severity: String? = null,
    val enabled: Boolean = true,
    @SerialName("cooldown_minutes") val cooldownMinutes: Int? = null,
    @SerialName("title_template") val titleTemplate: String? = null,
    @SerialName("body_template") val bodyTemplate: String? = null,
)

// =========================================================================
// Phase 8: native push device registration
// =========================================================================

/** A registered mobile push target (`GET /devices`). Endpoint URLs are
 *  masked server-side — this shape is for the 'Where am I signed in' list,
 *  never for re-use as a delivery target. */
@Serializable
data class MobilePushTarget(
    val id: String,
    @SerialName("user_id") val userId: String,
    @SerialName("device_id") val deviceId: String,
    val platform: String,
    @SerialName("endpoint_url") val endpointUrlMasked: String,
    @SerialName("encryption_pubkey") val encryptionPubkey: String? = null,
    @SerialName("app_version") val appVersion: String? = null,
    @SerialName("user_agent") val userAgent: String? = null,
    @SerialName("is_active") val isActive: Boolean = true,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/** `POST /notifications/register-device` payload. Build with the
 *  [registerDevice] helper (recommended) or directly when you need full
 *  control. */
@Serializable
data class DeviceRegistration(
    @SerialName("device_id") val deviceId: String,
    val platform: String, // "unifiedpush" | "fcm"
    @SerialName("endpoint_url") val endpointUrl: String,
    @SerialName("encryption_pubkey") val encryptionPubkey: String? = null,
    @SerialName("app_version") val appVersion: String? = null,
    @SerialName("user_agent") val userAgent: String? = null,
)
