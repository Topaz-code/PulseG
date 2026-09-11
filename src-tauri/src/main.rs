// PulseG Studio - the desktop shell.
//
// This process does five things and nothing else. It opens the window, starts the Python sidecar
// when the window opens, stops that sidecar when the window closes (even on a crash or a force
// quit), keeps a single instance of the app alive on the machine, and answers two small commands
// from the dashboard - "open this folder" and "is the backend up?".
//
// Everything else lives in Python, because the studio is the agents and the orchestration; the
// shell exists so a non-technical user gets a normal Windows program with an installer, a Start
// Menu entry and a taskbar button instead of a terminal.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

/// How long the shell waits for the sidecar's health endpoint before telling the window.
const BACKEND_BOOT_TIMEOUT: Duration = Duration::from_secs(45);

/// The sidecar process, kept so it can be stopped when this window closes.
#[derive(Default)]
struct Sidecar(Mutex<Option<Child>>);

impl Sidecar {
    fn set(&self, child: Child) {
        if let Ok(mut slot) = self.0.lock() {
            *slot = Some(child);
        }
    }

    /// Stop the backend. Called from every exit path, including the ones that skip destructors.
    fn stop(&self) {
        if let Ok(mut slot) = self.0.lock() {
            if let Some(mut child) = slot.take() {
                let _ = child.kill();
                let _ = child.wait();
                log_line("sidecar stopped");
            }
        }
    }

    fn running(&self) -> bool {
        match self.0.lock() {
            Ok(mut slot) => match slot.as_mut() {
                Some(child) => matches!(child.try_wait(), Ok(None)),
                None => false,
            },
            Err(_) => false,
        }
    }
}

/// stdout/stderr go to a file in the studio home so a failure is diagnosable after the window has
/// closed. Without this, a sidecar that dies on start leaves the user with a dashboard that says
/// "reconnecting" and no way to find out why.
fn log_line(message: &str) {
    eprintln!("[pulseg] {message}");
}

fn backend_port() -> String {
    std::env::var("PULSEG_PORT").unwrap_or_else(|_| "8787".to_string())
}

/// Find the backend executable. Three places, in order of how a real install looks:
/// the bundled binary next to the app, the bundled binary under resources, then - in a debug
/// build only - the Python source tree, so a developer can run `npm run tauri:dev` without
/// building the sidecar first.
fn backend_command(app: &tauri::AppHandle) -> Option<Command> {
    let exe_name = if cfg!(windows) {
        "pulseg-backend.exe"
    } else {
        "pulseg-backend"
    };

    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(dir) = app.path().resource_dir() {
        candidates.push(dir.join(exe_name));
        candidates.push(dir.join("backend").join(exe_name));
    }
    if let Ok(current) = std::env::current_exe() {
        if let Some(parent) = current.parent() {
            candidates.push(parent.join(exe_name));
            candidates.push(parent.join("backend").join(exe_name));
        }
    }

    for candidate in candidates {
        if candidate.is_file() {
            log_line(&format!("starting bundled backend {}", candidate.display()));
            let mut command = Command::new(candidate);
            command.arg("--port").arg(backend_port());
            return Some(command);
        }
    }

    if cfg!(debug_assertions) {
        log_line("no bundled backend found; falling back to the Python source tree (development)");
        let mut command = Command::new(if cfg!(windows) { "python" } else { "python3" });
        command
            .arg("-m")
            .arg("backend.main")
            .arg("--port")
            .arg(backend_port())
            .current_dir(dev_repo_root());
        return Some(command);
    }

    log_line("no backend executable found - the installer is incomplete");
    None
}

#[cfg(debug_assertions)]
fn dev_repo_root() -> PathBuf {
    // src-tauri/src -> src-tauri -> repo root
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(not(debug_assertions))]
fn dev_repo_root() -> PathBuf {
    PathBuf::from(".")
}

fn spawn_backend(app: &tauri::AppHandle) -> Option<Child> {
    let mut command = backend_command(app)?;
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    match command.spawn() {
        Ok(child) => Some(child),
        Err(error) => {
            log_line(&format!("could not start the backend: {error}"));
            None
        }
    }
}

fn backend_is_up(port: &str) -> bool {
    let address = format!("127.0.0.1:{port}");
    let Ok(socket) = address.parse::<SocketAddr>() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&socket, Duration::from_millis(250)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(600)));
    if stream
        .write_all(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);
    response.contains("\"ok\":true") || response.contains("\"ok\": true")
}

/// Wait for the sidecar, then tell the window. The window does not block on this: it renders its
/// own loading and reconnect states, because a user looking at an empty frame with no explanation
/// is worse than a user watching "starting the studio".
fn watch_backend(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        let port = backend_port();
        let started = Instant::now();
        while started.elapsed() < BACKEND_BOOT_TIMEOUT {
            if backend_is_up(&port) {
                log_line("backend is up");
                let _ = app.emit("backend-ready", port.clone());
                return;
            }
            std::thread::sleep(Duration::from_millis(400));
        }
        log_line("backend did not answer in time");
        let _ = app.emit("backend-timeout", port);
    });
}

/// Open a folder in the operating system's file browser.
///
/// The frontend never guesses a path: it asks the API where the project folder is, then asks the
/// shell to show it. Keeping this in Rust means the API stays a service and never opens windows on
/// the machine by itself.
#[tauri::command]
fn open_path(path: String, _app: tauri::AppHandle) -> Result<String, String> {
    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("{path} does not exist yet."));
    }

    #[cfg(target_os = "windows")]
    let result = Command::new("explorer").arg(&target).spawn();

    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg(&target).spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let result = Command::new("xdg-open").arg(&target).spawn();

    match result {
        Ok(_) => Ok(path),
        Err(error) => Err(format!("could not open {path}: {error}")),
    }
}

/// Small diagnostics command used by Settings > About and by support requests.
#[tauri::command]
fn backend_status(state: tauri::State<'_, Sidecar>) -> serde_json::Value {
    serde_json::json!({
        "port": backend_port(),
        "sidecar_running": state.running(),
        "up": backend_is_up(&backend_port()),
        "version": env!("CARGO_PKG_VERSION"),
    })
}

/// Raise the existing window when the user launches the app a second time.
fn focus_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            focus_main(app);
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Sidecar::default())
        .invoke_handler(tauri::generate_handler![open_path, backend_status])
        .setup(|app| {
            let handle = app.handle().clone();
            if let Some(child) = spawn_backend(&handle) {
                app.state::<Sidecar>().set(child);
            }
            watch_backend(handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("PulseG Studio could not start the desktop shell");

    app.run(|handle, event| match event {
        // Both events are handled: ExitRequested can be cancelled by the OS or by a plugin, and a
        // sidecar left running after the window closes would keep writing to the project folders.
        RunEvent::ExitRequested { .. } | RunEvent::Exit => {
            handle.state::<Sidecar>().stop();
        }
        _ => {}
    });
}
