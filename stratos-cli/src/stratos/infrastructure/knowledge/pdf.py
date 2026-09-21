"""PDF knowledge source (text-based PDFs). Needs the optional `pypdf` package:
`pip install stratos-cli[pdf]`."""

from collections.abc import Callable
from pathlib import Path

from stratos.domain.exceptions import DependencyError, ValidationError
from stratos.infrastructure.knowledge.markdown import LocalDocumentProvider

MAX_PAGES = 300
MAX_CHARS = 500_000


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DependencyError(
            "PDF support is not installed.", hint="Install it with `pip install stratos-cli[pdf]`."
        ) from exc
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages[:MAX_PAGES]]
    except Exception as exc:  # pypdf raises many parser-specific errors
        raise ValidationError(f"Cannot read PDF '{path.name}': {type(exc).__name__}.") from exc
    return "\n\n".join(pages)[:MAX_CHARS]


class PdfKnowledgeProvider(LocalDocumentProvider):
    name = "pdf"
    pattern = "*.pdf"
    max_bytes = 20 * 1024 * 1024
    extractor: Callable[[Path], str] = staticmethod(extract_pdf_text)

    def read_text(self, path: Path) -> str:
        try:
            return self.extractor(path)
        except ValidationError:
            return ""  # one unreadable PDF must not break a whole search

    def title_for(self, text: str, path: Path) -> str:
        return path.stem.replace("_", " ").replace("-", " ")
