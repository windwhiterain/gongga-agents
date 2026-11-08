import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
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
            mock.patch.dict(os.environ, {"GONGGA_SESSION": "main"}),
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
                    remote=None,
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

    def test_a_judgement_must_show_what_justice_ran(self):
        self.record(
            "plan",
            "advisor",
            {"spec_refs": ["Spec"], "steps": ["implement"], "acceptance": ["ran"]},
        )
        with self.assertRaisesRegex(ValueError, "verification"):
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
    def test_a_session_record_holds_what_a_role_runs_on(self):
        self.record(
            "session",
            "justice",
            {
                "role": "advisor",
                "session": "polity-demo-advisor",
                "model": "provider/flash",
                "remote": None,
                "note": "moved off a model the provider refused",
            },
        )
        _initial, records = polity.read_task("demo", self.home)
        last = records[-1]
        self.assertEqual(last["kind"], "session")
        self.assertEqual(last["model"], "provider/flash")
        with self.assertRaisesRegex(ValueError, "role"):
            self.record("session", "justice", {"role": "nobody", "session": "x"})

    def test_a_remote_start_passes_the_peers_paths_through(self):
        # The peer's spec cannot be tested here: a local is_file() would approve
        # this machine's checkout, or refuse a spec that exists over there.
        missing = self.home / "not-on-this-machine" / "SPEC.md"
        with contextlib.redirect_stdout(io.StringIO()):
            polity.cmd_start(
                SimpleNamespace(
                    task="peer",
                    spec="C:\\peer\\SPEC.md",
                    goal="work where the code is",
                    cwd="C:\\peer",
                    remote="build",
                    home=str(self.home),
                )
            )
        sessions = [
            call
            for call in self.calls
            if call[0] == "set_session" and "polity-peer-" in call[2]
        ]
        self.assertEqual(len(sessions), 2)
        for call in sessions:
            self.assertIn("--remote", call)
            self.assertEqual(call[call.index("--remote") + 1], "build")
        self.assertEqual(sessions[0][sessions[0].index("--cwd") + 1], "C:\\peer")
        self.assertFalse(missing.exists())

    def test_approved_plan_is_the_only_route_to_implementation(self):
        self.assertEqual(self.tiers, ["sota", "flash"])
        initial = polity.read_task("demo", self.home)[0]
        self.assertEqual(initial["roles"]["justice"]["session"], "main")
        self.assertNotIn("justice", self.tiers)
        self.assertEqual(
            sorted(initial["roles"]),
            ["advisor", "justice", "premier"],
        )
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
                    "verification": ["read SPEC.md clause 1"],
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
                "verification": ["read SPEC.md, ran nothing"],
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
                "verification": ["read the plan and the spec"],
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
                "verification": ["read the revised plan"],
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
                "verification": ["ran the focused test the report cites"],
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
                "verification": ["read the amended spec and the plan"],
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


    def test_only_justice_records_what_the_user_asked_for(self):
        body = {"text": "compact with flash only", "source": "user, 2026-11-08"}
        with self.assertRaisesRegex(ValueError, "only justice"):
            self.record("requirement", "premier", body)
        self.record("requirement", "justice", body)
        _initial, records = polity.read_task("demo", self.home)
        self.assertEqual(records[-1]["kind"], "requirement")
        self.assertEqual([r["id"] for r in polity.open_requirements(records)], ["requirement-1"])

    def test_a_judgement_answers_every_open_requirement(self):
        self.record("requirement", "justice", {"text": "no synthetic bench"})
        self.record("requirement", "justice", {"text": "all runs use flash"})
        self.record(
            "plan",
            "advisor",
            {"spec_refs": ["Spec"], "steps": ["implement"], "acceptance": ["ran"]},
        )
        judge = {
            "subject": "plan:plan-1",
            "verdict": "revise",
            "spec_violation": "none",
            "reason": "the plan names a scripted bench",
            "verification": ["read the plan against the two requirements"],
        }
        with self.assertRaisesRegex(ValueError, "every open requirement"):
            self.record("judgement", "justice", judge)
        partial = dict(judge, requirements={"requirement-1": "unmet"})
        with self.assertRaisesRegex(ValueError, "every open requirement"):
            self.record("judgement", "justice", partial)
        unknown = dict(
            judge,
            requirements={
                "requirement-1": "unmet",
                "requirement-2": "met",
                "requirement-9": "met",
            },
        )
        with self.assertRaisesRegex(ValueError, "no open requirement"):
            self.record("judgement", "justice", unknown)
        wrong_state = dict(
            judge,
            requirements={"requirement-1": "mostly", "requirement-2": "met"},
        )
        with self.assertRaisesRegex(ValueError, "met, unmet, or"):
            self.record("judgement", "justice", wrong_state)
        self.record(
            "judgement",
            "justice",
            dict(
                judge,
                requirements={"requirement-1": "unmet", "requirement-2": "met"},
            ),
        )
        # Closing one is a later requirement record naming it, so the next
        # judgement answers only what is still open.
        self.record(
            "requirement",
            "justice",
            {"text": "answered for this task", "closes": "requirement-1",
             "dropped": True},
        )
        with self.assertRaisesRegex(ValueError, "requirement-2"):
            self.record("judgement", "justice", dict(judge, requirements={}))
        open_ids = [r["id"] for r in polity.open_requirements(polity.read_task("demo", self.home)[1])]
        self.assertEqual(open_ids, ["requirement-2"])

    def test_closing_an_unknown_requirement_is_refused(self):
        with self.assertRaisesRegex(ValueError, "expected one requirement"):
            self.record(
                "requirement",
                "justice",
                {"text": "done", "closes": "requirement-7"},
            )


if __name__ == "__main__":
    unittest.main()
