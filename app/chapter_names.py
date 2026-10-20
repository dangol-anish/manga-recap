"""Readable chapter folder names; source identifiers stay in JSON only."""
from decimal import Decimal
import re


def chapter_folder_name(name):
    number = re.search(r'\b(?:chapter|episode|ch\.?)\s*#?\s*(\d+(?:\.\d+)?)', name, re.IGNORECASE)
    if number:
        value = format(Decimal(number[1]), 'f')
        if '.' in value: value = value.rstrip('0').rstrip('.')
        value = value.lstrip('0') or '0'
        if value.startswith('.'): value = '0' + value
        return 'Chapter ' + value
    clean = re.sub(r'[^\w .()-]+', '-', name).strip(' .-')[:80] or 'Chapter'
    return clean
