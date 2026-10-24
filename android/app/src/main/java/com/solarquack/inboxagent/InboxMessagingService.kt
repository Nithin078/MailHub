package com.solarquack.inboxagent

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Data-only FCM messages. The app builds the notification so each priority
 * can have its own channel, sound, and actions.
 */
class InboxMessagingService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        val priority = data["priority"] ?: "normal"
        val id = (data["notification_id"] ?: data["message_id"] ?: "0").hashCode()
        ensureChannels()
        val open = PendingIntent.getActivity(
            this,
            id,
            Intent(this, MainActivity::class.java).putExtra("message_id", data["message_id"]),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, channelFor(priority))
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(data["title"] ?: "Inbox Agent")
            .setContentText(data["body"] ?: "")
            .setPriority(if (priority == "urgent") NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .setContentIntent(open)
            .build()
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(id, notification)
        acknowledge(data["notification_id"])
    }

    override fun onNewToken(token: String) {
        val prefs = getSharedPreferences("inbox", MODE_PRIVATE)
        prefs.edit().putString("fcm", token).apply()
        val base = prefs.getString("base", null) ?: return
        val access = prefs.getString("token", null) ?: return
        CoroutineScope(Dispatchers.IO).launch {
            runCatching {
                inboxApi(base).device("Bearer $access", DeviceBody(token, android.os.Build.MODEL))
            }
        }
    }

    private fun acknowledge(notificationId: String?) {
        if (notificationId.isNullOrBlank()) return
        val prefs = getSharedPreferences("inbox", MODE_PRIVATE)
        val base = prefs.getString("base", null) ?: return
        val access = prefs.getString("token", null) ?: return
        CoroutineScope(Dispatchers.IO).launch {
            runCatching { inboxApi(base).ack("Bearer $access", notificationId) }
        }
    }

    private fun ensureChannels() {
        val manager = getSystemService(NotificationManager::class.java)
        listOf(
            Triple("urgent", "Urgent", NotificationManager.IMPORTANCE_HIGH),
            Triple("normal", "Normal", NotificationManager.IMPORTANCE_DEFAULT),
            Triple("digest", "Digest", NotificationManager.IMPORTANCE_LOW),
        ).forEach { (id, name, importance) ->
            manager.createNotificationChannel(NotificationChannel(id, name, importance))
        }
    }

    private fun channelFor(priority: String) = when (priority) {
        "urgent" -> "urgent"
        "digest" -> "digest"
        else -> "normal"
    }
}

fun Context.inboxPrefs() = getSharedPreferences("inbox", Context.MODE_PRIVATE)
