package com.shortsstudio.app

import org.junit.Assert.*
import org.junit.Test

class CloudEndpointTest {
    @Test fun parsesPrivateSetupLinkWithoutExposingItsKey() {
        val key = "a".repeat(48)
        val pair = CloudEndpoint.parseSetupLink(
            "https://owner--shorts-studio-cloud-web.modal.run/#key=$key"
        )
        assertEquals("https://owner--shorts-studio-cloud-web.modal.run",pair.first)
        assertEquals(key,pair.second)
        for (bad in listOf(
            "https://evil.test/#key=$key",
            "http://owner.modal.run/#key=$key",
            "https://owner.modal.run/path#key=$key",
            "https://owner.modal.run/#key=short",
            "https://owner.modal.run/?key=$key"
        )) {
            try { CloudEndpoint.parseSetupLink(bad); fail("Unsafe setup link accepted: $bad") }
            catch (_: IllegalArgumentException) { }
        }
    }

    @Test fun acceptsOnlyRootHttpsModalHosts() {
        assertTrue(CloudEndpoint.isAllowed("https://hadimortada--shorts-studio-cloud-web.modal.run"))
        for (url in listOf("http://x.modal.run", "https://x.modal.run.evil.test", "https://evil.test",
            "https://x.modal.run@evil.test", "https://user@x.modal.run", "https://x.modal.run:8443",
            "https://x.modal.run/path", "https://x.modal.run?key=bad", "javascript:alert(1)", "")) {
            assertFalse(url, CloudEndpoint.isAllowed(url))
        }
    }
}
