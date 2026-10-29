const { contextBridge, ipcRenderer } = require("electron");

const bridgeMethods = [
  "state",
  "get_provider_detail",
  "add_provider",
  "set_active_model",
  "remove_provider",
  "test_provider",
  "activate_target",
  "connect_target",
  "disconnect_target",
  "connect_claude",
  "disconnect_claude",
  "get_presets",
  "list_skills",
  "add_skill",
  "remove_skill",
  "list_journal",
  "journal_rollback",
];

const api = Object.fromEntries(
  bridgeMethods.map((method) => [
    method,
    (...args) => ipcRenderer.invoke("bridge:call", method, args),
  ]),
);

Object.assign(api, {
  minimize: () => ipcRenderer.invoke("bridge:window-control", "minimize"),
  resize_window: (width, height) => ipcRenderer.invoke("bridge:window-control", "resize", width, height),
  close_to_tray: () => ipcRenderer.invoke("bridge:window-control", "close"),
  show_codex_workspace: () => ipcRenderer.invoke("bridge:show-codex-workspace"),
});

contextBridge.exposeInMainWorld("pywebview", { api });

window.addEventListener("DOMContentLoaded", () => {
  // Match PyWebView's readiness event without racing the page's DOMContentLoaded handler.
  setTimeout(() => window.dispatchEvent(new Event("pywebviewready")), 0);
}, { once: true });
