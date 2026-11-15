"""GetAuthStatus у запущенного локального Hub; секреты не попадают в отчёт."""

from dataclasses import dataclass, field
import json
import os
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from patcher.hub.auth import normalize_auth_result
from patcher.hub.profile import PROFILE, inspect_backend
from patcher.vscode.discovery import expected_hub_binary


RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetAuthStatus"
MAX_RESPONSE = 1024 * 1024


class ProbeError(Exception):
    pass


@dataclass(frozen=True)
class HubEndpoint:
    pid: int
    port: int
    csrf_token: str = field(repr=False)


def _argument(command, name):
    match = re.search(r"(?:^|\s)" + re.escape(name) + r'(?:=|\s+)(?:"([^"]*)"|(\S+))', command)
    return (match.group(1) if match.group(1) is not None else match.group(2)) if match else None


def endpoints_from_processes(processes, backend_path):
    expected = os.path.normcase(os.path.realpath(backend_path))
    endpoints = []
    for process in processes:
        command = process.get("CommandLine") or ""
        executable = process.get("ExecutablePath")
        if not isinstance(command, str) or not isinstance(executable, str) or not executable:
            continue
        if os.path.normcase(os.path.realpath(executable)) != expected:
            continue
        if not re.search(r"(?:^|\s)--hub(?:\s|$)", command):
            continue
        port = _argument(command, "--hub-port")
        token = _argument(command, "--csrf_token")
        pid = process.get("ProcessId")
        if (port and port.isascii() and port.isdigit() and 0 < int(port) < 65536
                and token and len(token) <= 4096 and re.fullmatch(r"[!-~]+", token)
                and type(pid) is int and pid > 0):
            endpoints.append(HubEndpoint(pid, int(port), token))
    return endpoints


def discover_running_hubs(backend_path):
    if os.name != "nt":
        raise ProbeError("Process discovery is supported on Windows for this profile")
    # CommandLine содержит CSRF: JSON читается только в памяти и не печатается.
    command = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$items=@(Get-CimInstance Win32_Process -Filter \"Name='agy.exe'\" | "
        "Select-Object ProcessId,ExecutablePath,CommandLine); "
        "ConvertTo-Json -InputObject $items -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=20, check=True, encoding="utf-8-sig",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        processes = json.loads(completed.stdout)
        if not isinstance(processes, list) or any(not isinstance(item, dict) for item in processes):
            raise ValueError("Malformed process list")
        return endpoints_from_processes(processes, backend_path)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ProbeError("Could not inspect running Hub processes") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_auth_status(endpoint):
    if type(endpoint.port) is not int or not 0 < endpoint.port < 65536:
        raise ProbeError("Invalid local Hub port")
    if (not isinstance(endpoint.csrf_token, str) or len(endpoint.csrf_token) > 4096
            or not re.fullmatch(r"[!-~]+", endpoint.csrf_token)):
        raise ProbeError("Invalid local Hub CSRF token")
    # Фиксированный loopback, без прокси и перенаправлений с секретным заголовком.
    request = Request(
        f"http://127.0.0.1:{endpoint.port}{RPC_PATH}", data=b"{}",
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": endpoint.csrf_token},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ProbeError("Hub response exceeded the size limit")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ProbeError("Hub response is not an object")
        return normalize_auth_result(document.get("authResult"), secrets=(endpoint.csrf_token,))
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise ProbeError(f"GetAuthStatus returned HTTP {status}") from None
    except (URLError, OSError):
        raise ProbeError("Could not reach the local Hub RPC endpoint") from None
    except (ValueError, UnicodeError):
        raise ProbeError("Hub response is not valid JSON") from None


def probe_running_hub(*, repeat=2, pid=None, backend_path=None):
    if type(repeat) is not int or not 1 <= repeat <= 10:
        raise ValueError("repeat must be between 1 and 10")
    path = backend_path or expected_hub_binary()
    report = {
        "profile": PROFILE, "runtime_status": "not_checked", "auth_status": "not_checked",
        "success": False, "observations": [], "consistent_between_probes": None,
        "service_access_status": "not_checked", "persistence_status": "not_checked",
        "backend": None, "endpoint": None, "error": None,
    }
    try:
        report["backend"] = inspect_backend(path)
        if not report["backend"]["supported"]:
            report.update(runtime_status="unsupported_backend", error="Backend build is not recognized by this profile")
            return report
        endpoints = discover_running_hubs(path)
        if pid is not None:
            endpoints = [endpoint for endpoint in endpoints if endpoint.pid == pid]
        if not endpoints:
            report.update(runtime_status="hub_not_running", error="No matching running Hub found; open IDE first")
            return report
        if len(endpoints) != 1:
            report.update(runtime_status="ambiguous_hub", error="Multiple Hub processes found; select one with --hub-pid")
            return report
        endpoint = endpoints[0]
        report["endpoint"] = {"pid": endpoint.pid, "port": endpoint.port}
        for index in range(repeat):
            if index:
                time.sleep(0.25)
            observation = request_auth_status(endpoint)
            report["observations"].append(observation)
            report["auth_status"] = observation["auth_status"]
        report["consistent_between_probes"] = all(
            item == report["observations"][0] for item in report["observations"]
        )
        report["runtime_status"] = ("observed" if all(item["consistent"] for item in report["observations"])
                                    else "invalid_response")
        report["success"] = (report["consistent_between_probes"] and
                             all(item["authenticated"] for item in report["observations"]))
    except FileNotFoundError:
        report.update(runtime_status="missing_backend", error="Hub backend does not exist")
    except OSError:
        report.update(runtime_status="backend_error", error="Could not read Hub backend")
    except ProbeError as exc:
        report.update(runtime_status="probe_error", error=str(exc))
    return report


def exit_code(report):
    return 0 if report["success"] else 2 if report["runtime_status"] == "observed" else 3


def print_report(report):
    print(f"Hub profile: {report['profile']}; runtime: {report['runtime_status']}")
    for index, observation in enumerate(report["observations"], 1):
        print(f"GetAuthStatus #{index}: {observation['auth_status']}; consistent={observation['consistent']}")
        if observation["message"] or observation["error"]:
            print("  " + (observation["error"] or observation["message"]))
    if report["error"]:
        print(report["error"])
    print("Service access and restart persistence have not been checked by this command.")
