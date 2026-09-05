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

            StreamConfig(
                serverHost = host,
                streamType = streamType,
                rtspPort = rtspPort,
                hlsPort = hlsPort,
                streamPath = path
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
        }
    }

    /**
     * Сброс всех сохраненных параметров.
     */
    suspend fun clear() {
        context.dataStore.edit { it.clear() }
    }
}
