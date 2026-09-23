use std::path::Path;
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Manager, RunEvent, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

struct DesktopState {
    port: AtomicU16,
    token: String,
    workspace: String,
    child: Mutex<Option<CommandChild>>,
}

#[derive(Serialize)]
struct DesktopConnection {
    port: u16,
    token: String,
    workspace: String,
}

#[tauri::command]
fn desktop_connection(state: State<'_, DesktopState>) -> Result<DesktopConnection, String> {
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        let port = state.port.load(Ordering::Acquire);
        if port != 0 {
            return Ok(DesktopConnection {
                port,
                token: state.token.clone(),
                workspace: state.workspace.clone(),
            });
        }
        thread::sleep(Duration::from_millis(50));
    }
    Err("Nemo Server did not become ready".into())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![desktop_connection])
        .setup(|app| {
            let token = uuid::Uuid::new_v4().simple().to_string();
            let workspace = std::env::var("HOME")
                .ok()
                .filter(|home| Path::new(home).is_dir())
                .unwrap_or_else(|| "/".into());
            let command = app
                .shell()
                .sidecar("nemo-server")?
                .env("NEMO_DESKTOP_TOKEN", &token);
            let (mut receiver, child) = command.spawn()?;
            app.manage(DesktopState {
                port: AtomicU16::new(0),
                token,
                workspace,
                child: Mutex::new(Some(child)),
            });

            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let mut buffer = String::new();
                while let Some(event) = receiver.recv().await {
                    if let CommandEvent::Stdout(bytes) = event {
                        buffer.push_str(&String::from_utf8_lossy(&bytes));
                        while let Some(end) = buffer.find('\n') {
                            let line: String = buffer.drain(..=end).collect();
                            if let Some(value) = line.trim().strip_prefix("NEMO_READY:") {
                                if let Ok(port) = value.parse::<u16>() {
                                    handle.state::<DesktopState>().port.store(port, Ordering::Release);
                                }
                            }
                        }
                    }
                }
                handle.state::<DesktopState>().port.store(0, Ordering::Release);
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Nemo desktop application");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let state = handle.state::<DesktopState>();
            if let Ok(mut child) = state.child.lock() {
                if let Some(child) = child.take() {
                    let _ = child.kill();
                }
            };
        }
    });
}
