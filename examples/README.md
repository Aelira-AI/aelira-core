# Examples

Guides and examples for extending Aelira Core.

## Adding a New Document Processor

See [custom_processor.py](custom_processor.py) for a complete example of how to add support for a new file format.

The example shows how to:
- Define issue models using Pydantic
- Create a processor class with the standard scan interface
- Return structured results compatible with the dashboard
- Register the processor with the API

## Architecture Overview

```
User uploads file
    |
    v
API route receives file → identifies type → dispatches to processor
    |
    v
Processor scans for WCAG violations → returns structured results
    |
    v
Results stored in database → displayed in dashboard
    |
    v
(Optional) Remediator generates fixed file
```

Each processor follows the same pattern:
1. Accept a file path or bytes
2. Parse the document format
3. Check for WCAG 2.1 AA violations
4. Return a list of issues with locations, severity, and suggested fixes
