"""Show TRMNL HA add-on dashboards on a Waveshare IT8951 e-paper panel."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ha-trmnl-ws-display")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"
