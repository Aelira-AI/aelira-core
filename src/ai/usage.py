"""Evidence for the legacy Ollama scan fields.

Counts are successful provider operations within one scan runtime, not requested
features, detected objects, transport retries, or proof of accessible output.
None means the writer was not given a measurement.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OllamaUsage:
    successful_calls: int = 0

    def __post_init__(self):
        if type(self.successful_calls) is not int or self.successful_calls < 0:
            raise ValueError("successful_calls must be a nonnegative integer")


def ollama_usage_fields(usage: OllamaUsage | None) -> dict[str, bool | int | None]:
    """Map measured success to storage; uninstrumented results stay unknown."""
    if not isinstance(usage, OllamaUsage):
        return {"ollama_used": None, "ollama_calls": None}
    return {
        "ollama_used": usage.successful_calls > 0,
        "ollama_calls": usage.successful_calls,
    }


def runtime_ollama_usage(runtime) -> dict[str, bool | int | None]:
    """A deliberately absent scan runtime made no calls."""
    usage = OllamaUsage() if runtime is None else getattr(runtime, "ollama_usage", None)
    return ollama_usage_fields(usage)
