#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VolCtrl - Windows键盘音量控制工具
功能：快捷键调节音量、自定义热键、开机自启、系统托盘驻留
"""
import sys
import os
import json
import queue

# PyInstaller 打包支持
if getattr(sys, 'frozen', False):
    os.chdir(sys._MEIPASS)

import ctypes
from ctypes import cast, POINTER

try:
    import comtypes
    from comtypes import CLSCTX_ALL
except ImportError:
    comtypes = None
    CLSCTX_ALL = None  # 兼容处理

from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from pynput import keyboard as pynput_keyboard
from pynput.keyboard import Key, KeyCode
import threading
import time
import winreg
import shutil
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw, ImageFont
import tkinter as tk

# ============== 配置 ==============
APP_NAME = "VolCtrl"
ICON_SIZE = 64
VOLUME_STEP = 0.05  # 每次调节5%
BAR_WIDTH = 300
BAR_HEIGHT = 20
BAR_DISPLAY_TIME = 1.5  # 音量条显示持续时间（秒）

# 可自定义的热键动作：action -> 菜单显示名
HOTKEY_ACTIONS = (
    ("volume_up", "增大音量"),
    ("volume_down", "减小音量"),
    ("mute", "静音/取消静音"),
)

DEFAULT_HOTKEYS = {
    "volume_up": "win+alt+up",
    "volume_down": "win+alt+down",
    "mute": "win+alt+m",
}

# ============== 热键字符串解析 ==============

# 修饰键的规范名称与书写顺序
MODIFIER_ORDER = ("win", "ctrl", "alt", "shift")
MODIFIER_CANONICALS = frozenset(MODIFIER_ORDER)

# 所有可以互换的修饰键写法（pynput 的 Key 名 / 用户输入的别名）
_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt_gr": "alt",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "win": "win",
    "windows": "win",
    "cmd": "win",
    "cmd_l": "win",
    "cmd_r": "win",
    "super": "win",
    "super_l": "win",
    "super_r": "win",
    "meta": "win",
}

# 主键别名
_KEY_ALIASES = {
    "escape": "esc",
    "return": "enter",
    "prior": "page_up",
    "next": "page_down",
    "pageup": "page_up",
    "pagedown": "page_down",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "del": "delete",
    "ins": "insert",
    "spacebar": "space",
}

# 界面显示用的名称
_DISPLAY_NAMES = {
    "win": "Win",
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "space": "Space",
    "enter": "Enter",
    "esc": "Esc",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "page_up": "PageUp",
    "page_down": "PageDown",
    "caps_lock": "CapsLock",
    "num_lock": "NumLock",
    "scroll_lock": "ScrollLock",
    "print_screen": "PrtSc",
    "pause": "Pause",
    "menu": "Menu",
}


def _allowed_without_modifier(key):
    """F1-F12、媒体键允许单独作为热键（普通按键单独使用会误触发）"""
    if len(key) > 1 and key[0] == "f" and key[1:].isdigit():
        return True
    return key.startswith("media_")


def parse_hotkey(hotkey_str):
    """把 'Ctrl+Alt+Up' 之类的字符串解析为 (修饰键集合, 主键)。

    无法解析或不满足约束（例如只有单个字母）时抛出 ValueError。
    """
    if not isinstance(hotkey_str, str):
        raise ValueError("热键必须是字符串")

    parts = [p.strip().lower() for p in hotkey_str.split("+")]
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError("热键为空")

    mods = []
    keys = []
    for part in parts:
        modifier = _MODIFIER_ALIASES.get(part)
        if modifier:
            if modifier not in mods:
                mods.append(modifier)
        else:
            keys.append(_KEY_ALIASES.get(part, part))

    if len(keys) != 1:
        raise ValueError("热键必须且只能包含一个主键")
    key = keys[0]
    if not mods and not _allowed_without_modifier(key):
        raise ValueError("热键至少需要一个修饰键（Ctrl/Alt/Shift/Win）")
    return set(mods), key


def format_hotkey(mods, key):
    """按固定顺序拼成规范写法，例如 {'alt','win'}+up -> 'win+alt+up'"""
    ordered = [m for m in MODIFIER_ORDER if m in mods]
    ordered.append(key)
    return "+".join(ordered)


def normalize_hotkey(hotkey_str):
    """校验并规范化为内部写法，非法时抛出 ValueError"""
    mods, key = parse_hotkey(hotkey_str)
    return format_hotkey(mods, key)


def display_hotkey(hotkey_str):
    """转换为界面显示写法，例如 'win+alt+up' -> 'Win+Alt+↑'"""
    try:
        mods, key = parse_hotkey(hotkey_str)
    except ValueError:
        return str(hotkey_str)

    parts = [_DISPLAY_NAMES.get(m, m.capitalize())
             for m in MODIFIER_ORDER if m in mods]
    if len(key) == 1:
        parts.append(key.upper())
    elif key.startswith("f") and key[1:].isdigit():
        parts.append(key.upper())
    else:
        parts.append(_DISPLAY_NAMES.get(key, key.replace("_", " ").title()))
    return "+".join(parts)


# ============== 配置文件 ==============
def get_config_path():
    """配置文件路径：%APPDATA%\\VolCtrl\\config.json"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, "config.json")


