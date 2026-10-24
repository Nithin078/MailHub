package com.solarquack.inboxagent

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase

@Entity(tableName = "feed_cache")
data class FeedCache(
    @PrimaryKey val lane: String,
    val json: String,
    val savedAt: Long,
)

@Dao
interface FeedDao {
    @Query("SELECT * FROM feed_cache WHERE lane = :lane")
    suspend fun get(lane: String): FeedCache?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun put(row: FeedCache)
}

@Database(entities = [FeedCache::class], version = 1)
abstract class AppDatabase : RoomDatabase() {
    abstract fun feedDao(): FeedDao

    companion object {
        fun open(context: Context): AppDatabase =
            Room.databaseBuilder(context, AppDatabase::class.java, "inbox-agent.db").build()
    }
}
