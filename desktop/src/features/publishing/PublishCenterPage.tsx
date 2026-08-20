import {
  AlignLeft,
  AlertCircle,
  Ban,
  CalendarClock,
  CalendarRange,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  ExternalLink,
  FileVideo,
  FolderOpen,
  Globe2,
  Layers3,
  RefreshCw,
  RotateCcw,
  Send,
  Trash2,
  WandSparkles,
} from "lucide-react";
import { useMemo, useState } from "react";
import { formatDateTime } from "../../shared/api/client";
import { EmptyState, PlatformMarks, Section, SkeletonRows, StatusPill } from "../../shared/components/Common";
import {
  directoryName,
  fileName,
  jobLabel,
  jobTone,
  localDateValue,
  minimumScheduleValue,
  PLATFORM_LABELS,
  sourceFileName,
} from "./utils";
import { usePublishCenter } from "./hooks/usePublishCenter";
import "./publishing.css";
import type { Job, PlatformKey } from "../../shared/types";

type JobBatch = {
  key: string;
  name: string;
  jobs: Job[];
};

function groupJobsByBatch(jobs: Job[]): JobBatch[] {
  const groups = new Map<string, JobBatch>();
  for (const job of jobs) {
    const batchId = String(job.batch_id || "").trim();
    const key = batchId || `legacy:${job.profile_id}:${job.video_id}`;
    const existing = groups.get(key);
    if (existing) {
      existing.jobs.push(job);
      continue;
    }
    groups.set(key, {
      key,
      name: String(job.batch_name || "").trim(),
      jobs: [job],
    });
  }
  return [...groups.values()];
}

