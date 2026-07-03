"""Dataset splitting utilities for experiments."""

import json
import random
from pathlib import Path


def split_dataset(
    dataset: list[dict],
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split dataset into train/val/test."""
    assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, "Ratios must sum to 1.0"

    random.seed(seed)
    shuffled = dataset.copy()
    random.shuffle(shuffled)

    n = len(shuffled)
    train_end = int(n * train_ratio)
    val_end = train_end + int(n * val_ratio)

    train = shuffled[:train_end]
    val = shuffled[train_end:val_end]
    test = shuffled[val_end:]

    return train, val, test


def load_dataset(file_path: str) -> list[dict]:
    """Load dataset from JSON file."""
    with open(file_path) as f:
        return json.load(f)


def save_split(train: list[dict], val: list[dict], test: list[dict], output_dir: str):
    """Save split datasets to files."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    with open(output_path / "train.json", "w") as f:
        json.dump(train, f, indent=2)

    with open(output_path / "val.json", "w") as f:
        json.dump(val, f, indent=2)

    with open(output_path / "test.json", "w") as f:
        json.dump(test, f, indent=2)
