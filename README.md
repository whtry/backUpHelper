# backUpHelper

<p align="center">
  <img src="assets/app-icon.svg" width="88" alt="backUpHelper archive icon">
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="https://doc.qt.io/qtforpython-6/"><img src="https://img.shields.io/badge/PySide6-Qt%20for%20Python-41CD52?logo=qt&logoColor=white" alt="PySide6"></a>
  <img src="https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white" alt="Windows">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0--or--later-A42E2B?logo=gnu&logoColor=white" alt="GPL-3.0-or-later"></a>
</p>

<p align="center">
  <a href="#中文说明">跳转至中文说明</a>
</p>

**A Windows migration backup assistant for the files and settings that matter.**

backUpHelper helps prepare for a Windows reinstall or a move to a new PC. Instead of treating every backup as an opaque disk image, it discovers common user folders, application data, developer configuration, installed application metadata, and Conda environments so that you can review exactly what will be included.

> [!IMPORTANT]
> Smart backup is the primary tested workflow. Full backup and Restore are experimental developer-mode features. They have not been verified on every Windows configuration; please report issues with reproduction steps and environment details.

## Highlights

- **Explainable smart selection**: identifies user folders, developer configuration, browsers, chat clients, package managers, and Conda-related files, with a reason for every backup item.
- **Installed application inventory**: reads Windows uninstall registry entries and preserves application names, versions, publishers, registry references, and available icons.
- **Preview before packaging**: inspect a selected item in a hierarchical tree, list, table, or large-icon view; explicitly select or exclude individual files and folders before creating an archive.
- **Portable backup packages**: create ZIP, 7z, ISO, or directory packages containing a manifest, file data, checksums, application inventory, and registry exports.
- **Privacy-aware defaults**: sensitive items such as SSH keys, Git credentials, browser cookies, and chat records are opt-in and can be encrypted.
- **Background operations**: backup and restore run in worker threads with progress shown in the UI and detailed logs available when the program is launched from a console.
- **Windows-native UI**: PySide6 + QFluentWidgets, high-DPI support, light/dark/system themes, Simplified Chinese and English, remembered window placement, and direct sidebar controls for theme and language.

## Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| Smart backup | Ready for testing | The main, actively exercised workflow. |
| Package browser | Ready for testing | Browses directory, ZIP, 7z, and ISO packages. |
| Full backup | Experimental | Available only after enabling Developer mode. Creates a file-level ISO of readable files, not a bootable or sector-by-sector disk image. |
| Restore | Experimental | Available only after enabling Developer mode. Starts with a dry-run plan and does not import registry data or run commands automatically. |

## What Is Backed Up

Smart backup can discover and present, when present on the current machine:

- Windows known folders: Desktop, Downloads, Documents, Pictures, Videos, and Music.
- Developer configuration: SSH, Git, GnuPG, npm, PyPI, Conda, VS Code user settings/extensions, and other common home-directory configuration.
- Application data: Chrome, Edge, Firefox, VS Code, JetBrains IDEs, Windows Terminal, PowerShell, Git, WeChat, QQ, Telegram, Scoop, Chocolatey, and winget-related data.
- Installed applications from the Windows uninstall registry, including exported metadata and available icons.
- Conda environments, with `environment.yml`, `requirements.txt`, and `explicit.txt` exports where the local Conda installation supports them.

Discovery does not silently select anything. Review the list, select only what you need, preview the files, and then create the package.

## Security And Safety

- Sensitive entries are unchecked by default. Enable encryption before including secrets, session data, or chat history.
- AES-GCM encryption is available for output packages. Keep the password separately; a lost password cannot be recovered.
- Restore defaults to skipping existing files. Switching to overwrite requires an explicit confirmation.
- Registry exports are saved for review but are never automatically imported. Recorded installation and Conda commands are displayed for review and are never silently run.
- A volume ISO packages files that can be read from the selected drive. It is **not** a bootable Windows image and it is **not** a sector-by-sector clone. Use a dedicated imaging tool for bare-metal recovery.

## Developer Mode

The normal interface exposes Smart backup and Package browser. Open **Settings**, enable **Developer mode**, and the Full backup and Restore pages become available. This setting is persisted and disabling it immediately hides and disables both experimental workflows.

## Requirements

- Windows 10 or Windows 11
- Python 3.12 or newer
- Optional: a local Conda installation for environment export
- Optional: a 7-Zip runtime for fast, multithreaded ZIP/7z processing

## Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[ui,dev]"
```

To use the machine's `dev` Conda environment:

```powershell
conda run -n dev python -m pip install -e ".[ui,dev]"
```

## Run

```powershell
back-up-helper
```

or:

```powershell
python main.py --ui
```

The UI build does not open a console window in packaged releases. Launching it from an existing console still prints selected items, progress, output paths, and errors for troubleshooting.

For a command-line backup, choose items explicitly:

```powershell
python main.py --backup D:\Backups --items known-documents,vscode-user --format 7z
```

## Temporary Work Directory

Archives are staged before they are finalized. To avoid filling the Windows system drive, choose a dedicated temporary-work root in **Settings**. The setting is used for package staging, Python compression/extraction work, and 7-Zip temporary files.

The command-line equivalent is:

```powershell
python main.py --temp-root D:\BackupWork --backup D:\Backups --items known-documents
```

Do not choose a temporary directory inside the source directory being backed up.

## 7-Zip Runtime

backUpHelper uses an available `7z`, `7za`, or `7zz` executable for multithreaded compression (`-mmt=on`), then falls back to Python libraries when it is unavailable.

7-Zip binaries are not committed to Git. Download the official runtime into the ignored `tools/7zip/` directory with:

```powershell
python scripts/download_7zip.py
```

The GitHub Actions release workflow performs the same download and includes the runtime in the onedir release package. 7-Zip is distributed under the [7-Zip license](https://www.7-zip.org/license.txt).

## Development

Run the checks:

```powershell
python -m compileall -q .
python -m ruff check .
python -m pytest
```

The GitHub Actions workflow builds Windows `x64` and `arm64` release artifacts with PyInstaller. Releases are built with `--noconsole` so a console window is not created by default.

## License And Dependencies

This project is licensed under [GPL-3.0-or-later](LICENSE). Please review the licenses of all bundled and optional dependencies before redistributing a packaged application, especially QFluentWidgets and 7-Zip.

The application icon is based on Bootstrap Icons' [archive icon](https://icons.getbootstrap.com/icons/archive/), licensed under MIT.

## Contributing And Support

Bug reports, compatibility findings, and pull requests are welcome. For a useful issue report, include:

- Windows version and CPU architecture.
- Python and app version.
- The action being performed and the selected output format.
- Relevant console logs, with private paths, passwords, tokens, and chat content removed.

## Support The Project

backUpHelper is maintained as an open-source project. If it saves you time during a system reinstall or migration, you are welcome to support its continued maintenance, compatibility testing, and practical improvements with a voluntary Alipay contribution. Thank you for helping keep the project moving.

<p align="center">
  <img src="assets/alipay.jpg" width="220" alt="Alipay support QR code">
</p>

---

# 中文说明

**一个面向 Windows 重装系统和迁移新电脑的备份助手。**

backUpHelper 用于在重装 Windows 或迁移到新电脑之前，整理真正需要迁移的文件和配置。它不会把所有内容都当成无法查看的磁盘镜像，而是会发现常见用户目录、应用数据、开发者配置、已安装应用信息和 Conda 环境，让你在打包前清楚知道每一项为什么需要备份。

> [!IMPORTANT]
> 智能备份是当前主要经过验证的流程。完整备份和恢复属于开发者模式中的实验性功能，尚未在所有 Windows 环境完成验证。遇到问题请提交 issue，并附上复现步骤和运行环境。

## 特性

- **可解释的智能选择**：识别用户目录、开发者配置、浏览器、聊天软件、包管理器和 Conda 相关文件，每一项都显示备份原因。
- **已安装应用清单**：读取 Windows 卸载注册表，保存应用名称、版本、发布者、注册表引用以及可获取的应用图标。
- **打包前预览**：以目录树、列表、表格或大图标查看选中项；可在创建备份前精确选择或排除具体文件、文件夹。
- **可移植备份包**：支持 ZIP、7z、ISO 和目录式备份包，内含 manifest、文件数据、校验和、应用清单与注册表导出。
- **重视隐私**：SSH 密钥、Git 凭据、浏览器 Cookie、聊天记录等敏感项默认不选，并可使用加密输出。
- **后台执行**：备份和恢复在工作线程中执行，界面显示进度；从控制台启动时也会输出详细日志。
- **Windows 风格界面**：基于 PySide6 + QFluentWidgets，支持高 DPI、浅色/深色/跟随系统、简体中文/英文、窗口状态记忆，以及侧边栏直接切换主题和语言。

## 功能状态

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| 智能备份 | 可测试 | 当前主要维护和验证的流程。 |
| 备份包浏览 | 可测试 | 支持目录包、ZIP、7z 与 ISO。 |
| 完整备份 | 实验性 | 仅在开启开发者模式后显示。创建的是可读取文件的文件级 ISO，不是可启动或逐扇区磁盘镜像。 |
| 恢复 | 实验性 | 仅在开启开发者模式后显示。先生成 dry-run 预案，不会自动导入注册表或执行命令。 |

## 可以发现的备份内容

智能备份会在当前电脑中尝试发现并列出以下内容：

- Windows 已知目录：桌面、下载、文档、图片、视频和音乐。
- 开发者配置：SSH、Git、GnuPG、npm、PyPI、Conda、VS Code 用户设置/扩展，以及其他常见用户目录配置。
- 应用数据：Chrome、Edge、Firefox、VS Code、JetBrains IDE、Windows Terminal、PowerShell、Git、微信、QQ、Telegram、Scoop、Chocolatey 和 winget 相关数据。
- Windows 卸载注册表中的已安装应用，并导出元数据和可用图标。
- Conda 环境：本地 Conda 支持时，为环境导出 `environment.yml`、`requirements.txt` 和 `explicit.txt`。

发现到的内容不会被自动勾选。请先审阅列表，手动选择需要的内容，预览文件后再创建备份。

## 安全边界

- 敏感内容默认不选。需要备份密钥、会话数据或聊天记录时，请先启用加密。
- 输出包可使用 AES-GCM 加密。请妥善保存密码，丢失后无法恢复。
- 恢复默认跳过同名文件；切换为覆盖策略时必须主动确认。
- 注册表只会导出供查看，不会被自动导入；包中记录的安装命令和 Conda 命令也不会静默执行。
- 盘符 ISO 会归档所选盘中可以读取的文件，**不是**可启动 Windows 镜像，也**不是**逐扇区克隆。需要裸机恢复时，请使用专业磁盘镜像工具。

## 开发者模式

普通模式只显示“智能备份”和“备份包浏览”。在**设置**中开启**开发者模式**后，才会显示并启用“完整备份”和“恢复”。该开关会持久保存；关闭后，这两个实验性页面会立即隐藏并禁用。

## 环境要求

- Windows 10 或 Windows 11
- Python 3.12 或更高版本
- 可选：本地 Conda，用于导出环境信息
- 可选：7-Zip 运行时，用于更快的多线程 ZIP/7z 压缩

## 安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[ui,dev]"
```

