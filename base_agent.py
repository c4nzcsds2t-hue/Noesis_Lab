"""
base_agent.py —— 所有专家 Agent 的父类
=====================================
⭐️ 每个组员写自己的 Agent 时，必须继承 BaseAgent 并实现 _run() 方法。
父类负责：日志记录、异常捕获、耗时统计、状态管理。
组员只需要专注业务逻辑，不用操心这些横切关注点。

使用示例（组员照抄这个模式）：
─────────────────────────────────────
    from base_agent import BaseAgent
    from schemas import TaskInput, MeetingOutput, AgentType, TaskStatus

    class MeetingAgent(BaseAgent):
        def _run(self, input_data: TaskInput) -> MeetingOutput:
            # 在这里写你的业务逻辑
            # 调用 LLM、解析结果……
            return MeetingOutput(
                request_id = input_data.request_id,
                agent_type = AgentType.MEETING,
                status     = TaskStatus.SUCCESS,
                summary    = "本次会议讨论了……",
                decisions  = ["决定A", "决定B"],
                todo_list  = [],
            )
─────────────────────────────────────
"""

from __future__ import annotations

import logging
import time
import traceback
from abc import ABC, abstractmethod
from typing import Optional

from schemas import AgentType, BaseOutput, TaskInput, TaskStatus

