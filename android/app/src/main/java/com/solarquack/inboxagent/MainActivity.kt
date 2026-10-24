package com.solarquack.inboxagent

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.view.WindowCompat
import android.app.Activity
import com.google.gson.Gson
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        val db = AppDatabase.open(this)
        setContent {
            val systemDark = isSystemInDarkTheme()
            var themeMode by remember { mutableStateOf(inboxPrefs().getString("theme", "system") ?: "system") }
            val dark = themeMode == "dark" || (themeMode != "light" && systemDark)
            InboxTheme(dark) {
                InboxApp(
                    baseDefault = inboxPrefs().getString("base", "http://10.0.2.2:8000") ?: "http://10.0.2.2:8000",
                    tokenDefault = inboxPrefs().getString("token", "") ?: "",
                    onSave = { base, token ->
                        inboxPrefs().edit().putString("base", base).putString("token", token).apply()
                    },
                    cache = db.feedDao(),
                    batteryIgnored = batteryIgnored(),
                    onBattery = { askBatteryExemption() },
                    dark = dark,
                    themeMode = themeMode,
                    onTheme = { mode ->
                        themeMode = mode
                        inboxPrefs().edit().putString("theme", mode).apply()
                    },
                )
            }
        }
    }

    private fun batteryIgnored(): Boolean {
        val pm = getSystemService(PowerManager::class.java)
        return pm.isIgnoringBatteryOptimizations(packageName)
    }

    private fun askBatteryExemption() {
        val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).setData(Uri.parse("package:$packageName"))
        startActivity(intent)
    }
}

private val LightColors = lightColorScheme(
    primary = Color(0xFF243E73),
    onPrimary = Color(0xFFF6F1E8),
    background = Color(0xFFF3EFE6),
    onBackground = Color(0xFF1C1916),
    surface = Color(0xFFFAF7F1),
    onSurface = Color(0xFF1C1916),
    surfaceVariant = Color(0xFFE8E1D4),
    onSurfaceVariant = Color(0xFF6D665C),
    error = Color(0xFFC8422F),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFA9C0EF),
    onPrimary = Color(0xFF12161F),
    background = Color(0xFF141311),
    onBackground = Color(0xFFF3EFE6),
    surface = Color(0xFF1E1B17),
    onSurface = Color(0xFFF3EFE6),
    surfaceVariant = Color(0xFF2C2822),
    onSurfaceVariant = Color(0xFFC4BBB0),
    error = Color(0xFFFF7A68),
)

@Composable
private fun InboxTheme(dark: Boolean, content: @Composable () -> Unit) {
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            val bar = if (dark) android.graphics.Color.parseColor("#141311") else android.graphics.Color.parseColor("#F3EFE6")
            window.statusBarColor = bar
            window.navigationBarColor = bar
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !dark
            WindowCompat.getInsetsController(window, view).isAppearanceLightNavigationBars = !dark
        }
    }
    MaterialTheme(colorScheme = if (dark) DarkColors else LightColors, content = content)
}

private enum class Tab { Feed, Rules, Accounts, Settings }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun InboxApp(
    baseDefault: String,
    tokenDefault: String,
    onSave: (String, String) -> Unit,
    cache: FeedDao,
    batteryIgnored: Boolean,
    onBattery: () -> Unit,
    dark: Boolean,
    themeMode: String,
    onTheme: (String) -> Unit,
) {
    var base by remember { mutableStateOf(baseDefault) }
    var token by remember { mutableStateOf(tokenDefault) }
    var tab by remember { mutableStateOf(Tab.Feed) }
    var error by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val api = remember(base) { runCatching { inboxApi(base) }.getOrNull() }

    if (token.isBlank()) {
        LoginScreen(base, { base = it }, error, dark, onTheme) { email, password, demo ->
            scope.launch {
                try {
                    val client = inboxApi(base)
                    val response = if (demo) client.demo() else client.login(LoginBody(email, password))
                    token = response.access_token
                    onSave(base, token)
                    error = ""
                } catch (exc: Exception) {
                    error = exc.message ?: "Sign-in failed"
                }
            }
        }
        return
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Inbox Agent") },
                actions = {
                    TextButton(onClick = { onTheme(if (dark) "light" else "dark") }) {
                        Text(if (dark) "Light" else "Dark")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                    titleContentColor = MaterialTheme.colorScheme.onBackground,
                ),
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                Tab.entries.forEach { item ->
                    NavigationBarItem(
                        selected = tab == item,
                        onClick = { tab = item },
                        label = { Text(item.name) },
                        icon = {},
                    )
                }
            }
        },
    ) { padding ->
        when (tab) {
            Tab.Feed -> FeedScreen(padding, api, token, cache, error) { error = it }
            Tab.Rules -> SimpleList(padding, "Rules") {
                api?.rules("Bearer $token")?.map { "${it.name} · ${it.hit_count} hits" } ?: emptyList()
            }
            Tab.Accounts -> SimpleList(padding, "Accounts") {
                api?.accounts("Bearer $token")?.map { row ->
                    val banner = when (row.status) {
                        "blocked" -> " — may be blocked by its administrator"
                        "needs_reconnect" -> " — reconnect"
                        else -> ""
                    }
                    "${row.label ?: row.email} (${row.status}$banner)"
                } ?: emptyList()
            }
            Tab.Settings -> SettingsScreen(padding, api, token, batteryIgnored, onBattery, themeMode, onTheme) { error = it }
        }
    }
}

