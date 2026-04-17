"""
Profile Store - Manage user profile data for auto-filling

Stores personal information locally in JSON format.
All data stays on your machine - nothing is sent externally.
"""

import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from jobpilot.core.config import DATA_DIR
from jobpilot.core.logger import get_logger

console = Console()
log = get_logger(__name__)


class UserProfile(BaseModel):
    """User profile data for job applications"""
    
    # Basic Info
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    
    # Location
    city: str = ""
    state: str = ""
    country: str = "United States"
    zip_code: str = ""
    
    # Professional
    linkedin_url: str = ""
    portfolio_url: str = ""
    github_url: str = ""
    
    # Resume & Cover Letter
    resume_path: str = ""
    cover_letter_path: str = ""
    
    # Work Authorization
    authorized_to_work: bool = True
    requires_sponsorship: bool = False
    
    # Experience (for common questions)
    years_of_experience: int = 0
    current_title: str = ""
    current_company: str = ""
    
    # Salary (optional)
    desired_salary: str = ""
    
    # Common Answers (for frequently asked questions)
    # Key: question pattern, Value: your answer
    custom_answers: dict[str, str] = Field(default_factory=dict)


class ProfileStore:
    """Manages loading/saving of user profile data"""
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_path = self.data_dir / "profile.json"
        self._profile: Optional[UserProfile] = None
    
    def load(self) -> UserProfile:
        """Load profile from disk, or create empty one"""
        if self._profile:
            return self._profile
            
        if self.profile_path.exists():
            try:
                data = json.loads(self.profile_path.read_text())
                self._profile = UserProfile(**data)
            except Exception as e:
                log.warning("Could not load profile: %s", e)
                self._profile = UserProfile()
        else:
            self._profile = UserProfile()
            
        return self._profile
    
    def save(self, profile: Optional[UserProfile] = None):
        """Save profile to disk"""
        if profile:
            self._profile = profile
        if self._profile:
            self.profile_path.write_text(
                self._profile.model_dump_json(indent=2)
            )
            log.info("Profile saved to %s", self.profile_path)
    
    def update(self, **kwargs):
        """Update specific profile fields"""
        profile = self.load()
        for key, value in kwargs.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        self.save(profile)
    
    def display(self):
        """Display profile in a nice table"""
        profile = self.load()
        
        table = Table(title="Your Profile", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")
        
        # Basic Info
        table.add_row("Name", f"{profile.first_name} {profile.last_name}")
        table.add_row("Email", profile.email or "[dim]not set[/dim]")
        table.add_row("Phone", profile.phone or "[dim]not set[/dim]")
        
        # Location
        location = ", ".join(filter(None, [profile.city, profile.state, profile.country]))
        table.add_row("Location", location or "[dim]not set[/dim]")
        
        # Professional
        table.add_row("Current Title", profile.current_title or "[dim]not set[/dim]")
        table.add_row("Experience", f"{profile.years_of_experience} years" if profile.years_of_experience else "[dim]not set[/dim]")
        
        # Resume
        table.add_row("Resume", profile.resume_path or "[dim]not set[/dim]")
        
        # URLs
        table.add_row("LinkedIn", profile.linkedin_url or "[dim]not set[/dim]")
        table.add_row("Portfolio", profile.portfolio_url or "[dim]not set[/dim]")
        table.add_row("GitHub", profile.github_url or "[dim]not set[/dim]")
        
        # Work Auth
        auth_status = "✓ Authorized" if profile.authorized_to_work else "✗ Not Authorized"
        if profile.requires_sponsorship:
            auth_status += " (needs sponsorship)"
        table.add_row("Work Authorization", auth_status)
        
        # Custom Answers
        if profile.custom_answers:
            table.add_row("Custom Answers", f"{len(profile.custom_answers)} saved")
        
        console.print(table)
    
    def get_field_value(self, field_type: str) -> Optional[str]:
        """Get the appropriate profile value for a detected field type"""
        profile = self.load()
        
        field_mapping = {
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "full_name": f"{profile.first_name} {profile.last_name}".strip(),
            "email": profile.email,
            "phone": profile.phone,
            "city": profile.city,
            "state": profile.state,
            "zip": profile.zip_code,
            "country": profile.country,
            "linkedin": profile.linkedin_url,
            "portfolio": profile.portfolio_url,
            "website": profile.portfolio_url,
            "github": profile.github_url,
            "years_experience": str(profile.years_of_experience) if profile.years_of_experience else "",
            "current_title": profile.current_title,
            "current_company": profile.current_company,
            "salary": profile.desired_salary,
        }
        
        return field_mapping.get(field_type)


# Global instance for convenience
_store: Optional[ProfileStore] = None

def get_profile_store() -> ProfileStore:
    """Get the global profile store instance"""
    global _store
    if _store is None:
        _store = ProfileStore()
    return _store
