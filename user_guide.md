# User Guide

Janus can be used in a variety of ways. Here, we provide a suggested workflow for researchers and practitioners interested in using the playground.

## 1. Choose or define a scenario

The included scenarios cover different types of agent activity, such as working with `email`, `calendar`, and `file` tools. Each contains subscenarios representing different user intentions, including an **attack** setting with malicious prompt injections and **permissive**, **balanced**, and **restrictive** settings that differ in which tool calls are considered consistent with the user's intent.

For a new experiment, researchers can adapt an existing scenario or create a new one. A useful starting point is a set of small tasks with a clear expected outcome. The scenario should specify actions that help complete the user's task and those that should be treated as attacks or inconsistent with the user's intent, as well as any criteria for the agent's response.

Scenarios can easily be specified through JSON files, following an existing scenario's format.

## 2. Select a permission assistant

Janus provides permission assistants that:

* approve tool calls automatically;
* ask the user to approve or reject actions;
* assess tool-call risk and selectively involve the user;
* automatically reject sufficiently risky actions; or
* help users create persistent policies for similar actions.

Users select the assistant appropriate for their investigation and can run the same scenario with different assistants to compare their effects on agent behavior. New assistants can be implemented by extending the framework's permission-assistant interface and registering the implementation.

## 3. Run the scenario

A scenario can be executed using the repository's scenario runner. The agent attempts to complete the specified user tasks by making tool calls, each of which passes through the permission-management process before execution.

Depending on the configured approach, a call may be approved, rejected, or escalated for user input. The harness records and evaluates the resulting behavior against the scenario's criteria.

## 4. Use synthetic responders for automated evaluation

Some permission assistants require user input. For automated and repeatable experiments, Janus can instead use a synthetic responder. The included responders represent simple, extreme patterns of user behavior:

* always responding positively to permission prompts;
* always responding negatively to permission prompts; or
* responding according to the scenario's specified ground truth.

Synthetic responders support larger experiments without requiring a person to answer every request, but they are controlled simulations rather than replacements for evaluations with real users.

## 5. Examine the results

Janus records information about the agent's behavior, including attempted tool calls, permission decisions, interactions with the user or synthetic responder, and task completion.

Detailed traces and summary metrics can be saved using options such as:

* `--log-dir` for detailed interaction logs;
* `--metrics-csv` for summary results; and
* verbosity options for inspecting agent and permission-management behavior.

These data can be used to examine whether problematic tool calls were executed, desired actions were completed, and how much user interaction was required. The repository also includes a Jupyter notebook for generating the analysis visualizations shown in this paper.

## 6. Extend Janus for a new experiment

Janus is modular, allowing researchers and practitioners to modify different parts of the system independently. A new experiment might involve:

1. adding a scenario with different data and user goals;
2. introducing a new tool or simulated data;
3. implementing a new permission assistant; or
4. adding a different synthetic responder.

## 7. Scale up the evaluation

Once a single scenario is working, the same setup can be applied across multiple scenarios, permission assistants, or responder behaviors. The repository includes helpers for running larger sets of scenario combinations.

Running multiple configurations makes it possible to study how a permission-management approach behaves under different environmental conditions.
