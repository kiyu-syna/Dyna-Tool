# Dyna Tool

<div align="center">

![Dyna Tool](desktop/src-tauri/icons/128x128.png)

### Automated Video Processing & Multi-Platform Publishing Powerhouse

[![Release](https://img.shields.io/github/v/release/kiyu-syna/Dyna-Tool?style=flat-square&color=blue)](https://github.com/kiyu-syna/Dyna-Tool/releases)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078d7?style=flat-square&logo=windows)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Tauri](https://img.shields.io/badge/Tauri-v2-24c8db?style=flat-square&logo=tauri&logoColor=white)](https://tauri.app/)
[![React](https://img.shields.io/badge/Frontend-React%2019-61dafb?style=flat-square&logo=react&logoColor=black)](https://react.dev/)

**Dyna Tool** is an advanced desktop automation platform engineered for high-efficiency vertical video processing, content ingestion, and scheduled publishing across multiple platforms (TikTok, YouTube Shorts, Facebook Reels).

Built with a lightning-fast **Tauri v2 (Rust + WebView2 + React)** front-end and an asynchronous **Python** backend orchestration engine.

[Features](#-key-features) • [Installation](#-installation--setup) • [Quick Start](#-quick-start) • [247-background-operation](#-247-background-operation) • [Project Structure](#-project-structure)

---

</div>

## ✨ Key Features

- **⚡ Multi-Platform Publishing Automation**
  - High-precision browser automation powered by Playwright and isolated Chrome profiles.
  - Native upload pipelines for **TikTok**, **YouTube Studio (Shorts)**, and **Facebook Reels**.
  - Advanced anti-detection, custom proxy support, and persistent session state handling.

- **📥 Video Ingestion & Processing Pipeline**
  - Automated tracking, vertical video parsing, and automated media transformation.
  - Video format standardization, caption burning, and FFmpeg transcoding pipelines.

- **🪟 Ultra-Lightweight Desktop Shell (Tauri v2)**
  - Native Windows desktop application with negligible RAM footprint (~60MB idle vs. 300MB+ in Electron).
  - **System Tray Integration:** Closes to the system tray (`[X]` hides to tray) to maintain continuous 24/7 background tasks without cluttering your taskbar.

- **🔔 Telegram Bot & Remote Command Center**
  - Real-time job status notifications, confirmation prompts, upload success/failure alerts, and system health monitoring.

---

## 🏗️ Project Structure

```text
Dyna Tool/
├── main.py                      # Application bootstrap script
├── Chay_Dyna_TreoMay.bat        # Optimized launcher for continuous background runtime
├── Tao_Shortcut_Desktop.bat     # Windows desktop shortcut generator
├── core/                        # Central configuration, logging, and shared utilities
├── services/                    # Domain services (browser orchestration, media, tracking)
├── profile_automation/          # Browser automation, upload pipelines, profile managers
│   ├── uploaders/               # YouTube, TikTok, and Facebook uploader implementations
│   └── profiles/                # Isolated channel profiles and settings
├── desktop/                     # Tauri v2 + React 19 frontend application
│   ├── src/                     # React UI components, virtual tables, and hooks
│   └── src-tauri/               # Rust desktop shell, system tray, and native bridge
├── desktop_backend/             # Python IPC/REST server serving desktop UI requests
├── payment_server/              # Telegram command listener and webhook integrations
├── runtime/                     # Working directories, SQLite history, and temp cache (untracked)
└── tests/                       # Cross-domain unit and integration test suites
```

---

## 💻 System Requirements

- **Operating System:** Windows 10 (64-bit) or Windows 11
- **Python:** Version 3.11 or higher
- **Node.js:** Node.js 20+ and npm
- **C++ Build Tools:** Visual Studio C++ Build Tools (required only when compiling Tauri from source)
- **Browser:** Google Chrome or Chromium installed on the host machine

---

## 📦 Installation & Setup

### 1. Download Pre-built Release (Recommended for Users)
If you simply want to use the application without building from source:
1. Navigate to the **[Releases](https://github.com/kiyu-syna/Dyna-Tool/releases)** page.
2. Download the latest installer: `Dyna_Setup.exe`.
3. Run the installer to set up Dyna Tool with desktop and Start Menu shortcuts.

---

### 2. Development Setup (From Source)

#### Step 1: Clone the Repository
```powershell
git clone https://github.com/kiyu-syna/Dyna-Tool.git
cd Dyna-Tool
```

#### Step 2: Set Up Python Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

#### Step 3: Install Desktop Dependencies & Build Assets
```powershell
cd desktop
npm ci
npm run build
cd ..
```

#### Step 4: Configure Local Settings
Copy the example configuration to initialize your local settings:
```powershell
Copy-Item config/settings.example.json config/settings.json
```
> **Security Notice:** Never commit `config/settings.json`, API keys, or Telegram bot tokens into version control.

---

## 🚀 Quick Start

### Running in Development Mode
Launch both the Tauri desktop UI and local backend services:
```powershell
.\.venv\Scripts\python.exe main.py
```
*Or directly through npm in the `desktop/` directory:*
```powershell
cd desktop
npm run dev
```

---

## 🌙 24/7 Background Operation

Dyna Tool is engineered to run seamlessly around the clock on your workstation without causing UI lag or memory bottlenecks:

1. **System Tray Mode:**
   - Clicking the close button **`[X]`** hides the window directly to the Windows System Tray (next to the clock).
   - Left-click the tray icon to toggle the window.
   - Right-click the tray icon for options to show, hide, or completely quit the process.

2. **Personal In-Place Launcher:**
   - Execute `Chay_Dyna_TreoMay.bat` to launch the compiled release binary directly using your current workspace configurations and profiles without duplication.
   - Run `Tao_Shortcut_Desktop.bat` to create a 1-click Desktop shortcut.

3. **RAM & CPU Optimization Recommendations:**
   - **Concurrency Limit:** Keep `MAX_CONCURRENT_UPLOADS` set to 1 or 2 in `config/settings.json` to avoid multi-browser memory spikes.
   - **Windows Power Settings:** Set your screen to turn off after 5 minutes, but configure **Sleep** to **Never** when plugged in.

---

## 🛠️ Building Standalone Binaries

To produce standalone release executables and an NSIS Windows installer:

```powershell
cd desktop
npm run package:win
```

The output installer will be packaged in:
```text
desktop/src-tauri/target/release/bundle/nsis/Dyna_0.1.0_x64-setup.exe
```

---

## 🧪 Testing

Execute the test suites from the project root:

```powershell
# Run backend unit and integration tests
.\.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py" -q

# Run frontend test suite
cd desktop
npm run test
```

---

## 🛡️ Security & Privacy

- **Zero Credential Leaking:** User login sessions, SQLite activity history, and authentication tokens are kept isolated inside `runtime/` and local browser profile data directories.
- **Git Safety:** Sensitive files, API secrets, and compiled distribution packages are enforced in `.gitignore`.

---

## 🙏 Acknowledgements & Third-Party Credits

- **[Tauri](https://tauri.app/)** - High-performance desktop application framework.
- **[Playwright](https://playwright.dev/)** - Reliable end-to-end browser automation.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE). See the LICENSE file for details.
