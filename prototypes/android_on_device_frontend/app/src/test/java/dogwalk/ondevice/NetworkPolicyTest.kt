package dogwalk.ondevice

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.net.UnknownServiceException
import javax.xml.parsers.DocumentBuilderFactory

class NetworkPolicyTest {
    private fun config(source: String) = DocumentBuilderFactory.newInstance().newDocumentBuilder()
        .parse(File("src/$source/res/xml/network_security_config.xml"))

    @Test fun tailnetExceptionIsDebugOnly() {
        val production = config("main")
        val debug = config("debug")
        for (doc in listOf(production, debug)) {
            val base = doc.getElementsByTagName("base-config").item(0) as org.w3c.dom.Element
            assertEquals("false", base.getAttribute("cleartextTrafficPermitted"))
        }
        fun hosts(doc: org.w3c.dom.Document): Set<String> {
            val domains = doc.getElementsByTagName("domain")
            return (0 until domains.length).map { domains.item(it).textContent.trim() }.toSet()
        }
        assertFalse(hosts(production).contains("100.85.3.93"))
        assertEquals(setOf("localhost", "127.0.0.1", "10.0.2.2", "100.85.3.93"), hosts(debug))
    }

    @Test fun cleartextFailureIsActionableWithoutDisclosingUrl() {
        val message = OrchestratorLink.failureReason(UnknownServiceException(
            "CLEARTEXT communication to private.invalid with secret-token not permitted"))
        assertTrue(message.contains("Android blocked cleartext"))
        assertFalse(message.contains("private.invalid"))
        assertFalse(message.contains("secret-token"))
        assertEquals("failed: UnknownServiceException", OrchestratorLink.failureReason(UnknownServiceException("other cause")))
    }
}