# 全局 logger，所有 Agent 共用同一格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class BaseAgent(ABC):
    """
    所有专家 Agent 的抽象父类。

    子类必须实现：
        _run(self, input_data: TaskInput) -> BaseOutput

    子类可以选择覆盖：
        validate(self, input_data: TaskInput) -> None   （输入校验，抛异常即拦截）
        on_success(self, output: BaseOutput) -> None    （成功后钩子，如存储结果）
        on_failure(self, e: Exception) -> None          （失败后钩子，如告警）
    """

    def __init__(self, agent_type: AgentType):
        self.agent_type = agent_type
        self.logger = logging.getLogger(f"Agent.{agent_type.value}")
        self.logger.info(f"✅ {agent_type.value} Agent 初始化完成")

    # ──────────────────────────────────────────
    # 公开方法：Router 调用这个，不要直接调用 _run
    # ──────────────────────────────────────────

    def run(self, input_data: TaskInput) -> BaseOutput:
        """
        统一入口。封装了：日志、耗时统计、异常捕获、状态填写。
        Router / main.py 调用这个方法。
        """
        self.logger.info(f"▶ 开始处理 request_id={input_data.request_id}")
        start_ms = int(time.time() * 1000)

        try:
            # 1. 输入校验（子类可覆盖）
            self.validate(input_data)

            # 2. 执行业务逻辑（子类必须实现）
            output = self._run(input_data)

            # 3. 补全公共字段
            output.status      = TaskStatus.SUCCESS
            output.duration_ms = int(time.time() * 1000) - start_ms

            self.logger.info(
                f"✔ 处理成功 request_id={input_data.request_id} "
                f"耗时={output.duration_ms}ms"
            )

            # 4. 成功钩子
            self.on_success(output)
            return output

        except Exception as e:
            duration_ms = int(time.time() * 1000) - start_ms
            self.logger.error(
                f"✖ 处理失败 request_id={input_data.request_id} "
                f"耗时={duration_ms}ms\n{traceback.format_exc()}"
            )

            # 5. 失败钩子
            self.on_failure(e)

            # 6. 返回失败响应（不抛异常，让 Router 决定如何处理）
            return self._make_error_output(input_data, e, duration_ms)

    # ──────────────────────────────────────────
    # 子类必须实现的抽象方法
    # ──────────────────────────────────────────

    @abstractmethod
    def _run(self, input_data: TaskInput) -> BaseOutput:
        """
        ⭐️ 核心业务逻辑在这里实现。
        - 调用 LLM API
        - 解析结构化输出
        - 返回对应的 Output 模型

        注意：不需要处理异常，父类的 run() 已经包住了。
        """
        ...

    # ──────────────────────────────────────────
    # 子类可选覆盖的钩子方法
    # ──────────────────────────────────────────

    def validate(self, input_data: TaskInput) -> None:
        """
        输入校验。默认只检查 text 和 file_url 不能同时为空。
        子类可以覆盖以添加更严格的校验逻辑。

        示例（在子类里覆盖）：
            def validate(self, input_data):
                super().validate(input_data)   # 保留基础校验
                if "音频" not in input_data.options:
                    raise ValueError("会议 Agent 需要提供音频转写文本")
        """
        if not input_data.text and not input_data.file_url:
            raise ValueError(
                "输入校验失败：text 和 file_url 不能同时为空，"
                "请至少提供一种输入。"
            )

    def on_success(self, output: BaseOutput) -> None:
        """
        成功后的钩子。默认不做任何事。
        子类可覆盖，例如：把 ZettelCard 存入数据库。
        """
        pass

    def on_failure(self, e: Exception) -> None:
        """
        失败后的钩子。默认不做任何事。
        子类可覆盖，例如：发送钉钉告警。
        """
        pass

    # ──────────────────────────────────────────
    # 私有辅助方法
    # ──────────────────────────────────────────

    def _make_error_output(
        self,
        input_data: TaskInput,
        e: Exception,
        duration_ms: int,
    ) -> BaseOutput:
        """生成标准错误响应，供 run() 内部使用。"""
        return BaseOutput(
            request_id  = input_data.request_id,
            agent_type  = self.agent_type,
            status      = TaskStatus.FAILED,
            error_msg   = f"{type(e).__name__}: {str(e)}",
            duration_ms = duration_ms,
        )

    # ──────────────────────────────────────────
    # 组员可以调用的公共工具方法
    # ──────────────────────────────────────────

    def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str = "deepseek-chat",
        temperature: float = 0.3,
        expect_json: bool = True,
        api_key_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
    ) -> str:
        """
        统一的 LLM 调用方法（已集成重试逻辑）。
        组员在 _run() 里直接调用这个，不要自己拼 openai client。

        Args:
            system_prompt: 系统提示词（从 prompt_lib 取）
            user_prompt:   用户输入内容
            model:         模型名称，默认 deepseek-chat
            temperature:   生成温度，结构化输出建议用 0.0~0.3
            expect_json:   是否期望 JSON 输出（自动添加 response_format 参数）

        Returns:
            LLM 返回的原始字符串

        示例：
            raw = self.call_llm(
                system_prompt = PROMPTS["meeting_extract"],
                user_prompt   = input_data.text,
                expect_json   = True,
            )
            data = json.loads(raw)
        """
        # 延迟导入，避免没装 openai 时整个模块无法加载
        try:
            from openai import OpenAI  # DeepSeek / Kimi / GLM 均兼容 OpenAI SDK
        except ImportError:
            raise ImportError("请先安装依赖：pip install openai")

        # ⚠️  API key 和 base_url 从环境变量读取，不要硬编码！
        # 在 .env 文件里配置：
        #   DEEPSEEK_API_KEY=sk-xxx
        #   DEEPSEEK_BASE_URL=https://api.deepseek.com
        import os
        client = OpenAI(
            api_key  = api_key_override  or os.environ.get("LLM_API_KEY",  "请在.env中配置LLM_API_KEY"),
            base_url = base_url_override or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]

        kwargs: dict = dict(
            model       = model,
            messages    = messages,
            temperature = temperature,
            max_tokens  = 4096,
        )
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                self.logger.info(
                    f"LLM 调用 → model={model} "
                    f"base_url={base_url_override or os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com')} "
                    f"attempt={attempt}"
                )
                response = client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as e:
                self.logger.warning(f"LLM 调用失败 attempt={attempt}/{max_retries}: {e}")
                if attempt == max_retries:
                    raise
                time.sleep(2 ** attempt)  # 指数退避

        return ""  # 不会走到这里，只是让 mypy 满意