import { useI18n } from "../../../shared/i18n";
import type { BrowserRunMode } from "../types";

interface BrowserRunModeProps {
  profileId: string;
  headless: boolean;
  onChange(mode: BrowserRunMode): void;
}

export default function BrowserRunMode({ profileId, headless, onChange }: BrowserRunModeProps) {
  const { l } = useI18n();

  return (
    <div className="browser-run-mode">
      <div className="browser-run-mode-heading">
        <strong>{l("Chế độ chạy trình duyệt", "Browser run mode", "浏览器运行模式")}</strong>
        <span>
          {l(
            "Chọn cách cửa sổ trình duyệt hoạt động khi Dyna tự động đăng video.",
            "Choose how the browser window behaves while Dyna publishes automatically.",
            "选择 Dyna 自动发布时浏览器窗口的运行方式。",
          )}
        </span>
      </div>
      <div
        className="browser-run-mode-options"
        role="radiogroup"
        aria-label={l("Chế độ chạy trình duyệt", "Browser run mode", "浏览器运行模式")}
      >
        <label className={headless ? "selected" : ""}>
          <input
            type="radio"
            name={`browser-run-mode-${profileId}`}
            checked={headless}
            onChange={() => onChange("headless")}
          />
          <span>
            <strong>Headless</strong>
            <small>
              {l(
                "Chạy hoàn toàn không có cửa sổ trình duyệt. Gọn và ít làm phiền màn hình, nhưng một số trang có thể nhận biết hoặc hoạt động kém ổn định hơn.",
                "Runs with no browser window. It keeps the desktop clear, but some sites may detect it or behave less reliably.",
                "完全不显示浏览器窗口，桌面更整洁，但部分网站可能会检测到或运行不够稳定。",
              )}
            </small>
          </span>
        </label>
        <label className={!headless ? "selected" : ""}>
          <input
            type="radio"
            name={`browser-run-mode-${profileId}`}
            checked={!headless}
            onChange={() => onChange("offscreen")}
          />
          <span>
            <strong>{l("Mở cửa sổ trên màn hình", "Show browser window", "显示浏览器窗口")}</strong>
            <small>
              {l(
                "Mở giao diện trình duyệt trực tiếp trên màn hình, tương thích tốt nhất và dễ dàng theo dõi tiến trình hoặc xử lý CAPTCHA.",
                "Opens the browser window directly on screen for the best compatibility, live monitoring, and CAPTCHA handling.",
                "直接在屏幕上打开浏览器窗口，兼容性最佳，便于观察进度或处理验证码。",
              )}
            </small>
          </span>
        </label>
      </div>
    </div>
  );
}
