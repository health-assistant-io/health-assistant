package io.healthassistant.bridge

import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.HttpTimeoutConfig
import io.ktor.client.request.header
import io.ktor.client.request.request
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsBytes
import io.ktor.client.statement.bodyAsChannel
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpMethod
import io.ktor.http.contentType
import io.ktor.utils.io.readUTF8Line
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.io.IOException
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.util.Base64
import kotlin.random.Random

/**
 * Client for the Health Assistant Bridge two-way API proxy. Mirrors
 * python-sdk/.../async_client.py: pooled client, 30s timeout. Every path is
 * HMAC-signed when [apiSecret] is set; without one, only the minimal
 * unsigned [getStatus] liveness probe is answered by the server (the
 * full discovery payload requires a signature — least-privilege by
 * design).
 *
 * Pass a custom [client] (e.g. a MockEngine-backed HttpClient) for tests.
 *
 * ## Retry matrix
 *
 * Two request classes, selected by [send]'s `retry` flag:
 *
 * - **Retried** (`retry = true`, the default): reads ([getStatus], every
 *   typed getter), `/sync`, idempotent mutations (Phase-6 writes keyed
 *   on `client_request_id` / `id`, notification read/dismiss, session
 *   rename), and the read-only voice-input POST (audio is
 *   ephemeral server-side — a replay just re-transcribes). 429/5xx
 *   answers and `IOException`s are retried with
 *   full-jitter backoff (3 retries, cap 8s). Retrying here is safe because
 *   a replay either converges (idempotent writes) or was never applied.
 * - **Never retried** (`retry = false`): the AI turn POSTs — [chat], and
 *   every stream that goes through [requestLines] (`POST /ai/chat/stream`
 *   and `POST /ai/sessions/{id}/resume`). A turn POST is not idempotent:
 *   replaying it after a transport failure duplicates the user message and
 *   the LLM spend (and a partially-delivered answer cannot be resumed
 *   mid-SSE). With `retry = false` there is exactly one dispatch: a
 *   429/5xx response and any `IOException` — including connect-phase
 *   failures before the request was dispatched — surface to the caller
 *   ([BridgeException] / `IOException`) without a second attempt.
 *   **Recovery is the caller starting a NEW turn** (re-send the message,
 *   or [resumeChatStream] for a HITL continuation); the server-side
 *   single-flight turn lock keeps that new turn from colliding with one
 *   that is still running.
 */
