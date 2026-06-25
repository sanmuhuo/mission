"""
DeepSeek 余额监控 — Windows 系统托盘应用

启动后在系统托盘显示 DeepSeek 账户余额，定时刷新并记录历史。
余额低于阈值时弹出 Windows 原生通知告警。

使用方法：
    python main.py

首次使用请在 config.json 中填写 api_key，或设置环境变量 DEEPSEEK_API_KEY。
"""

import json
import os
import threading
import time

from PIL import Image, ImageDraw
import pystray

from balance import BalanceClient
from storage import (
    init_db,
    save_balance,
    get_latest_balance,
    get_balance_history,
    cleanup_old_records,
)
from notifier import (
    notify_balance_update,
    notify_low_balance,
    notify_error,
    WINOTIFY_AVAILABLE,
)

APP_NAME = "DeepSeek 余额监控"
APP_ID = "deepseek_balance_monitor"
ICON_SIZE = 64

# 获取项目根目录
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_startup_vbs_path() -> str:
    """Windows 启动文件夹下 vbs 脚本的完整路径"""
    startup_dir = os.path.join(
        os.environ["APPDATA"],
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )
    return os.path.join(startup_dir, "DeepSeekBalanceMonitor.vbs")


def _write_startup_vbs() -> None:
    """在启动文件夹写入自启脚本"""
    vbs_path = _get_startup_vbs_path()
    # 需要找到 pythonw.exe；优先用 Miniconda，其次当前解释器
    pythonw = os.path.join(os.path.dirname(os.path.dirname(os.__file__)), "pythonw.exe")
    if not os.path.exists(pythonw):
        # 回退：Miniconda 常见路径
        for candidate in [
            r"D:\Miniconda3\pythonw.exe",
            r"C:\ProgramData\miniconda3\pythonw.exe",
            r"C:\Users\mcjdh\AppData\Local\Programs\Python\Python313\pythonw.exe",
        ]:
            if os.path.exists(candidate):
                pythonw = candidate
                break

    content = (
        f'Set ws = CreateObject("WScript.Shell")\n'
        f'ws.CurrentDirectory = "{_PROJECT_DIR}"\n'
        f'ws.Run "{pythonw} main.py", 0, False\n'
    )
    with open(vbs_path, "w", encoding="ascii") as f:
        f.write(content)


def _remove_startup_vbs() -> None:
    """删除启动文件夹下的自启脚本"""
    vbs_path = _get_startup_vbs_path()
    if os.path.exists(vbs_path):
        os.remove(vbs_path)


def _is_autostart_enabled() -> bool:
    return os.path.exists(_get_startup_vbs_path())


# ── 图标生成 ─────────────────────────────────────────────

# 预生成各颜色图标，避免每次查询都重新绘制
_ICON_CACHE = {}


def _create_icon(color: str) -> Image.Image:
    """生成一个纯色圆形图标"""
    if color in _ICON_CACHE:
        return _ICON_CACHE[color]

    size = ICON_SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆形背景
    margin = 3
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
        outline="white",
        width=2,
    )

    _ICON_CACHE[color] = img
    return img


# ── 配置 ──────────────────────────────────────────────────


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_api_key(config: dict) -> str:
    """优先从环境变量读取 API Key，其次从配置文件"""
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    return config.get("api_key", "").strip()


# ── 主应用 ───────────────────────────────────────────────


