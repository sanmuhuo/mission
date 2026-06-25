"""
ZoomScope v1.0 — 游戏准星区域放大镜
======================================
截取主屏幕中心（准星）区域，放大显示在副屏窗口上。
所有参数实时可调，设置自动保存。

依赖: pip install mss pygame Pillow pynput

控件（全局热键，游戏中也生效 —— 需要 pynput）:
  Ctrl+Alt+↑/↓    调整放大倍率 (0.2x 步进)
  Ctrl+Alt+←/→    调整捕获区域大小 (10px 步进)
  Ctrl+Alt+R      重置为默认
  Ctrl+Alt+C      切换准星线
  Ctrl+Alt+I      切换插值算法
  Ctrl+Alt+H      显示/隐藏帮助
  Ctrl+Alt+Q      退出

控件（窗口获得焦点时，无需 pynput，更安全）:
  ↑/↓ / 滚轮      调整放大倍率
  ←/→             调整捕获区域大小
  +/-             微调倍率 (0.1x)
  R                重置
  C                切换准星线
  I                切换插值算法
  H                显示/隐藏帮助
  F                切换无边框/有边框（方便拖拽窗口）
  ESC / Q          退出

反作弊说明:
  本程序只读取屏幕像素，不写入游戏内存。
  建议游戏使用"无边框窗口模式"以避免黑屏。
  如有顾虑，不安装 pynput，只用窗口焦点模式调整参数。
"""

import sys
import os
import json
import time
import ctypes
import threading
from pathlib import Path
from collections import deque

import numpy as np

# ── 依赖检查 ──────────────────────────────────────────────────
missing = []
try:
    import mss
    import mss.tools
except ImportError:
    missing.append("mss")
try:
    from PIL import Image
except ImportError:
    missing.append("Pillow")
try:
    import pygame
    from pygame.locals import *
except ImportError:
    missing.append("pygame")

if missing:
    print(f"缺少依赖: {', '.join(missing)}")
    print(f"请运行: pip install {' '.join(missing)}")
    sys.exit(1)

# pynput 可选
try:
    from pynput import keyboard as pynput_keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False

# ── 配置 ──────────────────────────────────────────────────────
CONFIG_DIR = Path(__file__).parent
CONFIG_PATH = CONFIG_DIR / "config.json"

INTERPOLATION_MODES = [
    ("最近邻", Image.NEAREST),     # 0: 最快，像素块状
    ("双线性", Image.BILINEAR),    # 1: 速度/质量平衡 (默认)
    ("双三次", Image.BICUBIC),     # 2: 更平滑
    ("Lanczos", Image.LANCZOS),    # 3: 最佳质量，稍慢
]

DEFAULT_CONFIG = {
    "capture_size": 200,           # 捕获区域边长 (像素)
    "zoom_level": 4.0,             # 放大倍率
    "interpolation": 1,            # 插值算法索引
    "show_crosshair": True,        # 显示准星线
    "crosshair_color": [255, 0, 0],# 准星线颜色 RGB
    "crosshair_thickness": 1,      # 准星线粗细
    "show_info": True,             # 显示参数信息
    "target_monitor": 1,           # 显示到哪个副屏 (mss 编号)
    "fps_limit": 0,                # 帧率上限，0=不限制
    "global_hotkeys": True,        # 启用全局热键
    "window_borderless": False,    # 无边框模式
    "window_x": None,              # 窗口位置 X (None=自动居中)
    "window_y": None,              # 窗口位置 Y
    "window_width": 600,           # 窗口宽
    "window_height": 600,          # 窗口高
}


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(saved)
            return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ── Windows API 辅助 ──────────────────────────────────────────
def make_window_always_on_top(hwnd):
    """设置窗口置顶"""
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    HWND_TOPMOST = -1
    ctypes.windll.user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE
    )


def make_window_borderless(hwnd):
    """去除窗口边框（备用方案）"""
    GWL_STYLE = -16
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
    style &= ~(WS_CAPTION | WS_THICKFRAME)
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
    ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0020)


