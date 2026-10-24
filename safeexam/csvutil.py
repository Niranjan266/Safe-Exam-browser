"""CSV helpers shared by every export."""

# A cell starting with one of these is read by spreadsheets as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Neutralise spreadsheet formula injection by prefixing a single quote."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_row(row):
    return [safe_cell(v) for v in row]
