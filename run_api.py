# -*- encoding: utf-8 -*-
"""
QuantFly API 启动脚本
注意事项:
  - reload=False: Windows 上 watchdog 会卡死
  - workers=4:   单 worker 会被 QMT 同步调用阻塞事件循环
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "quantfly.api.main:app",
        host="0.0.0.0",
        port=8765,
        workers=4,          # 多进程防 QMT 阻塞
        reload=False,       # Windows上reload会卡死
    )