# ── 显示器工具 ────────────────────────────────────────────────
def get_monitors():
    """返回 (主屏信息, 副屏列表)"""
    with mss.mss() as sct:
        all_monitors = sct.monitors
    # monitors[0] = 虚拟全屏, [1] = 主屏, [2+] = 副屏
    primary = all_monitors[1] if len(all_monitors) > 1 else None
    secondaries = all_monitors[2:] if len(all_monitors) > 2 else []
    return primary, secondaries, all_monitors


def get_capture_region(monitor, size):
    """计算屏幕中心的正方形捕获区域"""
    cx = monitor["left"] + monitor["width"] // 2
    cy = monitor["top"] + monitor["height"] // 2
    half = size // 2
    return {
        "left": cx - half,
        "top": cy - half,
        "width": size,
        "height": size,
    }


# ── 图像处理 ──────────────────────────────────────────────────
class FrameProcessor:
    def __init__(self, sct, interpolation=Image.BILINEAR):
        self.sct = sct
        self.interpolation = interpolation

    def capture_and_resize(self, region, zoom, out_size):
        """截取屏幕区域并放大，返回 pygame Surface"""
        # 1. mss 截取 → numpy array (BGRA)
        img_data = self.sct.grab(region)
        arr = np.array(img_data, dtype=np.uint8)

        # 2. numpy → PIL (mss 输出 BGRA，通道重排为 RGB)
        pil_img = Image.fromarray(arr[:, :, [2, 1, 0]], "RGB")

        # 3. 放大
        new_size = (out_size[0], out_size[1])
        resized = pil_img.resize(new_size, self.interpolation)

        # 4. PIL → pygame Surface
        mode = resized.mode
        data = resized.tobytes()
        surface = pygame.image.fromstring(data, resized.size, mode)

        return surface


# ── UI 绘制 ───────────────────────────────────────────────────
def draw_crosshair(surface, color, thickness):
    """在表面中央绘制十字准星线"""
    w, h = surface.get_size()
    cx, cy = w // 2, h // 2
    gap = 8
    length = 20
    # 上
    pygame.draw.line(surface, color, (cx, cy - gap), (cx, cy - gap - length), thickness)
    # 下
    pygame.draw.line(surface, color, (cx, cy + gap), (cx, cy + gap + length), thickness)
    # 左
    pygame.draw.line(surface, color, (cx - gap, cy), (cx - gap - length, cy), thickness)
    # 右
    pygame.draw.line(surface, color, (cx + gap, cy), (cx + gap + length, cy), thickness)
    # 中心点
    pygame.draw.circle(surface, color, (cx, cy), 2, 0)


def draw_info(surface, cfg, fps, interpolation_name):
    """在表面底部绘制半透明参数信息"""
    font = pygame.font.SysFont("consolas", 14, bold=True)
    lines = [
        f"FPS: {fps:.0f}  |  捕获: {cfg['capture_size']}px  |  放大: {cfg['zoom_level']:.1f}x  |  插值: {interpolation_name}",
        f"窗口: {surface.get_width()}x{surface.get_height()}  |  拖拽标题栏移动 · 拖拽边缘缩放 · [H]帮助  [↑↓]倍率  [Q]退出",
    ]
    # 半透明背景
    text_height = len(lines) * 18 + 8
    overlay = pygame.Surface((surface.get_width(), text_height), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 128))
    surface.blit(overlay, (0, surface.get_height() - text_height))

    for i, line in enumerate(lines):
        text = font.render(line, True, (255, 255, 255))
        surface.blit(text, (6, surface.get_height() - text_height + 4 + i * 18))


def draw_help(surface):
    """绘制帮助覆盖层"""
    help_lines = [
        "── 控件 ──────────────────────────",
        "",
        "全局热键 (需要 pynput):",
        "  Ctrl+Alt+↑↓   调整倍率",
        "  Ctrl+Alt+←→   调整捕获区域",
        "  Ctrl+Alt+R     重置",
        "  Ctrl+Alt+C     切换准星线",
        "  Ctrl+Alt+I     切换插值",
        "  Ctrl+Alt+H     关闭帮助",
        "  Ctrl+Alt+Q     退出",
        "",
        "窗口焦点控件:",
        "  ↑↓ / 滚轮      调整倍率",
        "  ←→             调整捕获区域",
        "  +/-            微调倍率",
        "  R/C/I/H/F/Q    重置/准星/插值/帮助/切换边框/退出",
        "",
        "── 提示 ──────────────────────────",
        "  建议游戏设为「无边框窗口模式」",
        "  以避免全屏独占时截屏黑屏",
        "",
        "按 H 关闭帮助",
    ]

    overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 200))
    surface.blit(overlay, (0, 0))

    font_title = pygame.font.SysFont("consolas", 16, bold=True)
    font_body = pygame.font.SysFont("consolas", 13)

    y = 20
    for line in help_lines:
        if line.startswith("──"):
            text = font_title.render(line, True, (255, 200, 50))
        elif line == "":
            y += 8
            continue
        else:
            text = font_body.render(line, True, (220, 220, 220))
        surface.blit(text, (20, y))
        y += 20


