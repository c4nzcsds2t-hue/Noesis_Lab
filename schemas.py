"""
schemas.py —— 全项目数据契约
=====================================
⭐️ 这是组员之间最重要的"接口协议"文件。
所有 Agent 的输入输出都必须用这里定义的模型，禁止自己造轮子。

使用方式：
    from schemas import TaskInput, MeetingOutput, LiteratureOutput, ...
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ============================================================
# 0. 枚举类：Agent 类型 与 任务状态
# ============================================================

class AgentType(str, Enum):
    """Router 用这个枚举决定把任务派给谁"""
    MEETING    = "meeting"       # 会议纪要 Agent
    LITERATURE = "literature"    # 文献摘要 Agent
    TRANSLATE  = "translate"     # 多语言润色 Agent
    PPT        = "ppt"           # 演示文档生成 Agent


class TaskStatus(str, Enum):
    PENDING    = "pending"       # 等待处理
    RUNNING    = "running"       # 处理中
    SUCCESS    = "success"       # 成功
    FAILED     = "failed"        # 失败


# ============================================================
# 1. 通用输入模型
# ============================================================

class TaskInput(BaseModel):
    """
    所有 Agent 统一接收的输入格式。
    HTTP 请求体也用这个模型，FastAPI 会自动校验。

    示例：
        {
            "agent_type": "meeting",
            "text": "今天开会讨论了微藻项目……",
            "file_url": null,
            "options": {"language": "zh"}
        }
    """
    agent_type : AgentType           = Field(...,  description="指定处理该任务的 Agent 类型")
    text       : Optional[str]       = Field(None, description="直接传入的文本内容（会议转写稿、文献摘要等）")
    file_url   : Optional[str]       = Field(None, description="文件下载链接，由 file_parser 负责解析")
    options    : dict[str, Any]      = Field(default_factory=dict, description="各 Agent 自定义的额外参数")
    request_id : str                 = Field(
        default_factory=lambda: datetime.now().strftime("%Y%m%d%H%M%S%f"),
        description="请求唯一 ID，用于日志追踪"
    )


# ============================================================
# 2. 通用输出基类（所有 Agent 输出必须继承）
# ============================================================

class BaseOutput(BaseModel):
    """
    所有专家 Agent 输出的公共字段。
    具体 Agent 在此基础上扩展自己的字段。
    """
    model_config = {"arbitrary_types_allowed": True}

    request_id  : str        = Field(...,  description="与输入的 request_id 对应，方便前端对账")
    agent_type  : AgentType  = Field(...,  description="产出该结果的 Agent 类型")
    status      : TaskStatus = Field(...,  description="任务状态")
    error_msg   : Optional[str] = Field(None, description="失败时的错误信息")
    created_at  : datetime   = Field(default_factory=datetime.now)
    duration_ms : Optional[int] = Field(None, description="Agent 处理耗时（毫秒），由父类自动填写")


# ============================================================
# 3. 会议纪要 Agent —— 输出模型
# ============================================================

class TodoItem(BaseModel):
    """单条待办事项"""
    task        : str            = Field(..., description="任务描述")
    owner       : Optional[str] = Field(None, description="负责人")
    deadline    : Optional[str] = Field(None, description="截止时间，自由文本，如'下周一'")
    priority    : Optional[str] = Field(None, description="优先级：high / medium / low")


class MeetingOutput(BaseOutput):
    """
    会议纪要 Agent 的输出。
    LLM 被要求严格按 JSON 输出，字段与此模型一一对应。

    示例：
        output.summary       -> "本次会议讨论了微藻治污方案……"
        output.decisions     -> ["决定采购藻株 A 型", "下周提交预算"]
        output.todo_list     -> [TodoItem(task="撰写调研报告", owner="张三", deadline="周五")]
    """
    background  : Optional[str]     = Field(None, description="会议背景与参会人")
    summary     : str               = Field(...,  description="会议核心内容摘要（3-5句）")
    decisions   : list[str]         = Field(default_factory=list, description="核心决议列表")
    todo_list   : list[TodoItem]    = Field(default_factory=list, description="待办事项（含负责人与截止时间）")
    raw_json    : Optional[dict]    = Field(None, description="LLM 原始 JSON 输出，调试用")


# ============================================================
# 4. 文献摘要 Agent —— 输出模型（含 Zettelkasten）
# ============================================================

class ZettelCard(BaseModel):
    """
    Zettelkasten 概念卡片。
    每张卡片对应一个从文献中提取的核心概念。
    """
    card_id     : str            = Field(..., description="唯一ID，格式建议：YYYYMMDD-概念英文缩写")
    concept     : str            = Field(..., description="概念名称")
    definition  : str            = Field(..., description="一句话定义")
    source      : str            = Field(..., description="来源文献标题或 DOI")
    related_ids : list[str]      = Field(default_factory=list, description="关联卡片 ID 列表，用于构建知识网络")
    tags        : list[str]      = Field(default_factory=list, description="领域标签，如 ['微藻', '重金属吸附']")


class LiteratureOutput(BaseOutput):
    """
    文献摘要 Agent 的输出。
    核心亮点是 zettel_cards，实现跨文献知识互联。
    """
    title       : str               = Field(...,  description="文献标题")
    authors     : list[str]         = Field(default_factory=list, description="作者列表")
    core_argument : str             = Field(...,  description="核心论点（1-2句）")
    innovations : list[str]         = Field(default_factory=list, description="主要创新点列表")
    key_data    : list[str]         = Field(default_factory=list, description="关键实验数据或结论")
    zh_summary  : str               = Field(...,  description="中文研读摘要（面向研究生）")
    zettel_cards: list[ZettelCard]  = Field(default_factory=list, description="自动提取的 Zettelkasten 概念卡片")


# ============================================================
# 5. 多语言润色 Agent —— 输出模型
# ============================================================

class TranslateStyle(str, Enum):
    """润色方向"""
    ACADEMIC_TO_BUSINESS = "academic_to_business"  # 学术态 → 商务态
    BUSINESS_TO_ACADEMIC = "business_to_academic"  # 商务态 → 学术态
    POLISH_ONLY          = "polish_only"            # 仅润色，不转换风格


class TranslateOutput(BaseOutput):
    """多语言润色 Agent 的输出"""
    style         : TranslateStyle  = Field(..., description="本次润色的方向")
    original_text : str             = Field(..., description="原始输入文本")
    polished_text : str             = Field(..., description="润色后的文本")
    target_lang   : str             = Field(..., description="目标语言，如 'zh' / 'en'")
    change_notes  : list[str]       = Field(default_factory=list, description="主要修改说明，方便用户对比理解")


# ============================================================
# 6. 演示文档 Agent —— 输出模型
# ============================================================

class SlideSection(BaseModel):
    """单张幻灯片的内容结构"""
    slide_num   : int           = Field(..., description="幻灯片序号，从 1 开始")
    title       : str           = Field(..., description="本页标题")
    bullets     : list[str]     = Field(default_factory=list, description="要点列表")
    notes       : Optional[str] = Field(None, description="演讲者备注")


class PptOutput(BaseOutput):
    """
    演示文档 Agent 的输出。
    outline_json 供 Python 渲染层消费，pdf_url 是最终产物。
    """
    deck_title   : str               = Field(...,  description="演示文稿标题")
    outline_json : list[SlideSection]= Field(...,  description="结构化大纲，Python 层用它生成 Typst 源码")
    typst_source : Optional[str]     = Field(None, description="生成的 Typst 源码（调试用）")
    pdf_url      : Optional[str]     = Field(None, description="编译完成后的 PDF 下载链接")


# ============================================================
# 7. Router 统一响应包装
# ============================================================

class RouterResponse(BaseModel):
    """
    FastAPI 统一返回格式。
    前端永远只需要解析这一个结构。

    示例：
        {
            "success": true,
            "agent_type": "meeting",
            "data": { ...MeetingOutput... },
            "message": "处理成功"
        }
    """
    model_config = {"arbitrary_types_allowed": True}

    success    : bool                    = Field(...,  description="是否成功")
    agent_type : AgentType               = Field(...,  description="处理本次请求的 Agent")
    data       : Optional[Any]           = Field(None, description="Agent 的具体输出")
    message    : str                     = Field("",   description="给前端看的提示信息")

    def model_post_init(self, __context: Any) -> None:
        """确保 data 字段在序列化时输出子类的所有字段"""
        pass

    def to_dict(self) -> dict:
        result = self.model_dump()
        if self.data and hasattr(self.data, 'model_dump'):
            result['data'] = self.data.model_dump(mode='json')  # mode='json' 自动把 datetime 转成字符串
        return result