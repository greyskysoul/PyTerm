# PyTerm

minicom 风格的跨平台（Windows / Linux）串口终端 TUI，内置健壮的 **YMODEM** 文件传输，
面向嵌入式固件烧录（STM32 等 ymodem bootloader）与日常串口调试。

- 全屏终端界面：设备 ANSI/VT 输出正确渲染、可滚动回看
- **Ctrl+A 前缀键 + 弹层菜单**（minicom 交互习惯）
- YMODEM 发送 / 接收：CRC-16-CCITT、128/1024 字节块可配置、超时重传、进度显示、可取消
- 端口/波特率等参数随时可改、配置持久化
- 会话捕获（log）、本地回显、行尾转换、HEX 显示等选项
- Windows（建议 Windows Terminal）与 Linux 双平台

## 安装

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux
source .venv/bin/activate
pip install -e ".[dev]"
```

## 运行

```bash
pyterm                          # 启动后按 Ctrl+A Z 打开菜单 / 自动弹出连接对话框
pyterm --port COM3 --baud 115200
pyterm /dev/ttyUSB0 -b 921600 -f ymodem
```

## 快捷键（Ctrl+A 前缀）

| 按键     | 功能                          |
|----------|-------------------------------|
| `Ctrl+A` `Z` | 打开主菜单 / 帮助         |
| `Ctrl+A` `X` | 退出                        |
| `Ctrl+A` `S` | 发送文件（YMODEM）          |
| `Ctrl+A` `R` | 接收文件（YMODEM）          |
| `Ctrl+A` `L` | 会话捕获开关                |
| `Ctrl+A` `C` | 清屏                        |
| `Ctrl+A` `P` | 串口参数（连接设置）        |
| `Ctrl+A` `O` | 选项                        |
| `Ctrl+A` `A` | 换行(自动回绕)开关          |
| `Ctrl+A` `E` | 本地回显开关                |
| `Ctrl+A` `Ctrl+A` | 向设备发送字节 `0x01`   |
| `Esc`（前缀中）| 取消前缀                    |

## 开发

```bash
pytest            # 单元测试：CRC / 帧 / YMODEM 回环等
ruff check .      # 静态检查
mypy src/pyterm   # 类型检查
```

结构：`src/pyterm/`（`serialio.py` 串口、`termdisplay/` 终端渲染、`xfer/ymodem.py`
协议引擎、`screens/` 各界面、`keys.py` 按键状态机、`app.py` 主程序）。

## 打包

```bash
pip install pyinstaller
pyinstaller packaging/pyterm.spec    # Windows 上得到单文件 exe（在 Windows 构建）
```

CI（`.github/workflows/ci.yml`）在 Windows / Ubuntu 双平台跑 lint/类型/测试并构建产物。

## 技术栈

- **Python ≥3.11**，`src` 布局，标准库优先
- **Textual** — 终端 TUI 框架（模态弹层/表单/文件树，天然支持弹层菜单）
- **pyserial** — 串口（含 `list_ports` 端口枚举）
- **pyte** — 设备 RX 字节流的 VT 终端模拟（子类化其 `Screen` 捕获滚出内容实现回看；LGPLv3）
- **自研 YMODEM 引擎**（`xfer/ymodem.py`）：CRC-16-CCITT、SOH/STX、128/1024 块、
  超时/重传可配置、重复块容忍、CAN-CAN 中止、坏块自动重传、进度回调
- 打包：PyInstaller；测试：pytest（含 Textual Pilot 无头 UI 测试）、ruff、mypy

## 目录结构

```
src/pyterm/
  app.py              主程序（Ctrl+A 前缀状态机、串口路由、传输 worker、捕获）
  config.py           配置数据模型 + JSON 持久化
  serialio.py         串口层（后台读线程、写锁、端口枚举、热拔插）
  keys.py             按键→字节映射（行尾/退格/方向键 VT 序列…）
  termdisplay/
    vt.py             pyte 终端模型（解码、滚动历史、resize）
    view.py           TerminalView / StatusBar 控件
  xfer/ymodem.py      YMODEM 双向协议引擎（纯 Python、可脱离串口单测）
  screens/            连接、主菜单、选项、文件/目录选择、收发传输界面
  resources/app.tcss  主题
tests/unit/           CRC/帧/block0、引擎回环(含错误注入)、按键、终端模型、Pilot UI
packaging/            PyInstaller 启动器与 spec
```

## 配置

配置文件为 JSON（`%APPDATA%\pyterm\config.json` Windows /
`~/.config/pyterm/config.json` Linux），可通过 Ctrl+A O 修改并在运行中保存。

## 已知范围（Roadmap）

- v1 已含：YMODEM 双向收发、捕获日志、行尾/回显/解码/流控配置、滚动回看、HEX 无需渲染
- v1 不含：XMODEM/ZMODEM/Kermit、ASCII 发送、宏脚本、拨号目录、分屏多会话
- 建议在 **Windows Terminal** 下运行（完整支持 ConPTY/颜色）

## 实测建议

1. Linux 与 lrzsz 交叉验证：`sz -Y file` 对 PyTerm 接收；`rz -Y` 对 PyTerm 发送
2. Windows/Linux 双端可用 socat(pty)/com0com 虚拟串口做端到端回环
3. STM32 bootloader 实机烧录建议 115200/921600 各验证一次大文件（SHA256 比对）

