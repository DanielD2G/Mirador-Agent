"""Regenerate the invented memory-helps-empty case with a fixed seed."""

import asyncio

from _lifecycle import generate

if __name__ == "__main__":
    asyncio.run(generate("memory-helps-empty"))
