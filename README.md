# Janus: A Playground for User-Involved Agentic Permission Management

Janus is the companion codebase for our academic work on user-involved permission management for tool-using agents. It provides an interactive runner, an evaluation harness, scenario definitions, and multiple interchangeable permission-assistant designs for studying the tradeoff between autonomy, safety, and user involvement.

![Janus system overview](assets/SystemOverview.png)

## Artifact Overview

This repository contains the complete Janus artifact: source code, scenario definitions, evaluation harness, and analysis materials. The same project is maintained on GitHub for active development and archived on Zenodo for stable citation and long-term access.

- Zenodo record: https://zenodo.org/records/22800573
- GitHub repository: https://github.com/GraceBrigham/Janus

The project includes:

- the source code and core logic in [src](src)
- the scenarios and evaluation data in [scenarios/definitions](scenarios/definitions)
- the analysis notebook in [analysis/analysis.ipynb](analysis/analysis.ipynb)
- the evaluation harness entrypoints in [src/scripts](src/scripts)
- the dependency metadata in [pyproject.toml](pyproject.toml) and [uv.lock](uv.lock)
- generated metrics and logs in [metrics](metrics) and output directories such as `runs/*`
- additional documentation:
  - [design_space_table.md](design_space_table.md): permission management design space table
  - [evaluation_scenarios.md](evaluation_scenarios.md): full definitions of the evaluation tasks, user profiles, attack cases, and alignment criteria used in the experiments
  - [architecture.md](architecture.md): architecture overview, execution flow, and assistant-by-assistant design notes

## Setup

### Requirements

- Python 3.13+
- `uv`

### Install

```bash
uv sync
```

Dependencies are managed in [pyproject.toml](pyproject.toml) and locked in `uv.lock`.

### Environment

The runners call `load_dotenv()`, so a local `.env` file is supported. A checked-in template is provided at [.env.example](.env.example).

To configure the environment:

```bash
cp .env.example .env
# then set OPENAI_API_KEY in .env
```

Minimum `.env` for the default model configuration (`openai/o3-mini`):

```bash
OPENAI_API_KEY=...
```

What each variable is for:

