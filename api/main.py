
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import sys
import os

# 将项目根目录加入模块检索路径，以便顺利导入 agents 模块
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from agents.workflow import app as agent_app
from langchain_core.messages import HumanMessage

app = FastAPI(title="Data Agent 真实数据库查询 API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 定义前端传入的数据结构
class QueryRequest(BaseModel):
    user_id: str = "test_user_001"
    query: str
    target_db: str = "sqlite"


@app.post("/api/v1/query")
async def query_database(request: QueryRequest):
    print(f"\n📥 收到来自前端的 API 请求: {request.query}")

    # 构造传递给 LangGraph 的初始状态
    initial_state = {
        "messages": [HumanMessage(content=request.query)],
        "target_db_type": request.target_db,
        "error_count": 0,
        "similarity_threshold": 0.8
    }

    try:
        # 调用 LangGraph 核心工作流
        final_state = agent_app.invoke(initial_state)

        # 提取 AI 最后一步生成的数据分析报告
        answer = final_state["messages"][-1].content

        return {
            "code": 200,
            "data": {
                "answer": answer,
                "metrics": {
                    "retries_triggered": final_state.get("error_count", 0),
                    "final_threshold": final_state.get("similarity_threshold", 0.8),
                    "executed_sql": final_state.get("generated_sql", "")  # 增加返回底层执行的 SQL 语句
                }
            }
        }
    except Exception as e:
        return {
            "code": 500,
            "data": {
                "answer": f"API 内部执行发生错误: {str(e)}",
                "metrics": {}
            }
        }


def _resolve_frontend_dist_dir():
    for rel in ("frontend/dist", "dist_package/frontend"):
        d = os.path.join(BASE_DIR, rel)
        if os.path.isfile(os.path.join(d, "index.html")):
            return d
    return None


STATIC_DIR = _resolve_frontend_dist_dir()
STATIC_ABS = os.path.abspath(STATIC_DIR) if STATIC_DIR else None

if STATIC_DIR and STATIC_ABS:
    index_html = os.path.join(STATIC_DIR, "index.html")

    @app.get("/", include_in_schema=False)
    async def spa_index():
        return FileResponse(index_html)

    @app.get("/{path_name:path}", include_in_schema=False)
    async def spa_fallback(path_name: str):
        if path_name.startswith("api"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = os.path.abspath(os.path.join(STATIC_DIR, path_name))
        if not (candidate == STATIC_ABS or candidate.startswith(STATIC_ABS + os.sep)):
            raise HTTPException(status_code=404, detail="Not Found")
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(index_html)


if __name__ == "__main__":
    print("🚀 正在启动 FastAPI 服务器...")
    # print("💡 请在此之前确保已运行过 create_test_db.py 并在根目录生成了 ecommerce_test.db")
    if STATIC_DIR:
        print(f"📦 已检测到前端静态资源: {STATIC_DIR}")
        print("   浏览器访问: http://127.0.0.1:8000/")
    else:
        print("💡 未找到 frontend/dist，仅 API 可用。打包前端: cd frontend && npm run build")
    uvicorn.run(app, host="127.0.0.1", port=8000)