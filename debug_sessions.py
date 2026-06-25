import json
import time
from comtypes import CoInitialize, CoUninitialize
from pycaw.pycaw import AudioUtilities, IAudioMeterInformation, ISimpleAudioVolume
import psutil


def get_all_sessions_debug():
    sessions = AudioUtilities.GetAllSessions()
    print(f"=" * 80)
    print(f"总共枚举到 {len(sessions)} 个音频会话")
    print(f"=" * 80)

    for i, session in enumerate(sessions):
        print(f"\n--- 会话 #{i+1} ---")
        try:
            process_id = session.ProcessId
            print(f"  ProcessId: {process_id}")

            try:
                display_name = session.DisplayName
                print(f"  DisplayName (pycaw): '{display_name}'")
            except Exception as e:
                print(f"  DisplayName (pycaw): 获取失败 - {e}")

            try:
                session_identifier = session.SessionIdentifier
                print(f"  SessionIdentifier: {session_identifier}")
            except Exception as e:
                print(f"  SessionIdentifier: 获取失败 - {e}")

            if process_id > 0:
                try:
                    proc = psutil.Process(process_id)
                    print(f"  进程名: {proc.name()}")
                    print(f"  进程路径: {proc.exe()}")
                    print(f"  进程状态: {proc.status()}")
                except Exception as e:
                    print(f"  进程信息: 获取失败 - {e}")
            else:
                print(f"  进程ID为0 - 系统会话")

            try:
                vol = session._ctl.QueryInterface(ISimpleAudioVolume)
                print(f"  音量: {vol.GetMasterVolume():.2f}")
                print(f"  静音: {vol.GetMute()}")
            except Exception as e:
                print(f"  音量接口: 获取失败 - {e}")

            try:
                meter = session._ctl.QueryInterface(IAudioMeterInformation)
                peak = meter.GetPeakValue()
                print(f"  峰值: {peak:.4f}")
            except Exception as e:
                print(f"  峰值表: 获取失败 - {e}")

        except Exception as e:
            print(f"  处理会话时出错: {e}")

    print(f"\n" + "=" * 80)


def monitor_sessions(duration=30, interval=1):
    print(f"开始监控音频会话，持续 {duration} 秒，每 {interval} 秒刷新一次...")
    print("请在此期间确保 oopz 正在运行并播放/录制声音")
    print("=" * 80)

    seen_pids = set()
    start_time = time.time()

    while time.time() - start_time < duration:
        try:
            sessions = AudioUtilities.GetAllSessions()
            current_pids = set()
            new_sessions = []

            for session in sessions:
                pid = session.ProcessId
                current_pids.add(pid)
                if pid not in seen_pids:
                    try:
                        proc_name = psutil.Process(pid).name() if pid > 0 else "system"
                    except Exception:
                        proc_name = f"pid_{pid}"
                    new_sessions.append((pid, proc_name))

            if new_sessions:
                elapsed = time.time() - start_time
                print(f"\n[{elapsed:.1f}s] 发现 {len(new_sessions)} 个新会话:")
                for pid, name in new_sessions:
                    print(f"  - PID {pid}: {name}")
                    seen_pids.add(pid)

            disappeared = seen_pids - current_pids
            if disappeared:
                elapsed = time.time() - start_time
                print(f"\n[{elapsed:.1f}s] {len(disappeared)} 个会话消失了:")
                for pid in disappeared:
                    try:
                        proc_name = psutil.Process(pid).name() if pid > 0 else "system"
                    except Exception:
                        proc_name = f"pid_{pid}"
                    print(f"  - PID {pid}: {proc_name}")
                    seen_pids.discard(pid)

            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n监控被用户中断")
            break
        except Exception as e:
            print(f"监控出错: {e}")
            time.sleep(interval)

    print(f"\n监控结束。总共发现过 {len(seen_pids)} 个不同的会话")


if __name__ == "__main__":
    CoInitialize()
    try:
        print("选择运行模式:")
        print("  1 - 一次性列出所有音频会话（快速诊断）")
        print("  2 - 持续监控30秒（捕捉偶发出现的会话）")
        print("  3 - 持续监控直到按 Ctrl+C")

        choice = input("\n请输入选项 (1/2/3): ").strip()

        if choice == "1":
            get_all_sessions_debug()
        elif choice == "2":
            monitor_sessions(duration=30, interval=1)
        elif choice == "3":
            monitor_sessions(duration=999999, interval=1)
        else:
            print("无效选项，运行模式1")
            get_all_sessions_debug()
    finally:
        CoUninitialize()
