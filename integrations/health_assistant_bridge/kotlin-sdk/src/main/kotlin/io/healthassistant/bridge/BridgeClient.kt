package io.healthassistant.bridge

import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.request
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsBytes
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpMethod
import io.ktor.http.contentType
import kotlinx.coroutines.delay
import kotlinx.io.IOException
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import kotlin.random.Random

/**
 * Client for the Health Assistant Bridge two-way API proxy. Mirrors
 * python-sdk/.../async_client.py: pooled client, 30s timeout, full-jitter
 * backoff (cap 8s, 3 attempts) on 429/5xx/network. `/status` is never signed;
 * every other path is HMAC-signed when [apiSecret] is set.
 *
 * Pass a custom [client] (e.g. a MockEngine-backed HttpClient) for tests.
 */
class BridgeClient(
    val baseUrl: String,
    val integrationId: String,
    val apiSecret: String? = null,
    client: HttpClient? = null,
) : AutoCloseable {

    val apiBase: String =
        "${baseUrl.trimEnd('/')}/api/v1/integrations/health_assistant_bridge/api/$integrationId"

    private val http: HttpClient = client ?: HttpClient(CIO) {
        install(HttpTimeout) {
            requestTimeoutMillis = 30_000
            connectTimeoutMillis = 10_000
        }
    }
    private val ownsClient = client == null
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = false }

    /** Connectivity + cursor + SDK discovery. NEVER signed. */
    suspend fun getStatus(): BridgeStatus {
        val text = http.get("$apiBase/status").bodyAsText()
        return json.decodeFromString(BridgeStatus.serializer(), text)
    }

    suspend fun requestMapping(metrics: List<MetricMappingRequest>): MapResponsePayload {
        val body = json.encodeToString(
            MapRequestPayload.serializer(),
            MapRequestPayload(metrics),
        ).encodeToByteArray()
        return decode(send("POST", "/map", body), MapResponsePayload.serializer())
    }

    suspend fun syncData(payload: SyncPayload): SyncResponse {
        val body = json.encodeToString(SyncPayload.serializer(), payload).encodeToByteArray()
        return decode(send("POST", "/sync", body), SyncResponse.serializer())
    }

    /** Generic signed (when secret set) request for the read/management paths. */
    suspend fun request(method: String, path: String, body: ByteArray? = null): HttpResponse =
        send(method, path, body ?: ByteArray(0), attempt = 0)

    /** Status code only — for callers (e.g. the sync sender) that don't need the body. */
    suspend fun statusOf(method: String, path: String, body: ByteArray? = null): Int =
        request(method, path, body).status.value

    /** Body text of a signed (when secret set) request, throwing [BridgeException]
     * on a non-2xx response. Lets the shared layer decode JSON without depending
     * on ktor (HttpResponse stays inside the SDK). */
    suspend fun requestText(method: String, path: String, body: ByteArray? = null): String {
        val resp = request(method, path, body)
        val text = resp.bodyAsText()
        if (resp.status.value !in 200..299) throw BridgeException(resp.status.value, text)
        return text
    }

    /** Body bytes of a signed (when secret set) request, throwing [BridgeException]
     * on a non-2xx response. Used for binary downloads (document content +
     * preview JPEG) — the bridge's content paths return `Response(content=bytes)`
     * which this method materialises. */
    suspend fun requestBytes(method: String, path: String, body: ByteArray? = null): ByteArray {
        val resp = request(method, path, body)
        val bytes = resp.bodyAsBytes()
        if (resp.status.value !in 200..299) throw BridgeException(resp.status.value, bytes.decodeToString())
        return bytes
    }

    /** Convenience: `GET /documents/{id}/content` → raw bytes. */
    suspend fun getDocumentContent(docId: String): ByteArray =
        requestBytes("GET", "/documents/$docId/content")

    /** Convenience: `GET /documents/{id}/preview?page=N` → JPEG bytes (or the
     *  original bytes for image files). `page` defaults to 0. */
    suspend fun getDocumentPreview(docId: String, page: Int? = null): ByteArray =
        requestBytes("GET", "/documents/$docId/preview" + (page?.let { "?page=$it" } ?: ""))

    /** Convenience: `GET /documents/{id}/text` — the OCR-extracted Markdown. */
    suspend fun getDocumentText(docId: String): DocumentText =
        readDetail("/documents/$docId/text", DocumentText.serializer())

    // --- Typed readers (Phase 2) --------------------------------------

    /** Generic helper: GET a path, decode the response as a [ReadEnvelope] of
     *  the given element type. Pass the element [serializer] explicitly so the
     *  envelope's generic parameter resolves at the call site. */
    suspend fun <T> readEnvelope(path: String, serializer: KSerializer<T>): ReadEnvelope<T> {
        val text = requestText("GET", path)
        return json.decodeFromString(ReadEnvelope.serializer(serializer), text)
    }

    /** Generic helper: GET a path, decode the response as a single object of
     *  type [T]. Use for detail endpoints (`/examinations/{id}`) that return
     *  one row, not a list. */
    suspend fun <T> readDetail(path: String, serializer: KSerializer<T>): T {
        val text = requestText("GET", path)
        return json.decodeFromString(serializer, text)
    }

    /** `GET /observations/latest` — one row per biomarker, newest first. */
    suspend fun getObservationsLatest(limit: Int? = null): ReadEnvelope<ObservationPoint> =
        readEnvelope("/observations/latest" + (limit?.let { "?limit=$it" } ?: ""), ObservationPoint.serializer())

    /** `GET /observations?biomarker=&since=&until=&limit=` — time series for
     *  one biomarker (LOINC/SNOMED code **or** slug). */
    suspend fun getObservations(
        biomarker: String,
        since: String? = null,
        until: String? = null,
        limit: Int? = null,
    ): ReadEnvelope<ObservationPoint> {
        val params = buildList {
            add("biomarker=${biomarker}")
            since?.let { add("since=$it") }
            until?.let { add("until=$it") }
            limit?.let { add("limit=$it") }
        }
        return readEnvelope("/observations?" + params.joinToString("&"), ObservationPoint.serializer())
    }

    /** `GET /biomarkers?limit=` — the patient's catalog (tenant + global). */
    suspend fun getBiomarkers(limit: Int? = null): ReadEnvelope<BiomarkerSummary> =
        readEnvelope("/biomarkers" + (limit?.let { "?limit=$it" } ?: ""), BiomarkerSummary.serializer())

    /** `GET /examinations?limit=` — the patient's exams (newest first). */
    suspend fun getExaminations(limit: Int? = null): ReadEnvelope<ExaminationSummary> =
        readEnvelope("/examinations" + (limit?.let { "?limit=$it" } ?: ""), ExaminationSummary.serializer())

    /** `GET /examinations/{id}` — one exam's detail (notes, diagnoses, impressions). */
    suspend fun getExamination(id: String): ExaminationSummary =
        readDetail("/examinations/$id", ExaminationSummary.serializer())

    /** `GET /examinations/{id}/documents` — documents attached to an exam. */
    suspend fun listDocumentsForExam(examId: String): ReadEnvelope<DocumentSummary> =
        readEnvelope("/examinations/$examId/documents", DocumentSummary.serializer())

    /** `GET /documents?examination_id=&limit=` — patient-wide document list
     *  (Phase 1). Optional [examinationId] narrows to one exam. */
    suspend fun listDocuments(examinationId: String? = null, limit: Int? = null): ReadEnvelope<DocumentSummary> {
        val params = buildList {
            examinationId?.let { add("examination_id=$it") }
            limit?.let { add("limit=$it") }
        }
        val qs = if (params.isEmpty()) "" else "?" + params.joinToString("&")
        return readEnvelope("/documents$qs", DocumentSummary.serializer())
    }

    // --- Phase 3: clinical-record typed readers -----------------------

    /** `GET /medications?limit=` — the bound patient's medication instances. */
    suspend fun getMedications(limit: Int? = null): ReadEnvelope<Medication> =
        readEnvelope("/medications" + (limit?.let { "?limit=$it" } ?: ""), Medication.serializer())

    /** `GET /allergies?active=&limit=` — the bound patient's allergies.
     *  `active` defaults to true (server-side); pass `false` to include
     *  resolved/inactive history. */
    suspend fun getAllergies(active: Boolean? = null, limit: Int? = null): ReadEnvelope<Allergy> {
        val params = buildList {
            active?.let { add("active=$it") }
            limit?.let { add("limit=$it") }
        }
        val qs = if (params.isEmpty()) "" else "?" + params.joinToString("&")
        return readEnvelope("/allergies$qs", Allergy.serializer())
    }

    /** `GET /vaccines?limit=` — the bound patient's immunizations. */
    suspend fun getVaccines(limit: Int? = null): ReadEnvelope<Vaccine> =
        readEnvelope("/vaccines" + (limit?.let { "?limit=$it" } ?: ""), Vaccine.serializer())

    /** `GET /clinical-events?status=&limit=` — flat list (no nested relations).
     *  Use [getClinicalEvent] for the full nested detail. */
    suspend fun getClinicalEvents(status: String? = null, limit: Int? = null): ReadEnvelope<ClinicalEvent> {
        val params = buildList {
            status?.let { add("status=$it") }
            limit?.let { add("limit=$it") }
        }
        val qs = if (params.isEmpty()) "" else "?" + params.joinToString("&")
        return readEnvelope("/clinical-events$qs", ClinicalEvent.serializer())
    }

    /** `GET /clinical-events/{id}` — full nested detail (type_details +
     *  examinations + observations + anatomy_links). Decode the rich shape
     *  from the returned JSON via kotlinx.serialization or read it as a
     *  generic `JsonElement`. */
    suspend fun getClinicalEventRaw(id: String): String =
        requestText("GET", "/clinical-events/$id")

    /** `GET /doctors?limit=` — the bound owner's tenant-wide doctor address book. */
    suspend fun getDoctors(limit: Int? = null): ReadEnvelope<Doctor> =
        readEnvelope("/doctors" + (limit?.let { "?limit=$it" } ?: ""), Doctor.serializer())

    /** `GET /changes?since=&types=&limit=` — unified delta for two-way
     *  incremental sync. Returns the raw JSON string (the response shape is
     *  `{data: {medications: [...], allergies: [...], ...}, cursor, cached_at, since}`,
     *  not the standard `ReadEnvelope<List<T>>`, so the caller decodes the
     *  per-type arrays from `JsonObject`). Pass the returned `cursor` as the
     *  next poll's `since`; a null cursor means nothing changed. */
    suspend fun getChangesRaw(since: String? = null, types: List<String>? = null, limit: Int? = null): String {
        val params = buildList {
            since?.let { add("since=$it") }
            types?.takeIf { it.isNotEmpty() }?.let { add("types=${it.joinToString(",")}") }
            limit?.let { add("limit=$it") }
        }
        val qs = if (params.isEmpty()) "" else "?" + params.joinToString("&")
        return requestText("GET", "/changes$qs")
    }

    // --- Phase 6: clinical-record mutations ---------------------------
    //
    // Every mutation takes a free-form [JsonObject] payload (built via
    // kotlinx.serialization.json.buildJsonObject). The typed response is
    // decoded from the server's reply, so the caller gets back the same
    // shape as the corresponding reader.

    /** `POST /medications` — create a medication. Idempotent on
     *  `id` / `client_request_id` in the payload (forwarded as external_id). */
    suspend fun createMedication(payload: JsonObject): Medication =
        decodeMutate("POST", "/medications", payload, Medication.serializer())

    /** `PUT /medications/{id}` — update fields on an existing medication. */
    suspend fun updateMedication(id: String, payload: JsonObject): Medication =
        decodeMutate("PUT", "/medications/$id", payload, Medication.serializer())

    /** `DELETE /medications/{id}` — soft-delete (filtered out of subsequent reads). */
    suspend fun deleteMedication(id: String): DeleteResult =
        decode(requestText("DELETE", "/medications/$id"), DeleteResult.serializer())

    suspend fun createAllergy(payload: JsonObject): Allergy =
        decodeMutate("POST", "/allergies", payload, Allergy.serializer())
    suspend fun updateAllergy(id: String, payload: JsonObject): Allergy =
        decodeMutate("PUT", "/allergies/$id", payload, Allergy.serializer())
    suspend fun deleteAllergy(id: String): DeleteResult =
        decode(requestText("DELETE", "/allergies/$id"), DeleteResult.serializer())

    suspend fun createVaccine(payload: JsonObject): Vaccine =
        decodeMutate("POST", "/vaccines", payload, Vaccine.serializer())
    suspend fun updateVaccine(id: String, payload: JsonObject): Vaccine =
        decodeMutate("PUT", "/vaccines/$id", payload, Vaccine.serializer())
    suspend fun deleteVaccine(id: String): DeleteResult =
        decode(requestText("DELETE", "/vaccines/$id"), DeleteResult.serializer())

    /** `POST /clinical-events` — returns the raw JSON (the event's to_dict()
     *  shape is rich + nested under `type_details`, so the caller decodes
     *  what it needs). Use [getClinicalEvent] for the typed flat shape. */
    suspend fun createClinicalEventRaw(payload: JsonObject): String =
        mutateRaw("POST", "/clinical-events", payload)

    /** `PUT /clinical-events/{id}` — raw JSON (same rich shape as create). */
    suspend fun updateClinicalEventRaw(id: String, payload: JsonObject): String =
        mutateRaw("PUT", "/clinical-events/$id", payload)

    suspend fun deleteClinicalEvent(id: String): DeleteResult =
        decode(requestText("DELETE", "/clinical-events/$id"), DeleteResult.serializer())

    /** `POST /clinical-events/{id}/occurrences` — log a recurrence. Returns
     *  the raw JSON of the updated event. */
    suspend fun addClinicalEventOccurrenceRaw(id: String, payload: JsonObject): String =
        mutateRaw("POST", "/clinical-events/$id/occurrences", payload)

    suspend fun createDoctor(payload: JsonObject): Doctor =
        decodeMutate("POST", "/doctors", payload, Doctor.serializer())
    suspend fun updateDoctor(id: String, payload: JsonObject): Doctor =
        decodeMutate("PUT", "/doctors/$id", payload, Doctor.serializer())
    suspend fun deleteDoctor(id: String): DeleteResult =
        decode(requestText("DELETE", "/doctors/$id"), DeleteResult.serializer())

    // --- Phase 7: notification inbox + preferences + triggers ---------

    /** `GET /notifications/inbox?status=&category=&source=&patient_id=&limit=&offset=`
     *  The owner's inbox (NOT the bound patient's — a multi-patient guardian
     *  sees one inbox per owner). */
    suspend fun getNotificationInbox(
        status: String? = null,
        category: String? = null,
        source: String? = null,
        patientId: String? = null,
        limit: Int? = null,
        offset: Int? = null,
    ): ReadEnvelope<NotificationItem> {
        val params = buildList {
            status?.let { add("status=$it") }
            category?.let { add("category=$it") }
            source?.let { add("source=$it") }
            patientId?.let { add("patient_id=$it") }
            limit?.let { add("limit=$it") }
            offset?.let { add("offset=$it") }
        }
        val qs = if (params.isEmpty()) "" else "?" + params.joinToString("&")
        return readEnvelope("/notifications/inbox$qs", NotificationItem.serializer())
    }

    /** `GET /notifications/unread-count` → the badge number. */
    suspend fun getUnreadNotificationCount(): Int =
        decode(requestText("GET", "/notifications/unread-count"), UnreadCount.serializer()).unreadCount

    /** `PATCH /notifications/{recipient_id}/read`. */
    suspend fun markNotificationRead(recipientId: String): StatusAck {
        val resp = request("PATCH", "/notifications/$recipientId/read")
        val text = resp.bodyAsText()
        if (resp.status.value !in 200..299) throw BridgeException(resp.status.value, text)
        return decode(text, StatusAck.serializer())
    }

    /** `PATCH /notifications/{recipient_id}/dismiss`. */
    suspend fun markNotificationDismissed(recipientId: String): StatusAck {
        val resp = request("PATCH", "/notifications/$recipientId/dismiss")
        val text = resp.bodyAsText()
        if (resp.status.value !in 200..299) throw BridgeException(resp.status.value, text)
        return decode(text, StatusAck.serializer())
    }

    /** `POST /notifications/read-all` — returns how many were marked. */
    suspend fun markAllNotificationsRead(): ReadAllResult =
        decode(requestText("POST", "/notifications/read-all"), ReadAllResult.serializer())

    /** `GET /notifications/preferences` — the full preferences hub listing. */
    suspend fun getNotificationPreferences(): ReadEnvelope<NotificationKind> =
        readEnvelope("/notifications/preferences", NotificationKind.serializer())

    /** `PUT /notifications/preferences/{kind_id}` — toggle a kind on/off. */
    suspend fun setNotificationPreference(kindId: String, enabled: Boolean): PreferenceToggleResult {
        val body = buildJsonObject { put("enabled", JsonPrimitive(enabled)) }
        val text = mutateRaw("PUT", "/notifications/preferences/$kindId", body)
        return decode(text, PreferenceToggleResult.serializer())
    }

    /** `GET /notifications/triggers` — biomarker-threshold rules for the
     *  bound patient. Medication reminders live on-device (the app's
     *  WorkManager), not here. */
    suspend fun getNotificationTriggers(limit: Int? = null): ReadEnvelope<NotificationTrigger> =
        readEnvelope(
            "/notifications/triggers" + (limit?.let { "?limit=$it" } ?: ""),
            NotificationTrigger.serializer(),
        )

    /** `POST /notifications/triggers` — create a biomarker-threshold rule. */
    suspend fun createNotificationTrigger(payload: JsonObject): NotificationTrigger =
        decodeMutate("POST", "/notifications/triggers", payload, NotificationTrigger.serializer())

    /** `DELETE /notifications/triggers/{id}`. */
    suspend fun deleteNotificationTrigger(id: String): DeleteResult =
        decode(requestText("DELETE", "/notifications/triggers/$id"), DeleteResult.serializer())

    // --- Phase 8: native push device registration ---------------------

    /** `POST /notifications/register-device` — register / re-register this
     *  mobile install for native push. Re-registering the same device_id
     *  upserts (e.g. after the user picks a new UnifiedPush distributor). */
    suspend fun registerDevice(registration: DeviceRegistration): MobilePushTarget =
        decode(
            mutateRaw("POST", "/notifications/register-device", json.encodeToJsonElement(DeviceRegistration.serializer(), registration).jsonObject),
            MobilePushTarget.serializer(),
        )

    /** `DELETE /notifications/register-device/{device_id}` — soft-deactivate
     *  (sign-out / lost-device). A re-register of the same device id later
     *  re-activates the row. */
    suspend fun unregisterDevice(deviceId: String): DeleteResult =
        decode(
            requestText("DELETE", "/notifications/register-device/$deviceId"),
            DeleteResult.serializer(),
        )

    /** `GET /devices` — the 'Where am I signed in' list. Endpoint URLs are
     *  masked server-side. */
    suspend fun listDevices(): ReadEnvelope<MobilePushTarget> =
        readEnvelope("/devices", MobilePushTarget.serializer())

    // --- internals ------------------------------------------------------

    private suspend fun send(
        method: String,
        path: String,
        body: ByteArray,
        attempt: Int = 0,
    ): HttpResponse {
        val signed = apiSecret?.takeIf { it.isNotBlank() }
            ?.let { Signing.sign(it, method, path, body) }
        return try {
            val resp = http.request(apiBase + normalize(path)) {
                this.method = HttpMethod.parse(method)
                if (body.isNotEmpty()) {
                    setBody(body)
                    contentType(ContentType.Application.Json)
                }
                if (signed != null) {
                    header("X-Api-Signature", signed.signature)
                    header("X-Api-Timestamp", signed.timestamp)
                }
            }
            if (resp.status.value in RETRYABLE && attempt < MAX_RETRIES) {
                backoff(attempt)
                send(method, path, body, attempt + 1)
            } else {
                resp
            }
        } catch (e: IOException) {
            if (attempt < MAX_RETRIES) {
                backoff(attempt)
                send(method, path, body, attempt + 1)
            } else {
                throw e
            }
        }
    }

    private fun normalize(path: String): String = if (path.startsWith("/")) path else "/$path"

    /** Serialize a [JsonObject] payload to UTF-8 bytes for a signed POST/PUT.
     *  Used by every Phase 6+ mutation helper. */
    private suspend fun mutateRaw(method: String, path: String, payload: JsonObject): String {
        val body = json.encodeToString(JsonObject.serializer(), payload).encodeToByteArray()
        return requestText(method, path, body)
    }

    /** Mutate + decode the typed reply (the common Phase 6 pattern: create /
     *  update returns the resource dict). */
    private suspend fun <T> decodeMutate(
        method: String,
        path: String,
        payload: JsonObject,
        serializer: kotlinx.serialization.KSerializer<T>,
    ): T {
        val text = mutateRaw(method, path, payload)
        return json.decodeFromString(serializer, text)
    }

    /** String overload of [decode] for the cases where the response has
     *  already been read via [requestText]. */
    private fun <T> decode(text: String, serializer: kotlinx.serialization.KSerializer<T>): T =
        json.decodeFromString(serializer, text)

    private suspend fun backoff(attempt: Int) {
        val ceiling = 8_000L
        val max = minOf(ceiling, 1L shl attempt)
        delay(Random.nextLong(0, max + 1))
    }

    private suspend fun <T> decode(resp: HttpResponse, serializer: kotlinx.serialization.KSerializer<T>): T =
        json.decodeFromString(serializer, resp.bodyAsText())

    override fun close() {
        if (ownsClient) http.close()
    }

    private companion object {
        const val MAX_RETRIES = 3
        val RETRYABLE = setOf(429, 500, 502, 503, 504)
    }
}

/** Thrown by [BridgeClient.requestText] on a non-2xx response. */
class BridgeException(val statusCode: Int, val body: String) :
    Exception("HTTP $statusCode: $body")
