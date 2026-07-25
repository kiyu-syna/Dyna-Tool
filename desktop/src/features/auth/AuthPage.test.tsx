import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { request } from "../../shared/api/client";
import { LanguageProvider } from "../../shared/i18n";
import AuthPage from "./AuthPage";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../shared/api/client")>();
  return { ...original, request: vi.fn() };
});

describe("AuthPage", () => {
  it("submits the registration form and returns the authenticated user", async () => {
    const user = userEvent.setup();
    const onAuthenticated = vi.fn();
    vi.mocked(request).mockResolvedValue({
      ok: true,
      user: { username: "alice", phone: "0901234567" },
    });

    render(
      <LanguageProvider language="en">
        <AuthPage onAuthenticated={onAuthenticated} />
      </LanguageProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Register" }));
    await user.type(screen.getByLabelText("Phone number"), "0901234567");
    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "secret12");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(request).toHaveBeenCalledWith("/api/auth/register", {
      method: "POST",
      body: {
        phone: "0901234567",
        username: "alice",
        password: "secret12",
      },
    });
    expect(onAuthenticated).toHaveBeenCalledWith({
      username: "alice",
      phone: "0901234567",
    });
  });
});
