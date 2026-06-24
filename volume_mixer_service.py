import time

from audio_backend import AudioBackend


class VolumeMixerService:
    def __init__(self, backend=None):
        self.backend = backend or AudioBackend()
        self.backend.start()
        settings = self.backend.get_settings()
        print("配置已加载")
        print(f"前景音阈值: {settings.foreground_threshold} dB")
        print(f"背景音比例: {int(settings.background_volume_ratio * 100)}%")
        self.backend.request_refresh()

    def run(self):
        print("音量混合器后台服务已启动...")
        print("按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在退出...")
            self.stop()

    def stop(self):
        self.backend.stop()
        print("服务已停止")
