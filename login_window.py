"""
login_window.py — Giao diện đăng nhập / đăng ký DynaTool (CustomTkinter).
"""

import os
import sys
import threading

import customtkinter as ctk
import requests

import auth_manager

# ── Theme (đồng bộ Premium) ───────────────────────────────────────────────────
BG_DEEP        = "#07071a"
BG_CARD        = "#0e0e2a"
BG_INPUT       = "#14143a"
NEON_BLUE      = "#00d4ff"
NEON_PURPLE    = "#7c3aed"
TEXT_PRIMARY   = "#f0f0ff"
TEXT_SECONDARY = "#8888bb"
TEXT_DIM       = "#555577"
BORDER         = "#1e1e4a"
ERROR_RED      = "#ef4444"
SUCCESS_GREEN  = "#10b981"


class LoginApp(ctk.CTk):
  """Cửa sổ đăng nhập — hiện trước khi vào DynaTool."""

  def __init__(self):
    super().__init__()
    self.success = False
    self._busy = False

    self.title("Dyna Tool — Đăng nhập")
    self.geometry("480x720")
    self.resizable(False, False)
    self.configure(fg_color=BG_DEEP)

    self._center_window()
    self._set_icon()
    self._build_ui()
    self.protocol("WM_DELETE_WINDOW", self._on_close)

  def _set_icon(self):
    try:
      base = os.path.dirname(os.path.abspath(__file__))
      icon = os.path.join(base, "icon.ico")
      if os.path.exists(icon):
        self.iconbitmap(icon)
    except Exception:
      pass

  def _center_window(self):
    self.update_idletasks()
    w, h = 480, 720
    sw = self.winfo_screenwidth()
    sh = self.winfo_screenheight()
    self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

  def _build_ui(self):
    # Header
    header = ctk.CTkFrame(self, fg_color="transparent")
    header.pack(fill="x", padx=40, pady=(36, 8))

    ctk.CTkLabel(
      header, text="⚡ DYNA TOOL",
      font=ctk.CTkFont(size=28, weight="bold"),
      text_color=NEON_BLUE,
    ).pack()
    ctk.CTkLabel(
      header, text="Nền tảng tự động hóa Douyin → TikTok",
      font=ctk.CTkFont(size=13),
      text_color=TEXT_SECONDARY,
    ).pack(pady=(4, 0))

    self.card = ctk.CTkFrame(
      self, fg_color=BG_CARD, corner_radius=18,
      border_width=1, border_color=BORDER,
    )
    self.card.pack(fill="both", expand=True, padx=36, pady=(16, 28))

    self.error_label = ctk.CTkLabel(
      self.card, text="",
      font=ctk.CTkFont(size=12),
      text_color=ERROR_RED,
      wraplength=360,
    )
    self.error_label.pack(pady=(16, 0))

    self.login_frame = ctk.CTkFrame(self.card, fg_color="transparent")
    self.register_frame = ctk.CTkFrame(self.card, fg_color="transparent")

    self._build_login_form()
    self._build_register_form()

    self.login_frame.pack(fill="both", expand=True, padx=28, pady=(8, 20))
    self.register_frame.pack_forget()

    footer = ctk.CTkLabel(
      self, text="© Dyna Tool · Bảo mật end-to-end",
      font=ctk.CTkFont(size=11),
      text_color=TEXT_DIM,
    )
    footer.pack(pady=(0, 12))

  def _field(self, parent, label: str, show: str = ""):
    ctk.CTkLabel(
      parent, text=label, anchor="w",
      font=ctk.CTkFont(size=12, weight="bold"),
      text_color=TEXT_SECONDARY,
    ).pack(fill="x", pady=(12, 4))
    entry = ctk.CTkEntry(
      parent, height=44, corner_radius=10,
      fg_color=BG_INPUT, border_color=BORDER, border_width=1,
      text_color=TEXT_PRIMARY, placeholder_text_color=TEXT_DIM,
      font=ctk.CTkFont(size=14),
      show=show,
    )
    entry.pack(fill="x")
    return entry

  def _build_login_form(self):
    ctk.CTkLabel(
      self.login_frame, text="Đăng nhập",
      font=ctk.CTkFont(size=20, weight="bold"),
      text_color=TEXT_PRIMARY,
    ).pack(anchor="w", pady=(8, 4))
    ctk.CTkLabel(
      self.login_frame, text="Chào mừng trở lại! Nhập thông tin tài khoản.",
      font=ctk.CTkFont(size=12),
      text_color=TEXT_SECONDARY,
    ).pack(anchor="w")

    self.login_username = self._field(self.login_frame, "Tên đăng nhập")
    self.login_password = self._field(self.login_frame, "Mật khẩu", show="•")

    self.login_btn = ctk.CTkButton(
      self.login_frame, text="Đăng nhập",
      height=46, corner_radius=10,
      font=ctk.CTkFont(size=15, weight="bold"),
      fg_color=NEON_BLUE, hover_color="#00a8cc",
      text_color="#001018",
      command=self._do_login,
    )
    self.login_btn.pack(fill="x", pady=(24, 12))

    self.login_username.bind("<Return>", lambda e: self._do_login())
    self.login_password.bind("<Return>", lambda e: self._do_login())

    row = ctk.CTkFrame(self.login_frame, fg_color="transparent")
    row.pack(fill="x", pady=(4, 0))
    ctk.CTkLabel(
      row, text="Chưa có tài khoản?",
      font=ctk.CTkFont(size=12),
      text_color=TEXT_SECONDARY,
    ).pack(side="left")
    ctk.CTkButton(
      row, text="Tạo tài khoản",
      width=120, height=32, corner_radius=8,
      fg_color="transparent", hover_color=BG_INPUT,
      text_color=NEON_BLUE, font=ctk.CTkFont(size=12, weight="bold"),
      command=self._show_register,
    ).pack(side="right")

  def _build_register_form(self):
    ctk.CTkLabel(
      self.register_frame, text="Tạo tài khoản",
      font=ctk.CTkFont(size=20, weight="bold"),
      text_color=TEXT_PRIMARY,
    ).pack(anchor="w", pady=(8, 4))
    ctk.CTkLabel(
      self.register_frame, text="Điền thông tin để bắt đầu sử dụng Dyna Tool.",
      font=ctk.CTkFont(size=12),
      text_color=TEXT_SECONDARY,
    ).pack(anchor="w")

    self.reg_phone = self._field(self.register_frame, "Số điện thoại")
    self.reg_phone.configure(placeholder_text="0912345678")
    self.reg_username = self._field(self.register_frame, "Tên đăng nhập")
    self.reg_password = self._field(self.register_frame, "Mật khẩu", show="•")
    self.reg_password2 = self._field(self.register_frame, "Xác nhận mật khẩu", show="•")

    ctk.CTkLabel(
      self.register_frame,
      text="Mật khẩu tối thiểu 6 ký tự · Tên đăng nhập: chữ, số, _",
      font=ctk.CTkFont(size=11),
      text_color=TEXT_DIM,
    ).pack(anchor="w", pady=(8, 0))

    self.register_btn = ctk.CTkButton(
      self.register_frame, text="Tạo tài khoản",
      height=46, corner_radius=10,
      font=ctk.CTkFont(size=15, weight="bold"),
      fg_color=NEON_PURPLE, hover_color="#6d28d9",
      command=self._do_register,
    )
    self.register_btn.pack(fill="x", pady=(16, 12))

    row = ctk.CTkFrame(self.register_frame, fg_color="transparent")
    row.pack(fill="x")
    ctk.CTkLabel(
      row, text="Đã có tài khoản?",
      font=ctk.CTkFont(size=12),
      text_color=TEXT_SECONDARY,
    ).pack(side="left")
    ctk.CTkButton(
      row, text="Đăng nhập",
      width=100, height=32, corner_radius=8,
      fg_color="transparent", hover_color=BG_INPUT,
      text_color=NEON_BLUE, font=ctk.CTkFont(size=12, weight="bold"),
      command=self._show_login,
    ).pack(side="right")

  def _show_error(self, msg: str):
    self.error_label.configure(text=msg or "")

  def _set_busy(self, busy: bool, btn: ctk.CTkButton, label: str):
    self._busy = busy
    btn.configure(state="disabled" if busy else "normal", text=label)

  def _show_register(self):
    self._show_error("")
    self.login_frame.pack_forget()
    self.register_frame.pack(fill="both", expand=True, padx=28, pady=(8, 20))

  def _show_login(self):
    self._show_error("")
    self.register_frame.pack_forget()
    self.login_frame.pack(fill="both", expand=True, padx=28, pady=(8, 20))

  def _do_login(self):
    if self._busy:
      return
    user = self.login_username.get().strip()
    pwd = self.login_password.get()
    if not user or not pwd:
      self._show_error("Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.")
      return
    self._set_busy(True, self.login_btn, "Đang đăng nhập...")
    threading.Thread(target=self._login_worker, args=(user, pwd), daemon=True).start()

  def _login_worker(self, user: str, pwd: str):
    try:
      auth_manager.login(user, pwd)
      self.after(0, self._on_auth_success)
    except requests.ConnectionError:
      self.after(0, lambda: self._on_auth_fail(
        "Không kết nối được server.\nKiểm tra payment server đang chạy và PAYMENT_API_URL."
      ))
    except ValueError as e:
      self.after(0, lambda: self._on_auth_fail(str(e)))
    except Exception as e:
      self.after(0, lambda: self._on_auth_fail(f"Lỗi: {e}"))

  def _do_register(self):
    if self._busy:
      return
    phone = self.reg_phone.get().strip()
    user = self.reg_username.get().strip()
    pwd = self.reg_password.get()
    pwd2 = self.reg_password2.get()
    if not all([phone, user, pwd, pwd2]):
      self._show_error("Vui lòng điền đầy đủ các trường.")
      return
    if pwd != pwd2:
      self._show_error("Mật khẩu xác nhận không khớp.")
      return
    self._set_busy(True, self.register_btn, "Đang tạo tài khoản...")
    threading.Thread(
      target=self._register_worker, args=(phone, user, pwd), daemon=True
    ).start()

  def _register_worker(self, phone: str, user: str, pwd: str):
    try:
      auth_manager.register(phone, user, pwd)
      self.after(0, self._on_auth_success)
    except requests.ConnectionError:
      self.after(0, lambda: self._on_auth_fail(
        "Không kết nối được server.\nChạy payment server trước khi đăng ký."
      ))
    except ValueError as e:
      self.after(0, lambda: self._on_auth_fail(str(e)))
    except Exception as e:
      self.after(0, lambda: self._on_auth_fail(f"Lỗi: {e}"))

  def _on_auth_fail(self, msg: str):
    self._set_busy(False, self.login_btn, "Đăng nhập")
    self._set_busy(False, self.register_btn, "Tạo tài khoản")
    self._show_error(msg)

  def _on_auth_success(self):
    self.success = True
    self._show_error("")
    self.destroy()

  def _on_close(self):
    self.success = False
    self.destroy()


def run_login_gate() -> bool:
  """
  Hiện màn hình đăng nhập nếu chưa có session hợp lệ.
  Trả về True nếu được phép vào app chính.
  """
  ctk.set_appearance_mode("Dark")
  ctk.set_default_color_theme("blue")

  if auth_manager.try_auto_login():
    return True

  app = LoginApp()
  app.mainloop()
  return app.success


def launch_app():
  """Điểm vào: login → DynaTool chính."""
  if not run_login_gate():
    sys.exit(0)
  from dyna_tool import DynaToolApp
  DynaToolApp().mainloop()


if __name__ == "__main__":
  launch_app()
