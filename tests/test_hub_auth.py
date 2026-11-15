"""Регрессии реального статуса: oneof, отказ, RPC и отсутствие утечек секретов."""

import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

import autopatch
from patcher.hub.auth import FAILURES, normalize_auth_result, sanitize_text
from patcher.hub import profile, probe


class AuthResultTests(unittest.TestCase):
    def test_proto_default_false_is_signed_out(self):
        result = normalize_auth_result({})
        self.assertEqual(result["auth_status"], "signed_out")
        self.assertTrue(result["consistent"])
        self.assertFalse(result["authenticated"])

    def test_success_requires_a_valid_boolean_without_failure(self):
        result = normalize_auth_result({"hasValidAuth": True, "grantedScopes": ["scope-a"]})
        self.assertEqual(result["auth_status"], "signed_in")
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["granted_scopes_count"], 1)

    def test_ineligible_survives_both_oneof_representations(self):
        payload = {"verificationUrl": "https://example.invalid/verify?private=secret"}
        wire = normalize_auth_result({"ineligible": payload, "uiMessage": "Account not eligible"})
        js = normalize_auth_result({"failureDetails": {"case": "ineligible", "value": payload},
                                    "uiMessage": "Account not eligible"})
        self.assertEqual(wire, js)
        self.assertEqual(wire["auth_status"], "ineligible")
        self.assertFalse(wire["authenticated"])
        self.assertTrue(wire["remediation_url_present"])
        self.assertNotIn("secret", json.dumps(wire))

    def test_contradiction_retains_server_flag_without_claiming_success(self):
        result = normalize_auth_result({"hasValidAuth": True, "ineligible": {}})
        self.assertEqual(result["auth_status"], "invalid_result")
        self.assertTrue(result["server_has_valid_auth"])
        self.assertEqual(result["failure_case"], "ineligible")
        self.assertFalse(result["consistent"])
        self.assertFalse(result["authenticated"])

    def test_each_server_rejection_remains_distinct(self):
        for failure, expected in FAILURES.items():
            with self.subTest(failure=failure):
                result = normalize_auth_result({failure: {}})
                self.assertEqual(result["auth_status"], expected)
                self.assertFalse(result["authenticated"])

    def test_invalid_documents_never_authenticate(self):
        documents = [None, [], {"hasValidAuth": "false"}, {"hasValidAuth": 1},
                     {"uiMessage": []}, {"grantedScopes": "scope"}, {"grantedScopes": [5]},
                     {"failureDetails": "ineligible"}, {"failureDetails": {"case": True}},
                     {"failureDetails": {"case": "futureFailure", "value": {}}},
                     {"ineligible": None}, {"ineligible": {}, "generalError": {}},
                     {"ineligible": {}, "failureDetails": {"case": "ineligible", "value": {}}}]
        for document in documents:
            with self.subTest(document=document):
                result = normalize_auth_result(document)
                self.assertEqual(result["auth_status"], "invalid_result")
                self.assertFalse(result["authenticated"])

    def test_diagnostics_redact_credentials_and_identity(self):
        raw = ("someone@example.invalid https://example.invalid/?code=private "
               "Bearer bearer-secret access_token=access-secret refresh_token='refresh-secret' "
               "ya29.another-secret eyJjwt-secret csrf-known")
        result = normalize_auth_result({"generalError": {}, "uiMessage": raw}, secrets=("csrf-known",))
        serialized = json.dumps(result)
        for secret in ("someone@", "code=private", "bearer-secret", "access-secret", "refresh-secret",
                       "another-secret", "jwt-secret", "csrf-known"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(result["auth_status"], "general_error")
        self.assertEqual(len(sanitize_text("x" * 10000)), 4096)


class ProfileTests(unittest.TestCase):
    def test_unknown_binary_is_never_assumed_to_be_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agy.exe"
            path.write_bytes(b"unknown build")
            result = profile.inspect_backend(path)
            self.assertFalse(result["supported"])
            self.assertIsNone(result["version"])
            self.assertEqual(result["auth_status"], "not_checked")
            self.assertEqual(path.read_bytes(), b"unknown build")

    def test_known_digest_reports_build_without_executing_or_modifying(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agy.exe"
            data = b"pinned fixture build"
            path.write_bytes(data)
            with patch.dict(profile.KNOWN_BINARIES, {hashlib.sha256(data).hexdigest(): "original"}):
                result = profile.inspect_backend(path)
            self.assertTrue(result["supported"])
            self.assertEqual(result["version"], "1.5.9")
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(list(Path(directory).iterdir()), [path])


class DiscoveryTests(unittest.TestCase):
    def test_only_matching_hub_process_is_selected(self):
        path = str(Path("expected-agy.exe").resolve())
        rows = [
            {"ExecutablePath": path, "ProcessId": 50,
             "CommandLine": '"agy.exe" --hub --hub-port=43210 --csrf_token="private-token"'},
            {"ExecutablePath": path, "ProcessId": 51,
             "CommandLine": 'agy.exe --hub-port=43211 --csrf_token=another-secret'},
            {"ExecutablePath": path + ".other", "ProcessId": 52,
             "CommandLine": 'agy.exe --hub --hub-port=43212 --csrf_token=another-secret'},
            {"ExecutablePath": path, "ProcessId": 53,
             "CommandLine": 'agy.exe --hub --hub-port=70000 --csrf_token=another-secret'},
            {"ExecutablePath": path, "ProcessId": 54, "CommandLine": None},
        ]
        result = probe.endpoints_from_processes(rows, path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].pid, 50)
        self.assertEqual(result[0].port, 43210)
        self.assertNotIn("private-token", repr(result[0]))


@contextlib.contextmanager
def rpc_server(body, *, status=200, location=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append({"path": self.path, "csrf": self.headers.get("x-codeium-csrf-token"),
                             "body": self.rfile.read(int(self.headers.get("Content-Length", 0)))})
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield probe.HubEndpoint(100, server.server_port, "csrf-private"), requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


class TransportTests(unittest.TestCase):
    def test_real_loopback_rpc_uses_json_and_redacts_echoed_csrf(self):
        body = json.dumps({"authResult": {"ineligible": {}, "uiMessage": "Denied csrf-private"}}).encode()
        with rpc_server(body) as (endpoint, requests):
            result = probe.request_auth_status(endpoint)
        self.assertEqual(result["auth_status"], "ineligible")
        self.assertNotIn("csrf-private", json.dumps(result))
        self.assertEqual(requests, [{"path": probe.RPC_PATH, "csrf": "csrf-private", "body": b"{}"}])

    def test_redirect_never_forwards_secret_to_other_endpoint(self):
        with rpc_server(b"{}") as (target, target_requests):
            with rpc_server(b"", status=302, location=f"http://127.0.0.1:{target.port}/capture") as (endpoint, _):
                with self.assertRaisesRegex(probe.ProbeError, "HTTP 302"):
                    probe.request_auth_status(endpoint)
        self.assertEqual(target_requests, [])

    def test_error_body_is_not_included_in_diagnostics(self):
        with rpc_server(b"private-error-body", status=403) as (endpoint, _):
            with self.assertRaises(probe.ProbeError) as error:
                probe.request_auth_status(endpoint)
        self.assertEqual(str(error.exception), "GetAuthStatus returned HTTP 403")

    def test_malformed_json_and_missing_auth_result_are_not_signed_out(self):
        with rpc_server(b"not JSON private-data") as (endpoint, _):
            with self.assertRaisesRegex(probe.ProbeError, "not valid JSON"):
                probe.request_auth_status(endpoint)
        with rpc_server(b"{}") as (endpoint, _):
            self.assertEqual(probe.request_auth_status(endpoint)["auth_status"], "invalid_result")

    def test_header_injection_is_rejected_without_echoing_secret(self):
        with self.assertRaises(probe.ProbeError) as error:
            probe.request_auth_status(probe.HubEndpoint(1, 1000, "secret\r\nInjected: value"))
        self.assertNotIn("secret", str(error.exception))


class ProbeFlowTests(unittest.TestCase):
    def run_probe(self, results=None, endpoints=None, **kwargs):
        endpoints = [probe.HubEndpoint(1, 43210, "private-token")] if endpoints is None else endpoints
        with patch.object(probe, "inspect_backend", return_value={"supported": True}), \
                patch.object(probe, "discover_running_hubs", return_value=endpoints), \
                patch.object(probe, "request_auth_status", side_effect=results or []), \
                patch.object(probe.time, "sleep"):
            return probe.probe_running_hub(**kwargs)

    def test_repeated_valid_status_does_not_claim_restart_or_model_check(self):
        signed_in = normalize_auth_result({"hasValidAuth": True})
        report = self.run_probe([signed_in, signed_in])
        self.assertTrue(report["success"])
        self.assertTrue(report["consistent_between_probes"])
        self.assertEqual(probe.exit_code(report), 0)
        self.assertEqual(report["persistence_status"], "not_checked")
        self.assertEqual(report["service_access_status"], "not_checked")
        self.assertNotIn("private-token", json.dumps(report))

    def test_repeated_ineligible_is_a_real_observed_rejection(self):
        denied = normalize_auth_result({"ineligible": {}})
        report = self.run_probe([denied, denied])
        self.assertEqual(report["runtime_status"], "observed")
        self.assertEqual(report["auth_status"], "ineligible")
        self.assertFalse(report["success"])
        self.assertEqual(probe.exit_code(report), 2)

    def test_later_rejection_or_rpc_failure_cancels_success(self):
        signed_in = normalize_auth_result({"hasValidAuth": True})
        denied = normalize_auth_result({"ineligible": {}})
        report = self.run_probe([signed_in, denied])
        self.assertFalse(report["success"])
        self.assertFalse(report["consistent_between_probes"])
        report = self.run_probe([signed_in, probe.ProbeError("RPC failed")])
        self.assertFalse(report["success"])
        self.assertEqual(probe.exit_code(report), 3)

    def test_contradictory_auth_result_never_authenticates(self):
        invalid = normalize_auth_result({"hasValidAuth": True, "ineligible": {}})
        report = self.run_probe([invalid, invalid])
        self.assertFalse(report["success"])
        self.assertEqual(probe.exit_code(report), 3)

    def test_missing_and_ambiguous_hub_are_not_signed_out(self):
        for endpoints in ([], [probe.HubEndpoint(1, 4000, "a"), probe.HubEndpoint(2, 4001, "b")]):
            report = self.run_probe(endpoints=endpoints)
            self.assertEqual(report["auth_status"], "not_checked")
            self.assertEqual(probe.exit_code(report), 3)

    def test_cli_emits_one_json_and_does_not_autopatch(self):
        denied = normalize_auth_result({"ineligible": {}})
        report = self.run_probe([denied, denied])
        output = io.StringIO()
        with patch.object(probe, "probe_running_hub", return_value=report), \
                patch.object(autopatch, "check_and_patch") as mutation, contextlib.redirect_stdout(output):
            code = autopatch.run_command(["--hub-auth-status", "--repeat", "2", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["auth_status"], "ineligible")
        mutation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
