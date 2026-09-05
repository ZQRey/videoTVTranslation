package com.example.streamclient

import com.example.streamclient.data.StreamConfig
import com.example.streamclient.data.StreamType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StreamConfigTest {

    @Test
    fun testEmptyConfigIsNotConfigured() {
        val config = StreamConfig()
        assertFalse(config.isConfigured)
    }

    @Test
    fun testConfigWithHostIsConfigured() {
        val config = StreamConfig(serverHost = "192.168.1.100")
        assertTrue(config.isConfigured)
    }

    @Test
    fun testBuildRtspUri() {
        val config = StreamConfig(
            serverHost = "192.168.1.100",
            streamType = StreamType.RTSP,
            rtspPort = 8554,
            streamPath = "live"
        )
        assertEquals("rtsp://192.168.1.100:8554/live", config.toDisplayString())
    }

    @Test
    fun testBuildHlsUri() {
        val config = StreamConfig(
            serverHost = "tv.local",
            streamType = StreamType.HLS,
            hlsPort = 8888,
            streamPath = "live"
        )
        assertEquals("http://tv.local:8888/live", config.toDisplayString())
    }

    @Test
    fun testCleanPrefixesAndTrailingSlashes() {
        val config = StreamConfig(
            serverHost = "http://10.0.0.5/",
            streamType = StreamType.RTSP,
            rtspPort = 8554,
            streamPath = "/channel1/"
        )
        assertEquals("rtsp://10.0.0.5:8554/channel1", config.toDisplayString())
    }
}
