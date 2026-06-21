#!/usr/bin/env python3
"""
Strike Counter Module

Implements Strike #2 checkpoint enforcement to prevent wasted iterations
when simple clarification would solve the issue.

Evolution: evolution-001-pipeline-auto-fix.md
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


# Loop limits
MAX_LOOPS_PER_PHASE = 3
MAX_TOTAL_LOOPS = 10
MAX_STRIKES = 3


class StrikeCounter:
    """Track consecutive failures and trigger checkpoints."""

    def __init__(self, state_file: Optional[str] = None):
        """
        Initialize strike counter with persistent state.

        Args:
            state_file: Path to state file (defaults to .obi/strike-state.json)
        """
        if state_file:
            self.state_file = Path(state_file)
        else:
            self.state_file = Path.cwd() / '.obi' / 'strike-state.json'

        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load strike counter state from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        return {
            'strikes': [],
            'current_issue': None,
            'checkpoint_triggered': False,
            'phase_loops': {},
            'total_loops': 0,
            'current_phase': None
        }

    def _save_state(self) -> None:
        """Save strike counter state to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except IOError:
            pass

    def record_failure(self, issue_signature: str, context: Dict) -> Dict:
        """
        Record a failure and check if checkpoint should trigger.

        Args:
            issue_signature: Unique identifier for this type of issue
                            (e.g., "linting:config_not_found", "test:assertion_error")
            context: Dictionary with failure details:
                     {
                         'error_message': str,
                         'file': str,
                         'attempt_description': str,
                         'timestamp': str
                     }

        Returns:
            Dictionary with strike analysis:
            {
                'strike_count': int,
                'should_checkpoint': bool,
                'checkpoint_message': Optional[str],
                'previous_attempts': List[Dict]
            }
        """
        # Check if this is a new issue or continuation
        if self.state['current_issue'] != issue_signature:
            # New issue - reset counter
            self.state['strikes'] = []
            self.state['current_issue'] = issue_signature
            self.state['checkpoint_triggered'] = False

        # Record this strike
        strike_record = {
            'issue_signature': issue_signature,
            'timestamp': context.get('timestamp', datetime.now().isoformat()),
            'error_message': context.get('error_message', ''),
            'file': context.get('file', ''),
            'attempt': context.get('attempt_description', '')
        }
        self.state['strikes'].append(strike_record)

        strike_count = len(self.state['strikes'])
        should_checkpoint = (strike_count == 2 and not self.state['checkpoint_triggered'])

        result = {
            'strike_count': strike_count,
            'should_checkpoint': should_checkpoint,
            'checkpoint_message': None,
            'previous_attempts': self.state['strikes']
        }

        # Generate checkpoint message if threshold reached
        if should_checkpoint:
            result['checkpoint_message'] = self._generate_checkpoint_message(
                issue_signature,
                self.state['strikes']
            )
            self.state['checkpoint_triggered'] = True

        self._save_state()
        return result

    def _generate_checkpoint_message(self, issue_signature: str, strikes: List[Dict]) -> str:
        """
        Generate human-readable checkpoint message.

        Args:
            issue_signature: Issue identifier
            strikes: List of strike records

        Returns:
            Formatted checkpoint message for Claude to present to user
        """
        attempts_summary = []
        for i, strike in enumerate(strikes, 1):
            attempts_summary.append(
                f"**Attempt {i}:** {strike['attempt']}\n"
                f"   Error: {strike['error_message'][:100]}..."
            )

        message = f"""
## ⚠️ Strike #2 Checkpoint

This issue has failed **twice** in a row. Before attempting a third fix, let's pause.

**Issue:** `{issue_signature}`

**Previous Attempts:**
{chr(10).join(attempts_summary)}

**Options:**
1. **Proceed with Strike #3** - I can attempt one more fix based on current understanding
2. **Provide guidance** - If you know the root cause or have context that would help
3. **Find examples** - I can search the repo for working examples of this pattern
4. **Skip this fix** - Move on and address this separately

Should I proceed with Strike #3, or would you like to provide input first?
"""
        return message.strip()

    def record_success(self) -> None:
        """Record successful resolution, reset counter."""
        self.state = {
            'strikes': [],
            'current_issue': None,
            'checkpoint_triggered': False,
            'phase_loops': self.state.get('phase_loops', {}),
            'total_loops': self.state.get('total_loops', 0),
            'current_phase': self.state.get('current_phase'),
        }
        self._save_state()

    def reset_counter(self, new_approach: Optional[str] = None) -> None:
        """
        Reset strike counter when new approach is taken.

        Args:
            new_approach: Optional description of new approach being tried
        """
        old_issue = self.state['current_issue']

        # Keep history but mark as resolved with new approach
        if new_approach and self.state['strikes']:
            self.state['strikes'].append({
                'issue_signature': old_issue,
                'timestamp': datetime.now().isoformat(),
                'resolution': f'New approach: {new_approach}'
            })

        self.state['current_issue'] = None
        self.state['checkpoint_triggered'] = False
        self._save_state()

    def get_current_status(self) -> Dict:
        """
        Get current strike counter status.

        Returns:
            Dictionary with current state
        """
        return {
            'strike_count': len(self.state['strikes']),
            'current_issue': self.state['current_issue'],
            'checkpoint_triggered': self.state['checkpoint_triggered'],
            'recent_strikes': self.state['strikes'][-5:],  # Last 5 strikes
            'phase_loops': self.state.get('phase_loops', {}),
            'total_loops': self.state.get('total_loops', 0),
            'current_phase': self.state.get('current_phase'),
        }

    def record_phase_loop(self, phase_name: str) -> Dict:
        """
        Record a loop iteration for a specific phase.

        Args:
            phase_name: Name of the phase (e.g., "2_author", "4_review")

        Returns:
            Dictionary with loop analysis:
            {
                'phase_loops': int,
                'total_loops': int,
                'should_escalate': bool,
                'escalation_reason': Optional[str]
            }
        """
        # Initialize if needed
        if 'phase_loops' not in self.state:
            self.state['phase_loops'] = {}
        if 'total_loops' not in self.state:
            self.state['total_loops'] = 0

        # Increment counters
        self.state['phase_loops'][phase_name] = self.state['phase_loops'].get(phase_name, 0) + 1
        self.state['total_loops'] += 1
        self.state['current_phase'] = phase_name

        phase_count = self.state['phase_loops'][phase_name]
        total_count = self.state['total_loops']

        result = {
            'phase_loops': phase_count,
            'total_loops': total_count,
            'should_escalate': False,
            'escalation_reason': None
        }

        # Check limits
        if phase_count > MAX_LOOPS_PER_PHASE:
            result['should_escalate'] = True
            result['escalation_reason'] = f"Max loops per phase exceeded ({phase_count}/{MAX_LOOPS_PER_PHASE})"
        elif total_count > MAX_TOTAL_LOOPS:
            result['should_escalate'] = True
            result['escalation_reason'] = f"Total workflow loops exceeded ({total_count}/{MAX_TOTAL_LOOPS})"

        self._save_state()
        return result

    def reset_phase_loops(self, phase_name: Optional[str] = None) -> None:
        """
        Reset loop counters for a specific phase or all phases.

        Args:
            phase_name: Optional phase name to reset. If None, resets all.
        """
        if phase_name:
            if 'phase_loops' in self.state:
                self.state['phase_loops'][phase_name] = 0
        else:
            self.state['phase_loops'] = {}
            self.state['total_loops'] = 0
            self.state['current_phase'] = None

        self._save_state()

    def complete_phase(self, phase_name: str) -> None:
        """
        Mark a phase as complete, resetting its loop counter.

        Args:
            phase_name: Name of the completed phase
        """
        if 'phase_loops' in self.state:
            self.state['phase_loops'][phase_name] = 0
        self.state['current_phase'] = None
        self._save_state()


