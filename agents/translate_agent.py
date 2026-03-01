"""
agents/translate_agent.py ---- 多语言润色 Agent
=============================================
功能：实现学术态 ↔ 商务态的风格迁移，以及纯文本润色
核心差异化亮点：语境与风格迁移 (Style Transfer)

继承 BaseAgent，遵循父类规范：
- _run() 实现核心业务逻辑
- 可选的 validate() 增强输入校验
- 使用父类的 call_llm() 调用模型
- 异常由父类自动捕获处理

本地调试运行：
    python translate_agent.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import cast

# ── 把父目录加入模块搜索路径，解决子文件夹找不到 base_agent 的问题 ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from base_agent import BaseAgent
from schemas import (
    AgentType,
    TaskInput,
    TranslateOutput,
    TranslateStyle,
    TaskStatus,
)
from utils.prompt_lib import PromptLib

load_dotenv()  # 读取 .env 里的 API Key


class TranslateAgent(BaseAgent):
    """
    多语言润色 Agent
    负责将文本在不同风格间转换：学术态 ↔ 商务态，或仅做语言润色
    """

    def __init__(self):
        """初始化，指定 Agent 类型为 translate"""
        super().__init__(AgentType.TRANSLATE)

    def validate(self, input_data: TaskInput) -> None:
        """
        增强的输入校验
        1. 调用父类基础校验（text/file_url 不能同时为空）
        2. 检查 options 中是否包含必要的参数
        3. 检查文本长度（润色需要足够内容）
        """
        # 先执行基础校验
        super().validate(input_data)

        # 获取文本并检查长度
        text = input_data.text or ""
        if len(text.strip()) < 10:
            raise ValueError("输入文本过短（少于10字），请提供需要润色的完整文本")

        # 获取 options（默认空字典）
        options = input_data.options or {}

        # 检查 style 参数
        style = options.get("style")
        if not style:
            raise ValueError("缺少必要参数：options.style (academic_to_business / business_to_academic / polish_only)")

        # 验证 style 值是否合法
        valid_styles = [s.value for s in TranslateStyle]
        if style not in valid_styles:
            raise ValueError(f"style 参数无效：{style}，可选值：{valid_styles}")

        # 检查 target_lang 参数
        target_lang = options.get("target_lang")
        if not target_lang:
            raise ValueError("缺少必要参数：options.target_lang (zh / en)")

        if target_lang not in ["zh", "en"]:
            raise ValueError(f"target_lang 当前仅支持 zh/en，不支持：{target_lang}")

        # 可选：记录日志表明校验通过
        self.logger.info(
            f"输入校验通过：style={style}, target_lang={target_lang}, "
            f"text长度={len(text)}"
        )

    # ── 私有工具：容错 JSON 解析（从 Meeting Agent 借鉴） ──
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

    def _run(self, input_data: TaskInput) -> TranslateOutput:
        """
        核心业务逻辑：调用 LLM 进行风格迁移润色

        流程：
        1. 从 input_data.options 提取参数
        2. 根据 style 选择对应的 System Prompt
        3. 调用 LLM（强制 JSON 输出）
        4. 解析结果并构造 TranslateOutput
        """
        # ---------- 1. 提取参数 ----------
        options = input_data.options or {}
        style_str = options.get("style")  # validate 已确保存在
        target_lang = options.get("target_lang")  # validate 已确保存在
        tone = options.get("tone", "formal")  # 可选，默认正式语气
        audience = options.get("audience", "")  # 可选，受众描述

        # 将 style_str 转为枚举类型
        style = TranslateStyle(style_str)

        # 获取原始文本（validate 已确保 text 或 file_url 不为空）
        original_text = input_data.text or ""
        
        self.logger.info(
            f"开始处理润色任务：style={style.value}, target_lang={target_lang}, "
            f"原文长度={len(original_text)}"
        )

        # ---------- 2. 选择 System Prompt ----------
        # 根据 style 从 PromptLib 获取对应的提示词
        system_prompt = PromptLib.get("translate", style=style.value)

        # 如果提供了受众信息，可以附加到 prompt 中增强效果
        if audience:
            system_prompt += f"\n\n特别注意：本次润色的目标受众是：{audience}。请根据受众特点调整表达方式。"

        # 构建用户提示词，包含原文和目标语言要求
        user_prompt = f"""
原文（需要润色的文本）：
{original_text}

目标语言：{target_lang}
语气要求：{tone}

