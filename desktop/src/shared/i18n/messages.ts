// Stable translation keys used by shared application chrome and auth flows.
export type CatalogMessage = Readonly<Record<"vi" | "en" | "zh", string>>;

export const messages = {
  "app.session.checking": {
    vi: "Đang kiểm tra phiên đăng nhập...",
    en: "Checking your session...",
    zh: "正在检查登录会话...",
  },
  "app.startup.failed": { vi: "Không thể khởi động Dyna", en: "Cannot start Dyna", zh: "无法启动 Dyna" },
  "common.retry": { vi: "Thử lại", en: "Try again", zh: "重试" },
  "auth.region": { vi: "Đăng nhập Dyna", en: "Sign in to Dyna", zh: "登录 Dyna" },
  "auth.console": { vi: "Bảng điều khiển tự động", en: "Automation Console", zh: "自动化控制台" },
  "auth.sign_in": { vi: "Đăng nhập", en: "Sign in", zh: "登录" },
  "auth.sign_in.description": {
    vi: "Tiếp tục phiên quản lý tự động hóa của bạn.",
    en: "Continue to your automation workspace.",
    zh: "继续进入您的自动化工作区。",
  },
  "auth.register": { vi: "Đăng ký", en: "Register", zh: "注册" },
  "auth.register.title": { vi: "Tạo tài khoản", en: "Create account", zh: "创建账户" },
  "auth.register.description": {
    vi: "Đăng ký tài khoản để sử dụng Dyna.",
    en: "Register an account to use Dyna.",
    zh: "注册账户以使用 Dyna。",
  },
  "auth.phone": { vi: "Số điện thoại", en: "Phone number", zh: "手机号码" },
  "auth.username": { vi: "Tên đăng nhập", en: "Username", zh: "用户名" },
  "auth.password": { vi: "Mật khẩu", en: "Password", zh: "密码" },
  "common.processing": { vi: "Đang xử lý...", en: "Please wait...", zh: "正在处理..." },
} as const satisfies Record<string, CatalogMessage>;

export type MessageId = keyof typeof messages;
