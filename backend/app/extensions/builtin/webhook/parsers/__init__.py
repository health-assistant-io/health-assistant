from ..validation import WebhookConfigError
from .base import BaseWebhookParser
from .basic import BasicParser, HomeAssistantParser
from .custom import CustomJSONPathParser
from .hc_webhook import HcWebhookParser
from .life_dashboard import LifeDashboardParser

PARSERS = {
    "life_dashboard": LifeDashboardParser(),
    "hc_webhook": HcWebhookParser(),
    "basic": BasicParser(),
    "home_assistant": HomeAssistantParser(),
    "custom": CustomJSONPathParser(),
}


def get_parser(parser_type: str) -> BaseWebhookParser:
    """Resolve a configured parser id; an unknown id is a typed config error."""
    parser = PARSERS.get(parser_type)
    if parser is None:
        raise WebhookConfigError(
            f"Unknown webhook parser {parser_type!r} "
            f"(configured ids: {', '.join(sorted(PARSERS))})."
        )
    return parser
