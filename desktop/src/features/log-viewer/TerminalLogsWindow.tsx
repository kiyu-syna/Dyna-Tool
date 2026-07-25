import { Search, Terminal } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePolling } from "../../shared/hooks/usePolling";
import { LanguageProvider, languageTag, normalizeLanguage, useI18n } from "../../shared/i18n";
import type { LogSnapshot } from "../../shared/types";
import "./log-viewer.css";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
const LOG_PATH = "/api/logs?limit=5000";
const LOG_POLLING = {
  requestPath(current: LogSnapshot | null) {
    return current?.cursor ? `${LOG_PATH}&cursor=${encodeURIComponent(current.cursor)}` : LOG_PATH;
  },
  merge(current: LogSnapshot | null, incoming: LogSnapshot): LogSnapshot {
    if (!current || incoming.reset) return incoming;
    const entries = [...current.entries, ...incoming.entries].slice(-5_000);
    return {
      ...incoming,
      lines: [],
      entries,
      profiles: incoming.profiles.length ? incoming.profiles : current.profiles,
      total: entries.length,
    };
  },
};

export default function TerminalLogsWindow() {
  const language = normalizeLanguage(localStorage.getItem("dyna-language"));
  document.title = "Dyna Logs";
  document.documentElement.lang = languageTag(language);
  document.documentElement.dataset.theme = "dark";
  return (
    <LanguageProvider language={language}>
      <TerminalLogs />
    </LanguageProvider>
  );
}

function TerminalLogs() {
  const { locale, l } = useI18n();
  const logs = usePolling<LogSnapshot>(LOG_PATH, 2000, LOG_POLLING);
  const [scope, setScope] = useState("all");
  const [search, setSearch] = useState("");
  const [levels, setLevels] = useState<string[]>(["INFO", "WARNING", "ERROR", "CRITICAL"]);
  const streamRef = useRef<HTMLDivElement | null>(null);
  const pinnedToBottom = useRef(true);

  const entries = logs.data?.entries || [];
  const profiles = useMemo(() => {
    const configured = logs.data?.profiles || [];
    const known = new Set(configured.map((profile) => profile.id));
    const discovered = entries
      .map((entry) => entry.profile_id)
      .filter((profileId) => profileId && !known.has(profileId));
    return [...configured, ...[...new Set(discovered)].map((id) => ({ id, name: `Profile ${id}` }))];
  }, [entries, logs.data?.profiles]);
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase(locale);
    return entries.filter(
      (entry) =>
        levels.includes(entry.level) &&
        (scope === "all" || (scope === "system" ? !entry.profile_id : entry.profile_id === scope)) &&
        (!query || `${entry.message} ${entry.raw}`.toLocaleLowerCase(locale).includes(query)),
    );
  }, [entries, levels, locale, scope, search]);
  const rowVirtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => streamRef.current,
    estimateSize: () => 22,
    overscan: 20,
  });

  useEffect(() => {
    if (!streamRef.current || !pinnedToBottom.current) return;
    window.requestAnimationFrame(() => {
      if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight;
    });
  }, [filtered]);

  function toggleLevel(level: string) {
    setLevels((current) => (current.includes(level) ? current.filter((item) => item !== level) : [...current, level]));
  }

  return (
    <main className="terminal-log-window">
      <div className="terminal-native-titlebar">
        <Terminal size={14} />
        <span>Dyna Logs</span>
      </div>
      <section className="terminal-toolbar">
        <select
          value={scope}
          onChange={(event) => setScope(event.target.value)}
          aria-label={l("Lọc theo hồ sơ", "Filter by profile", "按配置文件筛选")}
        >
          <option value="all">{l("Tất cả nhật ký", "All logs", "全部日志")}</option>
          <option value="system">{l("Hệ thống", "System", "系统")}</option>
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>
              P{profile.id} · {profile.name}
            </option>
          ))}
        </select>
        <label>
          <Search size={14} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={l("Tìm trong nhật ký...", "Search logs...", "搜索日志...")}
          />
        </label>
        <div className="terminal-levels">
          {LEVELS.map((level) => (
            <button
              key={level}
              className={levels.includes(level) ? `selected ${level.toLowerCase()}` : ""}
              onClick={() => toggleLevel(level)}
            >
              {level}
            </button>
          ))}
        </div>
      </section>
      <div
        className="terminal-stream"
        ref={streamRef}
        onScroll={(event) => {
          const node = event.currentTarget;
          pinnedToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 45;
        }}
      >
        {logs.error && !logs.data ? (
          <div className="terminal-empty error">{logs.error}</div>
        ) : !filtered.length ? (
          <div className="terminal-empty">{l("Chưa có nhật ký phù hợp", "No matching logs", "没有匹配的日志")}</div>
        ) : (
          <div className="terminal-virtual-list" style={{ height: rowVirtualizer.getTotalSize() }}>
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const entry = filtered[virtualRow.index];
              return (
                <div
                  key={entry.id}
                  ref={rowVirtualizer.measureElement}
                  data-index={virtualRow.index}
                  className={`terminal-line terminal-virtual-row ${entry.level.toLowerCase()}`}
                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                >
                  <time>{entry.time || "--:--:--"}</time>
                  <span className="terminal-source">[{entry.profile_id ? `P${entry.profile_id}` : "SYSTEM"}]</span>
                  <span className="terminal-level">{entry.level.padEnd(8, " ")}</span>
                  <code>{entry.message || " "}</code>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <footer className="terminal-footer">
        <span>
          {filtered.length} {l("dòng", "lines", "行")}
        </span>
        <span>
          {scope === "all"
            ? l("Tất cả nguồn", "All sources", "全部来源")
            : scope === "system"
              ? l("Hệ thống", "System", "系统")
              : `Profile ${scope}`}
        </span>
        <span>UTF-8</span>
      </footer>
    </main>
  );
}
