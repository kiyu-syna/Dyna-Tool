use rand::RngCore;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

#[derive(Clone)]
struct BackendState {
    inner: Arc<Mutex<BackendInner>>,
    token: String,
    client: reqwest::Client,
    quitting: Arc<AtomicBool>,
    shutdown_started: Arc<AtomicBool>,
    restart_scheduled: Arc<AtomicBool>,
}

struct BackendInner {
    child: Option<Child>,
    info: Option<Value>,
    generation: u64,
    last_focused_selection_id: String,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ApiRequest {
    path: String,
    method: Option<String>,
    body: Option<Value>,
    timeout_ms: Option<u64>,
}

impl BackendState {
    fn new() -> Self {
        let mut bytes = [0_u8; 32];
        rand::rng().fill_bytes(&mut bytes);
        let token = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
        Self {
            inner: Arc::new(Mutex::new(BackendInner {
                child: None,
                info: None,
                generation: 0,
                last_focused_selection_id: String::new(),
            })),
            token,
            client: reqwest::Client::new(),
            quitting: Arc::new(AtomicBool::new(false)),
            shutdown_started: Arc::new(AtomicBool::new(false)),
            restart_scheduled: Arc::new(AtomicBool::new(false)),
        }
    }

    fn info(&self) -> Option<Value> {
        self.inner.lock().ok()?.info.clone()
    }

    fn child_is_running(&self) -> bool {
        self.inner
            .lock()
            .map(|inner| inner.child.is_some())
            .unwrap_or(false)
    }

    fn stop_child(&self) {
        let child = if let Ok(mut inner) = self.inner.lock() {
            inner.generation = inner.generation.wrapping_add(1);
            inner.info = None;
            inner.child.take()
        } else {
            None
        };
        if let Some(mut child) = child {
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                let pid = child.id().to_string();
                let mut killer = Command::new("taskkill");
                killer
                    .args(["/PID", &pid, "/T", "/F"])
                    .creation_flags(0x0800_0000);
                let _ = killer.output();
            }
            #[cfg(not(windows))]
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn project_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("src-tauri must live under desktop")
        .to_path_buf()
}

fn backend_command(app: &tauri::AppHandle, token: &str) -> Result<Command, String> {
    let default_port = if cfg!(debug_assertions) { "0" } else { "8765" };
    let port = std::env::var("DYNA_EXTENSION_PORT").unwrap_or_else(|_| default_port.into());
    if cfg!(debug_assertions) {
        let root = project_root();
        let python = std::env::var_os("DYNA_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join(".venv").join("Scripts").join("python.exe"));
        if !python.exists() {
            return Err(format!("Không tìm thấy Python: {}", python.display()));
        }
        let mut command = Command::new(python);
        command.current_dir(root).args([
            "-u",
            "-m",
            "desktop_backend.api",
            "--port",
            &port,
            "--token",
            token,
        ]);
        return Ok(command);
    }

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?;
    let executable = resource_dir.join("backend").join("DynaBackend.exe");
    if !executable.exists() {
        return Err(format!(
            "Không tìm thấy backend đóng gói: {}",
            executable.display()
        ));
    }
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;
    let mut command = Command::new(executable);
    command
        .current_dir(&data_dir)
        .args(["--port", &port, "--token", token])
        .env("DYNA_DATA_DIR", &data_dir)
        .env(
            "DYNA_PLAYWRIGHT_DRIVER_DIR",
            resource_dir.join("playwright-driver"),
        );
    Ok(command)
}

fn schedule_backend_restart(app: tauri::AppHandle, state: BackendState, delay: Duration) {
    if state.quitting.load(Ordering::SeqCst) || state.restart_scheduled.swap(true, Ordering::SeqCst)
    {
        return;
    }
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(delay).await;
        state.restart_scheduled.store(false, Ordering::SeqCst);
        if state.quitting.load(Ordering::SeqCst) || state.child_is_running() {
            return;
        }
        if let Err(error) = start_backend(app.clone(), state.clone()) {
            let _ = app.emit(
                "dyna:backend-status",
                json!({ "state": "failed", "error": error }),
            );
            schedule_backend_restart(app, state, Duration::from_secs(2));
        }
    });
}

fn monitor_backend(app: tauri::AppHandle, state: BackendState, generation: u64) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(250));
        let exit_result = {
            let Ok(mut inner) = state.inner.lock() else {
                return;
            };
            if inner.generation != generation {
                return;
            }
            let Some(child) = inner.child.as_mut() else {
                return;
            };
            match child.try_wait() {
                Ok(None) => None,
                Ok(Some(status)) => {
                    inner.child.take();
                    inner.info = None;
                    Some(Ok(status.code()))
                }
                Err(error) => {
                    inner.child.take();
                    inner.info = None;
                    Some(Err(error.to_string()))
                }
            }
        };

        let Some(result) = exit_result else {
            continue;
        };
        if state.quitting.load(Ordering::SeqCst) {
            let _ = app.emit("dyna:backend-status", json!({ "state": "stopped" }));
            return;
        }
        let status = match result {
            Ok(code) => json!({ "state": "failed", "code": code }),
            Err(error) => json!({ "state": "failed", "error": error }),
        };
        let _ = app.emit("dyna:backend-status", status);
        schedule_backend_restart(app, state, Duration::from_millis(700));
        return;
    });
}

