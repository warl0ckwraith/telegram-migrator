"""Download engine - Core dumping logic"""

import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List
import time

from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument,
    MessageService
)
from telethon.errors import FloodWaitError, FileReferenceExpiredError

from utils import sanitize_filename, get_media_type


class DownloadEngine:
    """Handles channel dumping and media downloads"""
    
    def __init__(self, client, state_manager, config, progress_tracker):
        self.client = client
        self.state = state_manager
        self.config = config
        self.progress = progress_tracker
        
        self.output_dir = Path(config.output_dir)
        self.messages_file = self.output_dir / "messages.jsonl"
        self.media_dir = self.output_dir / "media"
        
        # Create directories
        self.media_dir.mkdir(exist_ok=True)
        for media_type in ['photos', 'videos', 'documents', 'audio', 'voice', 'video_notes']:
            (self.media_dir / media_type).mkdir(exist_ok=True)
        
        # Statistics
        self.stats = {
            'messages_saved': 0,
            'files_downloaded': 0,
            'files_skipped': 0,
            'files_failed': 0,
            'total_bytes': 0,
            'start_time': time.time()
        }
    
    async def dump_channel(self, entity):
        """Main dump function"""
        # Save entity metadata
        await self._save_metadata(entity)
        
        # Start progress
        selected_ids = self._get_selected_message_ids()
        total_messages = len(selected_ids) if selected_ids else None
        self.progress.start(total_messages=total_messages)
        
        try:
            if selected_ids:
                await self._dump_selected_messages(entity, selected_ids)
            else:
                await self._dump_all_messages(entity)
                
        except FloodWaitError as e:
            self.progress.set_flood_wait(e.seconds)
            await asyncio.sleep(e.seconds)
            self.progress.clear_flood_wait()
            # Resume iteration after wait
            return await self.dump_channel(entity)
        
        except asyncio.CancelledError:
            # Save progress before exit
            await self.state.set_metadata('last_run', datetime.now().isoformat())
            raise
        
        # Complete
        self.progress.stop()
        
        # Calculate final stats
        self.stats['elapsed_time'] = time.time() - self.stats['start_time']
        self.stats['total_bytes'] = self.progress.bytes_downloaded
        self.stats['files_downloaded'] = self.progress.files_downloaded
        self.stats['files_skipped'] = self.progress.files_skipped
        self.stats['files_failed'] = self.progress.files_failed
        
        # Save final metadata
        await self.state.set_metadata('dump_completed', datetime.now().isoformat())
        await self._save_summary()
        
        return self.stats

    def _get_selected_message_ids(self) -> List[int]:
        """Return deduplicated selected message IDs preserving order."""
        selected = getattr(self.config, 'message_ids', None) or []
        result = []
        seen = set()
        for mid in selected:
            if mid not in seen:
                result.append(mid)
                seen.add(mid)
        return result

    async def _dump_all_messages(self, entity):
        """Dump messages by iterating over the entire chat/channel."""
        iter_params = {
            'limit': self.config.limit,
            'reverse': False,  # Latest first
        }

        # Use date_to as API-side upper bound when provided.
        if self.config.date_to:
            iter_params['offset_date'] = self.config.date_to

        date_from = self._normalize_datetime(self.config.date_from)
        date_to = self._normalize_datetime(self.config.date_to)
        processed_count = 0

        async for message in self.client.iter_messages(entity, **iter_params):
            message_date = self._normalize_datetime(message.date)
            if date_to and message_date and message_date > date_to:
                continue
            if date_from and message_date and message_date < date_from:
                # Iteration is newest -> oldest in this mode.
                break

            # Check if already processed (resume capability)
            if await self.state.is_message_processed(message.id):
                await self._retry_media_for_processed_message(message)
                processed_count += 1
                self.progress.update_message_progress(processed_count)
                continue

            # Skip service messages if configured
            if self.config.skip_service_messages and isinstance(message, MessageService):
                continue

            # Process message
            await self._process_message(message)

            processed_count += 1
            self.progress.update_message_progress(processed_count)

            # Respect rate limits
            await asyncio.sleep(0.1)  # Small delay between messages

    async def _dump_selected_messages(self, entity, message_ids: List[int]):
        """Dump only a specific set of message IDs."""
        total = len(message_ids)
        processed = 0
        not_found = 0

        # Telegram API supports bulk id fetch; batch for reliability.
        batch_size = 200
        for i in range(0, total, batch_size):
            batch_ids = message_ids[i:i + batch_size]
            messages = await self.client.get_messages(entity, ids=batch_ids)
            if messages is None:
                messages = []
            elif not isinstance(messages, list):
                messages = [messages]

            # Preserve requested order and keep explicit not-found handling.
            message_by_id = {msg.id: msg for msg in messages if msg is not None}

            for mid in batch_ids:
                message = message_by_id.get(mid)
                if not message:
                    not_found += 1
                    processed += 1
                    self.progress.update_message_progress(processed, total=total)
                    continue

                # Check if already processed (resume capability)
                if await self.state.is_message_processed(message.id):
                    await self._retry_media_for_processed_message(message)
                    processed += 1
                    self.progress.update_message_progress(processed, total=total)
                    continue

                # Skip service messages if configured
                if self.config.skip_service_messages and isinstance(message, MessageService):
                    processed += 1
                    self.progress.update_message_progress(processed, total=total)
                    continue

                await self._process_message(message)
                processed += 1
                self.progress.update_message_progress(processed, total=total)

                # Respect rate limits
                await asyncio.sleep(0.1)

        if not_found:
            self.progress.console.print(
                f"[yellow]⚠ {not_found} selected message(s) were not found or inaccessible.[/yellow]"
            )

    def _normalize_datetime(self, value: Optional[datetime]) -> Optional[datetime]:
        """Normalize datetime values to naive UTC for safe comparisons."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    async def _retry_media_for_processed_message(self, message):
        """Retry media download for previously processed messages when requested."""
        if not self.config.retry_failed or self.config.no_media or not message.media:
            return

        media_type = get_media_type(message.media)
        if not media_type or media_type not in self.config.media_types:
            return

        status = await self.state.get_download_status(message.id, media_type)
        if status == 'complete':
            return

        await self._download_media(message, media_type)
    
    async def _process_message(self, message):
        """Process a single message"""
        # Extract message data
        message_data = {
            'id': message.id,
            'date': message.date.isoformat() if message.date else None,
            'text': message.text or '',
            'from_id': message.from_id.user_id if hasattr(message, 'from_id') and message.from_id else None,
            'reply_to': message.reply_to_msg_id if hasattr(message, 'reply_to_msg_id') else None,
            'views': message.views if hasattr(message, 'views') else None,
            'forwards': message.forwards if hasattr(message, 'forwards') else None,
            'has_media': bool(message.media),
            'media_type': None,
            'media_file': None,
        }
        
        # Handle media
        if message.media and not self.config.no_media:
            media_type = get_media_type(message.media)
            
            if media_type and media_type in self.config.media_types:
                message_data['media_type'] = media_type
                
                # Download media
                try:
                    filename = await self._download_media(message, media_type)
                    message_data['media_file'] = str(filename) if filename else None
                except Exception as e:
                    await self.state.mark_download_failed(
                        message.id, media_type, str(e)
                    )
        
        # Save message to JSONL
        self._append_to_jsonl(message_data)
        
        # Mark as processed
        await self.state.mark_message_processed(
            message.id,
            message.date,
            message.text,
            bool(message.media),
            message_data['media_type']
        )
        
        self.stats['messages_saved'] += 1
    
    async def _download_media(self, message, media_type: str) -> Optional[Path]:
        """Download media file"""
        # Generate filename
        filename = self._generate_filename(message, media_type)
        filepath = self.media_dir / f"{media_type}s" / filename
        media_size = self._get_media_size(message.media)

        await self.state.add_download(
            message_id=message.id,
            media_type=media_type,
            filename=str(filepath.relative_to(self.output_dir)),
            size=media_size
        )

        # Check state + file system for previously completed downloads.
        if await self.state.is_download_complete(message.id, media_type):
            if filepath.exists():
                self.progress.skip_file()
                return filepath.relative_to(self.output_dir)
        
        # Check if file exists and has correct size
        if filepath.exists():
            expected_size = media_size
            if expected_size and filepath.stat().st_size == expected_size:
                await self.state.mark_download_complete(message.id, media_type)
                self.progress.skip_file()
                return filepath.relative_to(self.output_dir)
        
        # Start download
        self.progress.start_file_download(filename, media_size)
        
        try:
            # Download with progress callback
            def progress_callback(current, total):
                self.progress.update_file_progress(current)
            
            # Download file
            path = await self.client.download_media(
                message.media,
                file=str(filepath),
                progress_callback=progress_callback
            )
            
            if path:
                await self.state.mark_download_complete(message.id, media_type)
                self.progress.complete_file_download(success=True)
                return filepath.relative_to(self.output_dir)
            else:
                raise Exception("Download returned None")
                
        except FloodWaitError as e:
            # Handle flood wait
            self.progress.set_flood_wait(e.seconds)
            
            if e.seconds <= self.config.flood_sleep_threshold:
                await asyncio.sleep(e.seconds)
                self.progress.clear_flood_wait()
                # Retry
                return await self._download_media(message, media_type)
            else:
                # Too long, skip for now
                error = f"FloodWait too long: {e.seconds}s"
                await self.state.mark_download_failed(message.id, media_type, error)
                self.progress.complete_file_download(success=False, error=error)
                return None
                
        except FileReferenceExpiredError:
            # File reference expired, need to refresh
            # This is rare but can happen with old messages
            error = "File reference expired"
            await self.state.mark_download_failed(message.id, media_type, error)
            self.progress.complete_file_download(success=False, error=error)
            return None
            
        except Exception as e:
            error = str(e)
            await self.state.mark_download_failed(message.id, media_type, error)
            self.progress.complete_file_download(success=False, error=error)
            return None
    
    def _generate_filename(self, message, media_type: str) -> str:
        """Generate safe filename for media"""
        # Get original filename if available
        original_name = None
        
        if isinstance(message.media, MessageMediaDocument):
            for attr in message.media.document.attributes:
                if hasattr(attr, 'file_name'):
                    original_name = attr.file_name
                    break
        
        if original_name:
            # Use original name with message ID prefix
            safe_name = sanitize_filename(original_name)
            return f"{message.id}_{safe_name}"
        else:
            # Generate name from message ID and date
            date_str = message.date.strftime('%Y%m%d_%H%M%S') if message.date else 'unknown'
            
            # Get extension based on media type
            extensions = {
                'photo': '.jpg',
                'video': '.mp4',
                'audio': '.mp3',
                'voice': '.ogg',
                'video_note': '.mp4',
                'document': '.bin'
            }
            ext = extensions.get(media_type, '.bin')
            
            return f"{message.id}_{date_str}{ext}"
    
    def _get_media_size(self, media) -> int:
        """Get media file size"""
        if isinstance(media, MessageMediaPhoto):
            photo = media.photo
            if hasattr(photo, 'sizes'):
                sizes = [s for s in photo.sizes if hasattr(s, 'size')]
                if sizes:
                    return max(s.size for s in sizes)
        
        elif isinstance(media, MessageMediaDocument):
            if hasattr(media.document, 'size'):
                return media.document.size
        
        return 0
    
    def _append_to_jsonl(self, data: dict):
        """Append message to JSONL file"""
        with open(self.messages_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')
    
    async def _save_metadata(self, entity):
        """Save channel/chat metadata"""
        metadata = {
            'id': entity.id,
            'title': getattr(entity, 'title', getattr(entity, 'first_name', 'Unknown')),
            'username': getattr(entity, 'username', None),
            'type': type(entity).__name__,
            'dump_started': datetime.now().isoformat(),
            'config': {
                'media_types': self.config.media_types,
                'limit': self.config.limit,
                'date_from': self.config.date_from.isoformat() if self.config.date_from else None,
                'date_to': self.config.date_to.isoformat() if self.config.date_to else None,
            }
        }
        
        with open(self.output_dir / 'metadata.json', 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    async def _save_summary(self):
        """Save final summary"""
        stats = await self.state.get_statistics()
        
        summary = {
            **stats,
            'completed_at': datetime.now().isoformat(),
        }
        
        with open(self.output_dir / 'summary.json', 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