# ── 全局热键回调 ──────────────────────────────────────────────
class HotkeyHandler:
    """pynput 全局键盘监听，修改配置并通过事件通知主循环"""

    def __init__(self, config_lock, config_ref, event_queue):
        self.config_lock = config_lock
        self.config_ref = config_ref
        self.event_queue = event_queue
        self.listener = None

    def _update(self, **kwargs):
        with self.config_lock:
            for k, v in kwargs.items():
                if k in self.config_ref:
                    self.config_ref[k] = v
        # 通知主循环刷新
        self.event_queue.put(("refresh", None))

    def on_press(self, key):
        try:
            # 检查 Ctrl+Alt 组合键
            is_ctrl = (
                key == pynput_keyboard.Key.ctrl_l
                or key == pynput_keyboard.Key.ctrl_r
            )
            is_alt = (
                key == pynput_keyboard.Key.alt_l
                or key == pynput_keyboard.Key.alt_r
            )

            if is_ctrl or is_alt:
                return  # 仅修饰键，不处理

            # Ctrl+Alt 组合（用 getattr 处理没有 pressed 属性的情况，视为 True）
            ctrl_pressed = getattr(self, "_ctrl_pressed", False)
            alt_pressed = getattr(self, "_alt_pressed", False)

            if ctrl_pressed and alt_pressed:
                # ── Ctrl+Alt+Key ──
                with self.config_lock:
                    cfg = self.config_ref

                if key == pynput_keyboard.Key.up:
                    cfg["zoom_level"] = round(
                        min(cfg["zoom_level"] + 0.2, 10.0), 1
                    )
                    self._update(zoom_level=cfg["zoom_level"])
                elif key == pynput_keyboard.Key.down:
                    cfg["zoom_level"] = round(
                        max(cfg["zoom_level"] - 0.2, 1.0), 1
                    )
                    self._update(zoom_level=cfg["zoom_level"])
                elif key == pynput_keyboard.Key.left:
                    cfg["capture_size"] = max(cfg["capture_size"] - 10, 40)
                    self._update(capture_size=cfg["capture_size"])
                elif key == pynput_keyboard.Key.right:
                    cfg["capture_size"] = min(cfg["capture_size"] + 10, 800)
                    self._update(capture_size=cfg["capture_size"])
                elif hasattr(key, "char"):
                    if key.char == "r":
                        self._update(
                            capture_size=DEFAULT_CONFIG["capture_size"],
                            zoom_level=DEFAULT_CONFIG["zoom_level"],
                            interpolation=DEFAULT_CONFIG["interpolation"],
                        )
                    elif key.char == "c":
                        self._update(
                            show_crosshair=not cfg.get("show_crosshair", True)
                        )
                    elif key.char == "i":
                        with self.config_lock:
                            cur = self.config_ref.get("interpolation", 1)
                        new = (cur + 1) % len(INTERPOLATION_MODES)
                        self._update(interpolation=new)
                    elif key.char == "h":
                        self._update(show_info=not cfg.get("show_info", True))
                    elif key.char == "q":
                        self.event_queue.put(("quit", None))

            # 追踪 Ctrl/Alt 状态
            if key == pynput_keyboard.Key.ctrl_l or key == pynput_keyboard.Key.ctrl_r:
                self._ctrl_pressed = True
            if key == pynput_keyboard.Key.alt_l or key == pynput_keyboard.Key.alt_r:
                self._alt_pressed = True

        except Exception:
            pass

    def on_release(self, key):
        if key == pynput_keyboard.Key.ctrl_l or key == pynput_keyboard.Key.ctrl_r:
            self._ctrl_pressed = False
        if key == pynput_keyboard.Key.alt_l or key == pynput_keyboard.Key.alt_r:
            self._alt_pressed = False

    def start(self):
        self.listener = pynput_keyboard.Listener(
            on_press=self.on_press, on_release=self.on_release
        )
        self.listener.daemon = True
        self.listener.start()

    def stop(self):
        if self.listener:
            self.listener.stop()


