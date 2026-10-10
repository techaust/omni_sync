"""OmniSync 2.0 server package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("omnisync")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
