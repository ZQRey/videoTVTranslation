package com.example.streamclient.data

import android.net.Uri

/**
 * Тип сетевого потока вещания.
 */
enum class StreamType {
    RTSP,
    HLS
}

/**
 * Дата-класс параметров стрима с медиасервера.
 */
data class StreamConfig(
    val serverHost: String = "",
    val streamType: StreamType = StreamType.RTSP,
    val rtspPort: Int = 8554,
    val hlsPort: Int = 8888,
    val streamPath: String = "live"
) {
    /**
     * Проверка, задан ли адрес сервера.
     */
    val isConfigured: Boolean
        get() = serverHost.trim().isNotEmpty()

    /**
     * Построение готового URI для ExoPlayer в зависимости от выбранного протокола.
     */
    fun buildStreamUri(): Uri {
        val cleanHost = serverHost.trim()
            .removePrefix("http://")
            .removePrefix("https://")
            .removePrefix("rtsp://")
            .trimEnd('/')

        val cleanPath = streamPath.trim().removePrefix("/").trimEnd('/')

        val uriString = when (streamType) {
            StreamType.RTSP -> "rtsp://$cleanHost:$rtspPort/$cleanPath"
            StreamType.HLS -> "http://$cleanHost:$hlsPort/$cleanPath"
        }
        return Uri.parse(uriString)
    }

    /**
     * Текстовое представление URL для отображения в HUD.
     */
    fun toDisplayString(): String = buildStreamUri().toString()
}