# ── 主程序 ────────────────────────────────────────────────────
class ZoomScope:
    def __init__(self):
        self.cfg = load_config()
        self.running = True
        self.showing_help = False
        self.config_lock = threading.Lock()
        self.event_queue = deque()  # 线程安全事件队列 (主线程用)

        # 显示信息
        print("=" * 60)
        print("  ZoomScope v1.0 — 游戏准星区域放大镜")
        print("=" * 60)

        # 检测显示器
        primary, secondaries, all_mons = get_monitors()
        if primary is None:
            print("❌ 未检测到显示器")
            sys.exit(1)

        self.primary = primary
        self.secondaries = secondaries
        self.all_monitors = all_mons

        print(f"  主屏: {primary['width']}x{primary['height']} "
              f"@ ({primary['left']},{primary['top']})")

        target_idx = min(self.cfg["target_monitor"], len(all_mons) - 1)
        if target_idx < 2 and len(all_mons) > 2:
            # 用户可能想要副屏
            target_idx = 2
        self.target_monitor_info = (
            all_mons[target_idx] if target_idx < len(all_mons) else primary
        )
        print(f"  输出: 显示器 {target_idx} "
              f"{self.target_monitor_info['width']}x{self.target_monitor_info['height']}")

        # 初始化屏幕捕获
        self.sct = mss.mss()

        # 初始化 pygame 窗口
        pygame.init()
        self.clock = pygame.time.Clock()
        self._init_window()

        # 图像处理器
        self.processor = FrameProcessor(self.sct)

        # 全局热键
        self.hotkey_handler = None
        if PYNPUT_AVAILABLE and self.cfg.get("global_hotkeys", True):
            self.hotkey_handler = HotkeyHandler(
                self.config_lock, self.cfg, self.event_queue
            )
            self.hotkey_handler.start()
            print("  ⌨  全局热键已启用 (Ctrl+Alt+... 组合键)")
        elif not PYNPUT_AVAILABLE:
            print("  ⚠ pynput 未安装，全局热键禁用")
            print("    请将焦点切换到放大窗口来调整设置")

        print("  按 H 查看帮助，按 Q 退出")
        print("=" * 60)

    def _init_window(self):
        """初始化或重新初始化 pygame 窗口"""
        mon = self.target_monitor_info
        borderless = self.cfg.get("window_borderless", False)

        if borderless:
            # 无边框模式：用上次保存的位置或居中
            win_w = self.cfg.get("window_width", 600)
            win_h = self.cfg.get("window_height", 600)
            if self.cfg.get("window_x") is not None:
                pos_x = self.cfg["window_x"]
                pos_y = self.cfg["window_y"]
            else:
                pos_x = mon["left"] + (mon["width"] - win_w) // 2
                pos_y = mon["top"] + (mon["height"] - win_h) // 2
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{pos_x},{pos_y}"
            self.window = pygame.display.set_mode(
                (win_w, win_h), pygame.NOFRAME
            )
        else:
            # 带边框窗口（可拖拽、可 resize）
            win_w = self.cfg.get("window_width", 600)
            win_h = self.cfg.get("window_height", 600)
            if self.cfg.get("window_x") is not None:
                pos_x = self.cfg["window_x"]
                pos_y = self.cfg["window_y"]
            else:
                pos_x = mon["left"] + (mon["width"] - win_w) // 2
                pos_y = mon["top"] + (mon["height"] - win_h) // 2
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{pos_x},{pos_y}"
            self.window = pygame.display.set_mode(
                (win_w, win_h), pygame.RESIZABLE
            )

        pygame.display.set_caption("ZoomScope — 拖拽标题栏移动，拖拽边缘调整大小")

        # 置顶
        try:
            hwnd = pygame.display.get_wm_info()["window"]
            make_window_always_on_top(hwnd)
        except Exception:
            pass

        self._last_window_pos = None

    def _handle_pygame_events(self):
        """处理 pygame 窗口事件"""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                return

            elif event.type == pygame.KEYDOWN:
                self._handle_key(event.key, event.mod)

            elif event.type == pygame.MOUSEWHEEL:
                # 滚轮调整倍率
                delta = event.y * 0.2
                with self.config_lock:
                    self.cfg["zoom_level"] = round(
                        max(1.0, min(10.0, self.cfg["zoom_level"] + delta)), 1
                    )

            elif event.type == pygame.VIDEORESIZE:
                # 窗口调整大小
                with self.config_lock:
                    self.cfg["window_width"] = event.w
                    self.cfg["window_height"] = event.h
                # 同时保存位置
                self._save_window_position()

    def _handle_key(self, key, mod):
        """处理按键"""
        # 帮助模式下，任意键关闭帮助
        if self.showing_help:
            if key != K_LCTRL and key != K_RCTRL and key != K_LALT and key != K_RALT:
                self.showing_help = False
                return

        with self.config_lock:
            cfg = self.cfg

        if key == K_ESCAPE or key == K_q:
            self.running = False

        elif key == K_UP:
            cfg["zoom_level"] = round(min(cfg["zoom_level"] + 0.2, 10.0), 1)

        elif key == K_DOWN:
            cfg["zoom_level"] = round(max(cfg["zoom_level"] - 0.2, 1.0), 1)

        elif key == K_LEFT:
            cfg["capture_size"] = max(cfg["capture_size"] - 10, 40)

        elif key == K_RIGHT:
            cfg["capture_size"] = min(cfg["capture_size"] + 10, 800)

        elif key == K_PLUS or key == K_EQUALS:
            cfg["zoom_level"] = round(min(cfg["zoom_level"] + 0.1, 10.0), 1)

        elif key == K_MINUS:
            cfg["zoom_level"] = round(max(cfg["zoom_level"] - 0.1, 1.0), 1)

        elif key == K_r:
            cfg["capture_size"] = DEFAULT_CONFIG["capture_size"]
            cfg["zoom_level"] = DEFAULT_CONFIG["zoom_level"]
            cfg["interpolation"] = DEFAULT_CONFIG["interpolation"]

        elif key == K_c:
            cfg["show_crosshair"] = not cfg.get("show_crosshair", True)

        elif key == K_i:
            cfg["interpolation"] = (cfg.get("interpolation", 1) + 1) % len(
                INTERPOLATION_MODES
            )

        elif key == K_h:
            self.showing_help = not self.showing_help

        elif key == K_f:
            self.cfg["window_borderless"] = not self.cfg.get("window_borderless", False)
            # 切换前保存当前窗口位置
            self._save_window_position()
            self._init_window()

    def _save_window_position(self):
        """保存当前窗口位置和大小到配置"""
        try:
            hwnd = pygame.display.get_wm_info()["window"]
            import ctypes.wintypes
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            # 无边框窗口用 GetWindowRect 得到的是客户区外框
            if self.cfg.get("window_borderless", False):
                self.cfg["window_x"] = rect.left
                self.cfg["window_y"] = rect.top
            else:
                # 带边框：GetWindowRect 包含标题栏，需转成客户区坐标
                self.cfg["window_x"] = rect.left
                self.cfg["window_y"] = rect.top
            self.cfg["window_width"] = self.window.get_width()
            self.cfg["window_height"] = self.window.get_height()
            self._last_window_pos = (rect.left, rect.top)
        except Exception:
            pass

    def _process_event_queue(self):
        """处理来自热键线程的事件"""
        while self.event_queue:
            try:
                cmd, _ = self.event_queue.popleft()
            except IndexError:
                break
            if cmd == "quit":
                self.running = False
            elif cmd == "refresh":
                pass  # 配置已更新，刷新时自然生效

    def _get_output_size(self):
        """返回输出窗口的尺寸"""
        return self.window.get_width(), self.window.get_height()

    def run(self):
        """主循环"""
        print("▶ 启动中...")

        # FPS 统计
        fps_history = deque(maxlen=30)
        last_frame_time = time.perf_counter()
        frame_count = 0

        while self.running:
            # 1. 处理事件
            self._handle_pygame_events()
            self._process_event_queue()

            if not self.running:
                break

            # 2. 读取配置 (线程安全)
            with self.config_lock:
                capture_size = self.cfg["capture_size"]
                zoom_level = self.cfg["zoom_level"]
                interp_idx = self.cfg["interpolation"]
                show_crosshair = self.cfg["show_crosshair"]
                crosshair_color = tuple(self.cfg["crosshair_color"])
                crosshair_thick = self.cfg["crosshair_thickness"]
                show_info = self.cfg["show_info"]

            # 3. 计算捕获区域
            capture_half = capture_size // 2
            px = self.primary["left"] + self.primary["width"] // 2 - capture_half
            py = self.primary["top"] + self.primary["height"] // 2 - capture_half

            region = {
                "left": px,
                "top": py,
                "width": capture_size,
                "height": capture_size,
            }

            # 4. 计算输出尺寸
            out_size = self._get_output_size()

            # 5. 更新插值模式
            interp_name, interp_mode = INTERPOLATION_MODES[
                interp_idx % len(INTERPOLATION_MODES)
            ]
            self.processor.interpolation = interp_mode

            # 6. 截取并放大
            try:
                frame = self.processor.capture_and_resize(
                    region, zoom_level, out_size
                )
            except Exception:
                # 截取失败时跳过此帧
                continue

            # 7. 绘制叠加层
            if show_crosshair:
                draw_crosshair(frame, crosshair_color, crosshair_thick)

            # 8. FPS 计算
            now = time.perf_counter()
            dt = now - last_frame_time
            last_frame_time = now
            if dt > 0:
                fps_history.append(1.0 / dt)
            fps = np.mean(fps_history) if fps_history else 0

            # 9. 信息显示
            if show_info:
                draw_info(frame, self.cfg, fps, interp_name)
            if self.showing_help:
                draw_help(frame)

            # 10. 渲染到窗口
            self.window.blit(frame, (0, 0))
            pygame.display.flip()

            # 每 60 帧保存一次窗口位置 + 重新置顶（约 1 秒一次）
            frame_count += 1
            if frame_count % 60 == 0:
                self._save_window_position()
                try:
                    hwnd = pygame.display.get_wm_info()["window"]
                    make_window_always_on_top(hwnd)
                except Exception:
                    pass

            # 11. 帧率控制
            fps_limit = self.cfg.get("fps_limit", 0)
            if fps_limit > 0:
                self.clock.tick(fps_limit)
            else:
                self.clock.tick(120)  # 硬上限 120 FPS

        self.shutdown()

    def shutdown(self):
        """清理资源"""
        print("\n正在退出...")
        # 退出前保存窗口位置
        self._save_window_position()
        if self.hotkey_handler:
            self.hotkey_handler.stop()
        self.sct.close()
        pygame.quit()

        # 保存配置
        save_config(self.cfg)
        print(f"配置已保存到 {CONFIG_PATH}")
        print("ZoomScope 已退出。")


