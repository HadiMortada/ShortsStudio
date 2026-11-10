package com.shortsstudio.app

import android.graphics.*
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils
import kotlin.math.*

/** Executes Claude's layer geometry and keyframes; no built-in visual style. */
object MotionDrawing {
    fun draw(canvas:Canvas,plan:MotionPlan,progress:Float) {
        val (index,t)=MotionMath.scene(plan.scenes.map { it.duration },progress)
        val scene=plan.scenes[index]
        val paint=Paint(Paint.ANTI_ALIAS_FLAG)
        paint.shader=LinearGradient(0f,0f,432f,768f,Color.parseColor(scene.background[0]),
            Color.parseColor(scene.background[1]),Shader.TileMode.CLAMP)
        canvas.drawRect(0f,0f,432f,768f,paint);paint.shader=null
        for(layer in scene.layers) {
            val f=MotionMath.sample(layer.frames,t)
            if(f.opacity<=0f || f.scale<=0f)continue
            val w=layer.width*432f;val h=layer.height*768f
            canvas.save()
            canvas.translate(f.x*432f,f.y*768f)
            canvas.rotate(f.rotation);canvas.scale(f.scale,f.scale)
            paint.color=Color.parseColor(layer.color);paint.alpha=(f.opacity*255).toInt()
            paint.style=if(layer.stroke>0)Paint.Style.STROKE else Paint.Style.FILL
            paint.strokeWidth=max(1f,layer.stroke);paint.strokeCap=Paint.Cap.ROUND
            val box=RectF(-w/2,-h/2,w/2,h/2)
            when(layer.kind) {
                "rect"->canvas.drawRoundRect(box,layer.radius,layer.radius,paint)
                "circle"->canvas.drawOval(box,paint)
                "line"->canvas.drawLine(-w/2,-h/2,w/2,h/2,paint)
                "polygon"->{
                    val path=Path()
                    layer.points.forEachIndexed { i,p ->
                        val x=(p.first-.5f)*w;val y=(p.second-.5f)*h
                        if(i==0)path.moveTo(x,y)else path.lineTo(x,y)
                    };path.close();canvas.drawPath(path,paint)
                }
                "text"->{
                    val textPaint=TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
                        color=Color.parseColor(layer.color);alpha=(f.opacity*255).toInt()
                        typeface=Typeface.create(when(layer.font){"serif"->"serif";"mono"->"monospace";else->"sans-serif"},
                            if(layer.bold)Typeface.BOLD else Typeface.NORMAL)
                        textSize=layer.fontSize
                    }
                    val align=when(layer.align){"left"->Layout.Alignment.ALIGN_NORMAL;"right"->Layout.Alignment.ALIGN_OPPOSITE;else->Layout.Alignment.ALIGN_CENTER}
                    fun layout()=StaticLayout.Builder.obtain(layer.text,0,layer.text.length,textPaint,max(1,w.toInt()))
                        .setAlignment(align).setIncludePad(false).setLineSpacing(0f,1.08f)
                        .setMaxLines(8).setEllipsize(TextUtils.TruncateAt.END).build()
                    var text=layout()
                    if(text.height>h){textPaint.textSize=(textPaint.textSize*h/text.height).coerceAtLeast(10f);text=layout()}
                    canvas.translate(-w/2,-text.height/2f);text.draw(canvas)
                }
            }
            canvas.restore()
        }
    }
}