def load_config():
    """读取配置，文件缺失或损坏时使用默认值"""
    config = {"hotkeys": dict(DEFAULT_HOTKEYS)}
    path = get_config_path()
    if not os.path.exists(path):
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ 读取配置失败，使用默认热键: {e}")
        return config

    hotkeys = data.get("hotkeys") if isinstance(data, dict) else None
    if isinstance(hotkeys, dict):
        for action, default in DEFAULT_HOTKEYS.items():
            value = hotkeys.get(action)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                config["hotkeys"][action] = normalize_hotkey(value)
            except ValueError as e:
                print(f"⚠️ 配置热键 {action}='{value}' 无效（{e}），使用默认值 {default}")
    return config


def save_config(config):
    """写入配置文件，返回实际路径"""
    path = get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return path


CONFIG = load_config()


# ============== 音量条窗口 ==============
class VolumeBar:
    def __init__(self):
        self.root = None
        self.canvas = None
        self.bar_fill = None
        self.text = None
        self.hide_timer = None
        self._ready = False
        # 从其他线程投递到 Tk 线程执行的任务队列
        self._ui_queue = queue.Queue()
        # 在独立线程中启动tkinter
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # 等待窗口创建完成
        while not self._ready:
            time.sleep(0.01)

    def _run_loop(self):
        """在独立线程中运行tkinter主循环"""
        self._create_window()
        self._ready = True
        self.root.mainloop()

    def _create_window(self):
        """创建音量条窗口"""
        self.root = tk.Tk()
        self.root.overrideredirect(True)  # 无边框
        self.root.attributes("-topmost", True)  # 始终置顶
        self.root.attributes("-alpha", 0.9)  # 半透明

        # 获取屏幕尺寸
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # 计算窗口位置（屏幕底部居中）
        x = (screen_width - BAR_WIDTH) // 2
        y = screen_height - BAR_HEIGHT - 80

        self.root.geometry(f"{BAR_WIDTH}x{BAR_HEIGHT}+{x}+{y}")

        # 创建画布
        self.canvas = tk.Canvas(self.root, width=BAR_WIDTH, height=BAR_HEIGHT,
                                bg='#2D2D2D', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 音量条背景（灰色）
        self.canvas.create_rectangle(2, 2, BAR_WIDTH-2, BAR_HEIGHT-2,
                                       fill='#404040', outline='')

        # 音量条填充
        self.bar_fill = self.canvas.create_rectangle(2, 2, 2, BAR_HEIGHT-2,
                                                      fill='#00BFFF', outline='')

        # 百分比文字
        self.text = self.canvas.create_text(BAR_WIDTH//2, BAR_HEIGHT//2,
                                             text="50%", fill='white',
                                             font=('Arial', 10, 'bold'))

        # 点击关闭
        self.root.bind("<Button-1>", lambda e: self.hide())
        self.root.withdraw()  # 初始隐藏

        # 轮询跨线程 UI 任务
        self.root.after(100, self._poll_ui_queue)

    def _poll_ui_queue(self):
        """在 Tk 线程中执行其他线程投递的任务"""
        try:
            while True:
                func = self._ui_queue.get_nowait()
                try:
                    func()
                except Exception as e:
                    print(f"❌ UI 任务执行失败: {e}")
        except queue.Empty:
            pass

        try:
            if self.root and self.root.winfo_exists():
                self.root.after(100, self._poll_ui_queue)
        except Exception:
            pass

    def run_ui(self, func):
        """线程安全地把任务交给 Tk 线程执行（用于打开设置窗口）"""
        self._ui_queue.put(func)

    def show(self, volume, muted=False):
        """显示音量条"""
        if self.root is None or not self._ready:
            return
        try:
            volume_percent = int(volume * 100)

            # 更新音量条宽度
            fill_width = int((volume_percent / 100) * (BAR_WIDTH - 4)) + 2
            self.canvas.coords(self.bar_fill, 2, 2, fill_width, BAR_HEIGHT-2)

            # 更新文字
            if muted:
                text_content = "静音"
            else:
                text_content = f"{volume_percent}%"
            self.canvas.itemconfig(self.text, text=text_content)

            # 根据音量设置颜色
            if muted:
                color = '#808080'  # 灰色
            elif volume_percent < 30:
                color = '#00FF00'  # 绿色
            elif volume_percent < 70:
                color = '#00BFFF'  # 蓝色
            else:
                color = '#FF4500'  # 橙色（高音量）
            self.canvas.itemconfig(self.bar_fill, fill=color)

            # 显示窗口
            self.root.deiconify()

            # 取消之前的隐藏定时器
            if self.hide_timer:
                self.root.after_cancel(self.hide_timer)

            # 设置隐藏定时器
            self.hide_timer = self.root.after(int(BAR_DISPLAY_TIME * 1000), self.hide)

        except Exception as e:
            print(f"显示音量条失败: {e}")

    def hide(self):
        """隐藏音量条"""
        try:
            if self.root and self.root.winfo_exists():
                self.root.withdraw()
        except:
            pass

    def destroy(self):
        """销毁窗口"""
        try:
            if self.root:
                self.root.quit()
                self.root.destroy()
        except:
            pass

# ============== 热键管理 ==============
class HotkeyManager:
    """基于 pynput 的全局热键管理器。

    热键写法：修饰键 + 主键，例如 'win+alt+up'、'ctrl+shift+m'。
    修饰键支持 ctrl/control、alt、shift、win/cmd/super。
    """

    def __init__(self):
        self._bindings = {}      # 规范热键字符串 -> (修饰键集合, 主键, 回调)
        self._running = True
        self._suspended = False  # 打开设置窗口时暂停普通热键
        self._lock = threading.RLock()
        self._pressed_keys = set()
        self._capture_cb = None  # 捕获热键时的回调
        self._capturing = False
        self._listener = None

        # 启动键盘监听器
        self._start_listener()

    def _start_listener(self):
        """启动键盘监听器"""
        def on_press(key):
            try:
                raw = self._raw_key_token(key)
            except Exception:
                return
            if not raw:
                return

            with self._lock:
                self._pressed_keys.add(raw)
                capturing = self._capturing
                suspended = self._suspended

            if capturing:
                self._capture_press(raw)
                return
            if suspended:
                return
            self._trigger(raw)

        def on_release(key):
            try:
                raw = self._raw_key_token(key)
            except Exception:
                return
            if raw:
                with self._lock:
                    self._pressed_keys.discard(raw)

        self._listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

    @staticmethod
    def _raw_key_token(key):
        """把 pynput 的按键对象转成统一的字符串 token"""
        char = getattr(key, "char", None)
        if char:
            return char.lower()
        if isinstance(key, KeyCode):
            return ""
        name = str(key)
        if name.startswith("Key."):
            name = name[4:]
        return name.lower()

    @staticmethod
    def _canonical_key(token):
        """把原始 token 规范化为内部名称（cmd_l -> win, escape -> esc）"""
        modifier = _MODIFIER_ALIASES.get(token)
        if modifier:
            return modifier
        return _KEY_ALIASES.get(token, token)

    @staticmethod
    def _pressed_modifiers(pressed):
        mods = set()
        for token in pressed:
            modifier = _MODIFIER_ALIASES.get(token)
            if modifier:
                mods.add(modifier)
        return mods

    def register(self, hotkey_str, callback):
        """注册热键；无法解析时抛出 ValueError"""
        mods, key = parse_hotkey(hotkey_str)
        canonical = format_hotkey(mods, key)
        with self._lock:
            self._bindings[canonical] = (set(mods), key, callback)
        print(f"✅ 注册热键: {display_hotkey(canonical)}")

    def clear(self):
        """清空所有已注册热键"""
        with self._lock:
            self._bindings.clear()

    def _trigger(self, raw):
        """当前按下的键是主键时，检查是否匹配某个热键"""
        key = self._canonical_key(raw)
        if key in MODIFIER_CANONICALS:
            return

        with self._lock:
            pressed = set(self._pressed_keys)
            bindings = list(self._bindings.values())

        mods = self._pressed_modifiers(pressed)
        for hot_mods, hot_key, callback in bindings:
            # 修饰键要求完全一致，避免 'alt+up' 被 'ctrl+alt+up' 意外触发
            if hot_key == key and hot_mods == mods:
                try:
                    callback()
                except Exception as e:
                    print(f"❌ 执行热键回调失败: {e}")
                break

    def begin_capture(self, callback):
        """进入热键捕获模式，捕获结果通过 callback(热键字符串或 None) 返回"""
        with self._lock:
            self._capture_cb = callback
            self._capturing = True
            self._pressed_keys.clear()

    def cancel_capture(self):
        """取消捕获"""
        self._finish_capture(None)

    def _capture_press(self, raw):
        key = self._canonical_key(raw)
        if key in MODIFIER_CANONICALS:
            return  # 只按了修饰键，继续等待主键

        with self._lock:
            pressed = set(self._pressed_keys)
        mods = self._pressed_modifiers(pressed)

        if key == "esc" and not mods:
            self._finish_capture(None)  # 单独按 Esc 取消
            return
        if not mods and not _allowed_without_modifier(key):
            return  # 普通按键必须搭配修饰键，继续等待

        self._finish_capture(format_hotkey(mods, key))

    def _finish_capture(self, hotkey):
        with self._lock:
            callback = self._capture_cb
            self._capture_cb = None
            self._capturing = False
        if callback:
            try:
                callback(hotkey)
            except Exception as e:
                print(f"❌ 处理捕获结果失败: {e}")

    def set_suspended(self, suspended):
        """暂停/恢复普通热键响应（设置窗口打开期间）"""
        self._suspended = bool(suspended)

# ============== 音量控制类 ==============
_com_local = threading.local()


def _ensure_com():
    """确保当前线程已初始化 COM。

    pynput 热键回调和托盘菜单都可能在非主线程执行，而 comtypes 只在
    导入它的线程里自动初始化 COM，这里按线程补一次初始化。
    """
    if getattr(_com_local, "ready", False):
        return
    if comtypes is not None:
        try:
            comtypes.CoInitialize()
        except Exception:
            # 该线程已经初始化过 COM（RPC_E_CHANGED_MODE）时忽略
            pass
    _com_local.ready = True


def _get_default_device():
    """获取当前默认播放设备，失败时返回 None"""
    try:
        return AudioUtilities.GetSpeakers()
    except Exception as e:
        print(f"获取默认音频设备失败: {e}")
        return None


def _get_device_id(device):
    """获取设备 ID（兼容不同版本的 pycaw）"""
    if device is None:
        return None
    device_id = getattr(device, "id", None)
    if device_id:
        return device_id
    try:
        return device.GetId()
    except Exception:
        return None


def _create_endpoint_volume(device):
    """从设备对象获取 IAudioEndpointVolume（兼容不同版本的 pycaw）"""
    if device is None:
        return None

    # 新版 pycaw: AudioDevice.EndpointVolume
    try:
        endpoint = getattr(device, "EndpointVolume", None)
    except Exception:
        endpoint = None
    if endpoint is not None:
        return endpoint

    # 部分版本: AudioDevice.endpoint / AudioDevice.interface
    for attr in ("endpoint", "interface"):
        endpoint = getattr(device, attr, None)
        if endpoint is not None:
            return endpoint

    # 旧版 pycaw: IMMDevice.Activate
    if hasattr(device, "Activate"):
        if CLSCTX_ALL is not None:
            interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        else:
            interface = device.Activate(IAudioEndpointVolume._iid_, -1, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    return None


class VolumeController:
    def __init__(self, hotkey_mgr=None):
        self.volume_bar = VolumeBar()
        self.hotkey_mgr = hotkey_mgr
        self.volume = None       # 当前默认设备的 IAudioEndpointVolume
        self._device_id = None   # self.volume 对应的设备 ID
        self._lock = threading.RLock()
        self._init_audio()

    def _init_audio(self):
        """初始化音频设备"""
        with self._lock:
            device = _get_default_device()
            if device is None:
                print("音频初始化失败: 未找到默认播放设备")
                return
            self._device_id = _get_device_id(device)
            self.volume = _create_endpoint_volume(device)
            if self.volume is None:
                print("音频初始化失败: 无法获取音量接口")

    def _ensure_volume(self):
        """返回当前默认设备的音量接口。

        用户手动切换播放设备（声道）后，之前缓存的接口仍指向旧设备，
        继续调节只会改旧设备的音量，听起来就是“音量控制失效”。
        因此每次操作前检查默认设备 ID，发现变化就重新绑定。
        """
        _ensure_com()
        with self._lock:
            device = _get_default_device()

            if device is None:
                if self.volume is None:
                    raise RuntimeError("没有可用的音频输出设备")
                return self.volume  # 枚举暂时失败，继续用上一个接口

            device_id = _get_device_id(device)
            if (self.volume is not None and device_id is not None
                    and device_id == self._device_id):
                return self.volume

            endpoint = _create_endpoint_volume(device)
            if endpoint is None:
                raise RuntimeError("无法获取默认播放设备的音量接口")
            self.volume = endpoint
            self._device_id = device_id
            return self.volume

    def _run_on_volume(self, action):
        """在当前默认设备上执行操作；接口失效（设备切换/拔插）时重新绑定并重试"""
        last_error = None
        for _ in range(2):
            try:
                return action(self._ensure_volume())
            except Exception as e:
                last_error = e
                with self._lock:
                    self.volume = None  # 强制下次重新绑定
        print(f"音量操作失败: {last_error}")
        return None

    @property
    def mute(self):
        """当前静音状态（实时读取，避免设备切换后缓存过期）"""
        return self.is_muted()

    def is_muted(self):
        result = self._run_on_volume(lambda volume: bool(volume.GetMute()))
        return bool(result)

    def get_volume(self):
        """获取当前音量 (0.0-1.0)"""
        result = self._run_on_volume(
            lambda volume: volume.GetMasterVolumeLevelScalar())
        if result is None:
            return 0.5
        return result

    def set_volume(self, value):
        """设置音量 (0.0-1.0)"""
        value = max(0.0, min(1.0, value))
        return self._run_on_volume(lambda volume: self._apply_volume(volume, value))

    @staticmethod
    def _apply_volume(volume, value):
        volume.SetMasterVolumeLevelScalar(value, None)
        return value

    def increase_volume(self):
        """增加音量"""
        return self._change_volume(self.get_volume() + VOLUME_STEP)

    def decrease_volume(self):
        """减少音量"""
        return self._change_volume(self.get_volume() - VOLUME_STEP)

    def _change_volume(self, target):
        new_vol = self.set_volume(target)
        if new_vol is not None:  # 0.0 也是有效音量，不能写成 if new_vol
            self.volume_bar.show(new_vol, self.is_muted())
        return new_vol

    def toggle_mute(self):
        """切换静音状态"""
        new_mute = self._run_on_volume(self._toggle_mute)
        if new_mute is not None:
            self.volume_bar.show(self.get_volume(), new_mute)
        return new_mute

    @staticmethod
    def _toggle_mute(volume):
        new_mute = not bool(volume.GetMute())
        volume.SetMute(new_mute, None)
        return new_mute

# ============== 热键设置窗口 ==============
class HotkeySettingsDialog:
    """Tk 设置窗口：点击「修改」后按下新的组合键即可重新绑定。"""

    def __init__(self, master, hotkey_mgr, on_saved):
        self.hotkey_mgr = hotkey_mgr
        self.on_saved = on_saved
        self.values = dict(CONFIG["hotkeys"])
        self.capturing = None
        self._results = queue.Queue()
        self._value_labels = {}
        self._buttons = {}
        self._closed = False

        self.win = tk.Toplevel(master)
        self.win.title(f"{APP_NAME} - 热键设置")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        container = tk.Frame(self.win, padx=16, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(container, text="点击「修改」后按下新的快捷键组合",
                 anchor="w").pack(fill=tk.X, pady=(0, 8))

        for action, title in HOTKEY_ACTIONS:
            row = tk.Frame(container)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=title, width=12, anchor="w").pack(side=tk.LEFT)

            value_label = tk.Label(
                row, text=display_hotkey(self.values[action]),
                width=20, anchor="w", relief="groove")
            value_label.pack(side=tk.LEFT, padx=(0, 8))
            self._value_labels[action] = value_label

            button = tk.Button(row, text="修改", width=8,
                               command=lambda a=action: self.start_capture(a))
            button.pack(side=tk.LEFT)
            self._buttons[action] = button

        self.status_var = tk.StringVar(value="")
        tk.Label(container, textvariable=self.status_var, fg="#666",
                 anchor="w").pack(fill=tk.X, pady=(8, 6))

        buttons = tk.Frame(container)
        buttons.pack(fill=tk.X)
        tk.Button(buttons, text="恢复默认",
                  command=self.reset_defaults).pack(side=tk.LEFT)
        tk.Button(buttons, text="保存", width=8,
                  command=self.save).pack(side=tk.RIGHT)
        tk.Button(buttons, text="取消", width=8,
                  command=self.close).pack(side=tk.RIGHT)

        self.win.update_idletasks()
        self._center()
        self.hotkey_mgr.set_suspended(True)
        self.win.after(50, self._poll)
        self.win.lift()
        self.win.focus_force()

    def _center(self):
        width = self.win.winfo_width()
        height = self.win.winfo_height()
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        self.win.geometry(f"+{x}+{y}")

    def _poll(self):
        """轮询键盘线程投递的捕获结果"""
        if self._closed:
            return
        try:
            while True:
                result = self._results.get_nowait()
                self._finish_capture(result)
        except queue.Empty:
            pass
        if not self._closed:
            self.win.after(50, self._poll)

    def start_capture(self, action):
        if self.capturing or self._closed:
            return
        self.capturing = action
        self._buttons[action].config(text="按键…", state=tk.DISABLED)
        self.status_var.set("请按下新的快捷键组合（至少一个修饰键，Esc 取消）")
        self.win.focus_force()
        self.hotkey_mgr.begin_capture(lambda hk: self._results.put(hk))

    def _finish_capture(self, hotkey):
        action = self.capturing
        self.capturing = None
        if action is None:
            return

        self._buttons[action].config(text="修改", state=tk.NORMAL)
        if hotkey is None:
            self.status_var.set("已取消修改")
            return

        for other, title in HOTKEY_ACTIONS:
            if other != action and self.values.get(other) == hotkey:
                self.status_var.set(f"该组合已被「{title}」占用，请换一个")
                return

        self.values[action] = hotkey
        self._value_labels[action].config(text=display_hotkey(hotkey))
        self.status_var.set(f"已设置为 {display_hotkey(hotkey)}，点击「保存」生效")

    def reset_defaults(self):
        if self.capturing:
            self.hotkey_mgr.cancel_capture()
            self._finish_capture(None)
        self.values = dict(DEFAULT_HOTKEYS)
        for action, _ in HOTKEY_ACTIONS:
            self._value_labels[action].config(
                text=display_hotkey(self.values[action]))
        self.status_var.set("已恢复默认热键，点击「保存」生效")

    def save(self):
        if self.capturing:
            self.hotkey_mgr.cancel_capture()
            self._finish_capture(None)

        new_config = {"hotkeys": dict(self.values)}
        try:
            path = save_config(new_config)
        except Exception as e:
            self.status_var.set(f"保存失败: {e}")
            return

        CONFIG["hotkeys"] = new_config["hotkeys"]
        try:
            self.on_saved()
        except Exception as e:
            self.status_var.set(f"应用热键失败: {e}")
            return

        print(f"✅ 热键配置已保存: {path}")
        self.close()

    def close(self):
        if self._closed:
            return
        if self.capturing:
            self.hotkey_mgr.cancel_capture()
        self.hotkey_mgr.set_suspended(False)
        self._closed = True
        try:
            self.win.destroy()
        except Exception:
            pass

# ============== 系统通知 ==============
def show_notification(message):
    """显示Windows通知"""
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(APP_NAME, message, duration=1, threaded=True)
    except ImportError:
        # 备用：使用ctypes弹出消息
        pass

# ============== 托盘图标 ==============
def create_volume_icon(volume_level=50):
    """创建音量图标"""
    img = Image.new('RGBA', (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 颜色
    bg_color = (0, 120, 212, 255)  # 蓝色
    fg_color = (255, 255, 255, 255)

    # 画喇叭图形
    # 外圈
    draw.ellipse([8, 20, 24, 44], outline=fg_color, width=3)
    # 喇叭主体
    draw.polygon([(24, 26), (36, 14), (36, 50), (24, 38)], fill=fg_color)
    # 音量条
    bar_count = int(volume_level / 20)
    for i in range(bar_count):
        x = 40 + i * 6
        draw.line([(x, 30 - i*3), (x, 34 + i*3)], fill=fg_color, width=2)

    return img

# 全局变量用于托盘菜单/设置窗口
_hotkey_mgr = None
_volume_ctrl = None


def apply_hotkeys(hotkey_mgr, volume_ctrl):
    """按当前配置注册热键（会清空旧绑定）"""
    callbacks = {
        "volume_up": volume_ctrl.increase_volume,
        "volume_down": volume_ctrl.decrease_volume,
        "mute": volume_ctrl.toggle_mute,
    }
    hotkey_mgr.clear()
    for action, callback in callbacks.items():
        hotkey = CONFIG["hotkeys"].get(action) or DEFAULT_HOTKEYS[action]
        try:
            hotkey_mgr.register(hotkey, callback)
        except ValueError as e:
            default = DEFAULT_HOTKEYS[action]
            print(f"⚠️ 热键「{hotkey}」无效（{e}），改用默认 {default}")
            CONFIG["hotkeys"][action] = default
            hotkey_mgr.register(default, callback)


def reload_config():
    """从磁盘重新读取配置并应用"""
    new_config = load_config()
    CONFIG["hotkeys"] = new_config["hotkeys"]
    if _hotkey_mgr and _volume_ctrl:
        apply_hotkeys(_hotkey_mgr, _volume_ctrl)
    print("✅ 已重新加载热键配置")


def _menu_label(prefix, action):
    """生成动态菜单文字，打开托盘菜单时读取最新热键"""
    def text(item):
        hotkey = CONFIG["hotkeys"].get(action, DEFAULT_HOTKEYS[action])
        return f"{prefix} ({display_hotkey(hotkey)})"
    return text


def setup_tray(volume_ctrl):
    """设置系统托盘"""
    icon_image = create_volume_icon(int(volume_ctrl.get_volume() * 100))

    def on_settings(icon, item):
        if _hotkey_mgr is None or volume_ctrl.volume_bar.root is None:
            return

        def open_dialog():
            bar = volume_ctrl.volume_bar
            existing = getattr(bar, "_settings_dialog", None)
            if existing is not None and not existing._closed:
                existing.win.lift()
                existing.win.focus_force()
                return
            bar._settings_dialog = HotkeySettingsDialog(
                bar.root, _hotkey_mgr,
                lambda: apply_hotkeys(_hotkey_mgr, volume_ctrl))

        volume_ctrl.volume_bar.run_ui(open_dialog)

    def on_exit(icon, item):
        volume_ctrl.volume_bar.destroy()
        if _hotkey_mgr:
            _hotkey_mgr._running = False
        icon.stop()
        os._exit(0)

    menu = Menu(
        MenuItem("显示音量", lambda icon, item: volume_ctrl.volume_bar.show(volume_ctrl.get_volume(), volume_ctrl.mute)),
        MenuItem(_menu_label("增大", "volume_up"), lambda icon, item: volume_ctrl.increase_volume()),
        MenuItem(_menu_label("减小", "volume_down"), lambda icon, item: volume_ctrl.decrease_volume()),
        MenuItem(_menu_label("静音", "mute"), lambda icon, item: volume_ctrl.toggle_mute()),
        MenuItem("---", lambda icon, item: None),
        MenuItem("设置热键…", on_settings),
        MenuItem("重新加载热键配置", lambda icon, item: reload_config()),
        MenuItem("---", lambda icon, item: None),
        MenuItem("退出", on_exit)
    )

    icon = Icon(APP_NAME, icon_image, APP_NAME, menu)
    icon.run_detached()

# ============== 开机自启动 ==============
def get_exe_path():
    """获取可执行文件路径（支持打包后的exe）"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    # Python脚本路径
    return f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'

def install_startup():
    """添加开机自启动"""
    try:
        exe_path = get_exe_path()

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
        winreg.CloseKey(key)
        print(f"✅ 已添加开机自启动: {exe_path}")
        return True
    except Exception as e:
        print(f"❌ 添加开机自启动失败: {e}")
        return False

def uninstall_startup():
    """移除开机自启动"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        print("✅ 已移除开机自启动")
        return True
    except:
        return False

# ============== 主程序 ==============
def main():
    global _hotkey_mgr, _volume_ctrl

    up = display_hotkey(CONFIG["hotkeys"]["volume_up"])
    down = display_hotkey(CONFIG["hotkeys"]["volume_down"])
    mute = display_hotkey(CONFIG["hotkeys"]["mute"])
    print(f"""
=====================================
  {APP_NAME} - 键盘音量控制器
=====================================
  {up}  增大音量
  {down}  减小音量
  {mute}  静音/取消静音
  右键托盘图标可修改热键或退出
=====================================
配置文件: {get_config_path()}
    """)

    # 初始化热键管理器
    hotkey_mgr = HotkeyManager()

    # 初始化音量控制器（传入热键管理器）
    volume_ctrl = VolumeController(hotkey_mgr)
    _volume_ctrl = volume_ctrl
    _hotkey_mgr = hotkey_mgr

    # 等待键盘钩子初始化
    time.sleep(0.5)

    # 注册快捷键（来自配置文件）
    apply_hotkeys(hotkey_mgr, volume_ctrl)

    # 启动热键守护线程（pynput 不需要刷新，保持进程活跃）
    def hotkey_watchdog():
        while hotkey_mgr._running:
            time.sleep(60)
            # pynput 监听器自动工作，只需确认还在运行
    watchdog_thread = threading.Thread(target=hotkey_watchdog, daemon=True)
    watchdog_thread.start()

    # 设置托盘图标
    setup_tray(volume_ctrl)

    # 尝试安装开机自启动
    if "--install" in sys.argv:
        install_startup()
    elif "--uninstall" in sys.argv:
        uninstall_startup()
    else:
        # 首次运行自动安装
        install_startup()

    print(f"✅ {APP_NAME} 已启动，快捷键已注册")
    print("💡 提示：右键托盘图标选择「设置热键…」可自定义快捷键")
    print("按 Ctrl+C 退出")

    # 保持运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 退出程序")
        volume_ctrl.volume_bar.destroy()
        hotkey_mgr._running = False
        sys.exit(0)

if __name__ == "__main__":
    main()
