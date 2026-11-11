package dogwalk.ondevice

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Private, nonbackup ciphertext. No readback in the UI, intents, history or diagnostics. */
class AppCredential(context: Context) {
    private val file = java.io.File(context.noBackupFilesDir, "app-credential")
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("dogwalk-app", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("dogwalk-app", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun save(endpoint: String, token: String) {
        require(Regex("[A-Za-z0-9_-]{43,128}").matches(token))
        requireRealEndpoint(endpoint)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        cipher.updateAAD(endpoint.toByteArray())
        file.writeText(Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + "\n" + Base64.encodeToString(cipher.doFinal(token.toByteArray()), Base64.NO_WRAP))
    }
    fun load(endpoint: String): String {
        requireRealEndpoint(endpoint)
        val parts = file.readLines()
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP))) }
        cipher.updateAAD(endpoint.toByteArray())
        return String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)))
    }
    fun clear() { file.delete() }
    companion object {
        fun requireRealEndpoint(endpoint: String) {
            val u = java.net.URI(endpoint)
            require(u.scheme == "wss" && !u.host.isNullOrBlank() && u.rawUserInfo == null && u.rawQuery == null && u.rawFragment == null && u.path == "/app/frontend")
        }
    }
}
