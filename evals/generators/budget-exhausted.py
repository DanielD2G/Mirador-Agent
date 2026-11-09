"""Regenerate the invented budget-exhausted case with a fixed seed."""

import asyncio

from _lifecycle import generate

if __name__ == "__main__":
    asyncio.run(generate("budget-exhausted"))
