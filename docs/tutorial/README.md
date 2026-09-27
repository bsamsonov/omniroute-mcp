# Code Walkthrough: MCP Server for OmniRoute

A walkthrough of the project based on its source code — for a developer
coming from backend work. Backend knowledge is assumed; MCP and async Python
are explained from scratch, through familiar analogies.

Chapters are meant to be read in order: each one builds on the previous.

| Chapter | Covers |
|---|---|
| [00. Overview](00_overview.md) | The project's goal, MCP explained for a backend developer, a map of the code |
| [01. Architecture](01_architecture.md) | Seven layers, the dependency rule, tracing a call |
| [02. Configuration and client](02_config_client.md) | Async Python in five minutes, translating errors for the model |
| [03. Formatting](03_formatting.md) | A layer with no backend equivalent: context hygiene |
| [04. MCP primitives](04_mcp_primitives.md) | tools / resources / prompts, the docstring as prompt |
| [05. Server and transports](05_server_transports.md) | Assembly, lifespan, stdio vs HTTP |
| [06. Tests and verification](06_testing.md) | Mocks, selftest, a live handshake |
| [07. Agents](07_agents.md) | Two server variants, the autonomous agent loop |
| [99. Glossary](99_glossary.md) | Terminology, a "where to find what" map, how to add a tool |

## If you're short on time

- To grasp **what MCP is** — chapters 00 and 04.
- To see **why the server is built this way** — chapters 01 and 03.
- To understand **how the autonomous agent works** — chapter 07.
- **To extend the code** — chapter 99 (the "How to add a new tool" section).

## Conventions

Code references use the `file:line` format and point to real locations in
the source — for example, `src/omniroute_mcp/client.py:56`. All code quotes
are taken from the project itself, not written for illustration.

The tone throughout is conversational, on purpose — this reads as a
conversation between two engineers, not a reference manual.
