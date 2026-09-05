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
private const val SERVER_CONNECT_TIMEOUT_MS = 18_000L // 18 секунд (15–20 сек) контрольный таймаут ответа сервера

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
    private var hasCheckedInitialConfig = false
    private var backPressedTime = 0L

    val isCurrentConfigConfigured: Boolean
        get() = currentConfig.isConfigured

    private var statusPollingJob: Job? = null
    private var serverConnectTimeoutJob: Job? = null
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
        // При возвращении на экран, если настройки так и не были заданы и окно закрыто — напоминаем
        if (hasCheckedInitialConfig && !currentConfig.isConfigured && !isSettingsOpen) {
            binding.unconfiguredOverlay.visibility = View.VISIBLE
            binding.btnInitialConfigure.requestFocus()
            openSettingsDialog()
        }
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
            hasCheckedInitialConfig = true

            if (!config.isConfigured) {
                Log.i(TAG, "Первый запуск: адрес сервера не задан. Открытие диалога настроек.")
                binding.unconfiguredOverlay.visibility = View.VISIBLE
                binding.btnInitialConfigure.requestFocus()
                openSettingsDialog()
            } else {
                binding.unconfiguredOverlay.visibility = View.GONE
                binding.tvStreamUrl.text = config.toDisplayString()
                playerController.start(config)
                startStatusPolling(config.serverHost)
                startConnectionTimeoutWatchdog()
            }
        }
    }

    private fun setupUI() {
        // Кнопка вызова настроек из верхнего HUD
        binding.btnOpenSettings.setOnClickListener {
            openSettingsDialog()
        }

        // Кнопка первичной настройки на оверлее приветствия
        binding.btnInitialConfigure.setOnClickListener {
            openSettingsDialog()
        }

        // Кнопка повторной попытки из оверлея ошибки
        binding.btnRetryNow.setOnClickListener {
            playerController.retryNow()
            startConnectionTimeoutWatchdog()
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
                cancelConnectionTimeoutWatchdog()
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
        if (isSettingsOpen || isFinishing || isDestroyed) return
        val existing = supportFragmentManager.findFragmentByTag(SettingsDialogFragment.TAG)
        if (existing != null && existing.isAdded) {
            isSettingsOpen = true
            return
        }
        isSettingsOpen = true
        cancelConnectionTimeoutWatchdog()

        playerController.pause()

        val dialog = SettingsDialogFragment.newInstance()
        // Если адрес сервера еще не был сохранен — блокируем случайное закрытие в пустоту
        dialog.isCancelable = currentConfig.isConfigured
        dialog.onConfigSavedListener = { newConfig ->
            currentConfig = newConfig
            binding.unconfiguredOverlay.visibility = View.GONE
            binding.tvStreamUrl.text = newConfig.toDisplayString()
            playerController.start(newConfig)
            startStatusPolling(newConfig.serverHost)
            startConnectionTimeoutWatchdog()
        }
        dialog.onDismissCallback = {
            isSettingsOpen = false
            SystemBarsUtil.hideSystemBars(this)

            if (currentConfig.isConfigured) {
                binding.unconfiguredOverlay.visibility = View.GONE
                playerController.resume()
                startConnectionTimeoutWatchdog()
            } else {
                binding.unconfiguredOverlay.visibility = View.VISIBLE
                binding.btnInitialConfigure.requestFocus()
            }
        }

        try {
            dialog.showSafely(supportFragmentManager, SettingsDialogFragment.TAG)
        } catch (e: Exception) {
            Log.e(TAG, "Не удалось отобразить диалог настроек", e)
            isSettingsOpen = false
        }
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
                    remove()
                    onBackPressedDispatcher.onBackPressed()
                    return
                }

                if (!currentConfig.isConfigured) {
                    // При незаданных настройках: двойное нажатие выходит из приложения, одиночное напоминает
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
     * Перехват дополнительных кнопок пульта ДУ (Menu, Settings, D-Pad Center, Enter).
     */
    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        when (keyCode) {
            KeyEvent.KEYCODE_MENU,
            KeyEvent.KEYCODE_SETTINGS -> {
                Log.d(TAG, "Нажата кнопка меню/настроек пульта ДУ")
                openSettingsDialog()
                return true
            }
            KeyEvent.KEYCODE_DPAD_CENTER,
            KeyEvent.KEYCODE_ENTER,
            KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                if (!currentConfig.isConfigured && !isSettingsOpen) {
                    openSettingsDialog()
                    return true
                }
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
        cancelConnectionTimeoutWatchdog()
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
            val token = appPreferences.getClientToken()
            val osInfo = "Android ${android.os.Build.VERSION.RELEASE} (API ${android.os.Build.VERSION.SDK_INT}, ${android.os.Build.MODEL})"
            val hostname = android.os.Build.DEVICE ?: "AndroidTV"

            while (isActive) {
                try {
                    val config = currentConfig
                    val schedMode = config.scheduleMode
                    val schedStart = config.scheduleStart
                    val schedEnd = config.scheduleEnd
                    val schedDays = config.scheduleDays.joinToString(",")

                    val statusUrl = "http://$cleanHost:8000/api/client/status" +
                        "?client_id=$token&token=$token" +
                        "&hostname=" + java.net.URLEncoder.encode(hostname, "UTF-8") +
                        "&os_info=" + java.net.URLEncoder.encode(osInfo, "UTF-8") +
                        "&schedule_mode=" + java.net.URLEncoder.encode(schedMode, "UTF-8") +
                        "&schedule_start=" + java.net.URLEncoder.encode(schedStart, "UTF-8") +
                        "&schedule_end=" + java.net.URLEncoder.encode(schedEnd, "UTF-8") +
                        "&schedule_days=" + java.net.URLEncoder.encode(schedDays, "UTF-8")

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

                        val isLocalInWindow = isNowInLocalSchedule(config)
                        val shouldBeStandby = isStandby || !streamAllowed || !isLocalInWindow

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
     * Локальная проверка активности клиента по расписанию.
     */
    private fun isNowInLocalSchedule(config: StreamConfig): Boolean {
        if (config.scheduleMode == "24/7") return true
        if (config.scheduleMode == "global") return true
        // Режим interval
        val cal = java.util.Calendar.getInstance()
        val calDay = cal.get(java.util.Calendar.DAY_OF_WEEK)
        val isoDay = if (calDay == java.util.Calendar.SUNDAY) 7 else calDay - 1
        if (!config.scheduleDays.contains(isoDay)) return false

        val curMin = cal.get(java.util.Calendar.HOUR_OF_DAY) * 60 + cal.get(java.util.Calendar.MINUTE)
        val sParts = config.scheduleStart.split(":").mapNotNull { it.toIntOrNull() }
        val eParts = config.scheduleEnd.split(":").mapNotNull { it.toIntOrNull() }
        val sMin = if (sParts.size >= 2) sParts[0] * 60 + sParts[1] else 0
        val eMin = if (eParts.size >= 2) eParts[0] * 60 + eParts[1] else 24 * 60

        return if (sMin <= eMin) {
            curMin in sMin..eMin
        } else {
            curMin >= sMin || curMin <= eMin
        }
    }

    /**
     * Применение состояния Standby (черный экран и Mute) или возврат в штатный эфир.
     */
    private fun applyStandbyState(standby: Boolean, audioEnabled: Boolean) {
        if (isStandbyActive != standby) {
            isStandbyActive = standby
            if (standby) {
                cancelConnectionTimeoutWatchdog()
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

    /**
     * Контрольный сторожевой таймер подключения к серверу при запуске (18 секунд).
     * Если за 15-20 секунд сервер не ответил (поток не перешел в воспроизведение),
     * автоматически открывается диалоговое окно настроек для корректировки адреса.
     */
    private fun startConnectionTimeoutWatchdog() {
        cancelConnectionTimeoutWatchdog()
        serverConnectTimeoutJob = lifecycleScope.launch {
            Log.i(TAG, "Запущен сторожевой таймер подключения к серверу ($SERVER_CONNECT_TIMEOUT_MS мс)")
            delay(SERVER_CONNECT_TIMEOUT_MS)

            // Если трансляция уже успешно играет (STATE_READY / Playing) — ничего не делаем
            val currentState = playerController.state.value
            if (currentState is PlayerState.Playing) {
                Log.d(TAG, "Сервер успешно отвечает, воспроизведение активно.")
                return@launch
            }

            // Если включен режим Standby (по расписанию экран штатно выключен) — не тревожим пользователя
            if (isStandbyActive) {
                Log.d(TAG, "Клиент находится в режиме Standby по расписанию.")
                return@launch
            }

            // Если окно настроек уже открыто или Activity завершается — пропускаем
            if (isSettingsOpen || isFinishing || isDestroyed) {
                return@launch
            }

            Log.w(TAG, "Сервер не ответил в течение 18 секунд. Автоматическое открытие окна настроек.")
            Toast.makeText(
                this@MainActivity,
                R.string.error_server_timeout,
                Toast.LENGTH_LONG
            ).show()

            openSettingsDialog()
        }
    }

    private fun cancelConnectionTimeoutWatchdog() {
        serverConnectTimeoutJob?.cancel()
        serverConnectTimeoutJob = null
    }
}
