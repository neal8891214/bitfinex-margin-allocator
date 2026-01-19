"""Config Manager 模組 - 載入 YAML 配置並支援環境變數替換"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml
from pydantic import BaseModel


class BitfinexConfig(BaseModel):
    """Bitfinex API 配置"""
    api_key: str
    api_secret: str
    base_url: str = "https://api.bitfinex.com"
    ws_url: str = "wss://api.bitfinex.com/ws/2"


class TelegramConfig(BaseModel):
    """Telegram 通知配置"""
    bot_token: str
    chat_id: str
    enabled: bool = True


class MonitorConfig(BaseModel):
    """監控設定"""
    poll_interval_sec: int = 60
    volatility_update_hours: int = 1
    volatility_lookback_days: int = 7
    heartbeat_interval_sec: int = 300


class ThresholdsConfig(BaseModel):
    """觸發閾值配置"""
    min_adjustment_usdt: float = 50.0
    min_deviation_pct: float = 5.0
    emergency_margin_rate: float = 2.0
    price_spike_pct: float = 3.0
    account_margin_rate_warning: float = 3.0


class LiquidationConfig(BaseModel):
    """減倉設定"""
    enabled: bool = True
    require_confirmation: bool = False
    max_single_close_pct: float = 25.0
    cooldown_seconds: int = 30
    safety_margin_multiplier: float = 3.0
    dry_run: bool = True


class DatabaseConfig(BaseModel):
    """資料庫配置"""
    path: str = "data/margin_balancer.db"


class LoggingConfig(BaseModel):
    """日誌配置"""
    level: str = "INFO"
    file: str = "logs/margin_balancer.log"


class Config(BaseModel):
    """主配置模型，整合所有子配置"""
    bitfinex: BitfinexConfig
    telegram: TelegramConfig
    monitor: MonitorConfig = MonitorConfig()
    thresholds: ThresholdsConfig = ThresholdsConfig()
    risk_weights: Dict[str, float] = {}
    position_priority: Dict[str, int] = {}
    liquidation: LiquidationConfig = LiquidationConfig()
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()

    def get_risk_weight(self, symbol: str) -> Optional[float]:
        """取得指定幣種的風險權重，未配置則回傳 None"""
        return self.risk_weights.get(symbol)

    def get_position_priority(self, symbol: str) -> int:
        """取得指定幣種的優先級，未配置則使用 default 值"""
        if symbol in self.position_priority:
            return self.position_priority[symbol]
        return self.position_priority.get("default", 50)


# 環境變數替換正規表達式：匹配 ${VAR_NAME} 格式
ENV_VAR_PATTERN = re.compile(r'\$\{([^}]+)\}')


def _substitute_env_vars(value: Any) -> Any:
    """遞迴處理環境變數替換

    支援 ${ENV_VAR} 語法，遞迴處理 dict 和 list
    """
    if isinstance(value, str):
        # 替換字串中的所有 ${VAR} 為環境變數值
        def replace_match(match: re.Match[str]) -> str:
            var_name = match.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                # 如果環境變數不存在，保留原始格式
                return match.group(0)
            return env_value
        return ENV_VAR_PATTERN.sub(replace_match, value)
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    else:
        return value


def load_config(path: Union[str, Path]) -> Config:
    """從 YAML 檔案載入配置

    Args:
        path: YAML 配置檔路徑

    Returns:
        Config: 驗證後的配置物件

    Raises:
        FileNotFoundError: 配置檔不存在
        yaml.YAMLError: YAML 格式錯誤
        pydantic.ValidationError: 配置驗證失敗
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    # 遞迴替換環境變數
    config_data = _substitute_env_vars(raw_config)

    # 使用 Pydantic 驗證並建立配置物件
    return Config(**config_data)
