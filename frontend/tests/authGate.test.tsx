import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getHealth, openChat } from "../src/api";
import { AuthGate } from "../src/AuthGate";
import { GlobalRail } from "../src/app/GlobalRail";

type FakeSession = { access_token: string; user: { email: string } };

// A stand-in for the Supabase client. The gate only uses these four calls.
const fake = vi.hoisted(() => {
  const state = {
    session: null as FakeSession | null,
    listener: null as null | ((event: string, session: FakeSession | null) => void),
  };
  const auth = {
    getSession: vi.fn(),
    onAuthStateChange: vi.fn(),
    signInWithOAuth: vi.fn(),
    signOut: vi.fn(),
  };
  return { state, auth, createClient: vi.fn(() => ({ auth })) };
});

vi.mock("@supabase/supabase-js", () => ({ createClient: fake.createClient }));

const session = (token: string, email = "dana@codeology.club"): FakeSession => ({
  access_token: token,
  user: { email },
});

let fetchMock: ReturnType<typeof vi.fn>;

function respondWith(status: number, body: unknown) {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify(body), { status }));
}

function sentToken(call = fetchMock.mock.calls.length - 1): string | null {
  const init = fetchMock.mock.calls[call]?.[1] as RequestInit | undefined;
  return new Headers(init?.headers).get("X-Club-Token");
}

function railProps() {
  return {
    open: false,
    collapsed: false,
    operator: { email: "dana@codeology.club", persona: "Sourcing Director" },
    route: { kind: "chat" as const },
    sessions: [],
    scheduledApprovalCount: 0,
    memoryReviewCount: 0,
    heldEffectCount: 0,
    onToggleCollapsed: vi.fn(),
    onNewChat: vi.fn(),
    onOpenSession: vi.fn(),
    onPin: vi.fn(),
    onRename: vi.fn().mockResolvedValue(undefined),
    onSearch: vi.fn(),
    onClose: vi.fn(),
  };
}

const App = () => <p>App content</p>;

