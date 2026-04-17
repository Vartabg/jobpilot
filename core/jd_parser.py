"""
Job Description Parser — Extract structured data from LinkedIn JD pages.

Parses the job description visible on the listing page *before* the user clicks
Easy Apply, extracting title, company, requirements, nice-to-haves, salary
range, location type, and raw text.  The structured output feeds smarter
AI answer generation and cover letter tailoring.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from playwright.async_api import Page
from jobpilot.core.selector_registry import JD_CONTAINER, JOB_TITLE, COMPANY_NAME
from jobpilot.core.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Keyword lists for lightweight extraction
# ---------------------------------------------------------------------------

_REQUIREMENT_HEADERS = re.compile(
    r"(requirements?|qualifications?|must.have|what you.ll need|"
    r"minimum|basic|essential|who you are)",
    re.IGNORECASE,
)

_NICE_TO_HAVE_HEADERS = re.compile(
    r"(nice.to.have|preferred|bonus|plus|desirable|ideally)",
    re.IGNORECASE,
)

_SALARY_PATTERN = re.compile(
    r"\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?(?:\s*/?\s*(?:yr|year|annually|hr|hour))?",
    re.IGNORECASE,
)

_LOCATION_KEYWORDS = {
    "remote": "remote",
    "hybrid": "hybrid",
    "on-site": "onsite",
    "on site": "onsite",
    "in-office": "onsite",
    "in office": "onsite",
}


@dataclass
class ParsedJD:
    """Structured representation of a job description."""
    title: str = ""
    company: str = ""
    raw_text: str = ""
    requirements: list[str] = field(default_factory=list)
    nice_to_haves: list[str] = field(default_factory=list)
    salary_range: str = ""
    location_type: str = ""  # "remote", "hybrid", "onsite", ""
    skills: list[str] = field(default_factory=list)

    def summary(self, max_len: int = 600) -> str:
        """Return a compact summary suitable for AI prompt context."""
        parts = []
        if self.title:
            parts.append(f"Role: {self.title}")
        if self.company:
            parts.append(f"Company: {self.company}")
        if self.location_type:
            parts.append(f"Location: {self.location_type}")
        if self.salary_range:
            parts.append(f"Salary: {self.salary_range}")
        if self.requirements:
            parts.append("Requirements: " + "; ".join(self.requirements[:5]))
        if self.nice_to_haves:
            parts.append("Nice-to-haves: " + "; ".join(self.nice_to_haves[:3]))
        text = " | ".join(parts)
        return text[:max_len] if len(text) > max_len else text


class JDParser:
    """Extracts structured job description data from a LinkedIn page."""

    def __init__(self, page: Page):
        self.page = page
        self._cache: dict[str, ParsedJD] = {}

    async def parse(self) -> Optional[ParsedJD]:
        """Parse the JD on the current page. Results are cached by URL."""
        url = self.page.url
        if url in self._cache:
            return self._cache[url]

        jd = ParsedJD()

        # --- Title ---
        title_el = await JOB_TITLE.query(self.page)
        if title_el:
            jd.title = (await title_el.inner_text()).strip()

        # --- Company ---
        company_el = await COMPANY_NAME.query(self.page)
        if company_el:
            jd.company = (await company_el.inner_text()).strip()

        # --- Raw JD text ---
        jd_el = await JD_CONTAINER.query(self.page)
        if not jd_el:
            log.warning("Could not find JD container on page")
            # Still cache partial result
            self._cache[url] = jd
            return jd

        jd.raw_text = (await jd_el.inner_text()).strip()

        # --- Salary ---
        salary_match = _SALARY_PATTERN.search(jd.raw_text)
        if salary_match:
            jd.salary_range = salary_match.group().strip()

        # --- Location type ---
        text_lower = jd.raw_text.lower()
        for keyword, loc_type in _LOCATION_KEYWORDS.items():
            if keyword in text_lower:
                jd.location_type = loc_type
                break

        # --- Section-based extraction ---
        jd.requirements, jd.nice_to_haves = self._extract_sections(jd.raw_text)

        # --- Skill keywords ---
        jd.skills = self._extract_skills(jd.raw_text)

        log.info(
            f"Parsed JD: {jd.title} @ {jd.company} | "
            f"{len(jd.requirements)} reqs, {len(jd.nice_to_haves)} NTH, "
            f"salary={jd.salary_range or 'N/A'}"
        )

        self._cache[url] = jd
        return jd

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sections(text: str) -> tuple[list[str], list[str]]:
        """Split JD text into requirements and nice-to-haves based on headers."""
        lines = text.split("\n")
        requirements: list[str] = []
        nice_to_haves: list[str] = []
        current_bucket: Optional[list[str]] = None

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if _REQUIREMENT_HEADERS.search(stripped):
                current_bucket = requirements
                continue
            if _NICE_TO_HAVE_HEADERS.search(stripped):
                current_bucket = nice_to_haves
                continue

            # Bullet points / list items
            if current_bucket is not None and (
                stripped.startswith(("•", "-", "–", "·", "▪", "*"))
                or re.match(r"^\d+[\.\)]\s", stripped)
            ):
                clean = re.sub(r"^[\•\-\–\·\▪\*\d\.\)]+\s*", "", stripped).strip()
                if len(clean) > 5:
                    current_bucket.append(clean)

        return requirements, nice_to_haves

    @staticmethod
    def _extract_skills(text: str) -> list[str]:
        """Extract common tech skills mentioned in the JD text."""
        # Ordered by specificity so longer matches are preferred
        _SKILL_PATTERNS = [
            "TypeScript", "JavaScript", "Python", "Java", "C\\+\\+", "C#", "Go",
            "Rust", "Ruby", "Swift", "Kotlin", "Scala", "PHP",
            "React", "Angular", "Vue", "Next\\.js", "Node\\.js", "Django",
            "Flask", "Spring", "FastAPI", "Express",
            "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform",
            "PostgreSQL", "MySQL", "MongoDB", "Redis", "ElasticSearch",
            "GraphQL", "REST", "gRPC", "Kafka", "RabbitMQ",
            "CI/CD", "Git", "Jenkins", "GitHub Actions",
            "Machine Learning", "Deep Learning", "NLP", "TensorFlow", "PyTorch",
            "Figma", "Sketch",
        ]
        found = []
        for skill in _SKILL_PATTERNS:
            if re.search(rf"\b{skill}\b", text, re.IGNORECASE):
                # Use the canonical casing from the list
                canonical = skill.replace("\\", "")
                if canonical not in found:
                    found.append(canonical)
        return found
