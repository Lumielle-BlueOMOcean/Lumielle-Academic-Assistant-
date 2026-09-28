#!/bin/bash
# ============================================================
#  Lumielle Academic Assistant - MacOS one-click launcher (uv)
#  Double-click to prepare the runtime and start.
#  Python runtime bundled - no download needed (deps need internet).
# ============================================================

# 进入脚本所在目录（保证双击运行时路径正确）
cd "$(dirname "$0")" || exit 1

echo ""
echo "=============================================="
echo "  Lumielle Academic Assistant"
echo "  Preparing runtime environment..."
echo "=============================================="
echo ""

# ---------- 1. 定位 uv（包内优先，其次系统） ----------
UV_BIN=""
if [ -f "$(pwd)/.uv/uv" ]; then
    UV_BIN="$(pwd)/.uv/uv"
elif command -v uv &>/dev/null; then
    UV_BIN="$(command -v uv)"
else
    echo "[1/4] uv runtime not found, downloading (~30s)..."
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ]; then
        UV_ARCH="aarch64-apple-darwin"
    else
        UV_ARCH="x86_64-apple-darwin"
    fi
    mkdir -p "$(pwd)/.uv"
    curl -LsSf "https://github.com/astral-sh/uv/releases/latest/download/uv-${UV_ARCH}.tar.gz" | tar -xz -C "$(pwd)/.uv" || {
        echo ""
        echo "❌ Failed to download uv. Check network and retry."
        echo "   Download: https://github.com/astral-sh/uv/releases"
        echo ""
        read -n 1 -s -r -p "按任意键退出..."
        exit 1
    }
    UV_BIN="$(pwd)/.uv/uv"
fi
echo "[1/4] uv 运行时: $($UV_BIN --version 2>&1)"
echo ""

# ---------- 2. 准备 Python（已内置；架构不符时自动下载） ----------
export UV_PYTHON_INSTALL_DIR="$(pwd)/.python"
export UV_PYTHON_DOWNLOADS="manual"
# 镜像加速（国内下载快）；失败自动回退官方源
export UV_PYTHON_INSTALL_MIRROR="https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download"

echo "[2/4] Checking Python runtime..."
PY_BIN=$(find "$(pwd)/.python" -type f -name "python3.12" -path "*/bin/*" 2>/dev/null | head -1)
if [ -z "$PY_BIN" ]; then
    PY_BIN=$(find "$(pwd)/.python" -name "python3" -path "*/bin/*" 2>/dev/null | head -1)
fi

if [ -z "$PY_BIN" ]; then
    echo "    No compatible Python found (different chip arch?), downloading (~1-3 min)..."
    rm -rf "$(pwd)/.python"
    mkdir -p "$(pwd)/.python"
    "$UV_BIN" python install 3.12 || {
        echo "    Mirror failed, trying official source..."
        unset UV_PYTHON_INSTALL_MIRROR
        "$UV_BIN" python install 3.12 || {
            echo ""
            echo "❌ Failed to download Python. Check network and retry."
            echo ""
            read -n 1 -s -r -p "按任意键退出..."
            exit 1
        }
    }
    PY_BIN=$(find "$(pwd)/.python" -type f -name "python3.12" -path "*/bin/*" 2>/dev/null | head -1)
    if [ -z "$PY_BIN" ]; then
        PY_BIN=$(find "$(pwd)/.python" -name "python3" -path "*/bin/*" 2>/dev/null | head -1)
    fi
fi
if [ -z "$PY_BIN" ]; then
    echo "❌ Python interpreter not found. Delete the .python folder and retry."
    read -n 1 -s -r -p "按任意键退出..."
    exit 1
fi
echo "    ✓ Python: $($PY_BIN --version 2>&1)"
echo ""

# ---------- 3. 创建虚拟环境 ----------
echo "[3/4] Checking virtual environment..."
if [ ! -f "$(pwd)/venv/bin/activate" ]; then
    echo "    Creating virtual environment..."
    "$UV_BIN" venv --seed venv --python "$PY_BIN" || { echo "❌ Failed to create venv"; read -n 1 -s -r -p "按任意键退出..."; exit 1; }
fi
source venv/bin/activate
echo "    ✓ Virtual environment ready"
echo ""

# ---------- 4. 安装依赖（增量） ----------
echo "[4/5] Checking dependencies..."
if [ ! -f ".deps_installed" ]; then
    echo "    First run: installing dependencies (1-3 min, please wait)..."
    echo "    Installing dependencies from the preferred package source."
    if python -m pip install -r requirements.txt --quiet --disable-pip-version-check --timeout 15 --retries 1 -i https://pypi.tuna.tsinghua.edu.cn/simple; then
        touch .deps_installed
        echo "    ✓ Dependencies installed"
    else
        echo "    The preferred package source is unavailable."
        echo "    Retrying with the official Python package index…"
        if python -m pip install -r requirements.txt --quiet --disable-pip-version-check --timeout 15 --retries 1 -i https://pypi.org/simple; then
            touch .deps_installed
            echo "    ✓ Dependencies installed"
        else
            echo ""
            echo "Dependency installation failed."
            echo ""
            echo "Check your internet connection and run Launch.command again."
            echo "If it keeps failing, send a screenshot of this window to the maintainer."
            echo ""
            read -n 1 -s -r -p "Press any key to close..."
            exit 1
        fi
    fi
else
    echo "    ✓ Dependencies ready (delete .deps_installed to force reinstall)"
fi
echo "Runtime environment is ready. Launching Lumielle…"
echo ""

# ---------- 5. 启动 ----------
echo "[5/5] Launching Lumielle Academic Assistant..."
echo ""
echo "Browser will open automatically at:"
echo "  http://localhost:8501"
echo ""
echo "Note: closing this terminal stops the app"
echo "=============================================="
echo ""

python -m streamlit run app.py --server.headless false --browser.gatherUsageStats false

deactivate
echo ""
echo "App stopped."
read -n 1 -s -r -p "Press any key to close..."
