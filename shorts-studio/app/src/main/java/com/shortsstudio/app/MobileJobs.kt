package com.shortsstudio.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Duration
import java.time.ZonedDateTime
import java.util.concurrent.TimeUnit

object MobileJobs {
    private const val SCHEDULE_NAME = "shorts_daily_local"
    @Synchronized
    fun saveVideo(context: Context, file: File, topic: String) {
        val prefs = context.getSharedPreferences("mobile",Context.MODE_PRIVATE)
        val arr = JSONArray(prefs.getString("videos","[]"))
        if ((0 until arr.length()).any { arr.optJSONObject(it)?.optString("path") == file.absolutePath }) return
        arr.put(JSONObject().put("path",file.absolutePath).put("topic",topic)
            .put("timestamp",System.currentTimeMillis()))
        prefs.edit().putString("videos",arr.toString()).apply()
    }
    fun list(context: Context): List<Pair<String,File>> {
        val prefs = context.getSharedPreferences("mobile",Context.MODE_PRIVATE)
        val arr = JSONArray(prefs.getString("videos","[]"))
        return (arr.length()-1 downTo 0).mapNotNull {
            val row = arr.optJSONObject(it)
            val file = File(row?.optString("path").orEmpty())
            if(row!=null && file.exists()) row.optString("topic") to file else null
        }
    }
    fun schedule(context: Context, topic: String, script: String, voice: String, style: String, clock: String) {
        val parts = clock.split(":").map { it.toInt() }
        require(parts.size==2 && parts[0] in 0..23 && parts[1] in 0..59) { "Enter time as HH:mm" }
        val now = ZonedDateTime.now()
        var due = now.withHour(parts[0]).withMinute(parts[1]).withSecond(0).withNano(0)
        if(!due.isAfter(now)) due=due.plusDays(1)
        val delay = Duration.between(now,due).toMillis()
        val request = OneTimeWorkRequestBuilder<RenderWorker>()
            .setInitialDelay(delay,TimeUnit.MILLISECONDS)
            .setInputData(workDataOf("topic" to topic,"script" to script,
                "voice" to voice,"style" to style,"clock" to clock))
            .build()
        context.getSharedPreferences("mobile",0).edit().putString("schedule",clock).apply()
        WorkManager.getInstance(context).enqueueUniqueWork(SCHEDULE_NAME,
            ExistingWorkPolicy.REPLACE,request)
    }
    fun scheduleDirector(context: Context, topic: String, voice: String, seconds: Int, clock: String) {
        require(topic.toByteArray(Charsets.UTF_8).size<=7000) { "Shorten the brief before scheduling it" }
        val parts=clock.split(":").map { it.toInt() }
        require(parts.size==2 && parts[0] in 0..23 && parts[1] in 0..59) { "Enter time as HH:mm" }
        val now=ZonedDateTime.now()
        var due=now.withHour(parts[0]).withMinute(parts[1]).withSecond(0).withNano(0)
        if(!due.isAfter(now))due=due.plusDays(1)
        val request=OneTimeWorkRequestBuilder<RenderWorker>()
            .setInitialDelay(Duration.between(now,due).toMillis(),TimeUnit.MILLISECONDS)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setInputData(workDataOf("topic" to topic,"voice" to voice,"seconds" to seconds,"clock" to clock,"director" to true))
            .build()
        context.getSharedPreferences("mobile",0).edit().putString("schedule",clock).apply()
        WorkManager.getInstance(context).enqueueUniqueWork(SCHEDULE_NAME,ExistingWorkPolicy.REPLACE,request)
    }
    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(SCHEDULE_NAME)
        context.getSharedPreferences("mobile",0).edit().remove("schedule").apply()
    }
    fun notify(context: Context, message: String) {
        val manager=context.getSystemService(NotificationManager::class.java)
        val id="studio_video"
        manager.createNotificationChannel(NotificationChannel(id,"Shorts Studio",NotificationManager.IMPORTANCE_DEFAULT))
        val open=PendingIntent.getActivity(context,0,Intent(context,MainActivity::class.java),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification=NotificationCompat.Builder(context,id).setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Shorts Studio").setContentText(message).setAutoCancel(true)
            .setContentIntent(open).build()
        manager.notify((System.currentTimeMillis()%100000).toInt(),notification)
    }
}

class RenderWorker(context: Context, params: WorkerParameters): CoroutineWorker(context,params) {
    override suspend fun doWork(): Result {
        val topic=inputData.getString("topic") ?: return Result.failure()
        val script=inputData.getString("script").orEmpty()
        val voice=inputData.getString("voice").orEmpty()
        val style=inputData.getString("style") ?: "Neon kinetic"
        val clock=inputData.getString("clock").orEmpty()
        val director=inputData.getBoolean("director",false)
        val seconds=inputData.getInt("seconds",30)
        return try {
            val file=if(director) {
                CloudConnection.produce(applicationContext,topic,seconds,voice)
            } else {
                val text=script.ifBlank { MobileVideo.fallbackNarration(topic) }
                val scenes=MobileVideo.localStoryboard(topic,text)
                MobileVideo.make(applicationContext,topic,text,scenes,voice,style,30)
            }
            MobileJobs.saveVideo(applicationContext,file,topic)
            MobileJobs.notify(applicationContext,"Video ready for YouTube: $topic")
            Result.success()
        } catch(e:Exception) {
            MobileJobs.notify(applicationContext,"Could not generate '$topic': ${e.message?.take(60)}")
            Result.failure()
        } finally {
            // WorkManager is best effort; Android battery limits can delay this run.
            if(clock.isNotBlank() && applicationContext.getSharedPreferences("mobile",0).getString("schedule","") == clock) {
                try {
                    if(director)MobileJobs.scheduleDirector(applicationContext,topic,voice,seconds,clock)
                    else MobileJobs.schedule(applicationContext,topic,script,voice,style,clock)
                }
                catch(_:Exception) {}
            }
        }
    }
}



