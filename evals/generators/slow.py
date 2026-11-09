"""Regenerate the invented slow case with a fixed seed."""

import asyncio

from _lifecycle import generate

if __name__ == "__main__":
    asyncio.run(generate("slow"))
