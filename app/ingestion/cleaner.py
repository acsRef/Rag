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
        """Strip ASCII control chars except \\n, \\t, \\r."""
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    def _normalize_unicode(self, text: str) -> str:
        """NFC normalization (e.g. é as single code point)."""
        return unicodedata.normalize("NFC", text)

    def _remove_invisible_chars(self, text: str) -> str:
        """Remove BOM, zero-width chars, non-breaking space -> regular space."""
        text = text.replace("\ufeff", "")
        text = text.replace("\u200b", "")
        text = text.replace("\u200c", "")
        text = text.replace("\u200d", "")
        text = text.replace("\xa0", " ")
        return text

    def _normalize_punctuation(self, text: str) -> str:
        """Curly quotes/apostrophes/dashes to straight equivalents."""
        text = text.replace("\u201c", '"').replace("\u201d", '"')
        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.replace("\u2013", "-").replace("\u2014", "-")
        return text

    def _strip_line_trailing_spaces(self, text: str) -> str:
        """Remove trailing whitespace on each line."""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    def _remove_page_markers(self, text: str) -> str:
        """Strip common page-break patterns: '--- Page N ---', HTML comments, form feeds."""
        text = re.sub(r"(?m)^---\s*Page\s+\d+\s*---\s*$", "", text)
        text = re.sub(r"(?m)^<!--\s*pagebreak\s*-->$", "", text)
        text = re.sub(r"(?m)^\f$", "", text)
        return text

    def _collapse_blank_lines(self, text: str) -> str:
        """Reduce 3+ consecutive newlines to exactly 2."""
        return re.sub(r"\n\s*\n", "\n\n", text)

    def _collapse_extra_spaces(self, text: str) -> str:
        """Collapse multiple spaces into one."""
        return re.sub(r" +", " ", text)


document_cleaner = DocumentCleaner()
