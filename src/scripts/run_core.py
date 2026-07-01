from pathlib import Path
from src.permissions.permission_manager import PermissionManager
from src.permissions.assistants import AVAILABLE_PERMISSION_ASSISTANTS
from dotenv import load_dotenv
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types
import asyncio
from rich import print
from src.agent import create_agent
import argparse
import uuid
from src.scripts.runner_common import DEFAULT_CONSTITUTION_FILE, resolve_risk_tolerance, risk_tolerance_arg
from src.tools.data_store import DEFAULT_DATA_SET, set_active_data_set
from src.utils.metrics import RunMetrics
from src.utils import run_logger

load_dotenv()

APP_NAME = "user_driven_agent_controls"
USER_ID = "user_12345"
SESSION_ID = "session_12345"

# Parse command line arguments
parser = argparse.ArgumentParser(description="User-driven agent controls core interactive runner")
parser.add_argument(
    "--permission-manager-verbose",
    action="store_true",
    help="Enable detailed permission evaluation logging",
)
parser.add_argument(
    "--permission-assistant",
    choices=AVAILABLE_PERMISSION_ASSISTANTS,
    default="policy_suggestion",
    help="Select which permission assistant implementation to use",
)
parser.add_argument(
    "--permission-assistant-verbose",
    action="store_true",
    help="Log intermediate permission assistant steps to the console",
)
parser.add_argument(
    "--agent-verbose",
    action="store_true",
    help="Enable verbose agent logging (e.g., tool calls and parameters).",
)
parser.add_argument(
    "--metrics-csv",
    default=None,
    help="Write run metrics to a CSV file at the end of the session",
)
parser.add_argument(
    "--task-assistant-risk-tolerance",
    type=risk_tolerance_arg,
    default=0.35,
    help="Risk tolerance (0-1) for the TaskPolicyPermissionAssistant approvals."
)
parser.add_argument(
    "--log-dir",
    default=None,
    help="Directory to write per-run conversation logs. If omitted, logs are not saved."
)
parser.add_argument(
    "--policy-file",
    default=None,
    help="Path to a policies.json file to load. If omitted, start with no policies.",
)
parser.add_argument(
    "--constitution-file",
    default=str(DEFAULT_CONSTITUTION_FILE),
    help=(
        "Path to a plain-English constitution file. "
        "Defaults to config/constitutions/default.md."
    ),
)
parser.add_argument(
    "--no-constitution-auto-approve",
    action="store_true",
    help=(
        "Disable the LLM-based auto-approver in the constitution assistant, "
        "so all unmatched calls escalate directly to the user."
    ),
)
args = parser.parse_args()

class CoreRunner:
    def __init__(self):
        self.session_service = None
        self.permission_mgr = None
        self.runner = None
        self.run_id = uuid.uuid4().hex
        self.log_path = None
        self.metrics = RunMetrics()
        risk_tolerance = resolve_risk_tolerance(
            args.permission_assistant,
            args.task_assistant_risk_tolerance,
        )
        self.metrics.set_run_context(
            permission_assistant=args.permission_assistant,
            risk_tolerance=risk_tolerance,
            run_id=self.run_id,
        )
        if args.log_dir:
            log_dir = Path(args.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = str(log_dir / f"run_{self.run_id}.log")
        run_logger.init(self.log_path)
        run_logger.log(f"Run {self.run_id} initialized.")
    
    async def process_query(self, query: str) -> str:
        """Process a single query and return the agent's response."""
        events = None
        try:
            content = types.Content(role="user", parts=[types.Part(text=query)])
            events = self.runner.run_async(
                new_message=content,
                user_id=USER_ID,
                session_id=SESSION_ID,
            )

            final_text = None
            async for event in events:
                if event.is_final_response():
                    final_text = event.content.parts[0].text
                    break

            if final_text is not None:
                return final_text
        except Exception as e:
            print(f"\n[red]Error during query processing: {str(e)}[/red]")
            return "I encountered an error processing your request. Please try again."
        finally:
            if events is not None:
                await events.aclose()

        return "I encountered an error processing your request. Please try again."

    async def start(self) -> None:
        """Start the interactive core runner."""
        try:
            # Create memory session
            self.session_service = InMemorySessionService()
            await self.session_service.create_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=SESSION_ID
            )

            set_active_data_set(DEFAULT_DATA_SET)

            # Create permission manager for the session
            policy_path = args.policy_file if args.policy_file else None
            self.permission_mgr = PermissionManager(
                policy_path,
                debug=args.permission_manager_verbose,
                assistant_name=args.permission_assistant,
                assistant_verbose=args.permission_assistant_verbose,
                metrics=self.metrics,
                assistant_risk_tolerance=args.task_assistant_risk_tolerance,
                constitution_file=args.constitution_file,
                constitution_use_auto_approve=not args.no_constitution_auto_approve,
            )
            
            agent_verbose = args.agent_verbose
            async with create_agent(
                self.permission_mgr,
                log_tool_calls=agent_verbose,
            ) as root_agent:
                # Create runner instance
                self.runner = Runner(
                    app_name=APP_NAME,
                    agent=root_agent,
                    session_service=self.session_service
                )
                
                print("[blue]Core runner session started. Type 'exit' to end the conversation.[/blue]")
                print("[blue]Type 'MANAGE_PERMISSIONS' to view and modify policies.[/blue]")
                print(f"[blue]Run ID: {self.run_id}[/blue]")
                
                while True:
                    try:
                        # Get user input
                        query = input("\n🧑 You: ").strip()
                        
                        # Handle special commands
                        if query.lower() in ['exit', 'quit']:
                            self._finalize_metrics()
                            print("\n[blue]Ending core runner session...[/blue]")
                            run_logger.log("Session ended by user command.")
                            break

                        if query.upper() == "MANAGE_PERMISSIONS":
                            # Delegate interactive policy management to the PermissionManager
                            await self.permission_mgr.interactive_policy_management()
                            continue

                        if not query:
                            continue

                        run_logger.log(f"USER: {query}")
                        self.metrics.increment_user()
                        await self.permission_mgr.handle_user_message(query)
                        # print("\n[blue]⏳ Processing...[/blue]\n")
                        response = await self.process_query(query)
                        if response is not None:
                            self.metrics.increment_agent()
                            run_logger.log(f"AGENT: {response}")
                        print("\nAgent:", response)
                        
                    except Exception as e:
                        print(f"\n[red]Error processing query: {str(e)}[/red]")
        
        except Exception as e:
            print(f"[red]Error in core runner: {str(e)}[/red]")
            self._finalize_metrics()

    def _finalize_metrics(self) -> None:
        summary_text = self.metrics.summary()
        print(f"\n[blue]{summary_text}[/blue]")
        run_logger.log("Run summary:\n" + summary_text)
        if args.metrics_csv:
            self.metrics.write_csv(args.metrics_csv)

async def _main_async():
  """Run CoreRunner.start() inside asyncio."""
  runner = CoreRunner()
  await runner.start()

def main():
  """Main entry point with proper asyncio handling."""
  try:
    asyncio.run(_main_async())
  except KeyboardInterrupt:
    print("\n[blue]Run interrupted. Metrics may be incomplete.[/blue]")
    print("\n[red]Core runner terminated by user.[/red]")
  except Exception as e:
    print(f"[red]Unexpected error: {str(e)}[/red]")

if __name__ == "__main__":
  main()
