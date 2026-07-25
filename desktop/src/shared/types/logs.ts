export type LogEntry = {
  id: string;
  timestamp: string;
  time: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL" | string;
  profile_id: string;
  message: string;
  raw: string;
  legacy: boolean;
};

export type LogSnapshot = {
  lines: string[];
  entries: LogEntry[];
  profiles: Array<{ id: string; name: string }>;
  updated_at: string;
  file_count: number;
  total: number;
  cursor: string;
  reset: boolean;
};
