from .base import BaseWebhookParser
from .basic import BasicParser, HomeAssistantParser
from .life_dashboard import LifeDashboardParser
from .custom import CustomJSONPathParser

PARSERS = {
    "life_dashboard": LifeDashboardParser(),
    "basic": BasicParser(),
    "home_assistant": HomeAssistantParser(),
    "custom": CustomJSONPathParser()
}

def get_parser(parser_type: str) -> BaseWebhookParser:
    return PARSERS.get(parser_type, PARSERS["life_dashboard"])
