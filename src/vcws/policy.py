from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import ApprovalRequest


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str


DANGEROUS_GIT_PATTERNS = (
    "git reset --hard",
    "git clean -fd",
    "git clean -xdf",
    "git push --force",
    "git push -f",
    "git checkout --",
)

DANGEROUS_SHELL_PATTERNS = (
    "rm -rf",
    "sudo ",
    "chmod -R 777",
    "mkfs",
    "dd if=",
)

SAFE_WORKSPACE_COMMAND_PREFIXES = (
    "python ",
    "python3 ",
    "pytest",
    "uv ",
    "pip ",
    "pip3 ",
    "node ",
    "npm ",
    "pnpm ",
    "yarn ",
    "bun ",
    "make ",
    "cargo ",
    "go ",
    "ruby ",
    "bundle ",
    "rake ",
    "javac ",
    "java ",
    "gradle ",
    "./",
    "ls",
    "pwd",
    "cat ",
    "sed ",
    "grep ",
    "rg ",
    "find ",
)

NORMAL_GIT_PREFIXES = (
    "git status",
    "git add",
    "git commit",
    "git push",
    "git diff",
    "git log",
    "git branch",
    "git switch",
)


class ApprovalPolicy:
    def evaluate(self, request: ApprovalRequest) -> PolicyDecision:
        if request.method == "item/permissions/requestApproval":
            return PolicyDecision("requires-human", "permission escalation requires explicit confirmation")
        if request.method == "item/fileChange/requestApproval":
            return self._evaluate_file_change(request)
        return self._evaluate_command(request)

    def _evaluate_command(self, request: ApprovalRequest) -> PolicyDecision:
        command = (request.command or "").strip().lower()
        cwd = Path(request.cwd or request.workspace_path).resolve()
        workspace = Path(request.workspace_path).resolve()

        if command and any(command.startswith(pattern) for pattern in DANGEROUS_GIT_PATTERNS):
            return PolicyDecision("requires-human", "dangerous git command")
        if command and any(command.startswith(pattern) for pattern in DANGEROUS_SHELL_PATTERNS):
            return PolicyDecision("requires-human", "dangerous shell command")
        if self._is_within_workspace(cwd, workspace):
            if command.startswith("git "):
                if any(command.startswith(prefix) for prefix in NORMAL_GIT_PREFIXES):
                    return PolicyDecision("auto-approve", "normal git command in workspace")
                return PolicyDecision("requires-human", "non-standard git command in workspace")
            if any(command.startswith(prefix) for prefix in SAFE_WORKSPACE_COMMAND_PREFIXES):
                return PolicyDecision("auto-approve", "safe development command in workspace")
            return PolicyDecision("requires-human", "command in workspace is outside the safe auto-approve set")
        return PolicyDecision("requires-human", "command targets outside active workspace")

    def _evaluate_file_change(self, request: ApprovalRequest) -> PolicyDecision:
        workspace = Path(request.workspace_path).resolve()
        candidates = [path for path in [request.grant_root, *request.file_paths] if path]
        if not candidates:
            return PolicyDecision("requires-human", "file-change request omitted target paths")
        for candidate in candidates:
            if not self._is_within_workspace(Path(candidate).resolve(), workspace):
                return PolicyDecision("requires-human", "file change reaches outside active workspace")
        return PolicyDecision("auto-approve", "file changes stay inside active workspace")

    @staticmethod
    def _is_within_workspace(candidate: Path, workspace: Path) -> bool:
        try:
            candidate.relative_to(workspace)
            return True
        except ValueError:
            return False
