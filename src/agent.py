# File agent_setup.py

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
from src.permissions.permission_manager import PermissionManager
import json
import logging
import uuid
from rich import print

# Configure logging
logger = logging.getLogger(__name__)

from src.tools.tool_registry import ToolRegistry

from src.tools.email_tools import list_emails, search_emails, get_email, send_email, delete_email
from src.tools.calendar_tools import (
    list_calendar_events,
    check_calendar_availability,
    create_calendar_event,
    get_calendar_event,
    add_calendar_participants,
    add_calendar_participant,
)
from src.tools.file_tools import list_files, get_file, delete_file
from src.utils import run_logger

DEFAULT_AGENT_NAME = "workplace_agent"

# Track the active agent name for the current run.
_agent_name = DEFAULT_AGENT_NAME

# Global permission manager instance
permission_manager: Optional[PermissionManager] = None
tool_call_logging: bool = False
tool_call_evaluator = None

@asynccontextmanager
async def create_agent(
    perm_mgr: PermissionManager,
    log_tool_calls: bool = False,
    evaluator=None,
    agent_name: Optional[str] = None,
):
    """Create and configure the agent with permission manager"""
    global permission_manager, tool_call_logging, tool_call_evaluator, _agent_name
    permission_manager = perm_mgr
    tool_call_logging = log_tool_calls
    tool_call_evaluator = evaluator

    # Assign a unique agent name per run.
    _agent_name = agent_name or f"{DEFAULT_AGENT_NAME}_{uuid.uuid4().hex[:8]}"
    
    agent = create_tools()
    try:
        yield agent
    finally:
        permission_manager = None

async def check_tool_permission(
    tool: FunctionTool,
    args: Dict[str, Any],
    tool_context: ToolContext
) -> Optional[dict]:
    """Check if the tool execution is permitted by policies."""
    # Create subject context
    subject = {
        "type": "agent",
        "name": _agent_name
    }
    
    # Get tool information from metadata
    metadata = tool.custom_metadata
    if not metadata or "tool_name" not in metadata or "action" not in metadata:
        logger.error(f"Tool missing required metadata: {tool}")
        return {"error": "Tool configuration error: missing required metadata"}
        
    tool_name = metadata["tool_name"]
    action = metadata["action"]

    should_log_tool_call = tool_call_logging or run_logger.path() is not None
    if should_log_tool_call:
        formatted_args = json.dumps(args, default=str, ensure_ascii=True, sort_keys=True)
        if tool_call_logging:
            print(f"\n[cyan][Tool call][/cyan] {tool_name}.{action} {formatted_args}")
        if run_logger.path() is not None:
            run_logger.log(f"TOOL CALL: {tool_name}.{action} {formatted_args}")

    # Check permission and handle interactive flow
    if permission_manager is None:
        logger.error("Permission manager is not initialized during tool callback")
        return {"error": "Permission system unavailable; tool call blocked"}

    try:
        is_permitted, reason = await permission_manager.check_permission(
            subject,
            tool_name,
            action,
            args
        )
    except Exception as exc:
        logger.exception("Permission check failed")
        return {"result": f"Permission check failed: {exc}"}
    
    if not is_permitted:
        return {"result": reason if reason else "Action not permitted by current policies"}
    
    if tool_call_evaluator is not None:
        tool_call_evaluator.record(tool_name, action, args)

    if reason:
        # Permission granted with a message (e.g., policy created)
        logger.info(f"Tool permission granted: {reason}")
    return None

def create_tools() -> FunctionTool:
    """Create all tool functions with metadata from registry and return the root agent"""
    registry = ToolRegistry()
    
    # Email tools with metadata
    list_emails_tool = registry.create_tool("email", "list_emails", list_emails)
    search_emails_tool = registry.create_tool("email", "search_emails", search_emails)
    get_email_tool = registry.create_tool("email", "get_email", get_email)
    send_email_tool = registry.create_tool("email", "send_email", send_email)
    delete_email_tool = registry.create_tool("email", "delete_email", delete_email)
    
    # Calendar tools with metadata
    list_calendar_tool = registry.create_tool("calendar", "list_calendar_events", list_calendar_events)
    check_availability_tool = registry.create_tool("calendar", "check_calendar_availability", check_calendar_availability)
    create_calendar_tool = registry.create_tool("calendar", "create_calendar_event", create_calendar_event)
    get_calendar_tool = registry.create_tool("calendar", "get_calendar_event", get_calendar_event)
    add_participants_tool = registry.create_tool("calendar", "add_calendar_participants", add_calendar_participants)
    add_participant_tool = registry.create_tool("calendar", "add_calendar_participant", add_calendar_participant)
    
    # File tools with metadata
    list_files_tool = registry.create_tool("file", "list_files", list_files)
    get_file_tool = registry.create_tool("file", "get_file", get_file)
    delete_file_tool = registry.create_tool("file", "delete_file", delete_file)
    
    # Create model
    model = LiteLlm(model="openai/o3-mini")
    
    # Create the agent using LiteLLM
    base_instruction = """You are a workplace assistant that can help with various office tasks:

Execution and data-grounding rules:
   - Do not invent recipients, filenames, dates, or tasks.
   - Only use values obtained from user input or tool outputs in this run.
   - When asked to send to multiple people separately, issue separate email:send calls (one call per recipient).
   - Before any email:send with a file attachment, retrieve each attached file first with file:get.

1. Email Management:
   - Use email:list to list emails (optionally unread)
   - Use email:search to find emails from a sender
   - Use email:get to retrieve a specific email
   - Use email:send to send new emails
   - When attaching files, set `attachments` to a list of file path strings (e.g., `attachments=["client-deck.pptx"]`)
   - Use email:delete to delete an email

2. Calendar Management:
   - Use calendar:list to view events on a date
   - Use calendar:check_availability to check a time slot
   - Use calendar:create to schedule events
   - Use calendar:get to retrieve an event by title
   - Use calendar:add_participants / calendar:add_participant to add attendees

3. File Management:
   - Use file:list to list files
   - Use file:get (path or id) to retrieve file contents/metadata or prep attachments
   - Before summarizing or emailing a document, call file:get with that document's path (e.g., file:get(path="client-meeting-minutes.docx")) to ensure you have the latest contents and attachment reference
   - Use file:delete to delete a file"""

    root_agent = LlmAgent(
        model=model,
        name=_agent_name,
        description="A helpful workplace assistant with email, calendar, and file management tools",
        instruction=base_instruction,
        tools=[
            # Email tools
            list_emails_tool,
            search_emails_tool,
            get_email_tool,
            send_email_tool,
            delete_email_tool,
            # Calendar tools
            list_calendar_tool,
            check_availability_tool,
            create_calendar_tool,
            get_calendar_tool,
            add_participants_tool,
            add_participant_tool,
            # File tools
            list_files_tool,
            get_file_tool,
            delete_file_tool
        ],
        before_tool_callback=check_tool_permission,
    )
    
    return root_agent