fn start_backend(app: tauri::AppHandle, state: BackendState) -> Result<(), String> {
    if state.quitting.load(Ordering::SeqCst) {
        return Err("Dyna đang tắt".into());
    }
    if state.child_is_running() {
        return Ok(());
    }
    let mut command = backend_command(&app, &state.token)?;
    command
        .env("PYTHONUTF8", "1")
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }

    let mut child = command.spawn().map_err(|error| error.to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or("Không thể đọc stdout của backend")?;
    let stderr = child
        .stderr
        .take()
        .ok_or("Không thể đọc stderr của backend")?;
    let generation = {
        let mut inner = state.inner.lock().map_err(|error| error.to_string())?;
        if inner.child.is_some() {
            let _ = child.kill();
            return Ok(());
        }
        inner.generation = inner.generation.wrapping_add(1);
        inner.info = None;
        inner.child = Some(child);
        inner.generation
    };
    let _ = app.emit("dyna:backend-status", json!({ "state": "starting" }));

    let stdout_app = app.clone();
    let stdout_state = state.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(marker) = line.find("DYNA_API_READY ") {
                let payload = &line[marker + "DYNA_API_READY ".len()..];
                match serde_json::from_str::<Value>(payload) {
                    Ok(mut info) => {
                        let host = info
                            .get("host")
                            .and_then(Value::as_str)
                            .unwrap_or("127.0.0.1");
                        let port = info.get("port").and_then(Value::as_u64).unwrap_or(8765);
                        info["baseUrl"] = Value::String(format!("http://{host}:{port}"));
                        let current = if let Ok(mut inner) = stdout_state.inner.lock() {
                            if inner.generation == generation && inner.child.is_some() {
                                inner.info = Some(info.clone());
                                true
                            } else {
                                false
                            }
                        } else {
                            false
                        };
                        if current {
                            let mut status = info;
                            status["state"] = Value::String("ready".into());
                            let _ = stdout_app.emit("dyna:backend-status", status);
                        }
                    }
                    Err(error) => {
                        let _ = stdout_app.emit(
                            "dyna:backend-log",
                            format!("Ready marker không hợp lệ: {error}"),
                        );
                    }
                }
            } else if !line.trim().is_empty() {
                let _ = stdout_app.emit("dyna:backend-log", line);
            }
        }
    });

    let stderr_app = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                let _ = stderr_app.emit("dyna:backend-log", line);
            }
        }
    });
    monitor_backend(app, state, generation);
    Ok(())
}

