import math
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from audio_backend import AudioBackend, ROLE_BACKGROUND, ROLE_FOREGROUND, ROLE_NORMAL

try:
    import sv_ttk
    HAS_SV_TTK = True
except ImportError:
    HAS_SV_TTK = False

try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    HAS_SYSTRAY = True
except ImportError:
    HAS_SYSTRAY = False


ROLE_DISPLAY_MAP = {
    ROLE_NORMAL: "常规",
    ROLE_FOREGROUND: "前景音",
    ROLE_BACKGROUND: "背景音",
}

DISPLAY_ROLE_MAP = {value: key for key, value in ROLE_DISPLAY_MAP.items()}


class VolumeMixerApp:
    def __init__(self, root, backend=None):
        self.root = root
        self.backend = backend or AudioBackend()
        self.backend.start()
        settings = self.backend.get_settings()

        self.root.title("音量混合器")
        self.root.geometry("600x750")
        self.root.minsize(600, 750)
        self.root.resizable(True, True)

        self.style = ttk.Style()
        self.current_theme = settings.theme
        self.ui_fps = settings.ui_fps
        self.settings_window = None
        self.session_widgets = {}
        self.secondary_labels = []
        self.last_session_signature = None
        self.last_version = -1
        self.last_applied_theme = None
        self.running = True
        self.systray_icon = None

        self._apply_theme(force=True)
        self._create_ui()
        self._init_systray()
        self.backend.request_refresh()
        self._schedule_ui_update()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _create_ui(self):
        title_frame = ttk.Frame(self.root, padding="10")
        title_frame.pack(fill=tk.X)

        title_label = ttk.Label(
            title_frame,
            text="🔊 音量混合器",
            font=("Microsoft YaHei UI", 16, "bold"),
        )
        title_label.pack(side=tk.LEFT)

        btn_frame = ttk.Frame(title_frame)
        btn_frame.pack(side=tk.RIGHT)

        settings_btn = ttk.Button(btn_frame, text="⚙️", width=3, command=self._open_settings)
        settings_btn.pack(side=tk.RIGHT, padx=(5, 0))

        self.theme_btn = ttk.Button(btn_frame, text="🌙", width=3, command=self._toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT, padx=(5, 0))

        refresh_btn = ttk.Button(btn_frame, text="刷新", command=self._refresh_sessions)
        refresh_btn.pack(side=tk.RIGHT)

        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=10, pady=5)

        canvas_frame = ttk.Frame(self.root)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(canvas_frame, highlightthickness=0, bd=0)
        self._update_canvas_bg()
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)

        self.scrollable_frame = ttk.Frame(self.canvas)
        self.scrollable_frame.bind("<Configure>", lambda e: self._update_scrollregion())

        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.status_var = tk.StringVar(value="正在扫描音频会话...")
        self.status_bar = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, padding="8 5")
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _get_theme_colors(self):
        if self.current_theme == "dark":
            return {
                "bg": "#1c1c1c",
                "meter_bg": "#2a2a2a",
                "meter_tick": "#404040",
                "text_secondary": "#a0a0a0",
            }
        return {
            "bg": "#f3f3f3",
            "meter_bg": "#e8e8e8",
            "meter_tick": "#c0c0c0",
            "text_secondary": "#666666",
        }

    def _apply_theme(self, force=False):
        if not force and self.last_applied_theme == self.current_theme:
            return

        if HAS_SV_TTK:
            sv_ttk.set_theme(self.current_theme)
        else:
            self.style.theme_use("clam")

        colors = self._get_theme_colors()
        self.root.configure(bg=colors["bg"])

        if hasattr(self, "theme_btn"):
            self.theme_btn.config(text="☀️" if self.current_theme == "dark" else "🌙")

        if hasattr(self, "canvas"):
            self._update_canvas_bg()

        for widgets in self.session_widgets.values():
            if "meter_canvas" in widgets:
                self._apply_meter_canvas_style(widgets["meter_canvas"])

        for label in self.secondary_labels:
            try:
                label.configure(foreground=colors["text_secondary"])
            except tk.TclError:
                pass

        self.last_applied_theme = self.current_theme

    def _toggle_theme(self):
        self.backend.toggle_theme()
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme(force=True)

    def _update_canvas_bg(self):
        colors = self._get_theme_colors()
        self.canvas.configure(bg=colors["bg"])

    def _apply_meter_canvas_style(self, canvas):
        colors = self._get_theme_colors()
        canvas.configure(bg=colors["meter_bg"])

    def _style_secondary_label(self, label):
        colors = self._get_theme_colors()
        label.configure(foreground=colors["text_secondary"])
        self.secondary_labels.append(label)

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)
        self._update_scrollregion()

    def _update_scrollregion(self):
        self.canvas.update_idletasks()
        bbox = self.canvas.bbox("all")
        if bbox:
            content_width = bbox[2] - bbox[0]
            content_height = bbox[3] - bbox[1]
            canvas_height = self.canvas.winfo_height()
            self.canvas.configure(scrollregion=(0, 0, content_width, max(content_height, canvas_height)))

    def _on_mousewheel(self, event):
        bbox = self.canvas.bbox("all")
        if bbox:
            content_height = bbox[3] - bbox[1]
            if content_height <= self.canvas.winfo_height():
                return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _init_meter_canvas_items(self, canvas):
        colors = self._get_theme_colors()
        items = {
            "raw_bar": canvas.create_rectangle(0, 0, 0, 0, fill="#1e90ff", outline=""),
            "main_bar": canvas.create_rectangle(0, 0, 0, 0, fill="#4CAF50", outline=""),
            "target_min_line": canvas.create_line(0, 0, 0, 0, fill="#00FF00", width=2),
            "target_max_line": canvas.create_line(0, 0, 0, 0, fill="#FF0000", width=2),
            "ticks": [],
        }
        for i in range(1, 10):
            tick = canvas.create_line(0, 0, 0, 0, fill=colors["meter_tick"], width=1)
            items["ticks"].append(tick)
        canvas.itemconfigure(items["target_min_line"], state="hidden")
        canvas.itemconfigure(items["target_max_line"], state="hidden")
        return items

    def _on_meter_canvas_configure(self, event, process_id):
        widgets = self.session_widgets.get(process_id)
        if not widgets:
            return
        widgets["meter_width"] = event.width
        widgets["meter_height"] = event.height
        for index, tick in enumerate(widgets["meter_items"]["ticks"]):
            x = int(event.width * (index + 1) / 10)
            widgets["meter_canvas"].coords(tick, x, 0, x, event.height)

    def _refresh_sessions(self):
        self.backend.request_refresh()

    def _clear_session_widgets(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.session_widgets.clear()
        self.secondary_labels.clear()

    def _rebuild_session_widgets(self, sessions):
        self._clear_session_widgets()

        if not sessions:
            no_session_label = ttk.Label(
                self.scrollable_frame,
                text="暂无音频会话\n请播放音频后点击刷新",
                font=("Microsoft YaHei UI", 10),
            )
            self._style_secondary_label(no_session_label)
            no_session_label.pack(pady=50)
            return

        for snapshot in sessions:
            self._create_session_widget(snapshot)
        self._update_scrollregion()

    def _create_session_widget(self, snapshot):
        frame = ttk.Frame(self.scrollable_frame, padding="8")
        frame.pack(fill=tk.X)

        top_frame = ttk.Frame(frame)
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text=snapshot.display_name, font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)

        btn_group = ttk.Frame(top_frame)
        btn_group.pack(side=tk.RIGHT)

        expand_btn = ttk.Button(btn_group, text="▼", width=3, command=lambda pid=snapshot.process_id: self._toggle_expand(pid))
        expand_btn.pack(side=tk.RIGHT, padx=(2, 0))

        mute_btn = ttk.Button(btn_group, text="🔇" if snapshot.is_muted else "🔊", width=3, command=lambda pid=snapshot.process_id: self._toggle_mute(pid))
        mute_btn.pack(side=tk.RIGHT, padx=(2, 0))

        role_var = tk.StringVar(value=ROLE_DISPLAY_MAP.get(snapshot.role, "常规"))
        role_combobox = ttk.Combobox(
            btn_group,
            textvariable=role_var,
            values=["常规", "前景音", "背景音"],
            state="readonly",
            width=6,
        )
        role_combobox.pack(side=tk.RIGHT, padx=(2, 0))
        role_combobox.bind("<<ComboboxSelected>>", lambda event, pid=snapshot.process_id, var=role_var: self._on_role_change(pid, var))

        auto_btn = ttk.Button(
            btn_group,
            text="⚡ 托管中" if snapshot.auto_adjust_enabled else "⭘ 未托管",
            width=9,
            command=lambda pid=snapshot.process_id: self._toggle_auto_adjust(pid),
        )
        auto_btn.pack(side=tk.RIGHT, padx=(2, 0))

        slider_frame = ttk.Frame(frame)
        slider_frame.pack(fill=tk.X, pady=(6, 2))

        volume_var = tk.DoubleVar(value=snapshot.live_volume * 100)
        volume_slider = ttk.Scale(
            slider_frame,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=volume_var,
            command=lambda value, pid=snapshot.process_id: self._on_volume_change(pid, float(value)),
        )
        volume_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        volume_percent = ttk.Label(slider_frame, text=f"{int(snapshot.live_volume * 100)}%", width=6, anchor=tk.E)
        volume_percent.pack(side=tk.RIGHT)

        meter_frame = ttk.Frame(frame)
        meter_frame.pack(fill=tk.X, pady=(2, 0))

        meter_canvas = tk.Canvas(meter_frame, height=16, highlightthickness=0, bd=0)
        self._apply_meter_canvas_style(meter_canvas)
        meter_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        meter_items = self._init_meter_canvas_items(meter_canvas)
        meter_canvas.bind("<Configure>", lambda event, pid=snapshot.process_id: self._on_meter_canvas_configure(event, pid))

        peak_label = ttk.Label(meter_frame, text="0.0 dB", width=14, anchor=tk.E)
        peak_label.pack(side=tk.RIGHT)

        expand_frame = ttk.Frame(frame)

        target_frame = ttk.Frame(expand_frame)
        target_frame.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(target_frame, text="目标:").pack(side=tk.LEFT)

        target_min_var = tk.StringVar(value=str(snapshot.target_min_db))
        ttk.Entry(target_frame, textvariable=target_min_var, width=6, justify=tk.CENTER).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(target_frame, text="~").pack(side=tk.LEFT)

        target_max_var = tk.StringVar(value=str(snapshot.target_max_db))
        ttk.Entry(target_frame, textvariable=target_max_var, width=6, justify=tk.CENTER).pack(side=tk.LEFT, padx=2)
        ttk.Label(target_frame, text="dB").pack(side=tk.LEFT, padx=(2, 4))

        adjust_btn_frame = ttk.Frame(target_frame)
        adjust_btn_frame.pack(side=tk.LEFT, padx=(0, 10))

        def adjust_target_range(delta):
            try:
                target_min_var.set(str(float(target_min_var.get()) + delta))
                target_max_var.set(str(float(target_max_var.get()) + delta))
            except ValueError:
                pass

        ttk.Button(adjust_btn_frame, text="◀", width=1.5, command=lambda: adjust_target_range(-1)).pack(side=tk.LEFT)
        ttk.Button(adjust_btn_frame, text="▶", width=1.5, command=lambda: adjust_target_range(1)).pack(side=tk.LEFT, padx=(2, 0))

        ttk.Label(target_frame, text="无声阈值:").pack(side=tk.LEFT)
        min_peak_var = tk.StringVar(value=str(snapshot.min_peak_db))
        ttk.Entry(target_frame, textvariable=min_peak_var, width=6, justify=tk.CENTER).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(target_frame, text="dB").pack(side=tk.LEFT, padx=(2, 0))

        ttk.Button(
            target_frame,
            text="应用",
            width=6,
            command=lambda pid=snapshot.process_id, min_var=target_min_var, max_var=target_max_var, peak_var=min_peak_var: self._apply_target_range(pid, min_var, max_var, peak_var),
        ).pack(side=tk.RIGHT)

        separator = ttk.Separator(self.scrollable_frame, orient="horizontal")
        separator.pack(fill=tk.X, padx=8)

        self.session_widgets[snapshot.process_id] = {
            "frame": frame,
            "separator": separator,
            "volume_var": volume_var,
            "volume_percent": volume_percent,
            "meter_canvas": meter_canvas,
            "meter_items": meter_items,
            "meter_width": 0,
            "meter_height": 0,
            "peak_label": peak_label,
            "mute_btn": mute_btn,
            "auto_btn": auto_btn,
            "target_min_var": target_min_var,
            "target_max_var": target_max_var,
            "min_peak_var": min_peak_var,
            "role_var": role_var,
            "expand_frame": expand_frame,
            "expand_btn": expand_btn,
            "expand_var": tk.BooleanVar(value=False),
            "last_peak": -1.0,
            "last_volume": -1.0,
            "last_role": None,
            "last_auto": None,
            "last_muted": None,
        }

    def _on_volume_change(self, process_id, value):
        self.backend.set_volume(process_id, value / 100.0)
        widgets = self.session_widgets.get(process_id)
        if widgets:
            widgets["volume_percent"].config(text=f"{int(value)}%")

    def _toggle_mute(self, process_id):
        self.backend.toggle_mute(process_id)

    def _toggle_auto_adjust(self, process_id):
        self.backend.toggle_auto_adjust(process_id)

    def _on_role_change(self, process_id, role_var):
        role = DISPLAY_ROLE_MAP.get(role_var.get(), ROLE_NORMAL)
        self.backend.set_role(process_id, role)

    def _toggle_expand(self, process_id):
        widgets = self.session_widgets.get(process_id)
        if not widgets:
            return
        is_expanded = widgets["expand_var"].get()
        if is_expanded:
            widgets["expand_frame"].pack_forget()
            widgets["expand_btn"].config(text="▼")
            widgets["expand_var"].set(False)
        else:
            widgets["expand_frame"].pack(fill=tk.X, pady=4)
            widgets["expand_btn"].config(text="▲")
            widgets["expand_var"].set(True)
        self._update_scrollregion()

    def _apply_target_range(self, process_id, min_var, max_var, min_peak_var):
        try:
            min_db = float(min_var.get())
            max_db = float(max_var.get())
            min_peak = float(min_peak_var.get())
        except ValueError:
            messagebox.showwarning("警告", "请输入有效的数值")
            return

        if min_db >= max_db:
            messagebox.showwarning("警告", "最小值必须小于最大值")
            return
        if min_db < -60 or max_db > 0:
            messagebox.showwarning("警告", "dB范围应在 -60 到 0 之间")
            return
        if min_peak < -60 or min_peak > 0:
            messagebox.showwarning("警告", "最低阈值应在 -60 到 0 dB 之间")
            return

        self.backend.apply_target_range(process_id, min_db, max_db, min_peak)

    def _open_settings(self):
        if self.settings_window is not None:
            try:
                self.settings_window.lift()
                self.settings_window.focus_force()
                return
            except tk.TclError:
                self.settings_window = None
        self._create_settings_window()

    def _create_settings_window(self):
        settings = self.backend.get_settings()
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("设置")
        self.settings_window.geometry("420x380")
        self.settings_window.minsize(400, 360)
        self.settings_window.resizable(True, True)
        self.settings_window.protocol("WM_DELETE_WINDOW", self._close_settings)

        main_frame = ttk.Frame(self.settings_window, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        ui_frame = ttk.LabelFrame(main_frame, text="界面设置", padding="10")
        ui_frame.pack(fill=tk.X, pady=(0, 10))

        fps_frame = ttk.Frame(ui_frame)
        fps_frame.pack(fill=tk.X, pady=2)
        ttk.Label(fps_frame, text="UI帧率:", width=12).pack(side=tk.LEFT)
        self._settings_fps_var = tk.StringVar(value=str(settings.ui_fps))
        ttk.Entry(fps_frame, textvariable=self._settings_fps_var, width=8, justify=tk.CENTER).pack(side=tk.LEFT, padx=5)
        ttk.Label(fps_frame, text="FPS (1-240)").pack(side=tk.LEFT)

        fps_slider_frame = ttk.Frame(ui_frame)
        fps_slider_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(fps_slider_frame, text="", width=12).pack(side=tk.LEFT)
        self._settings_fps_scale = ttk.Scale(fps_slider_frame, from_=1, to=240, orient=tk.HORIZONTAL, command=self._on_fps_slider_change)
        self._settings_fps_scale.set(settings.ui_fps)
        self._settings_fps_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ducking_frame = ttk.LabelFrame(main_frame, text="前景音/背景音设置", padding="10")
        ducking_frame.pack(fill=tk.X, pady=10)

        threshold_frame = ttk.Frame(ducking_frame)
        threshold_frame.pack(fill=tk.X, pady=2)
        ttk.Label(threshold_frame, text="前景音阈值:", width=12).pack(side=tk.LEFT)
        self._settings_threshold_var = tk.StringVar(value=str(settings.foreground_threshold))
        ttk.Entry(threshold_frame, textvariable=self._settings_threshold_var, width=8, justify=tk.CENTER).pack(side=tk.LEFT, padx=5)
        ttk.Label(threshold_frame, text="dB").pack(side=tk.LEFT)
        threshold_hint = ttk.Label(threshold_frame, text="(前景音高于此值时压低背景音)")
        self._style_secondary_label(threshold_hint)
        threshold_hint.pack(side=tk.LEFT, padx=(10, 0))

        ratio_frame = ttk.Frame(ducking_frame)
        ratio_frame.pack(fill=tk.X, pady=(8, 2))
        ttk.Label(ratio_frame, text="背景音比例:", width=12).pack(side=tk.LEFT)
        self._settings_ratio_var = tk.StringVar(value=str(int(settings.background_volume_ratio * 100)))
        ttk.Entry(ratio_frame, textvariable=self._settings_ratio_var, width=8, justify=tk.CENTER).pack(side=tk.LEFT, padx=5)
        ttk.Label(ratio_frame, text="%").pack(side=tk.LEFT)
        ratio_hint = ttk.Label(ratio_frame, text="(背景音相对前景音的音量百分比)")
        self._style_secondary_label(ratio_hint)
        ratio_hint.pack(side=tk.LEFT, padx=(10, 0))

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))
        ttk.Button(btn_frame, text="取消", command=self._close_settings).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="应用", command=self._apply_settings).pack(side=tk.RIGHT)

    def _on_fps_slider_change(self, value):
        self._settings_fps_var.set(str(int(float(value))))

    def _close_settings(self):
        if self.settings_window is not None:
            try:
                self.settings_window.destroy()
            except tk.TclError:
                pass
            self.settings_window = None
            for name in ("_settings_fps_var", "_settings_threshold_var", "_settings_ratio_var", "_settings_fps_scale"):
                if hasattr(self, name):
                    delattr(self, name)

    def _apply_settings(self):
        try:
            fps = int(self._settings_fps_var.get())
            threshold = float(self._settings_threshold_var.get())
            ratio = float(self._settings_ratio_var.get())
        except ValueError:
            messagebox.showwarning("警告", "请输入有效的数值")
            return

        if fps < 1 or fps > 240:
            messagebox.showwarning("警告", "帧率应在 1 到 240 之间")
            return
        if threshold < -60 or threshold > 0:
            messagebox.showwarning("警告", "阈值应在 -60 到 0 dB 之间")
            return
        if ratio < 0 or ratio > 100:
            messagebox.showwarning("警告", "比例应在 0 到 100% 之间")
            return

        self.ui_fps = fps
        self.backend.update_settings(threshold, ratio / 100.0, fps)
        self._close_settings()

    def _sync_sessions(self, sessions, version):
        session_signature = tuple(snapshot.process_id for snapshot in sessions)
        if session_signature != self.last_session_signature:
            self._rebuild_session_widgets(sessions)
            self.last_session_signature = session_signature
        elif version != self.last_version:
            for snapshot in sessions:
                widgets = self.session_widgets.get(snapshot.process_id)
                if not widgets:
                    continue
                widgets["role_var"].set(ROLE_DISPLAY_MAP.get(snapshot.role, "常规"))
                widgets["target_min_var"].set(str(snapshot.target_min_db))
                widgets["target_max_var"].set(str(snapshot.target_max_db))
                widgets["min_peak_var"].set(str(snapshot.min_peak_db))
        self.last_version = version
        self._update_meters(sessions)

    def _update_meters(self, sessions):
        count = 0
        for snapshot in sessions:
            widgets = self.session_widgets.get(snapshot.process_id)
            if not widgets:
                continue
            count += 1

            if widgets["last_role"] != snapshot.role:
                widgets["role_var"].set(ROLE_DISPLAY_MAP.get(snapshot.role, "常规"))
                widgets["last_role"] = snapshot.role

            if widgets["last_auto"] != snapshot.auto_adjust_enabled:
                widgets["auto_btn"].config(text="⚡ 托管中" if snapshot.auto_adjust_enabled else "⭘ 未托管")
                widgets["last_auto"] = snapshot.auto_adjust_enabled

            if widgets["last_muted"] != snapshot.is_muted:
                widgets["mute_btn"].config(text="🔇" if snapshot.is_muted else "🔊")
                widgets["last_muted"] = snapshot.is_muted

            peak = snapshot.live_peak
            current_volume = snapshot.live_volume
            adjusted_peak = peak * current_volume
            width = widgets["meter_width"]
            height = widgets["meter_height"]
            if width <= 1 or height <= 1:
                continue

            peak_changed = abs(peak - widgets["last_peak"]) > 0.005
            vol_changed = abs(current_volume - widgets["last_volume"]) > 0.005
            if not peak_changed and not vol_changed and self._widget_state_matches(snapshot, widgets):
                continue

            widgets["last_peak"] = peak
            widgets["last_volume"] = current_volume
            canvas = widgets["meter_canvas"]
            items = widgets["meter_items"]

            canvas.coords(items["raw_bar"], 0, 0, int(width * peak), height)
            bar_width = int(width * adjusted_peak)
            color = "#4CAF50" if adjusted_peak < 0.5 else "#FFC107" if adjusted_peak < 0.8 else "#F44336"
            canvas.itemconfigure(items["main_bar"], fill=color)
            canvas.coords(items["main_bar"], 0, 0, bar_width, height)

            if snapshot.auto_adjust_enabled:
                target_min_peak = 10 ** (snapshot.target_min_db / 20)
                target_max_peak = 10 ** (snapshot.target_max_db / 20)
                target_min_x = int(width * target_min_peak)
                target_max_x = int(width * target_max_peak)
                if target_min_x < width:
                    canvas.itemconfigure(items["target_min_line"], state="normal")
                    canvas.coords(items["target_min_line"], target_min_x, 0, target_min_x, height)
                else:
                    canvas.itemconfigure(items["target_min_line"], state="hidden")
                if target_max_x < width:
                    canvas.itemconfigure(items["target_max_line"], state="normal")
                    canvas.coords(items["target_max_line"], target_max_x, 0, target_max_x, height)
                else:
                    canvas.itemconfigure(items["target_max_line"], state="hidden")
            else:
                canvas.itemconfigure(items["target_min_line"], state="hidden")
                canvas.itemconfigure(items["target_max_line"], state="hidden")

            if vol_changed:
                widgets["volume_var"].set(current_volume * 100)
                widgets["volume_percent"].config(text=f"{int(current_volume * 100)}%")

            raw_db_text = f"{20 * math.log10(peak):.1f}" if peak > 0 else "-∞"
            adj_db_text = f"{20 * math.log10(adjusted_peak):.1f}" if adjusted_peak > 0 else "-∞"
            widgets["peak_label"].config(text=f"{raw_db_text}/{adj_db_text} dB")

        self.status_var.set(f"当前活动应用: {count} 个")

    def _widget_state_matches(self, snapshot, widgets):
        return (
            widgets["last_role"] == snapshot.role
            and widgets["last_auto"] == snapshot.auto_adjust_enabled
            and widgets["last_muted"] == snapshot.is_muted
        )

    def _schedule_ui_update(self):
        if not self.running:
            return
        settings = self.backend.get_settings()
        theme_changed = settings.theme != self.current_theme
        self.current_theme = settings.theme
        self.ui_fps = settings.ui_fps
        if theme_changed:
            self._apply_theme(force=True)
        sessions, version = self.backend.get_sessions_snapshot()
        self._sync_sessions(sessions, version)
        delay = max(1, int(1000 / max(1, self.ui_fps)))
        self.root.after(delay, self._schedule_ui_update)

    def _init_systray(self):
        if not HAS_SYSTRAY:
            return
        try:
            image = self._create_tray_icon()
            menu = (
                item("显示窗口", self._show_window),
                item("隐藏窗口", self._hide_window),
                item("退出", self._quit_app),
            )
            self.systray_icon = pystray.Icon("音量混合器", image, "音量混合器", menu)
            threading.Thread(target=self._run_systray, daemon=True).start()
        except Exception:
            self.systray_icon = None

    def _create_tray_icon(self):
        image = Image.new("RGB", (64, 64), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse([10, 10, 54, 54], fill=(100, 150, 255), outline=(255, 255, 255))
        draw.ellipse([20, 20, 44, 44], fill=(60, 100, 200))
        draw.arc([5, 5, 59, 59], 0, 360, fill=(150, 180, 255), width=2)
        draw.arc([0, 0, 64, 64], 0, 360, fill=(120, 150, 255), width=1)
        return image

    def _run_systray(self):
        if self.systray_icon:
            self.systray_icon.run()

    def _show_window(self):
        self.root.deiconify()

    def _hide_window(self):
        self.root.withdraw()

    def _quit_app(self):
        self.running = False
        if self.systray_icon:
            self.systray_icon.stop()
        self.backend.stop()
        self.root.destroy()

    def _on_closing(self):
        if HAS_SYSTRAY and self.systray_icon:
            self._hide_window()
        else:
            self._quit_app()
