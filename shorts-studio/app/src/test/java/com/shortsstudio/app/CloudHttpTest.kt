package com.shortsstudio.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import java.net.URL
import java.util.concurrent.TimeUnit

class CloudHttpTest {
    @Test fun modal303RetrievesExistingResultWithoutRepostingTheBrief() {
        val server=MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(303).addHeader("Location","/direct?__modal_function_call_id=existing-result"))
            server.enqueue(MockResponse().setBody("{\"result\":\"finished\"}"))
            val result=CloudHttp.postJson(server.url("/direct").toUrl(),"private-test-key","{\"brief\":\"Motion\"}",5000)
            assertEquals("finished",result.getString("result"))
            assertEquals(2,server.requestCount)
            val post=server.takeRequest(1,TimeUnit.SECONDS)!!
            assertEquals("POST",post.method)
            assertEquals("{\"brief\":\"Motion\"}",post.body.readUtf8())
            val get=server.takeRequest(1,TimeUnit.SECONDS)!!
            assertEquals("GET",get.method)
            assertEquals("/direct?__modal_function_call_id=existing-result",get.path)
            assertEquals(0L,get.bodySize)
            for(request in listOf(post,get))assertEquals("Bearer private-test-key",request.getHeader("Authorization"))
        } finally { server.shutdown() }
    }

    @Test fun redirectCannotMoveCredentialsToAnotherOriginOrPath() {
        val original=URL("https://mine.modal.run/direct")
        assertEquals("https://mine.modal.run/direct?__modal_result=ok",CloudHttp.resultUrl(original,"/direct?__modal_result=ok").toString())
        for(location in listOf("https://evil.test/direct","http://mine.modal.run/direct",
            "https://mine.modal.run:8443/direct","https://user@mine.modal.run/direct",
            "https://mine.modal.run/generate","https://mine.modal.run/direct#secret")) {
            try { CloudHttp.resultUrl(original,location);fail("Unsafe redirect accepted: $location") }
            catch(_:IllegalArgumentException) { }
        }
    }

    @Test fun redirectLoopsAreBoundedAndErrorsRemainReadable() {
        val server=MockWebServer()
        server.start()
        try {
            repeat(9){server.enqueue(MockResponse().setResponseCode(303).addHeader("Location","/direct?__modal_result=loop"))}
            try { CloudHttp.postJson(server.url("/direct").toUrl(),"test","{}",5000);fail("Loop accepted") }
            catch(e:IllegalStateException) { assertTrue(e.message.orEmpty().contains("redirect",true)) }
            assertEquals(9,server.requestCount)
            server.enqueue(MockResponse().setResponseCode(502).setBody("{\"detail\":\"Opus usage limit reached\"}"))
            try { CloudHttp.postJson(server.url("/error").toUrl(),"test","{}",5000);fail("Error accepted") }
            catch(e:IllegalStateException) { assertEquals("Opus usage limit reached",e.message) }
        } finally { server.shutdown() }
    }
    @Test fun pollingUsesAuthenticatedGetWithoutPostingAnotherJob() {
        val server=MockWebServer();server.start()
        try {
            server.enqueue(MockResponse().setBody("{\"state\":\"rendering\"}"))
            assertEquals("rendering",CloudHttp.getJson(server.url("/productions/test").toUrl(),"private-key").getString("state"))
            val request=server.takeRequest(1,TimeUnit.SECONDS)!!
            assertEquals("GET",request.method)
            assertEquals(0L,request.bodySize)
            assertEquals("Bearer private-key",request.getHeader("Authorization"))
        } finally {server.shutdown()}
    }

    @Test fun videoDownloadRejectsErrorsAndRemovesPartialFiles() {
        val server=MockWebServer();server.start()
        val folder=kotlin.io.path.createTempDirectory().toFile()
        val file=java.io.File(folder,"film.mp4")
        try {
            server.enqueue(MockResponse().addHeader("Content-Type","application/json").setBody("{\"error\":true}"))
            try {CloudHttp.downloadVideo(server.url("/video").toUrl(),"key",file);fail("JSON accepted as MP4")}
            catch(_:IllegalStateException){}
            assertFalse(file.exists())
            assertFalse(java.io.File(folder,"film.mp4.part").exists())
            server.enqueue(MockResponse().addHeader("Content-Type","video/mp4").setBody("x".repeat(2000)))
            CloudHttp.downloadVideo(server.url("/video").toUrl(),"key",file)
            assertEquals(2000L,file.length())
        } finally {server.shutdown();folder.deleteRecursively()}
    }

}