class BalanceMonitorApp:
    def __init__(self):
        self.config = load_config()
        self.api_key = get_api_key(self.config)
        self.interval = max(self.config.get("check_interval_minutes", 5), 1) * 60
        self.threshold = float(self.config.get("alert_threshold", 10.0))
        self.currency = self.config.get("currency", "CNY")

        # 当前余额状态
        self.current_balance: dict | None = None
        self.is_available: bool = True
        self.last_error: str | None = None

        # 托盘图标引用
        self.icon: pystray.Icon | None = None

        # 停止信号
        self._stop_event = threading.Event()

        # 首次余额通知已发送
        self._first_notify_done = False

        # 初始化数据库
        init_db()
        # 尝试加载最近一次记录
        latest = get_latest_balance()
        if latest:
            self.current_balance = {
                "total_balance": latest["total_balance"],
                "granted_balance": latest["granted_balance"],
                "topped_up_balance": latest["topped_up_balance"],
                "currency": latest["currency"],
            }

    # ── 图标 ──

    def _pick_icon_color(self) -> str:
        """根据当前余额状态选择图标颜色"""
        if self.current_balance is None:
            return "#808080"  # 灰色：尚未获取数据
        balance = self.current_balance["total_balance"]
        if balance <= self.threshold:
            return "#FF4444"  # 红色：余额不足
        elif balance <= self.threshold * 2:
            return "#FFAA00"  # 黄色：余额偏低
        else:
            return "#44BB44"  # 绿色：余额充足

    def _update_icon_and_menu(self) -> None:
        """刷新托盘图标和菜单文字"""
        if self.icon is None:
            return
        self.icon.icon = _create_icon(self._pick_icon_color())
        self.icon.menu = self._build_menu()
        self.icon.update_menu()

    # ── 右键菜单 ──

    def _build_menu(self) -> pystray.Menu:
        """构建右键菜单 —— 余额数字置于最显眼位置"""
        if self.current_balance is not None:
            b = self.current_balance
            status = "✅" if self.is_available else "⚠️"
            # 主余额：大字突出显示
            main_line = (
                f"{status} 余额  ¥ {b['total_balance']:,.2f}  {b['currency']}"
            )
            # 明细：充值 + 赠送
            detail_line = (
                f"    充值 ¥{b['topped_up_balance']:,.2f}"
                f"  +  赠送 ¥{b['granted_balance']:,.2f}"
            )
        elif self.last_error is not None:
            main_line = f"❌ {self.last_error[:30]}"
            detail_line = "查询出错，请检查网络或 API Key"
        else:
            main_line = "⏳ 余额查询中..."
            detail_line = "等待首次数据返回"

        _noop = lambda: None  # 占位回调，让菜单项保持正常字体颜色
        autostart_text = (
            "✅ 开机自启: 开" if _is_autostart_enabled() else "⬜ 开机自启: 关"
        )
        return pystray.Menu(
            pystray.MenuItem(main_line, _noop, enabled=True),
            pystray.MenuItem(detail_line, _noop, enabled=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 立即刷新", self._on_refresh),
            pystray.MenuItem("📊 历史记录", self._on_view_history),
            pystray.MenuItem(autostart_text, self._on_toggle_autostart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ 退出", self._on_exit),
        )

    # ── 菜单回调 ──

    def _on_refresh(self, icon, item):
        """手动刷新余额"""
        threading.Thread(target=self._check_balance, daemon=True).start()

    def _on_view_history(self, icon, item):
        """查看最近余额历史"""
        records = get_balance_history(30)
        if not records:
            self._show_info("暂无历史记录，等待首次数据采集...")
            return

        lines = [
            "DeepSeek 余额历史记录",
            "=" * 60,
            f"{'时间':<20} {'总额':>10} {'充值':>10} {'赠送':>10}",
            "-" * 60,
        ]
        for r in records:
            ts = r["timestamp"][:19]
            lines.append(
                f"{ts:<20} {r['total_balance']:>10.4f} "
                f"{r['topped_up_balance']:>10.4f} {r['granted_balance']:>10.4f}"
            )

        # 写入临时文件并用记事本打开
        temp_path = os.path.join(os.path.dirname(__file__), "history_temp.txt")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.startfile(temp_path)
        except Exception as e:
            self._show_info(f"无法打开历史记录: {e}")

    def _on_toggle_autostart(self, icon, item):
        """切换开机自启状态"""
        if _is_autostart_enabled():
            _remove_startup_vbs()
            self._show_info("已关闭开机自启")
        else:
            _write_startup_vbs()
            self._show_info("已开启开机自启\n下次开机时将自动启动")
        self._update_icon_and_menu()

    def _on_exit(self, icon, item):
        """退出应用"""
        self._stop_event.set()
        icon.stop()

    def _show_info(self, message: str):
        """显示信息（通过通知或弹窗）"""
        if WINOTIFY_AVAILABLE:
            from winotify import Notification

            Notification(
                app_id=APP_ID,
                title=APP_NAME,
                msg=message[:250],
                duration="short",
            ).show()
        else:
            # 回退到 print
            print(message)

    # ── 余额查询 ──

    def _check_balance(self) -> None:
        """执行一次余额查询并更新状态"""
        try:
            client = BalanceClient(self.api_key)
            info = client.get_balance()

            prev_balance = self.current_balance
            self.current_balance = {
                "total_balance": info.total_balance,
                "granted_balance": info.granted_balance,
                "topped_up_balance": info.topped_up_balance,
                "currency": info.currency,
            }
            self.is_available = info.is_available
            self.last_error = None

            # 存入数据库
            save_balance(**self.current_balance, is_available=info.is_available)

            # 更新托盘显示
            self._update_icon_and_menu()

            # 首次查询成功通知
            if not self._first_notify_done:
                self._first_notify_done = True
                notify_balance_update(info.total_balance, info.currency)

            # 余额低于阈值告警（避免重复告警）
            if prev_balance is not None and info.total_balance <= self.threshold:
                if prev_balance["total_balance"] > self.threshold:
                    notify_low_balance(
                        info.total_balance, self.threshold, info.currency
                    )

            # 定期清理 90 天前的旧数据
            if prev_balance is not None:  # 非首次
                cleanup_old_records(keep_days=90)

        except ValueError as e:
            # API Key 未配置 —— 仅首次提醒
            self.last_error = str(e)[:50]
            self._update_icon_and_menu()
            if not self._first_notify_done:
                notify_error(str(e))
        except ConnectionError as e:
            self.last_error = "网络连接失败"
            self._update_icon_and_menu()
        except RuntimeError as e:
            self.last_error = str(e)[:50]
            self._update_icon_and_menu()
            notify_error(str(e))
        except Exception as e:
            self.last_error = f"未知错误: {str(e)[:40]}"
            self._update_icon_and_menu()

    # ── 后台定时循环 ──

    def _run_checker(self):
        """后台线程：定时查询余额"""
        # 启动后等 3 秒再查，避免影响启动速度
        time.sleep(3)
        self._check_balance()

        while not self._stop_event.is_set():
            self._stop_event.wait(self.interval)
            if not self._stop_event.is_set():
                self._check_balance()

    # ── 启动 ──

    def run(self):
        """启动托盘应用（阻塞主线程）"""
        initial_icon = _create_icon(self._pick_icon_color())

        self.icon = pystray.Icon(
            APP_ID,
            initial_icon,
            APP_NAME,
            menu=self._build_menu(),
        )

        # 启动后台查询线程
        checker = threading.Thread(target=self._run_checker, daemon=True)
        checker.start()

        # 进入托盘事件循环（阻塞）
        self.icon.run()


# ── 入口 ──────────────────────────────────────────────────


def main():
    app = BalanceMonitorApp()
    app.run()


if __name__ == "__main__":
    main()
