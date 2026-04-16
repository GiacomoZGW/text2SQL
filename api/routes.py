from fastapi import APIRouter
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from agents.workflow import app as workflow_app
# 从项目入口导入编译好的 workflow app
from agents.workflow import app as text2sql_app

router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    target_db: str = "mysql"


@router.post("/chat/sql")
async def chat_with_db(request: QueryRequest):
    """
    对外暴露的接口，接收自然语言查询，触发多智能体工作流
    """
    initial_state = {
        "messages": [HumanMessage(content=request.query)],
        "target_db": request.target_db,
        "error_count": 0,
        "similarity_threshold": 0.8
    }

    # 触发 LangGraph 工作流
    final_state = workflow_app.invoke(initial_state)

    return {
        "status": "success",
        "reply": final_state["messages"][-1].content,
        "raw_sql": final_state.get("generated_sql")
    }