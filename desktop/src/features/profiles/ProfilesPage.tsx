import {
  AlertTriangle,
  Check,
  CircleUserRound,
  ListFilter,
  Plus,
  RadioTower,
  Save,
  Share2,
  Trash2,
} from "lucide-react";
import { EmptyState, Modal, PlatformMarks, SkeletonRows } from "../../shared/components/Common";
import { profilePlatforms as platforms } from "./constants";
import { useProfilesController } from "./hooks/useProfilesController";
import { tiktokInputValue } from "./utils";
import "./profiles.css";

export default function ProfilesPage() {
  const {
    l,
    summaries,
    selectedId,
    profile,
    saving,
    message,
    messageError,
    createOpen,
    setCreateOpen,
    createId,
    setCreateId,
    createName,
    setCreateName,
    deleteOpen,
    setDeleteOpen,
    bulkSources,
    setBulkSources,
    testingSource,
    sourceTestMessage,
    sourceTestError,
    sources,
    hasChanges,
    enabledPlatformCount,
    update,
    updatePlatform,
    save,
    createProfile,
    deleteProfile,
    selectProfile,
    updateSource,
    updateTikTokUsername,
    addSource,
    removeSource,
    addBulkSources,
    testSource,
  } = useProfilesController();

  if (summaries.loading && !summaries.data) return <SkeletonRows count={8} />;
  if (summaries.error && !summaries.data) return <EmptyState error message={summaries.error} />;

  return (
    <div className="profiles-layout">
      <aside className="profile-picker">
        <header>
          <div>
            <h2>{l("Hồ sơ", "Profiles", "配置文件")}</h2>
            <small>{l("Quản lý nguồn và nền tảng", "Manage sources and platforms", "管理来源和平台")}</small>
          </div>
          <div className="inline-actions">
            <span className="profile-count">{summaries.data?.profiles.length || 0}</span>
            <button
              className="icon-button primary"
              onClick={() => setCreateOpen(true)}
              title={l("Tạo hồ sơ", "Create Profile", "创建配置文件")}
            >
              <Plus size={15} />
            </button>
          </div>
        </header>
        <div>
          {summaries.data?.profiles.map((item) => (
            <button
              key={item.id}
              className={`${selectedId === item.id ? "selected" : ""} ${item.enabled ? "enabled" : "disabled"}`}
              onClick={() => selectProfile(item.id)}
            >
              <b>{item.id}</b>
              <span>
                <strong>
                  {item.name}
                  <i
                    className="profile-enabled-dot"
                    title={item.enabled ? l("Đang bật", "Enabled", "已启用") : l("Đang tắt", "Disabled", "已禁用")}
                  />
                </strong>
                <small>
                  {item.source_count} {l("nguồn", "sources", "个来源")} · {item.check_interval_minutes}{" "}
                  {l("phút", "minutes", "分钟")}
                </small>
              </span>
              <PlatformMarks platforms={item.platforms} />
            </button>
          ))}
        </div>
      </aside>

      <div className="profile-editor">
        {!profile ? (
          <SkeletonRows count={7} />
        ) : (
          <>
            <div className="editor-toolbar">
              <div className="profile-editor-title">
                <span>{profile.id}</span>
                <div>
                  <h2>{profile.name}</h2>
                  <p>
                    {l("Mã hồ sơ", "Profile ID", "配置文件 ID")} {profile.id}
                  </p>
                </div>
                <b className={profile.enabled ? "enabled" : "disabled"}>
                  {profile.enabled ? l("Đang bật", "Enabled", "已启用") : l("Đang tắt", "Disabled", "已禁用")}
                </b>
              </div>
              <div className="toolbar-actions">
                {message && (
                  <span className={messageError ? "save-message error" : "save-message"}>
                    {messageError ? <AlertTriangle size={14} /> : <Check size={14} />}
                    {message}
                  </span>
                )}
                {hasChanges && !message && (
                  <span className="unsaved-message">
                    {l("Có thay đổi chưa lưu", "Unsaved changes", "有未保存的更改")}
                  </span>
                )}
                <button
                  className="icon-button danger"
                  onClick={() => setDeleteOpen(true)}
                  title={l("Xóa hồ sơ", "Delete Profile", "删除配置文件")}
                >
                  <Trash2 size={16} />
                </button>
                <button className="primary-button" onClick={() => void save()} disabled={saving || !hasChanges}>
                  <Save size={16} />
                  {saving ? l("Đang lưu", "Saving", "正在保存") : l("Lưu thay đổi", "Save changes", "保存更改")}
                </button>
              </div>
            </div>

            <div className="profile-summary-strip">
              <div>
                <span>{l("Nguồn theo dõi", "Tracking sources", "跟踪来源")}</span>
                <strong>
                  {sources.filter((source) => source.enabled !== false).length}/{sources.length}
                </strong>
                <small>
                  {l(
                    `${sources.filter((source) => source.platform === "tiktok").length} TikTok · ${sources.filter((source) => source.platform === "douyin").length} Douyin`,
                    `${sources.filter((source) => source.platform === "tiktok").length} TikTok · ${sources.filter((source) => source.platform === "douyin").length} Douyin`,
                    `${sources.filter((source) => source.platform === "tiktok").length} TikTok · ${sources.filter((source) => source.platform === "douyin").length} 抖音`,
                  )}
                </small>
              </div>
              <div>
                <span>{l("Nền tảng đăng", "Publishing platforms", "发布平台")}</span>
                <strong>{enabledPlatformCount}/3</strong>
                <small>{l("đã chọn", "selected", "已选择")}</small>
              </div>
              <div>
                <span>{l("Chu kỳ kiểm tra", "Check interval", "检查周期")}</span>
                <strong>{profile.check_interval_minutes || 30}</strong>
                <small>{l("phút", "minutes", "分钟")}</small>
              </div>
              <div>
                <span>{l("Ưu tiên", "Priority", "优先级")}</span>
                <strong>
                  {(profile.processing_priority ?? 100) <= 0
                    ? l("Cao", "High", "高")
                    : (profile.processing_priority ?? 100) >= 200
                      ? l("Thấp", "Low", "低")
                      : l("Bình thường", "Normal", "普通")}
                </strong>
                <small>{l("xử lý video", "video processing", "视频处理")}</small>
              </div>
            </div>

            <section className="form-section">
              <div className="profile-section-heading">
                <span>
                  <CircleUserRound size={18} />
                </span>
                <div>
                  <h3>{l("Thông tin chung", "General information", "基本信息")}</h3>
                  <p>
                    {l(
                      "Tên, chu kỳ quét và cách xử lý mặc định của hồ sơ",
                      "Name, scan interval, and default Profile behavior",
                      "配置文件名称、扫描周期和默认行为",
                    )}
                  </p>
                </div>
              </div>
              <div className="form-grid">
                <label>
                  <span>{l("Tên hồ sơ", "Profile name", "配置文件名称")}</span>
                  <input value={profile.name} onChange={(event) => update({ name: event.target.value })} />
                </label>
                <label className="interval-control">
                  <span>{l("Kiểm tra nguồn mỗi", "Check sources every", "每隔")}</span>
                  <div>
                    <input
                      type="number"
                      min="1"
                      value={profile.check_interval_minutes || 30}
                      onChange={(event) => update({ check_interval_minutes: Number(event.target.value) })}
                    />
                    <em>{l("phút", "minutes", "分钟")}</em>
                  </div>
                </label>
                <label>
                  <span>{l("Độ ưu tiên xử lý", "Processing priority", "处理优先级")}</span>
                  <select
                    value={
                      (profile.processing_priority ?? 100) <= 0
                        ? "high"
                        : (profile.processing_priority ?? 100) >= 200
                          ? "low"
                          : "normal"
                    }
                    onChange={(event) =>
                      update({
                        processing_priority:
                          event.target.value === "high" ? 0 : event.target.value === "low" ? 200 : 100,
                      })
                    }
                  >
                    <option value="high">{l("Cao", "High", "高")}</option>
                    <option value="normal">{l("Bình thường", "Normal", "普通")}</option>
                    <option value="low">{l("Thấp", "Low", "低")}</option>
                  </select>
                </label>
                <label>
                  <span>{l("Nguồn mới lần đầu", "First scan behavior", "首次扫描行为")}</span>
                  <select
                    value={profile.initial_scan_mode || "skip_existing"}
                    onChange={(event) =>
                      update({
                        initial_scan_mode: event.target.value as "skip_existing" | "process_latest",
                      })
                    }
                  >
                    <option value="skip_existing">
                      {l("Bỏ qua video hiện có", "Skip existing videos", "跳过现有视频")}
                    </option>
                    <option value="process_latest">
                      {l("Đăng video mới nhất", "Publish latest video", "发布最新视频")}
                    </option>
                  </select>
                </label>
                <label className="full">
                  <span>{l("Thư mục lưu video", "Video folder", "视频保存文件夹")}</span>
                  <input
                    value={profile.save_dir || ""}
                    onChange={(event) => update({ save_dir: event.target.value })}
                  />
                </label>
                <label className="full">
                  <span>
                    {l("Mô tả mặc định gồm hashtag", "Default caption including hashtags", "包含标签的默认文案")}
                  </span>
                  <textarea
                    rows={3}
                    value={profile.default_caption || ""}
                    onChange={(event) => update({ default_caption: event.target.value })}
                  />
                </label>
                <div className="full caption-source-options">
                  <span>{l("Mô tả", "Caption", "文案")}</span>
                  <label className="switch-row">
                    <input
                      type="checkbox"
                      checked={profile.caption_options?.tiktok_use_original_desc === true}
                      onChange={(event) =>
                        update({
                          caption_options: {
                            ...(profile.caption_options || {}),
                            tiktok_use_original_desc: event.target.checked,
                          },
                        })
                      }
                    />
                    <span>
                      {l(
                        "TikTok · Dùng mô tả TikTok gốc",
                        "TikTok · Use original TikTok caption",
                        "TikTok · 使用原始 TikTok 文案",
                      )}
                    </span>
                  </label>
                  <label className="switch-row">
                    <input
                      type="checkbox"
                      checked={profile.caption_options?.douyin_use_original_desc === true}
                      onChange={(event) =>
                        update({
                          caption_options: {
                            ...(profile.caption_options || {}),
                            douyin_use_original_desc: event.target.checked,
                          },
                        })
                      }
                    />
                    <span>
                      {l(
                        "Douyin · Dùng mô tả tiếng Trung gốc",
                        "Douyin · Use original Chinese caption",
                        "Douyin · 使用原始中文文案",
                      )}
                    </span>
                  </label>
                  <label className="switch-row">
                    <input
                      type="checkbox"
                      checked={profile.caption_options?.telegram_use_custom_caption === true}
                      onChange={(event) =>
                        update({
                          caption_options: {
                            ...(profile.caption_options || {}),
                            telegram_use_custom_caption: event.target.checked,
                          },
                        })
                      }
                    />
                    <span>
                      {l(
                        "Dùng caption bạn nhập bằng cách gửi qua Telegram",
                        "Use the caption you send through Telegram",
                        "使用您通过 Telegram 发送的文案",
                      )}
                    </span>
                  </label>
                  <label className="switch-row">
                    <input
                      type="checkbox"
                      checked={profile.caption_options?.telegram_pin_caption_message === true}
                      onChange={(event) =>
                        update({
                          caption_options: {
                            ...(profile.caption_options || {}),
                            telegram_pin_caption_message: event.target.checked,
                          },
                        })
                      }
                    />
                    <span>{l("Ghim tin nhắn", "Pin the message", "置顶消息")}</span>
                  </label>
                </div>
                <label className="switch-row">
                  <input
                    type="checkbox"
                    checked={profile.enabled}
                    onChange={(event) => update({ enabled: event.target.checked })}
                  />
                  <span>{l("Kích hoạt hồ sơ", "Enable Profile", "启用配置文件")}</span>
                </label>
              </div>
            </section>

            <section className="form-section">
              <div className="profile-section-heading">
                <span>
                  <ListFilter size={18} />
                </span>
                <div>
                  <h3>{l("Bộ lọc video", "Video filters", "视频筛选")}</h3>
                  <p>
                    {l(
                      "Chỉ tiếp nhận video đáp ứng các điều kiện bên dưới",
                      "Only accept videos matching the conditions below",
                      "仅接收符合以下条件的视频",
                    )}
                  </p>
                </div>
              </div>
              <div className="form-grid">
                <label>
                  <span>{l("Lượt thích tối thiểu", "Minimum likes", "最低点赞数")}</span>
                  <input
                    type="number"
                    min="0"
                    value={profile.filters?.min_likes || 0}
                    onChange={(event) =>
                      update({ filters: { ...(profile.filters || {}), min_likes: Number(event.target.value) } })
                    }
                  />
                </label>
                <label>
                  <span>{l("Độ dài tối đa (giây)", "Maximum duration (seconds)", "最长时长（秒）")}</span>
                  <input
                    type="number"
                    min="1"
                    value={profile.filters?.max_duration_seconds || 120}
                    onChange={(event) =>
                      update({
                        filters: { ...(profile.filters || {}), max_duration_seconds: Number(event.target.value) },
                      })
                    }
                  />
                </label>
              </div>
            </section>

            <section className="form-section">
              <div className="form-section-title">
                <div className="profile-section-heading">
                  <span>
                    <RadioTower size={18} />
                  </span>
                  <div>
                    <h3>{l("Nguồn theo dõi", "Tracking sources", "跟踪来源")}</h3>
                    <p>
                      {l(
                        "Theo dõi đồng thời profile TikTok và Douyin trong cùng một hàng đợi",
                        "Monitor TikTok and Douyin Profiles in one queue",
                        "在同一队列中跟踪 TikTok 和抖音配置文件",
                      )}
                    </p>
                  </div>
                </div>
                <div className="inline-actions">
                  <button className="secondary-button" onClick={() => addSource("tiktok")}>
                    <Plus size={15} />
                    TikTok
                  </button>
                  <button className="secondary-button" onClick={() => addSource("douyin")}>
                    <Plus size={15} />
                    Douyin
                  </button>
                </div>
              </div>
              <div className="bulk-source-add">
                <textarea
                  rows={3}
                  value={bulkSources}
                  onChange={(event) => setBulkSources(event.target.value)}
                  placeholder={l(
                    "Mỗi dòng: @username (TikTok) hoặc sec_uid (Douyin).\nCó thể dùng: Tên gọi | định danh.",
                    "One per line: @username (TikTok) or sec_uid (Douyin).\nYou can use: Display name | identifier.",
                    "每行一个：@username（TikTok）或 sec_uid（抖音）。\n也可使用：显示名称 | 标识。",
                  )}
                />
                <button className="small-button" disabled={!bulkSources.trim()} onClick={addBulkSources}>
                  <Plus size={14} />
                  {l("Thêm các nguồn", "Add sources", "添加多个来源")}
                </button>
              </div>
              {sourceTestMessage && (
                <div className={`source-test-result ${sourceTestError ? "error" : "success"}`}>{sourceTestMessage}</div>
              )}
              <div className="source-list">
                {sources.map((source, index) => (
                  <div className="source-row" key={index}>
                    <b>{String(index + 1).padStart(2, "0")}</b>
                    <select
                      className={`source-platform-select ${source.platform}`}
                      value={source.platform}
                      onChange={(event) => updateSource(index, "platform", event.target.value)}
                    >
                      <option value="tiktok">TikTok</option>
                      <option value="douyin">Douyin</option>
                    </select>
                    <input
                      placeholder={l("Tên gợi nhớ", "Display name", "备注名称")}
                      value={source.display_name || ""}
                      onChange={(event) => updateSource(index, "display_name", event.target.value)}
                    />
                    {source.platform === "tiktok" ? (
                      <input
                        className="mono"
                        placeholder="@username"
                        value={tiktokInputValue(source)}
                        onChange={(event) => updateTikTokUsername(index, event.target.value)}
                      />
                    ) : (
                      <input
                        className="mono"
                        placeholder="sec_uid"
                        value={source.sec_uid || ""}
                        onChange={(event) => updateSource(index, "sec_uid", event.target.value)}
                      />
                    )}
                    <label className="source-interval">
                      <input
                        type="number"
                        min="1"
                        value={source.check_interval_minutes || profile.check_interval_minutes || 30}
                        onChange={(event) => updateSource(index, "check_interval_minutes", Number(event.target.value))}
                      />
                      <span>{l("phút", "min", "分钟")}</span>
                    </label>
                    <label className="compact-check">
                      <input
                        type="checkbox"
                        checked={source.enabled !== false}
                        onChange={(event) => updateSource(index, "enabled", event.target.checked)}
                      />
                      <span>{l("Bật", "On", "开启")}</span>
                    </label>
                    <button
                      className="small-button source-test-button"
                      disabled={testingSource >= 0}
                      onClick={() => void testSource(index)}
                    >
                      {testingSource === index ? l("Đang test", "Testing", "测试中") : "Test"}
                    </button>
                    <button
                      className="icon-button danger"
                      onClick={() => removeSource(index)}
                      title={l("Xóa nguồn", "Delete source", "删除来源")}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))}
              </div>
            </section>

            <section className="form-section">
              <div className="profile-section-heading">
                <span>
                  <Share2 size={18} />
                </span>
                <div>
                  <h3>{l("Nền tảng đăng", "Publishing platforms", "发布平台")}</h3>
                  <p>
                    {l(
                      "Chọn nơi đăng; TikTok → TikTok và Douyin → Douyin luôn tự động bị chặn",
                      "Choose destinations; TikTok → TikTok and Douyin → Douyin are always blocked automatically",
                      "选择发布位置；TikTok → TikTok 和抖音 → 抖音始终会被自动阻止",
                    )}
                  </p>
                </div>
              </div>
              <div className="platform-config-grid">
                {platforms.map(([key, label]) => (
                  <div
                    className={`platform-config ${key} ${profile[key]?.enabled === true ? "enabled" : ""}`}
                    key={key}
                  >
                    <header>
                      <span>{key === "youtube" ? "▶" : key === "facebook" ? "f" : "♪"}</span>
                      <div>
                        <strong>{label}</strong>
                        <small>
                          {profile[key]?.enabled === true
                            ? l("Đang sử dụng", "Enabled", "使用中")
                            : l("Chưa bật", "Disabled", "未启用")}
                        </small>
                      </div>
                      <label className="switch-row">
                        <input
                          type="checkbox"
                          checked={profile[key]?.enabled === true}
                          onChange={(event) => updatePlatform(key, "enabled", event.target.checked)}
                        />
                        <span>{l("Bật", "On", "开启")}</span>
                      </label>
                    </header>
                    {key === "youtube" && (
                      <label>
                        <span>Channel ID</span>
                        <input
                          value={profile.youtube?.channel_id || ""}
                          onChange={(event) => updatePlatform("youtube", "channel_id", event.target.value)}
                        />
                      </label>
                    )}
                    {key === "youtube" && (
                      <label>
                        <span>Preset FFmpeg</span>
                        <select
                          value={profile.youtube?.preset || "slow"}
                          onChange={(event) => updatePlatform("youtube", "preset", event.target.value)}
                        >
                          {["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"].map((preset) => (
                            <option key={preset}>{preset}</option>
                          ))}
                        </select>
                      </label>
                    )}
                    {key === "youtube" && (
                      <label>
                        <span>
                          {l(
                            "CRF (thấp hơn = chất lượng cao hơn)",
                            "CRF (lower = higher quality)",
                            "CRF（越低质量越高）",
                          )}
                        </span>
                        <input
                          type="number"
                          min="0"
                          max="51"
                          value={profile.youtube?.crf ?? 16}
                          onChange={(event) => updatePlatform("youtube", "crf", Number(event.target.value))}
                        />
                      </label>
                    )}
                    {key === "facebook" && (
                      <label>
                        <span>{l("URL trang cá nhân", "Profile URL", "个人主页网址")}</span>
                        <input
                          value={profile.facebook?.profile_url || ""}
                          onChange={(event) => updatePlatform("facebook", "profile_url", event.target.value)}
                        />
                      </label>
                    )}
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </div>
      {createOpen && (
        <Modal
          title={l("Tạo hồ sơ", "Create Profile", "创建配置文件")}
          onClose={() => setCreateOpen(false)}
          footer={
            <>
              <button className="secondary-button" onClick={() => setCreateOpen(false)}>
                {l("Hủy", "Cancel", "取消")}
              </button>
              <button
                className="primary-button"
                disabled={saving || !createId.trim()}
                onClick={() => void createProfile()}
              >
                <Plus size={15} />
                {l("Tạo hồ sơ", "Create Profile", "创建配置文件")}
              </button>
            </>
          }
        >
          <div className="form-grid">
            <label>
              <span>{l("Mã hồ sơ", "Profile ID", "配置文件 ID")}</span>
              <input
                autoFocus
                inputMode="numeric"
                value={createId}
                onChange={(event) => setCreateId(event.target.value.replace(/\D/g, ""))}
                placeholder={l("Ví dụ: 6", "Example: 6", "例如：6")}
              />
            </label>
            <label>
              <span>{l("Tên hồ sơ", "Profile name", "配置文件名称")}</span>
              <input
                value={createName}
                onChange={(event) => setCreateName(event.target.value)}
                placeholder={l("Tự điền nếu để trống", "Generated when left blank", "留空时自动填写")}
              />
            </label>
          </div>
        </Modal>
      )}
      {deleteOpen && profile && (
        <Modal
          title={l("Xóa hồ sơ", "Delete Profile", "删除配置文件")}
          onClose={() => setDeleteOpen(false)}
          footer={
            <>
              <button className="secondary-button" onClick={() => setDeleteOpen(false)}>
                {l("Giữ lại", "Keep", "保留")}
              </button>
              <button
                className="small-button danger destructive"
                disabled={saving}
                onClick={() => void deleteProfile()}
              >
                <Trash2 size={15} />
                {l("Xóa hồ sơ", "Delete Profile", "删除配置文件")}
              </button>
            </>
          }
        >
          <div className="confirm-copy">
            <AlertTriangle size={20} />
            <div>
              <strong>
                {l(
                  `Xóa hồ sơ ${profile.id} - ${profile.name}?`,
                  `Delete Profile ${profile.id} - ${profile.name}?`,
                  `删除配置文件 ${profile.id} - ${profile.name}？`,
                )}
              </strong>
              <p>
                {l(
                  "Cấu hình hồ sơ sẽ bị xóa. Lịch sử hàng đợi và dữ liệu đối chiếu được giữ lại để phục hồi khi cần.",
                  "The Profile configuration will be deleted. Queue history and reconciliation data are retained for recovery.",
                  "配置文件设置将被删除。队列历史和核对数据会保留，以便需要时恢复。",
                )}
              </p>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
