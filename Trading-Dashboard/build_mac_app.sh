#!/bin/bash
# Assembles JarvisTrader.app -- a self-contained, double-clickable macOS app
# wrapping this dashboard. Safe to re-run any time after changing the
# dashboard's source files; it always rebuilds Resources/app from scratch
# from the canonical files below (server.py etc. stay the single source of
# truth -- nothing here is meant to be hand-edited inside the .app).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HERE/JarvisTrader.app"
RESOURCES_APP="$APP/Contents/Resources/app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RESOURCES_APP/static"

cp "$HERE/server.py" "$HERE/data_engine.py" "$HERE/providers.py" "$HERE/ai_analyst.py" "$RESOURCES_APP/"
cp -R "$HERE/static/." "$RESOURCES_APP/static/"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key><string>Jarvis Trader</string>
	<key>CFBundleDisplayName</key><string>Jarvis Trader</string>
	<key>CFBundleIdentifier</key><string>local.jarvistrader.app</string>
	<key>CFBundleVersion</key><string>1.0</string>
	<key>CFBundleShortVersionString</key><string>1.0</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleExecutable</key><string>JarvisTrader</string>
	<key>LSMinimumSystemVersion</key><string>10.13</string>
	<key>NSHighResolutionCapable</key><true/>
	<key>LSApplicationCategoryType</key><string>public.app-category.finance</string>
</dict>
</plist>
EOF

cat > "$APP/Contents/MacOS/JarvisTrader" <<'EOF'
#!/bin/bash
# Launcher: starts the dashboard's own server.py and opens the browser once
# it responds. Runs via `exec` so this process *is* the app as far as the
# Dock/Cmd+Q are concerned -- quitting sends SIGTERM to server.py, which
# shuts down cleanly (see the signal handler in server.py).
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../Resources/app" && pwd)"
cd "$DIR" || exit 1

PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
  osascript -e 'display alert "Python 3 mangler" message "Jarvis Trader kraever Python 3, som ikke blev fundet paa denne Mac. Installer det fra python.org og proev igen." as critical' >/dev/null 2>&1
  exit 1
fi

PORT="${PORT:-8000}"
export PORT

LOG_DIR="$HOME/Library/Logs"
mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="/tmp"
LOG_FILE="$LOG_DIR/JarvisTrader.log"

if curl -s -o /dev/null "http://localhost:$PORT/api/meta"; then
  # already running (e.g. started earlier from a terminal) -- just show it
  open "http://localhost:$PORT/"
  exit 0
fi

(
  for _ in $(seq 1 50); do
    sleep 0.2
    if curl -s -o /dev/null "http://localhost:$PORT/api/meta"; then
      open "http://localhost:$PORT/"
      break
    fi
  done
) &

exec "$PYTHON" server.py >>"$LOG_FILE" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/JarvisTrader"

echo "Built $APP"
