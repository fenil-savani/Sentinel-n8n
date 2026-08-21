# Destination Query Generator

The **Destination Query Generator Agent** is a multi-stage, LLM-orchestrated agent that converts a normalized source query representation into a syntactically and semantically correct destination query.

## Quick Start

```bash
# Run the pipeline
agentweave run --project destination-query-generator --product <product-name> --prompt "Your task description here"
```

## Project Structure

```
destination-query-generator/
├── project.yaml      # Project configuration
├── README.md         # This file
└── agents/
    ├── supervisor.md # Orchestration flow
    ├── generator.md  # Code generation
    └── reviewer.md   # Code review
```

## Customizing Agent Prompts

Edit the `.md` files in `agents/` to define your domain-specific logic.

**Important:** The framework automatically handles:
- Non-interactive mode (no questions)
- Tool call enforcement
- Code quality standards
- Review severity levels

You only need to add domain-specific instructions!

## Validation

Test your configuration:

```bash
agentweave validate --project destination-query-generator
```

## More Information

- [Usage Guide](../../../docs/USAGE_GUIDE.md)
- [Observability](../../../docs/OBSERVABILITY.md)
