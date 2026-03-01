"""Desktop framework configurations for Electron and Tauri.

Each config is registered at module load time via ``register_desktop()``.
Frameworks cover cross-platform desktop application development with
web-based frontends and native backend capabilities.
"""

from app.agents.desktop_configs import DesktopConfig, register_desktop

# ---------------------------------------------------------------------------
# 1. Electron -- TypeScript / JavaScript + React
# ---------------------------------------------------------------------------

ELECTRON = DesktopConfig(
    name="electron",
    display_name="Electron",
    language="typescript",
    code_block_lang="typescript",
    backend_language="javascript",
    frontend_framework="react",
    file_structure={
        "main_process": "src/main/main.ts",
        "preload": "src/preload/preload.ts",
        "renderer": "src/renderer/App.tsx",
        "ipc_handler": "src/main/ipc-handlers.ts",
        "window_manager": "src/main/window-manager.ts",
        "tray": "src/main/tray.ts",
        "updater": "src/main/updater.ts",
        "shared_types": "src/shared/types.ts",
        "electron_builder": "electron-builder.yml",
        "package_json": "package.json",
    },
    rules=(
        # 1
        "Strictly separate Main process and Renderer process code. The Main "
        "process runs in Node.js with full OS access while the Renderer process "
        "runs in a Chromium sandbox. Never import Node.js modules directly in "
        "renderer code.",
        # 2
        "Always use contextBridge.exposeInMainWorld() in the preload script to "
        "expose a typed API object to the renderer. Define the API shape in a "
        "shared TypeScript interface so both sides have compile-time safety.",
        # 3
        "Use ipcMain.handle() and ipcRenderer.invoke() for request/response "
        "IPC patterns. Use ipcMain.on() and webContents.send() only for "
        "one-way push notifications from main to renderer.",
        # 4
        "Configure electron-builder in electron-builder.yml for packaging. "
        "Define platform targets (nsis for Windows, dmg for macOS, AppImage "
        "for Linux) and set the appId, productName, and publish configuration.",
        # 5
        "Integrate electron-updater for auto-updates by configuring a publish "
        "provider (GitHub Releases, S3, or generic server). Call "
        "autoUpdater.checkForUpdatesAndNotify() on app ready and handle "
        "update-downloaded to prompt the user.",
        # 6
        "Configure BrowserWindow with secure defaults: set nodeIntegration to "
        "false, contextIsolation to true, sandbox to true, and webSecurity to "
        "true. Always specify a preload script path using path.join(__dirname).",
        # 7
        "Never set nodeIntegration to true or contextIsolation to false in "
        "production builds. These settings expose the full Node.js API to the "
        "renderer and enable trivial remote code execution from XSS.",
        # 8
        "Set a strict Content-Security-Policy header on all BrowserWindows "
        "using session.defaultSession.webRequest.onHeadersReceived(). At "
        "minimum set default-src to 'self' and script-src to 'self'.",
        # 9
        "Use the Menu and Tray APIs to build native application menus and "
        "system tray icons. Build menus from a template array using "
        "Menu.buildFromTemplate() and set the application menu with "
        "Menu.setApplicationMenu().",
        # 10
        "Validate all IPC channel names and message payloads in the main "
        "process before acting on them. Use a whitelist of allowed channels "
        "and validate data shapes with zod or a similar runtime schema.",
        # 11
        "NEVER invent or hallucinate import paths, module names, or API "
        "methods. Only use imports and APIs that exist in the electron, "
        "electron-updater, and electron-builder packages. If unsure about "
        "an API, omit it rather than guess.",
        # 12
        "Output ONLY valid, runnable code. Do not include placeholder "
        "comments like '// TODO' or '// implement this'. Every function "
        "body must contain complete, working logic.",
    ),
    golden_examples={
        "main_process": (
            'import { app, BrowserWindow, session } from "electron";\n'
            'import path from "node:path";\n'
            'import { registerIpcHandlers } from "./ipc-handlers";\n'
            "\n"
            "let mainWindow: BrowserWindow | null = null;\n"
            "\n"
            "function createWindow(): void {\n"
            "  mainWindow = new BrowserWindow({\n"
            "    width: 1200,\n"
            "    height: 800,\n"
            "    webPreferences: {\n"
            "      preload: path.join(__dirname, '../preload/preload.js'),\n"
            "      nodeIntegration: false,\n"
            "      contextIsolation: true,\n"
            "      sandbox: true,\n"
            "    },\n"
            "  });\n"
            "\n"
            '  session.defaultSession.webRequest.onHeadersReceived(\n'
            "    (details, callback) => {\n"
            "      callback({\n"
            "        responseHeaders: {\n"
            "          ...details.responseHeaders,\n"
            '          "Content-Security-Policy": [\n'
            "            \"default-src 'self'; script-src 'self'\",\n"
            "          ],\n"
            "        },\n"
            "      });\n"
            "    },\n"
            "  );\n"
            "\n"
            "  if (process.env.NODE_ENV === 'development') {\n"
            "    mainWindow.loadURL('http://localhost:5173');\n"
            "  } else {\n"
            "    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));\n"
            "  }\n"
            "}\n"
            "\n"
            "app.whenReady().then(() => {\n"
            "  registerIpcHandlers();\n"
            "  createWindow();\n"
            "});\n"
            "\n"
            'app.on("window-all-closed", () => {\n'
            "  if (process.platform !== 'darwin') app.quit();\n"
            "});"
        ),
        "preload": (
            'import { contextBridge, ipcRenderer } from "electron";\n'
            "\n"
            "const electronAPI = {\n"
            "  readFile: (filePath: string): Promise<string> =>\n"
            "    ipcRenderer.invoke('file:read', filePath),\n"
            "\n"
            "  writeFile: (filePath: string, content: string): Promise<void> =>\n"
            "    ipcRenderer.invoke('file:write', filePath, content),\n"
            "\n"
            "  onUpdateAvailable: (callback: (info: { version: string }) => void) => {\n"
            "    const handler = (_event: Electron.IpcRendererEvent, info: { version: string }) =>\n"
            "      callback(info);\n"
            "    ipcRenderer.on('update:available', handler);\n"
            "    return () => ipcRenderer.removeListener('update:available', handler);\n"
            "  },\n"
            "\n"
            "  getAppVersion: (): Promise<string> =>\n"
            "    ipcRenderer.invoke('app:version'),\n"
            "} as const;\n"
            "\n"
            "contextBridge.exposeInMainWorld('electronAPI', electronAPI);\n"
            "\n"
            "export type ElectronAPI = typeof electronAPI;"
        ),
        "renderer": (
            'import { useState, useEffect } from "react";\n'
            "\n"
            "declare global {\n"
            "  interface Window {\n"
            "    electronAPI: {\n"
            "      readFile: (filePath: string) => Promise<string>;\n"
            "      writeFile: (filePath: string, content: string) => Promise<void>;\n"
            "      onUpdateAvailable: (cb: (info: { version: string }) => void) => () => void;\n"
            "      getAppVersion: () => Promise<string>;\n"
            "    };\n"
            "  }\n"
            "}\n"
            "\n"
            "export function App() {\n"
            '  const [version, setVersion] = useState<string>("");\n'
            '  const [content, setContent] = useState<string>("");\n'
            "\n"
            "  useEffect(() => {\n"
            "    window.electronAPI.getAppVersion().then(setVersion);\n"
            "    const unsubscribe = window.electronAPI.onUpdateAvailable((info) => {\n"
            "      alert(`Update available: v${info.version}`);\n"
            "    });\n"
            "    return unsubscribe;\n"
            "  }, []);\n"
            "\n"
            "  const handleOpen = async () => {\n"
            "    const text = await window.electronAPI.readFile('/path/to/file.txt');\n"
            "    setContent(text);\n"
            "  };\n"
            "\n"
            "  return (\n"
            "    <div>\n"
            "      <h1>My App v{version}</h1>\n"
            '      <button onClick={handleOpen}>Open File</button>\n'
            "      <pre>{content}</pre>\n"
            "    </div>\n"
            "  );\n"
            "}"
        ),
        "ipc_handler": (
            'import { ipcMain, app } from "electron";\n'
            'import fs from "node:fs/promises";\n'
            'import path from "node:path";\n'
            "\n"
            "const ALLOWED_EXTENSIONS = new Set(['.txt', '.md', '.json', '.csv']);\n"
            "\n"
            "function validateFilePath(filePath: string): void {\n"
            "  const ext = path.extname(filePath).toLowerCase();\n"
            "  if (!ALLOWED_EXTENSIONS.has(ext)) {\n"
            "    throw new Error(`File type '${ext}' is not allowed`);\n"
            "  }\n"
            "}\n"
            "\n"
            "export function registerIpcHandlers(): void {\n"
            "  ipcMain.handle('file:read', async (_event, filePath: string) => {\n"
            "    validateFilePath(filePath);\n"
            "    return fs.readFile(filePath, 'utf-8');\n"
            "  });\n"
            "\n"
            "  ipcMain.handle('file:write', async (_event, filePath: string, content: string) => {\n"
            "    validateFilePath(filePath);\n"
            "    await fs.writeFile(filePath, content, 'utf-8');\n"
            "  });\n"
            "\n"
            "  ipcMain.handle('app:version', () => {\n"
            "    return app.getVersion();\n"
            "  });\n"
            "}"
        ),
    },
    supports_auto_update=True,
    supports_system_tray=True,
    package_manager="npm",
    platforms=("windows", "macos", "linux"),
)

