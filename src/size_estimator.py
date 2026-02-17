"""Size estimation for downloads"""

from typing import List
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument


class SizeEstimator:
    """Estimates download size before starting"""
    
    def __init__(self, client, media_types: List[str]):
        self.client = client
        self.media_types = media_types
        
    async def estimate_fast(self, entity, total_messages: int, sample_size: int = 500) -> int:
        """
        Fast estimation by sampling messages
        
        Args:
            entity: Channel/chat entity
            total_messages: Total message count
            sample_size: Number of messages to sample
            
        Returns:
            Estimated size in bytes
        """
        if total_messages == 0:
            return 0
        
        # Sample messages
        sample_limit = min(sample_size, total_messages)
        sampled_size = 0
        sampled_count = 0
        
        try:
            async for message in self.client.iter_messages(entity, limit=sample_limit):
                size = self._get_message_media_size(message)
                if size:
                    sampled_size += size
                    sampled_count += 1
        except Exception:
            # If sampling fails, return conservative estimate
            return 0
        
        if sampled_count == 0:
            return 0
        
        # Calculate average size per message with media
        avg_size = sampled_size / sampled_count
        
        # Estimate percentage of messages with media
        media_percentage = sampled_count / sample_limit
        
        # Extrapolate to total
        estimated_total = int(avg_size * total_messages * media_percentage)
        
        return estimated_total
    
    async def estimate_accurate(self, entity, total_messages: int) -> int:
        """
        Accurate estimation by scanning all messages
        
        Warning: This can be slow for large channels!
        
        Args:
            entity: Channel/chat entity
            total_messages: Total message count
            
        Returns:
            Accurate size in bytes
        """
        if total_messages == 0:
            return 0
        
        total_size = 0
        
        try:
            async for message in self.client.iter_messages(entity):
                size = self._get_message_media_size(message)
                if size:
                    total_size += size
        except Exception:
            return 0
        
        return total_size
    
    def _get_message_media_size(self, message) -> int:
        """Get size of media in message"""
        if not message or not message.media:
            return 0
        
        media = message.media
        
        # Photos
        if isinstance(media, MessageMediaPhoto):
            if 'photo' not in self.media_types:
                return 0
            
            photo = media.photo
            if hasattr(photo, 'sizes') and photo.sizes:
                # Get largest size
                try:
                    sizes = [s for s in photo.sizes if hasattr(s, 'size')]
                    if sizes:
                        return max(s.size for s in sizes)
                except Exception:
                    # Estimate if can't get actual size
                    return 500_000  # ~500KB average for photos
            return 0
        
        # Documents (videos, files, audio, voice, etc.)
        elif isinstance(media, MessageMediaDocument):
            document = media.document
            
            if not hasattr(document, 'mime_type'):
                return 0
            
            mime_type = document.mime_type or ''
            
            # Check if this media type is requested
            if mime_type.startswith('video/'):
                if 'video' not in self.media_types:
                    return 0
            elif mime_type.startswith('audio/'):
                if 'audio' not in self.media_types and 'voice' not in self.media_types:
                    return 0
            elif mime_type.startswith('image/'):
                # Some images come as documents (GIFs, stickers)
                if 'document' not in self.media_types:
                    return 0
            else:
                if 'document' not in self.media_types:
                    return 0
            
            # Get document size
            if hasattr(document, 'size'):
                return document.size
        
        return 0
