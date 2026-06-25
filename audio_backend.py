import json
import math
import os
import threading
import time
import urllib.request
import urllib.error
from dataclasses import asdict, dataclass
from queue import Empty, Queue
from typing import Dict, List, Optional, Tuple

import psutil
from comtypes import CoInitialize, CoUninitialize
from pycaw.pycaw import AudioUtilities, IAudioMeterInformation, ISimpleAudioVolume

__version__ = "1.2.1"

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "volume_mixer_config.json")

ROLE_NORMAL = "normal"
ROLE_FOREGROUND = "foreground"
ROLE_BACKGROUND = "background"


@dataclass
class GlobalSettings:
    foreground_threshold: float = -40.0
    background_volume_ratio: float = 0.3
    theme: str = "dark"
    ui_fps: int = 60


@dataclass
class SessionSnapshot:
    session_id: str
    process_id: int
    display_name: str
    config_key: str
    role: str
    auto_adjust_enabled: bool
    target_min_db: float
    target_max_db: float
    min_peak_db: float
    live_volume: float
    live_peak: float
    is_muted: bool
    is_ducked: bool


class ConfigStore:
    def __init__(self, config_file: str = CONFIG_FILE):
        self.config_file = config_file

    def load(self):
        settings = GlobalSettings()
        session_configs: Dict[str, dict] = {}
        legacy_lookup: Dict[str, dict] = {}

        if not os.path.exists(self.config_file):
            return settings, session_configs, legacy_lookup

        try:
            with open(self.config_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception:
            return settings, session_configs, legacy_lookup

        if isinstance(data, dict) and "sessions" in data:
            global_config = data.get("global", {})
            self._apply_global_settings(settings, global_config)
            sessions = data.get("sessions", {})
            if isinstance(sessions, dict):
                for key, value in sessions.items():
                    if isinstance(value, dict):
                        session_configs[key] = value
            return settings, session_configs, legacy_lookup

        if isinstance(data, dict):
            global_config = data.get("__global__", {})
            self._apply_global_settings(settings, global_config)
            for key, value in data.items():
                if key == "__global__":
                    continue
                if isinstance(value, dict):
                    legacy_lookup[key.lower()] = value

        return settings, session_configs, legacy_lookup

    def save(self, settings: GlobalSettings, sessions: List["AudioSession"]):
        payload = {
            "global": asdict(settings),
            "sessions": {
                session.config_key: {
                    **session.get_config(),
                    "display_name": session.display_name,
                }
                for session in sessions
            },
        }
        with open(self.config_file, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)

    @staticmethod
    def _apply_global_settings(settings: GlobalSettings, config: dict):
        if not isinstance(config, dict):
            return
        if "foreground_threshold" in config:
            settings.foreground_threshold = config["foreground_threshold"]
        if "background_volume_ratio" in config:
            settings.background_volume_ratio = config["background_volume_ratio"]
        if "theme" in config:
            settings.theme = config["theme"]
        if "ui_fps" in config:
            settings.ui_fps = config["ui_fps"]


class SubSession:
    """单个音频会话实例的控制接口"""

    def __init__(self, session_control, session_id: str):
        self.session_control = session_control
        self.session_id = session_id
        self.volume = session_control.QueryInterface(ISimpleAudioVolume)
        self.meter = session_control.QueryInterface(IAudioMeterInformation)

    def get_volume(self):
        try:
            return float(self.volume.GetMasterVolume())
        except Exception:
            return 0.0

    def set_volume(self, level: float):
        try:
            bounded = max(0.0, min(1.0, level))
            self.volume.SetMasterVolume(bounded, None)
        except Exception:
            pass

    def get_peak(self):
        try:
            return float(self.meter.GetPeakValue())
        except Exception:
            return 0.0

    def get_mute(self):
        try:
            return bool(self.volume.GetMute())
        except Exception:
            return False

    def set_mute(self, muted: bool):
        try:
            self.volume.SetMute(muted, None)
        except Exception:
            pass


class AudioSession:
    """聚合同一进程的所有音频会话"""

    def __init__(self, process_id: int):
        self.process_id = process_id
        self.sub_sessions: List[SubSession] = []
        self.executable_path = self._get_executable_path()
        self.display_name = self._get_display_name()
        self.config_key = self._build_config_key()
        self.is_muted = False
        self.auto_adjust_enabled = False
        self.target_min_db = -30.0
        self.target_max_db = -20.0
        self.adjustment_speed = 0.02
        self.min_peak_db = -40.0
        self.role = ROLE_NORMAL
        self.saved_volume: Optional[float] = None
        self.is_ducked = False
        self.live_peak = 0.0
        self.live_volume = 0.0

    def add_sub_session(self, session_control, session_id: str):
        sub = SubSession(session_control, session_id)
        self.sub_sessions.append(sub)

    def _get_process(self):
        if self.process_id <= 0:
            return None
        try:
            return psutil.Process(self.process_id)
        except Exception:
            return None

    def _get_executable_path(self):
        process = self._get_process()
        if not process:
            return ""
        try:
            return process.exe()
        except Exception:
            return ""

    def _get_display_name(self):
        process = self._get_process()
        if process:
            try:
                return process.name()
            except Exception:
                pass
        return f"进程 {self.process_id}" if self.process_id > 0 else "系统声音"

    def _build_config_key(self):
        if self.executable_path:
            return f"exe:{self.executable_path.lower()}"
        if self.process_id <= 0:
            return "system:sounds"
        return f"name:{self.display_name.lower()}"

    def load_config(self, config: dict):
        if not isinstance(config, dict):
            return
        if "auto_adjust_enabled" in config:
            self.auto_adjust_enabled = bool(config["auto_adjust_enabled"])
        if "target_min_db" in config:
            self.target_min_db = float(config["target_min_db"])
        if "target_max_db" in config:
            self.target_max_db = float(config["target_max_db"])
        if "min_peak_db" in config:
            self.min_peak_db = float(config["min_peak_db"])
        if "role" in config:
            self.role = config["role"]

    def get_config(self):
        return {
            "auto_adjust_enabled": self.auto_adjust_enabled,
            "target_min_db": self.target_min_db,
            "target_max_db": self.target_max_db,
            "min_peak_db": self.min_peak_db,
            "role": self.role,
        }

    def refresh_runtime(self):
        self.live_volume = self.get_volume()
        self.live_peak = self.get_peak()
        self.is_muted = self.get_mute()

    def get_volume(self):
        if not self.sub_sessions:
            return 0.0
        return self.sub_sessions[0].get_volume()

    def set_volume(self, level: float):
        for sub in self.sub_sessions:
            sub.set_volume(level)
        self.live_volume = level

    def get_peak(self):
        max_peak = 0.0
        for sub in self.sub_sessions:
            peak = sub.get_peak()
            if peak > max_peak:
                max_peak = peak
        return max_peak

    def get_mute(self):
        if not self.sub_sessions:
            return False
        return self.sub_sessions[0].get_mute()

    def toggle_mute(self):
        target = not self.get_mute()
        for sub in self.sub_sessions:
            sub.set_mute(target)
        self.is_muted = target

    def auto_adjust_volume(self):
        if not self.auto_adjust_enabled:
            return

        peak = self.live_peak
        current_volume = self.live_volume

        if peak <= 0:
            return

        peak_db = self._peak_to_db(peak)
        if peak_db < self.min_peak_db:
            return

        adjusted_peak = peak * current_volume
        if adjusted_peak <= 0:
            return

        current_db = self._peak_to_db(adjusted_peak)

        if current_db < self.target_min_db:
            target_peak = 10 ** (self.target_min_db / 20)
            required_volume = max(0.01, min(1.0, target_peak / peak))
            if current_volume < required_volume:
                self.set_volume(min(current_volume + self.adjustment_speed, required_volume))
        elif current_db > self.target_max_db:
            target_peak = 10 ** (self.target_max_db / 20)
            required_volume = max(0.01, min(1.0, target_peak / peak))
            if current_volume > required_volume:
                self.set_volume(max(current_volume - self.adjustment_speed, required_volume))

    @staticmethod
    def _peak_to_db(value: float):
        if value <= 0:
            return -60.0
        return 20 * math.log10(value)

    def to_snapshot(self):
        return SessionSnapshot(
            session_id=str(self.process_id),
            process_id=self.process_id,
            display_name=self.display_name,
            config_key=self.config_key,
            role=self.role,
            auto_adjust_enabled=self.auto_adjust_enabled,
            target_min_db=self.target_min_db,
            target_max_db=self.target_max_db,
            min_peak_db=self.min_peak_db,
            live_volume=self.live_volume,
            live_peak=self.live_peak,
            is_muted=self.is_muted,
            is_ducked=self.is_ducked,
        )


class AudioBackend:
    def __init__(self, config_store: Optional[ConfigStore] = None):
        self.config_store = config_store or ConfigStore()
        self.settings, self.persisted_session_configs, self.legacy_session_configs = self.config_store.load()
        self.audio_sessions: Dict[int, AudioSession] = {}
        self.lock = threading.RLock()
        self.command_queue: Queue = Queue()
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.pending_refresh = True
        self.version = 0
        self.last_saved_at = 0.0
        self.save_interval = 0.25
        self.dirty = False

    def start(self):
        if self.running:
            return
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=2)
        self._save_config(force=True)

    def request_refresh(self):
        self.pending_refresh = True

    def get_settings(self):
        with self.lock:
            return GlobalSettings(**asdict(self.settings))

    def get_sessions_snapshot(self):
        with self.lock:
            sessions = [session.to_snapshot() for session in self.audio_sessions.values()]
            sessions.sort(key=lambda item: item.display_name.lower())
            return sessions, self.version

    def set_volume(self, process_id: int, level: float):
        self.command_queue.put(("set_volume", process_id, level))

    def toggle_mute(self, process_id: int):
        self.command_queue.put(("toggle_mute", process_id))

    def toggle_auto_adjust(self, process_id: int):
        self.command_queue.put(("toggle_auto_adjust", process_id))

    def set_role(self, process_id: int, role: str):
        self.command_queue.put(("set_role", process_id, role))

    def apply_target_range(self, process_id: int, min_db: float, max_db: float, min_peak_db: float):
        self.command_queue.put(("apply_target_range", process_id, min_db, max_db, min_peak_db))

    def update_settings(self, foreground_threshold: float, background_volume_ratio: float, ui_fps: int):
        self.command_queue.put(("update_settings", foreground_threshold, background_volume_ratio, ui_fps))

    def toggle_theme(self):
        self.command_queue.put(("toggle_theme",))

    def _monitor_loop(self):
        CoInitialize()
        refresh_counter = 0
        try:
            while self.running:
                try:
                    self._process_commands()
                    if self.pending_refresh or refresh_counter >= 5:
                        self._refresh_sessions()
                        self.pending_refresh = False
                        refresh_counter = 0
                    else:
                        refresh_counter += 1
                    self._refresh_runtime_state()
                    self._smart_ducking()
                    self._auto_adjust_all()
                    self._flush_pending_save()
                    time.sleep(0.1)
                except Exception:
                    time.sleep(0.5)
        finally:
            CoUninitialize()

    def _process_commands(self):
        while True:
            try:
                command = self.command_queue.get_nowait()
            except Empty:
                break
            self._execute_command(command)

    def _execute_command(self, command):
        action = command[0]
        with self.lock:
            if action == "set_volume":
                session = self.audio_sessions.get(command[1])
                if session:
                    session.set_volume(command[2])
                    self._mark_dirty()
                    self._bump_version()
            elif action == "toggle_mute":
                session = self.audio_sessions.get(command[1])
                if session:
                    session.toggle_mute()
                    self._bump_version()
            elif action == "toggle_auto_adjust":
                session = self.audio_sessions.get(command[1])
                if session:
                    session.auto_adjust_enabled = not session.auto_adjust_enabled
                    self._mark_dirty()
                    self._bump_version()
            elif action == "set_role":
                self._set_role_locked(command[1], command[2])
            elif action == "apply_target_range":
                session = self.audio_sessions.get(command[1])
                if session:
                    session.target_min_db = command[2]
                    session.target_max_db = command[3]
                    session.min_peak_db = command[4]
                    self._mark_dirty()
                    self._bump_version()
            elif action == "update_settings":
                self.settings.foreground_threshold = command[1]
                self.settings.background_volume_ratio = command[2]
                self.settings.ui_fps = command[3]
                self._mark_dirty()
                self._bump_version()
            elif action == "toggle_theme":
                self.settings.theme = "light" if self.settings.theme == "dark" else "dark"
                self._mark_dirty()
                self._bump_version()

    def _set_role_locked(self, process_id: int, role: str):
        session = self.audio_sessions.get(process_id)
        if not session:
            return
        if role not in {ROLE_NORMAL, ROLE_FOREGROUND, ROLE_BACKGROUND}:
            role = ROLE_NORMAL
        if role in {ROLE_FOREGROUND, ROLE_BACKGROUND}:
            for other in self.audio_sessions.values():
                if other.process_id != process_id and other.role == role:
                    other.role = ROLE_NORMAL
        session.role = role
        self._mark_dirty()
        self._bump_version()

    def _refresh_sessions(self):
        new_sessions = self._discover_sessions()
        with self.lock:
            previous_sessions = self.audio_sessions
            previous_runtime = {session.config_key: session.live_volume for session in previous_sessions.values()}
            previous_saved = {
                session.config_key: (session.saved_volume, session.is_ducked)
                for session in previous_sessions.values()
            }
            for session in new_sessions.values():
                persisted = self._get_persisted_session_config(session)
                if persisted:
                    session.load_config(persisted)
                if session.config_key in previous_runtime:
                    session.set_volume(previous_runtime[session.config_key])
                if session.config_key in previous_saved:
                    session.saved_volume, session.is_ducked = previous_saved[session.config_key]
                session.refresh_runtime()
            self.audio_sessions = new_sessions
            self._normalize_roles_locked()
            self._bump_version()

    def _get_persisted_session_config(self, session: AudioSession):
        persisted = self.persisted_session_configs.get(session.config_key)
        if persisted is not None:
            return persisted
        return self.legacy_session_configs.get(session.display_name.lower())

    def _discover_sessions(self):
        sessions_by_pid: Dict[int, AudioSession] = {}
        for session in AudioUtilities.GetAllSessions():
            try:
                process_id = session.ProcessId
                if process_id > 0:
                    try:
                        session_id = session.InstanceIdentifier
                    except Exception:
                        session_id = str(id(session._ctl))
                    if process_id not in sessions_by_pid:
                        audio_session = AudioSession(process_id)
                        sessions_by_pid[process_id] = audio_session
                    sessions_by_pid[process_id].add_sub_session(session._ctl, session_id)
            except Exception:
                continue
        return sessions_by_pid

    def _refresh_runtime_state(self):
        with self.lock:
            for session in self.audio_sessions.values():
                session.refresh_runtime()

    def _normalize_roles_locked(self):
        foreground_pid = None
        background_pid = None
        for session in self.audio_sessions.values():
            if session.role == ROLE_FOREGROUND:
                if foreground_pid is not None:
                    session.role = ROLE_NORMAL
                else:
                    foreground_pid = session.process_id
            elif session.role == ROLE_BACKGROUND:
                if background_pid is not None:
                    session.role = ROLE_NORMAL
                else:
                    background_pid = session.process_id

    def _smart_ducking(self):
        with self.lock:
            foreground_session = None
            for session in self.audio_sessions.values():
                if session.role == ROLE_FOREGROUND:
                    foreground_session = session
                    break

            if not foreground_session:
                self._restore_background_sessions_locked()
                return

            foreground_peak_db = AudioSession._peak_to_db(foreground_session.live_peak)
            if foreground_peak_db < foreground_session.min_peak_db:
                self._restore_background_sessions_locked()
                return

            foreground_adjusted = foreground_session.live_peak * foreground_session.live_volume
            foreground_db = AudioSession._peak_to_db(foreground_adjusted)

            if foreground_db > self.settings.foreground_threshold:
                for session in self.audio_sessions.values():
                    if session.role != ROLE_BACKGROUND:
                        continue
                    if session.saved_volume is None and not session.is_ducked:
                        session.saved_volume = session.live_volume
                        session.is_ducked = True
                    target_volume = foreground_session.live_volume * self.settings.background_volume_ratio
                    current_volume = session.live_volume
                    if current_volume > target_volume:
                        session.set_volume(max(current_volume - 0.05, target_volume))
                    elif current_volume < target_volume:
                        session.set_volume(min(current_volume + 0.05, target_volume))
            else:
                self._restore_background_sessions_locked()

    def _restore_background_sessions_locked(self):
        for session in self.audio_sessions.values():
            if session.role != ROLE_BACKGROUND or session.saved_volume is None:
                continue
            current_volume = session.live_volume
            saved_volume = session.saved_volume
            if current_volume < saved_volume:
                session.set_volume(min(current_volume + 0.05, saved_volume))
            elif abs(current_volume - saved_volume) < 0.01:
                session.saved_volume = None
                session.is_ducked = False
            elif current_volume > saved_volume:
                session.set_volume(max(current_volume - 0.05, saved_volume))

    def _auto_adjust_all(self):
        with self.lock:
            for session in self.audio_sessions.values():
                session.auto_adjust_volume()

    def _mark_dirty(self):
        self.dirty = True

    def _flush_pending_save(self):
        now = time.time()
        if self.dirty and now - self.last_saved_at >= self.save_interval:
            self._save_config(force=True)

    def _save_config(self, force: bool = False):
        if not force:
            return
        with self.lock:
            sessions = list(self.audio_sessions.values())
            self.config_store.save(self.settings, sessions)
            self.persisted_session_configs = {session.config_key: session.get_config() for session in sessions}
            self.last_saved_at = time.time()
            self.dirty = False

    def _bump_version(self):
        self.version += 1


