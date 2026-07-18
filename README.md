# backUpHelper

backUpHelper is a Windows desktop backup helper for system reinstall and new-machine
migration. It focuses on smart, explainable partial backups while also exposing
file-level full backup targets.

## Current milestone

This repository contains the first implementation slice:

- Smart backup item discovery for common Windows user folders, developer config,
  browser/app profiles, chat apps, package managers, and Conda.
- Installed software inventory from Windows uninstall registry keys.
- Manifest-based backup package layout with checksums.
- Zip packaging, optional 7z packaging, and file-level ISO packaging.
- Separate full-backup flows for whole-volume ISO images and folder archives.
- Optional AES-GCM output encryption for zip, 7z, ISO, and directory-mode packages
  that are converted to encrypted zip output.
- Backup creation runs in a Qt worker thread in the GUI, with progress and logs
  shown in the app. Console runs also print selected items and packaging progress.
- Zip and 7z output prefer an available `7z`/`7za`/`7zz` executable with `-mmt=on`
  for multithreaded compression, then fall back to Python libraries when needed.
- Restore dry-run planning.
- A PySide6 + QFluentWidgets UI with dark/light theme support, high-DPI settings,
  Chinese/English application text, selected-file previews, and five pages.

Volume ISO backup packages readable files from the selected drive into an ISO.
It is a file-level image, not a bootable sector-by-sector system clone.

At this stage, only the smart backup flow has been exercised as the primary tested
path. Other features are early implementations and have not been fully tested yet.
Issues and reproduction details are welcome.

## Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[ui,dev]"
```

## Run

```powershell
back-up-helper
```

or:

```powershell
python main.py --ui
```

With the requested conda environment:

```powershell
conda run -n dev python main.py --ui
```

## Test

```powershell
pytest
```

## Notes

- Sensitive entries such as SSH keys, Git credentials, browser cookies, and chat
  records are marked as sensitive and are not selected by default.
- 7z encryption is preferred for sensitive backups.
- The repository does not currently bundle a 7-Zip binary. If one is added later
  under `tools/7zip/`, keep the 7-Zip license/source notices with the package:
  <https://www.7-zip.org/license.txt>.
- Conda environments are exported as commands/artifacts that can be reviewed
  before restore.
- The app icon is based on Bootstrap Icons `archive`, licensed under MIT:
  <https://icons.getbootstrap.com/icons/archive/>.

---

# 中文说明

backUpHelper 是一个用于 Windows 系统重装和新机器迁移的桌面备份助手。
它重点提供可解释的智能局部备份，同时也暴露文件级完整备份目标。

## 当前里程碑

这个仓库目前包含第一阶段实现：

- 智能备份项发现：覆盖常见 Windows 用户目录、开发者配置、浏览器/应用配置、
  聊天软件、包管理器和 Conda。
- 从 Windows 卸载注册表项读取已安装软件清单。
- 基于 manifest 的备份包目录结构和校验和。
- Zip 打包、可选 7z 打包，以及文件级 ISO 打包。
- 分离的完整备份流程：整盘符 ISO 镜像和文件夹归档。
- 可选 AES-GCM 输出加密，支持 zip、7z、ISO，以及会转换为加密 zip 的目录模式包。
- GUI 中创建备份会在 Qt worker 线程里运行，并在界面显示进度和日志。
  从控制台启动时，也会打印用户选择的备份项和打包进度。
- zip 和 7z 输出会优先调用可用的 `7z`/`7za`/`7zz`，并使用 `-mmt=on`
  开启多线程压缩；不可用时再回退到 Python 库。
- 恢复前 dry-run 预案。
- PySide6 + QFluentWidgets 用户界面，支持深色/浅色主题、高 DPI、中文/英文界面文本、
  所选文件预览和五个页面。

盘符 ISO 备份会把所选盘符中可读取的文件打包进 ISO。
它是文件级镜像，不是可启动的、逐扇区系统克隆。

当前阶段只有智能备份流程作为主要路径进行过测试。
除智能备份以外，其他功能仍属于早期实现，尚未完整测试。
欢迎提交 issue，并附上复现步骤和环境信息。

## 安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[ui,dev]"
```

## 运行

```powershell
back-up-helper
```

或者：

```powershell
python main.py --ui
```

使用指定的 conda 环境：

```powershell
conda run -n dev python main.py --ui
```

## 测试

```powershell
pytest
```

## 说明

- SSH 密钥、Git 凭据、浏览器 Cookie、聊天记录等敏感项会被标记为敏感，
  并且默认不会被选中。
- 敏感备份建议优先使用 7z 加密。
- 仓库当前不直接内置 7-Zip 二进制。如果之后放入 `tools/7zip/`，需要随包保留
  7-Zip 的许可证和源码说明：<https://www.7-zip.org/license.txt>。
- Conda 环境会导出为可审阅的命令和文件，恢复时不会静默自动执行。
- 应用图标基于 Bootstrap Icons `archive`，使用 MIT 许可：
  <https://icons.getbootstrap.com/icons/archive/>。
