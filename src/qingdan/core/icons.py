"""Small display-only catalog icons; no HTML or file paths are interpreted."""
from qingdan.core.registry import validation_error


def check_icon(value: str) -> str:
    if not isinstance(value, str) or len(value) > 16 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise validation_error('图标需为不超过 16 个字符的单行文本')
    return value.strip()
