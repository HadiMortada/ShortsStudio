package com.shortsstudio.app

import org.json.JSONObject

fun parseClaudeText(text: String): Pair<String,List<String>> {
    val clean = text.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
    val j = JSONObject(clean)
    val speech = j.getString("narration")
    val arr = j.getJSONArray("scenes")
    val scenes = (0 until arr.length()).map { arr.getString(it) }
    require(speech.isNotBlank() && scenes.isNotEmpty()) { "Claude response did not contain narration and scenes" }
    return speech to scenes
}