# ---------------------------------------------------------------------------
# 2. Tauri v2 -- Rust + Any frontend
# ---------------------------------------------------------------------------

TAURI = DesktopConfig(
    name="tauri",
    display_name="Tauri",
    language="rust",
    code_block_lang="rust",
    backend_language="rust",
    frontend_framework="any",
    file_structure={
        "command": "src-tauri/src/commands.rs",
        "state": "src-tauri/src/state.rs",
        "main": "src-tauri/src/main.rs",
        "lib": "src-tauri/src/lib.rs",
        "frontend_invoke": "src/App.tsx",
        "config": "src-tauri/tauri.conf.json",
        "cargo_toml": "src-tauri/Cargo.toml",
        "capabilities": "src-tauri/capabilities/default.json",
        "frontend_root": "src/",
    },
    rules=(
        # 1
        "Define backend functions as Tauri commands using the #[tauri::command] "
        "attribute macro. Commands receive typed parameters and return "
        "Result<T, String> for error handling. Register all commands in the "
        "tauri::Builder via .invoke_handler(tauri::generate_handler![...]).",
        # 2
        "Use tauri::State<T> to share application state across commands. Wrap "
        "mutable state in Mutex<T> or RwLock<T> and register it with "
        ".manage(AppState::default()) on the Builder. Access state in commands "
        "by adding state: tauri::State<'_, AppState> as a parameter.",
        # 3
        "Use the Tauri event system for push notifications from backend to "
        "frontend. Emit events with app_handle.emit(\"event-name\", payload) "
        "and listen in the frontend with listen(\"event-name\", callback). "
        "Use typed payloads with #[derive(Serialize, Clone)].",
        # 4
        "Configure allowed APIs and permissions in src-tauri/capabilities/ "
        "JSON files. Tauri v2 uses a capability-based permission system where "
        "each plugin and core API must be explicitly allowed. Never use "
        "allow-all patterns in production.",
        # 5
        "Call Tauri commands from the frontend using the invoke() function "
        "from @tauri-apps/api/core. Pass arguments as a single object: "
        "invoke('command_name', { arg1: value1, arg2: value2 }). The function "
        "returns a Promise that resolves with the command's return value.",
        # 6
        "Scope file system access using the fs plugin's scope configuration "
        "in capabilities. Restrict access to specific directories like "
        "$APPDATA, $DOCUMENT, or $DOWNLOAD. Never grant blanket access to "
        "the entire file system.",
        # 7
        "Derive Serialize and Deserialize from serde on all structs that cross "
        "the IPC boundary. Tauri uses serde_json for automatic serialization "
        "between Rust and JavaScript. Use #[serde(rename_all = \"camelCase\")] "
        "to match JavaScript naming conventions.",
        # 8
        "Use async commands for I/O-bound operations by marking them with "
        "async fn. Tauri runs async commands on a separate thread pool so "
        "they do not block the main thread. Return Result<T, String> and "
        "use .map_err(|e| e.to_string())? for error propagation.",
        # 9
        "Configure the main window in tauri.conf.json under the windows "
        "array. Set title, width, height, resizable, fullscreen, and "
        "decorations. Use the url field to point to the frontend dev server "
        "in development or the built frontend in production.",
        # 10
        "Use the Tauri plugin system for extended functionality. Install "
        "plugins like tauri-plugin-fs, tauri-plugin-dialog, "
        "tauri-plugin-shell via Cargo.toml and register them with "
        ".plugin(tauri_plugin_fs::init()) on the Builder.",
        # 11
        "NEVER invent or hallucinate import paths, crate names, or API "
        "methods. Only use imports from tauri, serde, serde_json, tokio, "
        "and official tauri-plugin-* crates. If unsure about an API, omit "
        "it rather than guess.",
        # 12
        "Output ONLY valid, compilable Rust code. Do not include placeholder "
        "comments like '// TODO' or 'unimplemented!()'. Every function body "
        "must contain complete, working logic that compiles without errors.",
    ),
    golden_examples={
        "command": (
            "use serde::{Deserialize, Serialize};\n"
            "use std::fs;\n"
            "use std::path::PathBuf;\n"
            "use tauri::State;\n"
            "\n"
            "use crate::state::AppState;\n"
            "\n"
            "#[derive(Debug, Serialize, Deserialize)]\n"
            '#[serde(rename_all = "camelCase")]\n'
            "pub struct FileEntry {\n"
            "    pub name: String,\n"
            "    pub path: String,\n"
            "    pub is_directory: bool,\n"
            "    pub size_bytes: u64,\n"
            "}\n"
            "\n"
            "#[tauri::command]\n"
            "pub fn list_directory(dir_path: String) -> Result<Vec<FileEntry>, String> {\n"
            "    let path = PathBuf::from(&dir_path);\n"
            "    if !path.is_dir() {\n"
            '        return Err(format!("Not a directory: {}", dir_path));\n'
            "    }\n"
            "\n"
            "    let entries = fs::read_dir(&path)\n"
            '        .map_err(|e| format!("Failed to read directory: {}", e))?\n'
            "        .filter_map(|entry| entry.ok())\n"
            "        .map(|entry| {\n"
            "            let metadata = entry.metadata().unwrap_or_else(|_| {\n"
            "                fs::metadata(entry.path()).unwrap()\n"
            "            });\n"
            "            FileEntry {\n"
            "                name: entry.file_name().to_string_lossy().to_string(),\n"
            "                path: entry.path().to_string_lossy().to_string(),\n"
            "                is_directory: metadata.is_dir(),\n"
            "                size_bytes: metadata.len(),\n"
            "            }\n"
            "        })\n"
            "        .collect();\n"
            "\n"
            "    Ok(entries)\n"
            "}\n"
            "\n"
            "#[tauri::command]\n"
            "pub async fn read_file_contents(file_path: String) -> Result<String, String> {\n"
            "    tokio::fs::read_to_string(&file_path)\n"
            "        .await\n"
            '        .map_err(|e| format!("Failed to read file: {}", e))\n'
            "}"
        ),
        "state": (
            "use serde::{Deserialize, Serialize};\n"
            "use std::sync::Mutex;\n"
            "\n"
            "#[derive(Debug, Default, Serialize, Deserialize)]\n"
            "pub struct AppData {\n"
            "    pub recent_files: Vec<String>,\n"
            "    pub current_project: Option<String>,\n"
            "    pub window_title: String,\n"
            "}\n"
            "\n"
            "#[derive(Debug, Default)]\n"
            "pub struct AppState {\n"
            "    pub data: Mutex<AppData>,\n"
            "}\n"
            "\n"
            "#[tauri::command]\n"
            "pub fn get_recent_files(\n"
            "    state: tauri::State<'_, AppState>,\n"
            ") -> Result<Vec<String>, String> {\n"
            "    let data = state.data.lock()\n"
            '        .map_err(|e| format!("Lock poisoned: {}", e))?;\n'
            "    Ok(data.recent_files.clone())\n"
            "}\n"
            "\n"
            "#[tauri::command]\n"
            "pub fn add_recent_file(\n"
            "    file_path: String,\n"
            "    state: tauri::State<'_, AppState>,\n"
            ") -> Result<(), String> {\n"
            "    let mut data = state.data.lock()\n"
            '        .map_err(|e| format!("Lock poisoned: {}", e))?;\n'
            "    data.recent_files.retain(|f| f != &file_path);\n"
            "    data.recent_files.insert(0, file_path);\n"
            "    if data.recent_files.len() > 10 {\n"
            "        data.recent_files.truncate(10);\n"
            "    }\n"
            "    Ok(())\n"
            "}"
        ),
        "frontend_invoke": (
            'import { useState, useEffect } from "react";\n'
            'import { invoke } from "@tauri-apps/api/core";\n'
            'import { listen } from "@tauri-apps/api/event";\n'
            "\n"
            "interface FileEntry {\n"
            "  name: string;\n"
            "  path: string;\n"
            "  isDirectory: boolean;\n"
            "  sizeBytes: number;\n"
            "}\n"
            "\n"
            "export function App() {\n"
            "  const [files, setFiles] = useState<FileEntry[]>([]);\n"
            '  const [error, setError] = useState<string>("");\n'
            "\n"
            "  useEffect(() => {\n"
            '    const unlisten = listen<string>("file-changed", (event) => {\n'
            "      console.log('File changed:', event.payload);\n"
            "      loadFiles();\n"
            "    });\n"
            "    return () => { unlisten.then((fn) => fn()); };\n"
            "  }, []);\n"
            "\n"
            "  const loadFiles = async () => {\n"
            "    try {\n"
            "      const entries = await invoke<FileEntry[]>('list_directory', {\n"
            "        dirPath: '/home/user/documents',\n"
            "      });\n"
            "      setFiles(entries);\n"
            '      setError("");\n'
            "    } catch (err) {\n"
            "      setError(String(err));\n"
            "    }\n"
            "  };\n"
            "\n"
            "  return (\n"
            "    <div>\n"
            "      <h1>File Browser</h1>\n"
            '      <button onClick={loadFiles}>Load Files</button>\n'
            "      {error && <p style={{ color: 'red' }}>{error}</p>}\n"
            "      <ul>\n"
            "        {files.map((f) => (\n"
            "          <li key={f.path}>\n"
            '            {f.isDirectory ? "[DIR]" : "[FILE]"} {f.name} ({f.sizeBytes} bytes)\n'
            "          </li>\n"
            "        ))}\n"
            "      </ul>\n"
            "    </div>\n"
            "  );\n"
            "}"
        ),
        "config": (
            '{\n'
            '  "$schema": "https://raw.githubusercontent.com/tauri-apps/tauri/dev/crates/tauri-config-schema/schema.json",\n'
            '  "productName": "My Tauri App",\n'
            '  "version": "0.1.0",\n'
            '  "identifier": "com.myapp.desktop",\n'
            '  "build": {\n'
            '    "frontendDist": "../dist",\n'
            '    "devUrl": "http://localhost:5173",\n'
            '    "beforeDevCommand": "npm run dev",\n'
            '    "beforeBuildCommand": "npm run build"\n'
            '  },\n'
            '  "app": {\n'
            '    "windows": [\n'
            '      {\n'
            '        "title": "My Tauri App",\n'
            '        "width": 1200,\n'
            '        "height": 800,\n'
            '        "resizable": true,\n'
            '        "fullscreen": false\n'
            '      }\n'
            '    ],\n'
            '    "security": {\n'
            '      "csp": "default-src \'self\'; script-src \'self\'"\n'
            '    }\n'
            '  },\n'
            '  "bundle": {\n'
            '    "active": true,\n'
            '    "targets": "all",\n'
            '    "icon": [\n'
            '      "icons/32x32.png",\n'
            '      "icons/128x128.png",\n'
            '      "icons/128x128@2x.png",\n'
            '      "icons/icon.icns",\n'
            '      "icons/icon.ico"\n'
            '    ]\n'
            '  }\n'
            '}'
        ),
    },
    supports_auto_update=True,
    supports_system_tray=True,
    package_manager="cargo",
    platforms=("windows", "macos", "linux"),
)

# -- Register all configs -----------------------------------------------------

register_desktop(ELECTRON)
register_desktop(TAURI)
