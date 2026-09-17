// Stable translation keys used by shared application chrome.
export type CatalogMessage = Readonly<Record<"vi" | "en" | "zh", string>>;

export const messages = {
  "app.startup.failed": {
    vi: "Không thể khởi động Dyna",
    en: "Cannot start Dyna",
    zh: "无法启动 Dyna",
  },
} as const satisfies Record<string, CatalogMessage>;

export type MessageId = keyof typeof messages;
