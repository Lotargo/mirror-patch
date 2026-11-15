"""Нормализация наблюдаемого AuthResult с сохранением исходного отказа."""

import re


FAILURES = {
    "ineligible": "ineligible",
    "verificationRequired": "verification_required",
    "tosViolation": "tos_violation",
    "generalError": "general_error",
    "projectRequired": "project_required",
    "headlessAuthRequired": "headless_auth_required",
    "licenseRequired": "license_required",
}


def sanitize_text(value, secrets=()):
    if not isinstance(value, str):
        return ""
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email redacted]", value)
    value = re.sub(r"https?://[^\s<>\"']+", "[URL redacted]", value)
    value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", value)
    value = re.sub(r"\b(?:ya29\.[\w.-]+|eyJ[\w.-]+)", "[token redacted]", value)
    value = re.sub(
        r"(?i)\b(access_token|refresh_token|id_token|csrf_token|client_secret|api_key|code_verifier)"
        r"[\s\"':=]+[^\s,;\"}]+", r"\1=[redacted]", value,
    )
    return value[:4096]


def normalize_auth_result(result, *, secrets=()):
    """Поддерживает protobuf JSON и JS oneof; противоречие не становится успехом."""
    observation = {
        "auth_status": "invalid_result", "authenticated": False,
        "server_has_valid_auth": None, "failure_case": None,
        "message": "", "granted_scopes_count": 0, "remediation_url_present": False,
        "consistent": False, "error": None,
    }

    def invalid(message):
        observation["error"] = message
        return observation

    if not isinstance(result, dict):
        return invalid("AuthResult is missing or is not an object")
    # В protobuf JSON значение false может отсутствовать.
    valid = result.get("hasValidAuth", False)
    if not isinstance(valid, bool):
        return invalid("hasValidAuth must be a boolean")
    observation["server_has_valid_auth"] = valid
    if "uiMessage" in result and not isinstance(result["uiMessage"], str):
        return invalid("uiMessage must be a string")
    observation["message"] = sanitize_text(result.get("uiMessage", ""), secrets)
    scopes = result.get("grantedScopes", [])
    if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
        return invalid("grantedScopes must be an array of strings")
    observation["granted_scopes_count"] = len(scopes)
    failures = [(name, result[name]) for name in FAILURES if name in result]
    if "failureDetails" in result:
        details = result["failureDetails"]
        if not isinstance(details, dict):
            return invalid("failureDetails must be an object")
        case = details.get("case")
        if case is not None and not isinstance(case, str):
            return invalid("failureDetails case must be a string")
        if case:
            if not isinstance(case, str) or case not in FAILURES:
                return invalid("Unrecognized failureDetails case")
            failures.append((case, details.get("value")))
    if len(failures) > 1:
        return invalid("AuthResult contains multiple failureDetails variants")
    if failures:
        case, payload = failures[0]
        observation["failure_case"] = case
        if not isinstance(payload, dict):
            return invalid("Failure payload must be an object")
        observation["remediation_url_present"] = any(
            isinstance(payload.get(key), str) and bool(payload[key])
            for key in ("verificationUrl", "appealUrl")
        )
        if valid:
            return invalid("hasValidAuth=true contradicts failureDetails")
        observation["auth_status"] = FAILURES[case]
    elif valid:
        observation["auth_status"] = "signed_in"
        observation["authenticated"] = True
    else:
        observation["auth_status"] = "signed_out"
    observation["consistent"] = True
    return observation
