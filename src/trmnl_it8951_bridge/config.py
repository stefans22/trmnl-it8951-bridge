"""Configuration: defaults < YAML file < environment (ADR 0007).

Environment variables: ``EPD_<SECTION>__<KEY>``, e.g. ``EPD_PANEL__VCOM=-1.50``.
The YAML file is read from ``$EPD_CONFIG_FILE`` (default ``/config/config.yaml``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

CONFIG_FILE_ENV = "EPD_CONFIG_FILE"
DEFAULT_CONFIG_FILE = "/config/config.yaml"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PanelConfig(_Section):
    driver: Literal["it8951", "fake"] = "it8951"
    vcom: float = Field(lt=0, ge=-5, description="VCOM in volts from the FPC cable, e.g. -1.50")
    spi_device: Path = Path("/dev/spidev0.0")
    spi_speed_hz: int = Field(default=12_000_000, gt=0, le=32_000_000)
    gpio_chip: Path = Path("/dev/gpiochip0")
    pin_rst: int = Field(default=17, ge=0)
    pin_hrdy: int = Field(default=24, ge=0)
    pin_cs: int = Field(default=8, ge=0)
    ready_timeout_s: float = Field(default=10.0, gt=0)
    low_power: Literal["sleep", "standby", "none"] = "sleep"
    enhance_driving: bool = False
    expected_width: int | None = Field(default=None, gt=0)
    expected_height: int | None = Field(default=None, gt=0)


class DisplayConfig(_Section):
    rotation: Literal[0, 90, 180, 270] = 0
    rotation_via_controller: bool = True
    mirror_x: bool = False
    mirror_y: bool = False
    size_mismatch: Literal["reject", "fit", "stretch"] = "reject"
    dither: bool = False
    full_refresh_every: int = Field(default=10, ge=0)
    full_refresh_area_ratio: float = Field(default=0.5, gt=0, le=1)
    full_refresh_waveform: Literal["gc16", "init_then_gc16"] = "gc16"
    tile_size: int = Field(default=32, ge=4, le=256, multiple_of=4)
    max_rects: int = Field(default=8, ge=1)
    clear_on_startup: bool = True
    restore_last_frame: bool = True
    clear_on_shutdown: bool = True


class PushConfig(_Section):
    enabled: bool = True
    token: SecretStr | None = None
    max_body_bytes: int = Field(default=10 * 1024 * 1024, gt=0)


class PullConfig(_Section):
    enabled: bool = False
    url: AnyHttpUrl | None = None
    interval_s: int = Field(default=300, ge=10)
    timeout_s: float = Field(default=60.0, gt=0)
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    backoff_max_s: int = Field(default=900, ge=1)
    on_start: bool = True

    @model_validator(mode="after")
    def _url_required_when_enabled(self) -> Self:
        if self.enabled and self.url is None:
            raise ValueError("pull.url is required when pull.enabled is true")
        return self


class ServerConfig(_Section):
    host: str = "0.0.0.0"  # noqa: S104 - binding all interfaces is intended (trusted network)
    port: int = Field(default=8080, ge=1, le=65535)


class StorageConfig(_Section):
    data_dir: Path = Path("/data")
    persist: bool = True
    keep_images: int = Field(default=0, ge=0, description="keep the last N received images (0 = off)")


class LogConfig(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["text", "json"] = "text"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EPD_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="forbid",
    )

    panel: PanelConfig
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    push: PushConfig = Field(default_factory=PushConfig)
    pull: PullConfig = Field(default_factory=PullConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    log: LogConfig = Field(default_factory=LogConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # First source wins: init kwargs > environment > YAML file > defaults.
        path = Path(os.environ.get(CONFIG_FILE_ENV, DEFAULT_CONFIG_FILE))
        return (init_settings, env_settings, YamlConfigSettingsSource(settings_cls, yaml_file=path))

    def redacted(self) -> dict[str, Any]:
        """JSON-compatible dump with secrets masked (for logs and /api/status)."""
        return self.model_dump(mode="json")


def load_settings(**overrides: Any) -> Settings:
    """Load and validate settings. Raises ``pydantic.ValidationError`` on bad input."""
    return Settings(**overrides)
