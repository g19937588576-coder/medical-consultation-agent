"""问诊记录导出 PDF（reportlab，内置中文 CID 字体）。"""
from __future__ import annotations

import io
import json
from datetime import datetime

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

TRIAGE_TEXT = {"green": "🟢 可自行观察", "yellow": "🟡 建议尽快就医", "red": "🔴 需立即急诊"}


def build_pdf(session, messages) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Title"], fontName="STSong-Light", fontSize=18, alignment=TA_CENTER)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="STSong-Light", fontSize=13, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="STSong-Light", fontSize=10.5, leading=16)
    note = ParagraphStyle("note", parent=styles["BodyText"], fontName="STSong-Light", fontSize=8.5, textColor="#666666")

    story = [
        Paragraph("医疗问诊记录", title),
        Spacer(1, 6),
        Paragraph(f"会话：{session.title}　|　导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}", note),
        Spacer(1, 4),
        Paragraph("说明：本记录由 AI 健康咨询助手生成，仅供个人参考，不能替代医生诊断。", note),
        HRFlowable(width="100%", thickness=1, color="#cccccc"),
    ]

    for m in messages:
        role = "用户" if m.role == "user" else "助手"
        story.append(Paragraph(f"【{role}】", h2))
        content = m.content.replace("\n", "<br/>")
        story.append(Paragraph(content, body))
        if m.role == "assistant":
            if m.triage_level:
                story.append(Paragraph(f"分诊建议：{TRIAGE_TEXT.get(m.triage_level, m.triage_level)}", body))
            try:
                citations = json.loads(m.citations_json or "[]")
            except Exception:
                citations = []
            if citations:
                story.append(Spacer(1, 4))
                story.append(Paragraph("参考资料：", h2))
                for i, c in enumerate(citations, 1):
                    story.append(
                        Paragraph(
                            f"{i}. {c.get('title', '')} —— {c.get('source', '')} {c.get('url', '')}",
                            note,
                        )
                    )
        story.append(Spacer(1, 8))

    story.append(HRFlowable(width="100%", thickness=1, color="#cccccc"))
    story.append(Spacer(1, 6))
    story.append(Paragraph("免责声明：以上内容仅为健康信息参考，不能替代执业医师的面诊、诊断与治疗建议。如有不适请及时就医。", note))
    doc.build(story)
    return buf.getvalue()