# Convenience function for use in hooks
def check_strike_status(issue_signature: str, error_context: Dict, state_file: Optional[str] = None) -> Dict:
    """
    Check strike status and determine if checkpoint should trigger.

    Args:
        issue_signature: Unique identifier for issue type
        error_context: Dictionary with error details
        state_file: Optional custom state file location

    Returns:
        Strike analysis dictionary

    Example:
        >>> result = check_strike_status(
        ...     'linting:config_not_found',
        ...     {'error_message': 'File not found', 'attempt_description': 'Tried root/.psscriptanalyzer.psd1'}
        ... )
        >>> if result['should_checkpoint']:
        ...     print(result['checkpoint_message'])
    """
    counter = StrikeCounter(state_file)
    return counter.record_failure(issue_signature, error_context)


def record_loop(phase_name: str, state_file: Optional[str] = None) -> Dict:
    """
    Record a loop iteration and check if escalation is needed.

    Args:
        phase_name: Name of the phase (e.g., "2_author", "4_review")
        state_file: Optional custom state file location

    Returns:
        Loop analysis dictionary with should_escalate and escalation_reason

    Example:
        >>> result = record_loop('4_review')
        >>> if result['should_escalate']:
        ...     print(f"NEEDS USER INPUT: {result['escalation_reason']}")
    """
    counter = StrikeCounter(state_file)
    return counter.record_phase_loop(phase_name)


def complete_phase(phase_name: str, state_file: Optional[str] = None) -> None:
    """
    Mark a phase as complete, resetting its loop counter.

    Args:
        phase_name: Name of the completed phase
        state_file: Optional custom state file location
    """
    counter = StrikeCounter(state_file)
    counter.complete_phase(phase_name)
