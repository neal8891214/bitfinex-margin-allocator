"""Config Manager 模組測試"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.config_manager import (
    Config,
    BitfinexConfig,
    TelegramConfig,
    MonitorConfig,
    ThresholdsConfig,
    LiquidationConfig,
    DatabaseConfig,
    LoggingConfig,
    load_config,
    _substitute_env_vars,
)


class TestSubstituteEnvVars:
    """環境變數替換功能測試"""

    def test_substitute_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試字串中的環境變數替換"""
        monkeypatch.setenv("TEST_VAR", "test_value")
        result = _substitute_env_vars("${TEST_VAR}")
        assert result == "test_value"

    def test_substitute_string_with_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試字串中混合文字與環境變數"""
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        result = _substitute_env_vars("http://${HOST}:${PORT}/api")
        assert result == "http://localhost:8080/api"

    def test_substitute_missing_env_var(self) -> None:
        """測試未設定的環境變數保留原始格式"""
        # 確保環境變數不存在
        if "NONEXISTENT_VAR" in os.environ:
            del os.environ["NONEXISTENT_VAR"]
        result = _substitute_env_vars("${NONEXISTENT_VAR}")
        assert result == "${NONEXISTENT_VAR}"

    def test_substitute_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試 dict 遞迴替換"""
        monkeypatch.setenv("API_KEY", "my_key")
        monkeypatch.setenv("API_SECRET", "my_secret")
        data = {
            "api": {
                "key": "${API_KEY}",
                "secret": "${API_SECRET}",
            },
            "static": "no_change",
        }
        result = _substitute_env_vars(data)
        assert result == {
            "api": {
                "key": "my_key",
                "secret": "my_secret",
            },
            "static": "no_change",
        }

    def test_substitute_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試 list 遞迴替換"""
        monkeypatch.setenv("ITEM1", "value1")
        monkeypatch.setenv("ITEM2", "value2")
        data = ["${ITEM1}", "${ITEM2}", "static"]
        result = _substitute_env_vars(data)
        assert result == ["value1", "value2", "static"]

    def test_substitute_non_string(self) -> None:
        """測試非字串值保持不變"""
        assert _substitute_env_vars(123) == 123
        assert _substitute_env_vars(True) is True
        assert _substitute_env_vars(None) is None
        assert _substitute_env_vars(3.14) == 3.14


class TestConfigModels:
    """配置模型測試"""

    def test_bitfinex_config(self) -> None:
        """測試 BitfinexConfig 模型"""
        config = BitfinexConfig(api_key="key", api_secret="secret")
        assert config.api_key == "key"
        assert config.api_secret == "secret"
        assert config.base_url == "https://api.bitfinex.com"
        assert config.ws_url == "wss://api.bitfinex.com/ws/2"

    def test_telegram_config(self) -> None:
        """測試 TelegramConfig 模型"""
        config = TelegramConfig(bot_token="token", chat_id="123")
        assert config.bot_token == "token"
        assert config.chat_id == "123"
        assert config.enabled is True

    def test_monitor_config_defaults(self) -> None:
        """測試 MonitorConfig 預設值"""
        config = MonitorConfig()
        assert config.poll_interval_sec == 60
        assert config.volatility_update_hours == 1
        assert config.volatility_lookback_days == 7
        assert config.heartbeat_interval_sec == 300

    def test_thresholds_config_defaults(self) -> None:
        """測試 ThresholdsConfig 預設值"""
        config = ThresholdsConfig()
        assert config.min_adjustment_usdt == 50.0
        assert config.min_deviation_pct == 5.0
        assert config.emergency_margin_rate == 2.0
        assert config.price_spike_pct == 3.0
        assert config.account_margin_rate_warning == 3.0

    def test_liquidation_config_defaults(self) -> None:
        """測試 LiquidationConfig 預設值"""
        config = LiquidationConfig()
        assert config.enabled is True
        assert config.require_confirmation is False
        assert config.max_single_close_pct == 25.0
        assert config.cooldown_seconds == 30
        assert config.safety_margin_multiplier == 3.0
        assert config.dry_run is True

    def test_database_config_defaults(self) -> None:
        """測試 DatabaseConfig 預設值"""
        config = DatabaseConfig()
        assert config.path == "data/margin_balancer.db"

    def test_logging_config_defaults(self) -> None:
        """測試 LoggingConfig 預設值"""
        config = LoggingConfig()
        assert config.level == "INFO"
        assert config.file == "logs/margin_balancer.log"


class TestConfig:
    """主配置模型測試"""

    @pytest.fixture
    def minimal_config_data(self) -> dict:
        """最小配置資料"""
        return {
            "bitfinex": {
                "api_key": "test_key",
                "api_secret": "test_secret",
            },
            "telegram": {
                "bot_token": "test_token",
                "chat_id": "123456",
            },
        }

    def test_config_minimal(self, minimal_config_data: dict) -> None:
        """測試最小配置"""
        config = Config(**minimal_config_data)
        assert config.bitfinex.api_key == "test_key"
        assert config.telegram.bot_token == "test_token"
        # 檢查預設值
        assert config.monitor.poll_interval_sec == 60
        assert config.risk_weights == {}
        assert config.position_priority == {}

    def test_get_risk_weight_configured(self) -> None:
        """測試 get_risk_weight 取得已配置的權重"""
        config = Config(
            bitfinex=BitfinexConfig(api_key="k", api_secret="s"),
            telegram=TelegramConfig(bot_token="t", chat_id="c"),
            risk_weights={"BTC": 1.0, "ETH": 1.2},
        )
        assert config.get_risk_weight("BTC") == 1.0
        assert config.get_risk_weight("ETH") == 1.2

    def test_get_risk_weight_not_configured(self) -> None:
        """測試 get_risk_weight 未配置回傳 None"""
        config = Config(
            bitfinex=BitfinexConfig(api_key="k", api_secret="s"),
            telegram=TelegramConfig(bot_token="t", chat_id="c"),
            risk_weights={"BTC": 1.0},
        )
        assert config.get_risk_weight("SOL") is None

    def test_get_position_priority_configured(self) -> None:
        """測試 get_position_priority 取得已配置的優先級"""
        config = Config(
            bitfinex=BitfinexConfig(api_key="k", api_secret="s"),
            telegram=TelegramConfig(bot_token="t", chat_id="c"),
            position_priority={"BTC": 100, "ETH": 90, "default": 50},
        )
        assert config.get_position_priority("BTC") == 100
        assert config.get_position_priority("ETH") == 90

    def test_get_position_priority_default(self) -> None:
        """測試 get_position_priority 未配置使用 default 值"""
        config = Config(
            bitfinex=BitfinexConfig(api_key="k", api_secret="s"),
            telegram=TelegramConfig(bot_token="t", chat_id="c"),
            position_priority={"BTC": 100, "default": 50},
        )
        assert config.get_position_priority("SOL") == 50

    def test_get_position_priority_no_default(self) -> None:
        """測試 get_position_priority 無 default 時使用 50"""
        config = Config(
            bitfinex=BitfinexConfig(api_key="k", api_secret="s"),
            telegram=TelegramConfig(bot_token="t", chat_id="c"),
            position_priority={"BTC": 100},
        )
        assert config.get_position_priority("SOL") == 50


class TestLoadConfig:
    """load_config 函數測試"""

    def test_load_config_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試從 YAML 檔案載入配置"""
        monkeypatch.setenv("TEST_API_KEY", "env_api_key")
        monkeypatch.setenv("TEST_API_SECRET", "env_api_secret")
        monkeypatch.setenv("TEST_BOT_TOKEN", "env_bot_token")
        monkeypatch.setenv("TEST_CHAT_ID", "env_chat_id")

        config_data = {
            "bitfinex": {
                "api_key": "${TEST_API_KEY}",
                "api_secret": "${TEST_API_SECRET}",
            },
            "telegram": {
                "bot_token": "${TEST_BOT_TOKEN}",
                "chat_id": "${TEST_CHAT_ID}",
            },
            "risk_weights": {
                "BTC": 1.0,
                "ETH": 1.2,
            },
            "position_priority": {
                "BTC": 100,
                "default": 50,
            },
        }

        config_file = tmp_path / "config.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config = load_config(config_file)

        assert config.bitfinex.api_key == "env_api_key"
        assert config.bitfinex.api_secret == "env_api_secret"
        assert config.telegram.bot_token == "env_bot_token"
        assert config.telegram.chat_id == "env_chat_id"
        assert config.risk_weights == {"BTC": 1.0, "ETH": 1.2}
        assert config.get_risk_weight("BTC") == 1.0
        assert config.get_position_priority("BTC") == 100
        assert config.get_position_priority("SOL") == 50

    def test_load_config_file_not_found(self) -> None:
        """測試配置檔不存在時拋出 FileNotFoundError"""
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config("/nonexistent/path/config.yaml")

    def test_load_config_full_example(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試載入完整配置範例"""
        monkeypatch.setenv("BITFINEX_API_KEY", "my_api_key")
        monkeypatch.setenv("BITFINEX_API_SECRET", "my_api_secret")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "my_bot_token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "my_chat_id")

        config_data = {
            "bitfinex": {
                "api_key": "${BITFINEX_API_KEY}",
                "api_secret": "${BITFINEX_API_SECRET}",
                "base_url": "https://api.bitfinex.com",
                "ws_url": "wss://api.bitfinex.com/ws/2",
            },
            "telegram": {
                "bot_token": "${TELEGRAM_BOT_TOKEN}",
                "chat_id": "${TELEGRAM_CHAT_ID}",
                "enabled": True,
            },
            "monitor": {
                "poll_interval_sec": 120,
                "volatility_update_hours": 2,
                "volatility_lookback_days": 14,
                "heartbeat_interval_sec": 600,
            },
            "thresholds": {
                "min_adjustment_usdt": 100,
                "min_deviation_pct": 10,
                "emergency_margin_rate": 1.5,
                "price_spike_pct": 5.0,
                "account_margin_rate_warning": 2.5,
            },
            "risk_weights": {
                "BTC": 1.0,
                "ETH": 1.2,
                "SOL": 1.5,
            },
            "position_priority": {
                "BTC": 100,
                "ETH": 90,
                "SOL": 80,
                "default": 50,
            },
            "liquidation": {
                "enabled": True,
                "require_confirmation": False,
                "max_single_close_pct": 30,
                "cooldown_seconds": 60,
                "safety_margin_multiplier": 2.5,
                "dry_run": False,
            },
            "database": {
                "path": "data/test.db",
            },
            "logging": {
                "level": "DEBUG",
                "file": "logs/test.log",
            },
        }

        config_file = tmp_path / "full_config.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config = load_config(config_file)

        # 驗證所有欄位
        assert config.bitfinex.api_key == "my_api_key"
        assert config.monitor.poll_interval_sec == 120
        assert config.thresholds.min_adjustment_usdt == 100
        assert config.liquidation.max_single_close_pct == 30
        assert config.database.path == "data/test.db"
        assert config.logging.level == "DEBUG"
        assert config.get_risk_weight("SOL") == 1.5
        assert config.get_position_priority("SOL") == 80

    def test_load_config_with_path_object(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """測試使用 Path 物件載入配置"""
        monkeypatch.setenv("KEY", "value")
        monkeypatch.setenv("SECRET", "secret")
        monkeypatch.setenv("TOKEN", "token")
        monkeypatch.setenv("CHAT", "chat")

        config_data = {
            "bitfinex": {"api_key": "${KEY}", "api_secret": "${SECRET}"},
            "telegram": {"bot_token": "${TOKEN}", "chat_id": "${CHAT}"},
        }

        config_file = tmp_path / "config.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config = load_config(config_file)  # 使用 Path 物件
        assert config.bitfinex.api_key == "value"
