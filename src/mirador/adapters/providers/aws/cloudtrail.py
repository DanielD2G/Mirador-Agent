"""CloudTrail management changes."""

import json

from mirador.application.ports.capabilities.changes import Change, ChangesSource
from mirador.application.ports.health import HealthResult
from mirador.domain.time import TimeWindow

from .session import AwsSessions


class CloudTrailChanges(ChangesSource):
    provider = "aws"

    def __init__(self, sessions: AwsSessions) -> None:
        self.sessions = sessions

    async def health_check(self) -> HealthResult:
        return await self.sessions.health_check()

    async def recent_changes(
        self, window: TimeWindow, service: str | None, target: str | None, limit: int
    ) -> list[Change]:
        result: list[Change] = []
        token = None
        async with self.sessions.client("cloudtrail", target) as client:
            while len(result) < limit:
                params: dict[str, str] = {"NextToken": token} if token else {}
                response = await client.lookup_events(
                    StartTime=window.start,
                    EndTime=window.end,
                    LookupAttributes=[
                        {"AttributeKey": "ReadOnly", "AttributeValue": "false"}
                    ],
                    **params,
                )
                for event in response.get("Events", []):
                    details = json.loads(event.get("CloudTrailEvent", "{}"))
                    if str(details.get("readOnly", False)).lower() == "true":
                        continue
                    resources = event.get("Resources", [])
                    if service and not any(
                        service in r.get("ResourceName", "") for r in resources
                    ):
                        continue
                    result.append(
                        Change(
                            at=event["EventTime"],
                            source="cloudtrail",
                            actor=event.get("Username"),
                            kind=event["EventName"],
                            resource=resources[0].get("ResourceName")
                            if resources
                            else None,
                            summary=event["EventName"],
                            details=details,
                            link=None,
                        )
                    )
                token = response.get("NextToken")
                if not token:
                    break
        return result[:limit]
