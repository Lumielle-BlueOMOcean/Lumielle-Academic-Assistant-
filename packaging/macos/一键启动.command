#!/bin/bash
# ============================================================
#  Lumielle Academic Assistant - MacOS 一键启动器（uv 方案）
#  双击本文件即可自动准备运行环境并启动工具
#  已内置 Python 运行时，无需联网下载（仅装依赖需联网）
# ============================================================

# 进入脚本所在目录（保证双击运行时路径正确）
cd "$(dirname "$0")" || exit 1

echo ""
echo "=============================================="
echo "  Lumielle Academic Assistant"
echo "  正在准备运行环境并启动..."
echo "=============================================="
echo ""

# ---------- 1. 定位 uv（包内优先，其次系统） ----------
UV_BIN=""
if [ -f "$(pwd)/.uv/uv" ]; then
    UV_BIN="$(pwd)/.uv/uv"
elif command -v uv &>/dev/null; then
    UV_BIN="$(command -v uv)"
else
    echo "[1/4] 未找到 uv 运行时，正在自动下载（约30秒）..."
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ]; then
        UV_ARCH="aarch64-apple-darwin"
    else
        UV_ARCH="x86_64-apple-darwin"
    fi
    mkdir -p "$(pwd)/.uv"
    curl -LsSf "https://github.com/astral-sh/uv/releases/latest/download/uv-${UV_ARCH}.tar.gz" | tar -xz -C "$(pwd)/.uv" || {
        echo ""
        echo "❌ uv 下载失败，请检查网络后重新运行。"
        echo "   下载地址: https://github.com/astral-sh/uv/releases"
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

echo "[2/4] 检查 Python 运行时..."
PY_BIN=$(find "$(pwd)/.python" -type f -name "python3.12" -path "*/bin/*" 2>/dev/null | head -1)
if [ -z "$PY_BIN" ]; then
    PY_BIN=$(find "$(pwd)/.python" -name "python3" -path "*/bin/*" 2>/dev/null | head -1)
fi

if [ -z "$PY_BIN" ]; then
    echo "    未找到可用 Python（可能是不同芯片架构），正在自动下载匹配版本（约1-3分钟）..."
    rm -rf "$(pwd)/.python"
    mkdir -p "$(pwd)/.python"
    "$UV_BIN" python install 3.12 || {
        echo "    镜像下载失败，尝试官方源..."
        unset UV_PYTHON_INSTALL_MIRROR
        "$UV_BIN" python install 3.12 || {
            echo ""
            echo "❌ Python 下载失败，请检查网络后重新运行。"
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
    echo "❌ 未找到包内 Python 解释器，请删除 .python 文件夹后重新运行。"
    read -n 1 -s -r -p "按任意键退出..."
    exit 1
fi
echo "    ✓ Python: $($PY_BIN --version 2>&1)"
echo ""

# ---------- 3. 创建虚拟环境 ----------
echo "[3/4] 检查虚拟环境..."
if [ ! -f "$(pwd)/venv/bin/activate" ]; then
    echo "    正在创建虚拟环境..."
    "$UV_BIN" venv --seed venv --python "$PY_BIN" || { echo "❌ 虚拟环境创建失败"; read -n 1 -s -r -p "按任意键退出..."; exit 1; }
fi
source venv/bin/activate
echo "    ✓ 虚拟环境就绪"
echo ""

# ---------- 4. 安装依赖（增量） ----------
echo "[4/5] 检查依赖..."
if [ ! -f ".deps_installed" ]; then
    echo "    首次运行，正在安装依赖（约1-3分钟，请耐心等待）..."
    echo "    正在尝试国内软件源安装依赖。"
    if python -m pip install -r requirements.txt --quiet --disable-pip-version-check --timeout 15 --retries 1 -i https://pypi.tuna.tsinghua.edu.cn/simple; then
        touch .deps_installed
        echo "    ✓ 依赖安装完成"
    else
        echo "    国内软件源暂时无法连接，正在自动切换备用源……"
        if python -m pip install -r requirements.txt --quiet --disable-pip-version-check --timeout 15 --retries 1 -i https://pypi.org/simple; then
            touch .deps_installed
            echo "    ✓ 依赖安装完成"
        else
            echo ""
            echo "依赖安装失败。"
            echo ""
            echo "请检查网络连接后重新双击“一键启动.command”。"
            echo "如果多次失败，请将此窗口中的错误截图发送给维护者。"
            echo ""
            read -n 1 -s -r -p "按任意键退出..."
            exit 1
        fi
    fi
else
    echo "    ✓ 依赖已就绪（如需强制重装，删除 .deps_installed 后重试）"
fi
echo "运行环境准备完成，正在启动微光……"
echo ""

# ---------- 5. 启动 ----------
echo "[5/5] 启动 Lumielle Academic Assistant..."
echo ""
echo "启动完成后浏览器将自动打开，访问地址："
echo "  http://localhost:8501"
echo ""
echo "提示：关闭本终端窗口 = 停止工具"
echo "=============================================="
echo ""

python -m streamlit run app.py --server.headless false --browser.gatherUsageStats false

deactivate
echo ""
echo "工具已停止。"
read -n 1 -s -r -p "按任意键关闭窗口..."
