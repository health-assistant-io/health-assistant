package io.healthassistant.bridge

// Schema conformance wall (plan 32 ADR-0040 D11 — the bridge goes
// schema-first). The Kotlin model layer stays hand-maintained (no family-
// internal Kotlin codegen — the recorded follow-up path is a Kotlin
// codegen target when a second consumer demands it); THIS test is the wall:
// every shape Models.kt covers must deserialize the schema's example
// payloads — the same fixtures.json the python/ts suites consume
// (implementations may differ, outputs may not). SigningParityTest and
// BridgeClientTest are untouched and remain the transport walls.

import java.io.File
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class SchemaConformanceTest {

    // Same parsing config as the client (BridgeClient.json) — the wall runs
    // under production parsing semantics, not stricter test settings.
    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = false
    }

    private fun repoRoot(): File {
        var dir = File(System.getProperty("user.dir"))
        repeat(6) {
            if (File(dir, "backend/app/extensions/builtin/health_assistant_bridge/schema").isDirectory) {
                return dir
            }
            dir = dir.parentFile ?: throw IllegalStateException("repo root not found")
        }
        throw IllegalStateException("repo root not found")
    }

    private val schemaDir =
        File(repoRoot(), "backend/app/extensions/builtin/health_assistant_bridge/schema")

    private val schema: JsonObject =
        Json.parseToJsonElement(File(schemaDir, "bridge-api.schema.json").readText()).jsonObject

    private val fixtures: JsonObject =
        Json.parseToJsonElement(File(schemaDir, "fixtures.json").readText()).jsonObject

    private fun components(): JsonObject = schema["components"]!!.jsonObject

    private fun exampleOf(component: String): JsonObject =
        components()[component]!!.jsonObject["examples"]!!.jsonArray[0].jsonObject

    private fun componentExampleEntries(): List<Triple<String, String, String?>> {
        // (route, responseComponent, requestComponent) — the fixture wall
        // entries, mirrored from fixtures.json's componentExamples.
        val entries = fixtures["componentExamples"]!!.jsonArray.map { el ->
            val o = el.jsonObject
            Triple(
                o["route"]!!.jsonPrimitive.content,
                o["responseComponent"]!!.jsonPrimitive.content,
                o["requestComponent"]?.jsonPrimitive?.content,
            )
        }
        return entries
    }

    /** Models.kt coverage: response component → serializer. Entries absent
     *  here are shapes the Kotlin SDK does not model (envelope wrappers,
     *  request-only payloads the client builds inline) — they stay covered
     *  by the python/ts suites and the backend validator. */
    @Suppress("UNCHECKED_CAST")
    private fun responseCoverage(): Map<String, KSerializer<out Any>> = mapOf(
        "BridgeStatus" to BridgeStatus.serializer(),
        "MapResponse" to MapResponsePayload.serializer(),
        "SyncResponse" to SyncResponse.serializer(),
        "ChatTurnResult" to ChatTurnResult.serializer(),
        "ChatMessage" to ChatMessage.serializer(),
        "SessionRenameResult" to SessionRenameResult.serializer(),
        "TranscriptionResult" to TranscriptionResult.serializer(),
        "StatusAck" to StatusAck.serializer(),
        "ReadAllResult" to ReadAllResult.serializer(),
        "UnreadCount" to UnreadCount.serializer(),
        "NotificationKind" to NotificationKind.serializer(),
        "PreferenceToggleResult" to PreferenceToggleResult.serializer(),
        "NotificationTrigger" to NotificationTrigger.serializer(),
        "MobilePushTarget" to MobilePushTarget.serializer(),
        "DeleteResult" to DeleteResult.serializer(),
        "Medication" to Medication.serializer(),
        "Allergy" to Allergy.serializer(),
        "Vaccine" to Vaccine.serializer(),
        "ClinicalEventSummary" to ClinicalEvent.serializer(),
        "Doctor" to Doctor.serializer(),
        "ObservationPoint" to ObservationPoint.serializer(),
        "BiomarkerSummary" to BiomarkerSummary.serializer(),
        "DocumentSummary" to DocumentSummary.serializer(),
        "DocumentText" to DocumentText.serializer(),
        "ExaminationSummary" to ExaminationSummary.serializer(),
        "ExaminationDetail" to ExaminationDetail.serializer(),
    )

    /** Envelope-flavored fixture entries: the component rides the universal
     *  read envelope (data/cursor/cached_at) on its route. */
    private fun envelopedComponents(): Set<String> =
        setOf(
            "ObservationPoint",
            "BiomarkerSummary",
            "DocumentSummary",
            "DocumentText",
            "Medication",
            "Allergy",
            "Vaccine",
            "ClinicalEventSummary",
            "Doctor",
            "NotificationKind",
            "NotificationTrigger",
            "MobilePushTarget",
        )

    @Test
    fun `schema and fixtures are present and versioned`() {
        assertEquals(1, schema["schemaVersion"]!!.jsonPrimitive.content.toInt())
        assertNotNull(fixtures["componentExamples"])
        assertTrue(fixtures["errors"]!!.jsonArray.isNotEmpty())
        assertTrue(fixtures["stream"]!!.jsonObject["frames"]!!.jsonArray.isNotEmpty())
    }

    @Test
    fun `covered models deserialize the schema example payloads`() {
        val coverage = responseCoverage()
        val enveloped = envelopedComponents()
        var covered = 0
        for ((route, component, _request) in componentExampleEntries()) {
            val serializer = coverage[component] ?: continue
            covered++
            val payload = exampleOf(component)
            val wire = if (component in enveloped) {
                buildJsonObject {
                    put("data", JsonArray(listOf(payload)))
                    put("cursor", JsonNull)
                    put("cached_at", JsonPrimitive("2026-10-17T09:00:00+00:00"))
                }
            } else {
                payload
            }
            @Suppress("UNCHECKED_CAST")
            val itemSerializer = serializer as KSerializer<Any>
            val decoded: Any = if (component in enveloped) {
                json.decodeFromString(ReadEnvelope.serializer(itemSerializer), wire.toString())
                    .data.single()
            } else {
                json.decodeFromString(itemSerializer, wire.toString())
            }
            assertNotNull(decoded, "$route ($component) decoded to null")
        }
        // The wall must not silently shrink — Models.kt covers this many
        // fixture shapes today.
        assertTrue(covered >= 20, "kotlin fixture coverage regressed: $covered")
    }

    @Test
    fun `device registration payload deserializes`() {
        val entry = componentExampleEntries().firstOrNull {
            it.third == "DeviceRegistrationRequest"
        }
        assertNotNull(entry, "DeviceRegistrationRequest fixture entry missing")
        val routeEntry = fixtures["routes"]!!.jsonObject[entry!!.first]!!.jsonObject
        val requestExample = routeEntry["requestExample"]!!.jsonObject
        val registration = json.decodeFromString(
            DeviceRegistration.serializer(),
            requestExample.toString(),
        )
        assertEquals(registration.platform, requestExample["platform"]!!.jsonPrimitive.content)
    }

    @Test
    fun `typed error fixtures decode through BridgeException`() {
        val liveCodes = schema["errors"]!!.jsonObject["codes"]!!.jsonObject.keys
        for (error in fixtures["errors"]!!.jsonArray) {
            val o = error.jsonObject
            val body = o["body"]!!.jsonObject
            val exception = BridgeException.from(
                o["status"]!!.jsonPrimitive.content.toInt(),
                body.toString(),
            )
            if (o["typed"]!!.jsonPrimitive.content == "true") {
                val code = body["detail"]!!.jsonObject["code"]!!.jsonPrimitive.content
                assertEquals(code, exception.code)
                assertTrue(code in liveCodes, "fixture code $code not in the schema vocabulary")
                // The typed subclasses stay pinned to their codes.
                when (code) {
                    "turn_conflict" -> assertTrue(exception is TurnConflictException)
                    "hitl_conflict" -> assertTrue(exception is HitlConflictException)
                    "not_found" -> assertTrue(exception is NotFoundException)
                    else -> {}
                }
            } else {
                assertEquals(null, exception.code)
            }
        }
    }

    @Test
    fun `stream fixture frames carry the closed event vocabulary`() {
        val served = schema["sse"]!!.jsonObject["eventNames"]!!.jsonArray
            .map { it.jsonPrimitive.content }.toSet()
        for (frame in fixtures["stream"]!!.jsonObject["frames"]!!.jsonArray) {
            val o = frame.jsonObject
            val event = o["event"]!!.jsonPrimitive.content
            if (event == "keep-alive") {
                assertTrue(o["line"]!!.jsonPrimitive.content.startsWith(":"))
                continue
            }
            assertTrue(event in served, "frame event $event not in the schema vocabulary")
            assertTrue(o["line"]!!.jsonPrimitive.content.startsWith("data: "))
        }
    }
}
