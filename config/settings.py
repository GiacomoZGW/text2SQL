# import os
# import yaml
# from pathlib import Path
# import dotenv
# dotenv.load_dotenv()
#
# # 基础路径配置
# BASE_DIR = Path(__file__).resolve().parent.parent
# CONFIG_DIR = BASE_DIR / "config"
#
# # 大模型配置
# OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
# LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.5-flash")
#
#
# # 向量数据库配置
# MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
#
# # 加载数据库映射配置 (虚拟库名 -> 真实连接)
# def load_db_mappings():
#     mapping_file = CONFIG_DIR / "db_mappings.yaml"
#     if mapping_file.exists():
#         with open(mapping_file, "r", encoding="utf-8") as f:
#             return yaml.safe_load(f).get("mappings", {})
#     return {}
#
# DB_MAPPINGS = load_db_mappings()

import os
from dotenv import load_dotenv

# 🚀 核心补充：强制加载项目根目录下的 .env 文件
# 如果找到了 .env 文件，它会自动把里面的键值对写入系统的环境变量中
load_dotenv()

# 大模型环境变量 (现在它能正确读取 .env 里的值了)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 添加一个安全校验，如果没读到 Key，在启动时就在终端给与红色警告
if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("sk-your"):
    print("\033[91m⚠️ 警告: 未读取到有效的 OPENAI_API_KEY！请检查项目根目录下是否存在 .env 文件，并且内容正确配置！\033[0m")

# 兼容各种大模型平台 (默认指向阿里云百炼 Qwen，如果你用 DeepSeek 或 OpenAI，在 .env 里改掉即可)
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.5-flash")

# 向量数据库环境变量
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")