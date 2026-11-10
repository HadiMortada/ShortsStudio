package com.shortsstudio.app

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

/** Caller validates the initial paired endpoint. Only same-origin result redirects are followed. */
object CloudHttp {
    fun resultUrl(original:URL,location:String):URL {
        require(location.isNotBlank()) { "Modal returned a redirect without a result URL" }
        val target=URL(original,location)
        fun port(u:URL)=if(u.port==-1)u.defaultPort else u.port
        require(target.protocol==original.protocol && target.host.equals(original.host,true) &&
            port(target)==port(original) && target.userInfo==null && target.ref==null && target.path==original.path) {
            "The server redirected outside the paired result endpoint"
        }
        return target
    }

    fun postJson(original:URL,token:String,body:String,timeoutMs:Int):JSONObject {
        val deadline=System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(timeoutMs.toLong())
        var url=original;var method="POST"
        for(hop in 0..8) {
            val remaining=TimeUnit.NANOSECONDS.toMillis(deadline-System.nanoTime()).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
            check(remaining>0) { "Claude took too long. Check your connection and try again." }
            val connection=url.openConnection() as HttpURLConnection
            try {
                connection.instanceFollowRedirects=false
                connection.requestMethod=method
                connection.connectTimeout=minOf(20_000,remaining)
                connection.readTimeout=remaining
                connection.setRequestProperty("Authorization","Bearer $token")
                if(method=="POST") {
                    connection.setRequestProperty("Content-Type","application/json")
                    connection.doOutput=true
                    connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
                }
                val code=connection.responseCode
                if(code==303) {
                    check(hop<8) { "Too many Modal result redirects. Try again later." }
                    url=resultUrl(original,connection.getHeaderField("Location").orEmpty())
                    // Modal's result URL points to the existing invocation. Never repeat its POST.
                    method="GET"
                    continue
                }
                val stream=if(code in 200..299)connection.inputStream else connection.errorStream
                val raw=stream?.bufferedReader(Charsets.UTF_8)?.use { reader ->
                    val result=StringBuilder();val buffer=CharArray(4096)
                    while(true) {
                        check(System.nanoTime()<deadline) { "Cloud response timed out" }
                        val count=reader.read(buffer);if(count<0)break
                        check(result.length+count<=1_000_000) { "Cloud response was too large" }
                        result.append(buffer,0,count)
                    };result.toString()
                }.orEmpty()
                val json=try { JSONObject(raw) }catch(_:Exception){error("Cloud service unavailable (HTTP $code)")}
                check(code in 200..299) { json.optString("detail","Cloud request failed (HTTP $code)") }
                return json
            } finally { connection.disconnect() }
        }
        error("Too many Modal result redirects")
    }
}
