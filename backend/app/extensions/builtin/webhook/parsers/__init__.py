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
    return PARSERS.get(parser_type, PARSERS["life_dashboard"])
