# Migration Report -- source-analyzer

- Generated:   2026-04-23T17:00:21+00:00
- Source:      `C:\splunk_secops_migration\agentic_arc_1\agentic-migration-tool\agentweave\projects\analyzer`
- Destination: `C:\splunk_secops_migration\agentic_arc_1\agentic-migration-tool\migrated_projects\analyzer`


## Summary

**Status:** **COMPLETED WITH TODOs** -- review flagged items

- Pattern detected:  **A**
- LLM confidence:    0.97
- Heuristic hint:    A (confidence 0.90)
- Validation errors: 0
- Validation warns:  0
- Extractor cost:    $0.0904 (1 turns)

## Files Copied

- Agents: 2 file(s) copied from `agents/`
  - analyzer-v1.md
  - analyzer.md
- mcp.json:      yes
- product_docs/: not present in source
- skills/:       copied

**Scaffold warnings:**
- Copied skills/ from source but no subdirectory matches an agent name. The orchestrator expects skills/<agent_name>/SKILL.md. Manual restructure likely needed.

## Heuristic Pattern Classification

- Pattern:    **A**
- Confidence: 0.90
- Iteration hint: None

Signals:
- num_subagents=1
- pattern_B_hits=0
- pattern_C_hits=0
- single subagent, no loop keywords

## LLM Workflow Extraction

- Pattern:    **A**
- Confidence: 0.97
- Schema valid: True

**Extractor notes:**
- Pattern A confirmed: exactly one subagent (analyzer), no loop or retry logic in supervisor.
- passthrough_fields is [] because the supervisor explicitly states 'The output.json contains ONLY the 12 analyzer-generated fields' and all 12 fields (id, query, parsed_json, fields, commands, functions, metadata, usage_type, datasource, summary, intent, use_case) are produced or passed-through by the analyzer itself — the supervisor does not independently copy any input fields into output.json; it writes only what the analyzer returns after validation.
- SCHEMA DISCREPANCY: The analyzer output schema defines fields.meta_fields (a dict) as a third key inside the fields object, but the supervisor's Phase 2 validation table and Phase 3 output template omit meta_fields entirely. The supervisor enforces only reserved_fields and custom_fields. Assembler should be aware the analyzer may emit meta_fields which the supervisor strips on write.
- The supervisor performs field remediation (defaulting nulls) before writing output.json — this is inline validation logic, not a separate subagent step.
- CONDITIONAL LOGIC: The supervisor contains a conditional skip rule ('If a delegated subagent produces no output, treat the phase as SKIPPED') which the target engine's DAG cannot express declaratively.
- The id field is handled with a fallback: if absent from state, the supervisor substitutes 'unknown' before passing to the analyzer. This substitution logic is not representable as a declarative field mapping.

### Raw extraction JSON

```json
{
  "pattern": "A",
  "confidence": 0.97,
  "max_iterations": null,
  "steps": [
    {
      "id": "analyzer",
      "agent": "analyzer",
      "depends_on": [],
      "max_retries": null,
      "exit_condition": null,
      "feedback_field": null,
      "confidence": 0.97
    }
  ],
  "pipeline_exit_condition": null,
  "pipeline_feedback": null,
  "passthrough_fields": [],
  "passthrough_confidence": 0.92,
  "notes": [
    "Pattern A confirmed: exactly one subagent (analyzer), no loop or retry logic in supervisor.",
    "passthrough_fields is [] because the supervisor explicitly states 'The output.json contains ONLY the 12 analyzer-generated fields' and all 12 fields (id, query, parsed_json, fields, commands, functions, metadata, usage_type, datasource, summary, intent, use_case) are produced or passed-through by the analyzer itself \u2014 the supervisor does not independently copy any input fields into output.json; it writes only what the analyzer returns after validation.",
    "SCHEMA DISCREPANCY: The analyzer output schema defines fields.meta_fields (a dict) as a third key inside the fields object, but the supervisor's Phase 2 validation table and Phase 3 output template omit meta_fields entirely. The supervisor enforces only reserved_fields and custom_fields. Assembler should be aware the analyzer may emit meta_fields which the supervisor strips on write.",
    "The supervisor performs field remediation (defaulting nulls) before writing output.json \u2014 this is inline validation logic, not a separate subagent step.",
    "CONDITIONAL LOGIC: The supervisor contains a conditional skip rule ('If a delegated subagent produces no output, treat the phase as SKIPPED') which the target engine's DAG cannot express declaratively.",
    "The id field is handled with a fallback: if absent from state, the supervisor substitutes 'unknown' before passing to the analyzer. This substitution logic is not representable as a declarative field mapping."
  ]
}
```

## Open TODOs in project.yaml

The assembler inserted `# TODO` comments in `project.yaml` for the items below. Review and fill them in before running the pipeline.

- passthrough_fields is empty -- supervisor either explicitly declared no pass-through, or the LLM could not infer any.

## Validation

### Infos (1)

- [loader] project.yaml loads successfully

## Next Steps

1. Review every `# TODO` in `project.yaml`.
2. Review the raw extraction JSON above to verify the workflow shape.
3. Drop a sample input into `samples/input.json`.
4. Validate:
   ```
   python main.py --input samples/input.json --dry-run
   ```
5. Run the pipeline:
   ```
   python main.py --input samples/input.json
   ```
6. Artifacts land in `runs/<project-name>/<timestamp>/`.
