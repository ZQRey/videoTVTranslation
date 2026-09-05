package com.example.streamclient.player

import android.content.Context
import android.util.Log
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.exoplayer.rtsp.RtspMediaSource
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.ui.PlayerView
import com.example.streamclient.data.StreamConfig
import com.example.streamclient.data.StreamType
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

private const val TAG = "PlayerController"
private const val DEFAULT_RECONNECT_INTERVAL_SEC = 4

/**
 * Контроллер медиадвижка на базе AndroidX Media3 ExoPlayer.
 * Реализует:
 * - Принудительный RTSP over TCP (исключение потерь пакетов UDP по Wi-Fi).
 * - Поддержку HLS потоков.
 * - Стабильный анти-джиттер буфер воспроизведения для исключения заиканий звука на ТВ-приставках.
 * - Отказоустойчивый Reconnect Loop с таймером обратного отсчета.
 */
class PlayerController(
    private val context: Context,
    private val coroutineScope: CoroutineScope
) {
    private var exoPlayer: ExoPlayer? = null
    private var currentConfig: StreamConfig? = null

    private val _state = MutableStateFlow<PlayerState>(PlayerState.Idle)
    val state: StateFlow<PlayerState> = _state.asStateFlow()

    private var reconnectJob: Job? = null
    private var isManuallyPaused = false

    /**
     * Создание и инициализация экземпляра ExoPlayer со стабильным аудиотрактом и анти-джиттер буфером.
     */
    private fun getOrCreatePlayer(): ExoPlayer {
        exoPlayer?.let { return it }

        // Оптимальный буфер для предотвращения опустошения аудиобуфера (AudioTrack underrun) при джиттере Wi-Fi
        val loadControl = DefaultLoadControl.Builder()
            .setBufferDurationsMs(
                /* minBufferMs = */ 4_000,
                /* maxBufferMs = */ 15_000,
                /* bufferForPlaybackMs = */ 1_500,
                /* bufferForPlaybackAfterRebufferMs = */ 2_000
            )
            .setPrioritizeTimeOverSizeThresholds(true)
            .setBackBuffer(0, false)
            .build()

        val audioAttributes = AudioAttributes.Builder()
            .setUsage(C.USAGE_MEDIA)
            .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
            .build()

        val renderersFactory = DefaultRenderersFactory(context)
            .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_OFF)
            .setEnableAudioTrackPlaybackParams(true)

        val player = ExoPlayer.Builder(context, renderersFactory)
            .setLoadControl(loadControl)
            .setAudioAttributes(audioAttributes, /* handleAudioFocus = */ true)
            .build()
            .apply {
                playWhenReady = true
                addListener(PlayerEventListener())
            }

        exoPlayer = player
        return player
    }

    /**
     * Привязка Surface плеера к интерфейсному компоненту PlayerView.
     */
    fun attachPlayerView(playerView: PlayerView) {
        val player = getOrCreatePlayer()
        playerView.player = player
    }

    /**
     * Запуск воспроизведения стрима по переданной конфигурации.
     */
    fun start(config: StreamConfig) {
        if (!config.isConfigured) {
            _state.value = PlayerState.Idle
            return
        }

        currentConfig = config
        isManuallyPaused = false
        cancelReconnect()

        val streamUri = config.buildStreamUri()
        val streamUrl = streamUri.toString()
        Log.i(TAG, "Запуск вещания: $streamUrl (Протокол: ${config.streamType})")
        _state.value = PlayerState.Connecting(streamUrl)

        val player = getOrCreatePlayer()

        // Создание источника данных согласно выбранному протоколу
        val mediaSource: MediaSource = when (config.streamType) {
            StreamType.RTSP -> {
                // КРИТИЧНО: Принудительный TCP для RTSP потока MediaMTX
                RtspMediaSource.Factory()
                    .setForceUseRtpTcp(true)
                    .setTimeoutMs(10_000)
                    .createMediaSource(MediaItem.fromUri(streamUri))
            }
            StreamType.HLS -> {
                val httpDataSourceFactory = DefaultHttpDataSource.Factory()
                    .setConnectTimeoutMs(8_000)
                    .setReadTimeoutMs(8_000)
                    .setAllowCrossProtocolRedirects(true)

                HlsMediaSource.Factory(httpDataSourceFactory)
                    .setAllowChunklessPreparation(true)
                    .createMediaSource(MediaItem.fromUri(streamUri))
            }
        }

        player.setMediaSource(mediaSource)
        player.prepare()
        player.playWhenReady = true
    }

    /**
     * Приостановка вещания (например, при открытии окна настроек по кнопке «Назад»).
     */
    fun pause() {
        isManuallyPaused = true
        cancelReconnect()
        exoPlayer?.pause()
    }

    /**
     * Возобновление вещания.
     */
    fun resume() {
        isManuallyPaused = false
        if (exoPlayer != null && exoPlayer?.playbackState == Player.STATE_READY) {
            exoPlayer?.play()
            currentConfig?.let {
                _state.value = PlayerState.Playing(it.toDisplayString())
            }
        } else {
            currentConfig?.let { start(it) }
        }
    }

    /**
     * Принудительный немедленный повтор подключения (по кнопке «Повторить сейчас»).
     */
    fun retryNow() {
        cancelReconnect()
        currentConfig?.let { start(it) }
    }

    private var isStandby = false

    /**
     * Перевод в спящий режим по серверному расписанию или ручной команде.
     */
    fun setStandby(standby: Boolean) {
        if (isStandby == standby) return
        isStandby = standby
        if (standby) {
            cancelReconnect()
            exoPlayer?.volume = 0f
            exoPlayer?.pause()
        } else {
            exoPlayer?.volume = 1f
            if (!isManuallyPaused) {
                resume()
            }
        }
    }

    /**
     * Удаленное управление громкостью (Mute / Unmute).
     */
    fun setAudioEnabled(enabled: Boolean) {
        if (!isStandby) {
            exoPlayer?.volume = if (enabled) 1f else 0f
        }
    }

    /**
     * Запуск фонового цикла Reconnect Loop при потере соединения.
     */
    private fun scheduleReconnect(errorMessage: String) {
        if (isManuallyPaused) return
        val config = currentConfig ?: return

        cancelReconnect()
        reconnectJob = coroutineScope.launch(Dispatchers.Main) {
            val streamUrl = config.toDisplayString()
            Log.w(TAG, "Старт цикла переподключения: $errorMessage")

            for (secondsLeft in DEFAULT_RECONNECT_INTERVAL_SEC downTo 1) {
                _state.value = PlayerState.Reconnecting(streamUrl, secondsLeft)
                delay(1000)
            }

            Log.i(TAG, "Повторная попытка подключения к $streamUrl...")
            start(config)
        }
    }

    private fun cancelReconnect() {
        reconnectJob?.cancel()
        reconnectJob = null
    }

    /**
     * Полное освобождение ресурсов плеера при уничтожении Activity.
     */
    fun release() {
        cancelReconnect()
        exoPlayer?.let {
            it.stop()
            it.clearMediaItems()
            it.release()
        }
        exoPlayer = null
        _state.value = PlayerState.Idle
    }

    /**
     * Слушатель событий жизненного цикла ExoPlayer.
     */
    private inner class PlayerEventListener : Player.Listener {

        override fun onPlaybackStateChanged(playbackState: Int) {
            val url = currentConfig?.toDisplayString() ?: ""
            when (playbackState) {
                Player.STATE_BUFFERING -> {
                    Log.d(TAG, "ExoPlayer: STATE_BUFFERING")
                    if (_state.value !is PlayerState.Reconnecting) {
                        _state.value = PlayerState.Connecting(url)
                    }
                }
                Player.STATE_READY -> {
                    Log.i(TAG, "ExoPlayer: STATE_READY — Поток успешно воспроизводится")
                    cancelReconnect()
                    _state.value = PlayerState.Playing(url)
                }
                Player.STATE_ENDED -> {
                    Log.w(TAG, "ExoPlayer: STATE_ENDED — Поток завершился, перезапуск...")
                    scheduleReconnect("Поток завершен сервером")
                }
                Player.STATE_IDLE -> {
                    Log.d(TAG, "ExoPlayer: STATE_IDLE")
                }
            }
        }

        override fun onPlayerError(error: PlaybackException) {
            val errorMsg = error.message ?: "Неизвестная ошибка воспроизведения"
            Log.e(TAG, "Ошибка ExoPlayer [код: ${error.errorCode}]: $errorMsg", error)

            _state.value = PlayerState.Error(errorMsg, DEFAULT_RECONNECT_INTERVAL_SEC)
            scheduleReconnect(errorMsg)
        }
    }
}
