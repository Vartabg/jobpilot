"""
Action Recorder - Log user actions to learn patterns

Records:
- What was auto-filled vs manually typed
- Time spent on each field (hesitation = uncertainty)
- Which applications were abandoned
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict
from enum import Enum
from rich.console import Console

from jobpilot.core.config import DATA_DIR
from jobpilot.learning.learning_db import get_learning_db

console = Console()


class ActionType(Enum):
    """Types of recorded actions"""
    FIELD_APPROVED = "field_approved"      # User approved our suggestion
    FIELD_EDITED = "field_edited"          # User edited our suggestion
    FIELD_SKIPPED = "field_skipped"        # User skipped (manual entry)
    FIELD_MANUAL = "field_manual"          # User typed without suggestion
    APPLICATION_STARTED = "app_started"
    APPLICATION_SUBMITTED = "app_submitted"
    APPLICATION_ABANDONED = "app_abandoned"


@dataclass
class RecordedAction:
    """A single recorded action"""
    timestamp: str
    action_type: str
    job_url: Optional[str] = None
    job_title: Optional[str] = None
    company: Optional[str] = None
    field_label: Optional[str] = None
    field_type: Optional[str] = None
    suggested_value: Optional[str] = None
    final_value: Optional[str] = None
    confidence: Optional[float] = None
    time_spent_ms: Optional[int] = None  # Time spent on this field
    step_number: Optional[int] = None


class ActionRecorder:
    """
    Records user actions during job applications.
    
    Data is stored in JSONL format (one JSON object per line) for easy appending.
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db = get_learning_db(self.data_dir / "learning.db")
        
        # Current application context
        self._current_job_url: Optional[str] = None
        self._current_job_title: Optional[str] = None
        self._current_company: Optional[str] = None
        self._field_start_time: Optional[datetime] = None
    
    def set_context(self, job_url: str, job_title: str = "", company: str = ""):
        """Set the current job context for all following actions"""
        self._current_job_url = job_url
        self._current_job_title = job_title
        self._current_company = company
    
    def start_field_timer(self):
        """Start timing how long user spends on a field"""
        self._field_start_time = datetime.now()
    
    def _get_field_time(self) -> Optional[int]:
        """Get milliseconds spent on current field"""
        if self._field_start_time:
            delta = datetime.now() - self._field_start_time
            self._field_start_time = None
            return int(delta.total_seconds() * 1000)
        return None
    
    def record(self, action: RecordedAction):
        """Record an action to the database"""
        # Add context if not already set
        if not action.job_url:
            action.job_url = self._current_job_url
        if not action.job_title:
            action.job_title = self._current_job_title
        if not action.company:
            action.company = self._current_company
        
        # Add timestamp if not set
        if not action.timestamp:
            action.timestamp = datetime.now().isoformat()
        
        # Add time spent if we were timing
        if action.time_spent_ms is None:
            action.time_spent_ms = self._get_field_time()
        
        # Write to SQLite
        d = asdict(action)
        self._db.record_action(
            action_type=d["action_type"],
            timestamp=d["timestamp"],
            job_url=d.get("job_url"),
            job_title=d.get("job_title"),
            company=d.get("company"),
            field_label=d.get("field_label"),
            field_type=d.get("field_type"),
            suggested_value=d.get("suggested_value"),
            final_value=d.get("final_value"),
            confidence=d.get("confidence"),
            time_spent_ms=d.get("time_spent_ms"),
            step_number=d.get("step_number"),
        )
    
    def record_field_approved(
        self, 
        field_label: str,
        field_type: str,
        suggested_value: str,
        confidence: float,
        step_number: int = 1
    ):
        """Record that user approved our suggestion"""
        self.record(RecordedAction(
            timestamp=datetime.now().isoformat(),
            action_type=ActionType.FIELD_APPROVED.value,
            field_label=field_label,
            field_type=field_type,
            suggested_value=suggested_value,
            final_value=suggested_value,
            confidence=confidence,
            step_number=step_number,
        ))
    
    def record_field_edited(
        self,
        field_label: str,
        field_type: str,
        suggested_value: str,
        final_value: str,
        confidence: float,
        step_number: int = 1
    ):
        """Record that user edited our suggestion"""
        self.record(RecordedAction(
            timestamp=datetime.now().isoformat(),
            action_type=ActionType.FIELD_EDITED.value,
            field_label=field_label,
            field_type=field_type,
            suggested_value=suggested_value,
            final_value=final_value,
            confidence=confidence,
            step_number=step_number,
        ))
    
    def record_field_skipped(
        self,
        field_label: str,
        field_type: str,
        step_number: int = 1
    ):
        """Record that user skipped a field (no suggestion used)"""
        self.record(RecordedAction(
            timestamp=datetime.now().isoformat(),
            action_type=ActionType.FIELD_SKIPPED.value,
            field_label=field_label,
            field_type=field_type,
            step_number=step_number,
        ))
    
    def record_application_started(self, job_url: str, job_title: str = "", company: str = ""):
        """Record that user started a new application"""
        self.set_context(job_url, job_title, company)
        self.record(RecordedAction(
            timestamp=datetime.now().isoformat(),
            action_type=ActionType.APPLICATION_STARTED.value,
            job_url=job_url,
            job_title=job_title,
            company=company,
        ))
    
    def record_application_submitted(self):
        """Record that user submitted the application"""
        self.record(RecordedAction(
            timestamp=datetime.now().isoformat(),
            action_type=ActionType.APPLICATION_SUBMITTED.value,
        ))
    
    def record_application_abandoned(self, step_number: int = 1):
        """Record that user abandoned the application"""
        self.record(RecordedAction(
            timestamp=datetime.now().isoformat(),
            action_type=ActionType.APPLICATION_ABANDONED.value,
            step_number=step_number,
        ))
    
    def get_stats(self) -> dict:
        """Get statistics from recorded actions"""
        return self._db.get_stats()
    
    def display_stats(self):
        """Display statistics in a nice format"""
        from rich.table import Table
        
        stats = self.get_stats()
        
        table = Table(title="Application Statistics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        
        table.add_row("Total Applications", str(stats["total_applications"]))
        table.add_row("Submitted", f"[green]{stats['submitted']}[/green]")
        table.add_row("Abandoned", f"[yellow]{stats['abandoned']}[/yellow]")
        table.add_row("", "")
        table.add_row("Fields Approved", str(stats["fields_approved"]))
        table.add_row("Fields Edited", str(stats["fields_edited"]))
        table.add_row("Fields Skipped", str(stats["fields_skipped"]))
        table.add_row("Approval Rate", f"{stats['approval_rate']:.1%}")
        
        console.print(table)


# Global instance
_recorder: Optional[ActionRecorder] = None

def get_action_recorder() -> ActionRecorder:
    """Get the global action recorder instance"""
    global _recorder
    if _recorder is None:
        _recorder = ActionRecorder()
    return _recorder
