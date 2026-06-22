"""
Windows音量混合器 - 显示和调节各应用音量及实时音量
使用Windows Core Audio API实现
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import math
import json
import os
import sys
from ctypes import cast, POINTER, windll, oledll
from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
from pycaw.pycaw import (
    AudioUtilities, 
    IAudioMeterInformation, 
    IAudioSessionManager2,
    IAudioSessionControl,
    ISimpleAudioVolume
)
from pycaw.constants import CLSID_MMDeviceEnumerator

# 配置文件路径
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'volume_mixer_config.json')

try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    HAS_SYSTRAY = True
except ImportError:
    HAS_SYSTRAY = False


class AudioSession:
    """音频会话类，封装单个应用的音频信息"""
    
    def __init__(self, session_control, process_id, device_name=""):
        self.session_control = session_control
        self.device_name = device_name
        self.volume = session_control.QueryInterface(ISimpleAudioVolume)
        self.meter = session_control.QueryInterface(IAudioMeterInformation)
        self.process_id = process_id
        self.display_name = self._get_display_name()
        self.is_muted = False
        
        # 自动调节相关属性（默认值）
        self.auto_adjust_enabled = False
        self.target_min_db = -30.0  # 目标最小dB值
        self.target_max_db = -20.0  # 目标最大dB值
        self.adjustment_speed = 0.02  # 调节速度（每次调节的步长）
    
    def load_config(self, config):
        """从配置中加载自动调节设置"""
        if 'auto_adjust_enabled' in config:
            self.auto_adjust_enabled = config['auto_adjust_enabled']
        if 'target_min_db' in config:
            self.target_min_db = config['target_min_db']
        if 'target_max_db' in config:
            self.target_max_db = config['target_max_db']
    
    def get_config(self):
        """获取当前配置"""
        return {
            'auto_adjust_enabled': self.auto_adjust_enabled,
            'target_min_db': self.target_min_db,
            'target_max_db': self.target_max_db
        }
        
    def _get_display_name(self):
        """获取应用的显示名称"""
        try:
            import psutil
            if self.process_id > 0:
                process = psutil.Process(self.process_id)
                return process.name()
        except:
            pass
        return f"进程 {self.process_id}" if self.process_id > 0 else "系统声音"
    
    def get_volume(self):
        """获取当前音量 (0.0 - 1.0)"""
        try:
            return self.volume.GetMasterVolume()
        except:
            return 0.0
    
    def set_volume(self, level):
        """设置音量 (0.0 - 1.0)"""
        try:
            self.volume.SetMasterVolume(level, None)
        except:
            pass
    
    def get_peak(self):
        """获取实时音量峰值 (0.0 - 1.0)"""
        try:
            peak = self.meter.GetPeakValue()
            return peak
        except:
            return 0.0
    
    def toggle_mute(self):
        """切换静音状态"""
        try:
            self.is_muted = not self.is_muted
            self.volume.SetMute(self.is_muted, None)
        except:
            pass
    
    def auto_adjust_volume(self):
        """自动调节音量以保持目标电平范围"""
        if not self.auto_adjust_enabled:
            return
        
        try:
            peak = self.get_peak()
            current_volume = self.get_volume()
            
            if peak <= 0:
                return
            
            # 计算当前实际输出电平（dB）
            adjusted_peak = peak * current_volume
            if adjusted_peak <= 0:
                return
            
            current_db = 20 * math.log10(adjusted_peak)
            
            # 判断是否需要调节
            if current_db < self.target_min_db:
                # 电平过低，需要提高音量
                # 计算需要的音量增益
                target_peak = 10 ** (self.target_min_db / 20)
                required_volume = target_peak / peak
                
                # 限制音量范围在0.01到1.0之间
                required_volume = max(0.01, min(1.0, required_volume))
                
                # 逐步调节（避免突然变化）
                if current_volume < required_volume:
                    new_volume = min(current_volume + self.adjustment_speed, required_volume)
                    self.set_volume(new_volume)
                    
            elif current_db > self.target_max_db:
                # 电平过高，需要降低音量
                # 计算需要的音量衰减
                target_peak = 10 ** (self.target_max_db / 20)
                required_volume = target_peak / peak
                
                # 限制音量范围在0.01到1.0之间
                required_volume = max(0.01, min(1.0, required_volume))
                
                # 逐步调节（避免突然变化）
                if current_volume > required_volume:
                    new_volume = max(current_volume - self.adjustment_speed, required_volume)
                    self.set_volume(new_volume)
                    
        except Exception as e:
            pass


class VolumeMixerApp:
    """音量混合器主应用"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("音量混合器")
        self.root.geometry("750x700")
        self.root.minsize(700, 500)
        self.root.resizable(True, True)
        
        # 设置窗口图标和样式
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        # 存储音频会话和UI元素
        self.audio_sessions = {}
        self.session_widgets = {}
        self.running = True
        
        # 加载配置
        self.config = self._load_config()
        
        # 创建主界面
        self._create_ui()
        
        # 初始化系统托盘
        self._init_systray()
        
        # 启动时立即刷新会话列表（加载配置并显示应用）
        self._refresh_sessions()
        
        # 启动音频监控线程
        self.monitor_thread = threading.Thread(target=self._monitor_audio, daemon=True)
        self.monitor_thread.start()
        
        # 启动UI更新线程
        self.update_thread = threading.Thread(target=self._update_ui, daemon=True)
        self.update_thread.start()
        
        # 窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
    
    def _create_ui(self):
        """创建用户界面"""
        # 标题
        title_frame = ttk.Frame(self.root, padding="10")
        title_frame.pack(fill=tk.X)
        
        title_label = ttk.Label(
            title_frame, 
            text="🔊 音量混合器", 
            font=('Microsoft YaHei UI', 16, 'bold')
        )
        title_label.pack(side=tk.LEFT)
        
        # 刷新按钮
        refresh_btn = ttk.Button(
            title_frame, 
            text="刷新", 
            command=self._refresh_sessions
        )
        refresh_btn.pack(side=tk.RIGHT)
        
        # 分隔线
        ttk.Separator(self.root, orient='horizontal').pack(fill=tk.X, padx=10, pady=5)
        
        # 可滚动的应用列表
        canvas_frame = ttk.Frame(self.root)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        
        self.scrollable_frame = ttk.Frame(self.canvas)
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 绑定鼠标滚轮
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
        # 状态栏
        self.status_var = tk.StringVar(value="正在扫描音频会话...")
        status_bar = ttk.Label(
            self.root, 
            textvariable=self.status_var, 
            relief=tk.SUNKEN, 
            anchor=tk.W,
            padding="5"
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
    
    def _on_mousewheel(self, event):
        """鼠标滚轮事件处理"""
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    
    def _get_audio_sessions(self):
        """获取所有音频会话"""
        sessions = {}
        
        try:
            # 在每个线程中初始化COM
            CoInitialize()
            
            try:
                # 使用pycaw提供的简单方法获取所有音频会话
                audio_sessions = AudioUtilities.GetAllSessions()
                
                # 遍历所有会话
                for session in audio_sessions:
                    try:
                        # 获取进程ID
                        process_id = session.ProcessId
                        
                        if process_id > 0:
                            # 创建音频会话对象
                            audio_session = AudioSession(session._ctl, process_id, "默认设备")
                            sessions[process_id] = audio_session
                            
                    except Exception as e:
                        continue
                        
            finally:
                # 清理COM
                CoUninitialize()
                    
        except Exception as e:
            print(f"获取音频会话失败: {e}")
            import traceback
            traceback.print_exc()
        
        return sessions
    
    def _refresh_sessions(self):
        """刷新音频会话列表"""
        self.audio_sessions = self._get_audio_sessions()
        
        # 为每个会话加载保存的配置
        for pid, session in self.audio_sessions.items():
            if session.display_name in self.config:
                session.load_config(self.config[session.display_name])
        
        self._update_session_widgets()
    
    def _update_session_widgets(self):
        """更新会话控件"""
        # 清除现有控件
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        
        self.session_widgets.clear()
        
        if not self.audio_sessions:
            no_session_label = ttk.Label(
                self.scrollable_frame, 
                text="暂无音频会话\n请播放音频后点击刷新",
                font=('Microsoft YaHei UI', 10),
                foreground='gray'
            )
            no_session_label.pack(pady=50)
            return
        
        # 为每个会话创建控件
        for pid, session in self.audio_sessions.items():
            self._create_session_widget(session)
    
    def _create_session_widget(self, session):
        """为单个音频会话创建控件"""
        frame = ttk.LabelFrame(
            self.scrollable_frame, 
            text=session.display_name,
            padding="10"
        )
        frame.pack(fill=tk.X, pady=5, padx=5)
        
        # 第一行：应用名称和静音按钮
        top_frame = ttk.Frame(frame)
        top_frame.pack(fill=tk.X)
        
        # 应用图标和名称
        name_label = ttk.Label(
            top_frame, 
            text=f"🎵 {session.display_name}",
            font=('Microsoft YaHei UI', 10, 'bold')
        )
        name_label.pack(side=tk.LEFT)
        
        # 静音按钮
        mute_btn = ttk.Button(
            top_frame, 
            text="🔊", 
            width=3,
            command=lambda s=session: self._toggle_mute(s)
        )
        mute_btn.pack(side=tk.RIGHT)
        
        # 第二行：音量滑块
        slider_frame = ttk.Frame(frame)
        slider_frame.pack(fill=tk.X, pady=5)
        
        volume_label = ttk.Label(slider_frame, text="音量:", width=6)
        volume_label.pack(side=tk.LEFT)
        
        volume_var = tk.DoubleVar(value=session.get_volume() * 100)
        volume_slider = ttk.Scale(
            slider_frame, 
            from_=0, 
            to=100, 
            orient=tk.HORIZONTAL,
            variable=volume_var,
            command=lambda v, s=session: self._on_volume_change(s, float(v))
        )
        volume_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        volume_percent = ttk.Label(slider_frame, text="100%", width=5)
        volume_percent.pack(side=tk.RIGHT)
        
        # 第三行：实时音量表
        meter_frame = ttk.Frame(frame)
        meter_frame.pack(fill=tk.X, pady=5)
        
        meter_label = ttk.Label(meter_frame, text="电平:", width=6)
        meter_label.pack(side=tk.LEFT)
        
        # 音量表画布
        meter_canvas = tk.Canvas(
            meter_frame, 
            height=20, 
            bg='#2b2b2b',
            highlightthickness=1,
            highlightbackground='#555555'
        )
        meter_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # 实时音量数值
        peak_label = ttk.Label(meter_frame, text="0.0 dB", width=14)
        peak_label.pack(side=tk.RIGHT)
        
        # 第四行：自动调节功能
        auto_frame = ttk.Frame(frame)
        auto_frame.pack(fill=tk.X, pady=5)
        
        # 自动调节开关
        auto_label = ttk.Label(auto_frame, text="自动:", width=6)
        auto_label.pack(side=tk.LEFT)
        
        # 根据配置设置自动调节按钮状态
        auto_btn_text = "开启" if session.auto_adjust_enabled else "关闭"
        auto_btn = ttk.Button(
            auto_frame, 
            text=auto_btn_text, 
            width=6,
            command=lambda s=session: self._toggle_auto_adjust(s)
        )
        auto_btn.pack(side=tk.LEFT, padx=5)
        
        # 目标电平范围设置
        ttk.Label(auto_frame, text="目标范围:", width=8).pack(side=tk.LEFT)
        
        target_min_var = tk.StringVar(value=str(session.target_min_db))
        target_min_entry = ttk.Entry(
            auto_frame, 
            textvariable=target_min_var, 
            width=6,
            justify=tk.CENTER
        )
        target_min_entry.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(auto_frame, text="~").pack(side=tk.LEFT)
        
        target_max_var = tk.StringVar(value=str(session.target_max_db))
        target_max_entry = ttk.Entry(
            auto_frame, 
            textvariable=target_max_var, 
            width=6,
            justify=tk.CENTER
        )
        target_max_entry.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(auto_frame, text="dB").pack(side=tk.LEFT)
        
        # 应用目标范围按钮
        apply_btn = ttk.Button(
            auto_frame, 
            text="应用", 
            width=6,
            command=lambda s=session, min_var=target_min_var, max_var=target_max_var: 
                self._apply_target_range(s, min_var, max_var)
        )
        apply_btn.pack(side=tk.RIGHT)
        
        # 存储控件引用
        self.session_widgets[session.process_id] = {
            'frame': frame,
            'volume_var': volume_var,
            'volume_slider': volume_slider,
            'volume_percent': volume_percent,
            'meter_canvas': meter_canvas,
            'peak_label': peak_label,
            'mute_btn': mute_btn,
            'auto_btn': auto_btn,
            'target_min_var': target_min_var,
            'target_max_var': target_max_var,
            'session': session
        }
    
    def _on_volume_change(self, session, value):
        """音量滑块变化事件"""
        session.set_volume(value / 100.0)
        # 更新百分比显示
        if session.process_id in self.session_widgets:
            widgets = self.session_widgets[session.process_id]
            widgets['volume_percent'].config(text=f"{int(value)}%")
    
    def _toggle_mute(self, session):
        """切换静音状态"""
        session.toggle_mute()
        # 更新按钮显示
        if session.process_id in self.session_widgets:
            widgets = self.session_widgets[session.process_id]
            btn_text = "🔇" if session.is_muted else "🔊"
            widgets['mute_btn'].config(text=btn_text)
    
    def _toggle_auto_adjust(self, session):
        """切换自动调节状态"""
        session.auto_adjust_enabled = not session.auto_adjust_enabled
        # 更新按钮显示
        if session.process_id in self.session_widgets:
            widgets = self.session_widgets[session.process_id]
            btn_text = "开启" if session.auto_adjust_enabled else "关闭"
            widgets['auto_btn'].config(text=btn_text)
    
    def _apply_target_range(self, session, min_var, max_var):
        """应用目标电平范围"""
        try:
            min_db = float(min_var.get())
            max_db = float(max_var.get())
            
            # 验证范围有效性
            if min_db >= max_db:
                messagebox.showwarning("警告", "最小值必须小于最大值")
                return
            
            if min_db < -60 or max_db > 0:
                messagebox.showwarning("警告", "dB范围应在 -60 到 0 之间")
                return
            
            session.target_min_db = min_db
            session.target_max_db = max_db
            
        except ValueError:
            messagebox.showwarning("警告", "请输入有效的数值")
    
    def _monitor_audio(self):
        """音频监控线程"""
        while self.running:
            try:
                # 定期刷新会话列表
                new_sessions = self._get_audio_sessions()
                
                # 检查是否有新会话
                if set(new_sessions.keys()) != set(self.audio_sessions.keys()):
                    self.audio_sessions = new_sessions
                    # 在主线程中更新UI
                    self.root.after(0, self._update_session_widgets)
                
                time.sleep(2)  # 每2秒检查一次新会话
                
            except Exception as e:
                print(f"监控线程错误: {e}")
                time.sleep(1)
    
    def _update_ui(self):
        """UI更新线程"""
        while self.running:
            try:
                # 更新实时音量表
                self.root.after(0, self._update_meters)
                time.sleep(0.05)  # 20 FPS更新率
                
            except Exception as e:
                print(f"UI更新错误: {e}")
                time.sleep(0.1)
    
    def _update_meters(self):
        """更新所有音量表"""
        for pid, widgets in self.session_widgets.items():
            try:
                session = widgets['session']
                peak = session.get_peak()
                
                # 获取当前音量设置
                volume_level = session.get_volume()
                
                # 自动调节音量（如果已启用）
                session.auto_adjust_volume()
                
                # 更新音量滑块显示（如果自动调节改变了音量）
                current_volume = session.get_volume()
                if abs(current_volume - volume_level) > 0.01:
                    widgets['volume_var'].set(current_volume * 100)
                    widgets['volume_percent'].config(text=f"{int(current_volume * 100)}%")
                
                # 计算实际输出电平（原始电平 * 音量设置）
                adjusted_peak = peak * current_volume
                
                # 更新音量表
                canvas = widgets['meter_canvas']
                canvas.delete("all")
                
                # 获取画布宽度
                canvas.update_idletasks()
                width = canvas.winfo_width()
                height = canvas.winfo_height()
                
                if width > 1:
                    # 绘制原始电平背景条（浅蓝色，显示原始音频电平）
                    raw_bar_width = int(width * peak)
                    canvas.create_rectangle(
                        0, 0, raw_bar_width, height,
                        fill='#1e90ff', outline=''
                    )
                    
                    # 绘制实际输出电平条（显示音量调节后的电平）
                    bar_width = int(width * adjusted_peak)
                    
                    # 根据音量级别选择颜色
                    if adjusted_peak < 0.5:
                        color = '#4CAF50'  # 绿色
                    elif adjusted_peak < 0.8:
                        color = '#FFC107'  # 黄色
                    else:
                        color = '#F44336'  # 红色
                    
                    canvas.create_rectangle(
                        0, 0, bar_width, height,
                        fill=color, outline=''
                    )
                    
                    # 绘制目标范围指示线（如果启用了自动调节）
                    if session.auto_adjust_enabled:
                        # 计算目标范围的像素位置
                        target_min_peak = 10 ** (session.target_min_db / 20)
                        target_max_peak = 10 ** (session.target_max_db / 20)
                        
                        target_min_x = int(width * target_min_peak)
                        target_max_x = int(width * target_max_peak)
                        
                        # 绘制目标最小值线（绿色）
                        if target_min_x < width:
                            canvas.create_line(
                                target_min_x, 0, target_min_x, height,
                                fill='#00FF00', width=2
                            )
                        
                        # 绘制目标最大值线（红色）
                        if target_max_x < width:
                            canvas.create_line(
                                target_max_x, 0, target_max_x, height,
                                fill='#FF0000', width=2
                            )
                    
                    # 绘制刻度线
                    for i in range(1, 10):
                        x = int(width * i / 10)
                        canvas.create_line(
                            x, 0, x, height,
                            fill='#555555', width=1
                        )
                
                # 更新dB显示（同时显示原始和实际输出）
                if peak > 0:
                    raw_db = 20 * math.log10(peak) if peak > 0 else -60
                    raw_db_text = f"{raw_db:.1f}"
                else:
                    raw_db_text = "-∞"
                
                if adjusted_peak > 0:
                    adj_db = 20 * math.log10(adjusted_peak) if adjusted_peak > 0 else -60
                    adj_db_text = f"{adj_db:.1f}"
                else:
                    adj_db_text = "-∞"
                
                widgets['peak_label'].config(text=f"{raw_db_text}/{adj_db_text} dB")
                
            except Exception as e:
                continue
        
        # 更新状态栏
        self.status_var.set(f"当前活动应用: {len(self.session_widgets)} 个")
    
    def _init_systray(self):
        """初始化系统托盘"""
        if not HAS_SYSTRAY:
            return
        
        self.systray_icon = None
        try:
            # 创建托盘图标
            image = self._create_tray_icon()
            
            # 创建菜单
            menu = (
                item('显示窗口', self._show_window),
                item('隐藏窗口', self._hide_window),
                item('退出', self._quit_app)
            )
            
            # 创建托盘图标
            self.systray_icon = pystray.Icon("音量混合器", image, "音量混合器", menu)
            
            # 在后台运行托盘图标
            threading.Thread(target=self._run_systray, daemon=True).start()
            
        except Exception as e:
            print(f"系统托盘初始化失败: {e}")
    
    def _create_tray_icon(self):
        """创建托盘图标"""
        # 创建一个简单的图标（喇叭形状）
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # 绘制喇叭形状
        # 喇叭底座
        draw.ellipse([10, 10, 54, 54], fill=(100, 150, 255), outline=(255, 255, 255))
        # 喇叭中心
        draw.ellipse([20, 20, 44, 44], fill=(60, 100, 200))
        # 喇叭声音波纹
        draw.arc([5, 5, 59, 59], 0, 360, fill=(150, 180, 255), width=2)
        draw.arc([0, 0, 64, 64], 0, 360, fill=(120, 150, 255), width=1)
        
        return image
    
    def _run_systray(self):
        """运行系统托盘"""
        if self.systray_icon:
            self.systray_icon.run()
    
    def _show_window(self):
        """显示窗口"""
        self.root.deiconify()
    
    def _hide_window(self):
        """隐藏窗口到托盘"""
        self.root.withdraw()
    
    def _quit_app(self):
        """退出应用"""
        self._save_config()
        self.running = False
        if self.systray_icon:
            self.systray_icon.stop()
        self.root.destroy()
    
    def _on_closing(self):
        """窗口关闭事件 - 最小化到托盘"""
        if HAS_SYSTRAY and self.systray_icon:
            self._hide_window()
        else:
            self._quit_app()
    
    def _load_config(self):
        """加载配置文件"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"加载配置失败: {e}")
        return {}
    
    def _save_config(self):
        """保存配置文件"""
        try:
            # 收集所有会话的配置
            config = {}
            
            # 保存当前会话的配置
            for pid, session in self.audio_sessions.items():
                config[session.display_name] = session.get_config()
            
            # 写入配置文件
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            print(f"配置已保存到 {CONFIG_FILE}")
        except Exception as e:
            print(f"保存配置失败: {e}")


class VolumeMixerService:
    """无UI模式的音量混合器后台服务"""
    
    def __init__(self):
        self.audio_sessions = {}
        self.config = {}
        self.running = True
        
        # 加载配置
        self.config = self._load_config()
        print("配置已加载")
        
        # 立即刷新会话并应用配置
        self._refresh_sessions()
        
        # 启动监控线程
        self.monitor_thread = threading.Thread(target=self._monitor_audio, daemon=True)
        self.monitor_thread.start()
    
    def _load_config(self):
        """加载配置文件"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"加载配置失败: {e}")
        return {}
    
    def _save_config(self):
        """保存配置文件"""
        try:
            config = {}
            for pid, session in self.audio_sessions.items():
                config[session.display_name] = session.get_config()
            
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            print(f"配置已保存到 {CONFIG_FILE}")
        except Exception as e:
            print(f"保存配置失败: {e}")
    
    def _get_audio_sessions(self):
        """获取所有音频会话"""
        sessions = {}
        
        try:
            CoInitialize()
            
            try:
                audio_sessions = AudioUtilities.GetAllSessions()
                
                for session in audio_sessions:
                    try:
                        process_id = session.ProcessId
                        
                        if process_id > 0:
                            audio_session = AudioSession(session._ctl, process_id, "默认设备")
                            sessions[process_id] = audio_session
                            
                    except Exception as e:
                        continue
                        
            finally:
                CoUninitialize()
                    
        except Exception as e:
            print(f"获取音频会话失败: {e}")
        
        return sessions
    
    def _refresh_sessions(self):
        """刷新音频会话列表"""
        self.audio_sessions = self._get_audio_sessions()
        
        # 为每个会话加载保存的配置
        for pid, session in self.audio_sessions.items():
            if session.display_name in self.config:
                session.load_config(self.config[session.display_name])
                if session.auto_adjust_enabled:
                    print(f"已为 {session.display_name} 启用自动调节 (目标范围: {session.target_min_db} ~ {session.target_max_db} dB)")
    
    def _monitor_audio(self):
        """音频监控线程"""
        while self.running:
            try:
                # 更新会话列表
                new_sessions = self._get_audio_sessions()
                
                # 检查是否有新会话
                if set(new_sessions.keys()) != set(self.audio_sessions.keys()):
                    self.audio_sessions = new_sessions
                    for pid, session in self.audio_sessions.items():
                        if session.display_name in self.config:
                            session.load_config(self.config[session.display_name])
                
                # 执行自动调节
                for pid, session in self.audio_sessions.items():
                    session.auto_adjust_volume()
                
                time.sleep(0.1)  # 10 FPS 更新率
                
            except Exception as e:
                print(f"监控线程错误: {e}")
                time.sleep(1)
    
    def run(self):
        """运行服务（阻塞）"""
        print("音量混合器后台服务已启动...")
        print("按 Ctrl+C 退出")
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在退出...")
            self.stop()
    
    def stop(self):
        """停止服务"""
        self.running = False
        self._save_config()
        print("服务已停止")


def main():
    """主函数"""
    # 检查命令行参数
    no_gui = False
    if len(sys.argv) > 1:
        if sys.argv[1] == '--no-gui' or sys.argv[1] == '-n':
            no_gui = True
    
    # 检查依赖
    try:
        import pycaw
        import psutil
    except ImportError as e:
        if not no_gui:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "缺少依赖", 
                f"请先安装所需依赖:\n\npip install pycaw psutil comtypes\n\n错误: {e}"
            )
        else:
            print(f"缺少依赖: {e}")
            print("请先安装所需依赖: pip install pycaw psutil comtypes")
        return
    
    # 初始化COM
    try:
        CoInitialize()
    except:
        pass
    
    if no_gui:
        # 无UI模式
        service = VolumeMixerService()
        service.run()
    else:
        # 创建主窗口
        root = tk.Tk()
        
        # 设置DPI感知
        try:
            windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass
        
        # 创建应用
        app = VolumeMixerApp(root)
        
        # 运行主循环
        root.mainloop()
    
    # 清理COM
    try:
        CoUninitialize()
    except:
        pass


if __name__ == "__main__":
    main()