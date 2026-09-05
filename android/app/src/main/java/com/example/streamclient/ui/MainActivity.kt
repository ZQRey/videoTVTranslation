package com.example.streamclient.ui

import android.content.res.ColorStateList
import android.os.Bundle
import android.util.Log
import android.view.KeyEvent
import android.view.View
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.example.streamclient.R
import com.example.streamclient.data.AppPreferences
import com.example.streamclient.data.StreamConfig
import com.example.streamclient.databinding.ActivityMainBinding
import com.example.streamclient.player.PlayerController
import com.example.streamclient.player.PlayerState
import com.example.streamclient.util.SystemBarsUtil
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val TAG = "MainActivity"

/**
 * Главный полноэкранный экран клиентского приложения Android TV / Mobile.
 * Обеспечивает:
 * - Полноэкранный Immersive режим и блокировку засыпания экрана.
 * - Перехват кнопок пульта ДУ (D-Pad, Назад, Меню).
 * - Наблюдение за состояниями ExoPlayer и автоматический Reconnect Loop.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var appPreferences: AppPreferences
    private lateinit var playerController: PlayerController

    private var currentConfig: StreamConfig = StreamConfig()
    private var isSettingsOpen = false
    private var backPressedTime = 0L

    private var statusPollingJob: Job? = null
    private var isStandbyActive = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Инициализация ViewBinding
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Полноэкранный режим и энергосбережение
        SystemBarsUtil.hideSystemBars(this)
        SystemBarsUtil.keepScreenOn(this)

        appPreferences = AppPreferences(this)
        playerController = PlayerController(this, lifecycleScope)
        playerController.attachPlayerView(binding.playerView)

        setupUI()
        setupBackPressHandler()
        observePlayerState()
        checkInitialConfig()
    }

    override fun onResume() {
        super.onResume()
        SystemBarsUtil.hideSystemBars(this)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            SystemBarsUtil.hideSystemBars(this)
        }
    }

    /**
     * Первичная проверка наличия сохраненных настроек стрима.
     * Если адрес пуст — сразу открываем окно настроек.
     */
    private fun checkInitialConfig() {
        lifecycleScope.launch {
            val config = appPreferences.getStreamConfig()
            currentConfig = config

            if (!config.isConfigured) {
                Log.i(TAG, "Первый запуск: адрес сервера не задан. Открытие диалога настроек.")
                openSettingsDialog()
            } else {
                binding.tvStreamUrl.text = config.toDisplayString()
                playerController.start(config)
                startStatusPolling(config.serverHost)
            }
        }
    }

    private fun setupUI() {
        // Кнопка вызова настроек из верхнего HUD
        binding.btnOpenSettings.setOnClickListener {
            openSettingsDialog()
        }

        // Кнопка повторной попытки из оверлея ошибки
        binding.btnRetryNow.setOnClickListener {
            playerController.retryNow()
        }

        // Кнопка настроек из оверлея ошибки
        binding.btnErrorSettings.setOnClickListener {
            openSettingsDialog()
        }
    }

    /**
     * Наблюдение за состояниями плеера (StateFlow) и обновление интерфейса.
     */
    private fun observePlayerState() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                playerController.state.collect { state ->
                    renderPlayerState(state)
                }
            }
        }
    }

    /**
     * Отрисовка текущего состояния воспроизведения.
     */
    private fun renderPlayerState(state: PlayerState) {
        when (state) {
            is PlayerState.Idle -> {
                binding.progressBuffering.visibility = View.GONE
                binding.errorOverlay.visibility = View.GONE
                updateStatusBadge(getString(R.string.status_idle), R.color.text_muted)
            }

            is PlayerState.Connecting -> {
                binding.progressBuffering.visibility = View.VISIBLE
                binding.errorOverlay.visibility = View.GONE
                binding.tvStreamUrl.text = state.url
                updateStatusBadge(getString(R.string.status_connecting), R.color.status_warning)
            }

            is PlayerState.Playing -> {
                binding.progressBuffering.visibility = View.GONE
                binding.errorOverlay.visibility = View.GONE
                binding.tvStreamUrl.text = state.url
                updateStatusBadge(getString(R.string.status_playing), R.color.status_live)
            }

            is PlayerState.Error -> {
                binding.progressBuffering.visibility = View.GONE
                binding.errorOverlay.visibility = View.VISIBLE
                binding.tvErrorMessage.text = state.message
                updateStatusBadge(getString(R.string.status_error_title), R.color.status_error)

                // Фокус на кнопку «Повторить сейчас» для пульта ДУ
                binding.btnRetryNow.requestFocus()
            }

            is PlayerState.Reconnecting -> {
                binding.progressBuffering.visibility = View.GONE
                binding.errorOverlay.visibility = View.VISIBLE
                binding.tvErrorMessage.text = getString(
                    R.string.status_reconnecting_format,
                    state.secondsRemaining
                )
                updateStatusBadge(getString(R.string.status_error_title), R.color.status_warning)
            }
        }
    }

    private fun updateStatusBadge(text: String, colorRes: Int) {
        binding.tvStatusBadge.text = text
        val color = ContextCompat.getColor(this, colorRes)
        binding.statusDot.backgroundTintList = ColorStateList.valueOf(color)
    }

    /**
     * Открытие модального окна настроек.
     * При открытии поток ставится на паузу.
     */
    private fun openSettingsDialog() {
        if (isSettingsOpen) return
        isSettingsOpen = true

        playerController.pause()

        val dialog = SettingsDialogFragment.newInstance()
        dialog.onConfigSavedListener = { newConfig ->
            currentConfig = newConfig
            binding.tvStreamUrl.text = newConfig.toDisplayString()
            playerController.start(newConfig)
            startStatusPolling(newConfig.serverHost)
        }
        dialog.onDismissCallback = {
            isSettingsOpen = false
            SystemBarsUtil.hideSystemBars(this)

            // Если адрес задан, возобновляем воспроизведение
            if (currentConfig.isConfigured) {
                playerController.resume()
            }
        }

        dialog.show(supportFragmentManager, SettingsDialogFragment.TAG)
    }

    /**
     * Перехват аппаратной кнопки «Назад» (пульты ДУ и системная навигация):
     * - Если воспроизводится видео: кнопка «Назад» открывает настройки и НЕ закрывает приложение.
     * - Если окно настроек открыто: оно закрывается штатно.
     * - Двойное нажатие «Назад» вне воспроизведения закрывает приложение.
     */
    private fun setupBackPressHandler() {
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (isSettingsOpen) {
                    // Окно настроек само перехватит отмену
                    remove()
                    onBackPressedDispatcher.onBackPressed()
                    return
                }

                val currentState = playerController.state.value
                if (currentState is PlayerState.Playing || currentState is PlayerState.Connecting) {
                    Log.i(TAG, "Кнопка «Назад»: приостановка воспроизведения и открытие настроек")
                    openSettingsDialog()
                } else {
                    // Подтверждение выхода двойным нажатием
                    if (System.currentTimeMillis() - backPressedTime < 2000) {
                        finish()
                    } else {
                        backPressedTime = System.currentTimeMillis()
                        Toast.makeText(
                            this@MainActivity,
                            "Нажмите [Назад] еще раз для выхода",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                }
            }
        })
    }

    /**
     * Перехват дополнительных кнопок пульта ДУ (Menu, Settings, D-Pad Center).
     */
    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        when (keyCode) {
            KeyEvent.KEYCODE_MENU,
            KeyEvent.KEYCODE_SETTINGS -> {
                Log.d(TAG, "Нажата кнопка меню/настроек пульта ДУ")
                openSettingsDialog()
                return true
            }
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onStart() {
        super.onStart()
        if (currentConfig.isConfigured && !isSettingsOpen) {
            playerController.resume()
        }
    }

    override fun onStop() {
        super.onStop()
        playerController.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        statusPollingJob?.cancel()
        statusPollingJob = null
        playerController.release()
    }

    /**
     * Фоновый периодический опрос серверного расписания и статуса клиента (/api/client/status).
     * Позволяет автоматически гасить экран в нерабочие часы (Standby) и возобновлять эфир.
     */
    private fun startStatusPolling(serverHost: String) {
        statusPollingJob?.cancel()
        val cleanHost = serverHost.trim()
            .removePrefix("http://")
            .removePrefix("https://")
            .removePrefix("rtsp://")
            .split(":")[0]
            .trimEnd('/')

        if (cleanHost.isEmpty()) return

        statusPollingJob = lifecycleScope.launch(Dispatchers.IO) {
            val statusUrl = "http://$cleanHost:8000/api/client/status"
            while (isActive) {
                try {
                    val url = java.net.URL(statusUrl)
                    val connection = url.openConnection() as java.net.HttpURLConnection
                    connection.connectTimeout = 3000
                    connection.readTimeout = 3000
                    connection.requestMethod = "GET"
                    if (connection.responseCode == 200) {
                        val responseText = connection.inputStream.bufferedReader().use { it.readText() }
                        val json = org.json.JSONObject(responseText)
                        val isStandby = json.optBoolean("standby", false)
                        val streamAllowed = json.optBoolean("stream_allowed", true)
                        val audioEnabled = json.optBoolean("audio_enabled", true)

                        val shouldBeStandby = isStandby || !streamAllowed

                        withContext(Dispatchers.Main) {
                            applyStandbyState(shouldBeStandby, audioEnabled)
                        }
                    }
                } catch (e: Exception) {
                    // Игнорируем временные сетевые таймауты при опросе
                }
                delay(4000)
            }
        }
    }

    /**
     * Применение состояния Standby (черный экран и Mute) или возврат в штатный эфир.
     */
    private fun applyStandbyState(standby: Boolean, audioEnabled: Boolean) {
        if (isStandbyActive != standby) {
            isStandbyActive = standby
            if (standby) {
                Log.i(TAG, "Переход в спящий режим Standby (по расписанию или команде сервера)")
                binding.standbyOverlay.visibility = View.VISIBLE
                playerController.setStandby(true)
            } else {
                Log.i(TAG, "Выход из спящего режима Standby — возобновление эфира")
                binding.standbyOverlay.visibility = View.GONE
                playerController.setStandby(false)
            }
        }
        if (!standby) {
            playerController.setAudioEnabled(audioEnabled)
        }
    }
}
