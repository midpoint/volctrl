#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VolCtrl - Windows键盘音量控制工具
功能：快捷键调节音量、开机自启、系统托盘驻留
"""
import sys
import os

# PyInstaller 打包支持
if getattr(sys, 'frozen', False):
    os.chdir(sys._MEIPASS)

import ctypes
from ctypes import cast, POINTER

try:
    from comtypes import CLSCTX_ALL
except ImportError:
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
HOTKEY_UP = "win+alt+up"
HOTKEY_DOWN = "win+alt+down"
HOTKEY_MUTE = "win+alt+m"
BAR_WIDTH = 300
BAR_HEIGHT = 20
BAR_DISPLAY_TIME = 1.5  # 音量条显示持续时间（秒）

# ============== 音量条窗口 ==============
class VolumeBar:
    def __init__(self):
        self.root = None
        self.canvas = None
        self.bar_fill = None
        self.text = None
        self.hide_timer = None
        self._ready = False
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
# ============== Windows原生热键管理 ==============
# ============== 热键管理 ==============
class HotkeyManager:
    def __init__(self):
        self.callbacks = {}  # hotkey_str -> callback
        self._running = True
        self._lock = threading.Lock()
        self._pressed_keys = set()
        self._listener = None

        # 启动键盘监听器
        self._start_listener()

    def _start_listener(self):
        """启动键盘监听器"""
        def on_press(key):
            try:
                key_str = self._get_key_str(key)
                if key_str:
                    self._pressed_keys.add(key_str)
                    self._check_hotkeys()
            except:
                pass

        def on_release(key):
            try:
                key_str = self._get_key_str(key)
                if key_str:
                    self._pressed_keys.discard(key_str)
            except:
                pass

        self._listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

    def _get_key_str(self, key):
        if hasattr(key, 'char') and key.char:
            return key.char.lower()
        return str(key)

    def _check_hotkeys(self):
        for hotkey_str, callback in self.callbacks.items():
            if self._match_hotkey(hotkey_str):
                try:
                    callback()
                except:
                    pass

    def _match_hotkey(self, hotkey_str):
        keys = hotkey_str.lower().split('+')
        keys = [k.strip() for k in keys]

        for key in keys:
            if key == 'win':
                if not any('cmd' in k or 'win' in k for k in self._pressed_keys):
                    return False
            elif key == 'alt':
                if not any('alt' in k for k in self._pressed_keys):
                    return False
            elif key == 'up':
                if 'Key.up' not in self._pressed_keys:
                    return False
            elif key == 'down':
                if 'Key.down' not in self._pressed_keys:
                    return False
            elif key == 'm':
                if 'm' not in self._pressed_keys:
                    return False
            else:
                if key not in self._pressed_keys:
                    return False
        return True

    def register(self, hotkey_str, callback):
        with self._lock:
            self.callbacks[hotkey_str] = callback
            print(f"✅ 注册热键: {hotkey_str}")

    def refresh(self):
        pass

# ============== 音量控制类 ==============
class VolumeController:
    def __init__(self, hotkey_mgr=None):
        self.volume_bar = VolumeBar()
        self.hotkey_mgr = hotkey_mgr
        self._init_audio()
    
    def _init_audio(self):
        """初始化音频设备"""
        try:
            devices = AudioUtilities.GetSpeakers()

            # 方式1: 尝试新版pycaw API (使用endpoint属性)
            if hasattr(devices, 'endpoint'):
                self.volume = devices.endpoint
            # 方式2: 尝试通过interface属性
            elif hasattr(devices, 'interface'):
                self.volume = devices.interface
            # 方式3: 尝试旧版Activate方法
            elif CLSCTX_ALL:
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self.volume = cast(interface, POINTER(IAudioEndpointVolume))
            else:
                # 方式4: 使用 -1 代替 CLSCTX_ALL
                interface = devices.Activate(IAudioEndpointVolume._iid_, -1, None)
                self.volume = cast(interface, POINTER(IAudioEndpointVolume))

            self.mute = self.volume.GetMute()
        except Exception as e:
            print(f"音频初始化失败: {e}")
            self.volume = None
    
    def get_volume(self):
        """获取当前音量 (0.0-1.0)"""
        if self.volume:
            return self.volume.GetMasterVolumeLevelScalar()
        return 0.5
    
    def set_volume(self, value):
        """设置音量 (0.0-1.0)"""
        if self.volume:
            value = max(0.0, min(1.0, value))
            self.volume.SetMasterVolumeLevelScalar(value, None)
            return value
        return None
    
    def increase_volume(self):
        """增加音量"""
        current = self.get_volume()
        new_vol = self.set_volume(current + VOLUME_STEP)
        if new_vol:
            self.volume_bar.show(new_vol, self.mute)
        return new_vol

    def decrease_volume(self):
        """减少音量"""
        current = self.get_volume()
        new_vol = self.set_volume(current - VOLUME_STEP)
        if new_vol:
            self.volume_bar.show(new_vol, self.mute)
        return new_vol

    def toggle_mute(self):
        """切换静音状态"""
        if self.volume:
            self.mute = not self.mute
            self.volume.SetMute(self.mute, None)
            current_vol = self.get_volume()
            self.volume_bar.show(current_vol, self.mute)
            return self.mute
        return None

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

# 全局变量用于热键刷新
_hotkey_mgr = None
_volume_ctrl = None

def refresh_hotkeys():
    """刷新显示（pynput 监听器持续工作）"""
    global _hotkey_mgr, _volume_ctrl
    if _hotkey_mgr and _volume_ctrl:
        try:
            current_vol = _volume_ctrl.get_volume()
            _volume_ctrl.volume_bar.show(current_vol, _volume_ctrl.mute)
            print("✅ 音量条已显示")
        except Exception as e:
            print(f"❌ 刷新失败: {e}")

def setup_tray(volume_ctrl):
    """设置系统托盘"""
    icon_image = create_volume_icon(int(volume_ctrl.get_volume() * 100))

    def on_click(icon, item):
        if str(item) == "退出":
            volume_ctrl.volume_bar.destroy()
            if _hotkey_mgr:
                _hotkey_mgr._running = False
            icon.stop()
            os._exit(0)

    menu = Menu(
        MenuItem("显示音量", lambda icon, item: volume_ctrl.volume_bar.show(volume_ctrl.get_volume(), volume_ctrl.mute)),
        MenuItem("增大 (Win+Alt+↑)", lambda icon, item: volume_ctrl.increase_volume()),
        MenuItem("减小 (Win+Alt+↓)", lambda icon, item: volume_ctrl.decrease_volume()),
        MenuItem("静音 (Win+Alt+M)", lambda icon, item: volume_ctrl.toggle_mute()),
        MenuItem("---", lambda icon, item: None),
        MenuItem("重新注册热键", lambda icon, item: refresh_hotkeys()),
        MenuItem("---", lambda icon, item: None),
        MenuItem("退出", on_click)
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
    print(f"""