# ── 入口 ───────────────────────────────────────────────────────
def main():
    # 解析命令行参数
    import argparse

    parser = argparse.ArgumentParser(
        description="ZoomScope - 游戏准星区域放大镜",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--monitor", type=int, default=None,
                        help="输出目标显示器编号 (1=主屏, 2=第一副屏, ...)")
    parser.add_argument("--size", type=int, default=None,
                        help="初始捕获区域大小 (像素)")
    parser.add_argument("--zoom", type=float, default=None,
                        help="初始放大倍率")
    parser.add_argument("--no-hotkeys", action="store_true",
                        help="禁用全局热键 (更安全)")
    args = parser.parse_args()

    app = ZoomScope()

    # 应用命令行覆盖
    if args.monitor is not None:
        app.cfg["target_monitor"] = args.monitor
    if args.size is not None:
        app.cfg["capture_size"] = max(40, min(800, args.size))
    if args.zoom is not None:
        app.cfg["zoom_level"] = max(1.0, min(10.0, args.zoom))
    if args.no_hotkeys:
        app.cfg["global_hotkeys"] = False
        if app.hotkey_handler:
            app.hotkey_handler.stop()
            app.hotkey_handler = None
            print("  全局热键已禁用")

    app.run()


if __name__ == "__main__":
    main()
