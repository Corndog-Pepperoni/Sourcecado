import { Component, StrictMode, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AuthGate } from "./AuthGate";
import "./styles.css";

class Boundary extends Component<{ children: ReactNode }, { err: string | null }> {
  state = { err: null as string | null };
  static getDerivedStateFromError(err: Error) {
    return { err: err.message || String(err) };
  }
  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error(err, info.componentStack);
  }
  render() {
    if (this.state.err) {
      return (
        <main className="fatal-error-shell">
          <p className="fatal-error-message">{this.state.err}</p>
        </main>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boundary>
      <AuthGate>
        <App />
      </AuthGate>
    </Boundary>
  </StrictMode>
);
