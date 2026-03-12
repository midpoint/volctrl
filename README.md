# VolCtrl - Windows 键盘音量控制工具

一款 Windows 系统下的音量控制工具，通过键盘快捷键快速调节音量。

## 功能特性

- 🎹 快捷键控制：Win+Alt+↑/↓ 调节音量，Win+Alt+M 静音
- 📊 屏幕底部实时显示音量条
- 🚀 开机自启动
- 📍 系统托盘驻留
- 🔔 音量变化通知
- 💻 高稳定性热键（使用 pynput 库）

## 系统要求

- Windows 10/11
- Python 3.8+
- 需要管理员权限运行（确保热键稳定工作）

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

或手动安装：

```bash
pip install pycaw pynput pystray pillow comtypes win10toast
```

### 运行程序

```bash
python VolCtrl.py
```

> ⚠️ **重要**：请以管理员权限运行程序，以确保热键稳定工作。

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| Win + Alt + ↑ | 增加音量 |
| Win + Alt + ↓ | 减少音量 |
| Win + Alt + M | 静音/取消静音 |
| 右键托盘图标 | 退出程序 |

## 音量条说明

- 调节音量时，屏幕底部会显示音量条
- 颜色提示：
  - 🟢 绿色：低音量 (0-30%)
  - 🔵 蓝色：中音量 (30-70%)
  - 🟠 橙色：高音量 (70-100%)
  - ⚪ 灰色：静音状态
- 点击音量条可将其隐藏
- 音量条 1.5 秒后自动消失

## 开机自启动

首次运行后会自动添加开机启动，或手动运行 `install.bat`

## 打包成 EXE

```bash
build.bat
```

打包后的文件位于 `dist` 目录下。

## 项目结构

```
volctrl/
├── VolCtrl.py          # 主程序
├── README.md           # 说明文档
├── requirements.txt    # 依赖列表
├── build.bat           # 打包脚本
├── install.bat         # 安装脚本
├── uninstall.bat       # 卸载脚本
└── VolCtrl.spec        # PyInstaller 配置
```

## 常见问题

**Q: 热键失效怎么办？**
A: 请确保以管理员权限运行程序。如果仍有问题，可右键托盘图标选择"重新注册热键"。

**Q: 如何卸载？**
A: 运行 `uninstall.bat` 或手动删除开机启动项。

## License

MIT License
