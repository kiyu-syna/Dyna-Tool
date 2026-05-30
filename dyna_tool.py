import customtkinter as ctk
import json
import os
import sys
import threading
import time
import subprocess
import webbrowser
import psutil
from main import run_bot_system
import shared_state
import stats_manager
import license_manager
import auth_manager
from datetime import datetime
import auto_downloader as _adl
import config as _config_module
import sheets_manager as _sheets_manager

# Import nặng sẽ được nạp sẵn trong luồng nền ngay khi khởi động
_bot_douyin_module = None

def _preload_bot_douyin():
    global _bot_douyin_module
    import bot_douyin
    _bot_douyin_module = bot_douyin

threading.Thread(target=_preload_bot_douyin, daemon=True).start()


# Thiết lập giao diện
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# ── UI tokens ─────────────────────────────────────────────────────────────────
UI_ACCENT     = "#00d4ff"
UI_BG_CARD    = "#0e0e2a"
UI_SUCCESS    = "#10b981"
UI_WARNING    = "#f59e0b"
UI_DANGER     = "#e74c3c"
UI_TEXT_DIM   = "#8888bb"


class DynaToolApp(ctk.CTk):
    def resource_path(self, relative_path):
        """ Lấy đường dẫn tuyệt đối tới file resource, hoạt động cho cả môi trường dev và PyInstaller """
        try:
            # PyInstaller tạo ra một thư mục tạm thời và lưu đường dẫn trong _MEIPASS
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    def __init__(self):
        super().__init__()

        self.title("Dyna Tool - Control Center")
        self.geometry("1100x650")
        
        # Thiết lập Icon cho cửa sổ
        try:
            icon_path = self.resource_path("icon.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception as e:
            print(f"Không thể load icon: {e}")

        # Đường dẫn file
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            if os.path.basename(exe_dir).lower() == "dist":
                base_path = os.path.dirname(exe_dir)
            else:
                base_path = exe_dir
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            
        self.settings_path = os.path.join(base_path, "settings.json")
        self.log_path = os.path.join(base_path, "system.log")
        self.lock_path = os.path.join(base_path, "bot.lock")
        self.main_script = os.path.join(base_path, "main.py")
        
        self.settings_data = self.load_settings()

        # Reset trạng thái scan về False mỗi khi mở tool (tránh đọc giá trị cũ từ state.json)
        shared_state.DOUYIN_CONTROL["running"] = False

        # Biến trạng thái
        self.bot_process = None
        self.is_monitoring_logs = True
        self._last_log_pos = 0
        self._scan_launching = False
        self._scan_cooldown_until = 0.0
        self._tab_indicators = {}
        self._log_lines = []
        self._log_filter = "ALL"
        self._active_tab = "dashboard"
        self._premium_auto_shown = False

        # Layout chính
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar_frame = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(10, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="DYNA TOOL", font=ctk.CTkFont(size=26, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 4))

        user = auth_manager.get_current_user()
        uname = user.get("username", "—") if user else "—"
        self.user_label = ctk.CTkLabel(
            self.sidebar_frame,
            text=f"👤 {uname}",
            font=ctk.CTkFont(size=12),
            text_color=("#666", "#aaa"),
        )
        self.user_label.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="w")

        self.tab_buttons = []
        tabs = [
            ("🏠 Dashboard", "dashboard"),
            ("🔍 Crawler", "crawler"),
            ("🌐 Browser", "browser"),
            ("⚙️ API Config", "api"),
            ("🤖 Telegram", "telegram"),
            ("📊 Thống kê", "stats"),
            ("📋 Logs", "logs"),
            ("📥 AutoDownload", "autodownload"),
        ]

        for i, (text, name) in enumerate(tabs):
            row = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent", height=38)
            row.grid(row=i + 2, column=0, padx=6, pady=1, sticky="ew")
            row.grid_columnconfigure(1, weight=1)

            indicator = ctk.CTkFrame(
                row, width=3, height=30, fg_color="transparent", corner_radius=2,
            )
            indicator.grid(row=0, column=0, padx=(6, 0), pady=4, sticky="ns")
            self._tab_indicators[name] = indicator

            btn = ctk.CTkButton(
                row, text=text, fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray70", "gray30"),
                anchor="w", height=34,
                command=lambda n=name: self.select_tab(n),
            )
            btn.grid(row=0, column=1, sticky="ew", padx=(0, 6))
            self.tab_buttons.append((name, btn))

        self.premium_widget = ctk.CTkButton(
            self.sidebar_frame,
            text="⚡ Premium\nĐang tải...",
            height=52,
            corner_radius=10,
            fg_color=UI_BG_CARD,
            hover_color="#1a1a3a",
            border_width=1,
            border_color="#2a2a4a",
            text_color=("#f59e0b", "#fbbf24"),
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
            command=lambda: self.select_tab("premium"),
        )
        self.premium_widget.grid(row=10, column=0, padx=10, pady=(4, 6), sticky="ew")

        self.logout_btn = ctk.CTkButton(
            self.sidebar_frame, text="🚪 Đăng xuất",
            fg_color="transparent", border_width=1,
            border_color=("gray70", "gray40"),
            text_color=("gray30", "gray80"),
            hover_color=("gray85", "#2a2a4a"),
            height=32, font=ctk.CTkFont(size=12),
            command=self._logout,
        )
        self.logout_btn.grid(row=11, column=0, padx=10, pady=(0, 16), sticky="ew")

        # Main Content Area
        self.main_frame = ctk.CTkFrame(self, corner_radius=15, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)

        # Tabs Content
        self.tabs_content = {}
        self.create_dashboard_tab()
        self.create_crawler_tab()
        self.create_browser_tab()
        self.create_api_tab()
        self.create_telegram_tab()
        self.create_stats_tab()
        self.create_logs_tab()
        self.create_premium_tab()
        self.create_downloader_tab()

        # Footer Buttons
        self.footer_frame = ctk.CTkFrame(self, height=60, fg_color="transparent")
        self.footer_frame.grid(row=1, column=1, padx=20, pady=(0, 20), sticky="ew")
        
        self.status_label = ctk.CTkLabel(self.footer_frame, text="Hệ thống sẵn sàng", font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=10)

        self.save_btn = ctk.CTkButton(self.footer_frame, text="Lưu thay đổi", width=140, height=40, font=ctk.CTkFont(weight="bold"), command=self.save_settings)
        self.save_btn.pack(side="right", padx=10)

        self.cancel_btn = ctk.CTkButton(self.footer_frame, text="Hủy bỏ", width=100, height=40, fg_color="gray30", hover_color="gray40", command=self.reload_settings)
        self.cancel_btn.pack(side="right", padx=10)

        # Khởi tạo tab mặc định
        self.select_tab("dashboard")

        # Poll UI trên main thread để tránh lỗi Tkinter khi cập nhật từ thread phụ
        self.after(250, self.update_loop)

        # Tự động khởi động bot nếu chưa chạy
        self.after(2000, lambda: self.start_bot(quiet=True))

        self.after(800, lambda: threading.Thread(
            target=lambda: self.after(0, self._refresh_premium_widget), daemon=True
        ).start())

        # Hiện popup premium nếu chưa có license (chỉ 1 lần mỗi session)
        self.after(1200, self._check_and_show_premium_popup)

        # Đăng ký sự kiện khi bấm nút X tắt phần mềm
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _logout(self):
        from tkinter import messagebox
        if messagebox.askyesno("Đăng xuất", "Bạn có chắc muốn đăng xuất?"):
            try:
                self.stop_bot()
            except Exception:
                pass
            auth_manager.logout()
            self.destroy()
            from login_window import launch_app
            launch_app()

    def on_closing(self):
        """Hàm dọn dẹp và tắt tiến trình ngầm trước khi thoát phần mềm"""
        try:
            self.stop_bot()
        except:
            pass
        self.destroy()
        os._exit(0)

    def load_settings(self):
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except: return {}
        return {}

    def select_tab(self, name):
        self._active_tab = name

        for t_name, frame in self.tabs_content.items():
            if t_name == name:
                frame.grid(row=0, column=0, sticky="nsew")
                if t_name == "stats":
                    self.refresh_stats()
                elif t_name == "dashboard":
                    self._refresh_dashboard_stats()
            else:
                frame.grid_forget()

        for t_name, btn in self.tab_buttons:
            indicator = self._tab_indicators.get(t_name)
            if t_name == name:
                btn.configure(
                    fg_color=("#1a1a3a", "#14143a"),
                    text_color=(UI_ACCENT, UI_ACCENT),
                )
                if indicator:
                    indicator.configure(fg_color=UI_ACCENT)
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=("gray10", "gray90"),
                )
                if indicator:
                    indicator.configure(fg_color="transparent")

        if name == "premium":
            self.premium_widget.configure(
                fg_color=("#1a1a3a", "#14143a"),
                border_color=UI_ACCENT,
                text_color=(UI_ACCENT, UI_ACCENT),
            )
            self.footer_frame.grid_remove()
        else:
            self.premium_widget.configure(
                fg_color=UI_BG_CARD,
                border_color="#2a2a4a",
                text_color=("#f59e0b", "#fbbf24"),
            )
            self.footer_frame.grid(row=1, column=1, padx=20, pady=(0, 20), sticky="ew")
            self._refresh_premium_widget()

    # --- TAB CREATION METHODS ---

    def _make_stat_card(self, parent, value_var, label, color):
        card = ctk.CTkFrame(parent, corner_radius=12, fg_color=UI_BG_CARD, border_width=1, border_color="#1e1e4a")
        card.pack(side="left", expand=True, fill="x", padx=6)
        ctk.CTkLabel(
            card, textvariable=value_var,
            font=ctk.CTkFont(size=36, weight="bold"), text_color=color,
        ).pack(pady=(14, 0))
        ctk.CTkLabel(
            card, text=label,
            font=ctk.CTkFont(size=11), text_color=UI_TEXT_DIM,
        ).pack(pady=(0, 12))

    def _refresh_dashboard_stats(self):
        if not hasattr(self, "dash_scanned_var"):
            return
        today = stats_manager.get_stats_today()
        self.dash_scanned_var.set(str(today.get("scanned", 0)))
        self.dash_uploaded_var.set(str(today.get("uploaded", 0)))
        self.dash_pending_var.set(str(stats_manager.get_pending_count()))

    def _refresh_premium_widget(self):
        if not hasattr(self, "premium_widget"):
            return
        try:
            info = license_manager.verify_with_server()
            if info.get("is_active"):
                days = info.get("days_remaining", "?")
                self.premium_widget.configure(text=f"⚡ Premium\nCòn {days} ngày")
            else:
                self.premium_widget.configure(text="⚡ Premium\nNâng cấp ngay")
        except Exception:
            if license_manager.is_licensed_local():
                local = license_manager.get_license_info() or {}
                self.premium_widget.configure(text="⚡ Premium\nĐang hoạt động")
            else:
                self.premium_widget.configure(text="⚡ Premium\nNâng cấp ngay")

    def create_dashboard_tab(self):
        frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["dashboard"] = frame

        ctk.CTkLabel(
            frame, text="HỆ THỐNG ĐIỀU KHIỂN",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(pady=(16, 12), anchor="w", padx=30)

        status_row = ctk.CTkFrame(frame, fg_color="transparent")
        status_row.pack(fill="x", padx=30, pady=(0, 12))

        self.status_indicator = ctk.CTkLabel(
            status_row, text="● Hệ thống chạy",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=UI_SUCCESS,
            fg_color="#0d2818", corner_radius=20,
            width=130, height=28,
        )
        self.status_indicator.pack(side="left", padx=(0, 8))

        self.scan_indicator = ctk.CTkLabel(
            status_row, text="● Chưa scan",
            font=ctk.CTkFont(size=12),
            text_color=UI_TEXT_DIM,
            fg_color=UI_BG_CARD, corner_radius=20,
            width=110, height=28,
        )
        self.scan_indicator.pack(side="left")

        stats_row = ctk.CTkFrame(frame, fg_color="transparent")
        stats_row.pack(fill="x", padx=24, pady=(0, 14))

        self.dash_scanned_var = ctk.StringVar(value="0")
        self.dash_uploaded_var = ctk.StringVar(value="0")
        self.dash_pending_var = ctk.StringVar(value="0")
        self._make_stat_card(stats_row, self.dash_scanned_var, "Đã quét hôm nay", UI_ACCENT)
        self._make_stat_card(stats_row, self.dash_uploaded_var, "Đã đăng hôm nay", UI_SUCCESS)
        self._make_stat_card(stats_row, self.dash_pending_var, "Đang chờ đăng", UI_WARNING)

        self.mode_var = ctk.StringVar(value="Quét Fyp theo hashtag yêu cầu")
        if shared_state.DOUYIN_CONTROL["mode"] == 1:
            self.mode_var.set("Quét kênh Follow và tải tất cả")

        mode_bar = ctk.CTkFrame(frame, fg_color=UI_BG_CARD, corner_radius=10, border_width=1, border_color="#1e1e4a")
        mode_bar.pack(fill="x", padx=30, pady=(0, 12))
        mode_inner = ctk.CTkFrame(mode_bar, fg_color="transparent")
        mode_inner.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(
            mode_inner, text="Chế độ:",
            font=ctk.CTkFont(size=13), text_color=UI_TEXT_DIM,
        ).pack(side="left", padx=(0, 10))
        self.mode_menu = ctk.CTkOptionMenu(
            mode_inner,
            values=["Quét Fyp theo hashtag yêu cầu", "Quét kênh Follow và tải tất cả"],
            variable=self.mode_var,
            width=280,
            command=self.change_mode,
            fg_color="#14143a",
            button_color="#1e1e4a",
        )
        self.mode_menu.pack(side="left")

        self.scan_btn = ctk.CTkButton(
            frame,
            text="▶  BẮT ĐẦU SCAN VIDEO",
            width=400, height=64,
            font=ctk.CTkFont(size=17, weight="bold"),
            fg_color=UI_ACCENT, hover_color="#00a8cc",
            text_color="#001018",
            corner_radius=12,
            command=self.toggle_scan,
        )
        self.scan_btn.pack(pady=(4, 16), padx=30, fill="x")

        self.sheet_btn = ctk.CTkButton(
            frame, text="📊 Mở Google Sheet quản lý", width=300, height=42,
            fg_color="#f1c40f", text_color="black", hover_color="#f39c12",
            command=self.open_sheet,
        )
        self.sheet_btn.pack(pady=(0, 12))

        info_box = ctk.CTkFrame(frame, corner_radius=10, fg_color=UI_BG_CARD, border_width=1, border_color="#1e1e4a")
        info_box.pack(fill="x", padx=30, pady=8)
        ctk.CTkLabel(
            info_box,
            text="⏰ Upload tự động: điền giờ vào cột LỊCH ĐĂNG trong Google Sheet.",
            font=ctk.CTkFont(size=12), text_color=UI_TEXT_DIM,
        ).pack(pady=(10, 2), padx=12)
        ctk.CTkLabel(
            info_box,
            text="🤖 Scheduler + Telegram Bot luôn chạy ngầm sau khi bật tool.",
            font=ctk.CTkFont(size=12), text_color=UI_TEXT_DIM,
        ).pack(pady=(0, 10), padx=12)

        self._refresh_dashboard_stats()

    def create_stats_tab(self):
        frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["stats"] = frame
        
        lbl_title = ctk.CTkLabel(frame, text="BIỂU ĐỒ HOẠT ĐỘNG", font=ctk.CTkFont(size=24, weight="bold"))
        lbl_title.pack(pady=(10, 20))

        # Khung chỉ số tổng quát
        summary_frame = ctk.CTkFrame(frame, fg_color="transparent")
        summary_frame.pack(fill="x", padx=20, pady=10)
        
        self.scanned_today_var = ctk.StringVar(value="0")
        self.uploaded_today_var = ctk.StringVar(value="0")
        
        # Thẻ Scanned
        card_scanned = ctk.CTkFrame(summary_frame, corner_radius=10, width=220, height=120)
        card_scanned.pack(side="left", expand=True, padx=10)
        card_scanned.pack_propagate(False)
        ctk.CTkLabel(card_scanned, text="QUÉT HÔM NAY", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray60").pack(pady=(20, 5))
        ctk.CTkLabel(card_scanned, textvariable=self.scanned_today_var, font=ctk.CTkFont(size=42, weight="bold"), text_color="#3498db").pack()
        
        # Thẻ Uploaded
        card_uploaded = ctk.CTkFrame(summary_frame, corner_radius=10, width=220, height=120)
        card_uploaded.pack(side="left", expand=True, padx=10)
        card_uploaded.pack_propagate(False)
        ctk.CTkLabel(card_uploaded, text="ĐÃ ĐĂNG HÔM NAY", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray60").pack(pady=(20, 5))
        ctk.CTkLabel(card_uploaded, textvariable=self.uploaded_today_var, font=ctk.CTkFont(size=42, weight="bold"), text_color="#2ecc71").pack()

        # Khung biểu đồ
        chart_container = ctk.CTkFrame(frame, corner_radius=15)
        chart_container.pack(fill="both", expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(chart_container, text="Hiệu suất 7 ngày qua (Scanned vs Uploaded)", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=10)
        
        self.canvas_chart = ctk.CTkCanvas(chart_container, bg="#2b2b2b", highlightthickness=0)
        self.canvas_chart.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Nút làm mới
        btn_refresh = ctk.CTkButton(frame, text="🔄 Làm mới dữ liệu", width=160, height=40, font=ctk.CTkFont(weight="bold"), command=self.refresh_stats)
        btn_refresh.pack(pady=10)

    def refresh_stats(self):
        # Cập nhật số liệu hôm nay
        today = stats_manager.get_stats_today()
        self.scanned_today_var.set(str(today["scanned"]))
        self.uploaded_today_var.set(str(today["uploaded"]))
        
        # Vẽ lại biểu đồ sau một khoảng nghỉ ngắn để canvas kịp lấy kích thước
        self.after(200, self.draw_chart)

    def draw_chart(self):
        self.canvas_chart.delete("all")
        width = self.canvas_chart.winfo_width()
        height = self.canvas_chart.winfo_height()

        if width <= 1:
            return

        data = stats_manager.get_last_7_days_stats()
        max_val = max([d["scanned"] for d in data] + [d["uploaded"] for d in data] + [10])

        padding_x = 48
        padding_y = 52
        label_h = 22
        chart_h = height - padding_y - label_h
        chart_w = width - 2 * padding_x

        bar_gap = chart_w / len(data)
        bar_width = max(12, min(bar_gap * 0.28, 36))

        base_y = height - padding_y
        self.canvas_chart.create_line(padding_x, base_y, width - padding_x, base_y, fill="#444466", width=2)

        for i, d in enumerate(data):
            x = padding_x + i * bar_gap + bar_gap / 2

            h_s = (d["scanned"] / max_val) * chart_h
            if h_s > 0:
                x0, y0 = x - bar_width, base_y - h_s
                x1, y1 = x, base_y
                self.canvas_chart.create_rectangle(x0, y0, x1, y1, fill=UI_ACCENT, outline="")
                ty = max(y0 - 8, padding_y)
                self.canvas_chart.create_text(
                    (x0 + x1) / 2, ty, text=str(d["scanned"]),
                    fill=UI_ACCENT, font=("Segoe UI", 9, "bold"),
                )

            h_u = (d["uploaded"] / max_val) * chart_h
            if h_u > 0:
                x0, y0 = x, base_y - h_u
                x1, y1 = x + bar_width, base_y
                self.canvas_chart.create_rectangle(x0, y0, x1, y1, fill=UI_SUCCESS, outline="")
                ty = max(y0 - 8, padding_y)
                self.canvas_chart.create_text(
                    (x0 + x1) / 2, ty, text=str(d["uploaded"]),
                    fill=UI_SUCCESS, font=("Segoe UI", 9, "bold"),
                )

            self.canvas_chart.create_text(
                x, base_y + 14, text=d["day"],
                fill=UI_TEXT_DIM, font=("Segoe UI", 10),
            )

    def _log_matches_filter(self, line: str) -> bool:
        if self._log_filter == "ALL":
            return True
        upper = line.upper()
        if self._log_filter == "ERROR":
            return "ERROR" in upper or "❌" in line or "LỖI" in upper
        if self._log_filter == "WARNING":
            return "WARNING" in upper or "WARN" in upper or "⚠" in line
        if self._log_filter == "INFO":
            return "INFO" in upper or "✅" in line or "ℹ" in line
        return True

    def _set_log_filter(self, level: str):
        self._log_filter = level
        for lvl, btn in self._log_filter_buttons.items():
            if lvl == level:
                btn.configure(fg_color=UI_ACCENT, text_color="#001018")
            else:
                btn.configure(fg_color="#14143a", text_color=UI_TEXT_DIM)
        self._refresh_log_display()

    def _refresh_log_display(self):
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for line in self._log_lines:
            if self._log_matches_filter(line):
                self.log_text.insert("end", line)
        self.log_text.see("end")

    def _append_log_content(self, text: str):
        if not text:
            return
        self._log_lines.extend(text.splitlines(keepends=True))
        if len(self._log_lines) > 4000:
            self._log_lines = self._log_lines[-4000:]
        self._refresh_log_display()

    def create_logs_tab(self):
        frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["logs"] = frame

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            header, text="Nhật ký hệ thống",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        filter_frame = ctk.CTkFrame(header, fg_color="transparent")
        filter_frame.pack(side="right")
        self._log_filter_buttons = {}
        for level in ("ALL", "INFO", "WARNING", "ERROR"):
            btn = ctk.CTkButton(
                filter_frame, text=level, width=72, height=28,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color="#14143a" if level != "ALL" else UI_ACCENT,
                text_color=UI_TEXT_DIM if level != "ALL" else "#001018",
                hover_color="#1e1e4a",
                command=lambda lv=level: self._set_log_filter(lv),
            )
            btn.pack(side="left", padx=3)
            self._log_filter_buttons[level] = btn

        self.log_text = ctk.CTkTextbox(
            frame, font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=UI_BG_CARD, border_width=1, border_color="#1e1e4a",
        )
        self.log_text.pack(fill="both", expand=True)

        if os.path.exists(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.readlines()[-500:]
                self._log_lines = tail
                self._last_log_pos = os.path.getsize(self.log_path)
                self._refresh_log_display()
            except Exception:
                pass

    def _check_and_show_premium_popup(self):
        """Chuyển sang tab Premium nếu chưa có license (chỉ 1 lần / session)."""
        if self._premium_auto_shown:
            return
        if not license_manager.is_licensed_local():
            self._premium_auto_shown = True
            self.select_tab("premium")

    def create_premium_tab(self):
        from premium_modal import PremiumPanel

        frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["premium"] = frame

        def on_licensed():
            self.status_label.configure(text="✅ License đã được kích hoạt!")
            self._refresh_premium_widget()
            self.select_tab("dashboard")

        self._premium_panel = PremiumPanel(
            frame,
            on_licensed=on_licensed,
            on_close=lambda: self.select_tab("dashboard"),
        )
        self._premium_panel.pack(fill="both", expand=True)



    # ── TAB: DYNA AUTODOWNLOAD ────────────────────────────────────────────────

    def create_downloader_tab(self):
        """Tạo tab Dyna AutoDownload – tích hợp từ Chrome Extension AUTODOWLOAD."""
        # Trạng thái nội bộ
        self._dl_queue: list = []
        self._dl_log_lines: list = []
        self._dl_engine = _adl.get_engine()

        outer = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["autodownload"] = outer

        # ── Hero header ────────────────────────────────────────────────────────
        hero = ctk.CTkFrame(outer, corner_radius=14, fg_color="#06143a",
                            border_width=1, border_color="#1e2e5a")
        hero.pack(fill="x", padx=0, pady=(0, 10))

        hero_left = ctk.CTkFrame(hero, fg_color="transparent")
        hero_left.pack(side="left", padx=18, pady=12)
        ctk.CTkLabel(
            hero_left, text="DYNA TOOL",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#eab308",
        ).pack(anchor="w")
        ctk.CTkLabel(
            hero_left, text="Dyna AutoDownload",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#ffffff",
        ).pack(anchor="w")
        ctk.CTkLabel(
            hero_left, text="Tải hàng loạt video Facebook · TikTok · Instagram · Douyin",
            font=ctk.CTkFont(size=11),
            text_color="#8fb3d9",
        ).pack(anchor="w", pady=(2, 0))

        self._dl_badge_var = ctk.StringVar(value="Sẵn sàng")
        self._dl_badge = ctk.CTkLabel(
            hero, textvariable=self._dl_badge_var,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#eab308",
            fg_color="#1a1a3a", corner_radius=20,
            width=90, height=28,
        )
        self._dl_badge.pack(side="right", padx=18)

        # ── Scrollable body ────────────────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # ── Panel: Tải link lên ───────────────────────────────────────────────
        panel_input = ctk.CTkFrame(scroll, corner_radius=12, fg_color="#0a1928",
                                   border_width=1, border_color="#1e2e4a")
        panel_input.pack(fill="x", pady=(0, 8), padx=2)

        # Tiêu đề panel
        hdr1 = ctk.CTkFrame(panel_input, fg_color="transparent")
        hdr1.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(hdr1, text="Tải link lên",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#f8fbff").pack(side="left")
        ctk.CTkLabel(hdr1, text="TXT / CSV / Dán link",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#eab308",
                     fg_color="#1a1200", corner_radius=999,
                     width=100, height=20).pack(side="left", padx=8)

        # Grid: file drop + textarea
        upload_grid = ctk.CTkFrame(panel_input, fg_color="transparent")
        upload_grid.pack(fill="x", padx=12, pady=(0, 8))
        upload_grid.grid_columnconfigure(0, weight=1)
        upload_grid.grid_columnconfigure(1, weight=1)

        # File drop button (mô phỏng drag-drop bằng filedialog)
        self._dl_file_btn = ctk.CTkButton(
            upload_grid,
            text="+  Tải file link\n.txt, .csv hoặc file có chứa URL",
            height=70, corner_radius=12,
            fg_color="#0d1f38",
            border_width=1, border_color="#5a3c00",
            hover_color="#122840",
            text_color="#f5f8ff",
            font=ctk.CTkFont(size=11),
            command=self._dl_browse_file,
        )
        self._dl_file_btn.grid(row=0, column=0, padx=(0, 6), pady=4, sticky="nsew")

        # Textarea dán link thủ công
        manual_frame = ctk.CTkFrame(upload_grid, fg_color="transparent")
        manual_frame.grid(row=0, column=1, padx=(6, 0), pady=4, sticky="nsew")
        manual_frame.grid_rowconfigure(0, weight=1)
        manual_frame.grid_columnconfigure(0, weight=1)

        self._dl_textarea = ctk.CTkTextbox(
            manual_frame,
            height=60, font=ctk.CTkFont(family="Consolas", size=10),
            fg_color="#060f26",
            border_width=1, border_color="#1a2040",
            wrap="word",
        )
        self._dl_textarea.grid(row=0, column=0, sticky="nsew")
        self._dl_textarea.insert("end", "Dán link vào đây, mỗi dòng một link")
        self._dl_textarea.bind("<FocusIn>", self._dl_textarea_focus_in)

        self._dl_import_btn = ctk.CTkButton(
            manual_frame, text="Nạp link", height=24,
            fg_color="#1a1200", border_width=1, border_color="#5a3c00",
            text_color="#eab308", hover_color="#2a2000",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._dl_import_text,
        )
        self._dl_import_btn.grid(row=1, column=0, pady=(4, 0), sticky="ew")

        # Import từ Google Sheet
        sheet_frame = ctk.CTkFrame(panel_input, fg_color="transparent")
        sheet_frame.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(sheet_frame, text="Nạp từ Google Sheet:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#f5d876").pack(side="left", padx=(0, 8))
        
        self._dl_profile_var = ctk.StringVar(value="Profile 1")
        self._dl_profile_dropdown = ctk.CTkOptionMenu(
            sheet_frame, variable=self._dl_profile_var,
            values=["Profile 1", "Profile 2", "Profile 3", "Profile 4"],
            width=100, height=28,
            fg_color="#14143a", button_color="#2a2a5a", button_hover_color="#3a3a7a",
            dropdown_fg_color="#060f26", dropdown_hover_color="#1a2040",
            font=ctk.CTkFont(size=11)
        )
        self._dl_profile_dropdown.pack(side="left", padx=(0, 8))
        
        self._dl_sheet_btn = ctk.CTkButton(
            sheet_frame, text="🔄 Nạp từ Sheet", width=100, height=28,
            fg_color="#0e3a1f", border_width=1, border_color="#185c33",
            text_color="#4ade80", hover_color="#124a27",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._dl_import_from_sheet
        )
        self._dl_sheet_btn.pack(side="left")

        # Thư mục lưu
        folder_row = ctk.CTkFrame(panel_input, fg_color="transparent")
        folder_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(folder_row, text="Thư mục tải về:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#f5d876").pack(side="left", padx=(0, 8))
        self._dl_folder_entry = ctk.CTkEntry(folder_row, placeholder_text="Ví dụ: D:/Videos/SO9-Downloads")
        self._dl_folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        default_folder = self.settings_data.get("DL_FOLDER",
                                                 os.path.join(os.path.expanduser("~"), "Downloads", "Dyna-Downloads"))
        self._dl_folder_entry.insert(0, default_folder)
        ctk.CTkButton(
            folder_row, text="Chọn...", width=80, height=30,
            fg_color="#14143a", border_width=1, border_color="#2a2a5a",
            text_color=UI_ACCENT, hover_color="#1e1e4a",
            font=ctk.CTkFont(size=11),
            command=self._dl_browse_folder,
        ).pack(side="right")

        # Metrics bar
        metrics_frame = ctk.CTkFrame(panel_input, fg_color="transparent")
        metrics_frame.pack(fill="x", padx=12, pady=(0, 10))
        self._dl_total_var   = ctk.StringVar(value="0")
        self._dl_success_var = ctk.StringVar(value="0")
        self._dl_failed_var  = ctk.StringVar(value="0")
        for var, label in [
            (self._dl_total_var,   "Tổng link"),
            (self._dl_success_var, "Thành công"),
            (self._dl_failed_var,  "Thất bại"),
        ]:
            m = ctk.CTkFrame(metrics_frame, corner_radius=12, fg_color="#060f26",
                             border_width=1, border_color="#1a2a4a")
            m.pack(side="left", expand=True, fill="x", padx=4)
            ctk.CTkLabel(m, textvariable=var,
                         font=ctk.CTkFont(size=22, weight="bold"),
                         text_color="#eab308").pack(pady=(8, 0))
            ctk.CTkLabel(m, text=label,
                         font=ctk.CTkFont(size=10),
                         text_color="#8fb3d9").pack(pady=(0, 8))

        # Action buttons
        action_frame = ctk.CTkFrame(panel_input, fg_color="transparent")
        action_frame.pack(fill="x", padx=12, pady=(0, 12))
        action_frame.grid_columnconfigure(0, weight=2)
        action_frame.grid_columnconfigure(1, weight=1)
        action_frame.grid_columnconfigure(2, weight=1)

        self._dl_start_btn = ctk.CTkButton(
            action_frame, text="▶  Chạy automation",
            height=36, corner_radius=10,
            fg_color="#0064d2", hover_color="#004aaa",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._dl_start,
        )
        self._dl_start_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self._dl_pause_btn = ctk.CTkButton(
            action_frame, text="⏸ Tạm dừng",
            height=36, corner_radius=10,
            fg_color="#14143a", hover_color="#1e1e4a",
            border_width=1, border_color="#2a2a5a",
            state="disabled",
            font=ctk.CTkFont(size=12),
            command=self._dl_toggle_pause,
        )
        self._dl_pause_btn.grid(row=0, column=1, padx=(0, 6), sticky="ew")

        self._dl_stop_btn = ctk.CTkButton(
            action_frame, text="⏹ Dừng",
            height=36, corner_radius=10,
            fg_color="#14143a", hover_color="#1e1e4a",
            border_width=1, border_color="#2a2a5a",
            state="disabled",
            font=ctk.CTkFont(size=12),
            command=self._dl_stop,
        )
        self._dl_stop_btn.grid(row=0, column=2, sticky="ew")

        # ── Panel: Danh sách link (hàng đợi) ──────────────────────────────────
        panel_queue = ctk.CTkFrame(scroll, corner_radius=12, fg_color="#0a1928",
                                   border_width=1, border_color="#1e2e4a")
        panel_queue.pack(fill="x", pady=(0, 8), padx=2)

        hdr_q = ctk.CTkFrame(panel_queue, fg_color="transparent")
        hdr_q.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(hdr_q, text="Danh sách link",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#f8fbff").pack(side="left")
        ctk.CTkButton(
            hdr_q, text="Xóa", width=52, height=26,
            fg_color="#1a1200", border_width=1, border_color="#5a3c00",
            text_color="#eab308", hover_color="#2a2000",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._dl_clear_queue,
        ).pack(side="right")

        self._dl_queue_frame = ctk.CTkScrollableFrame(
            panel_queue, height=120, fg_color="#060f26",
            corner_radius=10,
        )
        self._dl_queue_frame.pack(fill="x", padx=12, pady=(0, 12))

        self._dl_queue_empty_lbl = ctk.CTkLabel(
            self._dl_queue_frame,
            text="Chưa có link nào được tải lên.",
            font=ctk.CTkFont(size=11), text_color="#8fb3d9",
        )
        self._dl_queue_empty_lbl.pack(pady=18)

        # ── Panel: Log trạng thái ──────────────────────────────────────────────
        panel_log = ctk.CTkFrame(scroll, corner_radius=12, fg_color="#0a1928",
                                 border_width=1, border_color="#1e2e4a")
        panel_log.pack(fill="x", pady=(0, 4), padx=2)

        hdr_l = ctk.CTkFrame(panel_log, fg_color="transparent")
        hdr_l.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(hdr_l, text="Log trạng thái",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#f8fbff").pack(side="left")
        ctk.CTkButton(
            hdr_l, text="Xuất log", width=68, height=26,
            fg_color="#1a1200", border_width=1, border_color="#5a3c00",
            text_color="#eab308", hover_color="#2a2000",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._dl_export_log,
        ).pack(side="right")

        self._dl_log_box = ctk.CTkTextbox(
            panel_log,
            height=130,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color="#060f26",
            border_width=1, border_color="#1a2a4a",
            state="disabled",
        )
        self._dl_log_box.pack(fill="x", padx=12, pady=(0, 12))

    # ── Helpers cho AutoDownload tab ───────────────────────────────────────────

    def _dl_textarea_focus_in(self, event):
        """Xóa placeholder khi click vào textarea."""
        current = self._dl_textarea.get("1.0", "end").strip()
        if current == "Dán link vào đây, mỗi dòng một link":
            self._dl_textarea.delete("1.0", "end")

    def _dl_browse_file(self):
        """Mở hộp thoại chọn file .txt/.csv để nạp link."""
        import tkinter.filedialog as fd
        path = fd.askopenfilename(
            title="Chọn file chứa link",
            filetypes=[("Text/CSV files", "*.txt *.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            queue = _adl.build_queue(text)
            self._dl_queue = queue
            self._dl_add_log(f"Đã nạp {len(queue)} link từ file: {os.path.basename(path)}", "info")
            self._dl_render_queue()
            self._dl_update_metrics()
        except Exception as e:
            self._dl_add_log(f"Lỗi đọc file: {e}", "error")

    def _dl_browse_folder(self):
        """Mở hộp thoại chọn thư mục lưu."""
        import tkinter.filedialog as fd
        folder = fd.askdirectory(title="Chọn thư mục lưu video")
        if folder:
            self._dl_folder_entry.delete(0, "end")
            self._dl_folder_entry.insert(0, folder)
            self.save_settings()

    def _dl_import_text(self):
        """Nạp link từ textarea."""
        text = self._dl_textarea.get("1.0", "end").strip()
        placeholder = "Dán link vào đây, mỗi dòng một link"
        if not text or text == placeholder:
            self._dl_add_log("Chưa có link để nạp.", "warn")
            return
        queue = _adl.build_queue(text)
        self._dl_queue = queue
        self._dl_add_log(f"Đã nạp {len(queue)} link từ ô nhập tay.", "info")
        self._dl_render_queue()
        self._dl_update_metrics()

    def _dl_import_from_sheet(self):
        """Nạp link FALSE từ Google Sheet của Profile đã chọn (chạy trong thread ngầm)."""
        profile_label = self._dl_profile_var.get()   # e.g. "Profile 2"
        profile_num = profile_label.split()[-1]       # "2"

        self._dl_sheet_btn.configure(state="disabled", text="⏳ Đang tải...")
        self._dl_add_log(f"Kết nối Google Sheet của {profile_label}...", "info")

        def _worker():
            try:
                pcfg = _config_module.get_profile_settings(profile_num)
                sheet_url = pcfg.get("GOOGLE_SHEET_URL", "")
                if not sheet_url:
                    self.after(0, lambda: self._dl_add_log(
                        f"[{profile_label}] Chưa cấu hình GOOGLE_SHEET_URL.", "error"))
                    return

                sheet = _sheets_manager.connect_and_style_sheets(sheet_url=sheet_url)
                if not sheet:
                    self.after(0, lambda: self._dl_add_log(
                        f"[{profile_label}] Không kết nối được Google Sheet!", "error"))
                    return

                all_rows = sheet.get_all_values()
                # Header ở dòng 0, dữ liệu từ dòng 1
                # Cột B (index 1) = Link, Cột F (index 5) = TẢI VỀ (TRUE/FALSE)
                pending_rows = [
                    (row_idx + 2, row[1])        # row_idx+2 = số dòng thực trong sheet (1-indexed + skip header)
                    for row_idx, row in enumerate(all_rows[1:])
                    if len(row) > 5 and row[1].startswith("http") and row[5].strip().upper() != "TRUE"
                ]

                if not pending_rows:
                    self.after(0, lambda: self._dl_add_log(
                        f"[{profile_label}] Không có link nào cần tải (tất cả đã TRUE).", "warn"))
                    return

                # Xây dựng queue kèm metadata (sheet, row_number, link)
                import time as _time
                new_items = []
                for row_num, link in pending_rows:
                    route = _adl.detect_route(link)
                    item = _adl.DownloadItem(
                        id=f"{int(_time.time()*1000)}-r{row_num}",
                        link=link,
                        platform=route["platform"] if route else "unknown",
                        downloader_url=route["url"] if route else "",
                        status=_adl.STATUS_PENDING if route else _adl.STATUS_UNSUPPORTED,
                        message="Chờ xử lý" if route else "Không hỗ trợ domain này",
                        metadata={
                            "sheet": sheet,
                            "row_num": row_num,
                            "profile": profile_label,
                        },
                    )
                    new_items.append(item)

                def _apply():
                    # Nối thêm vào queue hiện tại (không xóa các link cũ)
                    self._dl_queue.extend(new_items)
                    self._dl_render_queue()
                    self._dl_update_metrics()
                    self._dl_add_log(
                        f"✅ Đã nạp {len(new_items)} link chưa tải từ {profile_label}.", "info")
                self.after(0, _apply)

            except Exception as e:
                err = str(e)
                self.after(0, lambda: self._dl_add_log(f"Lỗi khi nạp từ Sheet: {err}", "error"))
            finally:
                self.after(0, lambda: self._dl_sheet_btn.configure(
                    state="normal", text="🔄 Nạp từ Sheet"))

        threading.Thread(target=_worker, daemon=True).start()


    def _dl_start(self):
        """Bắt đầu chạy hàng đợi tải video."""
        pending = [i for i in self._dl_queue if i.status in (_adl.STATUS_PENDING, _adl.STATUS_FAILED)]
        if not pending:
            self._dl_add_log("Không có link hợp lệ để tải.", "warn")
            return

        folder = _adl.normalize_folder(self._dl_folder_entry.get())
        self._dl_folder_entry.delete(0, "end")
        self._dl_folder_entry.insert(0, folder)
        self.save_settings()

        self._dl_start_btn.configure(state="disabled")
        self._dl_pause_btn.configure(state="normal", text="⏸ Tạm dừng")
        self._dl_stop_btn.configure(state="normal")
        self._dl_badge_var.set("Đang chạy")
        self._dl_badge.configure(text_color="#54f5b5")

        engine = self._dl_engine
        engine.start(
            queue=self._dl_queue,
            download_folder=folder,
            on_item_update=lambda item: self.after(0, lambda i=item: self._dl_on_item_update(i)),
            on_log=lambda msg, lvl: self.after(0, lambda m=msg, l=lvl: self._dl_add_log(m, l)),
            on_finish=lambda: self.after(0, self._dl_on_finish),
        )

    def _dl_toggle_pause(self):
        engine = self._dl_engine
        if engine.paused:
            engine.resume()
            self._dl_pause_btn.configure(text="⏸ Tạm dừng")
            self._dl_badge_var.set("Đang chạy")
            self._dl_badge.configure(text_color="#54f5b5")
            self._dl_add_log("Đã tiếp tục tiến trình.", "info")
        else:
            engine.pause()
            self._dl_pause_btn.configure(text="▶ Tiếp tục")
            self._dl_badge_var.set("Tạm dừng")
            self._dl_badge.configure(text_color="#eab308")
            self._dl_add_log("Đã tạm dừng tiến trình.", "warn")

    def _dl_stop(self):
        self._dl_engine.stop()
        self._dl_add_log("Đã yêu cầu dừng tiến trình.", "warn")

    def _dl_on_item_update(self, item: "_adl.DownloadItem"):
        """Callback cập nhật UI khi item thay đổi trạng thái.
        Nếu item tải thành công và có metadata sheet, tự động đánh dấu TRUE lên Sheet.
        """
        self._dl_render_queue()
        self._dl_update_metrics()

        # Ghi TRUE lên Google Sheet nếu download thành công
        if item.status == _adl.STATUS_SUCCESS and item.metadata.get("sheet"):
            sheet     = item.metadata["sheet"]
            row_num   = item.metadata["row_num"]
            profile   = item.metadata.get("profile", "?")
            link      = item.link

            def _write_true():
                try:
                    sheet.update_cell(row_num, 6, "TRUE")
                    self.after(0, lambda: self._dl_add_log(
                        f"✅ [{profile}] Sheet cập nhật TRUE ← dòng {row_num}", "info"))
                except Exception as e:
                    err = str(e)
                    self.after(0, lambda: self._dl_add_log(
                        f"⚠️ [{profile}] Không cập nhật được Sheet dòng {row_num}: {err}", "warn"))

            threading.Thread(target=_write_true, daemon=True).start()

    def _dl_on_finish(self):
        """Callback khi engine hoàn tất."""
        self._dl_start_btn.configure(state="normal")
        self._dl_pause_btn.configure(state="disabled", text="⏸ Tạm dừng")
        self._dl_stop_btn.configure(state="disabled")
        self._dl_badge_var.set("Sẵn sàng")
        self._dl_badge.configure(text_color="#eab308")
        self._dl_update_metrics()

    def _dl_clear_queue(self):
        if self._dl_engine.running:
            return
        self._dl_queue = []
        self._dl_log_lines = []
        self._dl_render_queue()
        self._dl_update_metrics()
        self._dl_log_box.configure(state="normal")
        self._dl_log_box.delete("1.0", "end")
        self._dl_log_box.configure(state="disabled")

    def _dl_render_queue(self):
        """Render lại danh sách hàng đợi."""
        for w in self._dl_queue_frame.winfo_children():
            w.destroy()

        if not self._dl_queue:
            self._dl_queue_empty_lbl = ctk.CTkLabel(
                self._dl_queue_frame,
                text="Chưa có link nào được tải lên.",
                font=ctk.CTkFont(size=11), text_color="#8fb3d9",
            )
            self._dl_queue_empty_lbl.pack(pady=18)
            return

        STATUS_COLORS = {
            _adl.STATUS_SUCCESS:     "#54f5b5",
            _adl.STATUS_FAILED:      "#ff6b8a",
            _adl.STATUS_UNSUPPORTED: "#ff6b8a",
            _adl.STATUS_RUNNING:     "#eab308",
            _adl.STATUS_PENDING:     "#8fb3d9",
        }

        for item in self._dl_queue:
            row = ctk.CTkFrame(
                self._dl_queue_frame, corner_radius=10,
                fg_color="#060f26", border_width=1, border_color="#1a2a4a",
            )
            row.pack(fill="x", pady=2, padx=2)

            # Link (truncated)
            short_link = item.link if len(item.link) <= 60 else item.link[:57] + "..."
            ctk.CTkLabel(
                row, text=short_link,
                font=ctk.CTkFont(family="Consolas", size=10),
                text_color="#f2f8ff", anchor="w",
            ).pack(fill="x", padx=10, pady=(6, 0))

            meta_row = ctk.CTkFrame(row, fg_color="transparent")
            meta_row.pack(fill="x", padx=10, pady=(2, 6))

            ctk.CTkLabel(
                meta_row, text=item.platform.upper(),
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color="#eab308",
            ).pack(side="left", padx=(0, 10))

            status_lbl = _adl.STATUS_LABELS.get(item.status, item.status)
            ctk.CTkLabel(
                meta_row, text=status_lbl,
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color=STATUS_COLORS.get(item.status, "#8fb3d9"),
            ).pack(side="left", padx=(0, 10))

            ctk.CTkLabel(
                meta_row, text=item.message,
                font=ctk.CTkFont(size=9),
                text_color="#6080a0",
            ).pack(side="left")

    def _dl_update_metrics(self):
        q = self._dl_queue
        self._dl_total_var.set(str(len(q)))
        self._dl_success_var.set(str(sum(1 for i in q if i.status == _adl.STATUS_SUCCESS)))
        self._dl_failed_var.set(str(sum(1 for i in q if i.status in (_adl.STATUS_FAILED, _adl.STATUS_UNSUPPORTED))))

    def _dl_add_log(self, message: str, level: str = "info"):
        """Thêm dòng vào log panel."""
        LOG_COLORS = {"info": "#f2f8ff", "warn": "#eab308", "error": "#ff6b8a"}
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self._dl_log_lines.append({"time": ts, "message": message, "level": level})
        if len(self._dl_log_lines) > 300:
            self._dl_log_lines = self._dl_log_lines[-300:]

        self._dl_log_box.configure(state="normal")
        self._dl_log_box.insert("end", line)
        self._dl_log_box.see("end")
        self._dl_log_box.configure(state="disabled")

    def _dl_export_log(self):
        """Xuất log ra file .txt."""
        import tkinter.filedialog as fd
        path = fd.asksaveasfilename(
            title="Xuất log",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt")],
            initialfile=f"dyna-autodownload-log-{time.strftime('%Y%m%d-%H%M%S')}.txt",
        )
        if not path:
            return
        try:
            lines = [
                f"[{l['time']}] {l['level'].upper()} {l['message']}"
                for l in self._dl_log_lines
            ]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self._dl_add_log(f"Đã xuất log: {os.path.basename(path)}", "info")
        except Exception as e:
            self._dl_add_log(f"Lỗi xuất log: {e}", "error")

    def create_crawler_tab(self):
        outer = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["crawler"] = outer
        ctk.CTkLabel(outer, text="Crawler - Quét video Douyin", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 10), anchor="w")

        # CTkTabview cho Global + từng profile
        self.crawler_tabview = ctk.CTkTabview(outer)
        self.crawler_tabview.pack(fill="both", expand=True)

        # Tab riêng từng Profile
        profile_ids = self.settings_data.get("PROFILE_IDS", [self.settings_data.get("PROFILE_ID", "1")])
        profiles_cfg = self.settings_data.get("PROFILES", {})
        for p_id in profile_ids:
            tab_name = f"Profile {p_id}"
            self.crawler_tabview.add(tab_name)
            tab_frame = self.crawler_tabview.tab(tab_name)
            p_data = profiles_cfg.get(str(p_id), {})
            self._build_crawler_fields(tab_frame, str(p_id), p_data)

    def _build_crawler_fields(self, parent, key, data):
        """
        Xây dựng các trường nhập liệu cho crawler (Global hoặc từng profile).
        key = 'global' cho cài đặt Global, key = profile_id cho profile cụ thể.
        """
        if not hasattr(self, '_crawler_fields'):
            self._crawler_fields = {}
        fields = {}

        scroll = ctk.CTkScrollableFrame(parent)
        scroll.pack(fill="both", expand=True)

        if key == "global":
            hint = "[Cài đặt này áp dụng cho tất cả profile khi profile đó chưa có cài riêng]"
        else:
            hint = f"[Cài đặt riêng cho Profile {key} - Để trống = dùng Global]"
        ctk.CTkLabel(scroll, text=hint, font=ctk.CTkFont(size=11, slant="italic"), text_color="gray60").pack(anchor="w", pady=(0,10))

        ctk.CTkLabel(scroll, text="Số video mới tối đa (MAX_NEW_VIDEOS):").pack(anchor="w")
        e_max = ctk.CTkEntry(scroll)
        e_max.pack(fill="x", pady=(3, 10))
        val_max = data.get("MAX_NEW_VIDEOS", self.settings_data.get("MAX_NEW_VIDEOS", 25) if key != "global" else 25)
        e_max.insert(0, str(val_max) if val_max else "")
        fields["MAX_NEW_VIDEOS"] = e_max

        ctk.CTkLabel(scroll, text="Hashtag cần lọc (phân tach bằng dấu phẩy):").pack(anchor="w")
        e_tags = ctk.CTkEntry(scroll)
        e_tags.pack(fill="x", pady=(3, 10))
        tags_val = data.get("TARGET_TAGS", self.settings_data.get("TARGET_TAGS", []) if key != "global" else [])
        e_tags.insert(0, ", ".join(tags_val) if tags_val else "")
        fields["TARGET_TAGS"] = e_tags

        row2 = ctk.CTkFrame(scroll, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 10))

        like_f = ctk.CTkFrame(row2, fg_color="transparent")
        like_f.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ctk.CTkLabel(like_f, text="Lượt tim tối thiểu (MIN_LIKES):").pack(anchor="w")
        e_likes = ctk.CTkEntry(like_f)
        e_likes.pack(fill="x", pady=(3, 0))
        val_likes = data.get("MIN_LIKES", self.settings_data.get("MIN_LIKES", 5000) if key != "global" else 5000)
        e_likes.insert(0, str(val_likes) if val_likes != "" else "")
        fields["MIN_LIKES"] = e_likes

        dur_f = ctk.CTkFrame(row2, fg_color="transparent")
        dur_f.pack(side="left", fill="x", expand=True, padx=(10, 0))
        ctk.CTkLabel(dur_f, text="Độ dài tối đa (giây - MAX_DURATION):").pack(anchor="w")
        e_dur = ctk.CTkEntry(dur_f)
        e_dur.pack(fill="x", pady=(3, 0))
        val_dur = data.get("MAX_DURATION", self.settings_data.get("MAX_DURATION", 120) if key != "global" else 120)
        e_dur.insert(0, str(val_dur) if val_dur != "" else "")
        fields["MAX_DURATION"] = e_dur

        ctk.CTkLabel(scroll, text="Google Sheet URL (URL tab riêng cho profile này):").pack(anchor="w", pady=(10,0))
        e_sheet = ctk.CTkEntry(scroll)
        e_sheet.pack(fill="x", pady=(3, 10))
        default_sheet = self.settings_data.get("GOOGLE_SHEET_URL", "") if key != "global" else self.settings_data.get("GOOGLE_SHEET_URL", "")
        val_sheet = data.get("GOOGLE_SHEET_URL", default_sheet)
        e_sheet.insert(0, val_sheet)
        fields["GOOGLE_SHEET_URL"] = e_sheet

        ctk.CTkLabel(scroll, text="Thư mục lưu video (SAVE_DIR):").pack(anchor="w")
        
        dir_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        dir_frame.pack(fill="x", pady=(3, 0))
        
        e_dir = ctk.CTkEntry(dir_frame)
        e_dir.pack(side="left", fill="x", expand=True, padx=(0, 10))
        dir_val = data.get("SAVE_DIR", self.settings_data.get("SAVE_DIR", "") if key != "global" else "")
        e_dir.insert(0, dir_val)
        fields["SAVE_DIR"] = e_dir
        
        def browse_dir(entry_widget=e_dir):
            import tkinter.filedialog as fd
            folder = fd.askdirectory(title="Chọn thư mục lưu video")
            if folder:
                entry_widget.delete(0, "end")
                entry_widget.insert(0, folder)
                
        btn_browse = ctk.CTkButton(dir_frame, text="Chọn thư mục...", width=100, command=browse_dir)
        btn_browse.pack(side="right")

        self._crawler_fields[key] = fields

    def create_browser_tab(self):
        frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["browser"] = frame
        ctk.CTkLabel(frame, text="Trình duyệt & Automation", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 20), anchor="w")

        # Xóa cấu hình NUM_TABS theo yêu cầu (chỉ dùng 1 tab)

    def create_api_tab(self):
        frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["api"] = frame
        ctk.CTkLabel(frame, text="API & AdsPower", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 20), anchor="w")

        ctk.CTkLabel(frame, text="AdsPower API URL:").pack(anchor="w")
        self.api_url_entry = ctk.CTkEntry(frame)
        self.api_url_entry.pack(fill="x", pady=(5, 15))
        self.api_url_entry.insert(0, self.settings_data.get("API_URL", "http://127.0.0.1:1010"))

        ctk.CTkLabel(frame, text="Profile ID(s) (cách nhau bằng dấu phẩy):").pack(anchor="w")
        self.profile_id_entry = ctk.CTkEntry(frame)
        self.profile_id_entry.pack(fill="x", pady=(5, 15))
        profile_ids_list = self.settings_data.get("PROFILE_IDS", [self.settings_data.get("PROFILE_ID", "1")])
        self.profile_id_entry.insert(0, ", ".join(profile_ids_list))

    def create_telegram_tab(self):
        frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs_content["telegram"] = frame
        ctk.CTkLabel(frame, text="Telegram Bot - Điều khiển từ xa", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 20), anchor="w")

        ctk.CTkLabel(frame, text="Bot Token:").pack(anchor="w")
        self.tg_token_entry = ctk.CTkEntry(frame)
        self.tg_token_entry.pack(fill="x", pady=(5, 15))
        self.tg_token_entry.insert(0, self.settings_data.get("TELEGRAM_BOT_TOKEN", ""))

        ctk.CTkLabel(frame, text="Chat ID:").pack(anchor="w")
        self.tg_chat_id_entry = ctk.CTkEntry(frame)
        self.tg_chat_id_entry.pack(fill="x", pady=(5, 15))
        self.tg_chat_id_entry.insert(0, self.settings_data.get("TELEGRAM_CHAT_ID", ""))

    # --- ACTIONS ---

    def is_bot_running(self):
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r") as f:
                    pid = int(f.read().strip())
                return psutil.pid_exists(pid)
            except: return False
        return False

    def start_bot(self, quiet=False):
        if self.is_bot_running():
            if not quiet:
                from tkinter import messagebox
                messagebox.showwarning("Cảnh báo", "Hệ thống đang chạy rồi!")
            return

            
        try:
            # Chạy main.py bằng pythonw để không hiện console (vì đã có logs tab)
            python_exe = sys.executable
            # Sử dụng subprocess.Popen để chạy độc lập
            if getattr(sys, 'frozen', False):
                # Khi đóng gói thành EXE, gọi chính nó với flag --bot
                subprocess.Popen([python_exe, "--bot"], creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            else:
                # Khi chạy từ source, gọi main.py với cờ --bot để chạy ngầm thay vì mở GUI
                subprocess.Popen([python_exe, self.main_script, "--bot"], creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

            self.status_label.configure(text="Hệ thống đang khởi động...")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Lỗi", f"Không thể khởi động: {e}")

    def stop_bot(self):
        # 1. Tìm PID từ file lock và tắt chính xác
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r") as f:
                    pid = int(f.read().strip())
                
                if psutil.pid_exists(pid):
                    parent = psutil.Process(pid)
                    # Tắt các tiến trình con trước
                    for child in parent.children(recursive=True):
                        try: child.kill()
                        except: pass
                    parent.kill()
                    
                if os.path.exists(self.lock_path):
                    os.remove(self.lock_path)
                self.status_label.configure(text="Đã dừng hệ thống thành công.")
            except Exception as e:
                print(f"Lỗi khi dừng bot qua PID: {e}")
        
        # 2. Quét và tắt các tiến trình "mồ côi" còn sót lại (nhưng không tắt chính GUI)
        current_pid = os.getpid()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pinfo = proc.info
                p_pid = pinfo['pid']
                p_name = pinfo['name'].lower()
                
                if p_pid == current_pid: continue
                
                # Nếu là python chạy main.py hoặc DynaTool.exe chạy chế độ --bot
                is_bot = False
                cmdline = pinfo.get('cmdline') or []
                cmd_str = " ".join(cmdline).lower()
                
                if "python" in p_name and "main.py" in cmd_str:
                    is_bot = True
                elif "dynatool" in p_name and "--bot" in cmd_str:
                    is_bot = True
                    
                if is_bot:
                    proc.kill()
            except: pass
            
        self.status_label.configure(text="Hệ thống đã được dọn dẹp sạch.")

    def toggle_scan(self):
        """Bật/tắt quét video Douyin"""
        try:
            bot_mod = _bot_douyin_module
            if bot_mod is None:
                self.status_label.configure(text="⏳ Hệ thống đang nạp, thử lại sau vài giây...")
                return

            if shared_state.DOUYIN_CONTROL["running"]:
                shared_state.DOUYIN_CONTROL["running"] = False
                self._scan_cooldown_until = time.time() + 2.0
                self.status_label.configure(text="⏹ Đang dừng quét Douyin...")
                # Đóng cửa sổ Iron Browser ngay lập tức, không chờ bot thread
                threading.Thread(target=self._close_iron_browsers, daemon=True).start()
            else:
                if self._scan_launching:
                    self.status_label.configure(text="Dang khoi chay scan, vui long cho...")
                    return
                shared_state.DOUYIN_CONTROL["running"] = True
                self._scan_launching = True
                self._scan_cooldown_until = time.time() + 2.5
                threading.Thread(target=self._run_scan_worker, args=(bot_mod,), daemon=True).start()
                self.status_label.configure(text="🔍 Đang bắt đầu quét Douyin...")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Lỗi", f"Không thể toggle scan: {e}")

    def _run_scan_worker(self, bot_mod):
        try:
            # Cho phép nút bấm lại sau 3 giây (đủ để tránh double-click), không cần chờ scan xong
            time.sleep(3)
            self._scan_launching = False
            bot_mod.run_automation()
        except Exception as e:
            shared_state.DOUYIN_CONTROL["running"] = False
            self.after(0, lambda: self.status_label.configure(text=f"Loi scan: {e}"))
        finally:
            self._scan_launching = False

    def _close_iron_browsers(self):
        """Đóng tất cả cửa sổ Iron Browser ngay lập tức"""
        try:
            import pygetwindow as gw
            time.sleep(0.3)  # Chờ một chút để flag lan truyền
            for win in gw.getAllWindows():
                try:
                    if win.title and "iron" in win.title.lower():
                        win.close()
                except Exception:
                    pass
        except Exception as e:
            print(f"Lỗi đóng Iron Browser: {e}")

    def change_mode(self, choice):
        if choice == "Quét kênh Follow và tải tất cả":
            shared_state.DOUYIN_CONTROL["mode"] = 1
        else:
            shared_state.DOUYIN_CONTROL["mode"] = 0
        self.status_label.configure(text=f"Đã chuyển sang chế độ: {choice}")

    def open_sheet(self):
        # Lấy URL từ tab Crawler > Global
        gf = getattr(self, '_crawler_fields', {}).get("global", {})
        if gf and "GOOGLE_SHEET_URL" in gf:
            url = gf["GOOGLE_SHEET_URL"].get().strip()
        else:
            url = self.settings_data.get("GOOGLE_SHEET_URL", "")
        if url.startswith("http"):
            webbrowser.open(url)
        else:
            from tkinter import messagebox
            messagebox.showwarning("Lỗi", "URL không hợp lệ!")


    def update_loop(self):
        is_running = self.is_bot_running()
        if hasattr(self, "status_indicator"):
            if is_running:
                self.status_indicator.configure(text="● Hệ thống chạy", text_color=UI_SUCCESS)
            else:
                self.status_indicator.configure(text="● Hệ thống dừng", text_color=UI_DANGER)

        if hasattr(self, "scan_btn"):
            if shared_state.DOUYIN_CONTROL["running"]:
                self.scan_indicator.configure(text="● Đang scan", text_color=UI_ACCENT)
                self.scan_btn.configure(
                    text="⏹  ĐANG SCAN... (click để dừng)",
                    fg_color=UI_DANGER, hover_color="#c0392b",
                    text_color="#ffffff",
                )
            else:
                self.scan_indicator.configure(text="● Chưa scan", text_color=UI_TEXT_DIM)
                self.scan_btn.configure(
                    text="▶  BẮT ĐẦU SCAN VIDEO",
                    fg_color=UI_ACCENT, hover_color="#00a8cc",
                    text_color="#001018",
                )

            is_button_busy = self._scan_launching or (time.time() < self._scan_cooldown_until)
            self.scan_btn.configure(state="disabled" if is_button_busy else "normal")

        if hasattr(self, "dash_scanned_var"):
            self._refresh_dashboard_stats()

        if os.path.exists(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    f.seek(self._last_log_pos)
                    new_content = f.read()
                    if new_content:
                        self._append_log_content(new_content)
                    self._last_log_pos = f.tell()
            except Exception:
                pass

        if hasattr(self, "premium_widget"):
            if not hasattr(self, "_premium_widget_tick"):
                self._premium_widget_tick = 0
            self._premium_widget_tick += 1
            if self._premium_widget_tick % 30 == 0:
                threading.Thread(target=lambda: self.after(0, self._refresh_premium_widget), daemon=True).start()

        self.after(1000, self.update_loop)

    def save_settings(self):
        try:
            profile_ids_str = self.profile_id_entry.get().strip()
            profile_ids = [p.strip() for p in profile_ids_str.split(",") if p.strip()]
            if not profile_ids:
                profile_ids = ["1"]

            # Đọc cài đặt global từ tab Global
            gf = self._crawler_fields.get("global", {})
            def _int(e, fallback):
                try: return int(e.get().strip()) if e.get().strip() else fallback
                except: return fallback
            def _tags(e):
                return [t.strip() for t in e.get().split(",") if t.strip()]
            def _dirs(e):
                try: return json.loads(e.get("0.0", "end").strip())
                except: return {}
            def _str(e):
                return e.get().strip()

            new_data = {
                "MAX_NEW_VIDEOS": _int(gf["MAX_NEW_VIDEOS"], 25) if gf else int(self.settings_data.get("MAX_NEW_VIDEOS", 25)),
                "TARGET_TAGS":    _tags(gf["TARGET_TAGS"]) if gf else self.settings_data.get("TARGET_TAGS", []),
                "MIN_LIKES":      _int(gf["MIN_LIKES"], 5000) if gf else int(self.settings_data.get("MIN_LIKES", 5000)),
                "MAX_DURATION":   _int(gf["MAX_DURATION"], 120) if gf else int(self.settings_data.get("MAX_DURATION", 120)),
                "SAVE_DIR":       _str(gf["SAVE_DIR"]) if gf else self.settings_data.get("SAVE_DIR", ""),
                "GOOGLE_SHEET_URL": _str(gf["GOOGLE_SHEET_URL"]) if gf else self.settings_data.get("GOOGLE_SHEET_URL", ""),
                "NUM_TABS": 1,
                "API_URL": self.api_url_entry.get().strip(),
                "PROFILE_ID": profile_ids[0],
                "PROFILE_IDS": profile_ids,
                "TELEGRAM_BOT_TOKEN": self.tg_token_entry.get().strip(),
                "TELEGRAM_CHAT_ID": self.tg_chat_id_entry.get().strip(),
                "DL_FOLDER": self._dl_folder_entry.get().strip() if hasattr(self, "_dl_folder_entry") else self.settings_data.get("DL_FOLDER", ""),
            }

            # Đọc cài đặt riêng từng profile
            profiles_cfg = {}
            for p_id in profile_ids:
                pf = self._crawler_fields.get(str(p_id), {})
                if not pf:
                    continue
                entry = {}
                raw_max  = pf["MAX_NEW_VIDEOS"].get().strip()
                raw_like = pf["MIN_LIKES"].get().strip()
                raw_dur  = pf["MAX_DURATION"].get().strip()
                raw_tags = pf["TARGET_TAGS"].get().strip()
                raw_sheet= pf["GOOGLE_SHEET_URL"].get().strip()
                raw_dir  = pf["SAVE_DIR"].get().strip()
                if raw_max:   entry["MAX_NEW_VIDEOS"] = int(raw_max)
                if raw_like:  entry["MIN_LIKES"]      = int(raw_like)
                if raw_dur:   entry["MAX_DURATION"]   = int(raw_dur)
                if raw_tags:  entry["TARGET_TAGS"]    = [t.strip() for t in raw_tags.split(",") if t.strip()]
                if raw_sheet: entry["GOOGLE_SHEET_URL"] = raw_sheet
                if raw_dir:   entry["SAVE_DIR"] = raw_dir
                profiles_cfg[str(p_id)] = entry

            new_data["PROFILES"] = profiles_cfg

            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(new_data, f, indent=4, ensure_ascii=False)
            self.settings_data = new_data
            self.status_label.configure(text="Đã lưu cài đặt lúc " + time.strftime("%H:%M:%S"))
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Lỗi", f"Không thể lưu cài đặt: {e}")

    def reload_settings(self):
        self.settings_data = self.load_settings()

        # Cập nhật các trường crawler
        profiles_cfg = self.settings_data.get("PROFILES", {})
        for key, fields in (self._crawler_fields or {}).items():
            if key == "global":
                data = self.settings_data
            else:
                data = profiles_cfg.get(key, {})
            if "MAX_NEW_VIDEOS" in fields:
                fields["MAX_NEW_VIDEOS"].delete(0, "end")
                v = data.get("MAX_NEW_VIDEOS", "")
                fields["MAX_NEW_VIDEOS"].insert(0, str(v) if v != "" else "")
            if "TARGET_TAGS" in fields:
                fields["TARGET_TAGS"].delete(0, "end")
                fields["TARGET_TAGS"].insert(0, ", ".join(data.get("TARGET_TAGS", [])))
            if "MIN_LIKES" in fields:
                fields["MIN_LIKES"].delete(0, "end")
                v = data.get("MIN_LIKES", "")
                fields["MIN_LIKES"].insert(0, str(v) if v != "" else "")
            if "MAX_DURATION" in fields:
                fields["MAX_DURATION"].delete(0, "end")
                v = data.get("MAX_DURATION", "")
                fields["MAX_DURATION"].insert(0, str(v) if v != "" else "")
            if "GOOGLE_SHEET_URL" in fields:
                fields["GOOGLE_SHEET_URL"].delete(0, "end")
                default_sheet = self.settings_data.get("GOOGLE_SHEET_URL", "")
                fields["GOOGLE_SHEET_URL"].insert(0, data.get("GOOGLE_SHEET_URL", default_sheet))
            if "SAVE_DIR" in fields:
                fields["SAVE_DIR"].delete(0, "end")
                fields["SAVE_DIR"].insert(0, data.get("SAVE_DIR", ""))

        self.api_url_entry.delete(0, "end")
        self.api_url_entry.insert(0, self.settings_data.get("API_URL", "http://127.0.0.1:1010"))
        self.profile_id_entry.delete(0, "end")
        profile_ids_list = self.settings_data.get("PROFILE_IDS", [self.settings_data.get("PROFILE_ID", "1")])
        self.profile_id_entry.insert(0, ", ".join(profile_ids_list))
        self.tg_token_entry.delete(0, "end")
        self.tg_token_entry.insert(0, self.settings_data.get("TELEGRAM_BOT_TOKEN", ""))
        self.tg_chat_id_entry.delete(0, "end")
        self.tg_chat_id_entry.insert(0, self.settings_data.get("TELEGRAM_CHAT_ID", ""))

        if hasattr(self, "_dl_folder_entry"):
            self._dl_folder_entry.delete(0, "end")
            self._dl_folder_entry.insert(0, self.settings_data.get(
                "DL_FOLDER",
                os.path.join(os.path.expanduser("~"), "Downloads", "Dyna-Downloads")
            ))

        current_mode = shared_state.DOUYIN_CONTROL["mode"]
        if current_mode == 1:
            self.mode_var.set("Quét kênh Follow và tải tất cả")
        else:
            self.mode_var.set("Quét Fyp theo hashtag yêu cầu")

        self.status_label.configure(text="Đã khôi phục cài đặt gốc.")

if __name__ == "__main__":
    if "--bot" in sys.argv:
        run_bot_system()
    else:
        from login_window import launch_app
        launch_app()
