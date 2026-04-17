"""
Question Matcher - Match screening questions to saved answer templates

Uses fuzzy string matching to find the best answer for custom questions.
Learns from your corrections over time.
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from rapidfuzz import fuzz, process
from rich.console import Console

from jobpilot.core.config import DATA_DIR
from jobpilot.learning.learning_db import get_learning_db
from jobpilot.core.logger import get_logger

console = Console()
log = get_logger(__name__)


@dataclass
class MatchResult:
    """Result of matching a question to a template"""
    question: str
    matched_template: Optional[str]
    answer: Optional[str]
    confidence: float  # 0.0 to 1.0
    
    @property
    def confidence_level(self) -> str:
        """Get human-readable confidence level"""
        if self.confidence >= 0.85:
            return "high"
        elif self.confidence >= 0.65:
            return "medium"
        elif self.confidence >= 0.45:
            return "low"
        else:
            return "none"
    
    @property
    def confidence_emoji(self) -> str:
        """Get emoji indicator for confidence"""
        level = self.confidence_level
        return {
            "high": "🟢",
            "medium": "🟡", 
            "low": "🟠",
            "none": "🔴"
        }[level]


class QuestionMatcher:
    """
    Matches screening questions to saved answer templates.
    
    Templates are stored as:
    {
        "questions": {
            "Why are you interested in this role?": "I'm excited about...",
            "Years of experience with Python": "5+ years",
            ...
        }
    }
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db = get_learning_db(self.data_dir / "learning.db")
        self._templates: dict[str, str] = self._db.get_all_templates()

    def _reload_templates(self):
        """Refresh the in-memory cache from SQLite."""
        self._templates = self._db.get_all_templates()
    
    def match(self, question: str, threshold: float = 0.45, use_rag: bool = True) -> MatchResult:
        """
        Find the best matching template for a question.
        Falls back to RAG + Ollama if no good template match.
        
        Args:
            question: The question text to match
            threshold: Minimum similarity score (0-1) to consider a match
            use_rag: Whether to try RAG fallback if no template matches
            
        Returns:
            MatchResult with the best match (or AI-generated if RAG fallback used)
        """
        # First try template matching
        if self._templates:
            normalized = self._normalize(question)
            template_questions = list(self._templates.keys())
            normalized_templates = [self._normalize(q) for q in template_questions]
            
            result = process.extractOne(
                normalized,
                normalized_templates,
                scorer=fuzz.token_sort_ratio
            )
            
            if result:
                matched_normalized, score, idx = result
                confidence = score / 100.0
                
                if confidence >= threshold:
                    original_question = template_questions[idx]
                    return MatchResult(
                        question=question,
                        matched_template=original_question,
                        answer=self._templates[original_question],
                        confidence=confidence
                    )
        
        # No template match - try RAG + Ollama fallback
        if use_rag:
            rag_result = self._rag_answer(question)
            if rag_result:
                return MatchResult(
                    question=question,
                    matched_template="[AI Generated from Resume]",
                    answer=rag_result,
                    confidence=0.6  # Medium confidence for AI answers
                )
        
        return MatchResult(
            question=question,
            matched_template=None,
            answer=None,
            confidence=0.0
        )
    
    def _rag_answer(self, question: str) -> Optional[str]:
        """
        Generate an answer using RAG (resume context) + Ollama.
        Requires Bro to be running.
        """
        try:
            from jobpilot.core.bro_client import query_rag, chat as bro_chat, is_bro_running
            
            if not is_bro_running():
                return None
            
            # Get relevant context from RAG (resume, profile, etc.)
            context = query_rag(question, top_k=3)
            
            if not context:
                return None
            
            # Ask Ollama to generate an answer
            prompt = f"""You are helping someone fill out a job application. 
Based on their resume/profile below, write a brief, professional answer to this question.
Keep it concise (1-3 sentences) and first-person.

Resume/Profile context:
{context}

Question: {question}

Answer:"""
            
            answer = bro_chat(prompt)
            
            # Clean up the answer
            if answer and not answer.startswith("Error") and not answer.startswith("Bro is not"):
                # Remove any "Answer:" prefix the model might have added
                answer = answer.strip()
                if answer.lower().startswith("answer:"):
                    answer = answer[7:].strip()
                return answer
            
            return None
            
        except Exception as e:
            log.warning("RAG fallback failed: %s", e)
            return None
    
    def add_template(self, question: str, answer: str):
        """Add or update an answer template"""
        self._db.upsert_template(question, answer)
        self._templates[question] = answer
        log.info("Saved answer for: %s", question[:50])

    def remove_template(self, question: str) -> bool:
        """Remove an answer template"""
        deleted = self._db.delete_template(question)
        if deleted:
            self._templates.pop(question, None)
        return deleted

    def get_all_templates(self) -> dict[str, str]:
        """Get all saved templates"""
        return self._templates.copy()
    
    def learn_from_correction(self, question: str, user_answer: str):
        """
        Learn from a user correction.
        
        If the user edits a suggested answer, save their version as the new template.
        """
        # Check if this is a correction to an existing template
        match_result = self.match(question, threshold=0.7)
        
        if match_result.matched_template and match_result.answer != user_answer:
            # User corrected our suggestion - update the template
            log.info("Learning from correction: %s", question[:50])
            self.add_template(question, user_answer)
        elif not match_result.matched_template:
            # New question - save the template
            log.info("Saving new answer template: %s", question[:50])
            self.add_template(question, user_answer)

    def learn_from_edit(self, question: str, old_answer: str, new_answer: str):
        """
        Learn from a user editing a suggested answer.

        If the user manually corrected a suggestion, record the new answer
        as the correct template for this question pattern.
        """
        if new_answer and new_answer != old_answer:
            self.learn_from_correction(question, new_answer)

    def match_with_context(
        self,
        question: str,
        jd_summary: str,
        threshold: float = 0.45,
    ) -> MatchResult:
        """Match a question and enrich the answer with JD context.

        For contextual questions (why interested, experiences, strengths),
        always tailors the template answer using the JD summary via Ollama.
        For factual questions (years of experience, salary), returns the
        template answer as-is.
        """
        base = self.match(question, threshold=threshold)

        # If no answer at all, nothing to enrich
        if not base.answer or not jd_summary:
            return base

        # Decide whether this question benefits from contextualisation
        contextual_keywords = [
            "why", "interest", "motivat", "excit", "strength",
            "challenge", "experience with", "tell us about",
            "describe", "bring to", "fit for", "contribution",
            "goals", "philosophy", "achieve", "situation where",
        ]
        q_lower = question.lower()
        needs_context = any(kw in q_lower for kw in contextual_keywords)

        if not needs_context:
            return base  # factual → return template as-is

        # Enrich via Ollama
        try:
            from jobpilot.core.bro_client import chat as bro_chat, is_bro_running

            if not is_bro_running():
                return base

            prompt = (
                f"Rewrite this job application answer to be specific to the company and role.\n"
                f"Keep the same length and first-person tone. Do NOT add placeholder brackets.\n\n"
                f"Job context: {jd_summary}\n\n"
                f"Question: {question}\n"
                f"Original answer: {base.answer}\n\n"
                f"Tailored answer:"
            )
            enriched = bro_chat(prompt)
            if enriched and not enriched.startswith("Error") and len(enriched) > 10:
                enriched = enriched.strip()
                if enriched.lower().startswith("tailored answer:"):
                    enriched = enriched[len("tailored answer:"):].strip()
                return MatchResult(
                    question=question,
                    matched_template=base.matched_template,
                    answer=enriched,
                    confidence=min(1.0, base.confidence + 0.05),
                )
        except Exception as e:
            log.warning("Context enrichment failed: %s", e)

        return base
    
    def _normalize(self, text: str) -> str:
        """Normalize text for matching"""
        # Lowercase and strip
        text = text.lower().strip()
        
        # Remove common filler words that don't affect meaning
        filler_words = ["please", "kindly", "briefly", "the", "a", "an", "your", "you", "are"]
        words = text.split()
        words = [w for w in words if w not in filler_words]
        
        return " ".join(words)
    
    def display_templates(self):
        """Display all saved templates"""
        from rich.table import Table
        
        if not self._templates:
            console.print("[dim]No answer templates saved yet.[/dim]")
            console.print("They'll be created automatically as you fill out applications.")
            return
        
        table = Table(title=f"Answer Templates ({len(self._templates)} saved)")
        table.add_column("Question", style="cyan", max_width=50)
        table.add_column("Answer", style="white", max_width=50)
        
        for question, answer in self._templates.items():
            # Truncate long text
            q_display = question[:47] + "..." if len(question) > 50 else question
            a_display = answer[:47] + "..." if len(answer) > 50 else answer
            table.add_row(q_display, a_display)
        
        console.print(table)


# Common question patterns with suggested prompt structures
COMMON_QUESTIONS = {
    "why interested": "Why are you interested in this role/company?",
    "strength": "What is your greatest strength?",
    "weakness": "What is your greatest weakness?",
    "experience with": "Describe your experience with [technology/skill]",
    "years of experience": "How many years of experience do you have?",
    "salary expectation": "What are your salary expectations?",
    "start date": "When can you start?",
    "why leaving": "Why are you leaving your current job?",
    "describe yourself": "Tell us about yourself",
    "challenging project": "Describe a challenging project you worked on",
}


# Global instance
_matcher: Optional[QuestionMatcher] = None

def get_question_matcher() -> QuestionMatcher:
    """Get the global question matcher instance"""
    global _matcher
    if _matcher is None:
        _matcher = QuestionMatcher()
    return _matcher
