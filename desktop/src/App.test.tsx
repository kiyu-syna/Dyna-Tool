import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { request } from "./shared/api/client";

vi.mock("./shared/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("./shared/api/client")>();
  return { ...original, request: vi.fn() };
});

vi.mock("./app/AppShell", () => ({
  default: () => <main>Dyna workspace</main>,
}));

const requestMock = vi.mocked(request);

describe("App startup", () => {
  beforeEach(() => {
    localStorage.setItem("dyna-language", "en");
    requestMock.mockReset();
    requestMock.mockResolvedValue({ settings: {} } as never);
  });

  it("opens the workspace without requesting an account session", async () => {
    render(<App />);

    expect(screen.getByText("Dyna workspace")).toBeInTheDocument();
    await waitFor(() => expect(requestMock).toHaveBeenCalledWith("/api/settings"));
    expect(requestMock).not.toHaveBeenCalledWith("/api/auth/status");
  });

  it("keeps the workspace open when optional settings fail", async () => {
    requestMock.mockRejectedValue(new Error("Backend unavailable"));

    render(<App />);

    expect(screen.getByText("Dyna workspace")).toBeInTheDocument();
    await waitFor(() => expect(requestMock).toHaveBeenCalledWith("/api/settings"));
  });
});
