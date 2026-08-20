import { useState, type Dispatch, type SetStateAction } from "react";
import { formatDateTime } from "../../../shared/api/client";
import {
  buildRandomSchedule,
  defaultBatchName,
  defaultScheduleDate,
  localDateTimeValue,
  parseScheduleTimes,
  type PublishItemDraft,
} from "../utils";

type Localize = (vi: string, en: string, zh: string) => string;

type PublishDraftOptions = {
  l: Localize;
  setMessage: Dispatch<SetStateAction<string>>;
  setError: Dispatch<SetStateAction<string>>;
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function usePublishDraft({ l, setMessage, setError }: PublishDraftOptions) {
  const [items, setItems] = useState<PublishItemDraft[]>([]);
  const [batchName, setBatchName] = useState(defaultBatchName);
  const [scheduleStartDate, setScheduleStartDate] = useState(defaultScheduleDate);
  const [videosPerDay, setVideosPerDay] = useState(2);
  const [scheduleTimes, setScheduleTimes] = useState("09:00, 19:00");
  const [scheduleMode, setScheduleMode] = useState<"fixed" | "random">("fixed");
  const [randomStartTime, setRandomStartTime] = useState("09:00");
  const [randomEndTime, setRandomEndTime] = useState("21:00");
  const missingCaptionCount = items.filter((item) => !item.caption.trim()).length;
  const invalidScheduleCount = items.filter((item) => {
    if (!item.scheduled_at) return false;
    const scheduled = new Date(item.scheduled_at).getTime();
    return !Number.isFinite(scheduled) || scheduled <= Date.now() + 30_000;
  }).length;
  const validItems = items.length > 0 && missingCaptionCount === 0 && invalidScheduleCount === 0;

  async function chooseFiles() {
    setError("");
    setMessage("");
    if (!window.dyna?.selectMedia) {
      setError(
        l(
          "Bộ chọn file chỉ hoạt động trong ứng dụng Dyna Desktop.",
          "The file picker is only available in Dyna Desktop.",
          "文件选择器仅可在 Dyna Desktop 中使用。",
        ),
      );
      return;
    }
    try {
      const selected = await window.dyna.selectMedia();
      if (!items.length && selected.length) setBatchName(defaultBatchName());
      setItems((current) => {
        const known = new Set(current.map((item) => item.file_path));
        const additions = selected.flatMap((filePath) => {
          if (known.has(filePath)) return [];
          known.add(filePath);
          return [{ file_path: filePath, caption: "", scheduled_at: "", source_type: "local" as const }];
        });
        const merged = [...current, ...additions];
        if (merged.length > 50) {
          setError(
            l(
              "Mỗi lần chỉ được chọn tối đa 50 video.",
              "You can select up to 50 videos at a time.",
              "每次最多可选择 50 个视频。",
            ),
          );
          return merged.slice(0, 50);
        }
        return merged;
      });
    } catch (error) {
      setError(errorText(error));
    }
  }

  function updateItem(filePath: string, changes: Partial<PublishItemDraft>) {
    setItems((current) => current.map((item) => (item.file_path === filePath ? { ...item, ...changes } : item)));
  }

  function applySharedCaption(caption: string) {
    const normalized = caption.trim();
    if (!normalized) {
      setError(l("Hãy nhập mô tả dùng chung.", "Enter a shared caption.", "请输入通用文案。"));
      return false;
    }
    setItems((current) => current.map((item) => ({ ...item, caption: normalized })));
    setError("");
    setMessage(
      l(
        `Đã áp dụng cùng một mô tả cho ${items.length} video.`,
        `Applied the same caption to ${items.length} videos.`,
        `已为 ${items.length} 个视频应用相同文案。`,
      ),
    );
    return true;
  }

  function renewBatchName() {
    setBatchName(defaultBatchName());
  }

  function applyAutoSchedule() {
    setError("");
    setMessage("");
    if (!items.length) {
      setError(
        l(
          "Hãy chọn video trước khi tự chia lịch.",
          "Select videos before generating a schedule.",
          "自动排期前请先选择视频。",
        ),
      );
      return;
    }
    const slots = parseScheduleTimes(scheduleTimes);
    if (!scheduleStartDate) {
      setError(l("Hãy chọn ngày bắt đầu.", "Select a start date.", "请选择开始日期。"));
      return;
    }
    if (scheduleMode === "fixed" && slots.length < videosPerDay) {
      setError(
        l(
          `Cần ít nhất ${videosPerDay} khung giờ hợp lệ cho ${videosPerDay} video/ngày.`,
          `At least ${videosPerDay} valid time slots are required for ${videosPerDay} videos/day.`,
          `每天发布 ${videosPerDay} 个视频至少需要 ${videosPerDay} 个有效时段。`,
        ),
      );
      return;
    }
    const start = new Date(`${scheduleStartDate}T00:00:00`);
    if (!Number.isFinite(start.getTime())) {
      setError(l("Ngày bắt đầu không hợp lệ.", "The start date is invalid.", "开始日期无效。"));
      return;
    }

    if (scheduleMode === "random") {
      const randomValues = buildRandomSchedule(
        items.length,
        scheduleStartDate,
        videosPerDay,
        randomStartTime,
        randomEndTime,
      );
      if (randomValues.length !== items.length) {
        setError(
          l(
            "Không thể tạo đủ giờ ngẫu nhiên trong khoảng đã chọn. Hãy mở rộng khoảng giờ hoặc giảm số video/ngày.",
            "The selected range cannot fit enough random times. Widen the range or reduce videos per day.",
            "所选时间范围无法容纳足够的随机发布时间，请扩大范围或减少每日视频数。",
          ),
        );
        return;
      }
      setItems((current) =>
        current.map((item, index) => ({
          ...item,
          scheduled_at: randomValues[index],
        })),
      );
      setMessage(
        l(
          `Đã tạo giờ đăng ngẫu nhiên cho ${randomValues.length} video trong khoảng ${randomStartTime}–${randomEndTime} mỗi ngày.`,
          `Created random publishing times for ${randomValues.length} videos between ${randomStartTime} and ${randomEndTime} each day.`,
          `已为 ${randomValues.length} 个视频生成每日 ${randomStartTime}–${randomEndTime} 之间的随机发布时间。`,
        ),
      );
      return;
    }

    const generated: string[] = [];
    const minimum = Date.now() + 120_000;
    const maximum = Date.now() + 365 * 24 * 60 * 60 * 1000;
    let dayOffset = 0;
    while (generated.length < items.length && dayOffset <= 365) {
      for (const slot of slots.slice(0, videosPerDay)) {
        const [hour, minute] = slot.split(":").map(Number);
        const candidate = new Date(start);
        candidate.setDate(start.getDate() + dayOffset);
        candidate.setHours(hour, minute, 0, 0);
        const timestamp = candidate.getTime();
        if (timestamp > minimum && timestamp <= maximum) {
          generated.push(localDateTimeValue(candidate));
          if (generated.length === items.length) break;
        }
      }
      dayOffset += 1;
    }
    if (generated.length < items.length) {
      setError(
        l(
          "Không thể xếp đủ lịch trong giới hạn 365 ngày. Hãy chọn ngày gần hơn.",
          "The schedule cannot fit within 365 days. Choose an earlier start date.",
          "无法在 365 天内完成排期，请选择更近的开始日期。",
        ),
      );
      return;
    }
    setItems((current) =>
      current.map((item, index) => ({
        ...item,
        scheduled_at: generated[index],
      })),
    );
    const last = generated[generated.length - 1];
    setMessage(
      l(
        `Đã tự chia lịch cho ${generated.length} video, từ ${formatDateTime(new Date(generated[0]).toISOString())} đến ${formatDateTime(new Date(last).toISOString())}.`,
        `Scheduled ${generated.length} videos from ${formatDateTime(new Date(generated[0]).toISOString())} to ${formatDateTime(new Date(last).toISOString())}.`,
        `已为 ${generated.length} 个视频自动排期。`,
      ),
    );
  }

  function clearAllSchedules() {
    setItems((current) => current.map((item) => ({ ...item, scheduled_at: "" })));
    setMessage(
      l(
        "Đã xóa toàn bộ lịch; các video sẽ chuyển sang đăng ngay.",
        "All schedules were cleared; videos will publish immediately.",
        "所有排期已清除；视频将改为立即发布。",
      ),
    );
    setError("");
  }

  return {
    items,
    setItems,
    batchName,
    setBatchName,
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
    missingCaptionCount,
    invalidScheduleCount,
    validItems,
    chooseFiles,
    updateItem,
    applySharedCaption,
    renewBatchName,
    applyAutoSchedule,
    clearAllSchedules,
  };
}
