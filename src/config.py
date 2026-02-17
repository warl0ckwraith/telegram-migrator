"""Configuration management for Telegram Migrator."""

import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

from utils import parse_tme_message_link


class Config:
    """Application configuration."""

    def __init__(self):
        # Target
        self.target: str = None
        self.dest_target: Optional[str] = None
        self.output_dir: Path = None
        self.operation: str = 'dump'

        # Authentication
        self.session_file: Path = None
        self.api_id: int = None
        self.api_hash: str = None

        # Media options
        self.media_types: List[str] = ['all']
        self.no_media: bool = False

        # Filtering
        self.limit: Optional[int] = None
        self.date_from: Optional[datetime] = None
        self.date_to: Optional[datetime] = None
        self.skip_service_messages: bool = False
        self.message_ids: List[int] = []

        # Size estimation / retry
        self.accurate_size: bool = False
        self.retry_failed: bool = False

        # Transfer mode (source -> destination)
        self.transfer_mode: str = 'forward'
        self.transfer_profile: str = 'balanced'
        self.transfer_batch: int = 30
        self.transfer_delay: float = 0.35
        self.transfer_jitter: float = 0.9
        self.transfer_batch_min: int = 8
        self.transfer_batch_max: int = 80
        self.transfer_delay_min: float = 0.05
        self.transfer_delay_max: float = 8.0
        self.transfer_skip_known_bad: bool = False
        self.transfer_max_retries: int = 6
        self.transfer_eta_every: float = 20.0
        self.transfer_eta_alpha: float = 0.12
        self.transfer_db_path: Path = None
        self.transfer_message_ids: List[int] = []

        # Advanced
        self.proxy: Optional[str] = None
        self.debug: bool = False

        # Telethon specific
        self.flood_sleep_threshold: int = 3600  # Auto-sleep up to 1 hour
        self.download_chunk_size: int = 128 * 1024  # 128 KB chunks

    @classmethod
    def from_args(cls, args):
        """Create config from command-line arguments."""
        config = cls()

        # Operation and required targeting fields are parser-enforced.
        config.operation = args.command
        config.target = args.channel.strip() if args.channel else None
        config.dest_target = args.dest.strip() if hasattr(args, 'dest') and args.dest else None
        config.output_dir = Path(args.output).resolve()

        # Parse message links passed via --channel (direct message link support).
        channel_link_target = None
        if config.target and config.operation == 'dump':
            parsed_target_link = parse_tme_message_link(config.target)
            if parsed_target_link:
                config.target = parsed_target_link['chat_ref']
                config.message_ids.append(parsed_target_link['message_id'])
                channel_link_target = parsed_target_link['chat_ref']

        # Authentication
        if args.session:
            config.session_file = Path(args.session).resolve()
        else:
            config.session_file = Path.home() / '.telegram-migrator.session'

        config.api_id = args.api_id or cls._get_env_int('TG_API_ID')
        config.api_hash = args.api_hash or os.getenv('TG_API_HASH')
        if not config.api_id or not config.api_hash:
            raise ValueError(
                "API credentials required. Either:\n"
                "  1. Pass --api-id and --api-hash\n"
                "  2. Set TG_API_ID and TG_API_HASH environment variables\n"
                "  3. Get credentials from https://my.telegram.org/apps"
            )

        # Common runtime options
        config.proxy = args.proxy
        config.debug = args.debug

        if config.operation == 'dump':
            config.no_media = args.no_media
            if not config.no_media:
                config.media_types = cls._parse_media_types(args.media_types)

            config.limit = args.limit
            config.date_from = cls._parse_datetime(args.date_from) if args.date_from else None
            config.date_to = cls._parse_datetime(args.date_to) if args.date_to else None
            config.skip_service_messages = args.skip_service_messages
            config.accurate_size = args.accurate_size
            config.retry_failed = args.retry_failed

            inferred_targets: Set[str] = set()
            selectors = args.select or []
            if selectors:
                selected_ids, selected_targets = cls._parse_selectors(selectors)
                config.message_ids.extend(selected_ids)
                inferred_targets.update(selected_targets)

            if not config.target:
                if inferred_targets:
                    if len(inferred_targets) == 1:
                        config.target = next(iter(inferred_targets))
                    else:
                        raise ValueError(
                            "Multiple selector targets detected. Provide --channel to choose one target."
                        )
                else:
                    raise ValueError("Target required. Provide --channel or --select.")

            if channel_link_target and inferred_targets and channel_link_target not in inferred_targets:
                raise ValueError(
                    "Channel direct message link and selectors point to different chats."
                )

            # Deduplicate message IDs preserving order
            seen = set()
            deduped_ids: List[int] = []
            for mid in config.message_ids:
                if mid not in seen:
                    deduped_ids.append(mid)
                    seen.add(mid)
            config.message_ids = deduped_ids

        elif config.operation == 'transfer':
            if not config.target:
                raise ValueError("Transfer mode requires source via --channel.")
            if not config.dest_target:
                raise ValueError("Transfer mode requires --dest.")

            # Allow transfer source as a direct message link.
            # Example: https://t.me/c/<chat>/<msg_id> or https://t.me/<username>/<msg_id>
            parsed_target_link = parse_tme_message_link(config.target)
            if parsed_target_link:
                config.target = parsed_target_link['chat_ref']
                config.transfer_message_ids = [parsed_target_link['message_id']]

            config.transfer_mode = args.mode
            config.transfer_profile = args.profile
            cls._apply_transfer_profile(config, args.profile)
            config.transfer_skip_known_bad = args.skip_known_bad
            config.transfer_db_path = (
                Path(args.db).resolve() if args.db else config.output_dir / ".transfer_state.sqlite3"
            )
            cls._validate_transfer_options(config)

        else:
            raise ValueError(f"Unsupported operation: {config.operation}")

        return config

    @staticmethod
    def _get_env_int(key: str) -> Optional[int]:
        """Get integer from environment variable."""
        value = os.getenv(key)
        if value:
            try:
                return int(value)
            except ValueError:
                raise ValueError(f"Invalid integer value for {key}: {value}")
        return None

    @staticmethod
    def _parse_media_types(media_types_str: str) -> List[str]:
        """Parse media types from comma-separated string."""
        if media_types_str.lower() == 'all':
            return ['photo', 'video', 'document', 'audio', 'voice', 'video_note']

        valid_types = {
            'photo', 'photos', 'video', 'videos',
            'document', 'documents', 'audio', 'voice',
            'video_note', 'video_notes'
        }
        normalize_map = {
            'photos': 'photo',
            'videos': 'video',
            'documents': 'document',
            'video_notes': 'video_note',
        }

        types: List[str] = []
        for token in media_types_str.lower().split(','):
            item = token.strip()
            if item in valid_types:
                normalized = normalize_map.get(item, item)
                if normalized not in types:
                    types.append(normalized)
            else:
                raise ValueError(f"Invalid media type: {item}")
        return types

    @staticmethod
    def _parse_datetime(date_str: str) -> datetime:
        """Parse datetime from string."""
        formats = [
            '%Y-%m-%d',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S',
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS'")

    @staticmethod
    def _parse_selectors(values: List[str]) -> Tuple[List[int], Set[str]]:
        """Parse unified selector syntax into message IDs and inferred targets."""
        ids: List[int] = []
        inferred_targets: Set[str] = set()

        for value in values:
            tokens = [token.strip() for token in value.split(',') if token.strip()]
            for token in tokens:
                lowered = token.lower()

                if lowered.startswith('id:'):
                    raw_id = token.split(':', 1)[1].strip()
                    if not raw_id.isdigit():
                        raise ValueError(f"Invalid selector '{token}'. Expected id:<number>.")
                    ids.append(int(raw_id))
                    continue

                if lowered.startswith('range:'):
                    raw_range = token.split(':', 1)[1].strip()
                    ids.extend(Config._parse_message_range(raw_range))
                    continue

                if lowered.startswith('link:'):
                    link_value = token.split(':', 1)[1].strip()
                    parsed = parse_tme_message_link(link_value)
                    if not parsed:
                        raise ValueError(f"Invalid selector '{token}'. Expected link:<t.me/...>.")
                    inferred_targets.add(parsed['chat_ref'])
                    ids.append(parsed['message_id'])
                    continue

                parsed_link = parse_tme_message_link(token)
                if parsed_link:
                    inferred_targets.add(parsed_link['chat_ref'])
                    ids.append(parsed_link['message_id'])
                    continue

                if token.isdigit():
                    ids.append(int(token))
                    continue

                if '-' in token:
                    ids.extend(Config._parse_message_range(token))
                    continue

                raise ValueError(
                    f"Invalid selector '{token}'. Use id:<n>, range:<start-end>, link:<t.me/...>, numeric ID, or direct link."
                )

        return ids, inferred_targets

    @staticmethod
    def _parse_message_range(value: str) -> List[int]:
        """Parse inclusive range start-end."""
        text = value.strip()
        if '-' not in text:
            raise ValueError(f"Invalid range '{value}'. Use start-end.")

        start_text, end_text = text.split('-', 1)
        start_text = start_text.strip()
        end_text = end_text.strip()

        if not start_text.isdigit() or not end_text.isdigit():
            raise ValueError(f"Invalid range '{value}'. Use numeric start-end.")

        start = int(start_text)
        end = int(end_text)
        if end < start:
            raise ValueError(f"Invalid range '{value}'. End must be >= start.")

        if (end - start) > 50000:
            raise ValueError(
                f"Range too large in '{value}'. Maximum supported span is 50,001 IDs."
            )

        return list(range(start, end + 1))

    @staticmethod
    def _apply_transfer_profile(config, profile: str) -> None:
        """Apply transfer profile defaults."""
        profiles = {
            'safe': {
                'transfer_batch': 15,
                'transfer_delay': 0.8,
                'transfer_jitter': 1.2,
                'transfer_batch_min': 6,
                'transfer_batch_max': 40,
                'transfer_delay_min': 0.2,
                'transfer_delay_max': 12.0,
                'transfer_max_retries': 8,
                'transfer_eta_every': 25.0,
                'transfer_eta_alpha': 0.10,
            },
            'balanced': {
                'transfer_batch': 30,
                'transfer_delay': 0.35,
                'transfer_jitter': 0.9,
                'transfer_batch_min': 8,
                'transfer_batch_max': 80,
                'transfer_delay_min': 0.05,
                'transfer_delay_max': 8.0,
                'transfer_max_retries': 6,
                'transfer_eta_every': 20.0,
                'transfer_eta_alpha': 0.12,
            },
            'fast': {
                'transfer_batch': 50,
                'transfer_delay': 0.15,
                'transfer_jitter': 0.45,
                'transfer_batch_min': 12,
                'transfer_batch_max': 110,
                'transfer_delay_min': 0.02,
                'transfer_delay_max': 6.0,
                'transfer_max_retries': 5,
                'transfer_eta_every': 15.0,
                'transfer_eta_alpha': 0.15,
            },
        }
        if profile not in profiles:
            raise ValueError(f"Invalid transfer profile: {profile}")
        for key, value in profiles[profile].items():
            setattr(config, key, value)

    @staticmethod
    def _validate_transfer_options(config) -> None:
        """Validate transfer tuning bounds."""
        if config.transfer_mode not in {'forward', 'copy'}:
            raise ValueError(f"Invalid transfer mode: {config.transfer_mode}")
        if config.transfer_batch_min <= 0 or config.transfer_batch_max <= 0:
            raise ValueError("Transfer batch min/max must be > 0.")
        if config.transfer_batch_min > config.transfer_batch_max:
            raise ValueError("Transfer batch min cannot exceed transfer batch max.")
        if config.transfer_delay_min < 0 or config.transfer_delay_max < 0:
            raise ValueError("Transfer delay min/max must be >= 0.")
        if config.transfer_delay_min > config.transfer_delay_max:
            raise ValueError("Transfer delay min cannot exceed transfer delay max.")
        if config.transfer_max_retries < 0:
            raise ValueError("Transfer max retries must be >= 0.")
