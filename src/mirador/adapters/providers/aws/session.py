"""Async AWS sessions and centralized SDK error translation."""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.exceptions import BotoCoreError, ClientError

from mirador.application.config import AwsTarget
from mirador.application.ports.health import HealthResult
from mirador.application.tooling.spec import ToolError


class AwsSessions:
    def __init__(
        self,
        targets: Sequence[AwsTarget],
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.targets = {t.name: t for t in targets}
        self.client_factory = client_factory

    def resolve(self, target: str | None) -> AwsTarget:
        if target is None and len(self.targets) == 1:
            return next(iter(self.targets.values()))
        if target not in self.targets:
            raise ToolError(
                "Specify a configured AWS target", actionable=True, kind="invalid"
            )
        return self.targets[target]

    @asynccontextmanager
    async def client(self, service: str, target: str | None) -> AsyncIterator[Any]:
        config = self.resolve(target)
        try:
            session = aioboto3.Session(
                profile_name=config.profile, region_name=config.region
            )
            factory: Any = self.client_factory or session.client
            credentials: dict[str, Any] = {}
            if config.role_arn:
                async with factory("sts") as sts:
                    response = await sts.assume_role(
                        RoleArn=config.role_arn,
                        RoleSessionName="mirador",
                        SourceIdentity="mirador",
                    )
                    value = response["Credentials"]
                    credentials = dict(
                        aws_access_key_id=value["AccessKeyId"],
                        aws_secret_access_key=value["SecretAccessKey"],
                        aws_session_token=value["SessionToken"],
                    )
            async with factory(service, **credentials) as client:
                yield client
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            message = exc.response["Error"].get("Message", code)
            kind: Any = "upstream"
            if code in {
                "ExpiredToken",
                "ExpiredTokenException",
                "UnrecognizedClientException",
                "InvalidClientTokenId",
            }:
                kind = "auth"
            elif code.startswith("AccessDenied"):
                kind = "permission"
            elif code.startswith("Throttling") or code == "TooManyRequestsException":
                kind = "rate_limited"
            elif "NotFound" in code:
                kind = "not_found"
            if kind == "auth":
                message = (
                    "Credentials expired or rejected: aws sso login --profile "
                    f"{config.profile or 'default'}"
                )
            raise ToolError(message, actionable=kind != "upstream", kind=kind) from exc
        except BotoCoreError as exc:
            auth = type(exc).__name__ in {
                "NoCredentialsError",
                "PartialCredentialsError",
                "TokenRetrievalError",
                "UnauthorizedSSOTokenError",
                "SSOTokenLoadError",
            }
            raise ToolError(
                f"aws sso login --profile {config.profile or 'default'}"
                if auth
                else str(exc),
                actionable=auth,
                kind="auth" if auth else "upstream",
            ) from exc

    async def health_check(self) -> HealthResult:
        details = []
        for target in self.targets:
            async with self.client("sts", target) as client:
                identity = await client.get_caller_identity()
                details.append(f"{target}: {identity['Account']} {identity['Arn']}")
        return HealthResult(
            component="aws", ok=bool(details), detail="; ".join(details)
        )
