package com.shortsstudio.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Android Keystore-backed AES-GCM storage for the runner bearer token. */
internal object SecretStore {
    private const val alias = "ShortsStudioRunnerToken"
    private const val tokenEntry = "secure_token"

    private fun secretKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore")
        store.load(null)
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    fun save(context: Context, token: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
        val data = Base64.encodeToString(cipher.doFinal(token.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
        context.getSharedPreferences("studio", 0).edit()
            .putString(tokenEntry, iv + ":" + data)
            .remove("token")
            .apply()
    }

    fun load(context: Context): String {
        val prefs = context.getSharedPreferences("studio", 0)
        val legacy = prefs.getString("token", "") ?: ""
        if (legacy.isNotEmpty()) {
            // One-time migration from older plaintext APK versions.
            save(context, legacy)
            return legacy
        }
        val packed = prefs.getString(tokenEntry, null) ?: return ""
        return try {
            val parts = packed.split(":", limit = 2)
            if (parts.size != 2) return ""
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, secretKey(),
                GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)))
            String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), Charsets.UTF_8)
        } catch (_: Exception) {
            // A restored/invalidated key cannot decrypt old tokens; re-connect instead.
            ""
        }
    }
}
