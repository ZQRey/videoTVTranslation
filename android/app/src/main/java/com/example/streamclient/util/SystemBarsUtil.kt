package com.example.streamclient.util

import android.app.Activity
import android.view.WindowManager
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

/**
 * Утилиты полноэкранного режима (Immersive Mode) и предотвращения сна экрана на Android TV.
 */
object SystemBarsUtil {

    /**
     * Перевод экрана в режим Immersive Sticky:
     * Скрывает системные панели статуса и навигации, свайп возвращает их полупрозрачными временно.
     */
    fun hideSystemBars(activity: Activity) {
        val window = activity.window ?: return
        WindowCompat.setDecorFitsSystemWindows(window, false)

        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    /**
     * Блокировка отключения и затемнения экрана при длительном воспроизведении видео.
     */
    fun keepScreenOn(activity: Activity) {
        activity.window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }
}