class BridgeClient(
    val baseUrl: String,
    val integrationId: String,
    val apiSecret: String? = null,
    client: HttpClient? = null,
    streamClient: HttpClient? = null,
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

    /** Two-tier status probe. **Unsigned** (no [apiSecret]) it is the
     *  pre-pairing liveness probe — the server answers the minimal
     *  `{status, server_time}` shape only. **Signed** (secret set) it
     *  returns the full discovery payload: cursor, latest SDK versions,
     *  frontend origin, [BridgeStatus.serverVersion], and the
     *  [BridgeStatus.bridgeFeatures] flags the app gates on. */
    suspend fun getStatus(): BridgeStatus =
        decode(send("GET", "/status", ByteArray(0)), BridgeStatus.serializer())

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
        if (resp.status.value !in 200..299) throw BridgeException.from(resp.status.value, text)
        return text
    }

    /** Body bytes of a signed (when secret set) request, throwing [BridgeException]
     * on a non-2xx response. Used for binary downloads (document content +
     * preview JPEG) — the bridge's content paths return `Response(content=bytes)`
     * which this method materialises. */
    suspend fun requestBytes(method: String, path: String, body: ByteArray? = null): ByteArray {
        val resp = request(method, path, body)
        val bytes = resp.bodyAsBytes()
        if (resp.status.value !in 200..299) {
            throw BridgeException.from(resp.status.value, bytes.decodeToString())
        }
        return bytes
    }

    /**
     * Line-by-line reading of a signed streaming response body (SSE `data:`
     * lines). Ktor stays inside the SDK — callers consume a cold [Flow] of
     * raw lines and parse the SSE framing themselves. Non-2xx →
     * [BridgeException] before the first emission.
     *
     * **Never retries** (`retry = false` in [send]): the paths streamed
     * through this reader are AI turn POSTs, and a replay after a transport
     * failure would duplicate the turn — the user message and the LLM call —
     * so a mid-body `IOException` surfaces to the collector with no second
     * request. Recovery is the caller starting a NEW turn (see the class
     * retry matrix).
     *
     * Runs on a dedicated engine WITHOUT the 30 s whole-request timeout — a
     * chat stream legitimately idles between deltas (connect timeout still
     * applies; engine-level socket timeouts cover a dead peer).
     */
    fun requestLines(method: String, path: String, body: ByteArray? = null): Flow<String> =
        flow {
            val resp = send(method, path, body ?: ByteArray(0), client = streamHttp, retry = false)
            if (resp.status.value !in 200..299) {
                throw BridgeException.from(resp.status.value, resp.bodyAsText())
            }
            val channel = resp.bodyAsChannel()
            while (!channel.isClosedForRead) {
                val line = channel.readUTF8Line() ?: break
                emit(line)
            }
        }.flowOn(Dispatchers.IO)

    /** The streaming twin of [http]: no whole-request timeout, so long SSE
     *  bodies aren't cut at 30 s (see [requestLines]). Lazily built (or the
     *  injected [streamClient], e.g. a MockEngine in tests); closed together
     *  with the main client when owned. */
    private val ownsStreamClient = streamClient == null
    private val streamHttp: HttpClient by lazy {
        streamClient
            ?: HttpClient(CIO) {
                install(HttpTimeout) {
                    // Disable the whole-request timeout — SSE bodies idle
                    // between deltas (connect timeout still applies).
                    requestTimeoutMillis = HttpTimeoutConfig.INFINITE_TIMEOUT_MS
                    connectTimeoutMillis = 10_000
                }
            }
    }

    /** `POST /ai/sessions/{id}/resume` — stream the HITL continuation turn
     *  (SSE lines via [requestLines]) after the user resolved task cards
     *  through `.../tasks/{proposal_id}/resolve`. [messageId] selects the
     *  assistant message whose tasks are summarized; `null` omits the field
     *  so the server picks the most recent task-bearing message. One
     *  streaming turn per session — while another turn streams the server
     *  answers non-2xx before the first emission. Like every AI turn POST
     *  this is **never retried** (see [requestLines] and the class retry
     *  matrix): a failed resume is recovered by starting a new turn. */
    fun resumeChatStream(sessionId: String, messageId: String? = null): Flow<String> {
        val body = buildJsonObject {
            messageId?.let { put("message_id", it) }
        }
        return requestLines(
            "POST",
            "/ai/sessions/$sessionId/resume",
            json.encodeToString(JsonObject.serializer(), body).encodeToByteArray(),
        )
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

    /** `GET /examinations?limit=` — the patient's exam cards (newest first;
     *  dateless exams last). */
    suspend fun getExaminations(limit: Int? = null): ReadEnvelope<ExaminationSummary> =
        readEnvelope("/examinations" + (limit?.let { "?limit=$it" } ?: ""), ExaminationSummary.serializer())

    /** `GET /examinations/{id}` — one exam's full detail: the card fields plus
     *  the observations/doctors/medications/clinical-events sections. */
    suspend fun getExamination(id: String): ExaminationDetail =
        readDetail("/examinations/$id", ExaminationDetail.serializer())

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
        if (resp.status.value !in 200..299) throw BridgeException.from(resp.status.value, text)
        return decode(text, StatusAck.serializer())
    }

    /** `PATCH /notifications/{recipient_id}/dismiss`. */
    suspend fun markNotificationDismissed(recipientId: String): StatusAck {
        val resp = request("PATCH", "/notifications/$recipientId/dismiss")
        val text = resp.bodyAsText()
        if (resp.status.value !in 200..299) throw BridgeException.from(resp.status.value, text)
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

    // --- AI assistant (bridge /ai/* paths) ------------------------------

    /** `POST /ai/chat` — one non-streaming turn. The reply carries the
     *  persisted assistant row's id + HITL `tasks` cards + `citations`, so a
     *  turn with proposals renders without a messages refetch. **Never
     *  auto-retries** (`retry = false` in [send], one dispatch): a replayed
     *  chat POST would duplicate the user message and the LLM call. A
     *  failure — 429/5xx or network — surfaces to the caller; recovery is
     *  starting a NEW turn (see the class retry matrix). The retry-on-
     *  transient policy is for idempotent reads/writes only. */
    suspend fun chat(message: String, sessionId: String? = null): ChatTurnResult {
        val payload = buildJsonObject {
            put("message", message)
            sessionId?.let { put("session_id", it) }
        }
        val body = json.encodeToString(JsonObject.serializer(), payload).encodeToByteArray()
        val resp = send("POST", "/ai/chat", body, retry = false)
        val text = resp.bodyAsText()
        if (resp.status.value !in 200..299) throw BridgeException.from(resp.status.value, text)
        return decode(text, ChatTurnResult.serializer())
    }

    /** `GET /ai/sessions/{id}/messages?limit=` — the session's messages,
     *  newest first. `limit` defaults to 200 server-side (max 500). */
    suspend fun getMessages(sessionId: String, limit: Int? = null): ReadEnvelope<ChatMessage> =
        readEnvelope(
            "/ai/sessions/$sessionId/messages" + (limit?.let { "?limit=$it" } ?: ""),
            ChatMessage.serializer(),
        )

    /** `PATCH /ai/sessions/{id}` — rename a session (title non-blank, ≤ 200
     *  chars, validated server-side). Idempotent, so the default retry
     *  policy applies. Returns the renamed row. */
    suspend fun renameSession(sessionId: String, title: String): SessionRenameResult {
        val payload = buildJsonObject { put("title", title) }
        val body = json.encodeToString(JsonObject.serializer(), payload).encodeToByteArray()
        return decode(
            requestText("PATCH", "/ai/sessions/$sessionId", body),
            SessionRenameResult.serializer(),
        )
    }

    /** `POST /ai/transcribe` — voice input: base64 [audio] + [mime] (a full
     *  mime with parameters, e.g. `audio/webm;codecs=opus`, is normalized
     *  and allowlisted server-side; audio beyond the server's
     *  `AI_STT_MAX_AUDIO_BYTES` cap and non-audio mimes are rejected with
     *  a typed 400 `validation`). The audio is **ephemeral** server-side —
     *  transcribed and discarded, never persisted — so this is read-only
     *  (a replay just re-transcribes) and the default retry policy
     *  applies (unlike the AI turn POSTs; see the class retry matrix).
     *  Counts toward the /ai/ family rate budget. */
    suspend fun transcribe(audio: ByteArray, mime: String): TranscriptionResult {
        val payload = buildJsonObject {
            put("data", Base64.getEncoder().encodeToString(audio))
            put("mime", mime)
        }
        val body = json.encodeToString(JsonObject.serializer(), payload).encodeToByteArray()
        return decode(
            requestText("POST", "/ai/transcribe", body),
            TranscriptionResult.serializer(),
        )
    }

    // --- internals ------------------------------------------------------

    /**
     * Signed request carrying the retry policy of the two request classes
     * (see the class retry matrix).
     *
     * `retry = true` (default): 429/5xx answers and `IOException`s are
     * retried up to [MAX_RETRIES] times with full-jitter backoff — safe on
     * reads, `/sync`, and idempotent mutations.
     *
     * `retry = false`: exactly one dispatch. A 429/5xx response is returned
     * as-is (the calling helper decodes it into a [BridgeException]); an
     * `IOException` — including a connect-phase failure BEFORE the request
     * was dispatched — surfaces without a second attempt. Used by the
     * non-idempotent AI turn POSTs: a replayed turn duplicates the user
     * message and the LLM spend.
     */
    private suspend fun send(
        method: String,
        path: String,
        body: ByteArray,
        attempt: Int = 0,
        client: HttpClient = http,
        retry: Boolean = true,
    ): HttpResponse {
        val signed = apiSecret?.takeIf { it.isNotBlank() }
            ?.let { Signing.sign(it, method, path, body) }
        return try {
            val resp = client.request(apiBase + normalize(path)) {
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
            if (retry && resp.status.value in RETRYABLE && attempt < MAX_RETRIES) {
                backoff(attempt)
                send(method, path, body, attempt + 1, client, retry)
            } else {
                resp
            }
        } catch (e: IOException) {
            if (retry && attempt < MAX_RETRIES) {
                backoff(attempt)
                send(method, path, body, attempt + 1, client, retry)
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
        if (ownsStreamClient) streamHttp.close()
    }

    companion object {
        /** This SDK's version — must stay in sync with the gradle `version`
         *  and manifest.json `sdks.kotlin`. Displayed by the app next to
         *  [BridgeStatus.latestSdks] ("your SDK vs latest on the server"). */
        const val SDK_VERSION: String = "0.6.0"

        private const val MAX_RETRIES = 3
        private val RETRYABLE = setOf(429, 500, 502, 503, 504)
    }
}

/**
 * Thrown by [BridgeClient] request helpers on a non-2xx response. [code] is
 * the stable error code decoded from the typed detail envelope
 * `{detail: {code, message}}` — null for older servers that answer with a
 * plain-string detail (the raw body stays available as [body]). The closed
 * vocabulary (`not_found`/404, `turn_conflict`/409, `hitl_conflict`/409,
 * `validation`/400) is documented in the bridge docs' Errors table; typed
 * surfaces: [NotFoundException], [TurnConflictException],
 * [HitlConflictException] — otherwise a plain [BridgeException] (check
 * [code] to discriminate).
 */
open class BridgeException(val statusCode: Int, val body: String, val code: String? = null) :
    Exception("HTTP $statusCode: $body") {

    companion object {
        /** Decode the error body into the most specific exception: the typed
         *  `{detail: {code, message}}` envelope drives the subclass, while a
         *  plain-string (or non-JSON) detail yields [code] = null. */
        fun from(statusCode: Int, body: String): BridgeException {
            val code = decodeCode(body)
            return when {
                code == "turn_conflict" -> TurnConflictException(body)
                code == "hitl_conflict" -> HitlConflictException(body)
                code == "not_found" || statusCode == 404 -> NotFoundException(statusCode, body)
                else -> BridgeException(statusCode, body, code)
            }
        }

        private fun decodeCode(body: String): String? = try {
            val detail = Json.parseToJsonElement(body).jsonObject["detail"]
            (detail as? JsonObject)?.get("code")?.jsonPrimitive?.contentOrNull
        } catch (_: Exception) {
            null
        }
    }
}

/** 404 / `not_found` — the session/exam/document/… row is missing or owned
 *  by another patient. */
class NotFoundException(statusCode: Int, body: String) :
    BridgeException(statusCode, body, "not_found")

/** 409 / `turn_conflict` — another turn is already streaming on the session. */
class TurnConflictException(body: String) : BridgeException(409, body, "turn_conflict")

/** 409 / `hitl_conflict` — the HITL proposal was already resolved. */
class HitlConflictException(body: String) : BridgeException(409, body, "hitl_conflict")
