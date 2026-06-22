# 音量混合器 (Volume Mixer)

一个基于Windows Core Audio API的音量混合器应用，支持显示和调节各应用的音量，并提供实时音量监控和自动调节功能。

## 功能特性

- 🎵 **应用音量显示** - 显示所有正在播放音频的应用程序
- 🎚️ **音量调节** - 通过滑块调节每个应用的音量
- 📊 **实时音量表** - 显示原始电平和实际输出电平
- 🔊 **分贝显示** - 实时显示音量的dB值
- 🔇 **静音控制** - 每个应用可单独静音
- 🤖 **自动调节** - 自动调整音量以保持目标电平范围
- 🔧 **配置保存** - 自动保存和加载设置
- 📥 **系统托盘** - 支持最小化到系统托盘
- 🚀 **无UI模式** - 支持后台静默运行

## 界面预览

![界面预览](image.jpg)

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方法

### 有UI模式（默认）

```bash
python volume_mixer.py
```

### 无UI模式（后台服务）

```bash
python volume_mixer.py --no-gui
# 或
python volume_mixer.py -n
```

## 自动调节功能

1. 点击应用的"自动"按钮启用自动调节
2. 设置目标电平范围（如 -30 ~ -20 dB）
3. 点击"应用"保存设置
4. 程序会自动调整音量，使实际输出电平保持在目标范围内

## 配置文件

配置文件 `volume_mixer_config.json` 会自动保存在程序目录，包含各应用的自动调节设置。

## 技术栈

- Python 3.x
- tkinter (UI界面)
- pycaw (Windows音频API)
- psutil (进程信息)
- pystray (系统托盘)
- Pillow (图标生成)

## 许可证

MIT License