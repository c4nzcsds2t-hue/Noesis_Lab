"""
main.py —— FastAPI 入口 + Router 路由主管
==========================================
职责分工：
  - main.py       : HTTP 层，只负责接收请求、返回响应，不写业务逻辑
  - orchestrator  : 意图识别 + Agent 调度

启动方式：
    uvicorn main:app --reload --port 8000

接口一览：
    POST /task                  主入口，提交任务（Router 自动路由）
    POST /upload/meeting        ⭐️ 上传录音 → 自动转写 → 生成会议纪要（完整流程）
    POST /upload/literature     ⭐️ 上传 PDF  → 自动解析 → 生成文献摘要（完整流程）
    POST /task/meeting          直接传文本调用会议 Agent（调试用）
    POST /task/literature       直接传文本调用文献 Agent（调试用）
    POST /task/translate        直接调用润色 Agent（调试用）
    POST /task/ppt              直接调用 PPT Agent（调试用）
    GET  /health                健康检查
    GET  /docs                  Swagger 文档（FastAPI 内置）
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from dotenv import load_dotenv
load_dotenv()  # ⭐️ 最优先执行，确保所有 Agent 都能读到 .env

from schemas import AgentType, RouterResponse, TaskInput, TaskStatus


# ── 延迟导入各 Agent ──────────────────────────────────────────

def _load_agents() -> dict[AgentType, Any]:
    agents = {}
    agent_map = {
        AgentType.MEETING    : ("agents.meeting_agent",    "MeetingAgent"),
        AgentType.LITERATURE : ("agents.literature_agent", "LiteratureAgent"),
        AgentType.TRANSLATE  : ("agents.translate_agent",  "TranslateAgent"),
        AgentType.PPT        : ("agents.ppt_agent",        "PptAgent"),
    }
    for agent_type, (module_path, class_name) in agent_map.items():
        try:
            import importlib
            module = importlib.import_module(module_path)
            cls    = getattr(module, class_name)
            agents[agent_type] = cls()
            logging.info(f"✅ 加载成功: {class_name}")
        except Exception as e:
            logging.warning(f"⚠️  加载失败: {class_name} — {e}（该 Agent 暂不可用）")
    return agents


AGENT_REGISTRY: dict[AgentType, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global AGENT_REGISTRY
    logging.info("🚀 服务启动，正在加载 Agent……")
    AGENT_REGISTRY = _load_agents()
    logging.info(f"📋 已加载 Agent：{[k.value for k in AGENT_REGISTRY]}")
    yield
    logging.info("🛑 服务关闭")


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title       = "多智能体办公助手 API",
    description = "聚焦学术与职场过渡场景的 Multi-Agent 系统",
    version     = "0.1.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


# ============================================================
# Orchestrator
# ============================================================

class Orchestrator:
    def __init__(self, registry: dict[AgentType, Any]):
        self.registry = registry
        self.logger   = logging.getLogger("Orchestrator")

    def dispatch(self, input_data: TaskInput) -> RouterResponse:
        self.logger.info(
            f"📨 收到任务 request_id={input_data.request_id} "
            f"agent_type={input_data.agent_type}"
        )
        agent_type = input_data.agent_type
        agent = self.registry.get(agent_type)
        if agent is None:
            return RouterResponse(
                success    = False,
                agent_type = agent_type,
                data       = None,
                message    = f"Agent [{agent_type.value}] 当前不可用，请检查服务日志",
            )
        output  = agent.run(input_data)
        success = output.status == TaskStatus.SUCCESS
        return RouterResponse(
            success    = success,
            agent_type = agent_type,
            data       = output,
            message    = "处理成功" if success else f"处理失败：{output.error_msg}",
        )


def get_orchestrator() -> Orchestrator:
    return Orchestrator(AGENT_REGISTRY)


# ============================================================
# 工具函数：保存上传文件到临时目录
# ============================================================

async def _save_upload(file: UploadFile, allowed_exts: set[str]) -> tuple[str, str]:
    """
    把前端上传的文件保存到系统临时目录。

    Returns:
        (tmp_path, error_msg)  — error_msg 为空字符串表示成功
    """
    suffix = Path(file.filename or "file").suffix.lower()
    if suffix not in allowed_exts:
        return "", f"不支持的文件格式 '{suffix}'，允许：{allowed_exts}"

    # 写入临时文件
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        shutil.copyfileobj(file.file, tmp)
    finally:
        tmp.close()

    return tmp.name, ""


# ============================================================
# 路由：健康检查
# ============================================================

@app.get("/health", tags=["系统"])
def health_check():
    """服务健康检查，Flutter 前端启动时 ping 这里确认后端在线。"""
    loaded = [k.value for k in AGENT_REGISTRY]
    return {"status": "ok", "loaded_agents": loaded, "total": len(loaded)}


# ============================================================
# 路由：⭐️ 完整业务流程（文件上传）
# ============================================================

@app.post("/upload/meeting", response_model=RouterResponse, tags=["完整流程"])
async def upload_meeting(file: UploadFile = File(..., description="会议录音文件，支持 mp3/mp4/wav/m4a")):
    """
    ⭐️ 会议纪要完整流程：上传录音 → 语音转文字 → 生成结构化纪要

    步骤：
      1. 接收录音文件（mp3 / mp4 / wav / m4a）
      2. 调用硅基流动 SenseVoiceSmall 转写为文字
      3. 转写结果交给 MeetingAgent 生成结构化纪要
      4. 返回：摘要 + 决议 + 待办事项（含负责人和截止时间）

    在 Swagger 里测试：
      点击 "Try it out" → 点击 "Choose File" 上传录音 → Execute
    """
    from utils.file_parser import FileParser

    # 1. 保存上传文件到临时目录
    allowed = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".flac", ".webm"}
    tmp_path, err = await _save_upload(file, allowed)
    if err:
        return RouterResponse(
            success=False, agent_type=AgentType.MEETING,
            data=None, message=err
        )

    try:
        # 2. 语音转文字
        logging.info(f"🎙️  开始转写录音：{file.filename}")
        parse_result = FileParser.parse_audio(tmp_path)

        if not parse_result.success:
            return RouterResponse(
                success=False, agent_type=AgentType.MEETING,
                data=None,
                message=f"语音转写失败：{parse_result.error}"
            )

        logging.info(f"✅ 转写完成，共 {len(parse_result.text)} 字")

        # 3. 转写文本 → MeetingAgent
        task = TaskInput(
            agent_type = AgentType.MEETING,
            text       = parse_result.text,
            options    = {"source_file": file.filename},
        )
        return _resp(get_orchestrator().dispatch(task))

    finally:
        # 4. 清理临时文件
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/upload/literature", response_model=RouterResponse, tags=["完整流程"])
async def upload_literature(file: UploadFile = File(..., description="英文文献 PDF 文件")):
    """
    ⭐️ 文献摘要完整流程：上传 PDF → 提取文字 → 生成中文摘要 + Zettelkasten 卡片

    步骤：
      1. 接收 PDF 文件
      2. 用 pymupdf 提取全文文字
      3. 交给 LiteratureAgent 生成摘要、创新点、Zettelkasten 概念卡片

    在 Swagger 里测试：
      点击 "Try it out" → 点击 "Choose File" 上传 PDF → Execute
    """
    from utils.file_parser import FileParser

    tmp_path, err = await _save_upload(file, {".pdf"})
    if err:
        return RouterResponse(
            success=False, agent_type=AgentType.LITERATURE,
            data=None, message=err
        )

    try:
        logging.info(f"📄 开始解析 PDF：{file.filename}")
        parse_result = FileParser.parse_pdf(tmp_path)

        if not parse_result.success:
            return RouterResponse(
                success=False, agent_type=AgentType.LITERATURE,
                data=None,
                message=f"PDF 解析失败：{parse_result.error}"
            )

        logging.info(f"✅ PDF 解析完成，共 {parse_result.page_count} 页，{len(parse_result.text)} 字")

        task = TaskInput(
            agent_type = AgentType.LITERATURE,
            text       = parse_result.text,
            options    = {
                "source_file" : file.filename,
                "page_count"  : parse_result.page_count,
            },
        )
        return get_orchestrator().dispatch(task)

    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ============================================================
# 路由：调试接口（直接传文本，跳过文件解析）
# ============================================================

def _resp(result: RouterResponse) -> JSONResponse:
    """把 RouterResponse 序列化为 JSON，确保子类字段完整输出。"""
    return JSONResponse(content=result.to_dict())


@app.post("/task", tags=["任务"])
async def submit_task(input_data: TaskInput):
    """主入口：提交文本任务，Router 自动路由到对应 Agent。"""
    return _resp(get_orchestrator().dispatch(input_data))


@app.post("/task/meeting", tags=["调试"])
async def task_meeting(input_data: TaskInput):
    """直接传文本调用会议 Agent（跳过语音转写，调试专用）"""
    input_data.agent_type = AgentType.MEETING
    return _resp(get_orchestrator().dispatch(input_data))


@app.post("/task/literature", tags=["调试"])
async def task_literature(input_data: TaskInput):
    """直接传文本调用文献 Agent（跳过 PDF 解析，调试专用）"""
    input_data.agent_type = AgentType.LITERATURE
    return _resp(get_orchestrator().dispatch(input_data))


@app.post("/task/translate", tags=["调试"])
async def task_translate(input_data: TaskInput):
    """直接调用润色 Agent（调试专用）"""
    input_data.agent_type = AgentType.TRANSLATE
    return _resp(get_orchestrator().dispatch(input_data))


@app.post("/task/ppt", tags=["调试"])
async def task_ppt(input_data: TaskInput):
    """直接调用 PPT Agent（调试专用）"""
    input_data.agent_type = AgentType.PPT
    return _resp(get_orchestrator().dispatch(input_data))


# ============================================================
# 全局异常兜底
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"未捕获异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code = 500,
        content = {
            "success": False, "agent_type": "unknown",
            "data": None,
            "message": f"服务器内部错误（{type(exc).__name__}），请检查终端日志",
        },
    )


# ============================================================
# 本地运行入口
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = int(os.environ.get("PORT", 8000)),
        reload  = True,
        workers = 1,
    )