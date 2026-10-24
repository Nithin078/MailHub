package com.solarquack.inboxagent

import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

data class TokenResponse(val access_token: String, val user: UserOut)
data class UserOut(val id: String, val email: String, val name: String)
data class LoginBody(val email: String, val password: String)
data class DeviceBody(val fcm_token: String, val name: String, val platform: String = "android")
data class FeedResponse(val items: List<FeedItem>, val counts: Map<String, Int>?)
data class FeedItem(val type: String, val id: String, val message: MailRow?, val group: GroupRow?)
data class MailRow(
    val id: String,
    val subject: String?,
    val sender_name: String?,
    val sender_email: String?,
    val snippet: String?,
    val priority: String?,
    val account_label: String?,
    val decision_reason: String?,
    val received_at: String?,
)
data class GroupRow(val id: String, val title: String?, val count: Int, val latest_subject: String?)
data class MailDetail(
    val id: String,
    val subject: String?,
    val sender_email: String?,
    val body_text: String?,
    val decision_reason: String?,
    val decision_trace: List<TraceStep>?,
    val priority: String?,
    val llm_called: Boolean?,
)
data class TraceStep(val step: String?, val result: String?, val detail: String?)
data class RuleOut(val id: String, val name: String, val hit_count: Int, val enabled: Boolean, val action: Map<String, Any>?)
data class AccountOut(val id: String, val email: String, val label: String?, val status: String, val status_message: String?, val provider: String)
data class SettingsOut(val ai: AiOut?, val grouping_enabled: String?)
data class AiOut(val enabled: Boolean, val status: String?, val provider: String?, val key_last4: String?)
data class LlmBody(val provider: String, val model: String, val api_key: String)

interface InboxApi {
    @POST("api/auth/login")
    suspend fun login(@Body body: LoginBody): TokenResponse

    @POST("api/auth/demo")
    suspend fun demo(): TokenResponse

    @GET("api/feed")
    suspend fun feed(@Header("Authorization") auth: String, @Query("lane") lane: String = "all"): FeedResponse

    @GET("api/messages/{id}")
    suspend fun message(@Header("Authorization") auth: String, @Path("id") id: String): MailDetail

    @GET("api/rules")
    suspend fun rules(@Header("Authorization") auth: String): List<RuleOut>

    @GET("api/accounts")
    suspend fun accounts(@Header("Authorization") auth: String): List<AccountOut>

    @GET("api/settings")
    suspend fun settings(@Header("Authorization") auth: String): SettingsOut

    @POST("api/devices")
    suspend fun device(@Header("Authorization") auth: String, @Body body: DeviceBody): Map<String, String>

    @POST("api/notifications/{id}/ack")
    suspend fun ack(@Header("Authorization") auth: String, @Path("id") id: String): Map<String, Boolean>

    @PUT("api/llm")
    suspend fun saveKey(@Header("Authorization") auth: String, @Body body: LlmBody): AiOut
}

fun inboxApi(baseUrl: String): InboxApi {
    val root = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
    return Retrofit.Builder()
        .baseUrl(root)
        .addConverterFactory(GsonConverterFactory.create())
        .build()
        .create(InboxApi::class.java)
}
