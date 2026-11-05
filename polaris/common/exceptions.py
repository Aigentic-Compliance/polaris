"""Domain-specific exceptions. Every failure boundary in this repo raises
one of these rather than letting a raw library exception (requests,
PyMuPDF, openai, SQLAlchemy) leak past the module that knows what actually
went wrong -- and rather than swallowing the failure into a silently wrong
return value (an empty list, a None, a fabricated label). The rule applied
throughout: catch narrowly, log with context, re-raise as one of these, or
don't catch at all. Never catch-and-return-a-plausible-looking-default.
"""

from __future__ import annotations


class PolarisError(Exception):
    """Base class for all domain errors raised by this repo."""


class DownloadError(PolarisError):
    """Raised when fetching a URL fails (network error, non-2xx, size cap
    exceeded). Callers must record this as a failure, never as an implicit
    'no candidate here'."""


class DocumentParsingError(PolarisError):
    """Base class for failures parsing a downloaded document, regardless
    of format. Callers that don't care which format failed (the pipeline's
    per-candidate processing, which handles both PDF and HTML) can catch
    this common base instead of enumerating every format-specific
    subclass."""


class PDFParsingError(DocumentParsingError):
    """Raised when PyMuPDF cannot open or read a PDF (corrupt file,
    unsupported encoding, truncated download)."""


class HTMLParsingError(DocumentParsingError):
    """Raised when an HTML document cannot be parsed into extractable
    text (malformed beyond what a lenient parser can recover, encoding
    errors). Rare in practice -- HTML parsers are deliberately forgiving
    -- but never assumed impossible."""


class ClassificationError(PolarisError):
    """Raised when the LLM classifier call fails (API error, malformed
    response) after retries are exhausted. Callers must not fabricate a
    label to paper over this -- see cascade.py's handling."""


class RegistryError(PolarisError):
    """Raised when a SourceRegistry operation fails (duplicate primary key,
    connection failure, constraint violation) in a way the caller needs to
    know about rather than have swallowed."""


class GoldDataError(PolarisError):
    """Raised when a gold/pack input file is missing, malformed, or fails
    contract validation."""


class SearchProviderError(PolarisError):
    """Raised when a web search API call fails (auth, rate limit, network,
    malformed response). Unlike the LLM classifier, there is no meaningful
    disposable fallback for search -- an empty or fabricated result set
    would be actively misleading (it would look like "searched, found
    nothing" rather than "could not search at all"), so failures here are
    always raised, never silently swallowed into an empty list."""


class RawStoreError(PolarisError):
    """Raised when saving to or reading from the content-addressed raw
    store fails (disk full, permissions, path does not exist, or the
    stored bytes at a path no longer match their recorded hash)."""


class QualificationError(PolarisError):
    """Raised when the LLM document-qualification call fails (API error,
    malformed response) after retries are exhausted. Distinct from
    ClassificationError (paragraph-level LET labelling) because this is a
    different call, a different stage (document-level accept/reject, not
    segment-level content labelling), and callers need to be able to catch
    one without the other."""


class DOCXParsingError(DocumentParsingError):
    """Raised when python-docx cannot open or read a .docx file (corrupt
    zip container, missing word/document.xml, truncated download)."""
