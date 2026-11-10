package com.shortsstudio.app

import org.junit.Assert.*
import org.junit.Test

class CloudEndpointTest {
    @Test fun acceptsOnlyRootHttpsModalHosts() {
        assertTrue(CloudEndpoint.isAllowed("https://hadimortada--shorts-studio-cloud-web.modal.run"))
        for (url in listOf("http://x.modal.run", "https://x.modal.run.evil.test", "https://evil.test",
            "https://x.modal.run@evil.test", "https://user@x.modal.run", "https://x.modal.run:8443",
            "https://x.modal.run/path", "https://x.modal.run?key=bad", "javascript:alert(1)", "")) {
            assertFalse(url, CloudEndpoint.isAllowed(url))
        }
    }
}
