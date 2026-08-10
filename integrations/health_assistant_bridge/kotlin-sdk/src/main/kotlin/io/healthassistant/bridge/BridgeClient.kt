package io.healthassistant.bridge

import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.request
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpMethod
import io.ktor.http.contentType
import kotlinx.coroutines.delay
import kotlinx.serialization.json.Json
import java.io.IOException
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

    // --- internals ------------------------------------------------------

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
