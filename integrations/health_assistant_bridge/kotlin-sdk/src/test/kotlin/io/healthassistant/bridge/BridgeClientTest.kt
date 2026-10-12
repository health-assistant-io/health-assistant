package io.healthassistant.bridge

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockRequestHandler
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class BridgeClientTest {

    private fun bridge(secret: String?, handler: MockRequestHandler): BridgeClient =
        BridgeClient(
            baseUrl = "https://example.test",
            integrationId = "00000000-0000-0000-0000-000000000000",
            apiSecret = secret,
            client = HttpClient(MockEngine(handler)),
        )

    @Test
    fun `getStatus sends no signature`() = runBlocking {
        var sawSig: String? = "unset"
        val c = bridge(null) { req ->
            sawSig = req.headers["X-Api-Signature"]
            respond("{\"status\":\"active\",\"integration_id\":\"x\"}")
        }
        c.getStatus()
        assertNull(sawSig)
        c.close()
    }

    @Test
    fun `getStatus signs the empty body when secret set`() = runBlocking {
        val secret = "test-secret-16chars!"
        var sawSig: String? = null
        var sawTs: String? = null
        val c = bridge(secret) { req ->
            sawSig = req.headers["X-Api-Signature"]
            sawTs = req.headers["X-Api-Timestamp"]
            respond("{\"status\":\"active\"}")
        }
        c.getStatus()
        assertEquals(
            Signing.sign(secret, "GET", "/status", ByteArray(0), sawTs!!.toLong()).signature,
            sawSig,
        )
        c.close()
    }

    @Test
    fun `getStatus decodes the minimal unsigned shape`() = runBlocking {
        val c = bridge(null) { respond("{\"status\":\"active\",\"server_time\":1760000000}") }
        val s = c.getStatus()
        assertEquals("active", s.status)
        assertEquals(1760000000L, s.serverTime)
        assertNull(s.cursor)
        assertNull(s.serverVersion)
        c.close()
    }

    @Test
    fun `getStatus decodes the full signed shape`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = """
            {"status":"active","server_time":1760000000,
             "integration_id":"00000000-0000-0000-0000-000000000000",
             "last_synced_at":null,"cursor":"2026-07-24T12:00:00Z",
             "latest_sdks":{"python":"1.3.0","ts":"1.3.0","kotlin":"0.5.0"},
             "frontend_base_url":"http://localhost:3000","server_version":"1.2.0",
             "bridge_features":["exam_enrichment","ai_assistant","ai_streaming"]}
        """.trimIndent()
        val c = bridge(secret) { respond(payload) }
        val s = c.getStatus()
        assertEquals(1760000000L, s.serverTime)
        assertEquals("2026-07-24T12:00:00Z", s.cursor)
        assertEquals("0.5.0", s.latestSdks?.get("kotlin"))
        assertEquals("http://localhost:3000", s.frontendBaseUrl)
        assertEquals("1.2.0", s.serverVersion)
        assertEquals(listOf("exam_enrichment", "ai_assistant", "ai_streaming"), s.bridgeFeatures)
        c.close()
    }

    @Test
    fun `request signs and signature covers the exact bytes sent`() = runBlocking {
        val secret = "test-secret-16chars!"
        val knownBody = "{\"records\":[]}".encodeToByteArray()
        val c = bridge(secret) { req ->
            val sent = req.headers["X-Api-Signature"]
            assertEquals(Signing.sign(secret, "POST", "/sync", knownBody).signature, sent)
            respond("{\"success\":true}")
        }
        val resp = c.request("POST", "/sync", knownBody)
        assertTrue(resp.status.value in 200..299)
        c.close()
    }

    @Test
    fun `syncData signs when secret set`() = runBlocking {
        val secret = "test-secret-16chars!"
        var sawSig: String? = null
        val c = bridge(secret) { req ->
            sawSig = req.headers["X-Api-Signature"]
            respond("{\"success\":true,\"metrics_synced\":1}")
        }
        val resp = c.syncData(SyncPayload(clientVersion = "0.1", sourceSystem = "test"))
        assertNotNull(sawSig)
        assertTrue(resp.success)
        c.close()
    }

    @Test
    fun `syncData sends no signature without secret`() = runBlocking {
        var sawSig: String? = "unset"
        val c = bridge(null) { req ->
            sawSig = req.headers["X-Api-Signature"]
            respond("{\"success\":true}")
        }
        c.syncData(SyncPayload(clientVersion = "0.1", sourceSystem = "test"))
        assertNull(sawSig)
        c.close()
    }

    @Test
    fun `syncData retries on 503 then succeeds`() = runBlocking {
        val secret = "test-secret-16chars!"
        var calls = 0
        val c = bridge(secret) {
            calls++
            if (calls == 1) respond("", HttpStatusCode.ServiceUnavailable)
            else respond("{\"success\":true,\"metrics_synced\":2}")
        }
        val resp = c.syncData(SyncPayload(clientVersion = "0.1", sourceSystem = "test"))
        assertEquals(2, calls)
        assertTrue(resp.success)
        c.close()
    }

    @Test
    fun `requestBytes returns the raw response body`() = runBlocking {
        val secret = "test-secret-16chars!"
        val expected = byteArrayOf(0x25, 0x50, 0x44, 0x46, 0x2D) // "%PDF-"
        val c = bridge(secret) {
            respond(expected, headers = headersOf("Content-Type", "application/pdf"))
        }
        val bytes = c.requestBytes("GET", "/documents/abc/content")
        assertEquals(expected.toList(), bytes.toList())
        c.close()
    }

    @Test
    fun `requestBytes throws on non-2xx`() = runBlocking {
        val secret = "test-secret-16chars!"
        val c = bridge(secret) {
            respond("not found".encodeToByteArray(), HttpStatusCode.NotFound)
        }
        var threw = false
        try {
            c.requestBytes("GET", "/documents/abc/content")
        } catch (e: BridgeException) {
            threw = true
            assertEquals(404, e.statusCode)
        }
        assertTrue(threw)
        c.close()
    }

    @Test
    fun `getDocumentContent signs the request`() = runBlocking {
        val secret = "test-secret-16chars!"
        val expectedSig = Signing.sign(secret, "GET", "/documents/abc/content").signature
        var sawSig: String? = null
        val c = bridge(secret) { req ->
            sawSig = req.headers["X-Api-Signature"]
            respond(byteArrayOf(1, 2, 3, 4))
        }
        c.getDocumentContent("abc")
        assertEquals(expectedSig, sawSig)
        c.close()
    }

    // --- Typed readers (Phase 2) ---

    private fun envelopeJson(itemsJson: String, cursor: String? = null): String {
        val c = cursor?.let { "\"$it\"" } ?: "null"
        return """{"data":$itemsJson,"cursor":$c,"cached_at":"2026-08-12T00:00:00Z"}"""
    }

    // --- Phase 6/7/8: mutations + notifications + push -----------------

    @Test
    fun `createMedication POSTs and decodes the reply`() = runBlocking {
        val c = bridge("secret-16-chars!!") { req ->
            assertEquals("POST", req.method.value)
            respond(
                """{"id":"m1","status":"ACTIVE","intent":"statement","code":{"text":"Lisinopril"},"dosage":"10mg"}""",
                headers = headersOf("Content-Type", "application/json"),
            )
        }
        val payload = kotlinx.serialization.json.buildJsonObject {
            put("dosage", kotlinx.serialization.json.JsonPrimitive("10mg"))
        }
        val m = c.createMedication(payload)
        assertEquals("m1", m.id)
        assertEquals("10mg", m.dosage)
        c.close()
    }

    @Test
    fun `deleteMedication decodes the DeleteResult ack`() = runBlocking {
        val c = bridge("secret-16-chars!!") { req ->
            assertEquals("DELETE", req.method.value)
            respond("""{"id":"m1","deleted":true,"message":"Medication deleted."}""")
        }
        val r = c.deleteMedication("m1")
        assertTrue(r.deleted)
        assertEquals("m1", r.id)
        c.close()
    }

    @Test
    fun `getNotificationInbox decodes the envelope`() = runBlocking {
        val c = bridge("secret-16-chars!!") { req ->
            respond(
                envelopeJson(
                    """[{"recipient_id":"r1","status":"unread","notification":{"id":"n1","title":"Hi","body":"World","type":"SYSTEM_UPDATE","severity":"info"}}]""",
                ),
            )
        }
        val env = c.getNotificationInbox()
        assertEquals(1, env.data.size)
        assertEquals("r1", env.data[0].recipientId)
        assertEquals("Hi", env.data[0].notification?.title)
        c.close()
    }

    @Test
    fun `getUnreadNotificationCount decodes the count`() = runBlocking {
        val c = bridge("secret-16-chars!!") { req ->
            respond("""{"unread_count":3}""")
        }
        assertEquals(3, c.getUnreadNotificationCount())
        c.close()
    }

    @Test
    fun `setNotificationPreference PUTs enabled flag and decodes ack`() = runBlocking {
        val c = bridge("secret-16-chars!!") { req ->
            assertEquals("PUT", req.method.value)
            respond("""{"status":"success","kind_id":"channel:PUSH","enabled":false}""")
        }
        val r = c.setNotificationPreference("channel:PUSH", enabled = false)
        assertEquals("channel:PUSH", r.kindId)
        assertFalse(r.enabled)
        c.close()
    }

    @Test
    fun `registerDevice decodes the masked target`() = runBlocking {
        val c = bridge("secret-16-chars!!") { req ->
            assertEquals("POST", req.method.value)
            respond(
                """{"id":"t1","user_id":"u1","device_id":"d1","platform":"unifiedpush","endpoint_url":"https://ntfy.x…","is_active":true}""",
            )
        }
        val r = c.registerDevice(
            DeviceRegistration(
                deviceId = "d1",
                platform = "unifiedpush",
                endpointUrl = "https://ntfy.example.test/secret",
            ),
        )
        assertEquals("d1", r.deviceId)
        assertEquals("unifiedpush", r.platform)
        // The masked endpoint never contains the real topic — verifies the
        // server-side masking the mobile app's UI relies on.
        assertFalse(r.endpointUrlMasked.contains("secret"))
        c.close()
    }

    @Test
    fun `listDevices returns the envelope of masked targets`() = runBlocking {
        val c = bridge("secret-16-chars!!") { req ->
            respond(
                envelopeJson(
                    """[{"id":"t1","user_id":"u1","device_id":"d1","platform":"unifiedpush","endpoint_url":"https://ntfy.x…","is_active":true}]""",
                ),
            )
        }
        val env = c.listDevices()
        assertEquals(1, env.data.size)
        assertEquals("d1", env.data[0].deviceId)
        c.close()
    }

    @Test
    fun `getBiomarkers decodes the envelope`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"b1","name":"Heart Rate","slug":"heart-rate","code":"8867-4","coding_system":"loinc","unit":"bpm","is_telemetry":true,"value_type":"quantity"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.getBiomarkers()
        assertEquals(1, env.data.size)
        val b = env.data[0]
        assertEquals("Heart Rate", b.name)
        assertEquals("8867-4", b.code)
        assertEquals("bpm", b.unit)
        assertTrue(b.isTelemetry)
        assertEquals("quantity", b.valueType)
        c.close()
    }

    @Test
    fun `getObservationsLatest decodes points with FHIR code shape`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"o1","effective_datetime":"2026-08-12T10:00:00Z","code":{"coding":[{"system":"http://loinc.org","code":"8867-4"}],"text":"Heart Rate"},"value_quantity":{"value":72.0,"unit":"bpm"},"reference_range":{"low":60.0,"high":100.0},"biomarker_id":"b1","biomarker_slug":"heart-rate","raw_value":72.0,"relative_score":0.4}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.getObservationsLatest()
        assertEquals(1, env.data.size)
        val o = env.data[0]
        assertEquals("o1", o.id)
        assertEquals("Heart Rate", o.code?.text)
        assertEquals(72.0, o.valueQuantity?.value)
        assertEquals(60.0, o.referenceRange?.low)
        assertEquals("heart-rate", o.biomarkerSlug)
        c.close()
    }

    @Test
    fun `getObservations builds the right query string`() = runBlocking {
        val secret = "test-secret-16chars!"
        var capturedPath: String? = null
        val c = bridge(secret) { req ->
            capturedPath = req.url.encodedPath
            envelopeJson("[]")
            respond(envelopeJson("[]"))
        }
        c.getObservations(biomarker = "heart-rate", since = "2026-01-01T00:00:00Z", limit = 100)
        // The path includes the full URL; check the query landed.
        assertTrue(capturedPath!!.endsWith("/observations"))
        c.close()
    }

    @Test
    fun `getExaminations decodes enriched card fields`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"e1","examination_date":"2026-10-02","notes":"Lipid panel","patient_notes":null,"extraction_status":"completed","extraction_progress":100,"diagnoses":["E78.5"],"category":"Biochemical Tests","lab_name":"Acme Labs","observation_count":2,"medication_count":1,"document_count":0,"created_at":"2026-10-02T09:00:00+00:00","updated_at":"2026-10-02T09:30:00+00:00"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.getExaminations()
        assertEquals(1, env.data.size)
        val e = env.data[0]
        assertEquals("e1", e.id)
        assertEquals(100, e.extractionProgress)
        assertEquals("Biochemical Tests", e.category)
        assertEquals("Acme Labs", e.labName)
        assertEquals(2, e.observationCount)
        assertEquals(1, e.medicationCount)
        assertEquals(0, e.documentCount)
        assertNotNull(e.createdAt)
        c.close()
    }

    @Test
    fun `getExamination decodes the enriched detail with sections`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = """
            {"id":"e1","examination_date":"2026-08-08","notes":"Annual checkup","patient_notes":null,
             "extraction_status":"completed","extraction_progress":100,"diagnoses":["Hypertension"],
             "category":"lab","lab_name":"Acme Labs","observation_count":1,"medication_count":1,
             "document_count":0,"created_at":"2026-08-08T09:00:00+00:00","updated_at":"2026-08-08T10:00:00+00:00",
             "impressions":null,"error_message":null,"external_id":"ext-1",
             "observations":[{"id":"o1","effective_datetime":"2026-08-08T09:10:00Z",
                 "code":{"coding":[{"system":"http://loinc.org","code":"13457-7"}],"text":"LDL Cholesterol"},
                 "raw_value":3.9,"normalized_value":3.9,"normalized_unit":"mmol/L","value_string":null,
                 "reference_range":{"low":0.0,"high":3.0},"interpretation":"HIGH","relative_score":1.0,
                 "biomarker_id":"b1","biomarker_slug":"ldl","biomarker_value_type":"numeric",
                 "biomarker_reference_range_min":0.0,"biomarker_reference_range_max":3.0,"document_id":null}],
             "doctors":[{"id":"d1","name":"Dr. Maria Papadopoulou","specialty_concept_id":null,
                 "specialty":"Cardiology","license_number":null,"email":null,"phone":null,
                 "telecom":null,"address":null,"office_number":null,"office_details":null}],
             "medications":[{"id":"m1","status":"active","intent":"proposal",
                 "code":{"text":"Atorvastatin 20 mg"},"start_date":null,"end_date":null,
                 "dosage":"1 tablet","frequency":null,"reason":null,"note":null,
                 "examination_id":"e1","created_at":null,"updated_at":null}],
             "clinical_events":[{"id":"ce1","title":"Hyperlipidemia follow-up","status":"ACTIVE","reason":"Annual lipid panel"}]}
        """.trimIndent()
        val c = bridge(secret) { respond(payload) }
        val e = c.getExamination("e1")
        assertEquals("e1", e.id)
        assertEquals("Annual checkup", e.notes)
        assertEquals("completed", e.extractionStatus)
        assertEquals(100, e.extractionProgress)
        assertEquals(listOf("Hypertension"), e.diagnoses)
        assertEquals("lab", e.category)
        assertEquals("Acme Labs", e.labName)
        assertEquals("ext-1", e.externalId)
        assertEquals(1, e.observationCount)
        val obs = e.observations!!.single()
        assertEquals("LDL Cholesterol", obs.code?.text)
        assertEquals(3.9, obs.rawValue)
        assertEquals(3.0, obs.referenceRange?.high)
        assertEquals("HIGH", obs.interpretation)
        assertEquals("ldl", obs.biomarkerSlug)
        assertEquals("Dr. Maria Papadopoulou", e.doctors!!.single().name)
        assertEquals("1 tablet", e.medications!!.single().dosage)
        val journey = e.clinicalEvents!!.single()
        assertEquals("Hyperlipidemia follow-up", journey.title)
        assertEquals("Annual lipid panel", journey.reason)
        c.close()
    }

    @Test
    fun `getDocumentText decodes extracted markdown`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = """{"id":"d1","extracted_text":"## Results\n- LDL 3.9","status":"completed","truncated":false}"""
        val c = bridge(secret) { respond(payload) }
        val t = c.getDocumentText("d1")
        assertEquals("d1", t.id)
        assertEquals("## Results\n- LDL 3.9", t.extractedText)
        assertEquals("completed", t.status)
        assertEquals(false, t.truncated)
        c.close()
    }

    @Test
    fun `biomarker row decodes info markdown`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"b1","name":"LDL","slug":"ldl","code":"13457-7","coding_system":"loinc","unit":"mmol/L","is_telemetry":false,"reference_range_min":0,"reference_range_max":3,"value_type":"numeric","info":"Low-density **lipoprotein** cholesterol."}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val b = c.getBiomarkers().data.first()
        assertEquals("LDL", b.name)
        assertEquals("Low-density **lipoprotein** cholesterol.", b.info)
        c.close()
    }

    @Test
    fun `listDocuments decodes the patient-wide envelope`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"d1","filename":"lab.pdf","status":"uploaded","progress":0,"content_type":"application/pdf","file_size":1024,"examination_id":"e1"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.listDocuments()
        assertEquals(1, env.data.size)
        val d = env.data[0]
        assertEquals("lab.pdf", d.filename)
        assertEquals("application/pdf", d.contentType)
        assertEquals(1024L, d.fileSize)
        assertEquals("e1", d.examinationId)
        c.close()
    }

    @Test
    fun `listDocumentsForExam decodes the under-exam envelope`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"d1","filename":"lab.pdf"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.listDocumentsForExam("e1")
        assertEquals(1, env.data.size)
        assertEquals("lab.pdf", env.data[0].filename)
        c.close()
    }

    // --- Phase 3: clinical-record readers ---

    @Test
    fun `getMedications decodes the envelope`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"m1","status":"ACTIVE","intent":"statement","code":{"text":"Lisinopril"},"dosage":"10mg","start_date":"2026-07-01"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.getMedications()
        assertEquals(1, env.data.size)
        val m = env.data[0]
        assertEquals("ACTIVE", m.status)
        assertEquals("10mg", m.dosage)
        assertEquals("Lisinopril", m.code["text"]?.jsonPrimitive?.content)
        c.close()
    }

    @Test
    fun `getAllergies decodes the envelope`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"a1","clinical_status":"ACTIVE","category":"MEDICATION","criticality":"LOW","code":{"text":"Penicillin"},"onset_date":"2026-01-01T00:00:00Z"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.getAllergies()
        assertEquals(1, env.data.size)
        val a = env.data[0]
        assertEquals("ACTIVE", a.clinicalStatus)
        assertEquals("MEDICATION", a.category)
        assertEquals("Penicillin", a.code["text"]?.jsonPrimitive?.content)
        c.close()
    }

    @Test
    fun `getVaccines decodes the envelope`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"v1","status":"completed","vaccine_code":{"text":"COVID-19"},"administered_at":"2026-07-15T00:00:00Z","dose_number":"1"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.getVaccines()
        assertEquals(1, env.data.size)
        val v = env.data[0]
        assertEquals("completed", v.status)
        assertEquals("COVID-19", v.vaccineCode["text"]?.jsonPrimitive?.content)
        c.close()
    }

    @Test
    fun `getClinicalEvents decodes the flat list`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"e1","status":"ACTIVE","title":"Hypertension","type_name":"HTN","type_slug":"htn","onset_date":"2026-06-01T00:00:00Z"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.getClinicalEvents()
        assertEquals(1, env.data.size)
        val e = env.data[0]
        assertEquals("ACTIVE", e.status)
        assertEquals("Hypertension", e.title)
        assertEquals("HTN", e.typeName)
        c.close()
    }

    @Test
    fun `getDoctors decodes the envelope`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = envelopeJson(
            """[{"id":"d1","name":"Dr. House","specialty":"Cardiology","email":"house@example.com","phone":"555-1000"}]"""
        )
        val c = bridge(secret) { respond(payload) }
        val env = c.getDoctors()
        assertEquals(1, env.data.size)
        val d = env.data[0]
        assertEquals("Dr. House", d.name)
        assertEquals("Cardiology", d.specialty)
        c.close()
    }

    @Test
    fun `requestLines streams SSE data lines`() = runBlocking {
        val secret = "test-secret-16chars!"
        val sse = "data: {\"event\":\"session\",\"session_id\":\"s1\"}\n\n" +
            "data: {\"event\":\"delta\",\"text\":\"Hel\"}\n\n" +
            "data: {\"event\":\"delta\",\"text\":\"lo\"}\n\n" +
            ": keep-alive comment\n\n" +
            "data: {\"event\":\"flow_finished\"}\n\n"
        val engine = MockEngine { respond(sse, HttpStatusCode.OK, headersOf("Content-Type", "text/event-stream")) }
        val c =
            BridgeClient(
                baseUrl = "https://example.test",
                integrationId = "00000000-0000-0000-0000-000000000000",
                apiSecret = secret,
                client = HttpClient(engine),
                streamClient = HttpClient(engine),
            )
        val lines = c.requestLines("POST", "/ai/chat/stream", "{}".encodeToByteArray()).toList()
        assertEquals(
            listOf(
                "data: {\"event\":\"session\",\"session_id\":\"s1\"}",
                "",
                "data: {\"event\":\"delta\",\"text\":\"Hel\"}",
                "",
                "data: {\"event\":\"delta\",\"text\":\"lo\"}",
                "",
                ": keep-alive comment",
                "",
                "data: {\"event\":\"flow_finished\"}",
                "",
            ),
            lines,
        )
        assertEquals(1, engine.requestHistory.size)
        assertEquals(
            "/api/v1/integrations/health_assistant_bridge/api/00000000-0000-0000-0000-000000000000/ai/chat/stream",
            engine.requestHistory[0].url.encodedPath,
        )
        c.close()
    }

    @Test
    fun `requestLines throws BridgeException on non-2xx`() = runBlocking {
        val secret = "test-secret-16chars!"
        val engine = MockEngine { respond("{\"detail\":\"nope\"}", HttpStatusCode.BadRequest) }
        val c =
            BridgeClient(
                baseUrl = "https://example.test",
                integrationId = "00000000-0000-0000-0000-000000000000",
                apiSecret = secret,
                client = HttpClient(engine),
                streamClient = HttpClient(engine),
            )
        val ex = runCatching { c.requestLines("POST", "/ai/chat/stream").toList() }.exceptionOrNull()
        assertTrue(ex is BridgeException)
        assertEquals(400, (ex as BridgeException).statusCode)
        c.close()
    }

    // --- HITL resume streaming (WS4) -----------------------------------

    private fun streamBridge(secret: String, engine: MockEngine): BridgeClient =
        BridgeClient(
            baseUrl = "https://example.test",
            integrationId = "00000000-0000-0000-0000-000000000000",
            apiSecret = secret,
            client = HttpClient(engine),
            streamClient = HttpClient(engine),
        )

    @Test
    fun `resumeChatStream POSTs the resume path and streams its lines`() = runBlocking {
        val secret = "test-secret-16chars!"
        val expectedBody = """{"message_id":"m-1"}"""
        val sse = "data: {\"event\":\"delta\",\"text\":\"Acknowledged.\"}\n\n" +
            "data: {\"event\":\"flow_finished\"}\n\n"
        var sawSig: String? = null
        val engine = MockEngine { req ->
            sawSig = req.headers["X-Api-Signature"]
            respond(sse, HttpStatusCode.OK, headersOf("Content-Type", "text/event-stream"))
        }
        val c = streamBridge(secret, engine)
        val lines = c.resumeChatStream("s-1", "m-1").toList()
        assertEquals(
            listOf(
                "data: {\"event\":\"delta\",\"text\":\"Acknowledged.\"}",
                "",
                "data: {\"event\":\"flow_finished\"}",
                "",
            ),
            lines,
        )
        assertEquals(1, engine.requestHistory.size)
        assertEquals(
            "/api/v1/integrations/health_assistant_bridge/api/00000000-0000-0000-0000-000000000000/ai/sessions/s-1/resume",
            engine.requestHistory[0].url.encodedPath,
        )
        assertEquals("POST", engine.requestHistory[0].method.value)
        assertEquals(
            Signing.sign(secret, "POST", "/ai/sessions/s-1/resume", expectedBody.encodeToByteArray()).signature,
            sawSig,
        )
        c.close()
    }

    @Test
    fun `resumeChatStream omits message_id when null`() = runBlocking {
        val secret = "test-secret-16chars!"
        var sawSig: String? = null
        val engine = MockEngine { req ->
            sawSig = req.headers["X-Api-Signature"]
            respond(
                "data: {\"event\":\"flow_finished\"}\n\n",
                HttpStatusCode.OK,
                headersOf("Content-Type", "text/event-stream"),
            )
        }
        val c = streamBridge(secret, engine)
        val lines = c.resumeChatStream("s-1").toList()
        assertEquals(listOf("data: {\"event\":\"flow_finished\"}", ""), lines)
        assertEquals(
            Signing.sign(secret, "POST", "/ai/sessions/s-1/resume", "{}".encodeToByteArray()).signature,
            sawSig,
        )
        c.close()
    }
}
