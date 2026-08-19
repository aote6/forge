#!/usr/bin/env bash
# Forge 一键安装 / 环境检查
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Forge install"
echo "project: $ROOT"

# Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found"
  exit 1
fi
PYVER=$(python3 -c 'import sys; print("%d.%d"%sys.version_info[:2])')
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || {
  echo "ERROR: need Python >= 3.10 (got $PYVER)"
  exit 1
}
echo "OK python $PYVER"

# veritasd
VERITASD=""
for cand in \
  "$HOME/veritas_kernel/target/release/veritasd" \
  "$HOME/veritas/target/release/veritasd" \
  "$(command -v veritasd 2>/dev/null || true)" \
  "/usr/local/bin/veritasd"
do
  if [ -n "$cand" ] && [ -x "$cand" ]; then
    VERITASD="$cand"
    break
  fi
done

if [ -z "$VERITASD" ]; then
  echo "WARN: veritasd binary not found"
  if [ -d "$HOME/veritas_kernel" ]; then
    echo "  Found source at ~/veritas_kernel — build with:"
    echo "    cd ~/veritas_kernel && cargo build --release"
  elif [ -d "$HOME/veritas" ]; then
    echo "  Found source at ~/veritas — build with:"
    echo "    cd ~/veritas && cargo build --release"
  else
    echo "  Clone/build Veritas (https://github.com/aote6/veritas) then re-run."
  fi
else
  echo "OK veritasd: $VERITASD"
fi

# deps (stdlib-heavy; pytest for tests)
python3 -m pip install -q pytest 2>/dev/null || true

echo "==> running tests"
python3 -m pytest -q || {
  echo "WARN: some tests failed (veritasd offline may cause skips)"
}

# convenience launcher
BIN="$ROOT/bin"
mkdir -p "$BIN"
cat > "$BIN/forge" << LAUNCH
#!/usr/bin/env bash
exec python3 "$ROOT/dp.py" "\$@"
LAUNCH
chmod +x "$BIN/forge" "$ROOT/dp.py" 2>/dev/null || true
echo "OK launcher: $BIN/forge"
echo "Add to PATH: export PATH=\"$BIN:\$PATH\""
echo "Done."
