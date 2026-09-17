#!/usr/bin/env python3
"""
Obi Wag Health Check - Runtime health checks only.

Structural/config health (JSON validity, hook presence, heredoc pollution,
dependency availability) is validated by ``tools/config-guardian.ps1`` (which
deploy.ps1 runs post-deploy). Run guardian separately for structural checks.

This module focuses on runtime state that only a live execution can verify:
  - Hook execution tests (actually invoking hooks)
  - Drift detection (hash comparison source vs deployed)
  - Version source and deployed content sync
  - Content integrity markers
  - Source structure validation
  - Settings precedence warnings
  - Auto-memory environment check
  - Hooks sync check (hash source vs deployed)

Usage:
    python tools/healthcheck.py           # Full health check
    python tools/healthcheck.py --quick   # Fast validation only
    python tools/healthcheck.py --repo-root C:\\source\\obiwag-agents
    python tools/healthcheck.py --verbose # Detailed output
    python tools/healthcheck.py --strict  # Treat warnings as failures

"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Set

# Shared health check constants (single source of truth for colors, model versions)
from health_shared import GREEN, RED, YELLOW, CYAN, BOLD, color
from hook_timeout_audit import scan_claude_hook_timeouts, summarize_hook_timeouts
from peer_review.io_utils import read_json_file


def _is_source_repo(path: Path) -> bool:
    """Return whether *path* has the source-only markers this check requires."""
    return (
        (path / 'tools' / 'version.yaml').is_file()
        and (path / 'policies').is_dir()
        and (path / 'phases').is_dir()
        and (path / 'platforms' / 'codex' / 'AGENTS.md').is_file()
    )


def resolve_repo_root(script_dir: Path, explicit: Optional[Path] = None) -> Path:
    """Resolve source independently from the deployed OBI_HOME script path."""
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not _is_source_repo(candidate):
            raise ValueError(f"--repo-root is not an obiwag-agents source tree: {candidate}")
        return candidate

    candidates = [script_dir.parent]
    project_root = os.environ.get('CLAUDE_PROJECT_ROOT')
    if project_root:
        candidates.append(Path(project_root))
    cwd = Path.cwd()
    candidates.extend([cwd, *cwd.parents])

    seen: Set[Path] = set()
    for raw in candidates:
        try:
            candidate = raw.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_source_repo(candidate):
            return candidate

    raise ValueError(
        "Could not locate the obiwag-agents source tree; pass --repo-root or set "
        "CLAUDE_PROJECT_ROOT"
    )


class HealthCheck:
    """Obi Wag deployment health checker."""

    def __init__(
        self,
        verbose: bool = False,
        strict: bool = False,
        repo_root: Optional[Path] = None,
    ):
        self.verbose = verbose
        self.strict = strict
        self.script_dir = Path(__file__).resolve().parent
        self.repo_root = resolve_repo_root(self.script_dir, repo_root)
        self.source_tools_dir = self.repo_root / 'tools'
        self.home = Path.home()
        self.checks_passed = 0
        self.checks_failed = 0
        self.warnings = 0

        # Source paths (lifecycle-centric structure)
        self.policies_dir = self.repo_root / 'policies'
        self.phases_dir = self.repo_root / 'phases'
        self.hooks_src_dir = self.repo_root / 'hooks'
        self.users_dir = self.repo_root / 'users'
        self.skills_dir = self.repo_root / 'skills'
        self.orchestration_dir = self.repo_root / 'orchestration'
        self.codex_src_dir = self.repo_root / 'platforms' / 'codex'
        self.version_file = self.source_tools_dir / 'version.yaml'

        # Deploy targets
        self.claude_target = self.home / '.claude'
        self.codex_target = self.home / '.codex'
        self.tools_target = Path(os.environ.get('OBI_HOME', str(self.home / '.obi-tools')))

        # Phase → deployed command name (mirrors deploy.ps1)
        self.phase_command_map = {
            '01-discovery': 'discovery.md',
            '02-author': 'author.md',
            '03-simplify': 'simplify.md',
            '04-review': 'review.md',
            '05-integrate': 'integrate.md',
            '06-re-review': 're-review.md',
            '07-readme': 'readme.md',
            '08-readme-review': 'readme-review.md',
            '09-release': 'release.md',
            '10-learning': 'learning.md',
        }

    def log(self, message: str, level: str = 'info'):
        """Log a message with appropriate formatting."""
        if level == 'pass':
            print(f"  {color('[OK]', GREEN)} {message}")
            self.checks_passed += 1
        elif level == 'fail':
            print(f"  {color('[FAIL]', RED)} {message}")
            self.checks_failed += 1
        elif level == 'warn':
            print(f"  {color('[WARN]', YELLOW)} {message}")
            self.warnings += 1
        elif level == 'info' and self.verbose:
            print(f"  {color('[INFO]', CYAN)} {message}")
        elif level == 'header':
            print(f"\n{color(BOLD + message, CYAN)}")

    def check_file_exists(self, path: Path, description: str) -> bool:
        if path.exists():
            self.log(f"{description}: {path.name}", 'pass')
            return True
        else:
            self.log(f"{description}: {path} NOT FOUND", 'fail')
            return False

    def check_dir_exists(self, path: Path, description: str) -> bool:
        if path.is_dir():
            self.log(f"{description}: {path}", 'pass')
            return True
        else:
            self.log(f"{description}: {path} NOT FOUND", 'fail')
            return False

    def check_file_contains(self, path: Path, marker: str, description: str) -> bool:
        if not path.exists():
            self.log(f"{description}: file not found", 'fail')
            return False
        content = path.read_text(encoding='utf-8', errors='ignore')
        if marker in content:
            self.log(f"{description}: contains '{marker[:30]}...'", 'pass')
            return True
        else:
            self.log(f"{description}: missing '{marker[:30]}...'", 'fail')
            return False

    def get_file_hash(self, path: Path) -> str:
        if not path.exists():
            return ""
        return hashlib.md5(path.read_bytes()).hexdigest()[:8]

    def get_sha256(self, path: Path) -> str:
        if not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def check_deployed_tool_hash(self, relative: str, label: str) -> bool:
        """Fail closed when one repo tool is missing or differs under OBI_HOME/tools."""
        source = self.source_tools_dir / Path(relative)
        deployed = self.tools_target / 'tools' / Path(relative)
        if not source.is_file():
            self.log(f"{label} source missing: {relative}", 'fail')
            return False
        if not deployed.is_file():
            self.log(f"{label} deployment missing: {deployed}", 'fail')
            return False
        source_hash = self.get_sha256(source)
        if source_hash != self.get_sha256(deployed):
            self.log(f"{label} deployment drift: {relative}", 'fail')
            return False
        self.log(f"{label} in sync: {relative} ({source_hash[:12]})", 'pass')
        return True

    # =========================================================================
    # Health Check Sections
    # =========================================================================

    def check_source_structure(self) -> bool:
        """Verify repo source structure exists."""
        self.log("Source Structure", 'header')
        all_pass = True

        # Required top-level directories
        for subdir in ['policies', 'phases', 'hooks', 'orchestration',
                       'skills', 'users', 'docs', 'tools']:
            if not self.check_dir_exists(self.repo_root / subdir, subdir):
                all_pass = False

        # Policy files (markdown)
        policies = ['zero-hallucination.md', 'three-strike-rule.md',
                    'hard-stop-conditions.md', 'express-lane.md', 'vendor-rules.md',
                    'approval-gates.md']
        for policy in policies:
            if not self.check_file_exists(self.policies_dir / policy, "Policy"):
                all_pass = False

        # Phase directories with command.md
        for phase_name in self.phase_command_map:
            phase_dir = self.phases_dir / phase_name
            if not phase_dir.is_dir():
                self.log(f"Phase dir {phase_name}: NOT FOUND", 'fail')
                all_pass = False
            else:
                cmd_file = phase_dir / 'command.md'
                if not cmd_file.exists():
                    self.log(f"{phase_name}/command.md: NOT FOUND", 'fail')
                    all_pass = False
                else:
                    self.log(f"{phase_name}/command.md: present", 'pass')

        # Version file
        if not self.check_file_exists(self.version_file, "Version file"):
            all_pass = False

        # CLAUDE.md at repo root
        if not self.check_file_exists(self.repo_root / 'CLAUDE.md', "CLAUDE.md"):
            all_pass = False

        return all_pass

    def check_codex_source(self) -> bool:
        """Verify Codex source files exist."""
        self.log("Codex Source Files", 'header')
        all_pass = True

        for filename in ['AGENTS.md', 'hooks.json', 'README.md', 'validate-codex.py']:
            if not self.check_file_exists(self.codex_src_dir / filename, f"Codex {filename}"):
                all_pass = False

        hook_wrapper = self.hooks_src_dir / 'hook_wrapper_codex.cmd'
        if not self.check_file_exists(hook_wrapper, "Codex hook wrapper"):
            all_pass = False

        return all_pass

    def check_deployment(self) -> bool:
        """Verify files are deployed to target locations."""
        self.log("Deployment Targets", 'header')
        all_pass = True

        # Claude Code deployment
        claude_commands_dir = self.claude_target / 'commands'
        if claude_commands_dir.is_dir():
            self.log("Claude commands directory exists", 'pass')

            for phase_name, cmd_name in self.phase_command_map.items():
                path = claude_commands_dir / cmd_name
                if path.exists():
                    self.log(f"Deployed: {cmd_name}", 'pass')
                else:
                    self.log(f"Missing: {cmd_name} (from {phase_name})", 'fail')
                    all_pass = False

            for extra in ['obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                         'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md']:
                path = claude_commands_dir / extra
                if path.exists():
                    self.log(f"Deployed: {extra}", 'pass')
                else:
                    self.log(f"Missing: {extra}", 'warn')
        else:
            self.log(f"Claude commands dir not found: {claude_commands_dir}", 'fail')
            all_pass = False

        # Claude Code agents
        claude_agents_dir = self.claude_target / 'agents'
        if claude_agents_dir.is_dir():
            for agent in ['obi-discovery.md', 'obi-reviewer.md',
                          'obi-rereviewer.md', 'obi-readme-verifier.md',
                          'obi-swarm-worker.md']:
                path = claude_agents_dir / agent
                if path.exists():
                    self.log(f"Deployed agent: {agent}", 'pass')
                else:
                    self.log(f"Missing agent: {agent}", 'warn')
        else:
            self.log(f"Claude agents dir not found: {claude_agents_dir}", 'warn')

        # Codex deployment
        codex_agents = self.repo_root / 'AGENTS.md'
        if codex_agents.exists():
            self.log("Codex AGENTS.md exists", 'pass')
        else:
            self.log(f"Codex AGENTS.md not found: {codex_agents}", 'fail')
            all_pass = False

        codex_hooks = self.repo_root / '.codex' / 'hooks.json'
        if codex_hooks.exists():
            self.log("Codex project hooks.json exists", 'pass')
        else:
            self.log(f"Codex hooks.json not found: {codex_hooks}", 'warn')

        codex_skills = self.codex_target / 'skills'
        if codex_skills.is_dir():
            self.log("Codex skills directory exists", 'pass')
        else:
            self.log(f"Codex skills dir not found: {codex_skills}", 'warn')

        # Workflow safety tools are copied under OBI_HOME/tools as part of the bulk tools deploy.
        # Presence alone is insufficient: a stale archive helper could apply an obsolete allowlist.
        for relative in ['archive-run-state.ps1', 'lib/path-safety.ps1']:
            if not self.check_deployed_tool_hash(relative, 'Workflow safety tool'):
                all_pass = False

        return all_pass

    def check_content_integrity(self) -> bool:
        """Verify deployed files contain expected content markers."""
        self.log("Content Integrity", 'header')
        all_pass = True

        author_cmd = self.claude_target / 'commands' / 'author.md'
        if not self.check_file_contains(author_cmd, 'zero-hallucination',
                                        "Author command policy reference"):
            all_pass = False

        obi_cmd = self.claude_target / 'commands' / 'obi.md'
        if not self.check_file_contains(obi_cmd, 'Orchestrator',
                                        "Obi command role"):
            all_pass = False

        codex_agents = self.repo_root / 'AGENTS.md'
        if not self.check_file_contains(codex_agents, 'OBI_PLATFORM=codex',
                                        "Codex AGENTS runtime marker"):
            all_pass = False

        return all_pass

    def check_hooks_execution(self) -> bool:
        """Runtime hooks execution tests (import validation, hook invocation, sync)."""
        self.log('Hooks Execution Tests', 'header')
        all_pass = True
        hooks_dir = self.claude_target / 'hooks'

        # Load hook manifest (single source of truth for filenames)
        # Note: hook file *presence* is checked by config-guardian.ps1.
        # This method only tests execution and sync.
        manifest_path = self.repo_root / 'tools' / 'lib' / 'hook-manifest.json'
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)

        hook_files = [h['file'] for h in manifest['hooks'] if h['type'] == 'hook']
        core_modules = manifest['core_modules']

        # Test imports
        import subprocess
        test_script = '''
import sys
sys.path.insert(0, r"{hooks_dir}")
errors = []
try:
    from core.hook_logger import HookTimer
except Exception as e:
    errors.append(f"hook_logger: {{e}}")
try:
    from core.calibration import load_calibration, is_safety_enabled
except Exception as e:
    errors.append(f"calibration: {{e}}")
try:
    from core.pattern_matcher import detect_task_type, get_injection_text
except Exception as e:
    errors.append(f"pattern_matcher: {{e}}")
try:
    from core.memory_reader import read_claude_history
except Exception as e:
    errors.append(f"memory_reader: {{e}}")
try:
    from core.session_state import get_session_state
except Exception as e:
    errors.append(f"session_state: {{e}}")
try:
    from core.version import get_version_display
except Exception as e:
    errors.append(f"version: {{e}}")
try:
    from core.learning_detector import detect_learnings
except Exception as e:
    errors.append(f"learning_detector: {{e}}")
try:
    from core.strike_counter import StrikeCounter
except Exception as e:
    errors.append(f"strike_counter: {{e}}")

if errors:
    print("IMPORT_ERRORS:" + "|".join(errors))
else:
    print("IMPORTS_OK")
'''.format(hooks_dir=hooks_dir)

        try:
            result = subprocess.run(
                [sys.executable, '-c', test_script],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout.strip()
            if 'IMPORTS_OK' in output:
                self.log('Core module imports: all successful', 'pass')
            elif 'IMPORT_ERRORS:' in output:
                for err in output.split('IMPORT_ERRORS:')[1].split('|'):
                    self.log(f'Import error: {err}', 'fail')
                all_pass = False
            else:
                self.log(f'Import test unexpected output: {output[:100]}', 'warn')
                if result.stderr:
                    self.log(f'stderr: {result.stderr[:200]}', 'warn')
        except subprocess.TimeoutExpired:
            self.log('Import test timed out', 'fail')
            all_pass = False
        except Exception as e:
            self.log(f'Import test failed: {e}', 'fail')
            all_pass = False

        # Test hook execution (metadata from manifest)
        test_hooks = [
            (h['file'], h.get('test_input', '{}'), h.get('expected_key'))
            for h in manifest['hooks'] if h['type'] == 'hook'
        ]
        for hook_file, test_input, expected_key in test_hooks:
            hook_path = hooks_dir / hook_file
            try:
                result = subprocess.run(
                    [sys.executable, str(hook_path)],
                    input=test_input,
                    capture_output=True, text=True, timeout=15,
                    cwd=str(hooks_dir)
                )
                if result.returncode != 0:
                    self.log(f'{hook_file} execution: exit code {result.returncode}', 'fail')
                    if result.stderr:
                        self.log(f'  stderr: {result.stderr[:150]}', 'info')
                    all_pass = False
                else:
                    try:
                        output_data = json.loads(result.stdout) if result.stdout.strip() else {}
                        if expected_key and expected_key not in output_data:
                            self.log(f'{hook_file} execution: missing {expected_key}', 'warn')
                        else:
                            self.log(f'{hook_file} execution: OK', 'pass')
                    except json.JSONDecodeError:
                        self.log(f'{hook_file} execution: invalid JSON output', 'warn')
            except subprocess.TimeoutExpired:
                self.log(f'{hook_file} execution: TIMEOUT (>15s)', 'fail')
                all_pass = False
            except Exception as e:
                self.log(f'{hook_file} execution: {e}', 'fail')
                all_pass = False

        # Test calibration
        try:
            result = subprocess.run(
                [sys.executable, '-c', f'''
import sys
sys.path.insert(0, r"{hooks_dir}")
from core.calibration import load_calibration
cal = load_calibration()
print(f"verification_budget={{cal.get('verification', {{}}).get('default_budget', 'N/A')}}")
print(f"auto_inject={{cal.get('safety', {{}}).get('auto_inject_sources', 'N/A')}}")
print(f"autonomous_learning={{cal.get('safety', {{}}).get('autonomous_learning', 'N/A')}}")
'''],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if '=' in line:
                        key, val = line.split('=', 1)
                        self.log(f'Calibration {key}: {val}', 'pass')
            else:
                self.log(f'Calibration read failed: {result.stderr[:100]}', 'warn')
        except Exception as e:
            self.log(f'Calibration test failed: {e}', 'warn')

        # Hooks source vs deployed sync
        self.log('Hooks Sync Check', 'header')
        if self.hooks_src_dir.is_dir():
            for sf in hook_files + ['hook_wrapper.cmd']:
                src = self.hooks_src_dir / sf
                dep = hooks_dir / sf
                if src.exists() and dep.exists():
                    src_hash = self.get_file_hash(src)
                    dep_hash = self.get_file_hash(dep)
                    if src_hash == dep_hash:
                        self.log(f'{sf}: in sync', 'pass')
                    else:
                        newer = 'source' if src.stat().st_mtime > dep.stat().st_mtime else 'deployed'
                        self.log(f'{sf}: DRIFT ({newer} is newer)', 'warn')

            src_core = self.hooks_src_dir / 'core'
            dep_core = hooks_dir / 'core'
            if src_core.is_dir() and dep_core.is_dir():
                for mod in core_modules:
                    src = src_core / mod
                    dep = dep_core / mod
                    if src.exists() and dep.exists():
                        if self.get_file_hash(src) != self.get_file_hash(dep):
                            newer = 'source' if src.stat().st_mtime > dep.stat().st_mtime else 'deployed'
                            self.log(f'core/{mod}: DRIFT ({newer} is newer)', 'warn')

        return all_pass

    def check_recent_hook_timeouts(self) -> bool:
        """Surface externally killed hooks recorded in Claude's transcripts.

        The newest deployed settings or hook-code mtime is the lower bound.
        A hook implementation fix must clear failures from the code it replaced
        even when settings.json itself did not change.
        """
        self.log('Recent Claude Hook Timeouts', 'header')
        projects_dir = self.claude_target / 'projects'
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        settings_path = self.claude_target / 'settings.json'
        window = 'in the last 24h'

        deployment_markers = [settings_path]
        hooks_path = self.claude_target / 'hooks'
        if hooks_path.is_dir():
            deployment_markers.extend(hooks_path.glob('*.py'))
            deployment_markers.extend(hooks_path.glob('core/*.py'))
            deployment_markers.extend(hooks_path.glob('core/detectors/*.py'))

        newest_deployment = None
        for marker in deployment_markers:
            try:
                deployed_at = datetime.fromtimestamp(
                    marker.stat().st_mtime, tz=timezone.utc
                )
                if newest_deployment is None or deployed_at > newest_deployment:
                    newest_deployment = deployed_at
            except OSError:
                pass
        if newest_deployment is not None and newest_deployment > since:
            since = newest_deployment
            window = 'since the current settings or hook code was deployed'

        try:
            records = scan_claude_hook_timeouts(
                projects_dir,
                since=since,
                now=now,
            )
        except Exception as exc:
            self.log(f'Could not audit Claude hook timeouts: {exc}', 'warn')
            return False

        if not records:
            self.log(f'No Claude-reported hook timeouts {window}', 'pass')
            return True

        summary = summarize_hook_timeouts(records)
        latest = summary['latest'][0]
        self.log(
            f"Claude reported {summary['total']} hook timeout(s) {window}; "
            f"latest={latest['event']} {latest['duration_ms']}ms/"
            f"{latest['timeout_ms']}ms command={latest['command']}",
            'warn',
        )
        return False

    def check_version(self) -> bool:
        """Check version and compare deployed vs repo."""
        self.log("Version Check", 'header')
        all_pass = True
        repo_version = None

        if self.version_file.exists():
            try:
                import yaml
                with open(self.version_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    repo_version = data.get('version', 'unknown')
                    repo_date = data.get('last_updated', 'unknown')
                self.log(f"Repo version: {repo_version} ({repo_date})", 'pass')
            except Exception as e:
                self.log(f"Failed to read version.yaml: {e}", 'fail')
                all_pass = False
        else:
            self.log(f"version.yaml not found at {self.version_file}", 'warn')

        # The deployed ~/.claude/CLAUDE.md is the global session contract, sourced from
        # platforms/claude-code/CLAUDE.global.md; the repo-root CLAUDE.md is repo context only.
        source_claude = self.repo_root / 'platforms' / 'claude-code' / 'CLAUDE.global.md'
        deployed_claude = self.claude_target / 'CLAUDE.md'
        if deployed_claude.exists():
            try:
                if source_claude.is_file() and (
                    self.get_sha256(source_claude) == self.get_sha256(deployed_claude)
                ):
                    self.log("Deployed CLAUDE.md matches source", 'pass')
                else:
                    self.log("Deployed CLAUDE.md differs from source", 'warn')
                    self.log("Run tools/deploy.ps1 to update", 'info')
                    all_pass = False
            except Exception as e:
                self.log(f"Failed to read deployed CLAUDE.md: {e}", 'fail')
                all_pass = False
        else:
            self.log(f"Deployed CLAUDE.md not found: {deployed_claude}", 'fail')
            all_pass = False

        return all_pass

    def check_settings_precedence(self) -> bool:
        """Report merged permission scopes without deleting project settings.

        Claude merges array-valued permission rules across scopes; deny rules
        take precedence. A shorter project allow-list therefore cannot shadow a
        user allow-list and is never grounds for deleting settings.local.json.
        """
        self.log("Settings Precedence Check", 'header')
        all_pass = True
        user_settings = self.claude_target / 'settings.json'
        user_permission_count = 0
        project_permission_count = 0
        user_allow: set[str] = set()

        if user_settings.exists():
            try:
                with open(user_settings, 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
                permissions = user_data.get('permissions', {}).get('allow', [])
                user_permission_count = len(permissions)
                user_allow = {str(item) for item in permissions}

                if user_permission_count >= 100:
                    self.log(f"User settings: {user_permission_count} permissions (comprehensive)", 'pass')
                elif user_permission_count >= 20:
                    self.log(f"User settings: {user_permission_count} permissions (moderate)", 'pass')
                elif user_permission_count > 0:
                    self.log(f"User settings: {user_permission_count} permissions (limited)", 'warn')
                    self.log("Consider deploying comprehensive settings from users/<username>/", 'info')
                else:
                    self.log("User settings: no permissions defined", 'warn')
                    all_pass = False
            except Exception as e:
                # JSON validity is checked by config-guardian.ps1; just warn here
                self.log(f"Could not read user settings.json (run config-guardian): {e}", 'warn')
        else:
            self.log(f"User settings not found: {user_settings}", 'fail')
            self.log("Run tools/deploy.ps1 to deploy user-specific settings", 'info')
            all_pass = False

        project_root = os.environ.get('CLAUDE_PROJECT_ROOT', '')
        candidates = [Path(project_root) if project_root else None, self.repo_root, Path.cwd()]
        project_dirs: list[Path] = []
        seen_projects: set[str] = set()
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            key = os.path.normcase(str(resolved))
            if key not in seen_projects:
                seen_projects.add(key)
                project_dirs.append(resolved)

        for project_dir in project_dirs:
            if not project_dir.exists():
                continue
            project_settings = project_dir / '.claude' / 'settings.local.json'
            if project_settings.exists():
                try:
                    with open(project_settings, 'r', encoding='utf-8') as f:
                        project_data = json.load(f)
                    perms = project_data.get('permissions', {}).get('allow', [])
                    denies = project_data.get('permissions', {}).get('deny', [])
                    project_permission_count += len(perms)
                    conflicts = sorted(user_allow.intersection(str(item) for item in denies))
                    self.log(
                        f"Project settings at {project_dir.name}: {len(perms)} allow, "
                        f"{len(denies)} deny (arrays merge across scopes)",
                        'pass',
                    )
                    if conflicts:
                        self.log(
                            "Project deny rules intentionally take precedence over matching "
                            f"user allows: {', '.join(conflicts[:3])}",
                            'warn',
                        )
                except Exception as e:
                    self.log(f"Failed to parse {project_settings}: {e}", 'warn')

        if user_permission_count == 0 and project_permission_count == 0:
            self.log("NO PERMISSIONS CONFIGURED - Claude will prompt for everything!", 'fail')
            self.log("Run: tools/deploy.ps1 -ClaudeOnly to deploy user settings", 'info')
            all_pass = False

        return all_pass

    def check_peer_review_runs(self) -> bool:
        """Surface stale or terminal-but-unconsumed durable broker obligations."""
        self.log("Peer Review Run Health", 'header')
        runs_root = self.repo_root / '.obi' / 'review' / 'runs'
        if not runs_root.is_dir():
            self.log("No durable peer-review runs recorded", 'pass')
            return True
        all_pass = True
        active = 0
        unconsumed = 0
        now = datetime.now(timezone.utc)
        try:
            statuses = sorted(
                runs_root.glob('*/status.json'),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )[:100]
        except OSError as exc:
            self.log(f"Could not enumerate peer-review runs: {exc}", 'fail')
            return False
        for status_path in statuses:
            try:
                status = read_json_file(
                    status_path,
                    max_bytes=1024 * 1024,
                    use_lock=True,
                )
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                self.log(f"Unreadable peer status {status_path}: {exc}", 'fail')
                all_pass = False
                continue
            if status.get('run_mode') != 'broker':
                continue
            state = str(status.get('transport_status') or 'unknown')
            if status.get('terminal') is True:
                if status.get('killed') is True and status.get('kill_verified') is not True:
                    self.log(
                        "Unverified peer process-tree termination: "
                        f"{status.get('run_id')} ({state})",
                        'fail',
                    )
                    all_pass = False
                if state != 'cancelled' and not status.get('result_consumed_at'):
                    unconsumed += 1
                    self.log(
                        f"Unconsumed terminal peer result: {status.get('run_id')} ({state})",
                        'warn',
                    )
                    all_pass = False
                continue
            active += 1
            heartbeat = status.get('heartbeat_at')
            try:
                parsed = datetime.fromisoformat(str(heartbeat).replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age = (now - parsed).total_seconds()
            except (TypeError, ValueError):
                age = float('inf')
            if age > 30:
                self.log(
                    f"Stale peer broker heartbeat: {status.get('run_id')} ({age:.1f}s, {state})",
                    'fail',
                )
                all_pass = False
        if active == 0 and unconsumed == 0:
            self.log("No outstanding broker obligations", 'pass')
        elif active:
            self.log(f"Active broker reviews with current receipts: {active}", 'pass')
        return all_pass

    def check_auto_memory(self) -> bool:
        """Verify CLAUDE_CODE_DISABLE_AUTO_MEMORY=0 for three-tier knowledge system."""
        self.log("Auto-Memory Configuration", 'header')

        val = os.environ.get('CLAUDE_CODE_DISABLE_AUTO_MEMORY')
        if val == '0':
            self.log("CLAUDE_CODE_DISABLE_AUTO_MEMORY=0 (auto-memory enabled)", 'pass')
            return True
        elif val is None:
            self.log("CLAUDE_CODE_DISABLE_AUTO_MEMORY not set — auto-memory disabled by default", 'fail')
            self.log("Fix: [System.Environment]::SetEnvironmentVariable('CLAUDE_CODE_DISABLE_AUTO_MEMORY','0','User')", 'info')
            return False
        else:
            self.log(f"CLAUDE_CODE_DISABLE_AUTO_MEMORY={val} — auto-memory is disabled", 'fail')
            self.log("Obi's Tier 1 (MEMORY.md) requires this set to 0", 'info')
            return False

    def check_drift(self) -> bool:
        """Check if deployed files match source (detect manual edits)."""
        self.log("Drift Detection (deployed vs source)", 'header')
        drift_found = False

        # Phase commands
        for phase_name, cmd_name in self.phase_command_map.items():
            source = self.phases_dir / phase_name / 'command.md'
            deployed = self.claude_target / 'commands' / cmd_name
            if source.exists() and deployed.exists():
                src_hash = self.get_file_hash(source)
                dep_hash = self.get_file_hash(deployed)
                if src_hash == dep_hash:
                    self.log(f"{cmd_name}: in sync ({src_hash})", 'pass')
                else:
                    self.log(f"{cmd_name}: DRIFT (src:{src_hash} != dep:{dep_hash})", 'warn')
                    drift_found = True

        # Standalone commands from orchestration/
        for standalone in ['obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-memory-review.md']:
            source = self.orchestration_dir / standalone
            deployed = self.claude_target / 'commands' / standalone
            if source.exists() and deployed.exists():
                src_hash = self.get_file_hash(source)
                dep_hash = self.get_file_hash(deployed)
                if src_hash == dep_hash:
                    self.log(f"{standalone}: in sync ({src_hash})", 'pass')
                else:
                    self.log(f"{standalone}: DRIFT (src:{src_hash} != dep:{dep_hash})", 'warn')
                    drift_found = True

        # Codex project files
        codex_pairs = [
            (self.codex_src_dir / 'AGENTS.md', self.repo_root / 'AGENTS.md', 'Codex AGENTS.md'),
            (self.codex_src_dir / 'hooks.json', self.repo_root / '.codex' / 'hooks.json', 'Codex hooks.json'),
        ]
        for source, deployed, label in codex_pairs:
            if source.exists() and deployed.exists():
                src_hash = self.get_file_hash(source)
                dep_hash = self.get_file_hash(deployed)
                if src_hash == dep_hash:
                    self.log(f"{label}: in sync ({src_hash})", 'pass')
                else:
                    self.log(f"{label}: DRIFT (src:{src_hash} != dep:{dep_hash})", 'warn')
                    drift_found = True

        if drift_found:
            self.log("Drift detected - run tools/deploy.ps1 to sync.", 'info')

        return True

    def check_peer_review_harness(self) -> bool:
        """Verify canonical peer-review artifacts, deployment hashes, and cutover."""
        self.log("Peer Review Harness", 'header')
        all_pass = True

        for provider in ('codex', 'claude'):
            executable = shutil.which(provider)
            if executable:
                self.log(f"{provider} on PATH: {executable}", 'pass')
            else:
                self.log(f"{provider} not found on PATH; that peer direction is unavailable", 'warn')

        required = [
            'peer-review.ps1',
            'peer-review.py',
            'peer_review/__init__.py',
            'peer_review/adapters.py',
            'peer_review/broker.py',
            'peer_review/cli.py',
            'peer_review/io_utils.py',
            'peer_review/packet.py',
            'peer_review/process_control.py',
            'peer_review/runner.py',
            'peer_review/validation.py',
            'schemas/peer-review-request.schema.json',
            'schemas/peer-review-scope-manifest.schema.json',
            'schemas/peer-review-result.schema.json',
            'schemas/peer-review-status.schema.json',
        ]
        for relative in required:
            if not self.check_deployed_tool_hash(relative, 'Peer artifact'):
                all_pass = False

        deployed_tools = self.tools_target / 'tools'

        policy_source = self.policies_dir / 'peer-review.md'
        policy_deployed = self.claude_target / 'docs' / 'policies' / 'peer-review.md'
        if not policy_source.is_file() or not policy_deployed.is_file():
            self.log("Peer-review policy source/deployment is incomplete", 'fail')
            all_pass = False
        elif self.get_sha256(policy_source) != self.get_sha256(policy_deployed):
            self.log("Peer-review policy deployment drift", 'fail')
            all_pass = False
        else:
            self.log("Peer-review policy in sync", 'pass')

        stale_skill = self.home / '.agents' / 'skills' / 'codex-adversarial-review'
        if stale_skill.exists():
            self.log(f"Retired unmanaged peer skill remains active: {stale_skill}", 'fail')
            all_pass = False
        else:
            self.log("Retired unmanaged peer skill absent", 'pass')

        for relative in (
            'codex-run.ps1', 'codex-run.tests.ps1', 'codex-plan-prep.ps1',
            'codex-plan-prep.tests.ps1', 'schemas/codex-plan-critique.schema.json',
        ):
            legacy = deployed_tools / Path(relative)
            if legacy.exists():
                self.log(f"Retired peer-review path remains deployed: {legacy}", 'fail')
                all_pass = False

        if all_pass:
            self.log("Canonical peer-review cutover is complete", 'pass')
        return all_pass

    # =========================================================================
    # Main Entry Points
    # =========================================================================

    def run_quick(self) -> bool:
        """Quick smoke test - deployment exists and has content."""
        print(color(BOLD + "=" * 60, CYAN))
        print(color(BOLD + "  Obi Wag Quick Health Check", CYAN))
        print(color(BOLD + "=" * 60, CYAN))

        self.check_version()
        self.check_deployment()
        self.check_peer_review_harness()
        self.check_peer_review_runs()
        self.check_auto_memory()
        self.check_settings_precedence()
        self.check_content_integrity()
        self.check_hooks_execution()
        self.check_recent_hook_timeouts()

        return self._print_summary()

    def run_full(self) -> bool:
        """Full health check - verify everything."""
        print(color(BOLD + "=" * 60, CYAN))
        print(color(BOLD + "  Obi Wag Full Health Check", CYAN))
        print(color(BOLD + "=" * 60, CYAN))

        self.check_version()
        self.check_source_structure()
        self.check_codex_source()
        self.check_deployment()
        self.check_peer_review_harness()
        self.check_peer_review_runs()
        self.check_auto_memory()
        self.check_settings_precedence()
        self.check_content_integrity()
        self.check_hooks_execution()
        self.check_recent_hook_timeouts()
        self.check_drift()

        return self._print_summary()

    def _print_summary(self) -> bool:
        print(color(BOLD + "\n" + "=" * 60, CYAN))
        print(color(BOLD + "  Summary", CYAN))
        print(color(BOLD + "=" * 60, CYAN))

        total = self.checks_passed + self.checks_failed
        print(f"\n  Checks passed: {color(str(self.checks_passed), GREEN)}/{total}")
        if self.checks_failed > 0:
            print(f"  Checks failed: {color(str(self.checks_failed), RED)}")
        if self.warnings > 0:
            print(f"  Warnings:      {color(str(self.warnings), YELLOW)}")

        strict_failed = self.strict and self.warnings > 0

        if self.checks_failed == 0 and self.warnings == 0:
            print(color(BOLD + "\n  STATUS: HEALTHY", GREEN))
            print("\n  Obi Wag is properly deployed and ready to use.")
            print("  - Claude Code: Run /obi to start orchestrator")
            print("  - Codex: use /obi or /review as prompt-level commands")
        elif self.checks_failed == 0 and not strict_failed:
            print(color(BOLD + "\n  STATUS: HEALTHY_WITH_WARNINGS", YELLOW))
            print("\n  Obi Wag is deployed, but warnings above should be reviewed before gating.")
            print("  Re-run with --strict to make warnings fail the health check.")
        else:
            print(color(BOLD + "\n  STATUS: UNHEALTHY", RED))
            print("\n  Issues detected. Review failures above and:")
            print("  1. Run tools/deploy.ps1 to redeploy")
            print("  2. Re-run this health check")

        print()
        return self.checks_failed == 0 and not strict_failed


def main():
    parser = argparse.ArgumentParser(
        description='Obi Wag Health Check - Validate deployment status',
        epilog='''
Examples:
  python tools/healthcheck.py           # Full health check
  python tools/healthcheck.py --quick   # Fast validation only
  python tools/healthcheck.py --repo-root C:/src/obiwag-agents
  python tools/healthcheck.py -v        # Verbose output
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('--quick', '-q', action='store_true',
                        help='Quick smoke test (deployment + content only)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output with additional details')
    parser.add_argument('--strict', action='store_true',
                        help='Treat warnings as failures for gating')
    parser.add_argument('--repo-root', type=Path,
                        help='Path to the obiwag-agents source tree (auto-detected by default)')

    args = parser.parse_args()
    try:
        checker = HealthCheck(
            verbose=args.verbose,
            strict=args.strict,
            repo_root=args.repo_root,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.quick:
        success = checker.run_quick()
    else:
        success = checker.run_full()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