export default function PublishCenterPage() {
  const {
    l,
    profiles,
    jobs,
    items,
    setItems,
    targets,
    scheduleStartDate,
    setScheduleStartDate,
    videosPerDay,
    setVideosPerDay,
    scheduleTimes,
    setScheduleTimes,
    scheduleMode,
    setScheduleMode,
    randomStartTime,
    setRandomStartTime,
    randomEndTime,
    setRandomEndTime,
    batchName,
    setBatchName,
    submitting,
    aiGenerating,
    aiCaptionProgress,
    checkingReady,
    busyJob,
    message,
    error,
    selectedTargets,
    selectedTargetsReady,
    selectedTargetsChecking,
    missingCaptionCount,
    invalidScheduleCount,
    canSubmit,
    chooseFiles,
    updateItem,
    applySharedCaption,
    generateAiCaptions,
    applyAutoSchedule,
    clearAllSchedules,
    toggleProfile,
    togglePlatform,
    publish,
    jobAction,
    resetQueue,
    dialogOpen,
    setDialogOpen,
    sourceUrl,
    setSourceUrl,
    starting,
    session,
    openDialog,
    start,
    cancel,
  } = usePublishCenter();
  const [captionTool, setCaptionTool] = useState<"shared" | "ai" | null>(null);
  const [sharedCaption, setSharedCaption] = useState("");
  const [aiPrompt, setAiPrompt] = useState("");
  const [expandedBatches, setExpandedBatches] = useState<Record<string, boolean>>({});
  const jobBatches = useMemo(() => groupJobsByBatch(jobs.data?.jobs || []), [jobs.data?.jobs]);

  function toggleBatch(key: string) {
    setExpandedBatches((current) => ({ ...current, [key]: !current[key] }));
  }

  return (
    <div className="page-stack publish-page">
      <div className="publish-intro">
        <FileVideo size={20} />
        <div>
          <strong>{l("Đăng video từ máy hoặc Douyin", "Publish local or Douyin videos", "发布本机或抖音视频")}</strong>
          <span>
            {l(
              "Chọn file trên máy hoặc chọn trực tiếp trên trang cá nhân Douyin, sau đó cấu hình mô tả và lịch trong cùng một nơi.",
              "Choose local files or select directly from a Douyin profile, then configure captions and schedules in one place.",
              "选择本机文件或直接从抖音个人主页选择，然后在同一位置配置文案和排期。",
            )}
          </span>
        </div>
      </div>

      {session && !["cancelled", "expired"].includes(session.status) && (
        <div className={`publish-douyin-session ${session.status}`}>
          <Globe2 size={17} />
          <span>
            <strong>
              {session.status === "selecting"
                ? l("Đang chờ chọn video trên Douyin", "Waiting for Douyin selection", "正在等待抖音视频选择")
                : session.status === "preparing"
                  ? l(
                      "Đang tải và kiểm tra video Douyin",
                      "Downloading and validating Douyin videos",
                      "正在下载并检查抖音视频",
                    )
                  : session.status === "published"
                    ? l("Đã chuẩn bị xong danh sách Douyin", "Douyin batch prepared", "抖音批次已准备完成")
                    : l(
                        `Đã nhận ${session.selected_count} video Douyin`,
                        `Received ${session.selected_count} Douyin videos`,
                        `已收到 ${session.selected_count} 个抖音视频`,
                      )}
            </strong>
            <small>
              {session.last_error ||
                (session.status === "selecting"
                  ? l(
                      "Extension sẽ tự bật nút chọn trên trang cá nhân vừa mở.",
                      "The extension enables selection controls on the opened profile.",
                      "扩展会在已打开的个人主页上自动启用选择控件。",
                    )
                  : l(
                      "Nếu một video tải lỗi, video phía sau sẽ tự thế chỗ trong lịch.",
                      "If a download fails, later videos automatically fill the empty schedule slot.",
                      "如果某个视频下载失败，后续视频会自动填补空缺排期。",
                    ))}
            </small>
          </span>
          {["selecting", "ready"].includes(session.status) && (
            <button className="small-button danger" onClick={() => void cancel()}>
              {l("Hủy phiên", "Cancel session", "取消会话")}
            </button>
          )}
        </div>
      )}

      <Section
        title={`1. ${l("Video, mô tả và lịch đăng", "Videos, captions, and schedules", "视频、文案和排期")} · ${items.length}/50`}
        className="publish-items-section"
        action={
          <div className="publish-source-actions">
            <button className="small-button" onClick={() => void chooseFiles()}>
              <FolderOpen size={14} />
              {l("Trên máy", "Local files", "本机文件")}
            </button>
            <button className="small-button primary" onClick={openDialog}>
              <Globe2 size={14} />
              Douyin
            </button>
          </div>
        }
      >
        {!items.length ? (
          <div className="publish-empty-sources">
            <button className="publish-dropzone" onClick={() => void chooseFiles()}>
              <FolderOpen size={25} />
              <strong>{l("Chọn video trên máy", "Select local videos", "选择本机视频")}</strong>
              <span>{l("Tối đa 50 video mỗi lần", "Up to 50 videos per batch", "每批最多 50 个视频")}</span>
            </button>
            <button className="publish-dropzone douyin" onClick={openDialog}>
              <Globe2 size={25} />
              <strong>{l("Chọn trên trang Douyin", "Select on a Douyin profile", "在抖音主页选择")}</strong>
              <span>
                {l(
                  "Dyna mở trang và tự bật các nút chọn",
                  "Dyna opens the profile and enables selection",
                  "Dyna 打开主页并启用选择",
                )}
              </span>
            </button>
          </div>
        ) : (
          <>
            <div className="publish-batch-editor">
              <label className="publish-batch-name">
                <span>
                  <Layers3 size={13} />
                  {l("Tên lô video", "Video batch name", "视频批次名称")}
                </span>
                <input
                  value={batchName}
                  maxLength={160}
                  placeholder={l("Ví dụ: Game tháng 7", "Example: July gaming videos", "例如：七月游戏视频")}
                  onChange={(event) => setBatchName(event.target.value)}
                />
                <small>
                  {l(
                    "Tên này sẽ đại diện cho cả lô trong hàng đợi Trung tâm đăng.",
                    "This name represents the whole batch in the Publish Center queue.",
                    "此名称将在发布中心队列中代表整个批次。",
                  )}
                </small>
              </label>
              <div className="publish-caption-actions">
                <button
                  className={`small-button ${captionTool === "shared" ? "primary" : ""}`}
                  disabled={aiGenerating}
                  onClick={() => setCaptionTool((current) => (current === "shared" ? null : "shared"))}
                >
                  <AlignLeft size={14} />
                  {l(
                    "Dùng mô tả giống nhau cho mọi video",
                    "Use the same caption for every video",
                    "所有视频使用相同文案",
                  )}
                </button>
                <button
                  className={`small-button ai ${captionTool === "ai" ? "active" : ""}`}
                  disabled={aiGenerating}
                  onClick={() => setCaptionTool((current) => (current === "ai" ? null : "ai"))}
                >
                  <WandSparkles size={14} />
                  {l(
                    "Tạo mô tả AI hàng loạt từ mô tả gốc",
                    "Generate AI captions in bulk from originals",
                    "根据原文案批量生成 AI 文案",
                  )}
                </button>
              </div>
              {captionTool === "shared" && (
                <div className="publish-caption-tool-panel">
                  <label>
                    <span>{l("Mô tả dùng chung", "Shared caption", "通用文案")}</span>
                    <textarea
                      autoFocus
                      rows={3}
                      maxLength={10000}
                      value={sharedCaption}
                      placeholder={l(
                        "Nhập một mô tả để áp dụng cho toàn bộ video trong lô...",
                        "Enter one caption to apply to every video in this batch...",
                        "输入一条文案并应用到此批次的所有视频...",
                      )}
                      onChange={(event) => setSharedCaption(event.target.value)}
                    />
                  </label>
                  <div>
                    <small>{sharedCaption.length}/10.000</small>
                    <button className="small-button" onClick={() => setCaptionTool(null)}>
                      {l("Hủy", "Cancel", "取消")}
                    </button>
                    <button
                      className="small-button primary"
                      disabled={!sharedCaption.trim()}
                      onClick={() => {
                        if (applySharedCaption(sharedCaption)) setCaptionTool(null);
                      }}
                    >
                      <CheckCircle2 size={13} />
                      {l("Xác nhận", "Confirm", "确认")}
                    </button>
                  </div>
                </div>
              )}
              {captionTool === "ai" && (
                <div className="publish-caption-tool-panel ai">
                  <label>
                    <span>{l("Prompt cho DynaAI", "Instructions for DynaAI", "给 DynaAI 的提示词")}</span>
                    <textarea
                      autoFocus
                      rows={3}
                      maxLength={4000}
                      value={aiPrompt}
                      placeholder={l(
                        "Ví dụ: Viết lại vui vẻ, gây tò mò, giữ hashtag chính và thêm tối đa 3 hashtag phù hợp...",
                        "Example: Rewrite with a playful, curious tone, keep key hashtags, and add at most 3 relevant hashtags...",
                        "例如：改写得有趣且引人好奇，保留主要标签，并最多添加 3 个相关标签...",
                      )}
                      onChange={(event) => setAiPrompt(event.target.value)}
                    />
                    <small>
                      {l(
                        "DynaAI xử lý riêng từng video, tối đa 3 video cùng lúc. Video lỗi vẫn giữ mô tả hiện tại.",
                        "DynaAI processes each video separately, up to 3 at once. Failed videos keep their current captions.",
                        "DynaAI 会逐个处理视频，每次最多并行 3 个；失败视频保留当前文案。",
                      )}
                    </small>
                  </label>
                  <div>
                    <small>
                      {aiGenerating
                        ? l(
                            `Đang tạo ${aiCaptionProgress.completed}/${aiCaptionProgress.total} · lỗi ${aiCaptionProgress.failed}`,
                            `Generating ${aiCaptionProgress.completed}/${aiCaptionProgress.total} · ${aiCaptionProgress.failed} failed`,
                            `正在生成 ${aiCaptionProgress.completed}/${aiCaptionProgress.total} · ${aiCaptionProgress.failed} 个失败`,
                          )
                        : `${aiPrompt.length}/4.000`}
                    </small>
                    <button className="small-button" disabled={aiGenerating} onClick={() => setCaptionTool(null)}>
                      {l("Hủy", "Cancel", "取消")}
                    </button>
                    <button
                      className="small-button ai active"
                      disabled={aiGenerating || !aiPrompt.trim()}
                      onClick={() => void generateAiCaptions(aiPrompt)}
                    >
                      {aiGenerating ? <RefreshCw size={13} className="spin" /> : <WandSparkles size={13} />}
                      {aiGenerating
                        ? l("DynaAI đang viết...", "DynaAI is writing...", "DynaAI 正在编写...")
                        : l("Xác nhận và bắt đầu", "Confirm and start", "确认并开始")}
                    </button>
                  </div>
                </div>
              )}
            </div>
            <div className="publish-schedule-builder">
              <div className="publish-schedule-heading">
                <CalendarRange size={17} />
                <span>
                  <strong>{l("Tự chia lịch hàng loạt", "Bulk auto-scheduling", "批量自动排期")}</strong>
                  <small>
                    {l(
                      "Dyna xếp video theo đúng thứ tự bên dưới và tự bỏ qua khung giờ đã qua.",
                      "Dyna schedules videos in the order below and skips past time slots.",
                      "Dyna 会按下方顺序排期，并自动跳过已过时段。",
                    )}
                  </small>
                </span>
              </div>
              <label>
                <span>{l("Ngày bắt đầu", "Start date", "开始日期")}</span>
                <input
                  type="date"
                  min={localDateValue(new Date())}
                  value={scheduleStartDate}
                  onChange={(event) => setScheduleStartDate(event.target.value)}
                />
              </label>
              <label>
                <span>{l("Video/ngày", "Videos/day", "视频/天")}</span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={videosPerDay}
                  onChange={(event) => setVideosPerDay(Math.min(10, Math.max(1, Number(event.target.value) || 1)))}
                />
              </label>
              <label>
                <span>{l("Kiểu chia lịch", "Schedule mode", "排期方式")}</span>
                <select
                  value={scheduleMode}
                  onChange={(event) => setScheduleMode(event.target.value as "fixed" | "random")}
                >
                  <option value="fixed">{l("Giờ cố định", "Fixed times", "固定时间")}</option>
                  <option value="random">{l("Ngẫu nhiên trong khoảng", "Random within a range", "范围内随机")}</option>
                </select>
              </label>
              {scheduleMode === "fixed" ? (
                <label className="publish-schedule-times">
                  <span>
                    <Clock3 size={12} /> {l("Các giờ đăng", "Publishing times", "发布时间")}
                  </span>
                  <input
                    type="text"
                    value={scheduleTimes}
                    placeholder="09:00, 13:30, 19:00"
                    onChange={(event) => setScheduleTimes(event.target.value)}
                  />
                  <small>
                    {l(
                      `Nhập ít nhất ${videosPerDay} giờ, cách nhau bằng dấu phẩy.`,
                      `Enter at least ${videosPerDay} times separated by commas.`,
                      `请输入至少 ${videosPerDay} 个时间，并用逗号分隔。`,
                    )}
                  </small>
                </label>
              ) : (
                <div className="publish-random-range">
                  <label>
                    <span>
                      <Clock3 size={12} /> {l("Từ", "From", "从")}
                    </span>
                    <input
                      type="time"
                      value={randomStartTime}
                      onChange={(event) => setRandomStartTime(event.target.value)}
                    />
                  </label>
                  <label>
                    <span>{l("Đến", "To", "至")}</span>
                    <input
                      type="time"
                      value={randomEndTime}
                      onChange={(event) => setRandomEndTime(event.target.value)}
                    />
                  </label>
                  <small>
                    {l(
                      "Dyna chọn giờ ngẫu nhiên riêng cho từng ngày.",
                      "Dyna chooses different random times for each day.",
                      "Dyna 会为每天选择不同的随机时间。",
                    )}
                  </small>
                </div>
              )}
              <div className="publish-schedule-actions">
                <button className="small-button primary" onClick={applyAutoSchedule}>
                  <WandSparkles size={13} />
                  {l(
                    `Chia lịch cho ${items.length} video`,
                    `Schedule ${items.length} videos`,
                    `为 ${items.length} 个视频排期`,
                  )}
                </button>
                {items.some((item) => item.scheduled_at) && (
                  <button className="small-button" onClick={clearAllSchedules}>
                    {l("Xóa toàn bộ lịch", "Clear all schedules", "清除所有排期")}
                  </button>
                )}
              </div>
            </div>
            <div className="publish-item-list">
              {items.map((item, index) => (
                <article key={item.file_path}>
                  <header>
                    <span className="publish-item-number">{index + 1}</span>
                    {item.thumbnail_url ? (
                      <img className="publish-item-thumbnail" src={item.thumbnail_url} alt="" />
                    ) : (
                      <FileVideo size={16} />
                    )}
                    <span>
                      <strong title={item.source_url || item.file_path}>
                        {item.source_type === "douyin"
                          ? `${item.author_nickname || "Douyin"} · ${item.video_id}`
                          : fileName(item.file_path)}
                      </strong>
                      <small title={item.source_url || directoryName(item.file_path)}>
                        {item.source_type === "douyin"
                          ? l("Video được chọn thủ công trên Douyin", "Manually selected on Douyin", "在抖音手动选择")
                          : directoryName(item.file_path)}
                      </small>
                    </span>
                    <div className="publish-item-header-actions">
                      {item.source_url && (
                        <button
                          className="queue-video-link"
                          onClick={() => void window.dyna?.openExternal(item.source_url || "")}
                          title={l("Mở video gốc trên Douyin", "Open the original video on Douyin", "在抖音打开原视频")}
                        >
                          <ExternalLink size={13} />
                          {l("Xem video", "View video", "查看视频")}
                        </button>
                      )}
                      <span className={`publish-item-mode ${item.scheduled_at ? "scheduled" : ""}`}>
                        {item.scheduled_at
                          ? l("Đặt lịch", "Scheduled", "定时")
                          : l("Đăng ngay", "Publish now", "立即发布")}
                      </span>
                      <button
                        className="icon-button danger"
                        onClick={() =>
                          setItems((current) => current.filter((entry) => entry.file_path !== item.file_path))
                        }
                        title={l("Bỏ video", "Remove video", "移除视频")}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </header>
                  <div className="publish-item-fields">
                    <label>
                      <span>{l("Mô tả riêng", "Individual caption", "独立文案")}</span>
                      <textarea
                        value={item.caption}
                        maxLength={10000}
                        rows={3}
                        placeholder={l(
                          `Mô tả cho ${item.video_id || fileName(item.file_path)}...`,
                          `Caption for ${item.video_id || fileName(item.file_path)}...`,
                          `${item.video_id || fileName(item.file_path)} 的文案...`,
                        )}
                        onChange={(event) => updateItem(item.file_path, { caption: event.target.value })}
                      />
                      <small>
                        {item.caption.length}/10.000 {l("ký tự", "characters", "字符")}
                      </small>
                    </label>
                    <label>
                      <span>
                        <CalendarClock size={13} /> {l("Thời gian đăng", "Publish time", "发布时间")}
                      </span>
                      <input
                        type="datetime-local"
                        min={minimumScheduleValue()}
                        value={item.scheduled_at}
                        onChange={(event) => updateItem(item.file_path, { scheduled_at: event.target.value })}
                      />
                      <small>
                        {l(
                          "Để trống nếu muốn đăng ngay. Dyna cần đang mở; lịch bị lỡ khi tắt máy sẽ chạy lúc mở lại.",
                          "Leave blank to publish immediately. Dyna must be running; missed schedules run when it reopens.",
                          "留空则立即发布。Dyna 必须保持运行；关机期间错过的排期会在重新打开后执行。",
                        )}
                      </small>
                      {item.scheduled_at && (
                        <button
                          className="small-button"
                          onClick={() => updateItem(item.file_path, { scheduled_at: "" })}
                        >
                          {l("Chuyển sang đăng ngay", "Switch to publish now", "改为立即发布")}
                        </button>
                      )}
                    </label>
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </Section>

      <Section
        title={`2. ${l("Hồ sơ và nền tảng", "Profiles and platforms", "配置文件和平台")} · ${selectedTargets.length} ${l("hồ sơ", "Profiles", "个配置文件")}`}
      >
        {profiles.loading && !profiles.data ? (
          <SkeletonRows count={3} />
        ) : profiles.error && !profiles.data ? (
          <EmptyState error message={profiles.error} />
        ) : !profiles.data?.profiles.length ? (
          <EmptyState
            message={l("Chưa có hồ sơ nào được cấu hình", "No Profiles are configured", "尚未配置任何配置文件")}
          />
        ) : (
          <div className="publish-profile-grid">
            {profiles.data.profiles.map((profile) => {
              const selected = Boolean(targets[profile.id]);
              return (
                <article
                  key={profile.id}
                  className={`${selected ? "selected" : ""} ${!profile.available ? "disabled" : ""}`}
                >
                  <label className="publish-profile-choice">
                    <input
                      type="checkbox"
                      checked={selected}
                      disabled={!profile.available}
                      onChange={() => toggleProfile(profile)}
                    />
                    <span>
                      <strong>
                        P{profile.id} · {profile.name}
                      </strong>
                      <small>
                        {profile.available
                          ? l(
                              "Chọn nơi video sẽ được đăng",
                              "Choose where videos will be published",
                              "选择视频发布位置",
                            )
                          : profile.reason}
                      </small>
                    </span>
                  </label>
                  <div className="publish-platform-choices">
                    {(Object.keys(PLATFORM_LABELS) as PlatformKey[]).map((platform) => (
                      <label
                        key={platform}
                        className={`${profile.platforms[platform] ? "available" : "disabled"} ${targets[profile.id]?.includes(platform) ? "selected" : ""}`}
                      >
                        <input
                          type="checkbox"
                          checked={targets[profile.id]?.includes(platform) || false}
                          disabled={!selected || !profile.platforms[platform]}
                          onChange={() => togglePlatform(profile, platform)}
                        />
                        {PLATFORM_LABELS[platform]}
                      </label>
                    ))}
                  </div>
                </article>
              );
            })}
          </div>
        )}
        <div className="publish-submit-bar">
          <div>
            {error ? (
              <span className="publish-feedback error">
                <AlertCircle size={15} />
                {error}
              </span>
            ) : missingCaptionCount ? (
              <span className="publish-feedback error">
                <AlertCircle size={15} />
                {l(
                  `Còn ${missingCaptionCount} video chưa có mô tả.`,
                  `${missingCaptionCount} videos still need captions.`,
                  `还有 ${missingCaptionCount} 个视频没有文案。`,
                )}
              </span>
            ) : invalidScheduleCount ? (
              <span className="publish-feedback error">
                <AlertCircle size={15} />
                {l(
                  `Có ${invalidScheduleCount} lịch đăng quá gần hoặc không hợp lệ.`,
                  `${invalidScheduleCount} schedules are too soon or invalid.`,
                  `有 ${invalidScheduleCount} 个排期过近或无效。`,
                )}
              </span>
            ) : !batchName.trim() ? (
              <span className="publish-feedback error">
                <AlertCircle size={15} />
                {l(
                  "Hãy đặt tên cho lô video trước khi tạo tác vụ.",
                  "Name this video batch before creating jobs.",
                  "创建任务前请为此视频批次命名。",
                )}
              </span>
            ) : !selectedTargets.length ? (
              <span className="publish-hint">
                {l(
                  "Hãy chọn hồ sơ và nền tảng đăng.",
                  "Select Profiles and publishing platforms.",
                  "请选择配置文件和发布平台。",
                )}
              </span>
            ) : selectedTargetsChecking || checkingReady ? (
              <span className="publish-feedback">
                <RefreshCw size={15} className="spin" />
                {l(
                  "Dyna đang tự kiểm tra phiên đăng nhập...",
                  "Dyna is checking sign-in sessions...",
                  "Dyna 正在检查登录会话...",
                )}
              </span>
            ) : !selectedTargetsReady ? (
              <span className="publish-feedback error">
                <AlertCircle size={15} />
                {l(
                  "Có tài khoản chưa sẵn sàng; xem trạng thái kiểm tra ở hồ sơ.",
                  "Some accounts are not ready; review the Profile readiness status.",
                  "部分账户尚未就绪，请查看配置文件的就绪状态。",
                )}
              </span>
            ) : message ? (
              <span className="publish-feedback success">
                <CheckCircle2 size={15} />
                {message}
              </span>
            ) : (
              <span className="publish-feedback success">
                <CheckCircle2 size={15} />
                {l(
                  "Các tài khoản đã sẵn sàng. Có thể tạo tác vụ đăng.",
                  "Accounts are ready. Publishing jobs can be created.",
                  "账户已就绪，可以创建发布任务。",
                )}
              </span>
            )}
          </div>
          <button className="primary-button publish-submit" disabled={!canSubmit} onClick={() => void publish()}>
            <Send size={15} />
            {submitting
              ? l("Đang tạo tác vụ...", "Creating jobs...", "正在创建任务...")
              : selectedTargetsChecking
                ? l("Đang kiểm tra sẵn sàng...", "Checking readiness...", "正在检查就绪状态...")
                : l("Tạo tác vụ đăng", "Create publishing jobs", "创建发布任务")}
          </button>
        </div>
      </Section>

      <Section
        title={`${l("Hàng đợi Trung tâm đăng", "Publish Center queue", "发布中心队列")}${
          jobs.data
            ? ` · ${jobBatches.length} ${l("lô", "batches", "个批次")} · ${jobs.data.total} ${l(
                "tác vụ",
                "jobs",
                "个任务",
              )}`
            : ""
        }`}
        action={
          <button
            className="small-button danger queue-reset-button"
            disabled={!jobs.data?.total || Boolean(busyJob)}
            onClick={() => void resetQueue()}
            title={
              !jobs.data?.total
                ? l("Hàng đợi đang trống", "The queue is empty", "队列为空")
                : l(
                    "Xóa sạch toàn bộ hàng đợi Trung tâm đăng",
                    "Clear the entire Publish Center queue",
                    "清空整个发布中心队列",
                  )
            }
          >
            <Trash2 size={14} />
            {busyJob === "queue-reset"
              ? l("Đang reset...", "Resetting...", "正在重置...")
              : !jobs.data?.total
                ? l("Hàng đợi trống", "Queue empty", "队列为空")
                : l("Reset hàng đợi", "Reset queue", "重置队列")}
          </button>
        }
      >
        {jobs.loading && !jobs.data ? (
          <SkeletonRows />
        ) : jobs.error && !jobs.data ? (
          <EmptyState error message={jobs.error} />
        ) : !jobs.data?.jobs.length ? (
          <EmptyState
            message={l("Chưa có video local trong hàng đợi", "No local videos in the queue", "队列中没有本地视频")}
          />
        ) : (
          <div className="publish-batch-list">
            {jobBatches.map((batch, batchIndex) => {
              const expanded = Boolean(expandedBatches[batch.key]);
              const uniqueVideos = new Set(batch.jobs.map((job) => job.video_id)).size;
              const profileCount = new Set(batch.jobs.map((job) => job.profile_id)).size;
              const completedCount = batch.jobs.filter((job) => job.status === "completed").length;
              const failedCount = batch.jobs.filter((job) => job.status.startsWith("failed")).length;
              const activeCount = batch.jobs.filter((job) => job.active).length;
              const summaryStatus =
                completedCount === batch.jobs.length
                  ? "completed"
                  : failedCount
                    ? "failed"
                    : activeCount
                      ? "uploading"
                      : batch.jobs.some((job) => job.status === "scheduled")
                        ? "scheduled"
                        : batch.jobs[0]?.status || "detected";
              const fallbackName = l(
                `Video riêng ${batchIndex + 1}`,
                `Individual video ${batchIndex + 1}`,
                `单个视频 ${batchIndex + 1}`,
              );
              return (
                <article className={`publish-queue-batch ${expanded ? "expanded" : ""}`} key={batch.key}>
                  <button className="publish-queue-batch-header" onClick={() => toggleBatch(batch.key)}>
                    {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
                    <span className="publish-queue-batch-icon">
                      <Layers3 size={16} />
                    </span>
                    <span>
                      <strong>{batch.name || fallbackName}</strong>
                      <small>
                        {l(
                          `${uniqueVideos} video · ${batch.jobs.length} tác vụ · ${profileCount} hồ sơ`,
                          `${uniqueVideos} videos · ${batch.jobs.length} jobs · ${profileCount} Profiles`,
                          `${uniqueVideos} 个视频 · ${batch.jobs.length} 个任务 · ${profileCount} 个配置文件`,
                        )}
                      </small>
                    </span>
                    <span className="publish-queue-batch-progress">
                      {completedCount > 0 && (
                        <small>
                          {l(
                            `${completedCount}/${batch.jobs.length} hoàn tất`,
                            `${completedCount}/${batch.jobs.length} completed`,
                            `${completedCount}/${batch.jobs.length} 已完成`,
                          )}
                        </small>
                      )}
                      <StatusPill text={jobLabel(summaryStatus, l)} tone={jobTone(summaryStatus)} />
                    </span>
                  </button>
                  {expanded && (
                    <div className="table-wrap publish-queue-table">
                      <table>
                        <thead>
                          <tr>
                            <th>Video</th>
                            <th>{l("Hồ sơ", "Profile", "配置文件")}</th>
                            <th>{l("Nền tảng", "Platforms", "平台")}</th>
                            <th>{l("Trạng thái", "Status", "状态")}</th>
                            <th>{l("Lịch đăng", "Schedule", "排期")}</th>
                            <th>{l("Cập nhật", "Updated", "更新时间")}</th>
                            <th>{l("Thao tác", "Actions", "操作")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {batch.jobs.map((job) => {
                            const retryKey = `${job.profile_id}:${job.video_id}:retry`;
                            const cancelKey = `${job.profile_id}:${job.video_id}:cancel`;
                            return (
                              <tr key={`${job.profile_id}:${job.video_id}`}>
                                <td>
                                  <div className="publish-video-cell">
                                    <FileVideo size={15} />
                                    <span>
                                      <strong title={sourceFileName(job)}>{sourceFileName(job)}</strong>
                                      {job.last_error ? (
                                        <small className="danger-text" title={job.last_error}>
                                          {job.last_error}
                                        </small>
                                      ) : (
                                        job.caption && <small title={job.caption}>{job.caption}</small>
                                      )}
                                    </span>
                                  </div>
                                </td>
                                <td>P{job.profile_id}</td>
                                <td>
                                  <PlatformMarks platforms={job.enabled_platforms || []} />
                                </td>
                                <td>
                                  <StatusPill text={jobLabel(job.status, l)} tone={jobTone(job.status)} />
                                </td>
                                <td>
                                  {job.scheduled_at
                                    ? formatDateTime(job.scheduled_at)
                                    : l("Đăng ngay", "Publish now", "立即发布")}
                                </td>
                                <td>{formatDateTime(job.updated_at)}</td>
                                <td>
                                  <div className="row-actions">
                                    {job.status.startsWith("failed") && (
                                      <button
                                        className="small-button"
                                        disabled={busyJob === retryKey}
                                        onClick={() => void jobAction("retry", job)}
                                      >
                                        <RotateCcw size={13} />
                                        {l("Thử lại", "Retry", "重试")}
                                      </button>
                                    )}
                                    {!job.active && !["completed", "cancelled", "ignored"].includes(job.status) && (
                                      <button
                                        className="small-button danger"
                                        disabled={busyJob === cancelKey}
                                        onClick={() => void jobAction("cancel", job)}
                                      >
                                        <Ban size={13} />
                                        {l("Hủy", "Cancel", "取消")}
                                      </button>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </Section>

      {dialogOpen && (
        <div className="publish-dialog-backdrop" role="presentation" onMouseDown={() => setDialogOpen(false)}>
          <section
            className="publish-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={l("Chọn video từ Douyin", "Select videos from Douyin", "从抖音选择视频")}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <Globe2 size={19} />
              <span>
                <strong>{l("Chọn video từ Douyin", "Select videos from Douyin", "从抖音选择视频")}</strong>
                <small>
                  {l(
                    "Dyna sẽ mở trang cá nhân và extension tự bật chế độ chọn tối đa 50 video.",
                    "Dyna opens the profile and the extension enables selection for up to 50 videos.",
                    "Dyna 将打开个人主页，扩展会自动启用最多 50 个视频的选择模式。",
                  )}
                </small>
              </span>
            </header>
            <label>
              <span>{l("Link trang cá nhân Douyin", "Douyin profile URL", "抖音个人主页链接")}</span>
              <input
                autoFocus
                type="url"
                value={sourceUrl}
                placeholder="https://www.douyin.com/user/..."
                onChange={(event) => setSourceUrl(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void start();
                }}
              />
            </label>
            <footer>
              <button className="small-button" disabled={starting} onClick={() => setDialogOpen(false)}>
                {l("Hủy", "Cancel", "取消")}
              </button>
              <button className="primary-button" disabled={starting || !sourceUrl.trim()} onClick={() => void start()}>
                <Globe2 size={14} />
                {starting
                  ? l("Đang mở...", "Opening...", "正在打开...")
                  : l("Mở Douyin để chọn", "Open Douyin to select", "打开抖音进行选择")}
              </button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
