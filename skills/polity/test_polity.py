import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("polity", ROOT / "polity.py")
polity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(polity)


class PolityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.home = Path(self.temporary.name)
        self.spec = self.home / "SPEC.md"
        self.spec.write_text("# Spec\n", encoding="utf-8")
        self.calls = []
        self.tiers = []

        def draw(tier):
            self.tiers.append(tier)
            return f"provider/{tier}"

        self.patchers = [
            mock.patch.object(
                polity, "cli", side_effect=lambda *a, **k: self.calls.append(a)
            ),
            mock.patch.object(polity, "draw_model", side_effect=draw),
        ]
        for patcher in self.patchers:
            patcher.start()
        with contextlib.redirect_stdout(io.StringIO()):
            polity.cmd_start(
                SimpleNamespace(
                    task="demo",
                    spec=str(self.spec),
                    goal="implement the feature",
                    cwd=str(self.home),
                    justice="main",
                    home=str(self.home),
                )
            )

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        self.temporary.cleanup()

    def record(self, kind, actor, body):
        args = SimpleNamespace(
            task="demo",
            kind=kind,
            actor=actor,
            id=None,
            json=json.dumps(body),
            home=str(self.home),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            polity.cmd_record(args)

    def dispatch(self):
        polity.cmd_dispatch(
            SimpleNamespace(task="demo", plan="plan-1", home=str(self.home))
        )

    def test_approved_plan_is_the_only_route_to_implementation(self):
        self.assertEqual(self.tiers, ["flash", "sota", "flash"])
        initial = polity.read_task("demo", self.home)[0]
        self.assertEqual(initial["roles"]["justice"]["session"], "main")
        self.assertNotIn("justice", self.tiers)
        with self.assertRaisesRegex(ValueError, "no approved judgement"):
            self.dispatch()

        plan = {
            "spec_refs": ["Spec"],
            "steps": ["implement"],
            "acceptance": ["verified"],
        }
        with self.assertRaisesRegex(ValueError, "only advisor"):
            self.record("plan", "premier", plan)
        self.record("plan", "advisor", plan)
        with self.assertRaisesRegex(ValueError, "cannot be approved"):
            self.record(
                "judgement",
                "justice",
                {
                    "subject": "plan:plan-1",
                    "verdict": "approve",
                    "spec_violation": "direct",
                    "spec_refs": ["Spec"],
                    "reason": "conflict",
                },
            )
        self.record(
            "judgement",
            "justice",
            {
                "subject": "plan:plan-1",
                "verdict": "approve",
                "spec_violation": "none",
                "spec_refs": ["Spec"],
                "reason": "conforms",
            },
        )
        self.dispatch()
        self.assertIn("plan-1", self.calls[-1][-1])
        self.assertIn("polity-demo-premier", self.calls[-1])
        self.record(
            "judgement",
            "justice",
            {
                "subject": "plan:plan-1",
                "verdict": "revise",
                "spec_violation": "none",
                "reason": "scope changed",
            },
        )
        with self.assertRaisesRegex(ValueError, "no approved judgement"):
            self.dispatch()
        self.record(
            "judgement",
            "justice",
            {
                "subject": "plan:plan-1",
                "verdict": "approve",
                "spec_violation": "none",
                "reason": "approved again",
            },
        )

        self.record(
            "report",
            "premier",
            {
                "plan": "plan-1",
                "changes": ["changed the feature"],
                "verification": ["focused test passed"],
                "deviations": [],
                "remaining": [],
            },
        )
        self.record(
            "judgement",
            "justice",
            {
                "subject": "report:report-1",
                "verdict": "approve",
                "spec_violation": "none",
                "spec_refs": ["Spec"],
                "reason": "accepted",
            },
        )
        _initial, records = polity.read_task("demo", self.home)
        self.assertEqual(records[-1]["subject"], "report:report-1")

    def test_spec_amendment_references_proposal_and_user_decision(self):
        with self.assertRaisesRegex(ValueError, "expected"):
            self.record("proposal", "advisor", {"example": "incomplete"})
        self.record(
            "proposal",
            "advisor",
            {
                "example": "case",
                "expected": "new behavior",
                "actual": "old behavior",
                "actual_spec": "old clause",
                "expected_spec": "new clause",
            },
        )
        self.record(
            "judgement",
            "justice",
            {
                "subject": "proposal:proposal-1",
                "verdict": "approve",
                "spec_violation": "premise",
                "spec_refs": ["Spec"],
                "reason": "ask the user",
            },
        )
        self.record(
            "spec_amendment",
            "justice",
            {
                "ref_proposal": "proposal-1",
                "ref_judgement": "judgement-1",
                "user_approved": False,
                "user_decision": "No.",
                "change": "none",
            },
        )
        self.assertEqual(self.spec.read_text(encoding="utf-8"), "# Spec\n")
        _initial, records = polity.read_task("demo", self.home)
        self.assertFalse(records[-1]["user_approved"])
        self.record(
            "plan",
            "advisor",
            {"spec_refs": ["Spec"], "steps": ["implement"], "acceptance": ["verified"]},
        )
        self.record(
            "judgement",
            "justice",
            {
                "subject": "plan:plan-1",
                "verdict": "approve",
                "spec_violation": "none",
                "reason": "conforms",
            },
        )
        self.record(
            "spec_amendment",
            "justice",
            {
                "ref_proposal": "proposal-1",
                "ref_judgement": "judgement-1",
                "user_approved": True,
                "user_decision": "Approved.",
                "change": "new clause",
            },
        )
        with self.assertRaisesRegex(ValueError, "spec changed"):
            self.dispatch()

    def test_hr_remodel_marks_route_without_losing_the_session(self):
        before = len(self.calls)
        with mock.patch.object(polity.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            with contextlib.redirect_stdout(io.StringIO()):
                polity.cmd_remodel(
                    SimpleNamespace(
                        task="demo",
                        role="premier",
                        old_model="provider/old",
                        reason="quota exhausted",
                        home=str(self.home),
                    )
                )
        self.assertEqual(
            run.call_args.args[0][1:4], ["mark", "provider/old", "--reason"]
        )
        self.assertEqual(
            self.calls[before],
            (
                "set_session",
                "--session",
                "polity-demo-premier",
                "--model",
                "provider/flash",
            ),
        )
        self.assertEqual(len(self.calls), before + 1)


if __name__ == "__main__":
    unittest.main()
