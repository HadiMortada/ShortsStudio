package com.shortsstudio.app

import org.json.JSONObject
import kotlin.math.*

data class MotionFrame(val t:Float,val x:Float,val y:Float,val scale:Float=1f,
    val rotation:Float=0f,val opacity:Float=1f,val easing:String="smooth")
data class MotionLayer(val kind:String,val text:String,val color:String,val width:Float,val height:Float,
    val fontSize:Float,val bold:Boolean,val align:String,val font:String,val stroke:Float,val radius:Float,
    val points:List<Pair<Float,Float>>,val frames:List<MotionFrame>)
data class MotionScene(val duration:Float,val background:List<String>,val layers:List<MotionLayer>)
data class MotionPlan(val title:String,val direction:String,val language:String,val narration:String,val scenes:List<MotionScene>) {
    val seconds:Float get()=scenes.sumOf { it.duration.toDouble() }.toFloat()
    companion object {
        fun parse(text:String):MotionPlan {
            val root=JSONObject(text)
            val j=if(root.has("plan")) root.getJSONObject("plan") else root
            require(j.getInt("version")==1) { "Unsupported motion timeline version" }
            fun number(o:JSONObject,key:String,min:Float,max:Float,default:Float?=null):Float {
                val n=if(default!=null && !o.has(key)) default else o.getDouble(key).toFloat()
                require(n.isFinite() && n in min..max) { "Invalid motion value: $key" };return n
            }
            fun color(s:String):String { require(s.matches(Regex("#[0-9a-fA-F]{6}"))) { "Invalid motion color" };return s }
            val a=j.getJSONArray("scenes");require(a.length() in 2..8) { "Expected 2–8 designed scenes" }
            val scenes=(0 until a.length()).map { i ->
                val scene=a.getJSONObject(i);val bg=scene.getJSONArray("background")
                require(bg.length()==2)
                val layers=scene.getJSONArray("layers");require(layers.length() in 1..12)
                MotionScene(number(scene,"duration",.5f,30f),(0..1).map { color(bg.getString(it)) },
                    (0 until layers.length()).map { k ->
                        val l=layers.getJSONObject(k);val kind=l.getString("kind")
                        require(kind in listOf("text","rect","circle","line","polygon"))
                        val words=l.optString("text","");require(words.length<=240 && (kind!="text" || words.isNotBlank()))
                        val fs=l.getJSONArray("frames");require(fs.length() in 2..8)
                        val frames=(0 until fs.length()).map { f ->
                            val v=fs.getJSONObject(f);val ease=v.optString("easing","smooth")
                            require(ease in listOf("linear","smooth","out","spring"))
                            MotionFrame(number(v,"t",0f,1f),number(v,"x",-1f,2f),number(v,"y",-1f,2f),
                                number(v,"scale",0f,4f,1f),number(v,"rotation",-720f,720f,0f),
                                number(v,"opacity",0f,1f,1f),ease)
                        }
                        require(frames.first().t==0f && frames.last().t==1f && frames.zipWithNext().all { it.first.t<it.second.t })
                        val pts=l.optJSONArray("points");require((pts?.length() ?: 0)<=16)
                        val points=(0 until (pts?.length() ?: 0)).map { p ->
                            val v=pts!!.getJSONObject(p);number(v,"x",0f,1f) to number(v,"y",0f,1f)
                        };require(kind!="polygon" || points.size>=3)
                        val align=l.optString("align","center");val font=l.optString("font","sans")
                        require(align in listOf("left","center","right") && font in listOf("sans","serif","mono"))
                        MotionLayer(kind,words,color(l.getString("color")),number(l,"width",.001f,2f),
                            number(l,"height",.001f,2f),number(l,"fontSize",10f,100f,36f),l.optBoolean("bold",true),
                            align,font,number(l,"stroke",0f,16f,0f),number(l,"radius",0f,100f,0f),points,frames)
                    })
            }
            val narration=j.getString("narration");val title=j.getString("title");val direction=j.getString("direction")
            val language=j.getString("language")
            require(narration.isNotBlank() && narration.length<=3600 && title.isNotBlank() && title.length<=120)
            require(direction.isNotBlank() && direction.length<=500 && language.matches(Regex("[a-z]{2,3}(-[A-Za-z]{2,4})?")))
            return MotionPlan(title,direction,language,narration,scenes).also { require(it.seconds in 14.5f..60.5f) }
        }
    }
}

object MotionMath {
    fun sample(frames:List<MotionFrame>,progress:Float):MotionFrame {
        val t=progress.coerceIn(0f,1f)
        val end=frames.indexOfFirst { it.t>=t }.coerceAtLeast(0)
        if(end==0)return frames.first()
        val a=frames[end-1];val b=frames[end]
        val u=((t-a.t)/(b.t-a.t)).coerceIn(0f,1f)
        val eased=when(b.easing) {
            "linear"->u
            "out"->1f-(1f-u).pow(3)
            "spring"->1f+2.70158f*(u-1f).pow(3)+1.70158f*(u-1f).pow(2)
            else->u*u*(3f-2f*u)
        }
        fun mix(x:Float,y:Float)=x+(y-x)*eased
        return MotionFrame(t,mix(a.x,b.x),mix(a.y,b.y),mix(a.scale,b.scale).coerceAtLeast(0f),
            mix(a.rotation,b.rotation),mix(a.opacity,b.opacity).coerceIn(0f,1f),b.easing)
    }
    fun scene(durations:List<Float>,progress:Float):Pair<Int,Float> {
        val time=progress.coerceIn(0f,1f)*durations.sum();var start=0f
        for(i in durations.indices) {
            if(time<start+durations[i] || i==durations.lastIndex)
                return i to ((time-start)/durations[i]).coerceIn(0f,1f)
            start+=durations[i]
        };return 0 to 0f
    }
}
