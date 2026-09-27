"""MCP server for OmniRoute — a teaching example for a lecture on autonomous agents.

Reading order for the code (layers go strictly top to bottom by dependency):

    config.py      settings from the environment
    client.py      OmniRoute HTTP client (knows nothing about MCP)
    formatting.py  JSON -> compact text for the model
    tools.py       MCP tools     — actions, called by the model
    resources.py   MCP resources — data, fetched by the client
    prompts.py     MCP prompts   — scenarios, run by a human
    server.py      assembly and transport
"""

__version__ = "1.0.0"
