"""Regenerate the invented nightly-noise case with a fixed seed."""

import asyncio

from _lifecycle import generate

if __name__ == "__main__":
    asyncio.run(generate("nightly-noise"))
