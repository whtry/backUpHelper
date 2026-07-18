from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.models import BackupItem, RiskLevel
from core.paths import appdata_local, appdata_roaming, home_dir, known_folder, program_data


@dataclass(frozen=True)
class ItemTemplate:
    id: str
    name: str
    category: str
    path: Path
    reason: str
    software: str | None = None
    risk: RiskLevel = RiskLevel.NORMAL
    sensitive: bool = False
    default_selected: bool = True
    tags: tuple[str, ...] = ()


def _known_folder_items() -> list[BackupItem]:
    specs = [
        ("known-desktop", "Desktop", "User Folder", "Desktop shortcuts and working files."),
        ("known-downloads", "Downloads", "User Folder", "Downloaded installers and documents."),
        ("known-documents", "Documents", "User Folder", "Documents and application-created files."),
        ("known-pictures", "Pictures", "User Folder", "Photos and image projects."),
        ("known-videos", "Videos", "User Folder", "Videos and screen recordings."),
        ("known-music", "Music", "User Folder", "Music library and audio projects."),
    ]
    return [
        BackupItem(
            id=item_id,
            name=name,
            category=category,
            path=known_folder(name),
            reason=reason,
            risk=RiskLevel.LARGE if name in {"Downloads", "Videos"} else RiskLevel.NORMAL,
            default_selected=name in {"Desktop", "Documents", "Pictures"},
            tags=("known-folder",),
        )
        for item_id, name, category, reason in specs
    ]


