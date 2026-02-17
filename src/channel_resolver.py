"""Channel/Chat resolver"""

import re
from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError, ChannelPrivateError
from telethon.tl.types import Channel, Chat, User
from rich.console import Console
from rich.prompt import Confirm


console = Console()


class ChannelResolver:
    """Resolves various channel/chat input formats to Telegram entities"""
    
    def __init__(self, client, prompt_for_invite_join: bool = False):
        self.client = client
        self.prompt_for_invite_join = prompt_for_invite_join
        
    async def resolve(self, target: str):
        """
        Resolve target to entity
        
        Supports:
        - @username
        - https://t.me/username
        - https://t.me/joinchat/xxxxx (invite links)
        - +1234567890 (phone numbers for private chats)
        - Numeric IDs (channel/chat ID)
        """
        target = target.strip()
        
        # Try different resolution methods
        try:
            # Method 1: t.me links
            if 't.me/' in target.lower():
                return await self._resolve_tme_link(target, allow_join_invites=True)
            
            # Method 2: Numeric ID
            if target.lstrip('-').isdigit():
                entity_id = int(target)
                entity = await self.client.get_entity(entity_id)
                return entity, self._get_entity_type(entity)
            
            # Method 3: Phone number
            if target.startswith('+') and target[1:].isdigit():
                entity = await self.client.get_entity(target)
                return entity, self._get_entity_type(entity)

            # Method 4: Explicit username
            if target.startswith('@'):
                username = target.lstrip('@')
                entity = await self.client.get_entity(username)
                return entity, self._get_entity_type(entity)

            # Method 5: Text query (username/title/wildcard)
            if self._is_text_query(target):
                try:
                    # Try as username-like identifier first.
                    entity = await self.client.get_entity(target)
                    return entity, self._get_entity_type(entity)
                except Exception:
                    # Fall back to local dialog title search and selection.
                    entity = await self._resolve_by_dialog_title(target)
                    if entity:
                        return entity, self._get_entity_type(entity)
                    raise
            
            # Fallback: try as-is
            entity = await self.client.get_entity(target)
            return entity, self._get_entity_type(entity)
            
        except (UsernameInvalidError, UsernameNotOccupiedError):
            raise ValueError(f"Channel/user not found: {target}")
        except ChannelPrivateError:
            raise ValueError(f"Cannot access private channel: {target}. You may need to join it first.")
        except Exception as e:
            raise ValueError(f"Failed to resolve target '{target}': {e}")
    
    async def resolve_spec(self, spec: str, allow_join_invites: bool = False):
        """
        Resolve chat spec for transfer mode.

        Supported:
        - invite:<t.me/+hash or joinchat/...>
        - title:<Exact dialog title>
        - id:<-100... or positive id>
        - @username / username / t.me links / wildcard title input
        """
        spec = (spec or "").strip()
        if not spec:
            raise ValueError("Empty chat spec")

        lowered = spec.lower()

        if lowered.startswith("invite:"):
            invite_value = spec.split(":", 1)[1].strip()
            return await self._resolve_invite(invite_value, allow_join_invites=allow_join_invites)

        if lowered.startswith("title:"):
            title = spec.split(":", 1)[1].strip()
            entity = await self._resolve_title_exact(title)
            return entity, self._get_entity_type(entity)

        if lowered.startswith("id:"):
            raw = spec.split(":", 1)[1].strip()
            entity = await self._resolve_id(raw)
            return entity, self._get_entity_type(entity)

        return await self.resolve(spec)

    async def _resolve_tme_link(self, link: str, allow_join_invites: bool = True):
        """Resolve t.me links"""
        # Extract username or invite hash. Keep type with pattern to avoid
        # misclassifying https://t.me/+... as a username.
        patterns = [
            ("invite", r't\.me/joinchat/([a-zA-Z0-9_-]+)(?:[/?#]|$)'),  # invite link
            ("invite", r't\.me/\+([a-zA-Z0-9_-]+)(?:[/?#]|$)'),  # new invite format
            ("username", r't\.me/([a-zA-Z0-9_]+)(?:[/?#]|$)'),  # t.me/username
        ]
        
        for link_type, pattern in patterns:
            match = re.search(pattern, link, re.IGNORECASE)
            if match:
                identifier = match.group(1)
                
                # For joinchat links, need special handling
                if link_type == "invite":
                    return await self._resolve_invite(identifier, allow_join_invites=allow_join_invites)
                else:
                    # Regular username
                    entity = await self.client.get_entity(identifier)
                    return entity, self._get_entity_type(entity)
        
        raise ValueError(f"Invalid t.me link format: {link}")

    async def _resolve_invite(self, invite_or_hash: str, allow_join_invites: bool):
        """Resolve invite link/hash and optionally join if needed."""
        invite_hash = self._normalize_invite_hash(invite_or_hash)

        try:
            from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
        except Exception as e:
            raise ValueError(f"Failed to load invite handlers: {e}")

        try:
            check = await self.client(CheckChatInviteRequest(invite_hash))
            # Already joined
            chat = getattr(check, 'chat', None) or getattr(check, 'channel', None)
            if chat is not None:
                return chat, self._get_entity_type(chat)

            # Preview only and not joined
            if not allow_join_invites:
                raise ValueError(
                    "Invite resolves to a chat you're not joined to. "
                    "Join manually first or enable invite auto-join for transfer."
                )

            if self.prompt_for_invite_join:
                chat_title = getattr(check, 'title', 'this chat')
                should_join = Confirm.ask(
                    f"[yellow]You're not a member of '{chat_title}'. Join now?[/yellow]",
                    default=True
                )
                if not should_join:
                    raise ValueError("Join cancelled by user.")

            result = await self.client(ImportChatInviteRequest(invite_hash))
            if hasattr(result, 'chats') and result.chats:
                entity = result.chats[0]
                console.print("[green]✓ Joined invite chat successfully.[/green]")
                return entity, self._get_entity_type(entity)

            raise ValueError("Invite resolved but no chats returned")
        except Exception as e:
            raise ValueError(f"Cannot access invite link: {e}")

    async def _resolve_title_exact(self, title: str):
        """Resolve exact dialog title among joined channel/group dialogs."""
        title = (title or "").strip()
        if not title:
            raise ValueError("Empty title in title:<name> spec")

        matches = []
        async for dialog in self.client.iter_dialogs():
            is_supported = bool(getattr(dialog, 'is_channel', False) or getattr(dialog, 'is_group', False))
            if is_supported and (dialog.name or "").strip() == title:
                matches.append(dialog)

        if not matches:
            raise ValueError(
                f"Not found in your dialogs: '{title}'. Ensure your account is a member."
            )

        if len(matches) == 1:
            return matches[0].entity

        return self._prompt_for_match(f"title:{title}", matches)

    async def _resolve_id(self, raw_id: str):
        """Resolve numeric id with -100 fallback for channels."""
        text = (raw_id or "").strip()
        if not text or not text.lstrip('-').isdigit():
            raise ValueError(f"Invalid id spec: {raw_id}")

        rid = int(text)
        try:
            return await self.client.get_entity(rid)
        except Exception:
            if rid > 0:
                try:
                    return await self.client.get_entity(int(f"-100{rid}"))
                except Exception:
                    pass
            raise

    def _normalize_invite_hash(self, invite: str) -> str:
        """Extract raw invite hash from URL/hash input."""
        value = (invite or "").strip()
        if "t.me/" in value:
            tail = value.split("t.me/")[-1].strip("/")
            if tail.startswith("+"):
                tail = tail[1:]
            if tail.startswith("joinchat/"):
                tail = tail.split("joinchat/", 1)[1]
            return tail
        return value.lstrip("+")

    def _is_text_query(self, target: str) -> bool:
        """Check if target should be treated as a text query."""
        if not target:
            return False
        if 't.me/' in target.lower():
            return False
        if target.startswith('@'):
            return False
        if target.lstrip('-').isdigit():
            return False
        if target.startswith('+') and target[1:].isdigit():
            return False
        return True

    async def _resolve_by_dialog_title(self, title: str):
        """Resolve entity by dialog title/name (supports wildcard *)."""
        raw_query = title.strip()
        wildcard = '*' in raw_query
        query = raw_query.replace('*', '').strip().lower()
        if not query:
            return None

        exact_matches = []
        prefix_matches = []
        partial_matches = []

        async for dialog in self.client.iter_dialogs():
            name = (dialog.name or "").strip()
            if not name:
                continue

            lowered = name.lower()
            username = (getattr(dialog.entity, 'username', None) or "").strip().lower()

            if lowered == query or (username and username == query):
                exact_matches.append(dialog)
            elif lowered.startswith(query) or (username and username.startswith(query)):
                prefix_matches.append(dialog)
            elif query in lowered or (username and query in username):
                partial_matches.append(dialog)

        # Prefer exact match, then prefix, then contains.
        candidates = []
        if not wildcard and exact_matches:
            candidates = exact_matches
        elif prefix_matches:
            candidates = prefix_matches
        elif partial_matches:
            candidates = partial_matches

        if len(candidates) == 1:
            return candidates[0].entity
        if len(candidates) > 1:
            return self._prompt_for_match(title, candidates)

        return None

    def _prompt_for_match(self, query: str, dialogs):
        """Prompt user to select one dialog from multiple matches."""
        shown = dialogs[:20]

        print(f"\nMultiple chats match '{query}'. Choose one:")
        for idx, dialog in enumerate(shown, start=1):
            entity = dialog.entity
            name = (dialog.name or "").strip() or "Unknown"
            entity_type = self._get_entity_type(entity)
            username = getattr(entity, 'username', None)

            line = f"  {idx}. {name} ({entity_type}, id={entity.id})"
            if username:
                line += f", @{username}"
            print(line)

        if len(dialogs) > len(shown):
            print(f"  ... showing first {len(shown)} of {len(dialogs)} matches")

        while True:
            try:
                choice = input(f"Enter 1-{len(shown)} (or press Enter to cancel): ").strip()
            except EOFError:
                raise ValueError(
                    "Cannot prompt for selection in non-interactive mode. "
                    "Please use channel ID or invite link."
                )

            if choice == "":
                raise ValueError("Selection cancelled. Please provide channel ID or invite link.")

            if choice.isdigit():
                index = int(choice)
                if 1 <= index <= len(shown):
                    return shown[index - 1].entity

            print(f"Invalid selection. Enter a number between 1 and {len(shown)}.")
    
    def _get_entity_type(self, entity) -> str:
        """Determine entity type"""
        if isinstance(entity, Channel):
            if entity.broadcast:
                return "Channel"
            elif entity.megagroup:
                return "Megagroup"
            else:
                return "Group"
        elif isinstance(entity, Chat):
            return "Group"
        elif isinstance(entity, User):
            return "User (Private Chat)"
        else:
            return "Unknown"
