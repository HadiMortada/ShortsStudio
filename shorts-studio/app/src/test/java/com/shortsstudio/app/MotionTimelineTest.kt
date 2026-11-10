package com.shortsstudio.app

import org.junit.Assert.*
import org.junit.Test

class MotionTimelineTest {
    @Test fun keyframesDrivePositionOpacityAndRotation() {
        val frames=listOf(MotionFrame(0f,.1f,.8f,1f,0f,0f,"linear"),
            MotionFrame(1f,.9f,.2f,2f,180f,1f,"linear"))
        val middle=MotionMath.sample(frames,.5f)
        assertEquals(.5f,middle.x,.001f)
        assertEquals(.5f,middle.y,.001f)
        assertEquals(90f,middle.rotation,.001f)
        assertEquals(.5f,middle.opacity,.001f)
        assertEquals(.1f,MotionMath.sample(frames,-1f).x,.001f)
        assertEquals(.9f,MotionMath.sample(frames,2f).x,.001f)
    }

    @Test fun sceneClockUsesDesignedDurationsRatherThanEqualCards() {
        val (index,inside)=MotionMath.scene(listOf(2f,8f),.6f)
        assertEquals(1,index)
        assertEquals(.5f,inside,.001f)
        assertEquals(1f,MotionMath.scene(listOf(2f,8f),1f).second,.001f)
    }

    @Test fun parserPreservesCustomArtDirectionAndRejectsBrokenGeometry() {
        val text="""{"version":1,"title":"Orbits","direction":"Editorial orbital diagrams","language":"en","narration":"Ideas move.","scenes":[{"duration":7.5,"background":["#101020","#202040"],"layers":[{"kind":"circle","color":"#ffaa44","width":0.4,"height":0.3,"frames":[{"t":0,"x":0.2,"y":0.4},{"t":1,"x":0.8,"y":0.6}]}]},{"duration":7.5,"background":["#000000","#111111"],"layers":[{"kind":"text","text":"Move","color":"#ffffff","width":0.8,"height":0.3,"frames":[{"t":0,"x":0.5,"y":0.7},{"t":1,"x":0.5,"y":0.3}]}]}]}"""
        val plan=MotionPlan.parse(text)
        assertEquals("Editorial orbital diagrams",plan.direction)
        assertEquals("circle",plan.scenes[0].layers[0].kind)
        assertEquals(.8f,plan.scenes[0].layers[0].frames[1].x,.001f)
        for (bad in listOf(text.replace("0.8","9.8"),text.replace("#ffaa44","not-a-color"),
            text.replace("\"t\":1","\"t\":0"))) {
            try { MotionPlan.parse(bad); fail("Invalid timeline accepted") }
            catch (_: IllegalArgumentException) { }
        }
    }
}