╔══════════════════════════════════╗
║     {APP_NAME} - 键盘音量控制器       ║
╠══════════════════════════════════╣
║  Win + Alt + ↑  增加音量          ║
║  Win + Alt + ↓  减少音量          ║
║  Win + Alt + M  静音/取消静音     ║
║  右键托盘图标  退出程序            ║
╚══════════════════════════════════╝
    """)

    # 初始化热键管理器
    global _hotkey_mgr, _volume_ctrl
    hotkey_mgr = HotkeyManager()

    # 初始化音量控制器（传入热键管理器）
    volume_ctrl = VolumeController(hotkey_mgr)
    _volume_ctrl = volume_ctrl
    _hotkey_mgr = hotkey_mgr

    # 等待键盘钩子初始化
    time.sleep(0.5)

    # 注册快捷键
    hotkey_mgr.register(HOTKEY_UP, volume_ctrl.increase_volume)
    hotkey_mgr.register(HOTKEY_DOWN, volume_ctrl.decrease_volume)
    hotkey_mgr.register(HOTKEY_MUTE, volume_ctrl.toggle_mute)

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

    # 检查是否以管理员权限运行
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        if not is_admin:
            print("⚠️ 警告：建议以管理员权限运行以确保热键稳定工作")
    except:
        pass
    print("💡 提示：如遇热键失效，可右键托盘图标选择'重新注册热键'")
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
