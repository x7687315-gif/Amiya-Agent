"""入口：启动助手桌面应用。

运行方式（项目根目录）：
    python main.py
或：python ui/app.py
"""
from ui.app import main

import flet as ft

if __name__ == "__main__":
    ft.run(target=main)
