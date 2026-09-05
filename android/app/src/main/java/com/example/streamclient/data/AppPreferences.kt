package com.example.streamclient.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.io.IOException

// Расширение Context для синглтон-доступа к DataStore
private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "stream_client_prefs")

/**
 * Менеджер персистентного хранения настроек приложения на базе AndroidX DataStore Preferences.
 */
class AppPreferences(private val context: Context) {

    companion object {
        private val KEY_SERVER_HOST = stringPreferencesKey("server_host")
        private val KEY_STREAM_TYPE = stringPreferencesKey("stream_type")
        private val KEY_RTSP_PORT = intPreferencesKey("rtsp_port")
        private val KEY_HLS_PORT = intPreferencesKey("hls_port")
        private val KEY_STREAM_PATH = stringPreferencesKey("stream_path")
        private val KEY_CLIENT_TOKEN = stringPreferencesKey("client_token")
        private val KEY_SCHEDULE_MODE = stringPreferencesKey("schedule_mode")
        private val KEY_SCHEDULE_START = stringPreferencesKey("schedule_start")
        private val KEY_SCHEDULE_END = stringPreferencesKey("schedule_end")
        private val KEY_SCHEDULE_DAYS = stringPreferencesKey("schedule_days")
    }

    /**
     * Получение или создание постоянного уникального токена клиента Android TV.
     */
    suspend fun getClientToken(): String {
        val prefs = context.dataStore.data.first()
        val existing = prefs[KEY_CLIENT_TOKEN]
        if (!existing.isNullOrBlank()) {
            return existing
        }
        val newToken = "android-tv-${java.util.UUID.randomUUID()}"
        context.dataStore.edit { it[KEY_CLIENT_TOKEN] = newToken }
        return newToken
    }

    /**
     * Поток изменений конфигурации стрима.
     */
    val streamConfigFlow: Flow<StreamConfig> = context.dataStore.data
        .catch { exception ->
            if (exception is IOException) {
                emit(emptyPreferences())
            } else {
                throw exception
            }
        }
        .map { prefs ->
            val host = prefs[KEY_SERVER_HOST] ?: ""
            val typeStr = prefs[KEY_STREAM_TYPE] ?: StreamType.RTSP.name
            val streamType = runCatching { StreamType.valueOf(typeStr) }.getOrDefault(StreamType.RTSP)
            val rtspPort = prefs[KEY_RTSP_PORT] ?: 8554
            val hlsPort = prefs[KEY_HLS_PORT] ?: 8888
            val path = prefs[KEY_STREAM_PATH] ?: "live"
            val schedMode = prefs[KEY_SCHEDULE_MODE] ?: "global"
            val schedStart = prefs[KEY_SCHEDULE_START] ?: "08:00"
            val schedEnd = prefs[KEY_SCHEDULE_END] ?: "20:00"
            val schedDaysRaw = prefs[KEY_SCHEDULE_DAYS] ?: "1,2,3,4,5,6,7"
            val schedDays = schedDaysRaw.split(",")
                .mapNotNull { it.trim().toIntOrNull() }
                .ifEmpty { listOf(1, 2, 3, 4, 5, 6, 7) }

            StreamConfig(
                serverHost = host,
                streamType = streamType,
                rtspPort = rtspPort,
                hlsPort = hlsPort,
                streamPath = path,
                scheduleMode = schedMode,
                scheduleStart = schedStart,
                scheduleEnd = schedEnd,
                scheduleDays = schedDays
            )
        }

    /**
     * Получение текущего снимка настроек.
     */
    suspend fun getStreamConfig(): StreamConfig {
        return streamConfigFlow.first()
    }

    /**
     * Сохранение новой конфигурации стрима.
     */
    suspend fun saveStreamConfig(config: StreamConfig) {
        context.dataStore.edit { prefs ->
            prefs[KEY_SERVER_HOST] = config.serverHost.trim()
            prefs[KEY_STREAM_TYPE] = config.streamType.name
            prefs[KEY_RTSP_PORT] = config.rtspPort
            prefs[KEY_HLS_PORT] = config.hlsPort
            prefs[KEY_STREAM_PATH] = config.streamPath.trim()
            prefs[KEY_SCHEDULE_MODE] = config.scheduleMode
            prefs[KEY_SCHEDULE_START] = config.scheduleStart
            prefs[KEY_SCHEDULE_END] = config.scheduleEnd
            prefs[KEY_SCHEDULE_DAYS] = config.scheduleDays.joinToString(",")
        }
    }

    /**
     * Сброс всех сохраненных параметров.
     */
    suspend fun clear() {
        context.dataStore.edit { it.clear() }
    }
}
