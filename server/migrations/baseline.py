"""Immutable SQL snapshot shared by the baseline and guarded adoption check."""

from pathlib import Path


def baseline_statements():
    source = Path(__file__).with_name("baseline.sql").read_text(encoding="utf-8")
    return [part.strip() for part in source.split("-- statement-breakpoint")]
