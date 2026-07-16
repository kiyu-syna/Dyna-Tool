import { ArrowDownToLine, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { EmptyState, Section } from "../components/Common";
import { usePolling } from "../hooks";

export default function LogsPage() {
  const { data, error, refresh } = usePolling<{ lines: string[]; updated_at: string }>("/api/logs?limit=1000", 2000);
  const [follow, setFollow] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [data, follow]);

  return <Section title="Log hệ thống" className="logs-section" action={<div className="inline-actions">
    <button className={follow ? "small-button active" : "small-button"} onClick={() => setFollow((value) => !value)}><ArrowDownToLine size={14} />Theo log mới</button>
    <button className="icon-button" onClick={refresh} title="Tải lại"><RefreshCw size={16} /></button>
  </div>}>
    {error && !data ? <EmptyState error message={error} /> : <div className="log-view" ref={logRef}>{data?.lines.map((line, index) => <div key={`${index}:${line.slice(0, 20)}`}><span>{String(index + 1).padStart(4, "0")}</span><code>{line}</code></div>)}</div>}
  </Section>;
}
