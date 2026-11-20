"""오버레이 브리지가 잘못 건드린 창(VS Code 등)의 Win32 스타일·투명도·항상 위를 즉시 복구한다."""
import ctypes
from ctypes import wintypes

u = ctypes.windll.user32
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_POPUP = 0x80000000
FRAME = 0x00CF0000  # WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
BAD_EX = 0x080800A8  # WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_TOPMOST
HWND_NOTOPMOST = -2
SWP_NOMOVE, SWP_NOSIZE, SWP_FRAMECHANGED = 0x0002, 0x0001, 0x0020
SW_HIDE, SW_SHOW = 0, 5
LWA_ALPHA = 0x00000002

u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
u.SetWindowPos.restype = wintypes.BOOL
u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
u.SetLayeredWindowAttributes.restype = wintypes.BOOL
u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
u.ShowWindow.restype = wintypes.BOOL

CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
restored = []


def _cb(hwnd, _):
    if not u.IsWindowVisible(hwnd):
        return True
    cls = ctypes.create_unicode_buffer(128)
    u.GetClassNameW(hwnd, cls, 128)
    title = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, title, 512)
    ex = int(u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
    st = int(u.GetWindowLongPtrW(hwnd, GWL_STYLE))
    # 오버레이 스타일(WS_EX_NOACTIVATE + WS_EX_LAYERED)이 붙은 창 중 HUD 본체가 아닌 창 복구
    if cls.value == "Chrome_WidgetWin_1" and (ex & 0x08000000) and (ex & 0x00080000):
        if title.value.strip() != "페이커 오버레이":
            u.ShowWindow(hwnd, SW_HIDE)
            u.SetWindowLongPtrW(hwnd, GWL_STYLE, (st & ~WS_POPUP) | FRAME)
            u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex & ~BAD_EX)
            u.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
            u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED)
            u.ShowWindow(hwnd, SW_SHOW)
            restored.append(title.value or f"hwnd={hwnd}")
    return True


u.EnumWindows(CB(_cb), 0)
print(f"복구 완료: {len(restored)}개 창 -> {restored}")
