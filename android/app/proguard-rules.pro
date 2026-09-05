# ProGuard rules for Media3 ExoPlayer and Kotlin Coroutines

# Keep Media3 ExoPlayer components
-keep class androidx.media3.** { *; }
-dontwarn androidx.media3.**

# Keep Coroutines internals
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory { *; }
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler { *; }

# DataStore Preferences
-keepclassmembers class * extends androidx.datastore.preferences.core.Preferences { *; }
