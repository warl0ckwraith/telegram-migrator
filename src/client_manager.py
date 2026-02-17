"""Telegram client management"""

from urllib.parse import urlparse

from telethon import TelegramClient
from rich.prompt import Prompt
from rich.console import Console

console = Console()


class ClientManager:
    """Manages Telegram client connection and authentication"""
    
    def __init__(self, config):
        self.config = config
        self.client = None
        
    async def connect(self):
        """Initialize and connect Telegram client"""
        client_kwargs = {
            'flood_sleep_threshold': self.config.flood_sleep_threshold
        }

        if self.config.proxy:
            client_kwargs['proxy'] = self._build_proxy(self.config.proxy)
            console.print(f"[cyan]Using proxy:[/cyan] {self.config.proxy}")

        # Create client
        self.client = TelegramClient(
            str(self.config.session_file),
            self.config.api_id,
            self.config.api_hash,
            **client_kwargs
        )

        # Connect
        await self.client.connect()
        
        # Authenticate if needed
        if not await self.client.is_user_authorized():
            await self._authenticate()
        
        # Verify connection
        me = await self.client.get_me()
        console.print(f"[green]✓ Connected as: {me.first_name} (@{me.username or 'no username'})[/green]")
        
    async def _authenticate(self):
        """Handle user authentication"""
        console.print("\n[yellow]⚠ Authentication required[/yellow]")
        console.print("[dim]Get your credentials from https://my.telegram.org/apps[/dim]\n")
        
        phone = Prompt.ask("[cyan]Enter your phone number (with country code)")
        
        await self.client.send_code_request(phone)
        code = Prompt.ask("[cyan]Enter the code you received")
        
        try:
            await self.client.sign_in(phone, code)
        except Exception as e:
            # Might need 2FA
            if 'password' in str(e).lower() or 'SessionPasswordNeededError' in str(type(e).__name__):
                password = Prompt.ask("[cyan]2FA enabled. Enter your password", password=True)
                await self.client.sign_in(password=password)
            else:
                raise
        
        console.print("[green]✓ Authentication successful![/green]\n")
        console.print("[yellow]Session saved. You won't need to login again.[/yellow]")
    
    def _build_proxy(self, proxy_url: str):
        """Build Telethon proxy tuple from URL."""
        parsed = urlparse(proxy_url)
        scheme = (parsed.scheme or '').lower()
        host = parsed.hostname
        port = parsed.port

        if not host or not port:
            raise ValueError(
                f"Invalid proxy URL '{proxy_url}'. Expected scheme://host:port"
            )

        try:
            import socks
        except Exception as exc:
            raise ValueError(
                "Proxy support requires PySocks. Install with: pip install pysocks"
            ) from exc

        scheme_map = {
            'socks5': socks.SOCKS5,
            'socks5h': socks.SOCKS5,
            'http': socks.HTTP,
            'https': socks.HTTP,
        }
        if scheme not in scheme_map:
            raise ValueError(
                f"Unsupported proxy protocol '{scheme}'. Use socks5:// or http://"
            )

        # Telethon/PySocks proxy tuple format.
        return (
            scheme_map[scheme],
            host,
            port,
            True,  # rdns
            parsed.username,
            parsed.password,
        )
    
    async def disconnect(self):
        """Disconnect client gracefully"""
        if self.client:
            await self.client.disconnect()
            console.print("[dim]Disconnected from Telegram[/dim]")
