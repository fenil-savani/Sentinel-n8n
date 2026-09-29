# Source Analyzer

Analyzes raw source queries from observability platforms, extracts structural elements (fields, commands, functions), generates semantic understanding (summary, intent, use case), and produces Enriched Normalized JSON for the migration pipeline.

## Quick Start

```bash
# Run the pipeline
agentweave run --project source-analyzer --product <product-name> --prompt "Your task description here"
```

## Project Structure

```
source-analyzer/
├── project.yaml          # Project configuration
├── mcp.json              # MCP server configuration (context-engine)
├── README.md             # This file
├── agents/
│   ├── supervisor.md     # Orchestration flow
│   └── analyzer.md       # Source query analysis, extraction, and semantic generation
└── .claude/
    └── skills/
        ├── splunk/
        │   └── SKILL.md      # Splunk SPL extraction and semantic skills
        └── dynatrace/
            └── SKILL.md      # Dynatrace DQL extraction and semantic skills
```

## Customizing Agent Prompts

Edit the `.md` files in `agents/` to define your domain-specific logic.

**Important:** The framework automatically handles:
- Non-interactive mode (no questions)
- Tool call enforcement
- Code quality standards

You only need to add domain-specific instructions!

## Validation

Test your configuration:

```bash
agentweave validate --project source-analyzer
```

## More Information

- [Usage Guide](../../../docs/USAGE_GUIDE.md)
- [Observability](../../../docs/OBSERVABILITY.md)
