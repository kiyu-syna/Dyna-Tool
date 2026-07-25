import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/inter/vietnamese-400.css";
import "@fontsource/inter/vietnamese-500.css";
import "@fontsource/inter/vietnamese-600.css";
import "@fontsource/inter/vietnamese-700.css";
import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/index.css";

const standaloneView = new URLSearchParams(window.location.search).get("view");
const TerminalLogsWindow = lazy(() => import("./features/log-viewer/TerminalLogsWindow"));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {standaloneView === "logs-terminal" ? (
      <Suspense
        fallback={
          <main className="startup-shell">
            <strong>Dyna Logs</strong>
          </main>
        }
      >
        <TerminalLogsWindow />
      </Suspense>
    ) : (
      <App />
    )}
  </React.StrictMode>,
);
