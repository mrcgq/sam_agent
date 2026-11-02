"""
SAM Agent 配置管理
"""
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any


class SAMConfig:
    """SAM Agent 配置"""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path) if config_path else Path.home() / ".sam_agent" / "config.json"
        self.data: Dict[str, Any] = {}
        self._load_or_init()

    def _load_or_init(self):
        """加载或初始化配置"""
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            # 默认配置
            self.data = {
                "model": {
                    "base_url": os.environ.get("SAM_MODEL_BASE_URL", ""),
                    "api_key": os.environ.get("SAM_API_KEY", ""),
                    "model_name": os.environ.get("SAM_MODEL_NAME", "deepseek-chat"),
                    "provider": "openai"  # openai / anthropic / deepseek
                },
                "agent": {
                    "max_iterations": 50,
                    "timeout_seconds": 300,
                    "max_tokens": 4096,
                    "temperature": 0.7
                },
                "oracle": {
                    "accept_threshold": 0.95,
                    "enable_ap25_check": True
                },
                "trajectory": {
                    "db_path": str(Path.home() / ".sam_agent" / "trajectories.db"),
                    "enabled": True
                },
                "knowledge": {
                    "rules_dir": str(Path.home() / ".sam_agent" / "rules"),
                    "enabled": True
                }
            }
            self.save()

    def save(self):
        """保存配置"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default=None):
        """获取配置项"""
        keys = key.split(".")
        value = self.data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value

    def set(self, key: str, value: Any):
        """设置配置项"""
        keys = key.split(".")
        target = self.data
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value
        self.save()

    def update_model_config(self, base_url: str, api_key: str, model_name: str, provider: str = "openai"):
        """更新模型配置"""
        self.data["model"]["base_url"] = base_url
        self.data["model"]["api_key"] = api_key
        self.data["model"]["model_name"] = model_name
        self.data["model"]["provider"] = provider
        self.save()

    def __repr__(self):
        return f"SAMConfig(path={self.config_path})"
