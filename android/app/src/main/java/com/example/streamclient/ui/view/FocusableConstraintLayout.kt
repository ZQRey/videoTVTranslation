package com.example.streamclient.ui.view

import android.content.Context
import android.graphics.Rect
import android.util.AttributeSet
import android.view.animation.DecelerateInterpolator
import androidx.constraintlayout.widget.ConstraintLayout

/**
 * Кастомный ConstraintLayout с плавной анимацией масштабирования и подсветкой
 * при навигации пультом ДУ на Android TV (D-Pad).
 */
class FocusableConstraintLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : ConstraintLayout(context, attrs, defStyleAttr) {

    private val scaleInterpolator = DecelerateInterpolator()

    init {
        isFocusable = true
        isFocusableInTouchMode = false
    }

    override fun onFocusChanged(gainFocus: Boolean, direction: Int, previouslyFocusedRect: Rect?) {
        super.onFocusChanged(gainFocus, direction, previouslyFocusedRect)

        val targetScale = if (gainFocus) 1.03f else 1.0f
        val targetElevation = if (gainFocus) 16f else 0f

        animate()
            .scaleX(targetScale)
            .scaleY(targetScale)
            .translationZ(targetElevation)
            .setDuration(200)
            .setInterpolator(scaleInterpolator)
            .start()
    }
}
