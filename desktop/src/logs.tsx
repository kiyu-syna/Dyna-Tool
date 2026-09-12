import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/inter/vietnamese-400.css";
import "@fontsource/inter/vietnamese-500.css";
import "@fontsource/inter/vietnamese-600.css";
import "@fontsource/inter/vietnamese-700.css";
import React from "react";
import ReactDOM from "react-dom/client";
import TerminalLogsWindow from "./features/log-viewer/TerminalLogsWindow";
import { installTauriBridge } from "./shared/platform/tauriBridge";
import "./styles/index.css";

installTauriBridge();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <TerminalLogsWindow />
  </React.StrictMode>,
);
