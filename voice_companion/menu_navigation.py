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
        return matches[min(matches.index(current) + 1, len(matches)-1)]
    return matches[0]


def announce_item(name, index, count):
    return f'{menu_label(name)}, {index + 1} of {count}.' + (' '+boundary_notice(index,count) if boundary_notice(index,count) else '')


def boundary_notice(index,count):
    if count<=0:return 'Empty list.'
    if count==1:return 'Start and end of list.'
    return 'Start of list.' if index<=0 else 'End of list.' if index>=count-1 else ''

def bounded_index(index,step,count):
    if count<=0:return None
    if index is None:return 0 if step>=0 else count-1
    return max(0,min(count-1,index+step))