请严格按照要求的 JSON 格式输出润色结果。
"""

        # ---------- 3. 调用 LLM ----------
        # 从环境变量读取模型名称，支持动态切换
        model_name = os.environ.get("TRANSLATE_MODEL", "deepseek-chat")
        
        self.logger.info(f"LLM 调用：model={model_name}, style={style.value}")

        raw_output = self.call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model_name,
            temperature=0.2,  # 风格迁移需要一定创造性，但不宜过高
            expect_json=True,  # 强制 JSON 输出
        )

        # ---------- 4. 解析结果（使用容错解析） ----------
        result = self._parse_json_safe(raw_output)
        
        if not result:
            self.logger.warning("LLM 返回空结果，使用降级处理")
            # 降级：直接返回原文，并添加说明
            polished_text = original_text
            change_notes = ["⚠️ 润色服务暂时不可用，返回原文"]
        else:
            polished_text = result.get("polished_text", original_text)
            change_notes = result.get("change_notes", ["文本已完成风格迁移润色"])
            
        # 确保 change_notes 不为空且有内容
        if not change_notes:
            change_notes = ["文本已完成润色"]
            
        # 过滤空白的修改说明
        change_notes = [note for note in change_notes if note and note.strip()]
        if not change_notes:
            change_notes = ["文本已完成风格转换"]

        self.logger.info(
            f"润色完成：润色后长度={len(polished_text)}, "
            f"修改说明数={len(change_notes)}"
        )

        # ---------- 5. 构造输出对象 ----------
        output = TranslateOutput(
            # 业务字段
            style=style,
            original_text=original_text,
            polished_text=polished_text,
            target_lang=target_lang,
            change_notes=change_notes,
            # 父类字段
            request_id=input_data.request_id,
            agent_type=self.agent_type,
            status=TaskStatus.SUCCESS,
            error_msg=None,
            duration_ms=None,  # 父类会填
        )

        return output

    def on_success(self, output: TranslateOutput) -> None:
        """
        成功后的钩子函数
        记录统计信息
        """
        # 计算润色前后的变化
        original_len = len(output.original_text)
        polished_len = len(output.polished_text)
        change_ratio = (polished_len - original_len) / original_len if original_len else 0
        
        self.logger.info(
            f"润色成功统计："
            f"style={output.style.value}, "
            f"原文长度={original_len}, "
            f"润色后长度={polished_len}, "
            f"变化率={change_ratio:+.1%}, "
            f"修改说明数={len(output.change_notes)}"
        )

    def on_failure(self, e: Exception) -> None:
        """
        失败后的钩子函数
        记录错误日志
        """
        self.logger.error(f"TranslateAgent 处理失败：{type(e).__name__}: {e}")


# ============================================================
# 本地测试
# ============================================================

if __name__ == "__main__":
    # 创建 Agent 实例
    agent = TranslateAgent()

    # 测试用例 1：学术 → 商务（中文）
    test_academic_to_business = TaskInput(
        agent_type=AgentType.TRANSLATE,
        text="基于微藻的生物吸附技术在处理含重金属工业废水方面展现出显著潜力。研究表明，通过优化培养条件和细胞表面修饰，藻类对Cd2+和Pb2+的去除率可分别达到85%和92%以上。这一发现为开发低成本、环境友好的废水处理工艺提供了新思路。",
        options={
            "style": "academic_to_business",
            "target_lang": "zh",
            "audience": "环保企业技术负责人"
        }
    )

    # 测试用例 2：商务 → 学术（中文）
    test_business_to_academic = TaskInput(
        agent_type=AgentType.TRANSLATE,
        text="我们团队用微藻处理污水效果很好，Cd和Pb去除率分别到了85%和92%。这个技术成本低，环保，可以推广给企业用。",
        options={
            "style": "business_to_academic",
            "target_lang": "zh"
        }
    )

    # 测试用例 3：仅润色（英文）
    test_polish_only = TaskInput(
        agent_type=AgentType.TRANSLATE,
        text="The microalgae-based biosorption technology shows significant potential for treating heavy metal-containing industrial wastewater. Research indicates that by optimizing culture conditions and cell surface modification, the removal rates of Cd2+ and Pb2+ can reach over 85% and 92% respectively.",
        options={
            "style": "polish_only",
            "target_lang": "en"
        }
    )

    # 测试用例 4：边界情况 - 超短文本
    test_too_short = TaskInput(
        agent_type=AgentType.TRANSLATE,
        text="很好。",
        options={
            "style": "polish_only",
            "target_lang": "zh"
        }
    )

    # 运行测试
    test_cases = [
        ("学术 → 商务（中文）", test_academic_to_business),
        ("商务 → 学术（中文）", test_business_to_academic),
        ("仅润色（英文）", test_polish_only),
        ("边界：超短文本", test_too_short),
    ]

    for test_name, test_input in test_cases:
        print("\n" + "=" * 60)
        print(f"测试：{test_name}")
        print("=" * 60)
        print(f"输入文本预览：{test_input.text[:100]}..." if len(test_input.text) > 100 else f"输入文本：{test_input.text}")
        print(f"参数：style={test_input.options.get('style')}, target_lang={test_input.options.get('target_lang')}")
        print("-" * 40)
        print("正在处理...")
        
        output = agent.run(test_input)
        
        print(f"\n状态：{output.status}")
        if output.status == "success":
            print(f"\n润色后：\n{output.polished_text}")
            print(f"\n修改说明：")
            for i, note in enumerate(output.change_notes, 1):
                print(f"  {i}. {note}")
            print(f"\n耗时：{output.duration_ms}ms")
        else:
            print(f"错误：{output.error_msg}")