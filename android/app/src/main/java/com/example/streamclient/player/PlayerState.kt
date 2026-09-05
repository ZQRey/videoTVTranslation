package com.example.streamclient.player

/**
 * Состояния жизненного цикла воспроизведения и подключения плеера.
 */
sealed interface PlayerState {
    /**
     * Плеер не инициализирован или ожидает ввода настроек.
     */
    object Idle : PlayerState

    /**
     * Инициализация источника и буферизация.
     */
    data class Connecting(val url: String) : PlayerState

    /**
     * Активное стабильное воспроизведение живого стрима.
     */
    data class Playing(val url: String) : PlayerState

    /**
     * Ошибка соединения с сервером или сетевой сбой.
     */
    data class Error(
        val message: String,
        val retryInSeconds: Int
    ) : PlayerState

    /**
     * Обратный отсчет до автоматического перезапуска стрима (Reconnect Loop).
     */
    data class Reconnecting(
        val url: String,
        val secondsRemaining: Int
    ) : PlayerState
}
