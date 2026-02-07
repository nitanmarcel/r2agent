from pathlib import Path
from typing import Optional

import appdirs
import yaml
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    model: str = Field(description="Model identifier")
    base_url: Optional[str] = Field(
        default=None, description="Base URL (defaults to OpenAI)"
    )
    api_key: Optional[str] = Field(default=None, description="API key (if required)")
    extra_headers: Optional[dict[str, str]] = Field(
        default=None, description="Extra headers to send with requests"
    )


class R2AgentConfig(BaseModel):
    default_provider: str = Field(
        default="default", description="Default provider to use"
    )
    allow_r2cmd: bool = Field(
        default=False, description="Allow the AI to execute r2 commands via r2cmd tool"
    )
    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {"default": ProviderConfig(model="gpt-4o-mini")}
    )

    @classmethod
    def get_config_path(cls) -> Path:
        config_dir = Path(appdirs.user_config_dir("r2agent"))
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.yaml"

    @classmethod
    def load(cls) -> "R2AgentConfig":
        config_path = cls.get_config_path()

        if config_path.exists():
            try:
                data = yaml.safe_load(config_path.read_text())
                return cls.model_validate(data)
            except (yaml.YAMLError, Exception):
                return cls()

        config = cls()
        config.save()
        return config

    def save(self) -> None:
        config_path = self.get_config_path()
        config_path.write_text(
            yaml.dump(self.model_dump(), default_flow_style=False, sort_keys=False)
        )

    def get_provider(self, name: Optional[str] = None) -> ProviderConfig:
        provider_name = name or self.default_provider
        if provider_name not in self.providers:
            raise ValueError(f"Provider '{provider_name}' not found in config")
        return self.providers[provider_name]


_config: Optional[R2AgentConfig] = None


def get_config() -> R2AgentConfig:
    global _config
    if _config is None:
        _config = R2AgentConfig.load()
    return _config


def reload_config() -> R2AgentConfig:
    global _config
    _config = R2AgentConfig.load()
    return _config