async fn wait_for_backend(state: &BackendState) -> Result<Value, String> {
    for _ in 0..150 {
        if let Some(info) = state.info() {
            return Ok(info);
        }
        if state.quitting.load(Ordering::SeqCst) {
            return Err("Dyna đang tắt".into());
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    Err("Python backend startup timed out".into())
}

fn validate_request(request: &ApiRequest) -> Result<(reqwest::Method, Duration), String> {
    if !request.path.starts_with("/api/") {
        return Err("Invalid API path".into());
    }
    let method = request.method.as_deref().unwrap_or("GET").to_uppercase();
    if !["GET", "POST", "PUT", "DELETE"].contains(&method.as_str()) {
        return Err("Invalid API method".into());
    }
    let method =
        reqwest::Method::from_bytes(method.as_bytes()).map_err(|error| error.to_string())?;
    let timeout = Duration::from_millis(request.timeout_ms.unwrap_or(55_000).clamp(5_000, 600_000));
    Ok((method, timeout))
}

async fn send_backend_request(
    request: &ApiRequest,
    info: &Value,
    state: &BackendState,
) -> Result<Value, (String, bool)> {
    let (method, timeout) = validate_request(request).map_err(|error| (error, false))?;
    let base_url = info
        .get("baseUrl")
        .and_then(Value::as_str)
        .ok_or_else(|| ("Backend URL is unavailable".into(), false))?;
    let mut outgoing = state
        .client
        .request(method, format!("{base_url}{}", request.path))
        .timeout(timeout)
        .header("X-Dyna-Token", &state.token);
    if let Some(body) = &request.body {
        outgoing = outgoing.json(body);
    }
    let response = outgoing
        .send()
        .await
        .map_err(|error| (error.to_string(), error.is_connect()))?;
    let status = response.status();
    let text = response
        .text()
        .await
        .map_err(|error| (error.to_string(), error.is_connect()))?;
    let data = serde_json::from_str::<Value>(&text).unwrap_or_else(|_| json!({ "detail": text }));
    if !status.is_success() {
        return Err((
            data.get("detail")
                .and_then(Value::as_str)
                .unwrap_or("Backend request failed")
                .into(),
            false,
        ));
    }
    Ok(data)
}

async fn restart_backend(app: &tauri::AppHandle, state: &BackendState) -> Result<Value, String> {
    state.stop_child();
    let _ = app.emit("dyna:backend-status", json!({ "state": "starting" }));
    start_backend(app.clone(), state.clone())?;
    wait_for_backend(state).await
}

#[tauri::command]
fn backend_info(state: tauri::State<'_, BackendState>) -> Value {
    state.info().map_or_else(
        || json!({ "state": "starting" }),
        |mut info| {
            info["state"] = Value::String("ready".into());
            info
        },
    )
}

#[tauri::command]
async fn dyna_api(
    request: ApiRequest,
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<Value, String> {
    validate_request(&request)?;
    for attempt in 0..2 {
        let info = wait_for_backend(&state).await?;
        match send_backend_request(&request, &info, &state).await {
            Ok(data) => return Ok(data),
            Err((_error, true)) if attempt == 0 => {
                restart_backend(&app, &state).await?;
            }
            Err((error, _)) => return Err(error),
        }
    }
    Err("Python backend không khả dụng".into())
}

fn checked_id(value: &str, message: &str) -> Result<String, String> {
    let normalized = value.trim().to_ascii_lowercase();
    if normalized.len() != 32 || !normalized.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(message.into());
    }
    Ok(normalized)
}

fn media_url(
    state: &BackendState,
    id: String,
    suffix: &str,
    message: &str,
) -> Result<String, String> {
    let id = checked_id(&id, message)?;
    let info = state.info().ok_or("Backend chưa sẵn sàng")?;
    let base_url = info
        .get("baseUrl")
        .and_then(Value::as_str)
        .ok_or("Backend URL is unavailable")?;
    Ok(format!(
        "{base_url}{suffix}{id}?access_token={}",
        state.token
    ))
}

#[tauri::command]
fn local_media_url(
    project_id: String,
    state: tauri::State<'_, BackendState>,
) -> Result<String, String> {
    media_url(
        &state,
        project_id,
        "/api/video-ai/projects/",
        "Mã dự án video xem trước không hợp lệ",
    )
    .map(|url| url.replace("?access_token", "/media?access_token"))
}

#[tauri::command]
fn local_dubbing_url(
    project_id: String,
    state: tauri::State<'_, BackendState>,
) -> Result<String, String> {
    media_url(
        &state,
        project_id,
        "/api/video-ai/projects/",
        "Mã dự án lồng tiếng không hợp lệ",
    )
    .map(|url| url.replace("?access_token", "/dubbing-audio?access_token"))
}

#[tauri::command]
fn local_tts_preview_url(
    preview_id: String,
    state: tauri::State<'_, BackendState>,
) -> Result<String, String> {
    media_url(
        &state,
        preview_id,
        "/api/video-ai/tts/previews/",
        "Mã bản nghe thử giọng đọc không hợp lệ",
    )
}

#[tauri::command]
fn open_log_window(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("logs") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }
    let url = if cfg!(debug_assertions) {
        WebviewUrl::External(
            "http://127.0.0.1:5173/logs.html"
                .parse()
                .map_err(|error| format!("URL cửa sổ logs không hợp lệ: {error}"))?,
        )
    } else {
        WebviewUrl::App("logs.html".into())
    };
    WebviewWindowBuilder::new(&app, "logs", url)
        .title("Dyna Logs")
        .inner_size(1120.0, 720.0)
        .min_inner_size(760.0, 480.0)
        .visible(true)
        .build()
        .map_err(|error| error.to_string())?;
    Ok(())
}

fn start_selection_focus_watcher(app: tauri::AppHandle, state: BackendState) {
    tauri::async_runtime::spawn(async move {
        while !state.quitting.load(Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_secs(1)).await;
            let Some(info) = state.info() else {
                continue;
            };
            let request = ApiRequest {
                path: "/api/publisher/douyin-selections/active".into(),
                method: None,
                body: None,
                timeout_ms: Some(5_000),
            };
            let Ok(result) = send_backend_request(&request, &info, &state).await else {
                continue;
            };
            let Some(session) = result.get("session") else {
                continue;
            };
            let session_id = session.get("id").and_then(Value::as_str).unwrap_or("");
            let should_focus = !session_id.is_empty()
                && session.get("status").and_then(Value::as_str) == Some("ready")
                && session
                    .get("focus_requested")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
            if !should_focus {
                continue;
            }
            let is_new = if let Ok(mut inner) = state.inner.lock() {
                if inner.last_focused_selection_id == session_id {
                    false
                } else {
                    inner.last_focused_selection_id = session_id.into();
                    true
                }
            } else {
                false
            };
            if is_new {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
                let _ = app.emit("dyna:navigate", "publish");
            }
        }
    });
}

