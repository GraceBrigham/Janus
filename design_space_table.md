| Category | System | Persistent policies: Leverages persistent global policies | Ability to act beyond persistent policies: Approve tool calls | Ability to act beyond persistent policies: Reject tool calls | Ability to act beyond persistent policies: Escalate to user | Level of user involvement: During runtime | Level of user involvement: During configuration | Grounding: Assesses against user task | Grounding: Assesses against other information | Grounding: Uses AI Model |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Base Cases** | Approves all |  | ● |  |  |  |  |  |  |  |
| **Base Cases** | Escalates all |  |  |  | ● | ● |  |  |  |  |
| **Base Cases** | Digital twin/Oracle |  | ● | ● |  |  |  | ● | ● |  |
| **Production Systems** | [ChatGPT Agent Mode](https://openai.com/index/introducing-chatgpt-agent/) | ● |  |  | ● | ● |  |  |  |  |
| **Production Systems** | [Claude Auto Mode](https://code.claude.com/docs/en/auto-mode-config) | ● | ● | ● |  |  | ○ | ● | ● | ● |
| **Production Systems** | [IronCurtain](https://www.provos.org/p/ironcurtain-secure-personal-assistant/) | ● | ● | ● | ● | ○ | ○ | ● |  | ● |
| **Implemented Prototypes** | risk_assessment | ○ | ● |  | ● | ○ |  | ● |  | ● |
| **Implemented Prototypes** | risk_assessment_autonomous | ○ | ● | ● |  |  |  | ● |  | ● |
| **Implemented Prototypes** | auto_approve | ○ | ● |  |  |  |  |  |  |  |
| **Implemented Prototypes** | user_confirmation | ○ |  |  | ● | ● |  |  |  |  |
| **Implemented Prototypes** | constitution | ● | ● |  | ● | ○ | ○ | ● |  | ● |
| **Implemented Prototypes** | policy_suggestion | ● |  |  | ● | ● |  |  |  |  |
| **Example Academic Proposals** | [CaMeL](https://github.com/google-research/camel-prompt-injection) | ● | ● | ● |  |  | ○ | ● | ● | ● |
| **Example Academic Proposals** | [Conseca](https://sigops.org/s/conferences/hotos/2025/papers/hotos25-100.pdf) |  | ● | ● | ○ | ○ |  | ● | ● | ● |
| **Example Academic Proposals** | [Progent](https://huggingface.co/papers/2504.11703) | ● | ● | ● | ○ | ○ | ● |  |  | ● |
| **Example Academic Proposals** | [Wu et al.](https://github.com/llm-platform-security/ai-agent-permissions) | ● | ● | ● |  |  | ● |  | ● | ● |
