from collections.abc import Mapping
from html import escape


def wrap_untrusted(
    content: str, *, tool: str, call_id: str, extra: Mapping[str, str] = {}
) -> str:
    attributes = {"tool": tool, "call_id": call_id, **extra}
    attrs = " ".join(
        f'{escape(k, quote=True)}="{escape(v, quote=True)}"'
        for k, v in attributes.items()
    )
    content = content.replace("<untrusted_data", "&lt;untrusted_data").replace(
        "</untrusted_data", "&lt;/untrusted_data"
    )
    return f"<untrusted_data {attrs}>\n{content}\n</untrusted_data>"
