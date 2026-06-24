import re
import unicodedata


class DocumentCleaner:
    """6-step text cleaner: line endings, control chars, unicode NFC, invisible chars,
    page markers, blank lines. Optionally normalizes punctuation and collapses spaces."""

    def clean(
        self,
        text: str,
        fix_punctuation: bool = False,
        collapse_spaces: bool = False,
    ) -> str:
        if not text:
            return ""
        text = self._normalize_line_endings(text)
        text = self._remove_control_chars(text)
        text = self._normalize_unicode(text)
        text = self._remove_invisible_chars(text)
        if fix_punctuation:
            text = self._normalize_punctuation(text)
        text = self._strip_line_trailing_spaces(text)
        text = self._remove_page_markers(text)
        text = self._collapse_blank_lines(text)
        if collapse_spaces:
            text = self._collapse_extra_spaces(text)
        return text.strip()

    def _normalize_line_endings(self, text: str) -> str:
        """Unify \\r\\n and \\r to \\n."""
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _remove_control_chars(self, text: str) -> str:
        """Strip ASCII control chars except \\n, \\t, \\r, \\f (form feed kept for page marker detection)."""
        return re.sub(r"[\x00-\x08\x0b\x0e-\x1f\x7f]", "", text)

    def _normalize_unicode(self, text: str) -> str:
        """NFC normalization with optional ftfy encoding repair."""
        try:
            import ftfy
            text = ftfy.fix_text(text, normalization="NFC")
        except ImportError:
            text = unicodedata.normalize("NFC", text)
        return text

    def _remove_invisible_chars(self, text: str) -> str:
        """Remove BOM, zero-width, invisible marks; special spaces -> regular space."""
        text = text.replace("\ufeff", "")
        text = text.replace("\u200b", "")
        text = text.replace("\u200c", "")
        text = text.replace("\u200d", "")
        text = text.replace("\u00ad", "")    # soft hyphen
        text = text.replace("\u200e", "")    # LRM
        text = text.replace("\u200f", "")    # RLM
        text = text.replace("\u2060", "")    # word joiner
        text = text.replace("\u2028", "\n")  # line separator
        text = text.replace("\u2029", "\n")  # paragraph separator
        text = text.replace("\u2009", " ")   # thin space
        text = text.replace("\u200a", " ")   # hair space
        text = text.replace("\xa0", " ")
        return text

    def _normalize_punctuation(self, text: str) -> str:
        """Curly quotes/apostrophes/dashes/ellipsis to straight equivalents."""
        text = text.replace("\u201c", '"').replace("\u201d", '"')
        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.replace("\u2013", "-").replace("\u2014", "-")
        text = text.replace("\u2026", "...")
        text = text.replace("\u2e3a", "--").replace("\u2e3b", "--")
        return text

    def _strip_line_trailing_spaces(self, text: str) -> str:
        """Remove trailing whitespace on each line."""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    def _remove_page_markers(self, text: str) -> str:
        """Strip page-break patterns (EN/CN) and turn form feeds into regular line breaks."""
        text = re.sub(r"(?m)^---\s*Page\s+\d+\s*---\s*$", "", text)
        text = re.sub(r"(?m)^---\s*第\s*\d+\s*页\s*---\s*$", "", text)
        text = re.sub(r"(?m)^<!--\s*pagebreak\s*-->$", "", text)
        text = text.replace("\f", "\n")
        return text

    def _collapse_blank_lines(self, text: str) -> str:
        """Reduce 3+ consecutive newlines to exactly 2."""
        return re.sub(r"\n{3,}", "\n\n", text)

    def _collapse_extra_spaces(self, text: str) -> str:
        """Collapse multiple spaces and tabs into one space."""
        return re.sub(r"[ \t]+", " ", text)


document_cleaner = DocumentCleaner()
