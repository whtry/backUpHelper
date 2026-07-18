from __future__ import annotations

from core.models import InstalledApplication

UNINSTALL_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
WOW64_UNINSTALL_SUBKEY = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"


def _read_value(key, name: str) -> str | None:
    try:
        import winreg

        value, _ = winreg.QueryValueEx(key, name)
        return str(value) if value else None
    except OSError:
        return None


def _scan_root(root_name: str, root, subkey: str) -> list[InstalledApplication]:
    import winreg

    apps: list[InstalledApplication] = []
    try:
        parent = winreg.OpenKey(root, subkey)
    except OSError:
        return apps

    with parent:
        for index in range(winreg.QueryInfoKey(parent)[0]):
            try:
                child_name = winreg.EnumKey(parent, index)
                child_path = f"{subkey}\\{child_name}"
                with winreg.OpenKey(parent, child_name) as child:
                    display_name = _read_value(child, "DisplayName")
                    system_component = _read_value(child, "SystemComponent")
                    if not display_name or system_component == "1":
                        continue
                    apps.append(
                        InstalledApplication(
                            name=display_name,
                            version=_read_value(child, "DisplayVersion"),
                            publisher=_read_value(child, "Publisher"),
                            install_location=_read_value(child, "InstallLocation"),
                            uninstall_string=_read_value(child, "UninstallString"),
                            icon_path=_read_value(child, "DisplayIcon"),
                            registry_key=f"{root_name}\\{child_path}",
                        )
                    )
            except OSError:
                continue
    return apps


def list_installed_applications() -> list[InstalledApplication]:
    try:
        import winreg
    except ImportError:
        return []

    apps: list[InstalledApplication] = []
    for root_name, root in [
        ("HKCU", winreg.HKEY_CURRENT_USER),
        ("HKLM", winreg.HKEY_LOCAL_MACHINE),
    ]:
        apps.extend(_scan_root(root_name, root, UNINSTALL_SUBKEY))
        apps.extend(_scan_root(root_name, root, WOW64_UNINSTALL_SUBKEY))

    deduped: dict[tuple[str, str | None, str | None], InstalledApplication] = {}
    for app in apps:
        deduped[(app.name, app.version, app.publisher)] = app
    return sorted(deduped.values(), key=lambda item: item.name.lower())
