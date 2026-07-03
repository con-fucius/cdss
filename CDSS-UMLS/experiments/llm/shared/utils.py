"""Shared utilities for LLM experiments."""

import json
import time
from pathlib import Path


def load_prompt_template(template_path: str) -> str:
    """Load prompt template from file."""
    with open(template_path) as f:
        return f.read()


def format_prompt(template: str, **kwargs) -> str:
    """Format prompt template with variables."""
    return template.format(**kwargs)


def save_results(results: list[dict], output_path: str):
    """Save experiment results to JSON."""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)


def measure_time(func):
    """Decorator to measure function execution time."""

    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        return result, elapsed

    return wrapper