@Composable
private fun LoginScreen(
    base: String,
    onBase: (String) -> Unit,
    error: String,
    dark: Boolean,
    onTheme: (String) -> Unit,
    onSubmit: (String, String, Boolean) -> Unit,
) {
    var email by remember { mutableStateOf("demo@inboxagent.local") }
    var password by remember { mutableStateOf("demopass") }
    Column(Modifier.padding(24.dp).fillMaxSize(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Inbox Agent", style = MaterialTheme.typography.headlineMedium)
        Text("Rules first. The model only sees what your rules did not handle, and only with your own key.")
        OutlinedTextField(base, onBase, label = { Text("API base URL") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(email, { email = it }, label = { Text("Email") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(password, { password = it }, label = { Text("Password") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = { onSubmit(email, password, false) }, modifier = Modifier.fillMaxWidth()) { Text("Sign in") }
        TextButton(onClick = { onSubmit(email, password, true) }) { Text("Open the demo inbox") }
        TextButton(onClick = { onTheme(if (dark) "light" else "dark") }) { Text(if (dark) "Light mode" else "Dark mode") }
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
    }
}

@Composable
private fun FeedScreen(padding: PaddingValues, api: InboxApi?, token: String, cache: FeedDao, error: String, onError: (String) -> Unit) {
    var rows by remember { mutableStateOf(listOf<String>()) }
    var detail by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    Column(Modifier.padding(padding).padding(16.dp)) {
        Button(onClick = {
            scope.launch {
                try {
                    val feed = api?.feed("Bearer $token") ?: return@launch
                    cache.put(FeedCache("all", Gson().toJson(feed), System.currentTimeMillis()))
                    rows = feed.items.map { item ->
                        if (item.type == "group") "${item.group?.count ?: 0} similar — ${item.group?.latest_subject}"
                        else "${item.message?.priority}: ${item.message?.subject}"
                    }
                    onError("")
                } catch (exc: Exception) {
                    val cached = cache.get("all")
                    detail = if (cached != null) "Showing the last feed saved on this phone." else (exc.message ?: "Could not load")
                    onError(detail)
                }
            }
        }) { Text("Refresh") }
        if (error.isNotBlank()) Text(error)
        if (detail.isNotBlank()) Text(detail, modifier = Modifier.padding(top = 8.dp))
        LazyColumn {
            items(rows) { row -> Text(row, modifier = Modifier.padding(vertical = 8.dp)) }
        }
    }
}

@Composable
private fun SimpleList(padding: PaddingValues, title: String, load: suspend () -> List<String>) {
    var rows by remember { mutableStateOf(listOf<String>()) }
    val scope = rememberCoroutineScope()
    Column(Modifier.padding(padding).padding(16.dp)) {
        Text(title, style = MaterialTheme.typography.titleLarge)
        Button(onClick = { scope.launch { rows = runCatching { load() }.getOrElse { listOf(it.message ?: "Failed") } } }) { Text("Load") }
        LazyColumn { items(rows) { Text(it, modifier = Modifier.padding(vertical = 6.dp)) } }
    }
}

@Composable
private fun RowButtons(themeMode: String, onTheme: (String) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf("light" to "Light", "dark" to "Dark", "system" to "Match device").forEach { (mode, label) ->
            if (themeMode == mode) Button(onClick = { onTheme(mode) }) { Text(label) }
            else TextButton(onClick = { onTheme(mode) }) { Text(label) }
        }
    }
}

@Composable
private fun SettingsScreen(
    padding: PaddingValues,
    api: InboxApi?,
    token: String,
    batteryIgnored: Boolean,
    onBattery: () -> Unit,
    themeMode: String,
    onTheme: (String) -> Unit,
    onError: (String) -> Unit,
) {
    var status by remember { mutableStateOf("AI status loads when you ask") }
    var key by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    Column(Modifier.padding(padding).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Appearance", style = MaterialTheme.typography.titleLarge)
        Text("Light is the paper desk. Dark keeps the same colors, only inverted. Match device follows the phone.")
        RowButtons(themeMode, onTheme)
        Text("Battery optimization", style = MaterialTheme.typography.titleLarge)
        Text(
            if (batteryIgnored) "This phone is not restricting Inbox Agent."
            else "Some phones delay or drop notifications unless the app is exempt from battery optimization.",
        )
        if (!batteryIgnored) Button(onClick = onBattery) { Text("Request exemption") }
        Text("Your model key", style = MaterialTheme.typography.titleLarge)
        Text(status)
        OutlinedTextField(key, { key = it }, label = { Text("API key") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = {
            scope.launch {
                try {
                    val saved = api?.saveKey("Bearer $token", LlmBody("gemini", "gemini-2.0-flash-lite", key))
                    status = "Status ${saved?.status}. Key ending ${saved?.key_last4}."
                } catch (exc: Exception) {
                    onError(exc.message ?: "Could not save the key")
                }
            }
        }) { Text("Save key") }
        TextButton(onClick = {
            scope.launch {
                val settings = runCatching { api?.settings("Bearer $token") }.getOrNull()
                status = if (settings?.ai?.enabled == true) "AI is on (${settings.ai.provider})." else "Add an API key to enable. Rules still run."
            }
        }) { Text("Check AI status") }
    }
}
