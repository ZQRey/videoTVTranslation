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

        // Перехват кнопок на уровне окна диалога:
        // 1. Двойное нажатие [Назад] для выхода из приложения, если настройки еще не сохранены.
        // 2. Страховочный фокус: если при нажатии кнопки пульта фокус потерян вообще во всем окне.
        dialog?.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_UP && keyCode == KeyEvent.KEYCODE_BACK) {
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
            } else if (event.action == KeyEvent.ACTION_DOWN) {
                // Если ни один элемент в диалоге сейчас не имеет фокуса — даем фокус первому полю
                val root = _binding?.root
                if (root != null && root.findFocus() == null) {
                    _binding?.let { b -> focusAndScrollTo(b.etServerHost) }
                    return@setOnKeyListener true
                }
            }
            false
        }

        // Первоначальный фокус на первом поле ввода
        _binding?.root?.post {
            _binding?.let { b ->
                focusAndScrollTo(b.etServerHost)
            }
        }
    }

    private fun focusAndScrollTo(target: View) {
        target.isFocusable = true
        target.isFocusableInTouchMode = true
        target.requestFocus()
        val scroll = _binding?.scrollView ?: return
        scroll.post {
            val rect = android.graphics.Rect()
            target.getDrawingRect(rect)
            scroll.offsetDescendantRectToMyCoords(target, rect)
            rect.top = (rect.top - 32).coerceAtLeast(0)
            rect.bottom += 32
            scroll.requestChildRectangleOnScreen(target, rect, false)
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
     * Обеспечивает прямое перемещение фокуса между элементами и скролл без взаимных блокировок.
     */
    private fun setupDpadNavigation() {
        val b = _binding ?: return

        // 1. Поле ввода хоста сервера
        setupFocusAndScroll(b.etServerHost)
        b.etServerHost.setOnKeyListener { v, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.rbRtsp)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        val imm = v.context.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
                        imm?.showSoftInput(v, InputMethodManager.SHOW_IMPLICIT)
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 2. Радиокнопка RTSP
        setupFocusAndScroll(b.rbRtsp)
        b.rbRtsp.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.etServerHost)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.rbHls)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbRtsp.isChecked = true
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 3. Радиокнопка HLS
        setupFocusAndScroll(b.rbHls)
        b.rbHls.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.rbRtsp)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.etPort)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbHls.isChecked = true
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 4. Поле порта
        setupFocusAndScroll(b.etPort)
        b.etPort.setOnKeyListener { v, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.rbHls)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.rbSchedGlobal)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        val et = v as? EditText
                        if (et == null || et.selectionEnd >= (et.text?.length ?: 0)) {
                            focusAndScrollTo(b.etStreamPath)
                            return@setOnKeyListener true
                        }
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        val imm = v.context.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
                        imm?.showSoftInput(v, InputMethodManager.SHOW_IMPLICIT)
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 5. Поле пути потока
        setupFocusAndScroll(b.etStreamPath)
        b.etStreamPath.setOnKeyListener { v, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.rbHls)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.rbSchedGlobal)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_LEFT -> {
                        val et = v as? EditText
                        if (et == null || et.selectionStart <= 0) {
                            focusAndScrollTo(b.etPort)
                            return@setOnKeyListener true
                        }
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        val imm = v.context.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
                        imm?.showSoftInput(v, InputMethodManager.SHOW_IMPLICIT)
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 6. Режим расписания: Серверное (по умолчанию)
        setupFocusAndScroll(b.rbSchedGlobal)
        b.rbSchedGlobal.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.etPort)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.rbSched247)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbSchedGlobal.isChecked = true
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 7. Режим расписания: 24/7
        setupFocusAndScroll(b.rbSched247)
        b.rbSched247.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.rbSchedGlobal)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.rbSchedInterval)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbSched247.isChecked = true
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 8. Режим расписания: Интервал
        setupFocusAndScroll(b.rbSchedInterval)
        b.rbSchedInterval.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.rbSched247)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        val isInterval = b.rgScheduleMode.checkedRadioButtonId == R.id.rbSchedInterval
                        if (isInterval) {
                            focusAndScrollTo(b.etScheduleStart)
                        } else {
                            focusAndScrollTo(b.btnCancel)
                        }
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.rbSchedInterval.isChecked = true
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 9. Время начала интервала
        setupFocusAndScroll(b.etScheduleStart)
        b.etScheduleStart.setOnKeyListener { v, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.rbSchedInterval)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.btnCancel)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        val et = v as? EditText
                        if (et == null || et.selectionEnd >= (et.text?.length ?: 0)) {
                            focusAndScrollTo(b.etScheduleEnd)
                            return@setOnKeyListener true
                        }
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        val imm = v.context.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
                        imm?.showSoftInput(v, InputMethodManager.SHOW_IMPLICIT)
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 10. Время окончания интервала
        setupFocusAndScroll(b.etScheduleEnd)
        b.etScheduleEnd.setOnKeyListener { v, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        focusAndScrollTo(b.rbSchedInterval)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        focusAndScrollTo(b.btnSaveConnect)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_LEFT -> {
                        val et = v as? EditText
                        if (et == null || et.selectionStart <= 0) {
                            focusAndScrollTo(b.etScheduleStart)
                            return@setOnKeyListener true
                        }
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        val imm = v.context.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
                        imm?.showSoftInput(v, InputMethodManager.SHOW_IMPLICIT)
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 11. Кнопка Отмена
        setupFocusAndScroll(b.btnCancel)
        b.btnCancel.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        val isInterval = b.rgScheduleMode.checkedRadioButtonId == R.id.rbSchedInterval
                        if (isInterval) {
                            focusAndScrollTo(b.etScheduleStart)
                        } else {
                            focusAndScrollTo(b.rbSchedInterval)
                        }
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        focusAndScrollTo(b.btnSaveConnect)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.btnCancel.performClick()
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }

        // 12. Кнопка Сохранить и подключиться
        setupFocusAndScroll(b.btnSaveConnect)
        b.btnSaveConnect.setOnKeyListener { _, keyCode, event ->
            if (event.action == KeyEvent.ACTION_DOWN) {
                when (keyCode) {
                    KeyEvent.KEYCODE_DPAD_UP -> {
                        val isInterval = b.rgScheduleMode.checkedRadioButtonId == R.id.rbSchedInterval
                        if (isInterval) {
                            focusAndScrollTo(b.etScheduleEnd)
                        } else {
                            focusAndScrollTo(b.rbSchedInterval)
                        }
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_LEFT -> {
                        focusAndScrollTo(b.btnCancel)
                        return@setOnKeyListener true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                        b.btnSaveConnect.performClick()
                        return@setOnKeyListener true
                    }
                }
            }
            false
        }
    }

    private fun setupFocusAndScroll(view: View) {
        view.isFocusable = true
        view.isFocusableInTouchMode = true
        view.setOnFocusChangeListener { v, hasFocus ->
            if (hasFocus) {
                val scroll = _binding?.scrollView ?: return@setOnFocusChangeListener
                scroll.post {
                    val rect = android.graphics.Rect()
                    v.getDrawingRect(rect)
                    scroll.offsetDescendantRectToMyCoords(v, rect)
                    rect.top = (rect.top - 32).coerceAtLeast(0)
                    rect.bottom += 32
                    scroll.requestChildRectangleOnScreen(v, rect, false)
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
