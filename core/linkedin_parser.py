"""
LinkedIn Parser - Extract form fields from LinkedIn Easy Apply

Detects form fields, their types, and labels to enable intelligent auto-fill.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from playwright.async_api import Page, ElementHandle
from rich.console import Console

console = Console()


class FieldType(Enum):
    """Types of form fields we can detect"""
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    NUMBER = "number"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    TEXTAREA = "textarea"
    FILE = "file"  # Resume upload
    UNKNOWN = "unknown"


class SemanticType(Enum):
    """Semantic meaning of a field (what data it wants)"""
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    FULL_NAME = "full_name"
    EMAIL = "email"
    PHONE = "phone"
    CITY = "city"
    STATE = "state"
    ZIP = "zip"
    COUNTRY = "country"
    LINKEDIN_URL = "linkedin"
    PORTFOLIO_URL = "portfolio"
    GITHUB_URL = "github"
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    YEARS_EXPERIENCE = "years_experience"
    CURRENT_TITLE = "current_title"
    CURRENT_COMPANY = "current_company"
    SALARY = "salary"
    START_DATE = "start_date"
    WORK_AUTHORIZATION = "work_authorization"
    SPONSORSHIP = "sponsorship"
    CUSTOM_QUESTION = "custom_question"
    UNKNOWN = "unknown"


@dataclass
class FormField:
    """Represents a single form field"""
    element: ElementHandle
    field_type: FieldType
    semantic_type: SemanticType
    label: str
    placeholder: str = ""
    is_required: bool = False
    current_value: str = ""
    options: list[str] = field(default_factory=list)  # For select/radio
    confidence: float = 0.0  # How confident we are in the semantic type
    
    def __repr__(self):
        req = "*" if self.is_required else ""
        return f"FormField({self.semantic_type.value}{req}: '{self.label}')"


@dataclass  
class ApplicationPage:
    """Represents the current state of a LinkedIn Easy Apply page"""
    current_step: int = 1
    total_steps: int = 1
    fields: list[FormField] = field(default_factory=list)
    has_resume_upload: bool = False
    has_cover_letter: bool = False
    submit_button_text: str = ""


# Patterns to detect semantic field types from labels
SEMANTIC_PATTERNS: dict[SemanticType, list[str]] = {
    SemanticType.FIRST_NAME: [r"first\s*name", r"given\s*name"],
    SemanticType.LAST_NAME: [r"last\s*name", r"family\s*name", r"surname"],
    SemanticType.FULL_NAME: [r"^name$", r"full\s*name", r"your\s*name"],
    SemanticType.EMAIL: [r"email", r"e-mail"],
    SemanticType.PHONE: [r"phone", r"mobile", r"cell", r"telephone"],
    SemanticType.CITY: [r"\bcity\b", r"city/town"],
    SemanticType.STATE: [r"\bstate\b", r"province", r"region"],
    SemanticType.ZIP: [r"zip", r"postal", r"postcode"],
    SemanticType.COUNTRY: [r"country"],
    SemanticType.LINKEDIN_URL: [r"linkedin.*url", r"linkedin.*profile"],
    SemanticType.PORTFOLIO_URL: [r"portfolio", r"personal.*website", r"website.*url"],
    SemanticType.GITHUB_URL: [r"github"],
    SemanticType.RESUME: [r"resume", r"cv", r"curriculum"],
    SemanticType.COVER_LETTER: [r"cover\s*letter"],
    SemanticType.YEARS_EXPERIENCE: [r"years.*experience", r"experience.*years", r"how.*many.*years"],
    SemanticType.CURRENT_TITLE: [r"current.*title", r"job.*title", r"position"],
    SemanticType.CURRENT_COMPANY: [r"current.*company", r"employer", r"company.*name"],
    SemanticType.SALARY: [r"salary", r"compensation", r"pay.*expect"],
    SemanticType.START_DATE: [r"start.*date", r"when.*can.*you.*start", r"availability"],
    SemanticType.WORK_AUTHORIZATION: [r"authorized.*work", r"legally.*authorized", r"work.*authorization", r"eligible.*to.*work"],
    SemanticType.SPONSORSHIP: [r"sponsorship", r"visa.*sponsor", r"require.*sponsor"],
}


class LinkedInParser:
    """
    Parses LinkedIn Easy Apply forms to extract field information.
    
    Works by:
    1. Detecting the Easy Apply modal
    2. Finding all form fields within it
    3. Inferring semantic types from labels and context
    """
    
    def __init__(self, page: Page):
        self.page = page
    
    async def is_easy_apply_open(self) -> bool:
        """Check if the Easy Apply modal is currently open"""
        modal = await self.page.query_selector('[data-test-modal-id="easy-apply-modal"]')
        return modal is not None
    
    async def parse_application(self) -> Optional[ApplicationPage]:
        """Parse the current Easy Apply form"""
        if not await self.is_easy_apply_open():
            return None
            
        app_page = ApplicationPage()
        
        # Get step info
        progress = await self.page.query_selector('.jobs-easy-apply-content progress')
        if progress:
            value = await progress.get_attribute("value")
            max_val = await progress.get_attribute("max")
            app_page.current_step = int(value) if value else 1
            app_page.total_steps = int(max_val) if max_val else 1
        
        # Find all form fields
        app_page.fields = await self._extract_fields()
        
        # Check for file uploads
        file_inputs = await self.page.query_selector_all('input[type="file"]')
        for input_el in file_inputs:
            label = await self._get_field_label(input_el)
            if "resume" in label.lower() or "cv" in label.lower():
                app_page.has_resume_upload = True
            elif "cover" in label.lower():
                app_page.has_cover_letter = True
        
        # Get submit button text
        submit_btn = await self.page.query_selector('[data-easy-apply-next-button]')
        if submit_btn:
            app_page.submit_button_text = await submit_btn.inner_text()
        
        return app_page
    
    async def _extract_fields(self) -> list[FormField]:
        """Extract all form fields from the modal"""
        fields = []
        
        # Text inputs
        text_inputs = await self.page.query_selector_all(
            '.jobs-easy-apply-modal input[type="text"], '
            '.jobs-easy-apply-modal input[type="email"], '
            '.jobs-easy-apply-modal input[type="tel"], '
            '.jobs-easy-apply-modal input[type="number"], '
            '.jobs-easy-apply-modal input:not([type])'
        )
        
        for input_el in text_inputs:
            field = await self._parse_input_field(input_el)
            if field:
                fields.append(field)
        
        # Textareas
        textareas = await self.page.query_selector_all('.jobs-easy-apply-modal textarea')
        for textarea in textareas:
            field = await self._parse_textarea_field(textarea)
            if field:
                fields.append(field)
        
        # Selects
        selects = await self.page.query_selector_all('.jobs-easy-apply-modal select')
        for select in selects:
            field = await self._parse_select_field(select)
            if field:
                fields.append(field)
        
        # Radio groups
        radio_groups = await self._find_radio_groups()
        fields.extend(radio_groups)
        
        return fields
    
    async def _parse_input_field(self, element: ElementHandle) -> Optional[FormField]:
        """Parse a text/email/phone input field"""
        input_type = await element.get_attribute("type") or "text"
        
        # Map HTML type to our FieldType
        type_map = {
            "text": FieldType.TEXT,
            "email": FieldType.EMAIL,
            "tel": FieldType.PHONE,
            "number": FieldType.NUMBER,
        }
        field_type = type_map.get(input_type, FieldType.TEXT)
        
        label = await self._get_field_label(element)
        placeholder = await element.get_attribute("placeholder") or ""
        required = (await element.get_attribute("required")) is not None
        current_value = await element.input_value()
        
        # Infer semantic type
        semantic_type, confidence = self._infer_semantic_type(
            label, placeholder, field_type
        )
        
        return FormField(
            element=element,
            field_type=field_type,
            semantic_type=semantic_type,
            label=label,
            placeholder=placeholder,
            is_required=required,
            current_value=current_value,
            confidence=confidence,
        )
    
    async def _parse_textarea_field(self, element: ElementHandle) -> Optional[FormField]:
        """Parse a textarea field (usually custom questions)"""
        label = await self._get_field_label(element)
        placeholder = await element.get_attribute("placeholder") or ""
        required = (await element.get_attribute("required")) is not None
        current_value = await element.input_value()
        
        semantic_type, confidence = self._infer_semantic_type(
            label, placeholder, FieldType.TEXTAREA
        )
        
        # Textareas are usually custom questions
        if semantic_type == SemanticType.UNKNOWN:
            semantic_type = SemanticType.CUSTOM_QUESTION
        
        return FormField(
            element=element,
            field_type=FieldType.TEXTAREA,
            semantic_type=semantic_type,
            label=label,
            placeholder=placeholder,
            is_required=required,
            current_value=current_value,
            confidence=confidence,
        )
    
    async def _parse_select_field(self, element: ElementHandle) -> Optional[FormField]:
        """Parse a select dropdown"""
        label = await self._get_field_label(element)
        required = (await element.get_attribute("required")) is not None
        
        # Get options
        options = []
        option_elements = await element.query_selector_all("option")
        for opt in option_elements:
            text = await opt.inner_text()
            if text.strip():
                options.append(text.strip())
        
        semantic_type, confidence = self._infer_semantic_type(
            label, "", FieldType.SELECT
        )
        
        return FormField(
            element=element,
            field_type=FieldType.SELECT,
            semantic_type=semantic_type,
            label=label,
            is_required=required,
            options=options,
            confidence=confidence,
        )
    
    async def _find_radio_groups(self) -> list[FormField]:
        """Find and parse radio button groups"""
        fields = []
        
        # Find fieldsets or divs containing radio buttons
        radio_containers = await self.page.query_selector_all(
            '.jobs-easy-apply-modal fieldset:has(input[type="radio"])'
        )
        
        for container in radio_containers:
            legend = await container.query_selector("legend")
            label = await legend.inner_text() if legend else ""
            
            # Get options
            options = []
            option_labels = await container.query_selector_all("label")
            for opt_label in option_labels:
                text = await opt_label.inner_text()
                if text.strip():
                    options.append(text.strip())
            
            semantic_type, confidence = self._infer_semantic_type(
                label, "", FieldType.RADIO
            )
            
            # Radio groups are often for yes/no questions
            if semantic_type == SemanticType.UNKNOWN and len(options) <= 3:
                if any("yes" in opt.lower() for opt in options):
                    # Could be work auth or sponsorship
                    if "author" in label.lower() or "eligible" in label.lower():
                        semantic_type = SemanticType.WORK_AUTHORIZATION
                    elif "sponsor" in label.lower():
                        semantic_type = SemanticType.SPONSORSHIP
            
            first_radio = await container.query_selector('input[type="radio"]')
            if first_radio:
                fields.append(FormField(
                    element=first_radio,
                    field_type=FieldType.RADIO,
                    semantic_type=semantic_type,
                    label=label,
                    options=options,
                    confidence=confidence,
                ))
        
        return fields
    
    async def _get_field_label(self, element: ElementHandle) -> str:
        """Get the label for a form field"""
        # Try aria-label first
        aria_label = await element.get_attribute("aria-label")
        if aria_label:
            return aria_label.strip()
        
        # Try associated label via id
        field_id = await element.get_attribute("id")
        if field_id:
            label_el = await self.page.query_selector(f'label[for="{field_id}"]')
            if label_el:
                return (await label_el.inner_text()).strip()
        
        # Try parent label
        parent_label = await element.evaluate(
            "el => el.closest('label')?.innerText || ''"
        )
        if parent_label:
            return parent_label.strip()
        
        # Try nearby legend or span
        nearby_text = await element.evaluate("""
            el => {
                const container = el.closest('.fb-form-element, .artdeco-text-input');
                if (container) {
                    const label = container.querySelector('label, .fb-form-element-label');
                    return label?.innerText || '';
                }
                return '';
            }
        """)
        
        return nearby_text.strip() if nearby_text else ""
    
    def _infer_semantic_type(
        self, 
        label: str, 
        placeholder: str, 
        field_type: FieldType
    ) -> tuple[SemanticType, float]:
        """Infer the semantic meaning of a field from its label and type.
        
        Patterns in SEMANTIC_PATTERNS are evaluated in order per type —
        the first matching pattern wins for that type. Place more specific
        (longer) patterns before generic ones to get higher confidence.
        """
        text_to_match = f"{label} {placeholder}".lower()
        
        best_match = SemanticType.UNKNOWN
        best_confidence = 0.0
        
        for semantic_type, patterns in SEMANTIC_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_to_match, re.IGNORECASE):
                    # Calculate confidence based on pattern specificity
                    confidence = 0.9 if len(pattern) > 10 else 0.7
                    if confidence > best_confidence:
                        best_match = semantic_type
                        best_confidence = confidence
                        break
        
        # Boost confidence for matching HTML types
        if field_type == FieldType.EMAIL and best_match == SemanticType.EMAIL:
            best_confidence = min(1.0, best_confidence + 0.1)
        elif field_type == FieldType.PHONE and best_match == SemanticType.PHONE:
            best_confidence = min(1.0, best_confidence + 0.1)
        
        return best_match, best_confidence
