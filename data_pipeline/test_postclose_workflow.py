import unittest
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PostCloseWorkflowTests(unittest.TestCase):
    def test_all_workflow_shell_blocks_are_syntactically_complete(self):
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                if line.strip() != 'run: |':
                    continue
                indent = len(line) - len(line.lstrip())
                body = []
                for following in lines[index + 1:]:
                    if following.strip() and len(following) - len(following.lstrip()) <= indent:
                        break
                    body.append(following[indent + 2:])
                import re
                script = re.sub(r'\$\{\{.*?\}\}', 'TEST_VALUE', '\n'.join(body))
                result = subprocess.run(['bash', '-n'], input=script, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, f'{path}:{index + 1}: {result.stderr}')

    def test_single_automatic_workflow_probes_before_full_build(self):
        text = (ROOT / ".github" / "workflows" / "daily-etf-data.yml").read_text("utf-8")
        for cron in (
            '"30 14 * * 1-5"',
            '"0,30 15 * * 1-5"',
            '"0,30 16 * * 1-5"',
            '"0,30 17 * * 1-5"',
            '"20 0,4 * * 2-6"',
            '"23 1,5,9 * * 0,6"',
        ):
            self.assertIn(cron, text)
        self.assertIn("probe_official_shares.py", text)
        self.assertIn("official SSE/SZSE", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("independent official-share probes disagree", text)
        self.assertIn("Build and audit schema-v6 production snapshot", text)
        self.assertNotIn("for attempt in 1 2 3", text)
        self.assertIn("python data_pipeline/audit_snapshot_v6.py", text)
        self.assertIn("timeout 25m python data_pipeline/update_daily_v2.py", text)
        self.assertIn("resolve_publication_target", text)
        self.assertNotIn("order_flow/latest.json", text)
        self.assertNotIn("for attempt in 1 2 3 4", text)
        self.assertNotIn('attempts=1', text)
        self.assertIn("needs.probe.outputs.ready == 'true'", text)
        self.assertIn('ETF_USE_VERIFIED_SHARE_CACHE: "1"', text)
        self.assertIn('WeChat alert on failure', text)
        self.assertIn('PUSHPLUS_TOKEN', text)
        self.assertNotIn('sleep 300', text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn('ETF_SKIP_RETURN_PROXIES: "1"', text)

    def test_capture_workflow_still_persists_order_flow_independently(self):
        text = (ROOT / ".github" / "workflows" / "capture-etf-order-flow.yml").read_text("utf-8")
        self.assertIn("Capture same-day secondary-market ETF order flow", text)
        self.assertNotIn('attempts=7', text)
        self.assertIn("git add site/data/order_flow", text)
        self.assertIn("data: capture same-day ETF secondary order flow", text)

    def test_public_render_is_checked_only_after_a_snapshot_commit(self):
        text = (ROOT / ".github" / "workflows" / "verify-render-deploy.yml").read_text("utf-8")
        self.assertIn("site/data/**", text)
        self.assertIn("workflow_run", text)
        self.assertIn("payload != committed", text)
        self.assertIn("etf-flow-radar-cn.onrender.com/data/latest.json", text)
        self.assertIn("EXPECTED_TRADE_DATE", text)


if __name__ == "__main__":
    unittest.main()
