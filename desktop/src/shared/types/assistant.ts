export type AssistantAction = {
  type: "start_profile" | "stop_profile" | "refresh_readiness";
  args: Record<string, unknown>;
};

export type AssistantChatResponse = {
  reply: string;
  actions: AssistantAction[];
  proposal_token: string;
  proposal_expires_in: number;
  provider: string;
  model: string;
};

export type AssistantConfirmResponse = {
  ok: boolean;
  results: Array<{
    type: AssistantAction["type"];
    ok: boolean;
    result?: unknown;
    error?: string;
  }>;
};
