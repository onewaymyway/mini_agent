"""JSON 接口订阅：parse.items_path / parse.fields 与 json_api 适配器一致。"""
from checkers.wrapped import WrappedListChecker


class JsonLatestChecker(WrappedListChecker):
    name = "json_latest"
    wraps = "json_api"
