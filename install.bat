@echo off
chcp 65001 >nul
title VolCtrl 安装程序

echo ========================================
echo    VolCtrl - 键盘音量控制工具
echo ========================================
echo.

echo [1/3] 安装依赖...
pip install pycaw keyboard pystray pillow comtypes win10toast -q

echo [2/3] 安装开机自启动...
python "%~dp0VolCtrl.py" --install

echo.
echo ========================================
echo ✅ 安装完成!
echo ========================================
echo.
echo 使用方法:
echo   • Win + Alt + ↑   : 增加音量
echo   • Win + Alt + ↓   : 减少音量
echo   • Win + Alt + M   : 静音/取消静音
echo   • 右键托盘图标: 退出程序
echo.
echo 程序已启动并驻留系统托盘
echo 按任意键打开托盘位置...
pause >nul
