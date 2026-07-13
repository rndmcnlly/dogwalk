#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["agent-client-protocol==0.11.0", "pyyaml>=6,<7"]
# ///
"""Run scripted Dogwalk tests without Realtime, WebRTC, or audio."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any

import yaml

from webrtc_spike import SessionLog, SessionManager, TimerQueue


ROOT = Path(__file__).parent


class TestFailure(RuntimeError):
    pass


class TextDriver:
    def __init__(
        self, cwd: Path, verbose: bool, deadline: float
    ) -> None:
        self.log = SessionLog("text-test")
        self.manager = SessionManager(self.log, cwd)
        self.timers = TimerQueue(self.log)
        self.cwd = cwd
        self.verbose = verbose
        self.deadline = deadline
        self.values: dict[str, Any] = {}
        self.starts: dict[str, dict[str, Any]] = {}
        self.turns: dict[str, int] = {}

    def event(self, event: str, **data: Any) -> None:
        if self.verbose:
            print(json.dumps({"event": event, **data}, ensure_ascii=True), flush=True)

    def tool(self, tool_name: str, **arguments: Any) -> dict[str, Any]:
        result = self.manager.dispatch(tool_name, arguments)
        self.event("tool_result", tool=tool_name, arguments=arguments, result=result)
        if not result.get("ok"):
            raise TestFailure(f"{tool_name} failed: {result.get('error', result)}")
        return result

    def wait_for_dog(self, name: str) -> dict[str, Any]:
        while time.monotonic() < self.deadline:
            self.resolve_test_permissions()
            result = self.manager.dispatch("check_dog", {"name": name})
            if result.get("status") in {"resting", "failed", "cancelled"}:
                self.event("dog_settled", result=result)
                if result["status"] != "resting":
                    raise TestFailure(f"{name} settled as {result['status']}: {result}")
                return result
            time.sleep(0.1)
        raise TestFailure(f"Overall scenario timeout expired while waiting for {name}")

    def resolve_test_permissions(self) -> None:
        for decision in self.manager.attention_requests():
            if decision["kind"] != "permission":
                raise TestFailure(f"Unhandled Dog question: {decision['message']}")
            option = next(
                (
                    item
                    for item in decision["options"]
                    if "once" in item["name"].casefold()
                ),
                None,
            )
            if option is None:
                raise TestFailure("Test permission request offered no allow-once option")
            self.tool(
                "respond_to_dog_permission",
                decision_id=decision["decision_id"],
                option_id=option["option_id"],
            )

    def dog(self, name: str) -> dict[str, Any]:
        dog = next(
            (item for item in self.manager.monitor() if item["name"].casefold() == name.casefold()),
            None,
        )
        if dog is None:
            raise TestFailure(f"No Dog named {name}")
        return dog

    def resolve(self, expression: Any) -> Any:
        if not isinstance(expression, str):
            return expression
        if expression.startswith("$"):
            key = expression[1:]
            if key not in self.values:
                raise TestFailure(f"Unknown remembered value {expression}")
            return self.values[key]
        if "." not in expression:
            return expression
        name, field = expression.split(".", 1)
        if field == "turns":
            return self.turns.get(name, 0)
        if field == "start_message":
            try:
                return self.starts[name]["message"]
            except KeyError as exc:
                raise TestFailure(f"No start result for {name}") from exc
        value: Any = self.dog(name)
        for part in field.split("."):
            if part not in value:
                raise TestFailure(f"{expression} has no field {part}")
            value = value[part]
        return value

    def close(self) -> None:
        self.manager.close()
        self.log.file.close()

    def restart_manager(self) -> None:
        self.manager.close()
        self.manager = SessionManager(self.log, self.cwd)


def load_scenario(name_or_path: str) -> tuple[Path, dict[str, Any]]:
    candidate = Path(name_or_path)
    path = candidate if candidate.suffix else ROOT / f"{name_or_path}.test.yaml"
    if not path.exists():
        raise TestFailure(f"Scenario not found: {path}")
    data = yaml.safe_load(path.read_text())
    validate_scenario(data)
    return path, data


def validate_scenario(data: Any) -> None:
    if not isinstance(data, dict):
        raise TestFailure("Scenario must be a YAML mapping")
    allowed = {"name", "timeout", "steps", "assert"}
    unknown = set(data) - allowed
    if unknown:
        raise TestFailure(f"Unknown scenario keys: {', '.join(sorted(unknown))}")
    if not isinstance(data.get("name"), str) or not data["name"]:
        raise TestFailure("Scenario requires a non-empty name")
    if not isinstance(data.get("steps"), list) or not data["steps"]:
        raise TestFailure("Scenario requires at least one step")
    operations = {
        "start",
        "wait",
        "remember",
        "continue",
        "rename",
        "timer",
        "restart",
        "recall",
        "revive",
    }
    started: set[str] = set()
    settled: set[str] = set()
    for index, step in enumerate(data["steps"], 1):
        if not isinstance(step, dict) or len(step) != 1:
            raise TestFailure(f"Step {index} must contain exactly one operation")
        operation = next(iter(step))
        if operation not in operations:
            raise TestFailure(f"Unknown operation at step {index}: {operation}")
        value = step[operation]
        if operation != "wait" and not isinstance(value, dict):
            raise TestFailure(f"Step {index} operation {operation} requires a mapping")
        if operation == "wait" and not isinstance(value, (str, dict)):
            raise TestFailure(f"Step {index} operation wait requires a Dog name")
        if operation == "start":
            name = value.get("dog") if isinstance(value, dict) else None
            if not name or name in started:
                raise TestFailure(f"Step {index} must start a new, named Dog")
            started.add(name)
        elif operation == "wait":
            name = value if isinstance(value, str) else value.get("dog")
            if name not in started:
                raise TestFailure(f"Step {index} waits for unknown Dog {name}")
            settled.add(name)
        elif operation == "remember":
            for expression in value.values():
                if isinstance(expression, str) and expression.endswith(".session.id"):
                    name = expression.split(".", 1)[0]
                    if name not in settled:
                        raise TestFailure(
                            f"Step {index} observes {expression} before waiting for {name}"
                        )
        elif operation == "continue":
            if value.get("dog") not in settled:
                raise TestFailure(f"Step {index} continues a Dog that is not resting")
            settled.discard(value["dog"])
        elif operation == "restart":
            started.clear()
            settled.clear()
        elif operation == "revive":
            name = value.get("dog")
            if not name or name in started:
                raise TestFailure(f"Step {index} must revive a newly named Dog")
            started.add(name)
        elif operation == "rename":
            old_name, new_name = value.get("dog"), value.get("to")
            if old_name not in started or not new_name:
                raise TestFailure(f"Step {index} renames an unknown Dog")
            started.remove(old_name)
            started.add(new_name)
            if old_name in settled:
                settled.remove(old_name)
                settled.add(new_name)
    if not isinstance(data.get("assert", []), list):
        raise TestFailure("assert must be a list")
    assertion_types = {"file_exists", "same", "different", "turns", "contains"}
    for index, assertion in enumerate(data.get("assert", []), 1):
        if not isinstance(assertion, dict) or len(assertion) != 1:
            raise TestFailure(f"Assertion {index} must contain exactly one type")
        kind = next(iter(assertion))
        if kind not in assertion_types:
            raise TestFailure(f"Unknown assertion {index}: {kind}")


def run_steps(driver: TextDriver, scenario: dict[str, Any]) -> None:
    for step in scenario["steps"]:
        if time.monotonic() >= driver.deadline:
            raise TestFailure("Overall scenario timeout expired")
        operation, value = next(iter(step.items()))
        if operation == "start":
            name = value["dog"]
            result = driver.tool(
                "sic_dog",
                name=name,
                task=value["task"],
            )
            driver.starts[name] = result
            driver.turns[name] = 1
        elif operation == "wait":
            name = value if isinstance(value, str) else value["dog"]
            driver.wait_for_dog(name)
        elif operation == "remember":
            for key, expression in value.items():
                driver.values[key] = driver.resolve(expression)
        elif operation == "continue":
            name = value["dog"]
            driver.tool("relay_to_dog", name=name, message=value["message"])
            driver.turns[name] = driver.turns.get(name, 0) + 1
        elif operation == "rename":
            old_name, new_name = value["dog"], value["to"]
            driver.tool("name_dog", current_name=old_name, name=new_name)
            if old_name in driver.starts:
                driver.starts[new_name] = driver.starts.pop(old_name)
            driver.turns[new_name] = driver.turns.pop(old_name, 0)
        elif operation == "timer":
            timer = driver.timers.set(value["seconds"], value["purpose"])
            deadline = min(driver.deadline, time.monotonic() + value["seconds"] + 2)
            while time.monotonic() < deadline:
                due = driver.timers.take_due()
                if due:
                    driver.values[value.get("remember_as", "timer")] = due[0]
                    break
                time.sleep(0.05)
            else:
                raise TestFailure(f"Timer {timer['timer_id']} did not fire")
        elif operation == "restart":
            driver.restart_manager()
        elif operation == "recall":
            result = driver.tool("recall_previous_dogs")
            if remember_as := value.get("remember_as"):
                driver.values[remember_as] = result["sessions"]
        elif operation == "revive":
            name = value["dog"]
            driver.tool(
                "revive_dog",
                name=name,
                session_id=driver.resolve(value["session"]),
            )
            driver.turns[name] = 0


def run_assertions(driver: TextDriver, assertions: list[dict[str, Any]]) -> None:
    for assertion in assertions:
        if not isinstance(assertion, dict) or len(assertion) != 1:
            raise TestFailure("Each assertion must contain exactly one assertion type")
        kind, value = next(iter(assertion.items()))
        if kind == "file_exists":
            if not (driver.cwd / value).is_file():
                raise TestFailure(f"Expected file does not exist: {value}")
        elif kind in {"same", "different"}:
            left, right = map(driver.resolve, value)
            passed = left == right if kind == "same" else left != right
            if not passed:
                raise TestFailure(
                    f"Expected {value[0]} and {value[1]} to be {kind}; "
                    f"observed {left!r} and {right!r}"
                )
        elif kind == "turns":
            for name, expected in value.items():
                actual = driver.resolve(f"{name}.turns")
                if actual != expected:
                    raise TestFailure(f"Expected {name} to have {expected} turns, got {actual}")
        elif kind == "contains":
            actual = str(driver.resolve(value["value"]))
            if value["text"] not in actual:
                raise TestFailure(
                    f"Expected {value['value']} to contain {value['text']!r}, got {actual!r}"
                )
        else:
            raise TestFailure(f"Unknown assertion: {kind}")


def run_test(args: argparse.Namespace) -> int:
    started = time.monotonic()
    try:
        _, scenario = load_scenario(args.scenario)
    except TestFailure as exc:
        print(f"FAIL {args.scenario}: {exc}")
        return 1
    if args.workspace and not args.allow_project_workspace:
        print("FAIL: --workspace requires --allow-project-workspace")
        return 1
    temporary = args.workspace is None
    workspace = args.workspace or Path(
        tempfile.mkdtemp(prefix="dogwalk-test-", dir=os.environ.get("TMPDIR"))
    )
    workspace.mkdir(parents=True, exist_ok=True)
    driver = TextDriver(
        workspace.resolve(),
        args.verbose,
        deadline=started + float(scenario.get("timeout", 180)),
    )
    try:
        run_steps(driver, scenario)
        run_assertions(driver, scenario.get("assert", []))
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(f"FAIL {scenario['name']} ({elapsed:.1f}s): {exc}")
        print(f"Details: {driver.log.path}")
        print(f"Workspace preserved: {workspace}")
        return 1
    finally:
        driver.close()
    elapsed = time.monotonic() - started
    sessions = len(driver.manager.monitor())
    turns = sum(driver.turns.values())
    files = sum(1 for path in workspace.iterdir() if path.is_file())
    print(f"PASS {scenario['name']} ({elapsed:.1f}s): {sessions} sessions, {turns} turns, {files} files")
    if temporary:
        shutil.rmtree(workspace)
    return 0


def run_service_test() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="dogwalk-service-", dir=os.environ.get("TMPDIR")))
    log_dir = workspace / "logs"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [
        sys.executable,
        str(ROOT / "webrtc_spike.py"),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workspace",
        str(workspace),
        "--log-dir",
        str(log_dir),
        "--call-lease-seconds",
        "2",
    ]
    env = {**os.environ, "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "test-only")}
    process = subprocess.Popen(
        command,
        cwd=workspace,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"

    def request(
        path: str,
        method: str = "GET",
        token: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, bytes]:
        headers = {"X-Dogwalk-Call": token} if token else {}
        body = json.dumps(data).encode() if data is not None else None
        if body:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            base + path, body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if request("/healthz")[0] == HTTPStatus.OK:
                    break
            except urllib.error.URLError:
                time.sleep(0.05)
        else:
            raise TestFailure("Service did not become healthy within 10 seconds")
        if request("/webrtc_spike.html")[0] != HTTPStatus.OK:
            raise TestFailure("Static browser client was not served")
        for private_path in ("/.env", "/.git/config", "/logs/"):
            if request(private_path)[0] != HTTPStatus.NOT_FOUND:
                raise TestFailure(f"Private path was exposed: {private_path}")
        status, body = request("/readyz")
        readiness = json.loads(body)
        if status != HTTPStatus.OK or not readiness["ok"]:
            raise TestFailure(f"Service was not ready: {body.decode()}")
        status, body = request("/call", "POST")
        if status != HTTPStatus.OK:
            raise TestFailure(f"Could not acquire call: {body.decode()}")
        token = json.loads(body)["call_token"]
        heartbeat_request = urllib.request.Request(
            base + "/call-heartbeat", headers={"X-Dogwalk-Call": token}
        )
        heartbeat = urllib.request.urlopen(heartbeat_request, timeout=3)
        heartbeat.readline()
        time.sleep(2.2)
        if request("/call", "POST")[0] != HTTPStatus.CONFLICT:
            raise TestFailure("A second Walker call was not rejected")
        if request("/monitor")[0] != HTTPStatus.CONFLICT:
            raise TestFailure("Monitor accepted a request without the call token")
        if request("/monitor", token=token)[0] != HTTPStatus.OK:
            raise TestFailure("Monitor rejected the active call token")
        status, _ = request(
            "/event", "POST", token, {"kind": "browser_session_stopped"}
        )
        heartbeat.close()
        if status != HTTPStatus.OK or request("/call", "POST")[0] != HTTPStatus.OK:
            raise TestFailure("Released call lease could not be reacquired")
    except Exception as exc:
        print(f"FAIL service: {exc}")
        return_code = 1
    else:
        print(
            "PASS service: private static root, health, readiness, "
            "streaming exclusive call lease, release"
        )
        return_code = 0
    finally:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return_code = 1
        if process.returncode != 0:
            print(f"Service exited {process.returncode}: {stderr or stdout}")
            return_code = 1
        if return_code == 0:
            shutil.rmtree(workspace)
        else:
            print(f"Workspace preserved: {workspace}")
    return return_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    test = subparsers.add_parser("test", help="Run a declarative ACP scenario")
    test.add_argument("scenario", help="Scenario name or YAML path")
    test.add_argument("--verbose", action="store_true", help="Print structured step events")
    test.add_argument("--workspace", type=Path, help="Use an existing workspace")
    test.add_argument("--allow-project-workspace", action="store_true")
    subparsers.add_parser("service", help="Test the portable HTTP service boundary")
    args = parser.parse_args()
    raise SystemExit(run_test(args) if args.command == "test" else run_service_test())


if __name__ == "__main__":
    main()
