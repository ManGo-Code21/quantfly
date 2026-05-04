# -*- encoding: utf-8 -*-
"""
QuantFly API 启动脚本
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "quantfly.api.main:app",
        host="0.0.0.0",
        port=8765,
        reload=True,
    )
