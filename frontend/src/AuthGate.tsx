import { useEffect, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";

import {
  authClient,
  isHosted,
  onAuthProblem,
  rememberSession,
  removeCodeFromUrl,
  signInWithGoogle,
  signOut,
  watchBackendResponses,
} from "./auth";

type GateState =
  | { kind: "loading" }
  | { kind: "signedOut" }
  | { kind: "signedIn"; email: string }
  | { kind: "notInvited"; email: string };

/** Local mode renders the app as before. Hosted mode puts Google sign-in in front of it. */
export function AuthGate({ children }: { children: ReactNode }) {
  if (!isHosted()) return <>{children}</>;
  return <HostedGate>{children}</HostedGate>;
}

function HostedGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>({ kind: "loading" });

  useEffect(() => {
    const auth = authClient().auth;
    let active = true;
    const apply = (session: Session | null) => {
      rememberSession(session);
      if (!active) return;
      setState((previous) => {
        if (!session) return { kind: "signedOut" };
        const email = session.user?.email ?? "";
        return previous.kind === "notInvited" ? { kind: "notInvited", email } : { kind: "signedIn", email };
      });
    };
    // Fires on sign-in, sign-out and every token refresh.
    const { data } = auth.onAuthStateChange((_event, session) => apply(session));
    void auth.getSession().then(({ data: current }) => {
      removeCodeFromUrl();
      apply(current.session);
    });
    const restoreFetch = watchBackendResponses();
    const stopProblems = onAuthProblem((problem) => {
      if (problem === "not_invited") {
        setState((previous) => (previous.kind === "signedIn" ? { kind: "notInvited", email: previous.email } : previous));
        return;
      }
      // 401: the backend refused the token. Drop the session and ask again.
      rememberSession(null);
      setState({ kind: "signedOut" });
      void signOut();
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
      restoreFetch();
      stopProblems();
    };
  }, []);

  if (state.kind === "loading") {
    return <main className="auth-screen" aria-busy="true" aria-label="Loading Sourcecado" />;
  }
  if (state.kind === "signedOut") return <SignInScreen />;
  if (state.kind === "notInvited") return <NotInvitedScreen email={state.email} />;
  return <>{children}</>;
}

function SignInScreen() {
  const [failed, setFailed] = useState(false);
  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <img className="auth-mark" src="/favicon.png" alt="" width={48} height={48} />
        <h1 id="auth-title">Sourcecado</h1>
        <p>Find people, prepare outreach, and keep every conversation moving.</p>
        <button
          type="button"
          className="auth-button"
          onClick={() => {
            setFailed(false);
            signInWithGoogle().catch(() => setFailed(true));
          }}
        >
          Sign in with Google
        </button>
        {failed && <p className="auth-error" role="alert">Sign-in did not start. Try again.</p>}
      </section>
    </main>
  );
}

function NotInvitedScreen({ email }: { email: string }) {
  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <img className="auth-mark" src="/favicon.png" alt="" width={48} height={48} />
        <h1 id="auth-title">This account is not on the invite list</h1>
        <p>
          You are signed in as <strong className="auth-email">{email || "an unknown account"}</strong>. Ask the
          operator to add this address, or sign in with another Google account.
        </p>
        <button type="button" className="auth-button" onClick={() => void signOut()}>
          Sign out
        </button>
      </section>
    </main>
  );
}
