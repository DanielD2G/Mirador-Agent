"""Regenerate the invented provider-auth-error case with a fixed seed."""

import asyncio

from _lifecycle import generate

if __name__ == "__main__":
    asyncio.run(generate("provider-auth-error"))
