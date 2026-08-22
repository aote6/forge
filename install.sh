#!/usr/bin/env bash
# Forge 安装 / 环境检查
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Forge install"
echo "project: $ROOT"

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
  echo "INFO: veritasd not found — install/start still OK."
  echo "  Without veritasd: read / search / plan / shell work; transactional write path unavailable."
  echo "  Optional later: build/install veritasd for Intent→transaction writes."
  if [ -d "$HOME/veritas_kernel" ]; then
    echo "    cd ~/veritas_kernel && cargo build --release"
  elif [ -d "$HOME/veritas" ]; then
    echo "    cd ~/veritas && cargo build --release"
  else
    echo "    see https://github.com/aote6/veritas"
  fi
else
  echo "OK veritasd: $VERITASD"
fi

python3 -m pip install -q pytest 2>/dev/null || true

echo "==> running tests"
python3 -m pytest -q || {
  echo "WARN: some tests failed (offline veritasd may fail write-path tests; install still OK)"
}

BIN="$ROOT/bin"
mkdir -p "$BIN"
cat > "$BIN/forge" << LAUNCH
#!/usr/bin/env bash
exec python3 "$ROOT/dp.py" "\$@"
LAUNCH
chmod +x "$BIN/forge" "$ROOT/dp.py" 2>/dev/null || true
echo "OK launcher: $BIN/forge"
echo "Add to PATH: export PATH=\"$BIN:\$PATH\""
echo "Done. Set an AI key, then: python3 dp.py"
