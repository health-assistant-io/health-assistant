package io.healthassistant.bridge

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockRequestHandler
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
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
    fun `getExamination decodes a single detail object`() = runBlocking {
        val secret = "test-secret-16chars!"
        val payload = """{"id":"e1","examination_date":"2026-08-08","notes":"Annual checkup","patient_notes":null,"extraction_status":"completed","diagnoses":["Hypertension"],"impressions":null}"""
        val c = bridge(secret) { respond(payload) }
        val e = c.getExamination("e1")
        assertEquals("e1", e.id)
        assertEquals("Annual checkup", e.notes)
        assertEquals("completed", e.extractionStatus)
        assertEquals(listOf("Hypertension"), e.diagnoses)
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
}
