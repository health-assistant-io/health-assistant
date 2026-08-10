package io.healthassistant.bridge

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * HMAC request signing for the bridge. Mirrors the server-side canonical form
 * (integrations/sdk/webhook_security.py:verify_canonical_signature) and the
 * Python SDK (python-sdk/.../signing.py):
 *
 *   canonical = METHOD + "\n" + path + "\n" + timestamp + "\n" + raw_body
 *
 * `path` is the component AFTER the integration id, WITH a leading slash
 * ("/sync"). `rawBody` is the exact bytes sent. The resulting hex HMAC-SHA256
 * is sent as `X-Api-Signature`; `X-Api-Timestamp` is the epoch-second string.
 *
 * `GET /status` is NEVER signed (connectivity + SDK-discovery probe).
 */
object Signing {

    data class Headers(val signature: String, val timestamp: String)

    fun sign(
        apiSecret: String,
        method: String,
        path: String,
        rawBody: ByteArray = ByteArray(0),
        timestamp: Long = System.currentTimeMillis() / 1_000,
    ): Headers {
        val ts = timestamp.toString()
        val canonical = (
            method.uppercase().toByteArray(Charsets.UTF_8) + Newline +
                path.toByteArray(Charsets.UTF_8) + Newline +
                ts.toByteArray(Charsets.UTF_8) + Newline +
                rawBody
        )
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(apiSecret.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return Headers(toHex(mac.doFinal(canonical)), ts)
    }

    private val Newline = byteArrayOf('\n'.code.toByte())

    private fun toHex(bytes: ByteArray): String =
        bytes.joinToString("") { "%02x".format(it) }
}