def parse_version(version_str: str) -> Tuple[int, ...]:
    """解析版本字符串为元组，便于比较"""
    try:
        parts = version_str.strip().lstrip("v").split(".")
        return tuple(int(p) for p in parts[:3])
    except (ValueError, AttributeError):
        return (0, 0, 0)


def check_for_update(silent: bool = False) -> Tuple[bool, str, str]:
    """
    检查 Gitee/GitHub 仓库是否有新版本

    Args:
        silent: True 时只返回更新状态，不返回错误信息（用于启动时静默检查）

    Returns:
        Tuple[bool, str, str]: (是否有更新, 最新版本号, 更新日志/错误信息)
    """
    gitee_url = "https://gitee.com/Evelynall/volume-mixer/releases"
    github_url = "https://github.com/Evelynall/Volume_Mixer/releases"

    def make_request(url: str, timeout: int = 10) -> str:
        """发起网络请求并返回HTML内容"""
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")

    def parse_gitee_releases(html: str) -> Tuple[str, str]:
        """解析 Gitee releases 页面获取最新版本和更新日志"""
        import re

        # 匹配版本号，如 v1.2.0 或 1.2.0
        version_pattern = r'/releases/(?:v)?([0-9]+\.[0-9]+\.[0-9]+)'
        match = re.search(version_pattern, html)
        if not match:
            return "", ""

        latest_version = match.group(1)

        # 尝试提取更新日志
        version_pos = html.find(f'/releases/v{latest_version}')
        if version_pos == -1:
            version_pos = html.find(f'/releases/{latest_version}')

        if version_pos > 0:
            end_pattern = r'/releases/(?:v)?([0-9]+\.[0-9]+\.[0-9]+)'
            next_match = re.search(end_pattern, html[version_pos + 100:])
            if next_match:
                end_pos = version_pos + 100 + next_match.start()
            else:
                end_pos = min(version_pos + 5000, len(html))

            section = html[version_pos:end_pos]
            # 提取 markdown 内容
            body_pattern = r'<div[^>]*class="[^"]*markdown-body[^"]*"[^>]*>(.*?)</div>'
            body_match = re.search(body_pattern, section, re.DOTALL)
            if body_match:
                notes = body_match.group(1)
                notes = re.sub(r'<[^>]+>', ' ', notes)
                notes = re.sub(r'\s+', ' ', notes).strip()
                return latest_version, notes[:2000] if len(notes) > 2000 else notes

        return latest_version, ""

    def parse_github_releases(html: str) -> Tuple[str, str]:
        """解析 GitHub releases 页面获取最新版本和更新日志"""
        import re

        version_pattern = r'/releases/tag/(?:v)?([0-9]+\.[0-9]+\.[0-9]+)'
        match = re.search(version_pattern, html)
        if not match:
            return "", ""

        latest_version = match.group(1)

        version_pos = html.find(f'/releases/tag/v{latest_version}')
        if version_pos == -1:
            version_pos = html.find(f'/releases/tag/{latest_version}')

        if version_pos > 0:
            end_pattern = r'/releases/tag/(?:v)?([0-9]+\.[0-9]+\.[0-9]+)'
            next_match = re.search(end_pattern, html[version_pos + 100:])
            if next_match:
                end_pos = version_pos + 100 + next_match.start()
            else:
                end_pos = min(version_pos + 5000, len(html))

            section = html[version_pos:end_pos]
            body_pattern = r'<article[^>]*class="[^"]*markdown-body[^"]*"[^>]*>(.*?)</article>'
            body_match = re.search(body_pattern, section, re.DOTALL)
            if body_match:
                notes = body_match.group(1)
                notes = re.sub(r'<[^>]+>', ' ', notes)
                notes = re.sub(r'\s+', ' ', notes).strip()
                return latest_version, notes[:2000] if len(notes) > 2000 else notes

        return latest_version, ""

    def compare_versions(latest_version: str) -> bool:
        """比较版本是否更新"""
        current = parse_version(__version__)
        latest = parse_version(latest_version)
        return latest > current

    error_msg = "网络连接失败，请检查网络后重试" if not silent else ""

    # 1. 优先检查 Gitee
    try:
        html = make_request(gitee_url, timeout=10)
        latest_version, release_notes = parse_gitee_releases(html)
        if latest_version:
            return compare_versions(latest_version), latest_version, release_notes
    except Exception:
        pass

    # 2. Gitee 失败，尝试 GitHub
    try:
        html = make_request(github_url, timeout=10)
        latest_version, release_notes = parse_github_releases(html)
        if latest_version:
            return compare_versions(latest_version), latest_version, release_notes
    except urllib.error.HTTPError as e:
        if not silent:
            error_msg = f"网络错误: HTTP {e.code}"
    except urllib.error.URLError as e:
        if not silent:
            error_msg = f"网络连接失败: {e.reason}"
    except Exception:
        pass

    # 3. 都失败
    return False, "", error_msg
