"""
premium_modal.py
────────────────
Màn Premium nhúng trong DynaTool (không mở cửa sổ riêng).
  • Danh sách gói giá
  • Đăng kí → QR thanh toán (VietQR) trong cùng khung
  • Poll trạng thái thanh toán từ server
  • Khi paid → lưu license + callback on_licensed
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
import threading
import math
import time
import io
import os
import urllib.request
from datetime import datetime

import license_manager

# ── Màu sắc ──────────────────────────────────────────────────────────────────
BG_DEEP       = "#07071a"
BG_CARD       = "#0e0e2a"
BG_CARD2      = "#14143a"
NEON_BLUE     = "#00d4ff"
NEON_PURPLE   = "#7c3aed"
ACCENT_GOLD   = "#f59e0b"
TEXT_PRIMARY  = "#f0f0ff"
TEXT_SECONDARY= "#8888bb"
TEXT_DIM      = "#44446a"
BORDER_DIM    = "#1e1e4a"
SUCCESS_GREEN = "#10b981"

PLANS = [
    {"days": 7,   "label": "7 ngày",          "price": 99000,   "per_day": "~14k/ngày",  "tag": None,            "highlight": False},
    {"days": 14,  "label": "14 ngày",         "price": 169000,  "per_day": "~12k/ngày",  "tag": None,            "highlight": False},
    {"days": 30,  "label": "30 ngày",         "price": 249000,  "per_day": "~8k/ngày",   "tag": "Phổ biến ⭐",   "highlight": True},
    {"days": 60,  "label": "60 ngày",         "price": 449000,  "per_day": "~7.5k/ngày", "tag": None,            "highlight": False},
    {"days": 90,  "label": "90 ngày",         "price": 599000,  "per_day": "~6.7k/ngày", "tag": None,            "highlight": False},
    {"days": 365, "label": "1 năm (365 ngày)","price": 1799000, "per_day": "~4.9k/ngày", "tag": "Tiết kiệm 41%", "highlight": False, "yearly": True},
]


# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENT PANEL — QR + polling (nhúng trong PremiumPanel)
# ═══════════════════════════════════════════════════════════════════════════════
class PaymentPanel(ctk.CTkFrame):
    def __init__(self, parent, order_data: dict, on_success=None, on_back=None):
        super().__init__(parent, fg_color="transparent")
        self.order_data = order_data
        self.on_success = on_success
        self.on_back = on_back
        self._polling = True

        self._build()

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _build(self):
        plan   = self.order_data
        amount = plan["amount"]
        oid    = plan["order_id"]

        # Header
        ctk.CTkLabel(self, text="💳 Quét QR để thanh toán",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(pady=(20, 4))

        ctk.CTkLabel(self,
                     text=f"{plan['plan_name']}  •  {amount:,.0f}đ",
                     font=ctk.CTkFont(size=14), text_color=NEON_BLUE).pack(pady=(0, 12))

        # QR Image area
        self.qr_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=14,
                                     border_width=1, border_color=BORDER_DIM)
        self.qr_frame.pack(padx=40, pady=(0, 16), fill="x")

        self.qr_label = ctk.CTkLabel(self.qr_frame, text="⏳ Đang tải QR...",
                                     font=ctk.CTkFont(size=13), text_color=TEXT_SECONDARY)
        self.qr_label.pack(pady=20)

        # Load QR in background
        threading.Thread(target=self._load_qr, args=(plan["qr_url"],), daemon=True).start()

        # Bank info
        info_frame = ctk.CTkFrame(self, fg_color=BG_CARD2, corner_radius=10)
        info_frame.pack(padx=30, pady=(0, 14), fill="x")

        rows = [
            ("Ngân hàng",      plan["bank_id"]),
            ("Số tài khoản",   plan["account_no"]),
            ("Chủ tài khoản",  plan["account_name"]),
            ("Số tiền",        f"{amount:,.0f}đ"),
            ("Nội dung CK",    oid),
        ]
        for k, v in rows:
            row = ctk.CTkFrame(info_frame, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=4)
            ctk.CTkLabel(row, text=k, font=ctk.CTkFont(size=12),
                         text_color=TEXT_DIM, width=120, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=v, font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=TEXT_PRIMARY if k != "Nội dung CK" else NEON_BLUE).pack(side="left")

        # Status indicator
        self.status_label = ctk.CTkLabel(self,
                                         text="⏳ Đang chờ thanh toán...",
                                         font=ctk.CTkFont(size=13),
                                         text_color=TEXT_SECONDARY)
        self.status_label.pack(pady=8)

        self.progress = ctk.CTkProgressBar(self, mode="indeterminate",
                                           progress_color=NEON_BLUE, fg_color=BG_CARD2)
        self.progress.pack(padx=40, fill="x", pady=(0, 12))
        self.progress.start()

        ctk.CTkLabel(self,
                     text="⚠️  QR hết hạn sau 30 phút. Ghi đúng nội dung chuyển khoản.",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(pady=(0, 10))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=4)
        ctk.CTkButton(
            btn_row, text="← Quay lại", width=110, height=34,
            fg_color="transparent", border_width=1, border_color=BORDER_DIM,
            text_color=TEXT_SECONDARY, hover_color="#1a1a3a",
            command=self._on_back,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_row, text="Đóng", width=100, height=34,
            fg_color="transparent", border_width=1, border_color=BORDER_DIM,
            text_color=TEXT_SECONDARY, hover_color="#1a1a3a",
            command=self._on_close,
        ).pack(side="left", padx=6)

    def _load_qr(self, url: str):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                img_data = resp.read()
            img = Image.open(io.BytesIO(img_data)).resize((220, 220), Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(220, 220))
            self.after(0, lambda: self.qr_label.configure(image=ctk_img, text=""))
            self._qr_img = ctk_img  # giữ reference
        except Exception as e:
            self.after(0, lambda: self.qr_label.configure(text=f"❌ Không tải được QR\n{e}"))

    def _poll_loop(self):
        order_id = self.order_data["order_id"]
        while self._polling:
            time.sleep(4)
            if not self._polling:
                break
            try:
                status_data = license_manager.poll_payment_status(order_id)
                st = status_data.get("status")

                if st == "paid":
                    # Lưu license từ server
                    license_manager.save_license_from_server()
                    self.after(0, self._on_paid)
                    return
                elif st == "expired":
                    self.after(0, lambda: self.status_label.configure(
                        text="❌ QR đã hết hạn. Vui lòng tạo đơn mới.",
                        text_color="#ef4444"))
                    self._polling = False
                    return
            except Exception:
                pass  # Mất kết nối → thử lại sau

    def _on_paid(self):
        self._polling = False
        self.progress.stop()
        self.progress.configure(progress_color=SUCCESS_GREEN)
        self.progress.set(1.0)
        self.status_label.configure(
            text="✅ Thanh toán thành công! Cảm ơn bạn ❤️",
            text_color=SUCCESS_GREEN)
        self.after(2500, self._finish)

    def _finish(self):
        self._polling = False
        if self.on_success:
            self.on_success()

    def _on_back(self):
        self._polling = False
        if self.on_back:
            self.on_back()

    def _on_close(self):
        self._polling = False
        if self.on_back:
            self.on_back()
        elif self.winfo_toplevel():
            self.winfo_toplevel().destroy()


# Giữ tên cũ để tương thích (nếu có chỗ gọi PaymentWindow)
PaymentWindow = PaymentPanel


# ═══════════════════════════════════════════════════════════════════════════════
#  PREMIUM PANEL — nhúng trong main_frame DynaTool
# ═══════════════════════════════════════════════════════════════════════════════
class PremiumPanel(ctk.CTkFrame):
    def __init__(self, parent=None, on_licensed=None, on_close=None):
        super().__init__(parent, fg_color="transparent")
        self.on_licensed = on_licensed
        self.on_close = on_close
        self._angle = 0.0
        self._selected_days = None
        self._payment_panel = None

        self._shell = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=14,
                                   border_width=1, border_color=BORDER_DIM)
        self._shell.pack(fill="both", expand=True)

        self._bc = tk.Canvas(self._shell, bg=BG_DEEP, highlightthickness=0)
        self._bc.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._shell.bind("<Configure>", self._on_shell_resize)

        self._outer = ctk.CTkFrame(self._shell, fg_color="transparent")
        self._outer.place(relx=0, rely=0, relwidth=1, relheight=1)

        self._plans_frame = ctk.CTkScrollableFrame(
            self._outer, fg_color="transparent",
            scrollbar_button_color=BORDER_DIM,
            scrollbar_button_hover_color=NEON_BLUE,
        )
        self._plans_frame.pack(fill="both", expand=True, padx=12, pady=12)

        self._payment_host = ctk.CTkFrame(self._outer, fg_color="transparent")

        self._build_plans_content()
        self._animate_border()

    def _on_shell_resize(self, _event=None):
        w = max(self._shell.winfo_width(), 4)
        h = max(self._shell.winfo_height(), 4)
        self._bc.configure(width=w, height=h)
        self._bc.delete("bg")
        self._bc.create_rectangle(2, 2, w - 2, h - 2, fill=BG_CARD, outline="", tags="bg")
        self._bc.tag_lower("bg")

    def _animate_border(self):
        if not self.winfo_exists():
            return
        self._angle = (self._angle + 1.8) % 360
        t = (math.sin(math.radians(self._angle)) + 1) / 2
        r = int(0x00 + t * 0x7c)
        g = int(0xd4 * (1 - t))
        b = int(0xff - t * (0xff - 0xed))
        color = f"#{r:02x}{g:02x}{b:02x}"
        w = max(self._shell.winfo_width(), 4)
        h = max(self._shell.winfo_height(), 4)
        self._bc.delete("border")
        self._bc.create_rectangle(0, 0, w - 1, h - 1, outline=color, width=2, tags="border")
        self.after(30, self._animate_border)

    def _build_plans_content(self):
        outer = self._plans_frame

        # Header
        ctk.CTkLabel(outer, text="⚡", font=ctk.CTkFont(size=44), text_color=NEON_BLUE).pack(pady=(18, 2))
        ctk.CTkLabel(outer, text="Mở khóa Tool theo các gói",
                     font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(pady=(0, 2))
        ctk.CTkLabel(outer, text="Truy cập đầy đủ tính năng · Không giới hạn · Hỗ trợ 24/7",
                     font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(pady=(0, 12))

        # Divider
        tk.Canvas(outer, height=1, bg=BORDER_DIM, highlightthickness=0).pack(fill="x", padx=20, pady=(0, 10))

        # Plan rows
        self._plan_frames = {}
        plan_container = ctk.CTkFrame(outer, fg_color="transparent")
        plan_container.pack(fill="x", padx=16)

        for plan in PLANS:
            if plan.get("yearly"):
                sep = ctk.CTkFrame(plan_container, fg_color="transparent")
                sep.pack(fill="x", pady=(6, 2))
                ctk.CTkLabel(sep, text="─── hoặc ──────────────────────────",
                             font=ctk.CTkFont(size=10), text_color=TEXT_DIM).pack(side="left", padx=4)
                ctk.CTkLabel(sep, text="✦ Tiết kiệm tới 41% với gói năm",
                             font=ctk.CTkFont(size=11, weight="bold"), text_color=ACCENT_GOLD).pack(side="right", padx=4)

            self._build_plan_row(plan_container, plan)

        # Divider
        tk.Canvas(outer, height=1, bg=BORDER_DIM, highlightthickness=0).pack(fill="x", padx=20, pady=(14, 8))

        # Nút đăng kí
        self.register_btn = ctk.CTkButton(
            outer,
            text="⚡ ĐĂNG KÍ NGAY",
            width=300, height=50,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=NEON_BLUE, hover_color="#00b8d9",
            text_color="#000000",
            state="disabled",
            command=self._on_register
        )
        self.register_btn.pack(pady=(4, 8))

        self.select_hint = ctk.CTkLabel(outer, text="← Chọn gói phía trên để tiếp tục",
                                        font=ctk.CTkFont(size=12), text_color=TEXT_DIM)
        self.select_hint.pack()

        # Footer
        ctk.CTkLabel(outer, text="💬  Cần gói thời hạn riêng? Liên hệ quản trị viên qua Telegram",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(pady=(10, 4))

        ctk.CTkButton(outer, text="← Quay lại Dashboard", width=180, height=32,
                      fg_color="transparent", border_width=1, border_color=BORDER_DIM,
                      text_color=TEXT_SECONDARY, hover_color="#1a1a3a",
                      font=ctk.CTkFont(size=12),
                      command=self._close_panel).pack(pady=(0, 6))

    def _build_plan_row(self, parent, plan):
        is_hl = plan.get("highlight", False)
        row = ctk.CTkFrame(parent,
                           fg_color=BG_CARD2 if is_hl else BG_CARD,
                           corner_radius=10,
                           border_width=1,
                           border_color=NEON_BLUE if is_hl else BORDER_DIM)
        row.pack(fill="x", pady=4)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=9)
        inner.columnconfigure(1, weight=1)

        lbl_f = ctk.CTkFrame(inner, fg_color="transparent")
        lbl_f.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(lbl_f,
                     text=f"Gói {plan['label']}",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=NEON_BLUE if is_hl else TEXT_PRIMARY).pack(side="left")
        if plan.get("tag"):
            tag_color = ACCENT_GOLD if plan.get("yearly") else NEON_PURPLE
            ctk.CTkLabel(lbl_f, text=f"  {plan['tag']}",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=tag_color).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(inner, text=plan["per_day"],
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).grid(row=0, column=1, sticky="e", padx=8)

        price_color = ACCENT_GOLD if plan.get("yearly") else (NEON_BLUE if is_hl else TEXT_PRIMARY)
        ctk.CTkLabel(inner,
                     text=f"{plan['price']:,}đ",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=price_color).grid(row=0, column=2, sticky="e")

        self._plan_frames[plan["days"]] = row

        def select(e=None, d=plan["days"], r=row):
            self._select_plan(d, r)

        row.bind("<Button-1>", select)
        for w in inner.winfo_children() + [inner]:
            w.bind("<Button-1>", select)

        def on_enter(e, r=row, hl=is_hl, d=plan["days"]):
            if self._selected_days != d:
                r.configure(fg_color="#181838" if not hl else "#16163e")
        def on_leave(e, r=row, hl=is_hl, d=plan["days"]):
            if self._selected_days != d:
                r.configure(fg_color=BG_CARD2 if hl else BG_CARD)
        row.bind("<Enter>", on_enter)
        row.bind("<Leave>", on_leave)

    def _select_plan(self, days: int, row):
        # Reset tất cả
        for d, r in self._plan_frames.items():
            plan = next(p for p in PLANS if p["days"] == d)
            r.configure(
                fg_color=BG_CARD2 if plan.get("highlight") else BG_CARD,
                border_color=NEON_BLUE if plan.get("highlight") else BORDER_DIM,
                border_width=1
            )

        # Highlight selected
        row.configure(fg_color="#1a1a50", border_color="#00d4ff", border_width=2)
        self._selected_days = days

        plan = next(p for p in PLANS if p["days"] == days)
        self.select_hint.configure(
            text=f"✓ Đã chọn: {plan['label']} — {plan['price']:,}đ",
            text_color=NEON_BLUE
        )
        self.register_btn.configure(state="normal")

    def _on_register(self):
        if not self._selected_days:
            return
        self.register_btn.configure(state="disabled", text="⏳ Đang tạo đơn hàng...")

        def _create():
            try:
                order = license_manager.create_order(self._selected_days)
                self.after(0, lambda: self._open_payment(order))
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda err=err_msg: [
                    self.register_btn.configure(state="normal", text="⚡ ĐĂNG KÍ NGAY"),
                    messagebox.showerror("Lỗi", f"Không thể tạo đơn hàng:\n{err}", parent=self)
                ])

        threading.Thread(target=_create, daemon=True).start()

    def _close_panel(self):
        if self.on_close:
            self.on_close()

    def _show_plans_view(self):
        if self._payment_panel and self._payment_panel.winfo_exists():
            self._payment_panel.destroy()
            self._payment_panel = None
        self._payment_host.pack_forget()
        self._plans_frame.pack(fill="both", expand=True, padx=12, pady=12)

    def _show_payment_view(self, order_data: dict):
        self._plans_frame.pack_forget()
        self._payment_host.pack(fill="both", expand=True, padx=12, pady=12)

        if self._payment_panel and self._payment_panel.winfo_exists():
            self._payment_panel.destroy()

        self._payment_panel = PaymentPanel(
            self._payment_host,
            order_data,
            on_success=self._on_payment_success,
            on_back=self._show_plans_view,
        )
        self._payment_panel.pack(fill="both", expand=True, padx=8, pady=8)

    def _open_payment(self, order_data: dict):
        self.register_btn.configure(text="⚡ ĐĂNG KÍ NGAY", state="normal")
        self._show_payment_view(order_data)

    def _on_payment_success(self):
        if self.on_licensed:
            self.on_licensed()


# Tên cũ (tương thích)
PremiumModal = PremiumPanel


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.geometry("900x650")
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(0, weight=1)
    PremiumPanel(root, on_close=root.destroy).grid(sticky="nsew", padx=20, pady=20)
    root.mainloop()
