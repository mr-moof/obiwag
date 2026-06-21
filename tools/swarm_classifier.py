#!/usr/bin/env python3
"""
Swarm Issue Classifier - Risk classification for parallel issue resolution.

Classifies GitHub issues into risk tiers to determine merge order and
worker isolation requirements.

Usage:
    python tools/swarm_classifier.py --title "..." --body "..." [--labels "..."]
    python tools/swarm_classifier.py --batch issues.json

Tiers:
    config-only  : docs, yaml, env, comments, typos
    single-file  : one code file change (fix, bug, rename)
    multi-file   : cross-module refactors, renames-across-files
"""

import argparse
import json
import sys
from typing import Dict, List, Optional


# Keywords that signal each risk tier (checked against title + body)
TIER_KEYWORDS = {
    'config-only': [
        'readme', 'changelog', 'documentation', 'docs', 'typo', 'comment',
        'newline', 'whitespace', 'formatting', 'yaml', 'env', '.md',
        'license', 'copyright year', 'spelling', 'docstring', 'annotation',
    ],
    'single-file': [
        'fix', 'bug', 'rename function', 'add function', 'add method',
        'update function', 'default value', 'parameter', 'return type',
        'error message', 'log message', 'variable', 'constant',
    ],
    'multi-file': [
        'refactor', 'rename across', 'cross-module', 'migration',
        'restructure', 'move to', 'split into', 'merge into',
        'update all', 'replace everywhere', 'global rename',
    ],
}

# Explicit label-to-tier mapping (highest priority)
LABEL_TIER_MAP = {
    'risk:config': 'config-only',
    'risk:config-only': 'config-only',
    'risk:single': 'single-file',
    'risk:single-file': 'single-file',
    'risk:multi': 'multi-file',
    'risk:multi-file': 'multi-file',
    'documentation': 'config-only',
    'docs': 'config-only',
    'typo': 'config-only',
    'bug': 'single-file',
    'refactor': 'multi-file',
}

DEFAULT_TIER = 'single-file'


def classify_issue(
    title: str,
    description: str = '',
    labels: Optional[List[str]] = None,
) -> str:
    """Classify a single issue into a risk tier.

    Priority: explicit labels > keyword matching > default.

    Args:
        title: Issue title.
        description: Issue body/description.
        labels: List of issue labels.

    Returns:
        Risk tier string: 'config-only', 'single-file', or 'multi-file'.
    """
    # 1. Label-first: check for explicit risk labels
    if labels:
        for label in labels:
            normalized = label.strip().lower()
            if normalized in LABEL_TIER_MAP:
                return LABEL_TIER_MAP[normalized]

    # 2. Keyword matching against title + description
    text = f"{title} {description}".lower()
    scores: Dict[str, int] = {'config-only': 0, 'single-file': 0, 'multi-file': 0}

    for tier, keywords in TIER_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                scores[tier] += 1

    # Return tier with highest score (if any matches found)
    max_score = max(scores.values())
    if max_score > 0:
        # On tie, prefer lower-risk tier (config-only < single-file < multi-file)
        tier_priority = ['config-only', 'single-file', 'multi-file']
        for tier in tier_priority:
            if scores[tier] == max_score:
                return tier

    # 3. Default fallback
    return DEFAULT_TIER


def classify_batch(
    issues: List[Dict],
) -> Dict[str, List[Dict]]:
    """Classify a batch of issues and group by risk tier.

    Args:
        issues: List of dicts with keys: id, title, description, labels.

    Returns:
        Dict mapping tier names to lists of issues in that tier.
    """
    grouped: Dict[str, List[Dict]] = {
        'config-only': [],
        'single-file': [],
        'multi-file': [],
    }

    for issue in issues:
        tier = classify_issue(
            title=issue.get('title', ''),
            description=issue.get('description', ''),
            labels=issue.get('labels', []),
        )
        issue_with_tier = {**issue, 'tier': tier}
        grouped[tier].append(issue_with_tier)

    return grouped


def main():
    parser = argparse.ArgumentParser(
        description='Classify GitHub issues into risk tiers for swarm processing.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('--title', '-t', help='Issue title')
    parser.add_argument('--body', '-b', default='', help='Issue body/description')
    parser.add_argument('--labels', '-l', default='', help='Comma-separated labels')
    parser.add_argument('--batch', help='Path to JSON file with issues array')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    if args.batch:
        with open(args.batch, 'r', encoding='utf-8') as f:
            issues = json.load(f)
        result = classify_batch(issues)
        print(json.dumps(result, indent=2))
    elif args.title:
        labels = [l.strip() for l in args.labels.split(',') if l.strip()]
        tier = classify_issue(args.title, args.body, labels)
        if args.json:
            print(json.dumps({'title': args.title, 'tier': tier}))
        else:
            print(tier)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
