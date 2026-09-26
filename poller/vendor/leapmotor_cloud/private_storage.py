"""Private account storage: POSIX modes or a protected current-user Windows DACL."""
import os
from pathlib import Path


def _is_link(path):
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def ensure_private_directory(path):
    path = Path(path)
    if _is_link(path):
        raise ValueError('Unsafe private directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != 'nt':
        if path.stat().st_mode & 0o077:
            raise ValueError('Private directory permissions required')
        return path
    _windows_directory(path, protect=True)
    return path


def validate_private_directory(path):
    """Validate an existing storage root without creating or changing it.

    Windows roots must have a protected DACL containing only the process user's
    inheritable full-control entry. POSIX roots must exclude group/other access.
    """
    path = Path(path)
    if _is_link(path) or not path.is_dir():
        raise ValueError('Unsafe private directory')
    if os.name == 'nt':
        _windows_directory(path, protect=False)
    elif path.stat().st_mode & 0o077:
        raise ValueError('Private directory permissions required')
    return path


def validate_private_file(path):
    """Validate an existing private file, including explicit Windows file ACEs."""
    path = Path(path)
    if _is_link(path) or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('Unsafe private file')
    if os.name == 'nt':
        _windows_directory(path, protect=False, directory=False)
    elif path.stat().st_mode & 0o077:
        raise ValueError('Private file permissions required')
    return path


def _windows_directory(path, *, protect, directory=True):
    # Convert a protected DACL with ONE inheritable allow entry for the process
    # user. Do not mistake Windows' synthetic POSIX mode bits for an ACL check.
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    class ACL(ctypes.Structure):
        _fields_ = [('revision', ctypes.c_byte), ('unused', ctypes.c_byte),
                    ('size', wintypes.WORD), ('count', wintypes.WORD), ('unused2', wintypes.WORD)]

    class ACE(ctypes.Structure):
        _fields_ = [('kind', ctypes.c_byte), ('flags', ctypes.c_byte),
                    ('size', wintypes.WORD), ('mask', wintypes.DWORD), ('sid', wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise OSError('Private directory protection unavailable')
    sid_text = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise OSError('Private directory protection unavailable')
        data = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
            raise OSError('Private directory protection unavailable')
        sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError('Private directory protection unavailable')
        if protect:
            sddl = 'D:P(A;' + ('OICI' if directory else '') + ';FA;;;' + sid_text.value + ')'
            if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
                raise OSError('Private directory protection unavailable')
            if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
                raise OSError('Private directory protection unavailable')
        # Read back the actual descriptor, failing closed on unsupported storage
        # (for example filesystems that do not enforce Windows access control).
        required = wintypes.DWORD()
        advapi.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(required))
        if not required.value:
            raise OSError('Private directory protection unavailable')
        actual = ctypes.create_string_buffer(required.value)
        if not advapi.GetFileSecurityW(str(path), 4, actual, required.value, ctypes.byref(required)):
            raise OSError('Private directory protection unavailable')
        control, revision = wintypes.WORD(), wintypes.DWORD()
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if (not advapi.GetSecurityDescriptorControl(actual, ctypes.byref(control), ctypes.byref(revision))
                or (directory and not control.value & 0x1000)
                or not advapi.GetSecurityDescriptorDacl(actual, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted))
                or not present.value or not acl.value
                or ctypes.cast(acl, ctypes.POINTER(ACL)).contents.count != 1):
            raise ValueError('Private directory permissions required')
        entry = ctypes.c_void_p()
        if not advapi.GetAce(acl, 0, ctypes.byref(entry)):
            raise OSError('Private directory protection unavailable')
        ace = ctypes.cast(entry, ctypes.POINTER(ACE)).contents
        if (ace.kind != 0 or ace.flags not in ((3,) if directory else (0, 16)) or ace.mask != 0x1f01ff
                or not advapi.EqualSid(entry.value + ACE.sid.offset, sid)):
            raise ValueError('Private directory permissions required')
    finally:
        if descriptor.value: kernel.LocalFree(descriptor)
        if sid_text: kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        kernel.CloseHandle(token)
