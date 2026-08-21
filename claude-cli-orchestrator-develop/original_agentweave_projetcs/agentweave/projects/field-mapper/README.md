# Field Mapper

This project extracts the fields and its extractions from the respective parser files and does field mapping between the source and destination platforms.

## Quick Start

```bash
# Run the pipeline
agentweave run --project field-mapper --product <product-name> --prompt "Your task description here"
```

## Project Structure

```
field-mapper/
├── project.yaml      # Project configuration
├── README.md         # This file
└── agents/
    ├── supervisor.md # Orchestration flow
    ├── field_extractor.md # Field extraction
    └── field_mapper.md  # Field mapping
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
agentweave validate --project field-mapper
```

## More Information

- [Usage Guide](../../../docs/USAGE_GUIDE.md)
- [Observability](../../../docs/OBSERVABILITY.md)
