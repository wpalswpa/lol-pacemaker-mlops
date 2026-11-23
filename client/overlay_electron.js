// 게임 중 투명 오버레이 셸 (AGENTS.md 3-4절). client/bridge.py --launch-popup 이 Electron 런타임으로 실행한다.
// 사용: electron overlay_electron.js <URL> [폭] [높이]
// Windows 와 macOS 모두에서 창 틀(frame: false)과 배경(transparent: true)을 없애고
// 위젯 카드(.hud-card)만 게임 화면 위에 띄우며, 카드 밖 투명 영역 클릭은 게임으로 통과시킨다.
"use strict";

const { app, BrowserWindow, screen } = require("electron");

const args = process.argv.slice(2).filter((a) => !a.startsWith("-"));
const targetUrl = args[0] || "";
const width = Math.max(240, parseInt(args[1] || "380", 10) || 380);
const height = Math.max(240, parseInt(args[2] || "560", 10) || 560);

if (!/^https?:\/\//i.test(targetUrl)) {
  process.stderr.write("사용: electron overlay_electron.js <http(s) URL> [폭] [높이]\n");
  process.exit(2);
}

app.commandLine.appendSwitch("disable-background-timer-throttling");
app.commandLine.appendSwitch("disable-renderer-backgrounding");
app.commandLine.appendSwitch("disable-backgrounding-occluded-windows");

let win = null;

function createOverlayWindow() {
  const primary = screen.getPrimaryDisplay();
  const area = (primary && primary.workArea) || { x: 0, y: 0, width: 1920, height: 1080 };
  const x = Math.max(area.x + 12, area.x + area.width - width - 18);
  const y = Math.max(area.y + 12, area.y + 56);

  win = new BrowserWindow({
    width,
    height,
    x,
    y,
    title: "LoL 페이스메이커",
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });

  win.setMenuBarVisibility(false);
  win.setAlwaysOnTop(true, "screen-saver", 1);
  try {
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  } catch (_) {}

  win.webContents.on("console-message", (_event, _level, message) => {
    if (!win || win.isDestroyed()) return;
    if (message === "__HUD_PASSTHROUGH__:1") {
      win.setIgnoreMouseEvents(true, { forward: true });
    } else if (message === "__HUD_PASSTHROUGH__:0") {
      win.setIgnoreMouseEvents(false);
    } else if (typeof message === "string" && message.startsWith("__HUD_CMD__:")) {
      try {
        const cmd = JSON.parse(message.slice("__HUD_CMD__:".length));
        if (cmd.close) {
          win.close();
          return;
        }
        if (typeof cmd.dx === "number" || typeof cmd.dy === "number") {
          const pos = win.getPosition();
          win.setPosition(pos[0] + Math.round(cmd.dx || 0), pos[1] + Math.round(cmd.dy || 0));
        }
        if (typeof cmd.height === "number") {
          const targetH = Math.max(120, Math.min(920, Math.round(cmd.height)));
          const b = win.getBounds();
          if (Math.abs(b.height - targetH) >= 2) {
            win.setBounds({ x: b.x, y: b.y, width: b.width, height: targetH });
          }
        }
      } catch (_) {}
    }
  });

  win.on("closed", () => {
    win = null;
    app.quit();
  });

  win.loadURL(targetUrl);
}

app.whenReady().then(createOverlayWindow);
app.on("window-all-closed", () => app.quit());
