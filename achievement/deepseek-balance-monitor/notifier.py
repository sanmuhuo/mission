"""
Windows 原生通知模块
"""

import sys
from typing import Optional


# winotify 仅 Windows 可用
try:
    from winotify import Notification, audio
    WINOTIFY_AVAILABLE = True
except ImportError:
    WINOTIFY_AVAILABLE = False


APP_ID = "DeepSeek Balance Monitor"
APP_NAME = "DeepSeek 余额监控"


def notify_balance_update(total: float, currency: str = "CNY") -> None:
    """余额更新通知（首次启动时）"""
    if not WINOTIFY_AVAILABLE:
        return
    toast = Notification(
        app_id=APP_ID,
        title=APP_NAME,
        msg=f"当前余额: {total:.2f} {currency}",
        duration="short",
    )
    toast.show()


def notify_low_balance(total: float, threshold: float, currency: str = "CNY") -> None:
    """余额不足警告"""
    if not WINOTIFY_AVAILABLE:
        return
    toast = Notification(
        app_id=APP_ID,
        title=f"⚠️ {APP_NAME} - 余额不足",
        msg=f"当前余额 {total:.2f} {currency}，低于告警阈值 {threshold:.2f} {currency}，请及时充值！",
        duration="long",
    )
    toast.set_audio(audio.Default, loop=False)
    toast.show()


def notify_error(message: str) -> None:
    """查询出错通知"""
    if not WINOTIFY_AVAILABLE:
        return
    toast = Notification(
        app_id=APP_ID,
        title=f"❌ {APP_NAME} - 查询失败",
        msg=message[:200],
        duration="short",
    )
    toast.show()
