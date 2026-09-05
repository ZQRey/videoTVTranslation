package com.example.streamclient.ui

import android.content.Context
import android.content.DialogInterface
import android.os.Bundle
import android.view.KeyEvent
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.Toast
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.DialogFragment
import androidx.fragment.app.FragmentManager
import androidx.lifecycle.lifecycleScope
import com.example.streamclient.R
import com.example.streamclient.data.AppPreferences
import com.example.streamclient.data.StreamConfig
import com.example.streamclient.data.StreamType
import com.example.streamclient.databinding.DialogSettingsBinding
import kotlinx.coroutines.launch

/**
 * Диалоговое окно настройки параметров подключения к серверу вещания.
 * Полностью оптимизировано для управления стрелками пульта ДУ (D-Pad).
 */
class SettingsDialogFragment : DialogFragment() {

    private var _binding: DialogSettingsBinding? = null
    private val binding get() = _binding!!

    private lateinit var appPreferences: AppPreferences

    var onConfigSavedListener: ((StreamConfig) -> Unit)? = null
    var onDismissCallback: (() -> Unit)? = null

    companion object {
        const val TAG = "SettingsDialogFragment"

        fun newInstance(): SettingsDialogFragment {
            return SettingsDialogFragment()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setStyle(STYLE_NORMAL, R.style.Theme_StreamClient_Dialog)
        appPreferences = AppPreferences(requireContext())
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = DialogSettingsBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        setupListeners()
        setupDpadNavigation()
        loadCurrentSettings()
    }

    private var lastBackPressTime = 0L

    override fun onStart() {
        super.onStart()
        dialog?.window?.apply {
            setLayout(
                resources.getDimensionPixelSize(R.dimen.dialog_width_fallback),
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
            // Гарантируем, что окно диалога перехватывает фокус для пульта ДУ
            clearFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
        }

        // Перехват кнопки Назад для подтверждения выхода при незаданных настройках
        dialog?.setOnKeyListener { _, keyCode, event ->
            if (keyCode == KeyEvent.KEYCODE_BACK && event.action == KeyEvent.ACTION_UP) {
                val isConfigured = (activity as? MainActivity)?.isCurrentConfigConfigured == true
                if (!isConfigured) {
                    val now = System.currentTimeMillis()
                    if (now - lastBackPressTime < 2000) {
                        activity?.finish()
                    } else {
                        lastBackPressTime = now
                        Toast.makeText(
                            requireContext(),
                            "Нажмите [Назад] еще раз для выхода из приложения",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                    return@setOnKeyListener true
                }
            }
            false
        }

        // Запрос начального фокуса после отрисовки окна
        binding.root.post {
            val host = binding.etServerHost.text?.toString()?.trim().orEmpty()
            if (host.isEmpty()) {
                binding.etServerHost.requestFocus()
            } else {
                binding.btnSaveConnect.requestFocus()
            }
        }
    }

    /**
     * Безопасный показ диалога, защищенный от исключений жизненного цикла FragmentManager.
     */
    fun showSafely(fragmentManager: FragmentManager, tag: String = TAG) {
        if (fragmentManager.isDestroyed) {
            return
        }
        val existing = fragmentManager.findFragmentByTag(tag)
        if (existing != null && existing.isAdded) {
            return
        }
        try {
            val ft = fragmentManager.beginTransaction()
            if (existing != null) {
                ft.remove(existing)
            }
            ft.add(this, tag)
            ft.commitAllowingStateLoss()
        } catch (e: Exception) {
            android.util.Log.e(TAG, "Ошибка безопасного показа диалога настроек", e)
        }
    }

    private fun loadCurrentSettings() {
        viewLifecycleOwner.lifecycleScope.launch {
            val config = appPreferences.getStreamConfig()
            binding.etServerHost.setText(config.serverHost)

            when (config.streamType) {
                StreamType.RTSP -> {
                    binding.rbRtsp.isChecked = true
                    binding.tvPortLabel.text = getString(R.string.rtsp_port_label)
                    binding.etPort.setText(config.rtspPort.toString())
                }
                StreamType.HLS -> {
                    binding.rbHls.isChecked = true
                    binding.tvPortLabel.text = getString(R.string.hls_port_label)
                    binding.etPort.setText(config.hlsPort.toString())
                }
            }

            binding.etStreamPath.setText(config.streamPath)

            val isInterval = config.scheduleMode == "interval"
            when (config.scheduleMode) {
                "24/7" -> {
                    binding.rbSched247.isChecked = true
                    binding.layoutScheduleInterval.visibility = View.GONE
                }
                "interval" -> {
                    binding.rbSchedInterval.isChecked = true
                    binding.layoutScheduleInterval.visibility = View.VISIBLE
                }
                else -> {
                    binding.rbSchedGlobal.isChecked = true
                    binding.layoutScheduleInterval.visibility = View.GONE
                }
            }
            binding.etScheduleStart.setText(config.scheduleStart)
            binding.etScheduleEnd.setText(config.scheduleEnd)
            updateDynamicFocusPaths(isInterval)

            updatePreview()
        }
    }

    private fun setupListeners() {
        // Переключение протоколов RTSP / HLS
        binding.rgProtocol.setOnCheckedChangeListener { _, checkedId ->
            if (checkedId == R.id.rbRtsp) {
                binding.tvPortLabel.text = getString(R.string.rtsp_port_label)
                if (binding.etPort.text.toString() == "8888") {
                    binding.etPort.setText("8554")
                }
            } else if (checkedId == R.id.rbHls) {
                binding.tvPortLabel.text = getString(R.string.hls_port_label)
                if (binding.etPort.text.toString() == "8554") {
                    binding.etPort.setText("8888")
                }
            }
            updatePreview()
        }

        // Переключение режима расписания
        binding.rgScheduleMode.setOnCheckedChangeListener { _, checkedId ->
            val isInterval = checkedId == R.id.rbSchedInterval
            binding.layoutScheduleInterval.visibility = if (isInterval) View.VISIBLE else View.GONE
            updateDynamicFocusPaths(isInterval)
        }

        // Обновление предпросмотра URL при изменении полей
        binding.etServerHost.doAfterTextChanged { updatePreview() }
        binding.etPort.doAfterTextChanged { updatePreview() }
        binding.etStreamPath.doAfterTextChanged { updatePreview() }

        // Кнопка сохранения и подключения
        binding.btnSaveConnect.setOnClickListener {
            saveAndConnect()
        }

        // Кнопка отмены
        binding.btnCancel.setOnClickListener {
            dismiss()
        }
    }

    /**
     * Динамическое переключение путей навигации пульта ДУ в зависимости от видимости полей интервала.
     */
    private fun updateDynamicFocusPaths(isIntervalVisible: Boolean) {
        if (isIntervalVisible) {
            binding.rbSchedInterval.nextFocusDownId = R.id.etScheduleStart
            binding.btnCancel.nextFocusUpId = R.id.etScheduleStart
            binding.btnSaveConnect.nextFocusUpId = R.id.etScheduleEnd
        } else {
            binding.rbSchedInterval.nextFocusDownId = R.id.btnCancel
            binding.btnCancel.nextFocusUpId = R.id.rbSchedInterval
            binding.btnSaveConnect.nextFocusUpId = R.id.rbSchedInterval
        }
    }

    /**
     * Комплексная настройка навигации стрелками пульта ДУ (D-Pad) для текстовых полей и автоскролла.
     */
    private fun setupDpadNavigation() {
        val editTexts = listOf(
            binding.etServerHost,
            binding.etPort,
            binding.etStreamPath,
            binding.etScheduleStart,
            binding.etScheduleEnd
        )

        for (et in editTexts) {
            et.setOnKeyListener { v, keyCode, event ->
                if (event.action == KeyEvent.ACTION_DOWN) {
                    when (keyCode) {
                        KeyEvent.KEYCODE_DPAD_DOWN -> {
                            val nextId = v.nextFocusDownId
                            if (nextId != View.NO_ID) {
                                binding.root.findViewById<View>(nextId)?.requestFocus()
                                return@setOnKeyListener true
                            }
                        }
                        KeyEvent.KEYCODE_DPAD_UP -> {
                            val prevId = v.nextFocusUpId
                            if (prevId != View.NO_ID) {
                                binding.root.findViewById<View>(prevId)?.requestFocus()
                                return@setOnKeyListener true
                            }
                        }
                        KeyEvent.KEYCODE_DPAD_RIGHT -> {
                            val etView = v as? EditText
                            if (etView == null || etView.selectionEnd >= (etView.text?.length ?: 0)) {
                                val rightId = v.nextFocusRightId
                                if (rightId != View.NO_ID) {
                                    binding.root.findViewById<View>(rightId)?.requestFocus()
                                    return@setOnKeyListener true
                                }
                            }
                        }
                        KeyEvent.KEYCODE_DPAD_LEFT -> {
                            val etView = v as? EditText
                            if (etView == null || etView.selectionStart <= 0) {
                                val leftId = v.nextFocusLeftId
                                if (leftId != View.NO_ID) {
                                    binding.root.findViewById<View>(leftId)?.requestFocus()
                                    return@setOnKeyListener true
                                }
                            }
                        }
                        KeyEvent.KEYCODE_ENTER,
                        KeyEvent.KEYCODE_DPAD_CENTER -> {
                            // Открытие экранной клавиатуры для ввода
                            val imm = v.context.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
                            imm?.showSoftInput(v, InputMethodManager.SHOW_IMPLICIT)
                        }
                    }
                }
                false
            }

            // Автоматическая прокрутка ScrollView при фокусе элемента
            et.setOnFocusChangeListener { v, hasFocus ->
                if (hasFocus) {
                    binding.scrollView.requestChildFocus(v, v)
                }
            }
        }

        // Поддержка кнопок пульта OK / DPAD_CENTER для мгновенного выбора RadioButton
        val radioButtons = listOf(
            binding.rbRtsp,
            binding.rbHls,
            binding.rbSchedGlobal,
            binding.rbSched247,
            binding.rbSchedInterval
        )
        for (rb in radioButtons) {
            rb.setOnKeyListener { v, keyCode, event ->
                if (event.action == KeyEvent.ACTION_DOWN &&
                    (keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER || keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER)
                ) {
                    (v as? android.widget.RadioButton)?.isChecked = true
                    return@setOnKeyListener true
                }
                false
            }
        }

        // Автоскролл для остальных элементов управления
        val otherFocusables = listOf(
            binding.rbRtsp,
            binding.rbHls,
            binding.rbSchedGlobal,
            binding.rbSched247,
            binding.rbSchedInterval,
            binding.btnCancel,
            binding.btnSaveConnect
        )

        for (item in otherFocusables) {
            item.setOnFocusChangeListener { v, hasFocus ->
                if (hasFocus) {
                    binding.scrollView.requestChildFocus(v, v)
                }
            }
        }
    }

    private fun updatePreview() {
        val host = binding.etServerHost.text?.toString()?.trim().orEmpty()
        val isRtsp = binding.rbRtsp.isChecked
        val port = binding.etPort.text?.toString()?.toIntOrNull() ?: if (isRtsp) 8554 else 8888
        val path = binding.etStreamPath.text?.toString()?.trim()?.removePrefix("/").orEmpty().ifEmpty { "live" }

        val prefix = if (isRtsp) "rtsp://" else "http://"
        val previewUrl = if (host.isEmpty()) {
            "URL: (укажите адрес хоста)"
        } else {
            "URL: $prefix$host:$port/$path"
        }
        binding.tvResultPreview.text = previewUrl
    }

    private fun saveAndConnect() {
        val host = binding.etServerHost.text?.toString()?.trim().orEmpty()
        if (host.isEmpty()) {
            Toast.makeText(requireContext(), R.string.error_empty_host, Toast.LENGTH_SHORT).show()
            binding.etServerHost.requestFocus()
            return
        }

        val isRtsp = binding.rbRtsp.isChecked
        val port = binding.etPort.text?.toString()?.toIntOrNull() ?: if (isRtsp) 8554 else 8888
        val path = binding.etStreamPath.text?.toString()?.trim()?.removePrefix("/").orEmpty().ifEmpty { "live" }

        val schedMode = when (binding.rgScheduleMode.checkedRadioButtonId) {
            R.id.rbSched247 -> "24/7"
            R.id.rbSchedInterval -> "interval"
            else -> "global"
        }
        val schedStart = binding.etScheduleStart.text?.toString()?.trim()?.ifEmpty { "08:00" } ?: "08:00"
        val schedEnd = binding.etScheduleEnd.text?.toString()?.trim()?.ifEmpty { "20:00" } ?: "20:00"

        val newConfig = StreamConfig(
            serverHost = host,
            streamType = if (isRtsp) StreamType.RTSP else StreamType.HLS,
            rtspPort = if (isRtsp) port else 8554,
            hlsPort = if (!isRtsp) port else 8888,
            streamPath = path,
            scheduleMode = schedMode,
            scheduleStart = schedStart,
            scheduleEnd = schedEnd
        )

        viewLifecycleOwner.lifecycleScope.launch {
            appPreferences.saveStreamConfig(newConfig)
            onConfigSavedListener?.invoke(newConfig)
            dismiss()
        }
    }

    override fun onDismiss(dialog: DialogInterface) {
        super.onDismiss(dialog)
        onDismissCallback?.invoke()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
