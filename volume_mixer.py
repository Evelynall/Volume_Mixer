import sys
import tkinter as tk
from tkinter import messagebox
from ctypes import windll

from volume_mixer_gui import VolumeMixerApp
from volume_mixer_service import VolumeMixerService


def main():
    no_gui = len(sys.argv) > 1 and sys.argv[1] in {"--no-gui", "-n"}

    try:
        import pycaw
        import psutil
    except ImportError as error:
        if not no_gui:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("缺少依赖", f"请先安装所需依赖:\n\npip install pycaw psutil comtypes\n\n错误: {error}")
        else:
            print(f"缺少依赖: {error}")
            print("请先安装所需依赖: pip install pycaw psutil comtypes")
        return

    if no_gui:
        service = VolumeMixerService()
        service.run()
        return

    root = tk.Tk()
    try:
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    VolumeMixerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
