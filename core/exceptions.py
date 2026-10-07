"""
Custom exception hierarchy for MULTI_DOWNLOADER.
"""

class MultiDLError(Exception):
    """Base exception for all MULTI_DOWNLOADER errors."""
    pass


class BackendNotFoundError(MultiDLError):
    """Raised when no matching backend can be found for a given URL or backend name."""
    pass


class UnsupportedURLError(MultiDLError):
    """Raised when none of the available backends support the given URL."""
    pass


class DownloadFailedError(MultiDLError):
    """Raised when a backend fails to complete a download."""
    pass


class ArchiveDuplicateError(MultiDLError):
    """Raised when a download is rejected because it already exists in the archive."""
    pass


class ConfigError(MultiDLError):
    """Raised when there is an issue loading or validating configuration settings."""
    pass


class EngineNotFoundError(MultiDLError):
    """Raised when an external or submodule engine is missing."""
    pass


class AuthenticationError(MultiDLError):
    """Raised when authentication credentials (cookie/token) are required or invalid."""
    pass

