from __future__ import annotations

import unittest

from cws.models import ApprovalRequest, ConversationRef
from cws.policy import ApprovalPolicy


class ApprovalPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ApprovalPolicy()
        self.conversation = ConversationRef("feishu", "default", "chat")
        self.workspace = "/tmp/project"

    def test_auto_approves_normal_git_inside_workspace(self) -> None:
        decision = self.policy.evaluate(
            ApprovalRequest("1", self.conversation, self.workspace, "item/commandExecution/requestApproval", command="git commit -m hi", cwd="/tmp/project")
        )
        self.assertEqual(decision.action, "auto-approve")

    def test_requires_human_for_dangerous_git(self) -> None:
        decision = self.policy.evaluate(
            ApprovalRequest("1", self.conversation, self.workspace, "item/commandExecution/requestApproval", command="git reset --hard", cwd="/tmp/project")
        )
        self.assertEqual(decision.action, "requires-human")

    def test_requires_human_outside_workspace(self) -> None:
        decision = self.policy.evaluate(
            ApprovalRequest("1", self.conversation, self.workspace, "item/commandExecution/requestApproval", command="pytest", cwd="/tmp/elsewhere")
        )
        self.assertEqual(decision.action, "requires-human")

    def test_auto_approves_safe_workspace_command(self) -> None:
        decision = self.policy.evaluate(
            ApprovalRequest("1", self.conversation, self.workspace, "item/commandExecution/requestApproval", command="pytest -q", cwd="/tmp/project")
        )
        self.assertEqual(decision.action, "auto-approve")

    def test_requires_human_for_unclassified_workspace_command(self) -> None:
        decision = self.policy.evaluate(
            ApprovalRequest(
                "1",
                self.conversation,
                self.workspace,
                "item/commandExecution/requestApproval",
                command="curl https://example.com",
                cwd="/tmp/project",
            )
        )
        self.assertEqual(decision.action, "requires-human")