fn graceful_shutdown(state: &BackendState) {
    state.quitting.store(true, Ordering::SeqCst);
    state.restart_scheduled.store(false, Ordering::SeqCst);
    if let Some(info) = state.info() {
        if let Some(base_url) = info.get("baseUrl").and_then(Value::as_str) {
            if let Ok(client) = reqwest::blocking::Client::builder()
                .timeout(Duration::from_secs(10))
                .build()
            {
                let _ = client
                    .post(format!("{base_url}/api/runtime/shutdown"))
                    .header("X-Dyna-Token", &state.token)
                    .send();
            }
        }
    }
    state.stop_child();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = BackendState::new();
    let single_instance_state = state.clone();
    let shutdown_state = state.clone();
    tauri::Builder::default()
        // This plugin must be registered first so the second process exits before setup.
        .plugin(tauri_plugin_single_instance::init(
            move |app, _args, _cwd| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
                if !single_instance_state.child_is_running() {
                    if let Err(error) = start_backend(app.clone(), single_instance_state.clone()) {
                        let _ = app.emit(
                            "dyna:backend-status",
                            json!({ "state": "failed", "error": error }),
                        );
                    }
                }
            },
        ))
        .manage(state.clone())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            backend_info,
            dyna_api,
            local_media_url,
            local_dubbing_url,
            local_tts_preview_url,
            open_log_window
        ])
        .setup(move |app| {
            if let Err(error) = start_backend(app.handle().clone(), state.clone()) {
                let _ = app.emit(
                    "dyna:backend-status",
                    json!({ "state": "failed", "error": error }),
                );
                schedule_backend_restart(
                    app.handle().clone(),
                    state.clone(),
                    Duration::from_secs(2),
                );
            }
            start_selection_focus_watcher(app.handle().clone(), state.clone());
            if let Some(window) = app.get_webview_window("main") {
                window.show()?;
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Tauri application")
        .run(move |app, event| match event {
            tauri::RunEvent::ExitRequested { .. } => {
                if !shutdown_state.shutdown_started.swap(true, Ordering::SeqCst) {
                    graceful_shutdown(&shutdown_state);
                    // The single-instance plugin owns a hidden Windows message window,
                    // so closing the last webview does not always stop the event loop.
                    app.cleanup_before_exit();
                    std::process::exit(0);
                }
            }
            tauri::RunEvent::Exit => shutdown_state.stop_child(),
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_expected_api_requests() {
        let valid = ApiRequest {
            path: "/api/health".into(),
            method: Some("GET".into()),
            body: None,
            timeout_ms: Some(1),
        };
        let (_, timeout) = validate_request(&valid).unwrap();
        assert_eq!(timeout, Duration::from_secs(5));

        let invalid_path = ApiRequest {
            path: "https://example.com".into(),
            ..valid.clone()
        };
        assert!(validate_request(&invalid_path).is_err());

        let invalid_method = ApiRequest {
            method: Some("PATCH".into()),
            ..valid
        };
        assert!(validate_request(&invalid_method).is_err());
    }

    #[test]
    fn validates_media_ids() {
        assert!(checked_id(&"a".repeat(32), "bad").is_ok());
        assert!(checked_id(&"g".repeat(32), "bad").is_err());
        assert!(checked_id("abc", "bad").is_err());
    }
}