使用系统中的 `dev` Conda 环境：

```powershell
conda run -n dev python -m pip install -e ".[ui,dev]"
```

## 运行

```powershell
back-up-helper
```

或：

```powershell
python main.py --ui
```

打包后的图形界面默认不会弹出控制台窗口；但从已有控制台启动时，仍会输出选择项、进度、输出路径和错误日志，便于排查问题。

命令行创建备份时必须明确选择备份项：

```powershell
python main.py --backup D:\Backups --items known-documents,vscode-user --format 7z
```

## 临时工作目录

归档创建过程中需要暂存文件。为避免占满 Windows 系统盘，可在**设置**中选择专用临时工作目录。该设置会用于备份包暂存、Python 压缩/解压工作，以及 7-Zip 临时文件。

命令行等效参数：

```powershell
python main.py --temp-root D:\BackupWork --backup D:\Backups --items known-documents
```

请不要将临时目录设置在当前要备份的源目录内部。

## 7-Zip 运行时

程序会优先使用可用的 `7z`、`7za` 或 `7zz`，并通过 `-mmt=on` 启用多线程压缩；不可用时会回退到 Python 库。

7-Zip 二进制文件不会提交到 Git。可运行以下命令，将官方运行时下载到被忽略的 `tools/7zip/` 目录：

```powershell
python scripts/download_7zip.py
```

GitHub Actions 发布工作流也会自动下载，并将其放入 onedir 发布包。7-Zip 使用 [7-Zip 许可证](https://www.7-zip.org/license.txt)。

## 开发与测试

```powershell
python -m compileall -q .
python -m ruff check .
python -m pytest
```

GitHub Actions 会通过 PyInstaller 构建 Windows `x64` 和 `arm64` 两种发布产物，并采用 `--noconsole`，因此默认不会出现控制台窗口。

## 许可证与依赖

本项目使用 [GPL-3.0-or-later](LICENSE) 许可证。重新分发打包后的应用前，请审阅所有打包或可选依赖的许可证，特别是 QFluentWidgets 和 7-Zip。

应用图标基于 Bootstrap Icons 的 [archive 图标](https://icons.getbootstrap.com/icons/archive/)，使用 MIT 许可证。

## 贡献与反馈

欢迎提交 bug、兼容性测试结果和 Pull Request。提交 issue 时建议附上：

- Windows 版本与 CPU 架构。
- Python 与应用版本。
- 操作步骤和选择的输出格式。
- 相关控制台日志；请先移除隐私路径、密码、令牌和聊天内容。

## 赞助支持

backUpHelper 是一个持续维护的开源项目。如果它在重装系统或迁移电脑时为你节省了时间，欢迎通过支付宝自愿赞助，帮助项目继续进行维护、兼容性测试和实用功能改进。感谢你的支持，让这个项目能够继续向前。

<p align="center">
  <img src="assets/alipay.jpg" width="220" alt="支付宝赞助收款码">
</p>
