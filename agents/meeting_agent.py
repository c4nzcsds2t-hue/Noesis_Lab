"""
meeting_agent.py —— 会议纪要专家 Agent
========================================
职责：接收会议录音转写的长文本，输出结构化纪要。
输出包含：会议背景、核心摘要、决议列表、待办事项（含负责人与截止时间）。

开发者：组员 A
依赖模型：Kimi (月之暗面) —— 百万字级超长上下文，适合处理长会议转写稿
模型标识：moonshot-v1-128k

本地调试运行：
    python meeting_agent.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# ── 把父目录加入模块搜索路径，解决子文件夹找不到 base_agent 的问题 ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from base_agent import BaseAgent
from schemas import AgentType, MeetingOutput, TaskInput, TaskStatus, TodoItem

load_dotenv()  # 读取 .env 里的 API Key


# ============================================================
# Prompt 模板（物理隔离，独立维护）
# ============================================================

SYSTEM_PROMPT = """
你是一位专业的企业行政秘书，擅长将口语化的会议录音转写稿提炼为结构清晰的职场会议纪要。

你的任务是从用户提供的会议转写文本中，严格按照以下 JSON 格式提取信息。
请注意：
1. 只输出 JSON，不要有任何前缀文字、解释或 Markdown 代码块标记。
2. 所有字段都必须存在，如无相关信息则用空字符串或空数组填充。
3. todo_list 是最重要的部分，务必精确提取每一个明确分配给具体负责人的任务。
4. 语言风格：简洁、职场化，避免口语和冗余。

输出格式：
{
  "background": "会议背景，包括会议主题、时间、参会人等（如转写稿中有提及）",
  "summary": "3-5句话概括本次会议的核心讨论内容和结论",
  "decisions": [
    "决议1（一句话，动词开头）",
    "决议2"
  ],
  "todo_list": [
    {
      "task": "任务描述（具体、可执行）",
      "owner": "负责人姓名（如未明确则填 '待定'）",
      "deadline": "截止时间（如'本周五'、'下周一'，原文未提及则填 '待定'）",
      "priority": "high / medium / low（根据上下文判断）"
    }
  ]
}
""".strip()


# ============================================================
# MeetingAgent 实现
# ============================================================

class MeetingAgent(BaseAgent):

    def __init__(self):
        super().__init__(AgentType.MEETING)

    # ── 可选：覆盖父类校验，加会议场景专属检查 ──
    def validate(self, input_data: TaskInput) -> None:
        super().validate(input_data)  # 先跑父类的基础校验
        text = input_data.text or ""
        if len(text.strip()) < 50:
            raise ValueError(
                "会议转写文本过短（少于50字），请提供完整的会议记录。"
            )

    # ── 核心业务逻辑 ──
    def _run(self, input_data: TaskInput) -> MeetingOutput:
        text = input_data.text or ""

        self.logger.info(f"会议文本长度：{len(text)} 字")

        # 1. 调用 LLM 提取结构化纪要
        raw_response = self.call_llm(
            system_prompt = SYSTEM_PROMPT,
            user_prompt   = f"以下是会议转写文本，请提取结构化纪要：\n\n{text}",
            model         = os.environ.get("MEETING_MODEL", "deepseek-chat"),
            temperature   = 0.2,   # 低温：保证输出稳定，减少幻觉
            expect_json   = True,
        )

        # 2. 解析 JSON（加了容错清洗）
        data = self._parse_json_safe(raw_response)

        # 3. 组装 TodoItem 列表
        todo_list = [
            TodoItem(
                task     = item.get("task", ""),
                owner    = item.get("owner"),
                deadline = item.get("deadline"),
                priority = item.get("priority"),
            )
            for item in data.get("todo_list", [])
            if item.get("task")  # 过滤掉 task 为空的垃圾数据
        ]

        # 4. 返回结构化输出
        return MeetingOutput(
            request_id  = input_data.request_id,
            agent_type  = AgentType.MEETING,
            status      = TaskStatus.SUCCESS,
            background  = data.get("background", ""),
            summary     = data.get("summary", "（未能提取摘要）"),
            decisions   = data.get("decisions", []),
            todo_list   = todo_list,
            raw_json    = data,  # 保留原始数据，方便调试
        )

    # ── 私有工具：容错 JSON 解析 ──
    def _parse_json_safe(self, raw: str) -> dict:
        """
        LLM 偶尔会在 JSON 前后多输出一些文字，这里做三层容错：
          1. 直接解析（最理想情况）
          2. 去掉 Markdown 代码块标记后解析
          3. 用正则提取第一个 {...} 块后解析
        三层都失败则返回空 dict，让调用方处理降级。
        """
        # 第一层：直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 第二层：去掉 ```json ... ``` 标记
        cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 第三层：正则提取第一个完整 JSON 对象
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        self.logger.error(f"JSON 解析三层容错全部失败，原始响应：\n{raw[:300]}")
        return {}


# ============================================================
# 本地调试入口
# 直接运行 `python meeting_agent.py` 即可测试
# ============================================================

if __name__ == "__main__":
    # 模拟一段会议转写文本（替换成真实内容即可）
    SAMPLE_TEXT = """
    好，我们开始今天的组会，今天是周五下午三点，参加会议的有导师王教授、
    张三、李四、王五，还有今天负责记录的我。
    
    这次会议主要讨论微藻治理养殖场污水的课题进展，以及和企业那边的对接事项。
    
    王教授说，上周企业那边反馈，他们急需一份关于特定微藻株系吸附重金属效率的
    调研报告，最好能在下周一之前给到他们。这个任务就由张三来负责，张三你没问题吧？
    张三说可以。
    
    然后关于实验数据，李四说他那边的吸附实验已经做完了第一轮，数据初步看起来
    C型藻株的效率比A型高出大概40%，但还需要做重复实验来确认。王教授说这个很关键，
    李四这周内要把重复实验做完，数据整理出来。
    
    王五负责文献综述部分，目前还差三篇核心文献没有读完，王教授要求他在后天也就是
    周日之前把文献综述的初稿发给大家看。
    
    最后王教授说，下周三组会的时候，大家把各自的进展汇报一下，张三的报告、
    李四的实验数据、王五的文献综述都要准备好PPT。散会。
    """.strip()

    agent = MeetingAgent()

    task = TaskInput(
        agent_type = AgentType.MEETING,
        text       = SAMPLE_TEXT,
    )

    print("=" * 50)
    print("输入文本：")
    print(SAMPLE_TEXT[:200], "...")
    print("=" * 50)
    print("正在调用 LLM，请稍候……")

    result = agent.run(task)

    print("\n【处理结果】")
    print(f"状态：{result.status}")
    print(f"耗时：{result.duration_ms} ms")

    if result.status == TaskStatus.SUCCESS:
        output: MeetingOutput = result  # type: ignore
        print(f"\n背景：{output.background}")
        print(f"\n摘要：{output.summary}")
        print(f"\n决议（{len(output.decisions)} 条）：")
        for i, d in enumerate(output.decisions, 1):
            print(f"  {i}. {d}")
        print(f"\n待办事项（{len(output.todo_list)} 条）：")
        for item in output.todo_list:
            print(f"  - [{item.priority}] {item.task}")
            print(f"    负责人：{item.owner}  截止：{item.deadline}")
    else:
        print(f"错误：{result.error_msg}")