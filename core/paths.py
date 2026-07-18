from __future__ import annotations

import ctypes
import os
import uuid
from pathlib import Path

KNOWN_FOLDER_IDS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


def home_dir() -> Path:
    return Path.home()


def env_path(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else fallback


def appdata_roaming() -> Path:
    return env_path("APPDATA", home_dir() / "AppData" / "Roaming")


def appdata_local() -> Path:
    return env_path("LOCALAPPDATA", home_dir() / "AppData" / "Local")


def program_data() -> Path:
    return env_path("PROGRAMDATA", Path("C:/ProgramData"))


def known_folder(name: str) -> Path:
    folder_id = KNOWN_FOLDER_IDS.get(name)
    if not folder_id:
        raise KeyError(f"Unknown Known Folder: {name}")

    try:
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

            @classmethod
            def from_uuid(cls, value: uuid.UUID) -> GUID:
                data4 = (ctypes.c_ubyte * 8).from_buffer_copy(value.bytes[8:])
                return cls(value.time_low, value.time_mid, value.time_hi_version, data4)

        shell32 = ctypes.windll.shell32
        path_ptr = ctypes.c_wchar_p()
        guid = GUID.from_uuid(uuid.UUID(folder_id))
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(guid),
            0,
            wintypes.HANDLE(0),
            ctypes.byref(path_ptr),
        )
        if result != 0:
            raise OSError(f"SHGetKnownFolderPath failed: {result}")
        return Path(path_ptr.value)
    except Exception:
        defaults = {
            "Desktop": home_dir() / "Desktop",
            "Documents": home_dir() / "Documents",
            "Downloads": home_dir() / "Downloads",
            "Music": home_dir() / "Music",
            "Pictures": home_dir() / "Pictures",
            "Videos": home_dir() / "Videos",
        }
        return defaults[name]
