from .workflow import app as text2sql_app

# 限制 `from agents import *` 时暴露的内容
__all__ = ["text2sql_app"]