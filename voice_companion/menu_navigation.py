"""Shared first-letter cycling and spoken menu key labels."""
def quick_key(name):
    return next((c.lower() for c in str(name).strip() if c.isalpha()), '')


def menu_label(name):
    key = quick_key(name)
    return str(name) + (' (' + key.upper() + ')' if key else '')


def next_match(names, current, letter):
    matches = [i for i, name in enumerate(names) if quick_key(name) == letter.lower()]
    if not matches:
        return None
    if current in matches:
        return matches[(matches.index(current) + 1) % len(matches)]
    return matches[0]


def announce_item(name, index, count):
    return f'{menu_label(name)}, {index + 1} of {count}.'
