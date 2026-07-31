"""应用配置加载。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    base_url: str = Field(default="https://ilinkai.weixin.qq.com", alias="BASE_URL")
    bot_type: str = Field(default="3", alias="BOT_TYPE")
    """iLink 登录二维码的 bot_type，官方规范硬编码为 3。"""

    long_poll_timeout_ms: int = Field(default=35000, alias="LONG_POLL_TIMEOUT_MS")

    db_path: Path = Field(default=Path("data/clawbot.db"), alias="DB_PATH")

    ai_base_url: str = Field(default="https://api.openai.com/v1", alias="AI_BASE_URL")
    ai_api_key: str = Field(default="", alias="AI_API_KEY")
    ai_model: str = Field(default="gpt-4o-mini", alias="AI_MODEL")
    ai_system_prompt: str = Field(default="你是一个有用的助手。", alias="AI_SYSTEM_PROMPT")
    max_history: int = Field(default=20, alias="MAX_HISTORY")

    # ---- Coze (扣子) 配置 ----
    # 设置 COZE_PAT + COZE_BOT_ID 即启用 Coze Provider（推荐个人用户使用）
    coze_pat: str = Field(default="", alias="COZE_PAT")
    coze_bot_id: str = Field(default="", alias="COZE_BOT_ID")
    coze_api_base: str = Field(default="https://api.coze.cn", alias="COZE_API_BASE")
    coze_timeout: float = Field(default=60.0, alias="COZE_TIMEOUT")
    coze_poll_interval: float = Field(default=1.0, alias="COZE_POLL_INTERVAL")
    coze_user_id_strategy: str = Field(default="wechat", alias="COZE_USER_ID_STRATEGY")
    """wechat: 'wx-<wechat_user_id>'（截断 64）；hash: 'wx-' + sha1[:16]"""

    @property
    def use_coze(self) -> bool:
        """PAT 与 bot_id 同时设置时启用 Coze。"""

        return bool(self.coze_pat and self.coze_bot_id)

    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8088, alias="PORT")
    auth_token: str = Field(default="", alias="AUTH_TOKEN")

    notify_jobs: str = Field(default="", alias="NOTIFY_JOBS")

    rate_limit_per_minute: int = Field(default=60, alias="RATE_LIMIT_PER_MINUTE")
    """写端点（POST/DELETE）单 IP 每分钟限流。"""

    session_retention_days: int = Field(default=30, alias="SESSION_RETENTION_DAYS")
    """会话与消息数据留存天数，到期自动清除（PIPL 数据最小化要求）。"""

    login_max_refresh: int = Field(default=10, alias="LOGIN_MAX_REFRESH")
    """扫码二维码过期后自动刷新的最大次数；耗尽后登录会话结束。"""

    def is_auth_token_weak(self) -> bool:
        """AUTH_TOKEN 为空或过短视为弱令牌，生产启动时拒绝。"""

        return len(self.auth_token) < 16


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回缓存后的配置实例。"""

    return Settings()
