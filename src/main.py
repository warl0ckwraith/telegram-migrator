#!/usr/bin/env python3
"""
Telegram Migrator
A robust CLI tool for archiving Telegram channels/chats with full media support
"""

import asyncio
import sys
import signal
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
from rich.prompt import Confirm
from rich.table import Table

from config import Config
from client_manager import ClientManager
from channel_resolver import ChannelResolver
from size_estimator import SizeEstimator
from download_engine import DownloadEngine
from transfer_engine import TransferEngine
from state_manager import StateManager
from progress_tracker import ProgressTracker
from utils import format_size, format_duration, check_disk_space


console = Console()


class TelegramMigrator:
    """Main application controller"""
    
    def __init__(self):
        self.config = None
        self.client_manager = None
        self.state_manager = None
        self.running = True
        self.current_task = None
        self.main_task = None
        self.interrupt_count = 0
        
    def setup_signal_handlers(self):
        """Setup graceful shutdown on Ctrl+C"""
        def signal_handler(signum, frame):
            self.interrupt_count += 1

            if self.interrupt_count > 1:
                console.print("\n[red]⚠ Forced exit requested.[/red]")
                raise KeyboardInterrupt

            console.print("\n[yellow]⚠ Interrupt received. Shutting down gracefully...[/yellow]")
            self.running = False

            tasks_to_cancel = {
                task for task in (self.main_task, self.current_task)
                if task and not task.done()
            }

            for task in tasks_to_cancel:
                task.cancel()
                
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def run(self, args):
        """Main execution flow"""
        try:
            self.main_task = asyncio.current_task()
            self.setup_signal_handlers()
            
            # Initialize config
            self.config = Config.from_args(args)
            
            # Show welcome banner
            self.show_banner()
            
            # Initialize client
            console.print("[cyan]🔐 Initializing Telegram client...[/cyan]")
            self.client_manager = ClientManager(self.config)
            await self.client_manager.connect()
            
            # Resolve target channel/chat
            resolver = ChannelResolver(
                self.client_manager.client,
                prompt_for_invite_join=(self.config.operation == 'dump')
            )
            output_dir = Path(self.config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            if self.config.operation == 'transfer':
                console.print(f"[cyan]🔍 Resolving source: {self.config.target}[/cyan]")
                source_entity, source_type = await resolver.resolve_spec(
                    self.config.target,
                    allow_join_invites=False
                )
                self.show_entity_info(source_entity, source_type, label="Source")

                console.print(f"[cyan]🔍 Resolving destination: {self.config.dest_target}[/cyan]")
                dest_entity, dest_type = await resolver.resolve_spec(
                    self.config.dest_target,
                    allow_join_invites=False
                )
                self.show_entity_info(dest_entity, dest_type, label="Destination")

                console.print("\n[bold green]🚀 Starting transfer...[/bold green]\n")
                transfer_engine = TransferEngine(
                    client=self.client_manager.client,
                    config=self.config,
                    console=console,
                )
                self.current_task = asyncio.create_task(
                    transfer_engine.run(source_entity, dest_entity)
                )
                result = await self.current_task
                self.show_transfer_summary(result)
            else:
                console.print(f"[cyan]🔍 Resolving target: {self.config.target}[/cyan]")
                entity, entity_type = await resolver.resolve(self.config.target)
                
                # Show entity info
                self.show_entity_info(entity, entity_type)
                
                # Initialize state manager
                self.state_manager = StateManager(output_dir / ".dump_state.db")
                await self.state_manager.initialize()

                # Message selection mode
                if self.config.message_ids:
                    self.show_message_selection()
                else:
                    # Get message summary
                    console.print("[cyan]📊 Analyzing channel...[/cyan]")
                    summary = await self.get_channel_summary(entity)
                    self.show_summary(summary)
                    
                    # Size estimation
                    if not self.config.no_media and summary['total_messages'] > 0:
                        estimated_size = await self.estimate_size(entity, summary['total_messages'])
                        
                        # Check disk space
                        if not check_disk_space(output_dir, estimated_size):
                            console.print("[red]❌ Insufficient disk space![/red]")
                            return 1
                        
                        # Show size and confirm
                        if not self.confirm_download(estimated_size):
                            console.print("[yellow]Operation cancelled by user.[/yellow]")
                            return 0
                
                # Start download
                console.print("\n[bold green]🚀 Starting dump...[/bold green]\n")
                
                progress_tracker = ProgressTracker(console)
                download_engine = DownloadEngine(
                    client=self.client_manager.client,
                    state_manager=self.state_manager,
                    config=self.config,
                    progress_tracker=progress_tracker
                )
                
                self.current_task = asyncio.create_task(
                    download_engine.dump_channel(entity)
                )
                
                result = await self.current_task
                
                # Show final summary
                self.show_final_summary(result)
            
            return 0
            
        except asyncio.CancelledError:
            console.print("[yellow]Operation cancelled. Progress has been saved.[/yellow]")
            return 130  # Standard exit code for Ctrl+C
            
        except Exception as e:
            console.print(f"[red]❌ Fatal error: {e}[/red]")
            if self.config and self.config.debug:
                console.print_exception()
            return 1
            
        finally:
            # Cleanup
            if self.state_manager:
                await self.state_manager.close()
            if self.client_manager:
                await self.client_manager.disconnect()
            self.main_task = None
    
    def show_banner(self):
        """Display welcome banner"""
        banner_text = Text.assemble(
            ("TELEGRAM MIGRATOR", "bold cyan"),
            "\n",
            ("Archive and transfer chats/channels safely", "white"),
            "\n",
            ("Commands: dump | transfer", "dim"),
        )
        console.print(
            Panel(
                Align.center(banner_text),
                border_style="cyan",
                padding=(1, 3),
                expand=False,
            )
        )

    def show_entity_info(self, entity, entity_type, label: str = "Target"):
        """Display information about the resolved entity"""
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="white")
        
        table.add_row("Type", entity_type)
        table.add_row("Title", getattr(entity, 'title', getattr(entity, 'first_name', 'Unknown')))
        
        if hasattr(entity, 'username') and entity.username:
            table.add_row("Username", f"@{entity.username}")
        
        table.add_row("ID", str(entity.id))
        
        if hasattr(entity, 'participants_count'):
            table.add_row("Members", str(entity.participants_count))
        
        console.print(Panel(table, title=f"[bold]{label} Info[/bold]", border_style="green"))
    
    async def get_channel_summary(self, entity):
        """Get quick channel summary"""
        client = self.client_manager.client
        
        # Get total message count (fast)
        try:
            # Try to get accurate count
            messages = await client.get_messages(entity, limit=1)
            if messages:
                total = messages.total or 0
            else:
                total = 0
        except Exception as e:
            console.print(f"[yellow]Warning: Could not get message count: {e}[/yellow]")
            total = 0
        
        # Sample for date range (first and last message)
        first_msg = None
        last_msg = None
        
        if total > 0:
            try:
                # Get first message
                async for msg in client.iter_messages(entity, limit=1, reverse=True):
                    first_msg = msg
                    break
                
                # Get last message
                async for msg in client.iter_messages(entity, limit=1):
                    last_msg = msg
                    break
            except Exception:
                pass
        
        return {
            'total_messages': total,
            'first_message_date': first_msg.date if first_msg else None,
            'last_message_date': last_msg.date if last_msg else None
        }
    
    def show_summary(self, summary):
        """Display channel summary"""
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="white")
        
        table.add_row("Total Messages", str(summary['total_messages']))
        
        if summary['first_message_date']:
            table.add_row("First Message", summary['first_message_date'].strftime('%Y-%m-%d %H:%M:%S'))
        
        if summary['last_message_date']:
            table.add_row("Last Message", summary['last_message_date'].strftime('%Y-%m-%d %H:%M:%S'))
        
        if summary['first_message_date'] and summary['last_message_date']:
            duration = summary['last_message_date'] - summary['first_message_date']
            table.add_row("Duration", f"{duration.days} days")
        
        console.print(Panel(table, title="[bold]Channel Summary[/bold]", border_style="blue"))

    def show_message_selection(self):
        """Display selected message IDs when filtering by explicit IDs/links."""
        selected_count = len(self.config.message_ids)
        sample = ", ".join(str(mid) for mid in self.config.message_ids[:10])
        if selected_count > 10:
            sample += ", ..."

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Mode", "Specific messages")
        table.add_row("Selected IDs", str(selected_count))
        table.add_row("IDs (sample)", sample or "None")

        console.print(Panel(table, title="[bold]Message Selection[/bold]", border_style="magenta"))
    
    async def estimate_size(self, entity, total_messages):
        """Estimate download size"""
        estimator = SizeEstimator(
            client=self.client_manager.client,
            media_types=self.config.media_types
        )
        
        with console.status("[cyan]Estimating download size...[/cyan]"):
            if self.config.accurate_size:
                size = await estimator.estimate_accurate(entity, total_messages)
            else:
                size = await estimator.estimate_fast(entity, total_messages)
        
        console.print(f"[green]📦 Estimated size: {format_size(size)}[/green]")
        return size
    
    def confirm_download(self, estimated_size):
        """Ask user to confirm download"""
        console.print(f"\n[bold]Download will save to:[/bold] {self.config.output_dir}")
        console.print(f"[bold]Estimated size:[/bold] {format_size(estimated_size)}")
        console.print(f"[bold]Media types:[/bold] {', '.join(self.config.media_types)}")
        
        return Confirm.ask("\n[yellow]Continue with download?[/yellow]", default=True)
    
    def show_final_summary(self, result):
        """Display final summary after download"""
        console.print("\n")
        
        table = Table(title="[bold green]✓ Download Complete[/bold green]", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Count", style="white")
        
        table.add_row("Messages Saved", str(result['messages_saved']))
        table.add_row("Files Downloaded", str(result['files_downloaded']))
        table.add_row("Files Skipped", str(result['files_skipped']))
        table.add_row("Failed Downloads", str(result['files_failed']))
        table.add_row("Total Size", format_size(result['total_bytes']))
        table.add_row("Time Elapsed", format_duration(result['elapsed_time']))
        
        console.print(table)
        
        if result['files_failed'] > 0:
            console.print(f"\n[yellow]⚠ {result['files_failed']} files failed to download.[/yellow]")
            console.print("[yellow]You can retry with --retry-failed flag.[/yellow]")
        
        console.print(f"\n[green]📁 Output saved to: {self.config.output_dir}[/green]")

    def show_transfer_summary(self, result):
        """Display summary for source->destination transfer jobs."""
        table = Table(title="[bold green]✓ Transfer Complete[/bold green]", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("Mode", result.get('mode', 'forward'))
        table.add_row("Source", str(result.get('source')))
        table.add_row("Destination", str(result.get('dest')))
        table.add_row("Last Message ID", str(result.get('last_id')))
        table.add_row("Messages Sent", str(result.get('forwarded_count')))
        table.add_row("Messages Skipped", str(result.get('skipped_count')))
        table.add_row("FloodWait Total", format_duration(result.get('flood_seconds', 0)))
        table.add_row("Time Elapsed", format_duration(result.get('elapsed_time', 0)))
        table.add_row("State DB", str(result.get('db_path')))

        console.print(table)
        console.print(f"\n[green]📁 Output saved to: {self.config.output_dir}[/green]")


def _add_shared_runtime_args(parser):
    """Add auth/runtime args shared by dump and transfer."""
    parser.add_argument('--session', default=None,
                        help='Session file path (default: ~/.telegram-migrator.session)')
    parser.add_argument('--api-id', type=int,
                        help='Telegram API ID (or set TG_API_ID env)')
    parser.add_argument('--api-hash',
                        help='Telegram API hash (or set TG_API_HASH env)')
    parser.add_argument('--proxy',
                        help='Proxy URL (socks5://user:pass@host:port or http://host:port)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode')


def create_parser():
    """Create subcommand-based argument parser."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="telegram-migrator",
        description="Telegram Migrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s dump -c @mychannel -o ./backup
  %(prog)s dump -o ./backup --select "link:https://t.me/c/123456789/42"
  %(prog)s transfer -c "title:Source Chat" --dest "title:Archive Chat" -o ./transfer
        """
    )

    subparsers = parser.add_subparsers(dest='command', required=True)

    dump_parser = subparsers.add_parser(
        'dump',
        help='Archive messages/media from a chat or channel'
    )
    _add_shared_runtime_args(dump_parser)
    dump_parser.add_argument('-c', '--channel', required=False,
                             help='Source chat/channel (@username, title, t.me link, ID, or direct message link)')
    dump_parser.add_argument('-o', '--output', required=True,
                             help='Output directory for dump')
    dump_parser.add_argument('--media-types', default='all',
                             help='Media types: photos,videos,documents,audio,voice,video_notes,all')
    dump_parser.add_argument('--no-media', action='store_true',
                             help='Skip media download, save messages only')
    dump_parser.add_argument('--limit', type=int,
                             help='Maximum messages to process')
    dump_parser.add_argument('--date-from',
                             help='Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)')
    dump_parser.add_argument('--date-to',
                             help='End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)')
    dump_parser.add_argument('--skip-service-messages', action='store_true',
                             help='Skip joins/leaves and other service messages')
    dump_parser.add_argument(
        '--select', action='append',
        help=(
            "Message selector (repeatable): "
            "id:<n>, range:<start-end>, link:<t.me/...>, "
            "or direct numeric/link values"
        )
    )
    dump_parser.add_argument('--accurate-size', action='store_true',
                             help='Full scan for size estimate (slower)')
    dump_parser.add_argument('--retry-failed', action='store_true',
                             help='Retry previously failed media downloads')

    transfer_parser = subparsers.add_parser(
        'transfer',
        help='Transfer messages from source to destination'
    )
    _add_shared_runtime_args(transfer_parser)
    transfer_parser.add_argument('-c', '--channel', required=True,
                                 help='Source spec (@username, title:..., invite:..., id:..., or direct message link)')
    transfer_parser.add_argument('--dest', required=True,
                                 help='Destination spec (@username, title:..., invite:..., id:...)')
    transfer_parser.add_argument('-o', '--output', required=True,
                                 help='Output directory for transfer artifacts/state')
    transfer_parser.add_argument('--mode', choices=['forward', 'copy'], default='forward',
                                 help='Transfer mode')
    transfer_parser.add_argument('--profile', choices=['safe', 'balanced', 'fast'], default='balanced',
                                 help='Transfer pacing profile')
    transfer_parser.add_argument('--skip-known-bad', action='store_true',
                                 help='Skip message IDs already marked failed in transfer DB')
    transfer_parser.add_argument('--db',
                                 help='Transfer state DB path (default: <output>/.transfer_state.sqlite3)')

    return parser


def parse_cli_args(argv=None):
    """Parse CLI arguments."""
    argv = list(argv if argv is not None else sys.argv[1:])
    return create_parser().parse_args(argv)


async def main():
    """Entry point"""
    args = parse_cli_args()
    
    migrator = TelegramMigrator()
    exit_code = await migrator.run(args)
    sys.exit(exit_code)


def run():
    """Synchronous CLI entrypoint."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)


if __name__ == '__main__':
    run()
