@echo off
chcp 65001 >nul
echo ========================================
echo   QMT 数据服务启动脚本
echo ========================================
echo.

:: QMT Python 路径（根据实际安装位置修改）
set QMT_PYTHON=D:\国金证券QMT交易端\GlodonTAPy\python.exe

:: 检查 Python 是否存在
if not exist "%QMT_PYTHON%" (
    echo [错误] 找不到 QMT Python: %QMT_PYTHON%
    echo.
    echo 请确认 QMT 安装路径，编辑本脚本修改 QMT_PYTHON 变量
    echo 常见路径：
    echo   D:\国金证券QMT交易端\GlodonTAPy\python.exe
    echo   C:\QMT交易端\GlodonTAPy\python.exe
    pause
    exit /b 1
)

:: 检查依赖
echo [1/3] 检查依赖...
"%QMT_PYTHON%" -c "import flask, flask_cors" 2>nul
if errorlevel 1 (
    echo [2/3] 安装依赖 flask flask-cors...
    "%QMT_PYTHON%" -m pip install flask flask-cors -q
)

:: 启动服务
echo [3/3] 启动 QMT Data Server...
echo.
echo 监听地址: http://0.0.0.0:8888
echo 账户: 8886001679
echo.
echo 按 Ctrl+C 停止服务
echo.

cd /d "%~dp0"
start "QMT Data Server" "%QMT_PYTHON%" qmt_data_server.py --host 0.0.0.0 --port 8888

:: 也可用以下命令查看实时日志：
:: "%QMT_PYTHON%" qmt_data_server.py --host 0.0.0.0 --port 8888
