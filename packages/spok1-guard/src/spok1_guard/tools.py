"""Classify one Claude Code tool call (built-in or MCP) into a risk tier."""

from __future__ import annotations

import fnmatch
import posixpath

from spok1_guard._compat import TYPE_CHECKING
from spok1_guard.commands import Ctx, classify_bash, classify_pwsh, read_target, write_target
from spok1_guard.paths import PathContext
from spok1_guard.tiers import T0_OK, Tier, Verdict, v

if TYPE_CHECKING:
    from typing import Any

SAFE_TOOLS = {
    "Read",
    "Glob",
    "Grep",
    "LS",
    "WebFetch",
    "WebSearch",
    "Agent",
    "Task",
    "TodoWrite",
    "TodoRead",
    "AskUserQuestion",
    "Skill",
    "ToolSearch",
    "ExitPlanMode",
    "EnterPlanMode",
    "TaskStop",
    "TaskOutput",
    "TaskGet",
    "TaskList",
    "TaskCreate",
    "TaskUpdate",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "ReadMcpResourceDirTool",
    "SendMessage",
    "ListAgents",
    "McpTaskCheck",
    "EnterWorktree",
    "ExitWorktree",
    "PushNotification",
    "LSP",
    "BashOutput",
    "KillShell",
    "SlashCommand",
    "ReportFindings",
    "StructuredOutput",
}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
T2_TOOLS = {
    "CronCreate": "creates a scheduled job that uses Claude",
    "ScheduleWakeup": "schedules a future Claude run",
    "RemoteTrigger": "starts a remote agent run",
    "Artifact": "publishes a hosted page",
    "ArtifactData": "writes an artifact's shared database",
    "ArtifactComments": "posts artifact comments",
    "DesignSync": "changes a design system",
    "SendToProject": "sends content to a project",
    "SendToProjectContext": "changes a project's context",
    "CronDelete": "deletes a scheduled job",
}

MCP_READ_WORDS = (
    "get",
    "list",
    "search",
    "read",
    "fetch",
    "view",
    "query",
    "find",
    "describe",
    "show",
    "status",
    "check",
    "help",
    "guide",
    "count",
    "export",
    "lookup",
    "resolve",
    "download",
    "inspect",
)
MCP_DELETE_WORDS = (
    "delete",
    "remove",
    "destroy",
    "purge",
    "revoke",
    "drop",
    "wipe",
    "erase",
    "unpublish",
    "disconnect",
    "archive",
)


def classify_mcp(tool_name: str, ctx: Ctx) -> Verdict:
    lowered = tool_name.lower()
    for pattern, tier_name in ctx.paths.policy.mcp_tiers:
        if fnmatch.fnmatch(lowered, pattern.lower()):
            tier = Tier[tier_name] if tier_name in Tier.__members__ else Tier.T2
            return v(tier, f"MCP tool {tool_name} (policy: {pattern})", "mcp.policy")
    parts = tool_name.split("__")
    action = parts[-1].lower() if len(parts) >= 3 else lowered
    words = action.replace("-", "_").split("_")
    if any(w in MCP_DELETE_WORDS for w in words):
        return v(Tier.T3, f"MCP tool {tool_name} deletes something", "mcp.delete")
    if words and words[0] in MCP_READ_WORDS:
        return v(Tier.T0, f"MCP tool {tool_name} reads data", "mcp.read")
    return v(Tier.T2, f"MCP tool {tool_name} changes or sends something", "mcp.write")


def classify_tool(tool_name: str, tool_input: dict[str, Any], paths: PathContext) -> Verdict:
    ctx = Ctx(paths, paths.cwd)
    if tool_name == "Bash":
        return classify_bash(str(tool_input.get("command", "")), ctx)
    if tool_name == "PowerShell":
        return classify_pwsh(str(tool_input.get("command", "")), ctx)
    if tool_name == "Monitor":
        command = tool_input.get("command")
        return classify_bash(str(command), ctx) if command else T0_OK
    if tool_name in WRITE_TOOLS:
        target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if not target:
            return v(Tier.T2, f"{tool_name} without a file path", "path.unknown")
        return write_target(target, ctx, "edits")
    if tool_name in ("Read", "Glob", "Grep", "LS"):
        verdicts: list[Verdict] = []
        for key in ("file_path", "path"):
            value = tool_input.get(key)
            if value:
                verdicts.append(read_target(str(value), ctx))
        for key in ("pattern", "glob"):
            value = tool_input.get(key)
            if value and not (key == "pattern" and tool_name == "Grep"):  # Grep pattern is a regex
                name = posixpath.basename(str(value).replace("\\", "/"))
                if name in (
                    ".env",
                    "*.env",
                    ".env*",
                    "*.pem",
                    "*.key",
                    "id_rsa*",
                    ".credentials.json",
                ):
                    verdicts.append(
                        v(
                            Tier.FORBIDDEN,
                            f"{tool_name} targets secret files ({value})",
                            "secret.read",
                        )
                    )
        return max(verdicts, key=lambda x: x.tier) if verdicts else T0_OK
    if tool_name in SAFE_TOOLS:
        return T0_OK
    if tool_name in T2_TOOLS:
        return v(Tier.T2, f"{tool_name} {T2_TOOLS[tool_name]}", "tool.t2")
    if tool_name.startswith("mcp__"):
        return classify_mcp(tool_name, ctx)
    return v(Tier.T2, f"unrecognised tool '{tool_name}'", "tool.unknown")
