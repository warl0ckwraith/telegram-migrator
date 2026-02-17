"""Utility functions"""

import re
import shutil
from pathlib import Path
from typing import Optional
from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument,
    MessageMediaGeo, MessageMediaContact, MessageMediaPoll
)


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    """
    Sanitize filename for safe file system usage
    
    Args:
        filename: Original filename
        max_length: Maximum length for filename
        
    Returns:
        Safe filename
    """
    # Remove or replace unsafe characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    # Remove control characters
    filename = ''.join(char for char in filename if ord(char) >= 32)
    
    # Trim whitespace
    filename = filename.strip()
    
    # Truncate if too long (keep extension)
    if len(filename) > max_length:
        name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
        name = name[:max_length - len(ext) - 1]
        filename = f"{name}.{ext}" if ext else name
    
    # Ensure not empty
    if not filename:
        filename = 'unnamed_file'
    
    return filename


def get_media_type(media) -> Optional[str]:
    """
    Determine media type from MessageMedia
    
    Returns:
        Media type: 'photo', 'video', 'audio', 'voice', 'video_note', 'document', or None
    """
    if not media:
        return None
    
    if isinstance(media, MessageMediaPhoto):
        return 'photo'
    
    elif isinstance(media, MessageMediaDocument):
        document = media.document
        
        if not hasattr(document, 'mime_type'):
            return 'document'
        
        mime_type = document.mime_type or ''
        
        # Check attributes for specific types
        for attr in document.attributes:
            attr_type = type(attr).__name__
            
            if attr_type == 'DocumentAttributeAudio':
                if getattr(attr, 'voice', False):
                    return 'voice'
                else:
                    return 'audio'
            
            elif attr_type == 'DocumentAttributeVideo':
                if getattr(attr, 'round_message', False):
                    return 'video_note'
                else:
                    return 'video'
        
        # Fallback to MIME type
        if mime_type.startswith('video/'):
            return 'video'
        elif mime_type.startswith('audio/'):
            return 'audio'
        elif mime_type.startswith('image/'):
            return 'photo'  # Some images come as documents
        else:
            return 'document'
    
    # Non-downloadable media types
    elif isinstance(media, (MessageMediaGeo, MessageMediaContact, MessageMediaPoll)):
        return None
    
    return None


def format_size(bytes: int) -> str:
    """
    Format bytes to human-readable size
    
    Args:
        bytes: Number of bytes
        
    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes < 1024.0:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.1f} PB"


def format_duration(seconds: float) -> str:
    """
    Format duration to human-readable string
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted string (e.g., "2h 34m 12s")
    """
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    
    return ' '.join(parts)


def check_disk_space(path: Path, required_bytes: int, buffer_percent: float = 10.0) -> bool:
    """
    Check if enough disk space is available
    
    Args:
        path: Path to check disk space for
        required_bytes: Required bytes
        buffer_percent: Additional buffer percentage (default 10%)
        
    Returns:
        True if enough space available
    """
    # Add buffer
    required_with_buffer = required_bytes * (1 + buffer_percent / 100)
    
    # Get disk usage
    try:
        stat = shutil.disk_usage(path)
        available = stat.free
        
        if available < required_with_buffer:
            from rich.console import Console
            console = Console()
            
            console.print("[red]Insufficient disk space![/red]")
            console.print(f"Required: {format_size(required_with_buffer)}")
            console.print(f"Available: {format_size(available)}")
            console.print(f"Missing: {format_size(required_with_buffer - available)}")
            
            return False
        
        return True
        
    except Exception:
        # If can't check, assume it's OK
        return True


def parse_target(target: str) -> tuple:
    """
    Parse target input to determine type
    
    Returns:
        (type, value) where type is 'username', 'url', 'phone', or 'id'
    """
    target = target.strip()
    
    # URL
    if 't.me/' in target.lower():
        return ('url', target)
    
    # Username
    if target.startswith('@'):
        return ('username', target[1:])
    
    # Phone
    if target.startswith('+'):
        return ('phone', target)
    
    # ID (numeric)
    if target.lstrip('-').isdigit():
        return ('id', int(target))
    
    # Assume username without @
    return ('username', target)


def parse_tme_message_link(link: str) -> Optional[dict]:
    """
    Parse Telegram message link.

    Supported formats:
    - https://t.me/<username>/<message_id>
    - https://t.me/c/<internal_chat_id>/<message_id>

    Returns:
        dict with keys:
        - chat_ref: username or full numeric peer id string (-100...)
        - message_id: int
        - original_link: original input
        or None if not a supported message link.
    """
    if not link:
        return None

    value = link.strip()

    # Private supergroup/channel format
    private_match = re.match(
        r'^(?:https?://)?(?:t\.me|telegram\.me)/c/(\d+)/(\d+)(?:[/?#].*)?$',
        value,
        re.IGNORECASE
    )
    if private_match:
        internal_id = private_match.group(1)
        message_id = int(private_match.group(2))
        return {
            'chat_ref': f"-100{internal_id}",
            'message_id': message_id,
            'original_link': link
        }

    # Public username/group message link
    public_match = re.match(
        r'^(?:https?://)?(?:t\.me|telegram\.me)/(?!c/)([A-Za-z0-9_]+)/(\d+)(?:[/?#].*)?$',
        value,
        re.IGNORECASE
    )
    if public_match:
        username = public_match.group(1)
        message_id = int(public_match.group(2))
        return {
            'chat_ref': username,
            'message_id': message_id,
            'original_link': link
        }

    return None


def validate_media_types(media_types: list) -> bool:
    """Validate media type list"""
    valid = {'photo', 'video', 'document', 'audio', 'voice', 'video_note'}
    return all(mt in valid for mt in media_types)


def estimate_eta(processed: int, total: int, elapsed: float) -> float:
    """
    Estimate time remaining
    
    Args:
        processed: Items processed
        total: Total items
        elapsed: Time elapsed in seconds
        
    Returns:
        Estimated seconds remaining
    """
    if processed == 0:
        return 0.0
    
    rate = processed / elapsed
    remaining = total - processed
    
    return remaining / rate if rate > 0 else 0.0