- `OPENAI_API_KEY` required for the default agent model in [`src/agent.py`](src/agent.py#L145), the default harness judge model in [`src/scripts/run_harness.py`](src/scripts/run_harness.py#L195), and the LLM-backed permission assistants.
- `TOOL_DATA_SET` optional override for the default seed data set loaded by the in-memory email/calendar/file tools. If unset, the repo uses `default`. See [`src/tools/data_store.py`](src/tools/data_store.py#L11).

Example `.env`:

```bash
# Required for all default runs
OPENAI_API_KEY=your_openai_api_key

# Optional: choose a default seed data set when not running a scenario harness
# TOOL_DATA_SET=default
```

Notes:

- You do not need separate email, calendar, or file-service credentials. Those tools use scenario seed data stored in the repo, not live external services.
- If you change `--judge-model` or the hard-coded LiteLLM model strings to a non-OpenAI provider, you may also need that provider's API key variables as required by LiteLLM.

## Running Janus-Core

`run_core` is the interactive runner. Use it when you want to talk to the agent directly, inspect permission behavior live, or manage policies manually.

Example:

```bash
uv run python -m src.scripts.run_core \
  --permission-assistant risk_assessment
```

Useful flags:

- `--permission-assistant {auto_approve,constitution,policy_suggestion,risk_assessment,risk_assessment_autonomous,user_confirmation}`
- `--permission-manager-verbose`
- `--permission-assistant-verbose`
- `--agent-verbose`
- `--task-assistant-risk-tolerance 0.35`
- `--policy-file path/to/policies.json`
- `--log-dir logs/core`
- `--metrics-csv metrics/core.csv`
- `--constitution-file config/constitutions/default.md`
- `--no-constitution-auto-approve`

Interactive commands:

- `exit` / `quit`: end the session
- `MANAGE_PERMISSIONS`: open interactive policy management

## Running Janus-Harness

`run_harness` is the evaluation entrypoint. It expands selected scenarios, subscenarios, permission assistants, responder modes, risk tolerances, and repetitions into concrete runs.

With `openai/o3-mini`, each individual harness run cost under $0.10 in our testing.

Each run gets:

- a unique `run_id`
- a log file at `logs/run_<run_id>.log` under the selected output directory
- a metrics file at `metrics/run_<run_id>.csv` under the selected output directory

### Single run

```bash
uv run python -m src.scripts.run_harness \
  --scenarios 1 \
  --subscenarios attack \
  --permission-assistants risk_assessment \
  --risk-tolerances 0.35 \
  --output-dir runs/testing \
  --agent-verbose
```

Useful flags:

- `--scenarios 1,2,3` or `--scenarios all`
- `--subscenarios attack,balanced` or `--subscenarios all`
- `--permission-assistants ...` or `--permission-assistants all`
- `--synthetic-responder`
- `--synthetic-responder-modes {always_yes,always_no,alignment_aware}` or `all`
- `--risk-tolerances 0.2,0.7`
- `--repetitions 5`
- `--output-dir runs/my_experiment`
- `--permission-manager-verbose`
- `--permission-assistant-verbose`
- `--agent-verbose`
- `--policy-file path/to/policies.json`
- `--judge-model openai/o3-mini`
- `--max-followups 5`
- `--constitution-file config/constitutions/default.md`
- `--no-constitution-auto-approve`

Synthetic responder modes:

- `always_yes`: always approves permission prompts
- `always_no`: always rejects permission prompts
- `alignment_aware`: rejects attack/out-of-alignment calls and approves others

Scenario definitions live under [scenarios/definitions](scenarios/definitions). Combined definitions include tool seed data plus `eval` sections for desired, out-of-alignment, and attack tool-call expectations.

### Reproducing paper results

The full matrix reproduces the complete evaluation sweep used in the paper and is the recommended command for a complete end-to-end reproduction.

```bash
uv run python -m src.scripts.run_harness \
  --scenarios all \
  --subscenarios all \
  --permission-assistants auto_approve,policy_suggestion,user_confirmation,constitution,risk_assessment,risk_assessment_autonomous \
  --synthetic-responder \
  --synthetic-responder-modes all \
  --risk-tolerances 0.2,0.7 \
  --repetitions 5 \
  --output-dir runs/full_eval
```

This can also be run in smaller batched runs when runtime, rate limits, or machine constraints make the full run impractical. The harness supports partial runs by restricting any of the matrix dimensions and then combining the outputs in the analysis notebook.

Examples of batched runs:

```bash
# scenario subset only
uv run python -m src.scripts.run_harness \
  --scenarios 1 \
  --subscenarios all \
  --permission-assistants auto_approve,policy_suggestion,risk_assessment \
  --synthetic-responder-modes all \
  --risk-tolerances 0.2,0.7 \
  --repetitions 2 \
  --output-dir runs/scenario_1_chunk
```

```bash
# assistant subset only
uv run python -m src.scripts.run_harness \
  --scenarios all \
  --subscenarios attack \
  --permission-assistants constitution,risk_assessment \
  --synthetic-responder-modes alignment_aware \
  --risk-tolerances 0.35 \
  --repetitions 3 \
  --output-dir runs/assistant_chunk
```

```bash
# reduced repetitions for a quick validation pass
uv run python -m src.scripts.run_harness \
  --scenarios all \
  --subscenarios balanced \
  --permission-assistants all \
  --synthetic-responder-modes all \
  --risk-tolerances 0.2,0.7 \
  --repetitions 1 \
  --output-dir runs/quick_validation
```

## Analysis

Analysis artifacts live under [analysis](analysis).

Start with:

- [analysis.ipynb](analysis/analysis.ipynb)

Use the per-run metrics written by `run_harness` as notebook inputs.

To launch the notebook environment locally:

```bash
uv run jupyter lab
```
