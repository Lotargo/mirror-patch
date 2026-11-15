"""Общие состояния локальных правок; авторизация проверяется отдельно."""


def combine_states(states):
    states = set(states)
    if not states or "unknown" in states:
        return "unknown"
    if "partial" in states or {"patched", "unpatched"} <= states:
        return "partial"
    if states == {"patched"}:
        return "patched"
    if states == {"unpatched"}:
        return "unpatched"
    return "unknown"
