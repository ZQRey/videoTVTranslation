package com.example.streamclient.ui

import android.app.Dialog
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
        setStyle(STYLE_NO_TITLE, R.style.Theme_StreamClient_Dialog)
        appPreferences = AppPreferences(requireContext())
    }

    override fun onCreateDialog(savedInstanceState: Bundle?): Dialog {
        val dialog = super.onCreateDialog(savedInstanceState)
        dialog.window?.apply {
            clearFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
            clearFlags(WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM)
            setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_HIDDEN or WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        }
        return dialog
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
            clearFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
        }

        // Перехват кнопок пульта ДУ на уровне окна диалога
        dialog?.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                val current = dialog?.currentFocus
                val b = _binding
                if (b != null) {
                    if (current == null) {
                        focusView(b.etServerHost)
                        return@setOnKeyListener true
                    }
                    val handled = handleDpadNavigation(current, keyCode)
                    if (handled) return@setOnKeyListener true
                }
            } else if (event.action == KeyEvent.ACTION_UP && keyCode == KeyEvent.KEYCODE_BACK) {
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

        // Гарантированный начальный фокус на первом поле
        _binding?.root?.post {
            _binding?.let { b ->
                focusView(b.etServerHost)
            }
        }
    }

    private fun focusView(target: View) {
        target.isFocusable = true
        target.isFocusableInTouchMode = true
        target.requestFocus()
        _binding?.scrollView?.post {
            _binding?.scrollView?.requestChildFocus(target, target)
        }
    }

    /**
     * Безопасный показ диалога, защищенный от исключений жизненного цикла FragmentManager.
     */
    fun showSafely(fragmentManager: FragmentManager, tag: String = TAG) {
        if (fragmentManager.isDestroyed || fragmentManager.isStateSaved) {
            return
        }
        val existing = fragmentManager.findFragmentByTag(tag)
        if (existing != null && existing.isAdded) {
            return
        }
        try {
            show(fragmentManager, tag)
        } catch (e: Exception) {
            try {
                val ft = fragmentManager.beginTransaction()
                if (existing != null) {
                    ft.remove(existing)
                }
                ft.add(this, tag)
                ft.commitAllowingStateLoss()
            } catch (ex: Exception) {
                android.util.Log.e(TAG, "Ошибка безопасного показа диалога настроек", ex)
            }
        }
    }

    private fun loadCurrentSettings() {
        viewLifecycleOwner.lifecycleScope.launch {
            val config = appPreferences.getStreamConfig()
            val b = _binding ?: return@launch
            b.etServerHost.setText(config.serverHost)

            when (config.streamType) {
                StreamType.RTSP -> {
                    b.rbRtsp.isChecked = true
                    b.tvPortLabel.text = getString(R.string.rtsp_port_label)
                    b.etPort.setText(config.rtspPort.toString())
                }
                StreamType.HLS -> {
                    b.rbHls.isChecked = true
                    b.tvPortLabel.text = getString(R.string.hls_port_label)
                    b.etPort.setText(config.hlsPort.toString())
                }
            }

            b.etStreamPath.setText(config.streamPath)

            val isInterval = config.scheduleMode == "interval"
            when (config.scheduleMode) {
                "24/7" -> {
                    b.rbSched247.isChecked = true
                    b.layoutScheduleInterval.visibility = View.GONE
                }
                "interval" -> {
                    b.rbSchedInterval.isChecked = true
                    b.layoutScheduleInterval.visibility = View.VISIBLE
                }
                else -> {
                    b.rbSchedGlobal.isChecked = true
                    b.layoutScheduleInterval.visibility = View.GONE
                }
            }
            b.etScheduleStart.setText(config.scheduleStart)
            b.etScheduleEnd.setText(config.scheduleEnd)
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
        val b = _binding ?: return
        if (isIntervalVisible) {
            b.rbSchedInterval.nextFocusDownId = R.id.etScheduleStart
            b.btnCancel.nextFocusUpId = R.id.etScheduleStart
            b.btnSaveConnect.nextFocusUpId = R.id.etScheduleEnd
        } else {
            b.rbSchedInterval.nextFocusDownId = R.id.btnCancel
            b.btnCancel.nextFocusUpId = R.id.rbSchedInterval
            b.btnSaveConnect.nextFocusUpId = R.id.rbSchedInterval
        }
    }

    /**
     * Комплексная настройка навигации стрелками пульта ДУ (D-Pad) для всех элементов управления.
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
            et.isFocusable = true
            et.isFocusableInTouchMode = true
            et.setOnKeyListener { v, keyCode, event ->
                if (event.action == KeyEvent.ACTION_DOWN) {
                    when (keyCode) {
                        KeyEvent.KEYCODE_ENTER,
                        KeyEvent.KEYCODE_DPAD_CENTER,
                        KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                            val imm = v.context.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
                            imm?.showSoftInput(v, InputMethodManager.SHOW_IMPLICIT)
                            return@setOnKeyListener true
                        }
                    }
                    if (handleDpadNavigation(v, keyCode)) {
                        return@setOnKeyListener true
                    }
                }
                false
            }

            et.setOnFocusChangeListener { v, hasFocus ->
                if (hasFocus) {
                    _binding?.scrollView?.post {
                        _binding?.scrollView?.requestChildFocus(v, v)
                    }
                }
            }
        }

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
            item.isFocusable = true
            item.isFocusableInTouchMode = true
            item.setOnFocusChangeListener { v, hasFocus ->
                if (hasFocus) {
                    _binding?.scrollView?.post {
                        _binding?.scrollView?.requestChildFocus(v, v)
                    }
                }
            }
            item.setOnKeyListener { v, keyCode, event ->
                if (event.action == KeyEvent.ACTION_DOWN) {
                    if (handleDpadNavigation(v, keyCode)) {
                        return@setOnKeyListener true
                    }
                }
                false
            }
        }
    }

    /**
     * Прямой детерминированный обработчик перемещения фокуса пульта ДУ (D-Pad).
     * Гарантирует надежный отклик на всех моделях пультов Android TV.
     */
    private fun handleDpadNavigation(focused: View, keyCode: Int): Boolean {
        val b = _binding ?: return false
        val isInterval = b.rgScheduleMode.checkedRadioButtonId == R.id.rbSchedInterval

        when (focused.id) {
            R.id.etServerHost -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.rbRtsp)
                        return true
                    }
                }
            }

            R.id.rbRtsp -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.etServerHost)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.rbHls)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbRtsp.isChecked = true
                        return true
                    }
                }
            }

            R.id.rbHls -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.rbRtsp)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.etPort)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbHls.isChecked = true
                        return true
                    }
                }
            }

            R.id.etPort -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.rbHls)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.rbSchedGlobal)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        val et = focused as? EditText
                        if (et == null || et.selectionEnd >= (et.text?.length ?: 0)) {
                            focusView(b.etStreamPath)
                            return true
                        }
                    }
                }
            }

            R.id.etStreamPath -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.rbHls)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.rbSchedGlobal)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_LEFT -> {
                        val et = focused as? EditText
                        if (et == null || et.selectionStart <= 0) {
                            focusView(b.etPort)
                            return true
                        }
                    }
                }
            }

            R.id.rbSchedGlobal -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.etPort)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.rbSched247)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbSchedGlobal.isChecked = true
                        return true
                    }
                }
            }

            R.id.rbSched247 -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.rbSchedGlobal)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.rbSchedInterval)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbSched247.isChecked = true
                        return true
                    }
                }
            }

            R.id.rbSchedInterval -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.rbSched247)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        if (isInterval) {
                            focusView(b.etScheduleStart)
                        } else {
                            focusView(b.btnCancel)
                        }
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbSchedInterval.isChecked = true
                        return true
                    }
                }
            }

            R.id.etScheduleStart -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.rbSchedInterval)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.btnCancel)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        val et = focused as? EditText
                        if (et == null || et.selectionEnd >= (et.text?.length ?: 0)) {
                            focusView(b.etScheduleEnd)
                            return true
                        }
                    }
                }
            }

            R.id.etScheduleEnd -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusView(b.rbSchedInterval)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusView(b.btnSaveConnect)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_LEFT -> {
                        val et = focused as? EditText
                        if (et == null || et.selectionStart <= 0) {
                            focusView(b.etScheduleStart)
                            return true
                        }
                    }
                }
            }

            R.id.btnCancel -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        if (isInterval) {
                            focusView(b.etScheduleStart)
                        } else {
                            focusView(b.rbSchedInterval)
                        }
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        focusView(b.btnSaveConnect)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        dismiss()
                        return true
                    }
                }
            }

            R.id.btnSaveConnect -> {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        if (isInterval) {
                            focusView(b.etScheduleEnd)
                        } else {
                            focusView(b.rbSchedInterval)
                        }
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_LEFT -> {
                        focusView(b.btnCancel)
                        return true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        saveAndConnect()
                        return true
                    }
                }
            }
        }
        return false
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
