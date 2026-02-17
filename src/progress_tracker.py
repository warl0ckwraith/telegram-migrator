"""Progress tracking with Rich UI"""

import time
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
)
from rich.table import Table
from typing import Optional


class ProgressTracker:
    """Tracks and displays download progress"""
    
    def __init__(self, console):
        self.console = console
        self.start_time = time.time()
        
        # Counters
        self.messages_processed = 0
        self.messages_total = 0
        self.files_queued = 0
        self.files_downloaded = 0
        self.files_skipped = 0
        self.files_failed = 0
        self.bytes_downloaded = 0
        
        # Current state
        self.current_file = None
        self.current_file_size = 0
        self.flood_wait_seconds = 0
        
        # Progress bars
        self.progress = None
        self.message_task = None
        self.download_task = None
        
    def start(self, total_messages: Optional[int] = None):
        """Start progress tracking"""
        self.messages_total = total_messages or 0
        
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=self.console
        )
        
        self.progress.start()
        
        # Add tasks
        self.message_task = self.progress.add_task(
            "[cyan]Scanning messages...",
            total=total_messages if total_messages else None
        )
        
        self.download_task = self.progress.add_task(
            "[green]Downloading media...",
            total=None,
            visible=False
        )
    
    def stop(self):
        """Stop progress tracking"""
        if self.progress:
            self.progress.stop()
    
    def update_message_progress(self, processed: int, total: Optional[int] = None):
        """Update message scanning progress"""
        self.messages_processed = processed
        if total:
            self.messages_total = total
            self.progress.update(
                self.message_task,
                completed=processed,
                total=total
            )
        else:
            self.progress.update(self.message_task, advance=1)
    
    def start_file_download(self, filename: str, size: int):
        """Start downloading a file"""
        self.current_file = filename
        self.current_file_size = size
        self.files_queued += 1
        
        self.progress.update(
            self.download_task,
            description=f"[green]⬇ {filename}",
            completed=0,
            total=size,
            visible=True
        )
    
    def update_file_progress(self, bytes_downloaded: int):
        """Update file download progress"""
        self.progress.update(
            self.download_task,
            completed=bytes_downloaded
        )
    
    def complete_file_download(self, success: bool = True, error: str = None):
        """Mark file download as complete"""
        if success:
            self.files_downloaded += 1
            self.bytes_downloaded += self.current_file_size
        else:
            self.files_failed += 1
            if error:
                self.console.print(f"[red]✗ Failed: {self.current_file} - {error}[/red]")
        
        self.current_file = None
        self.current_file_size = 0
        
        self.progress.update(self.download_task, visible=False)
    
    def skip_file(self):
        """Mark file as skipped (already downloaded)"""
        self.files_skipped += 1
        self.bytes_downloaded += self.current_file_size
        self.current_file = None
        self.current_file_size = 0
    
    def set_flood_wait(self, seconds: int):
        """Set FloodWait status"""
        self.flood_wait_seconds = seconds
        if seconds > 0:
            self.console.print(f"[yellow]⏸ FloodWait: Sleeping for {seconds} seconds...[/yellow]")
    
    def clear_flood_wait(self):
        """Clear FloodWait status"""
        self.flood_wait_seconds = 0
    
    def get_statistics(self) -> dict:
        """Get current statistics"""
        elapsed = time.time() - self.start_time
        
        return {
            'messages_processed': self.messages_processed,
            'messages_total': self.messages_total,
            'files_downloaded': self.files_downloaded,
            'files_skipped': self.files_skipped,
            'files_failed': self.files_failed,
            'bytes_downloaded': self.bytes_downloaded,
            'elapsed_time': elapsed,
            'download_speed': self.bytes_downloaded / elapsed if elapsed > 0 else 0
        }
    
    def show_summary_table(self):
        """Show final summary table"""
        stats = self.get_statistics()
        
        table = Table(title="Download Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        
        table.add_row("Messages Processed", str(stats['messages_processed']))
        table.add_row("Files Downloaded", str(stats['files_downloaded']))
        table.add_row("Files Skipped", str(stats['files_skipped']))
        table.add_row("Files Failed", str(stats['files_failed']))
        
        from utils import format_size, format_duration
        table.add_row("Total Downloaded", format_size(stats['bytes_downloaded']))
        table.add_row("Time Elapsed", format_duration(stats['elapsed_time']))
        
        if stats['elapsed_time'] > 0:
            speed = stats['bytes_downloaded'] / stats['elapsed_time']
            table.add_row("Avg Speed", f"{format_size(speed)}/s")
        
        self.console.print(table)