def _template_items() -> list[ItemTemplate]:
    home = home_dir()
    roaming = appdata_roaming()
    local = appdata_local()
    pdata = program_data()
    return [
        ItemTemplate(
            "ssh",
            "SSH keys and config",
            "Developer",
            home / ".ssh",
            "SSH connection keys, known hosts, and client configuration.",
            "OpenSSH",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("developer", "credential"),
        ),
        ItemTemplate(
            "gitconfig",
            "Git user config",
            "Developer",
            home / ".gitconfig",
            "Global Git identity, aliases, signing, and credential helper settings.",
            "Git",
            RiskLevel.NORMAL,
            False,
            True,
            ("developer",),
        ),
        ItemTemplate(
            "git-credentials",
            "Git credentials",
            "Developer",
            home / ".git-credentials",
            "Stored Git remotes credentials; highly sensitive.",
            "Git",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("developer", "credential"),
        ),
        ItemTemplate(
            "gnupg",
            "GnuPG keyring",
            "Developer",
            home / ".gnupg",
            "GPG keys and trust database used for signing and encryption.",
            "GnuPG",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("developer", "credential"),
        ),
        ItemTemplate(
            "npmrc",
            "npm config",
            "Developer",
            home / ".npmrc",
            "npm registries and authentication tokens.",
            "npm",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("developer", "credential"),
        ),
        ItemTemplate(
            "pypirc",
            "Python package publishing config",
            "Developer",
            home / ".pypirc",
            "Python package index publishing credentials.",
            "Python",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("developer", "credential"),
        ),
        ItemTemplate(
            "condarc",
            "Conda config",
            "Developer",
            home / ".condarc",
            "Conda channels, proxy settings, and environment defaults.",
            "Conda",
            RiskLevel.NORMAL,
            False,
            True,
            ("developer", "python"),
        ),
        ItemTemplate(
            "vscode-user",
            "VS Code user data",
            "Developer",
            roaming / "Code" / "User",
            "VS Code settings, keybindings, snippets, tasks, and workspace storage.",
            "Visual Studio Code",
            RiskLevel.NORMAL,
            False,
            True,
            ("developer", "editor"),
        ),
        ItemTemplate(
            "vscode-extensions",
            "VS Code extensions",
            "Developer",
            home / ".vscode" / "extensions",
            "Installed VS Code extensions; useful for reconstructing editor setup.",
            "Visual Studio Code",
            RiskLevel.LARGE,
            False,
            False,
            ("developer", "editor"),
        ),
        ItemTemplate(
            "windows-terminal",
            "Windows Terminal settings",
            "Terminal",
            local / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState",
            "Windows Terminal profiles, themes, and startup settings.",
            "Windows Terminal",
            RiskLevel.NORMAL,
            False,
            True,
            ("terminal",),
        ),
        ItemTemplate(
            "powershell",
            "PowerShell profile",
            "Terminal",
            known_folder("Documents") / "PowerShell",
            "PowerShell profile scripts, modules, and console preferences.",
            "PowerShell",
            RiskLevel.NORMAL,
            False,
            True,
            ("terminal", "developer"),
        ),
        ItemTemplate(
            "chrome-profile",
            "Chrome user profile",
            "Browser",
            local / "Google" / "Chrome" / "User Data",
            "Chrome bookmarks, extensions, preferences, history, cookies, and profiles.",
            "Google Chrome",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("browser",),
        ),
        ItemTemplate(
            "edge-profile",
            "Edge user profile",
            "Browser",
            local / "Microsoft" / "Edge" / "User Data",
            "Edge bookmarks, extensions, preferences, history, cookies, and profiles.",
            "Microsoft Edge",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("browser",),
        ),
        ItemTemplate(
            "firefox-profile",
            "Firefox profiles",
            "Browser",
            roaming / "Mozilla" / "Firefox",
            "Firefox profiles, bookmarks, extensions, history, and cookies.",
            "Mozilla Firefox",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("browser",),
        ),
        ItemTemplate(
            "wechat-files",
            "WeChat data",
            "Chat",
            home / "Documents" / "xwechat_files",
            "WeChat chat history, account files, received files, media, and message databases.",
            "WeChat",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("chat",),
        ),
        ItemTemplate(
            "qq-files",
            "QQ Tencent Files",
            "Chat",
            home / "Documents" / "Tencent Files",
            "The whole Tencent Files folder used by QQ for chat history, received files, "
            "media, and account folders.",
            "QQ",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("chat",),
        ),
        ItemTemplate(
            "telegram",
            "Telegram Desktop data",
            "Chat",
            roaming / "Telegram Desktop" / "tdata",
            "Telegram Desktop local session and cached account data.",
            "Telegram",
            RiskLevel.SENSITIVE,
            True,
            False,
            ("chat",),
        ),
        ItemTemplate(
            "jetbrains",
            "JetBrains settings",
            "Developer",
            roaming / "JetBrains",
            "JetBrains IDE settings, plugins, options, and recent project metadata.",
            "JetBrains IDEs",
            RiskLevel.NORMAL,
            False,
            True,
            ("developer", "editor"),
        ),
        ItemTemplate(
            "scoop",
            "Scoop apps and buckets",
            "Package Manager",
            home / "scoop",
            "Scoop installed apps, shims, buckets, and manifests.",
            "Scoop",
            RiskLevel.LARGE,
            False,
            False,
            ("package-manager",),
        ),
        ItemTemplate(
            "chocolatey",
            "Chocolatey config",
            "Package Manager",
            pdata / "chocolatey",
            "Chocolatey package metadata and local package manager configuration.",
            "Chocolatey",
            RiskLevel.SYSTEM,
            False,
            False,
            ("package-manager",),
        ),
        ItemTemplate(
            "winget",
            "winget settings",
            "Package Manager",
            local / "Packages" / "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe" / "LocalState",
            "Windows Package Manager sources, settings, and cache metadata.",
            "winget",
            RiskLevel.NORMAL,
            False,
            True,
            ("package-manager",),
        ),
    ]


def discover_backup_items(include_missing: bool = True) -> list[BackupItem]:
    items = _known_folder_items()
    for template in _template_items():
        if include_missing or template.path.exists():
            items.append(
                BackupItem(
                    id=template.id,
                    name=template.name,
                    category=template.category,
                    path=template.path,
                    reason=template.reason,
                    software=template.software,
                    risk=template.risk,
                    sensitive=template.sensitive,
                    default_selected=template.default_selected and not template.sensitive,
                    tags=template.tags,
                )
            )
    return items
