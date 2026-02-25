"""
utils/file_parser.py —— 公共文件解析器
========================================
负责把各种格式的文件转成 Agent 能直接处理的纯文本字符串。

支持的格式：
  - PDF   → 提取文字内容（支持本地路径和 URL）
  - 音频  → 调用 Whisper API 转写为文字（支持 mp3/mp4/wav/m4a）
  - TXT   → 直接读取
  - DOCX  → 提取正文文字

所有方法都返回 ParseResult，包含提取的文本和元数据。

使用方式：
    from utils.file_parser import FileParser

    # 解析 PDF
    result = FileParser.parse_pdf("论文.pdf")
    print(result.text)

    # 解析音频
    result = FileParser.parse_audio("会议录音.mp3")
    print(result.text)

    # 自动识别类型
    result = FileParser.parse("任意文件.pdf")
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# 把父目录加入路径（从 utils/ 子目录运行时需要）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============================================================
# 解析结果数据类
# ============================================================

@dataclass
class ParseResult:
    """
    文件解析结果。
    所有 parse_* 方法都返回这个对象，Agent 直接用 result.text 即可。
    """
    text      : str              # 提取出的纯文本内容（Agent 的主要输入）
    source    : str              # 文件路径或 URL
    file_type : str              # "pdf" / "audio" / "txt" / "docx"
    page_count: int  = 0         # PDF 专用：总页数
    duration_s: float = 0.0     # 音频专用：时长（秒）
    metadata  : dict = field(default_factory=dict)  # 其他元数据
    error     : str  = ""        # 解析失败时的错误信息

    @property
    def success(self) -> bool:
        return not self.error and bool(self.text.strip())

    def __repr__(self) -> str:
        status = "✅" if self.success else "❌"
        return (
            f"{status} ParseResult(type={self.file_type}, "
            f"chars={len(self.text)}, source={Path(self.source).name})"
        )


# ============================================================
# 主解析器类
# ============================================================

class FileParser:
    """
    统一文件解析接口。
    所有方法都是类方法，无需实例化，直接 FileParser.parse_pdf(...) 调用。
    """

    # ── 自动识别类型的统一入口 ──────────────────────────────

    @classmethod
    def parse(cls, path_or_url: str) -> ParseResult:
        """
        自动识别文件类型并调用对应解析器。

        支持本地路径和 HTTP/HTTPS URL。
        对于 URL，会先下载到临时目录再解析。

        示例：
            result = FileParser.parse("paper.pdf")
            result = FileParser.parse("https://example.com/paper.pdf")
            result = FileParser.parse("meeting.mp3")
        """
        # 如果是 URL，先下载
        if path_or_url.startswith(("http://", "https://")):
            return cls._parse_from_url(path_or_url)

        path = Path(path_or_url)
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            return cls.parse_pdf(path_or_url)
        elif suffix in {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".flac", ".webm"}:
            return cls.parse_audio(path_or_url)
        elif suffix == ".txt":
            return cls.parse_txt(path_or_url)
        elif suffix == ".docx":
            return cls.parse_docx(path_or_url)
        else:
            return ParseResult(
                text="", source=path_or_url, file_type="unknown",
                error=f"不支持的文件格式：{suffix}，"
                      f"支持：.pdf / .mp3 / .mp4 / .wav / .m4a / .txt / .docx"
            )

    # ── PDF 解析 ────────────────────────────────────────────

    @classmethod
    def parse_pdf(cls, path: str, max_pages: int = 500) -> ParseResult:
        """
        从 PDF 中提取纯文本。
        优先使用 pymupdf（速度快），如未安装则尝试 pdfplumber（表格效果好）。

        Args:
            path:      PDF 文件路径（本地）
            max_pages: 最大解析页数，超长文献可限制范围

        Returns:
            ParseResult，text 为所有页面文字的拼接（页面间用 \\n\\n 分隔）
        """
        try:
            import fitz  # pymupdf
            return cls._parse_pdf_with_fitz(path, max_pages)
        except ImportError:
            pass

        try:
            import pdfplumber
            return cls._parse_pdf_with_pdfplumber(path, max_pages)
        except ImportError:
            return ParseResult(
                text="", source=path, file_type="pdf",
                error="PDF 解析库未安装，请运行：pip install pymupdf 或 pip install pdfplumber"
            )

    @classmethod
    def _parse_pdf_with_fitz(cls, path: str, max_pages: int) -> ParseResult:
        """使用 pymupdf (fitz) 解析 PDF"""
        import fitz

        doc = fitz.open(path)
        total_pages = len(doc)
        pages_to_read = min(total_pages, max_pages)

        texts = []
        for i in range(pages_to_read):
            page = doc[i]
            text = page.get_text("text")
            if text.strip():
                texts.append(f"--- 第 {i+1} 页 ---\n{text.strip()}")

        doc.close()
        full_text = "\n\n".join(texts)

        return ParseResult(
            text       = full_text,
            source     = path,
            file_type  = "pdf",
            page_count = total_pages,
            metadata   = {
                "parser"      : "pymupdf",
                "pages_read"  : pages_to_read,
                "truncated"   : total_pages > max_pages,
            }
        )

    @classmethod
    def _parse_pdf_with_pdfplumber(cls, path: str, max_pages: int) -> ParseResult:
        """使用 pdfplumber 解析 PDF（表格识别更强）"""
        import pdfplumber

        texts = []
        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)
            pages_to_read = min(total_pages, max_pages)

            for i, page in enumerate(pdf.pages[:pages_to_read]):
                text = page.extract_text() or ""
                if text.strip():
                    texts.append(f"--- 第 {i+1} 页 ---\n{text.strip()}")

        return ParseResult(
            text       = "\n\n".join(texts),
            source     = path,
            file_type  = "pdf",
            page_count = total_pages,
            metadata   = {
                "parser"    : "pdfplumber",
                "pages_read": pages_to_read,
                "truncated" : total_pages > max_pages,
            }
        )

    # ── 音频解析 ────────────────────────────────────────────

    @classmethod
    def parse_audio(cls, path: str) -> ParseResult:
        """
        将音频文件转写为文字。
        使用硅基流动（SiliconFlow）的 SenseVoiceSmall 模型，中文识别效果好、价格低。

        支持格式：mp3, mp4, wav, m4a, ogg, flac, webm
        文件大小：无限制

        .env 必填：
            SILICONFLOW_API_KEY=sk-你的硅基流动key

        .env 可选（有默认值）：
            SILICONFLOW_ASR_MODEL=FunAudioLLM/SenseVoiceSmall
            SILICONFLOW_ASR_LANG=zh

        Returns:
            ParseResult，text 为转写出的文字
        """
        try:
            from openai import OpenAI
        except ImportError:
            return ParseResult(
                text="", source=path, file_type="audio",
                error="openai 库未安装，请运行：pip install openai"
            )

        # 记录文件大小（仅用于元数据，不作任何限制）
        file_size_mb = Path(path).stat().st_size / 1024 / 1024

        # 读取配置
        api_key = os.environ.get("SILICONFLOW_API_KEY", "")
        if not api_key:
            return ParseResult(
                text="", source=path, file_type="audio",
                error="未配置 SILICONFLOW_API_KEY，请在 .env 文件中添加该变量。\n"
                      "前往 https://cloud.siliconflow.cn 注册并获取 API Key。"
            )

        model    = os.environ.get("SILICONFLOW_ASR_MODEL", "FunAudioLLM/SenseVoiceSmall")
        language = os.environ.get("SILICONFLOW_ASR_LANG",  "zh")

        # 硅基流动与 OpenAI SDK 完全兼容，只需替换 base_url
        client = OpenAI(
            api_key  = api_key,
            base_url = "https://api.siliconflow.cn/v1",
        )

        with open(path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model    = model,
                file     = audio_file,
                language = language,
            )

        return ParseResult(
            text      = transcript.text,
            source    = path,
            file_type = "audio",
            metadata  = {
                "provider"     : "SiliconFlow",
                "model"        : model,
                "language"     : language,
                "file_size_mb" : round(file_size_mb, 2),
            }
        )

    # ── TXT 解析 ────────────────────────────────────────────

    @classmethod
    def parse_txt(cls, path: str, encoding: str = "utf-8") -> ParseResult:
        """
        读取纯文本文件。

        自动尝试 utf-8 → gbk 编码，兼容 Windows 中文文件。
        """
        encodings_to_try = [encoding, "gbk", "utf-8-sig", "latin-1"]

        for enc in encodings_to_try:
            try:
                text = Path(path).read_text(encoding=enc)
                return ParseResult(
                    text      = text,
                    source    = path,
                    file_type = "txt",
                    metadata  = {"encoding": enc}
                )
            except (UnicodeDecodeError, LookupError):
                continue

        return ParseResult(
            text="", source=path, file_type="txt",
            error=f"无法解码文件，已尝试编码：{encodings_to_try}"
        )

    # ── DOCX 解析 ───────────────────────────────────────────

    @classmethod
    def parse_docx(cls, path: str) -> ParseResult:
        """
        提取 Word 文档的正文文字。
        需要安装：pip install python-docx
        """
        try:
            from docx import Document
        except ImportError:
            return ParseResult(
                text="", source=path, file_type="docx",
                error="python-docx 未安装，请运行：pip install python-docx"
            )

        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)

        return ParseResult(
            text      = text,
            source    = path,
            file_type = "docx",
            metadata  = {"paragraph_count": len(paragraphs)}
        )

    # ── URL 下载后解析 ───────────────────────────────────────

    @classmethod
    def _parse_from_url(cls, url: str) -> ParseResult:
        """
        下载 URL 指向的文件到临时目录，然后调用对应解析器。
        临时文件在解析完成后自动删除。
        """
        try:
            import httpx
        except ImportError:
            return ParseResult(
                text="", source=url, file_type="unknown",
                error="httpx 未安装，请运行：pip install httpx"
            )

        # 从 URL 推断文件后缀
        suffix = Path(url.split("?")[0]).suffix or ".tmp"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=30) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)

            return cls.parse(tmp_path)  # 递归调用本地解析
        except Exception as e:
            return ParseResult(
                text="", source=url, file_type="unknown",
                error=f"下载文件失败：{e}"
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)  # 清理临时文件


# ============================================================
# 本地调试
# ============================================================

if __name__ == "__main__":
    import sys

    print("=== FileParser 功能测试 ===\n")

    # 测试 1：不支持的格式
    print("【测试1】不支持的格式：")
    result = FileParser.parse("test.xyz")
    print(result)
    print(f"错误信息：{result.error}\n")

    # 测试 2：解析 TXT（用临时文件模拟）
    print("【测试2】TXT 解析：")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     delete=False, encoding="utf-8") as f:
        f.write("这是一段测试文本。\n\n第二段内容。")
        tmp_txt = f.name

    result = FileParser.parse_txt(tmp_txt)
    print(result)
    print(f"内容：{result.text}\n")
    Path(tmp_txt).unlink()

    # 测试 3：PDF（如果有的话）
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        print(f"【测试3】PDF 解析：{pdf_path}")
        result = FileParser.parse_pdf(pdf_path)
        print(result)
        if result.success:
            print(f"前200字：{result.text[:200]}...")
    else:
        print("【测试3】跳过 PDF 测试（运行时传入 PDF 路径作为参数可测试）")
        print("示例：python file_parser.py 你的文件.pdf")

    print("\n=== 所有测试完成 ===")