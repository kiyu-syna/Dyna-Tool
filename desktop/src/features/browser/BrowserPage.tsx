import { AlertTriangle, Check, ChevronDown, ChevronUp, LogIn, Plus, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { Fragment } from "react";
import { EmptyState, Section, SkeletonRows } from "../../shared/components/Common";
import BrowserRunMode from "./components/BrowserRunMode";
import ProxySettings from "./components/ProxySettings";
import { useBrowserAutomation } from "./hooks/useBrowserAutomation";
import "./browser.css";
import { proxyEndpointOf } from "./utils/proxy";
import { hasCompleteLocalBrowser, loginStatusLabel, loginStatusTone, providerOf } from "./utils/profile";
import type { PlatformKey } from "../../shared/types";

const platformLabels: Array<[PlatformKey | "douyin", string]> = [
  ["douyin", "Douyin"],
  ["tiktok", "TikTok"],
  ["youtube", "YouTube"],
  ["facebook", "Facebook"],
];

const publishPlatformLabels: Array<[PlatformKey, string]> = [
  ["tiktok", "TikTok"],
  ["youtube", "YouTube"],
  ["facebook", "Facebook"],
];

export default function BrowserAutomationPage() {
  const {
    l,
    summaries,
    setups,
    profileList,
    readinessByKey,
    enabledLoginTargets,
    readyLoginCount,
    allLoginCheckInProgress,
    loadingProfiles,
    expandedIds,
    busyIds,
    checkingLoginIds,
    message,
    messageError,
    proxyQuickValues,
    setProxyQuickValues,
    setBrowserRunMode,
    updateBrowser,
    updateProxy,
    updateProxyEndpoint,
    selectProxyType,
    applyQuickProxy,
    updateGemLoginId,
    applyOneGemLoginId,
    refreshLoginStatus,
    refreshAllLoginStatuses,
    saveDetails,
    startLocalProfile,
    openLocalProfile,
    finishLocalProfile,
    toggleExpanded,
  } = useBrowserAutomation();

  if ((summaries.loading && !summaries.data) || loadingProfiles) return <SkeletonRows count={8} />;
  if (summaries.error && !summaries.data) return <EmptyState error message={summaries.error} />;

  return (
    <div className="page-stack browser-automation-page">
      <section className="browser-getting-started">
        <div>
          <strong>
            {l("Đăng nhập riêng cho từng hồ sơ", "A separate sign-in for each Profile", "每个配置文件单独登录")}
          </strong>
          <span>
            {l(
              "Phiên đăng nhập và dữ liệu trình duyệt của mỗi hồ sơ được lưu cục bộ trên máy bạn. Bạn tự đăng nhập trực tiếp trên trang của nền tảng; Dyna không yêu cầu mật khẩu trong ứng dụng.",
              "Each Profile's browser data and sign-in session are stored locally on your computer. You sign in directly on the platform's page; Dyna never asks for your password in the app.",
              "每个配置文件的浏览器数据和登录会话均保存在您的电脑本地。请直接在平台页面登录；Dyna 不会在应用中要求您的密码。",
            )}
          </span>
        </div>
        <ol>
          <li>
            <b>1</b>
            <span>{l("Tạo & đăng nhập", "Create & sign in", "创建并登录")}</span>
          </li>
          <li>
            <b>2</b>
            <span>{l("Hoàn tất", "Finish", "完成")}</span>
          </li>
        </ol>
      </section>

      <Section
        title={l("Đăng nhập từng hồ sơ", "Sign in to each Profile", "登录每个配置文件")}
        action={
          <div className="browser-section-actions">
            <span className="runtime-summary">
              {readyLoginCount}/{enabledLoginTargets.length} {l("phiên sẵn sàng", "sessions ready", "个会话已就绪")}
            </span>
            <button
              className="secondary-button"
              disabled={allLoginCheckInProgress || !enabledLoginTargets.length}
              onClick={() => void refreshAllLoginStatuses()}
            >
              <RefreshCw size={15} className={allLoginCheckInProgress ? "spin" : ""} />
              {allLoginCheckInProgress
                ? l("Đang kiểm tra tất cả", "Checking all", "正在全部检查")
                : l("Kiểm tra tất cả hồ sơ", "Check all Profiles", "检查所有配置文件")}
            </button>
          </div>
        }
      >
        <div className="browser-step-note">
          <ShieldCheck size={17} />
          <span>
            {l(
              "Mỗi nền tảng được kiểm tra độc lập bằng phiên trình duyệt của hồ sơ. Di chuột vào trạng thái để xem chi tiết, hoặc mở đăng nhập khi phiên đã hết hạn.",
              "Each platform is checked independently using the Profile browser session. Hover over a status for details, or reopen sign-in when a session expires.",
              "每个平台都会使用配置文件的浏览器会话独立检查。将鼠标悬停在状态上可查看详情，会话过期时可重新打开登录。",
            )}
          </span>
        </div>
        {message && (
          <div className={messageError ? "browser-page-message error" : "browser-page-message"}>
            {messageError ? <AlertTriangle size={15} /> : <Check size={15} />}
            {message}
          </div>
        )}
        {!profileList.length ? (
          <EmptyState
            message={l(
              "Chưa có hồ sơ để thiết lập trình duyệt.",
              "There are no Profiles to set up yet.",
              "还没有可设置的配置文件。",
            )}
          />
        ) : (
          <div className="table-wrap browser-profile-table">
            <table>
              <thead>
                <tr>
                  <th>{l("Hồ sơ", "Profile", "配置文件")}</th>
                  <th>{l("Đăng nhập từng nền tảng", "Platform sign-in", "各平台登录")}</th>
                  <th>{l("Thao tác", "Actions", "操作")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {profileList.map(({ profile }) => {
                  const provider = providerOf(profile);
                  const setup = setups.data?.sessions[profile.id];
                  const busy = busyIds.includes(profile.id);
                  const expanded = expandedIds.includes(profile.id);
                  const ready = hasCompleteLocalBrowser(profile);
                  const enabledPlatforms = publishPlatformLabels
                    .map(([platform]) => platform)
                    .filter((platform) => profile[platform]?.enabled === true);
                  const platformChecks = enabledPlatforms.map((platform) =>
                    readinessByKey.get(`${profile.id}:${platform}`),
                  );
                  const signedInCount = platformChecks.filter((platformCheck) => platformCheck?.ready).length;
                  const checkingLogin = checkingLoginIds.includes(profile.id);
                  const loginCheckInProgress =
                    checkingLogin ||
                    enabledPlatforms.some(
                      (platform) => readinessByKey.get(`${profile.id}:${platform}`)?.status === "checking",
                    );
                  const proxyEndpoint = proxyEndpointOf(profile.browser?.proxy?.server);
                  const proxyEnabled = profile.browser?.proxy?.enabled === true;
                  return (
                    <Fragment key={profile.id}>
                      <tr className={expanded ? "expanded" : ""}>
                        <td>
                          <div className="browser-profile-identity">
                            <div className="profile-cell">
                              <b>{profile.id}</b>
                              <span>{profile.name}</span>
                            </div>
                            <small>
                              {enabledPlatforms.length
                                ? l(
                                    `${signedInCount}/${enabledPlatforms.length} nền tảng đã đăng nhập`,
                                    `${signedInCount}/${enabledPlatforms.length} platforms signed in`,
                                    `${signedInCount}/${enabledPlatforms.length} 个平台已登录`,
                                  )
                                : l("Chưa bật nền tảng đăng", "No publishing platform enabled", "未启用发布平台")}
                            </small>
                          </div>
                        </td>
                        <td>
                          <div className="platform-login-statuses">
                            {enabledPlatforms.length ? (
                              enabledPlatforms.map((platform) => {
                                const platformCheck = readinessByKey.get(`${profile.id}:${platform}`);
                                const label = publishPlatformLabels.find(([key]) => key === platform)?.[1] || platform;
                                const tone = loginStatusTone(platformCheck);
                                return (
                                  <div
                                    className={`platform-login-card ${tone}`}
                                    key={platform}
                                    title={
                                      platformCheck?.message ||
                                      l(
                                        "Chưa có lần kiểm tra đăng nhập nào.",
                                        "No sign-in check has run yet.",
                                        "尚未执行登录检查。",
                                      )
                                    }
                                  >
                                    <span className={`platform-login-mark ${platform}`}>{label.slice(0, 1)}</span>
                                    <div>
                                      <strong>{label}</strong>
                                      <small>{loginStatusLabel(platformCheck, l)}</small>
                                    </div>
                                    <i />
                                  </div>
                                );
                              })
                            ) : (
                              <span className="platform-login-empty">
                                {l("Chưa bật nền tảng đăng", "No publishing platform enabled", "未启用发布平台")}
                              </span>
                            )}
                          </div>
                        </td>
                        <td>
                          <div className="row-actions browser-login-actions">
                            {provider === "local_chromium" && (
                              <>
                                <button
                                  className="small-button"
                                  disabled={busy || loginCheckInProgress || !enabledPlatforms.length}
                                  onClick={() => void refreshLoginStatus(profile)}
                                >
                                  <RefreshCw size={14} className={loginCheckInProgress ? "spin" : ""} />
                                  {loginCheckInProgress
                                    ? l("Đang kiểm tra", "Checking", "正在检查")
                                    : l("Kiểm tra đăng nhập", "Check sign-in", "检查登录")}
                                </button>
                                {setup?.active ? (
                                  <button
                                    className="primary-button"
                                    disabled={busy}
                                    onClick={() => void finishLocalProfile(profile.id)}
                                  >
                                    <Check size={14} />
                                    {l("Đã đăng nhập xong", "I've finished signing in", "我已完成登录")}
                                  </button>
                                ) : ready && !(setup?.status === "error" && setup.mode === "create") ? (
                                  <button
                                    className="secondary-button"
                                    disabled={busy}
                                    onClick={() => void openLocalProfile(profile)}
                                  >
                                    <LogIn size={14} />
                                    {setup?.status === "error"
                                      ? l("Mở lại để đăng nhập", "Reopen for sign-in", "重新打开登录")
                                      : l("Mở để đăng nhập", "Open to sign in", "打开并登录")}
                                  </button>
                                ) : (
                                  <button
                                    className="primary-button"
                                    disabled={busy}
                                    onClick={() => void startLocalProfile(profile)}
                                  >
                                    <Plus size={14} />
                                    {l("Tạo & đăng nhập", "Create & sign in", "创建并登录")}
                                  </button>
                                )}
                              </>
                            )}
                          </div>
                        </td>
                        <td>
                          <button
                            className="icon-button"
                            onClick={() => toggleExpanded(profile.id)}
                            title={l("Cài đặt nâng cao", "Advanced settings", "高级设置")}
                          >
                            {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                          </button>
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="browser-detail-row">
                          <td colSpan={4}>
                            <div className="browser-detail-panel">
                              {provider === "local_chromium" ? (
                                <>
                                  <div className="browser-detail-intro">
                                    <ShieldCheck size={17} />
                                    <span>
                                      {l(
                                        "Dyna tự tạo hồ sơ riêng và mở bằng Chrome chính thức ở chế độ bình thường. Bạn không phải nhập đường dẫn hay tự chỉnh fingerprint.",
                                        "Dyna creates a separate Profile and opens it in official Chrome in normal mode. You do not need to enter paths or tune the fingerprint yourself.",
                                        "Dyna 会自动创建独立配置文件，并使用官方 Chrome 正常模式打开。您无需输入路径或手动调整指纹。",
                                      )}
                                    </span>
                                  </div>
                                  <div className="browser-fingerprint-summary">
                                    <strong>
                                      {l(
                                        "Fingerprint hệ thống · đồng nhất",
                                        "System fingerprint · coherent",
                                        "系统指纹 · 一致",
                                      )}
                                    </strong>
                                    <span>
                                      {l(
                                        "User Agent, Canvas, WebGL, WebRTC, phông chữ, âm thanh, CPU/RAM, màn hình, múi giờ và ngôn ngữ đều dùng giá trị thật của cùng một máy. Dyna không trộn thông số giả dễ bị Google phát hiện.",
                                        "User Agent, Canvas, WebGL, WebRTC, fonts, audio, CPU/RAM, screen, timezone, and language all use real values from the same device. Dyna does not mix spoofed values that Google can detect.",
                                        "User Agent、Canvas、WebGL、WebRTC、字体、音频、CPU/RAM、屏幕、时区和语言均使用同一设备的真实值。Dyna 不会混用容易被 Google 检测到的伪造参数。",
                                      )}
                                    </span>
                                  </div>
                                  <BrowserRunMode
                                    profileId={profile.id}
                                    headless={profile.browser?.headless === true}
                                    onChange={(mode) => setBrowserRunMode(profile.id, mode)}
                                  />
                                  <details className="browser-advanced-settings">
                                    <summary>
                                      {l(
                                        "Thông tin kỹ thuật · Dyna tự quản lý",
                                        "Technical information · managed by Dyna",
                                        "技术信息 · 由 Dyna 管理",
                                      )}
                                    </summary>
                                    <div className="browser-detail-grid">
                                      <label>
                                        <span>{l("Thư mục hồ sơ", "Profile directory", "配置文件目录")}</span>
                                        <input value={profile.browser?.profile_directory || "Default"} readOnly />
                                      </label>
                                      <label>
                                        <span>
                                          {l("Thời gian chờ mở (ms)", "Launch timeout (ms)", "启动超时（毫秒）")}
                                        </span>
                                        <input
                                          type="number"
                                          min="5000"
                                          step="1000"
                                          value={profile.browser?.launch_timeout_ms || 60000}
                                          onChange={(event) =>
                                            updateBrowser(profile.id, "launch_timeout_ms", Number(event.target.value))
                                          }
                                        />
                                      </label>
                                      <label className="full">
                                        <span>
                                          {l(
                                            "Thư mục dữ liệu · tự động",
                                            "Data directory · automatic",
                                            "数据目录 · 自动",
                                          )}
                                        </span>
                                        <input
                                          className="mono"
                                          value={
                                            profile.browser?.user_data_dir ||
                                            l(
                                              "Dyna sẽ tự tạo khi bạn bấm Tạo & đăng nhập",
                                              "Dyna creates this when you click Create & sign in",
                                              "点击创建并登录后，Dyna 会自动创建",
                                            )
                                          }
                                          readOnly
                                        />
                                      </label>
                                      <label className="full">
                                        <span>
                                          {l(
                                            "Trình duyệt đăng nhập · tự động",
                                            "Sign-in browser · automatic",
                                            "登录浏览器 · 自动",
                                          )}
                                        </span>
                                        <input
                                          className="mono"
                                          value={
                                            profile.browser?.executable_path ||
                                            l(
                                              "Chrome chính thức (ưu tiên) hoặc Microsoft Edge",
                                              "Official Chrome (preferred) or Microsoft Edge",
                                              "官方 Chrome（首选）或 Microsoft Edge",
                                            )
                                          }
                                          readOnly
                                        />
                                      </label>
                                    </div>
                                  </details>
                                </>
                              ) : (
                                <>
                                  <div className="browser-detail-intro">
                                    <ShieldCheck size={17} />
                                    <span>
                                      {l(
                                        "Bạn đang dùng các hồ sơ đã tạo trong GemLogin. Chỉ cần chỉnh mã hồ sơ khi Dyna kết nối nhầm tài khoản.",
                                        "You are using Profiles created in GemLogin. Only change IDs if Dyna connects to the wrong account.",
                                        "您正在使用 GemLogin 中创建的配置文件。仅当 Dyna 连接到错误账号时才修改 ID。",
                                      )}
                                    </span>
                                  </div>
                                  <details className="browser-advanced-settings">
                                    <summary>
                                      {l(
                                        "Chỉnh mã hồ sơ GemLogin",
                                        "Edit GemLogin Profile IDs",
                                        "编辑 GemLogin 配置文件 ID",
                                      )}
                                    </summary>
                                    <div className="gemlogin-id-grid">
                                      {platformLabels.map(([platform, label]) => (
                                        <label key={platform}>
                                          <span>
                                            {label} {l("Mã hồ sơ", "Profile ID", "配置文件 ID")}
                                          </span>
                                          <input
                                            value={String(profile[platform]?.gemlogin_profile_id || profile.id)}
                                            onChange={(event) =>
                                              updateGemLoginId(profile.id, platform, event.target.value)
                                            }
                                          />
                                        </label>
                                      ))}
                                    </div>
                                    <button className="secondary-button" onClick={() => applyOneGemLoginId(profile.id)}>
                                      {l(
                                        "Dùng cùng một mã cho mọi nền tảng",
                                        "Use one ID for every platform",
                                        "所有平台使用同一 ID",
                                      )}
                                    </button>
                                  </details>
                                </>
                              )}
                              {provider === "local_chromium" && (
                                <ProxySettings
                                  profile={profile}
                                  endpoint={proxyEndpoint}
                                  enabled={proxyEnabled}
                                  quickValue={proxyQuickValues[profile.id] || ""}
                                  onQuickValue={(value) =>
                                    setProxyQuickValues((current) => ({ ...current, [profile.id]: value }))
                                  }
                                  onApplyQuick={() => applyQuickProxy(profile.id)}
                                  onSelectType={(value) => selectProxyType(profile.id, value)}
                                  onUpdate={(field, value) => updateProxy(profile.id, field, value)}
                                  onUpdateEndpoint={(changes) => updateProxyEndpoint(profile.id, changes)}
                                />
                              )}
                              {setup?.last_error && <div className="browser-inline-error">{setup.last_error}</div>}
                              <div className="browser-detail-actions">
                                <button
                                  className="primary-button"
                                  disabled={busy}
                                  onClick={() => void saveDetails(profile.id)}
                                >
                                  <Save size={15} />
                                  {l("Lưu cài đặt nâng cao", "Save advanced settings", "保存高级设置")}
                                </button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
