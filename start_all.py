import subprocess
import sys
import os
import time


def start_services():
    print("🚀 正在启动 Data Agent 联邦查询工作台 (开发环境)...")

    # 获取项目根目录和前端目录
    base_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(base_dir, "frontend")

    if not os.path.exists(frontend_dir):
        print(f"❌ 找不到前端目录: {frontend_dir}")
        print("请确保您已经在项目根目录下创建了 frontend 文件夹。")
        sys.exit(1)

    # 1. 启动 FastAPI 后端
    print("\n[1/2] 正在启动后端服务 (FastAPI)...")
    backend_process = subprocess.Popen(
        [sys.executable, "api/main.py"],
        cwd=base_dir
    )

    # 给后端一点启动时间
    time.sleep(3)

    # 2. 启动 React 前端
    print("\n[2/2] 正在启动前端服务 (Vite)...")
    # 兼容 Windows 和 Unix 系统的 npm 命令调用
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"

    frontend_process = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=frontend_dir
    )

    print("\n✅ 所有服务已成功拉起！请保持此窗口打开。")
    print("💡 提示: 按下 Ctrl+C 可以同时安全关闭前后端服务。\n")

    try:
        # 保持主进程运行，等待子进程
        backend_process.wait()
        frontend_process.wait()
    except KeyboardInterrupt:
        print("\n\n⏹️ 收到中断信号，正在安全关闭所有服务...")
        backend_process.terminate()
        frontend_process.terminate()
        print("👋 服务已完全关闭。")


if __name__ == "__main__":
    start_services()