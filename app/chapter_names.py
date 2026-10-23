"""Readable chapter folder names; source identifiers stay in JSON only."""
from decimal import Decimal
import re


def chapter_sort_key(name):
    """Sort chapter fractions numerically; leave image filename ordering alone."""
    name = str(name)
    parts = re.split(r'(\d+(?:\.\d+)?)', name.casefold())
    return (tuple((1, Decimal(part)) if i % 2 else (0, part)
                  for i, part in enumerate(parts)), name.casefold(), name)


def chapter_folder_name(name):
    number = re.search(r'\b(?:chapter|episode|ch\.?)\s*#?\s*(\d+(?:\.\d+)?)', name, re.IGNORECASE)
    if number:
        value = format(Decimal(number[1]), 'f')
        if '.' in value: value = value.rstrip('0').rstrip('.')
        value = value.lstrip('0') or '0'
        if value.startswith('.'): value = '0' + value
        return 'Chapter ' + value
    clean = re.sub(r'[^\w .()-]+', '-', name).strip(' .-')[:80].rstrip(' .') or 'Chapter'
    if clean.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        clean = '_' + clean
    return clean
