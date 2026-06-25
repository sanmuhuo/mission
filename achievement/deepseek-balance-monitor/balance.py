"""
DeepSeek 余额查询 API 客户端
"""

import requests
from dataclasses import dataclass
from typing import Optional


@dataclass
class BalanceInfo:
    """余额信息"""
    is_available: bool
    currency: str
    total_balance: float
    granted_balance: float
    topped_up_balance: float


class BalanceClient:
    """DeepSeek API 余额查询客户端"""

    BASE_URL = "https://api.deepseek.com/user/balance"

    def __init__(self, api_key: str, timeout: int = 15):
        self.api_key = api_key
        self.timeout = timeout

    def get_balance(self) -> BalanceInfo:
        """
        查询账户余额

        Returns:
            BalanceInfo: 余额信息

        Raises:
            ValueError: API key 未配置
            ConnectionError: 网络连接失败
            RuntimeError: API 返回错误
        """
        if not self.api_key:
            raise ValueError(
                "API Key 未配置。请在 config.json 中设置 api_key，"
                "或设置环境变量 DEEPSEEK_API_KEY"
            )

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            resp = requests.get(
                self.BASE_URL,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            raise ConnectionError(
                f"请求超时（{self.timeout}秒），请检查网络连接"
            )
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                "无法连接到 DeepSeek API，请检查网络连接"
            )
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"网络请求失败: {e}")

        if resp.status_code == 401:
            raise RuntimeError(
                "API Key 无效或已过期，请检查 config.json 中的 api_key"
            )
        elif resp.status_code == 429:
            raise RuntimeError(
                "请求过于频繁，请稍后再试"
            )
        elif resp.status_code != 200:
            raise RuntimeError(
                f"API 返回错误 (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        data = resp.json()

        if not data.get("balance_infos"):
            raise RuntimeError(
                f"API 返回数据异常，未找到余额信息: {data}"
            )

        info = data["balance_infos"][0]
        return BalanceInfo(
            is_available=data.get("is_available", False),
            currency=info.get("currency", "CNY"),
            total_balance=float(info.get("total_balance", "0")),
            granted_balance=float(info.get("granted_balance", "0")),
            topped_up_balance=float(info.get("topped_up_balance", "0")),
        )
