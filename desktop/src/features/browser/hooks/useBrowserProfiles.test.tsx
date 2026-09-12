import { renderHook, waitFor } from "@testing-library/react";
import { StrictMode, useState, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/client";
import type { ProfileConfig, ProfileSummary } from "../../../shared/types";
import { useBrowserProfiles } from "./useBrowserProfiles";

vi.mock("../../../shared/api/client", () => ({ request: vi.fn() }));

const summaries = [{ id: "1", name: "Profile 1" }] as ProfileSummary[];
const profile = { id: "1", name: "Profile 1", browser: {} } as ProfileConfig;
const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;

describe("useBrowserProfiles", () => {
  it("finishes loading when StrictMode restarts the initial effect", async () => {
    vi.mocked(request).mockResolvedValue({ profile } as never);

    const { result } = renderHook(
      () => {
        const [, setExpandedIds] = useState<string[]>([]);
        const [, setMessage] = useState("");
        const [, setMessageError] = useState(false);
        return useBrowserProfiles({
          summaries,
          l: (_vi, en) => en,
          setBusy: vi.fn(),
          setExpandedIds,
          setMessage,
          setMessageError,
        });
      },
      { wrapper },
    );

    await waitFor(() => expect(result.current.loadingProfiles).toBe(false));
    expect(result.current.profileList).toHaveLength(1);
    expect(result.current.profileList[0].profile).toEqual(profile);
  });
});
