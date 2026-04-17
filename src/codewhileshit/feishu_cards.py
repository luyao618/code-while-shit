from __future__ import annotations

from typing import Any

from .channels import ApprovalPrompt
from .models import ApprovalCardStatus, ConversationRef, ProgressMilestone, ProgressUpdate


def _build_progress_card(update: ProgressUpdate) -> dict[str, Any]:
    title, template, badge = _progress_style(update.milestone)
    elements = [
        {"tag": "markdown", "content": f"**{badge} {update.summary}**"},
    ]
    if update.detail:
        elements.append({"tag": "markdown", "content": update.detail})
    elements.append({"tag": "markdown", "content": "_该进度消息会在同一轮执行中持续更新。_"})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": template,
        },
        "elements": elements,
    }


def _progress_style(milestone: ProgressMilestone) -> tuple[str, str, str]:
    mapping: dict[ProgressMilestone, tuple[str, str, str]] = {
        "accepted": ("已收到请求", "blue", "📨"),
        "running": ("正在处理", "wathet", "⏳"),
        "waiting_approval": ("等待确认", "orange", "⚠️"),
        "waiting_input": ("等待补充信息", "orange", "📝"),
        "completed": ("已完成", "green", "✅"),
        "failed": ("执行失败", "red", "❌"),
    }
    return mapping[milestone]


def _build_approval_card(
    prompt: ApprovalPrompt,
    *,
    conversation: ConversationRef,
    status: ApprovalCardStatus,
    detail: str | None = None,
) -> dict[str, Any]:
    title, template, badge = _approval_style(status)
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": f"**{badge} {prompt.title}**"},
        {"tag": "markdown", "content": prompt.prompt},
    ]
    if prompt.reason:
        elements.append({"tag": "markdown", "content": f"**触发原因**\n{prompt.reason}"})
    if prompt.command:
        elements.append({"tag": "markdown", "content": f"**命令**\n```bash\n{prompt.command}\n```"})
    if prompt.cwd:
        elements.append({"tag": "markdown", "content": f"**工作目录**\n`{prompt.cwd}`"})
    if prompt.method:
        elements.append({"tag": "markdown", "content": f"**审批类型**\n`{prompt.method}`"})
    if detail:
        elements.append({"tag": "markdown", "content": f"**处理结果**\n{detail}"})
    if status == "pending":
        approve_value = _approval_action_value(prompt, conversation=conversation, decision="approve")
        deny_value = _approval_action_value(prompt, conversation=conversation, decision="deny")
        elements.extend(
            [
                {"tag": "markdown", "content": "_确认后会继续当前 Codex 执行；拒绝会终止本次敏感操作。_"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "继续执行"},
                            "type": "primary",
                            "value": approve_value,
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "拒绝本次操作"},
                            "value": deny_value,
                        },
                    ],
                },
            ]
        )
    else:
        elements.append({"tag": "markdown", "content": "_该审批卡片已结束，不会再次触发相同操作。_"})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"content": title, "tag": "plain_text"}, "template": template},
        "elements": elements,
    }


def _approval_action_value(
    prompt: ApprovalPrompt,
    *,
    conversation: ConversationRef,
    decision: str,
) -> dict[str, Any]:
    return {
        "request_id": prompt.request_id,
        "decision": decision,
        "conversation_id": conversation.conversation_id,
        "account_id": conversation.account_id,
        "thread_id": conversation.thread_id,
        "codex_thread_id": prompt.codex_thread_id,
        "codex_turn_id": prompt.codex_turn_id,
        "codex_item_id": prompt.codex_item_id,
    }


def _approval_style(status: ApprovalCardStatus) -> tuple[str, str, str]:
    mapping: dict[ApprovalCardStatus, tuple[str, str, str]] = {
        "pending": ("需要确认的操作", "orange", "⚠️"),
        "approved": ("已确认，继续执行", "green", "✅"),
        "denied": ("已拒绝本次操作", "red", "⛔"),
        "expired": ("确认已过期", "grey", "⌛"),
        "duplicate": ("该操作已处理", "grey", "ℹ️"),
        "error": ("处理失败", "red", "❌"),
    }
    return mapping[status]
