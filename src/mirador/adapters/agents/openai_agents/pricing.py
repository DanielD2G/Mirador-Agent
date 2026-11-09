from decimal import Decimal

import httpx

from mirador.application.ports.agent_runtime import AgentRunError


class PricingCatalog:
    def __init__(self, prices: dict[str, tuple[Decimal, Decimal]]) -> None:
        self._prices = dict(prices)

    @classmethod
    async def fetch(
        cls, api_key: str, base_url: str = "https://openrouter.ai/api/v1"
    ) -> "PricingCatalog":
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            prices = {
                item["id"]: (
                    Decimal(item["pricing"]["prompt"]),
                    Decimal(
                        item["pricing"].get("completion", item["pricing"]["prompt"])
                    ),
                )
                for item in response.json()["data"]
            }
        if any(not p.is_finite() or p < 0 for pair in prices.values() for p in pair):
            raise AgentRunError("Invalid model pricing", retryable=False)
        return cls(prices)

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        if model not in self._prices:
            raise AgentRunError(f"Unknown model pricing: {model}", retryable=False)
        prompt, completion = self._prices[model]
        return prompt * input_tokens + completion * output_tokens