beforeEach(() => {
  fake.state.session = null;
  fake.state.listener = null;
  fake.auth.getSession.mockImplementation(async () => ({ data: { session: fake.state.session }, error: null }));
  fake.auth.onAuthStateChange.mockImplementation((listener) => {
    fake.state.listener = listener;
    return { data: { subscription: { unsubscribe: vi.fn() } } };
  });
  fake.auth.signInWithOAuth.mockResolvedValue({ data: {}, error: null });
  fake.auth.signOut.mockImplementation(async () => {
    fake.state.session = null;
    fake.state.listener?.("SIGNED_OUT", null);
    return { error: null };
  });
  fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("hosted mode", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://demo.supabase.test");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "public-anon-key");
  });

  it("shows the sign-in screen and no app content without a session", async () => {
    render(<AuthGate><App /></AuthGate>);

    expect(await screen.findByRole("button", { name: "Sign in with Google" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sourcecado" })).toBeInTheDocument();
    expect(screen.queryByText("App content")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Sign in with Google" }));
    expect(fake.auth.signInWithOAuth).toHaveBeenCalledWith({
      provider: "google",
      options: { redirectTo: `${window.location.origin}${window.location.pathname}` },
    });
  });

  it("creates the client with the PKCE flow", async () => {
    // A fresh module, so the cached client from earlier tests does not hide the call.
    vi.resetModules();
    const auth = await import("../src/auth");
    auth.authClient();
    expect(fake.createClient).toHaveBeenCalledWith(
      "https://demo.supabase.test",
      "public-anon-key",
      { auth: expect.objectContaining({ flowType: "pkce", detectSessionInUrl: true }) },
    );
  });

  it("renders the app with a session and sends the access token in X-Club-Token", async () => {
    fake.state.session = session("access-1");
    render(<AuthGate><App /></AuthGate>);

    expect(await screen.findByText("App content")).toBeInTheDocument();
    await getHealth();
    expect(sentToken()).toBe("access-1");
  });

  it("offers the access token as the second WebSocket subprotocol", async () => {
    const protocols: (string | string[] | undefined)[] = [];
    class FakeWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 0;
      constructor(_url: string, list?: string | string[]) {
        protocols.push(list);
      }
      send() {}
      close() {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    fake.state.session = session("access-ws");
    render(<AuthGate><App /></AuthGate>);
    await screen.findByText("App content");

    const chat = openChat(() => {});
    chat.close();
    expect(protocols[0]).toEqual(["club", "access-ws"]);
  });

  it("shows the invite-list screen on 403 not_invited", async () => {
    fake.state.session = session("access-1", "guest@example.com");
    render(<AuthGate><App /></AuthGate>);
    await screen.findByText("App content");

    respondWith(403, { error: "not_invited" });
    await act(async () => {
      await getHealth().catch(() => undefined);
    });

    expect(await screen.findByRole("heading", { name: "This account is not on the invite list" })).toBeInTheDocument();
    expect(screen.getByText("guest@example.com")).toBeInTheDocument();
    expect(screen.queryByText("App content")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: "Sign in with Google" })).toBeInTheDocument();
  });

  it("keeps the app on a 403 that is not not_invited", async () => {
    fake.state.session = session("access-1");
    render(<AuthGate><App /></AuthGate>);
    await screen.findByText("App content");

    respondWith(403, { error: "forbidden" });
    await act(async () => {
      await getHealth().catch(() => undefined);
    });
    expect(screen.getByText("App content")).toBeInTheDocument();
  });

  it("stays signed in when a route answers 401 for another reason", async () => {
    // A connector that is not connected answers 401. Found on the live demo:
    // that 401 signed the person out a few seconds after sign-in.
    fake.state.session = session("access-1");
    render(<AuthGate><App /></AuthGate>);
    await screen.findByText("App content");

    respondWith(401, { error: "unauthorized" });
    await act(async () => {
      await getHealth().catch(() => undefined);
    });

    expect(screen.getByText("App content")).toBeInTheDocument();
    expect(fake.auth.signOut).not.toHaveBeenCalled();
  });

  it("drops the session and returns to sign-in on 401 sign_in_required", async () => {
    fake.state.session = session("access-1");
    render(<AuthGate><App /></AuthGate>);
    await screen.findByText("App content");

    respondWith(401, { error: "sign_in_required" });
    await act(async () => {
      await getHealth().catch(() => undefined);
    });

    expect(await screen.findByRole("button", { name: "Sign in with Google" })).toBeInTheDocument();
    expect(screen.queryByText("App content")).toBeNull();
    expect(fake.auth.signOut).toHaveBeenCalledWith({ scope: "local" });
    await getHealth().catch(() => undefined);
    expect(sentToken()).toBeNull();
  });

  it("signs out from the rail and clears the token", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    fake.state.session = session("access-1");
    render(<AuthGate><GlobalRail {...railProps()} /></AuthGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("button", { name: "Sign in with Google" })).toBeInTheDocument();
    expect(fake.auth.signOut).toHaveBeenCalled();
    await getHealth();
    expect(sentToken()).toBeNull();
  });

  it("uses a refreshed token on the next request", async () => {
    fake.state.session = session("access-1");
    render(<AuthGate><App /></AuthGate>);
    await screen.findByText("App content");

    await getHealth();
    expect(sentToken()).toBe("access-1");

    act(() => fake.state.listener?.("TOKEN_REFRESHED", session("access-2")));
    await getHealth();
    expect(sentToken()).toBe("access-2");
  });

  it("removes the sign-in code from the address bar", async () => {
    window.history.replaceState(null, "", "/?code=one-time#/board");
    fake.state.session = session("access-1");
    render(<AuthGate><App /></AuthGate>);
    await screen.findByText("App content");

    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("#/board");
    window.history.replaceState(null, "", "/");
  });
});

describe("local mode", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_SUPABASE_URL", "");
  });

  it("renders the app directly with no Supabase call", async () => {
    render(<AuthGate><App /></AuthGate>);

    expect(screen.getByText("App content")).toBeInTheDocument();
    expect(fake.createClient).not.toHaveBeenCalled();
    expect(fake.auth.getSession).not.toHaveBeenCalled();
    expect(fake.auth.onAuthStateChange).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Sign in with Google" })).toBeNull());
  });

  it("does not show a sign-out control in the rail", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    render(<GlobalRail {...railProps()} />);
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });
});
