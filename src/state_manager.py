"""State management for download resume and tracking"""

import aiosqlite
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime


class StateManager:
    """Manages download state using SQLite"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db = None
        
    async def initialize(self):
        """Initialize database and create tables"""
        self.db = await aiosqlite.connect(str(self.db_path))
        
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY,
                date TEXT,
                text TEXT,
                has_media BOOLEAN,
                media_type TEXT,
                processed_at TEXT
            )
        """)
        
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                media_type TEXT,
                filename TEXT,
                size INTEGER,
                status TEXT,  -- pending, complete, failed
                error TEXT,
                attempts INTEGER DEFAULT 0,
                downloaded_at TEXT,
                UNIQUE(message_id, media_type)
            )
        """)
        
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        await self.db.commit()
    
    async def close(self):
        """Close database connection"""
        if self.db:
            await self.db.close()
    
    # Message tracking
    async def is_message_processed(self, message_id: int) -> bool:
        """Check if message was already processed"""
        async with self.db.execute(
            "SELECT 1 FROM messages WHERE message_id = ?",
            (message_id,)
        ) as cursor:
            result = await cursor.fetchone()
            return result is not None
    
    async def mark_message_processed(self, message_id: int, date: datetime,
                                     text: str, has_media: bool, media_type: str = None):
        """Mark message as processed"""
        await self.db.execute("""
            INSERT OR REPLACE INTO messages
            (message_id, date, text, has_media, media_type, processed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            message_id,
            date.isoformat() if date else None,
            text[:1000] if text else None,  # Limit text length
            has_media,
            media_type,
            datetime.now().isoformat()
        ))
        await self.db.commit()
    
    # Download tracking
    async def add_download(self, message_id: int, media_type: str,
                          filename: str, size: int):
        """Add download to queue"""
        await self.db.execute("""
            INSERT OR IGNORE INTO downloads
            (message_id, media_type, filename, size, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (message_id, media_type, filename, size))
        await self.db.commit()
    
    async def mark_download_complete(self, message_id: int, media_type: str):
        """Mark download as complete"""
        now = datetime.now().isoformat()
        await self.db.execute("""
            INSERT INTO downloads
            (message_id, media_type, status, downloaded_at)
            VALUES (?, ?, 'complete', ?)
            ON CONFLICT(message_id, media_type) DO UPDATE SET
                status = 'complete',
                downloaded_at = excluded.downloaded_at
        """, (message_id, media_type, now))
        await self.db.commit()
    
    async def mark_download_failed(self, message_id: int, media_type: str,
                                  error: str):
        """Mark download as failed"""
        await self.db.execute("""
            INSERT INTO downloads
            (message_id, media_type, status, error, attempts)
            VALUES (?, ?, 'failed', ?, 1)
            ON CONFLICT(message_id, media_type) DO UPDATE SET
                status = 'failed',
                error = excluded.error,
                attempts = downloads.attempts + 1
        """, (message_id, media_type, error[:500]))
        await self.db.commit()
    
    async def is_download_complete(self, message_id: int, media_type: str) -> bool:
        """Check if download is already complete"""
        async with self.db.execute("""
            SELECT status FROM downloads
            WHERE message_id = ? AND media_type = ?
        """, (message_id, media_type)) as cursor:
            result = await cursor.fetchone()
            return result and result[0] == 'complete'

    async def get_download_status(self, message_id: int, media_type: str) -> Optional[str]:
        """Get current download status for a message/media pair."""
        async with self.db.execute("""
            SELECT status FROM downloads
            WHERE message_id = ? AND media_type = ?
        """, (message_id, media_type)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None
    
    async def get_failed_downloads(self) -> List[Dict]:
        """Get all failed downloads for retry"""
        async with self.db.execute("""
            SELECT message_id, media_type, filename, size, error, attempts
            FROM downloads
            WHERE status = 'failed'
        """) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    'message_id': row[0],
                    'media_type': row[1],
                    'filename': row[2],
                    'size': row[3],
                    'error': row[4],
                    'attempts': row[5]
                }
                for row in rows
            ]
    
    async def get_pending_downloads(self) -> List[Dict]:
        """Get all pending downloads"""
        async with self.db.execute("""
            SELECT message_id, media_type, filename, size
            FROM downloads
            WHERE status = 'pending'
        """) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    'message_id': row[0],
                    'media_type': row[1],
                    'filename': row[2],
                    'size': row[3]
                }
                for row in rows
            ]
    
    # Statistics
    async def get_statistics(self) -> Dict:
        """Get download statistics"""
        stats = {}
        
        # Message stats
        async with self.db.execute("SELECT COUNT(*) FROM messages") as cursor:
            stats['messages_processed'] = (await cursor.fetchone())[0]
        
        # Download stats
        async with self.db.execute("""
            SELECT status, COUNT(*), SUM(size)
            FROM downloads
            GROUP BY status
        """) as cursor:
            rows = await cursor.fetchall()
            for status, count, total_size in rows:
                stats[f'downloads_{status}'] = count
                stats[f'bytes_{status}'] = total_size or 0
        
        return stats
    
    # Metadata
    async def set_metadata(self, key: str, value: str):
        """Store metadata"""
        await self.db.execute("""
            INSERT OR REPLACE INTO metadata (key, value)
            VALUES (?, ?)
        """, (key, value))
        await self.db.commit()
    
    async def get_metadata(self, key: str) -> Optional[str]:
        """Get metadata"""
        async with self.db.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,)
        ) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else None
