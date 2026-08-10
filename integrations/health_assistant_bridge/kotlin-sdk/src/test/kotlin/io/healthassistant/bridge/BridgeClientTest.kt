package io.healthassistant.bridge

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockRequestHandler
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpStatusCode
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.assertEquals
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
}
