#!/usr/bin/env python3
"""Thin wrapper so you can run `waamgen generate/validate` from the repo root."""

from Waam_tech.waamgen.pipeline import generate_cli


if __name__ == "__main__":
    raise SystemExit(generate_cli())
