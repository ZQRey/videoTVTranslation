package com.example.streamclient.ui

import android.app.Dialog
import android.content.DialogInterface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.DialogFragment
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
        loadCurrentSettings()
    }

    override fun onStart() {
        super.onStart()
        dialog?.window?.setLayout(
            resources.getDimensionPixelSize(R.dimen.dialog_width_fallback),
            ViewGroup.LayoutParams.WRAP_CONTENT
        )
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

            updatePreview()

            // Первоначальный фокус на поле ввода или кнопке сохранения
            if (config.isConfigured) {
                binding.btnSaveConnect.requestFocus()
            } else {
                binding.etServerHost.requestFocus()
            }
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
            if (checkedId == R.id.rbSchedInterval) {
                binding.layoutScheduleInterval.visibility = View.VISIBLE
            } else {
                binding.layoutScheduleInterval.visibility = View.GONE
            }
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
