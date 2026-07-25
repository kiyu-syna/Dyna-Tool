import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { request } from "./shared/api/client";

vi.mock("./shared/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("./shared/api/client")>();
  return { ...original, request: vi.fn() };
});

const requestMock = vi.mocked(request);

describe("App startup", () => {
  beforeEach(() => {
    localStorage.setItem("dyna-language", "en");
    requestMock.mockImplementation(async (path) => {
      if (path === "/api/settings") return { settings: {} } as never;
      if (path === "/api/auth/status") {
        return { authenticated: false, user: null } as never;
      }
      if (path.startsWith("/api/license")) return { is_active: false } as never;
      throw new Error(`Unexpected request: ${path}`);
    });
  });

  it("shows the sign-in UI when no authenticated session exists", async () => {
    render(<App />);

    expect(screen.getByText("Checking your session...")).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Sign in to Dyna" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Sign in" })).toHaveLength(2);
  });

  it("shows a retry action when session loading fails", async () => {
    requestMock.mockImplementation(async (path) => {
      if (path === "/api/auth/status") throw new Error("Backend unavailable");
      if (path === "/api/settings") return { settings: {} } as never;
      if (path.startsWith("/api/license")) return { is_active: false } as never;
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<App />);

    expect(await screen.findByText("Cannot start Dyna")).toBeInTheDocument();
    expect(screen.getByText("Backend unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});
