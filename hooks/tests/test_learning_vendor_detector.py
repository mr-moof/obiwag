"""Unit tests for the vendor/reference-implementation detectors.

Cover the same module they target: ``hooks/core/learning_vendor_detector``.
The detector buckets are generic technology categories (cloud, virtualization,
service-management, authentication); these tests exercise the mechanism
(keyword + knowledge-signal co-occurrence, introspection/path-fragment
suppression, slash-command-body stripping) against those generic buckets.
"""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.learning_detector import (
    LearningType,
    detect_reference_implementations,
    detect_vendor_discoveries,
    detect_learnings,
)


class TestDetectVendorDiscoveries:
    """Tests for detect_vendor_discoveries function."""

    def test_cloud_with_knowledge_signal(self):
        """Cloud keyword + knowledge pattern should trigger a learning."""
        transcript = (
            "Looking at the AWS SDK, the endpoint should be set to the regional "
            "URL and you must always pass an explicit region."
        )
        result = detect_vendor_discoveries(transcript)
        assert len(result) == 1
        assert result[0].type == LearningType.TECHNOLOGY
        assert result[0].target_file == "docs/domain-patterns/cloud.md"
        assert result[0].metadata['category'] == 'cloud'

    def test_casual_mention_no_trigger(self):
        """Casual vendor mention without knowledge signal should not trigger."""
        transcript = "I looked at the aws console today and it loaded fine."
        result = detect_vendor_discoveries(transcript)
        assert result == []

    def test_virtualization_with_imperative_rule(self):
        """Virtualization keyword with imperative rule should trigger."""
        transcript = (
            "When connecting to the hypervisor, you must never use the root "
            "account. Always use the service account for API calls."
        )
        result = detect_vendor_discoveries(transcript)
        assert len(result) == 1
        assert result[0].target_file == "docs/domain-patterns/virtualization.md"

    def test_service_management_with_error_handling(self):
        """Service-management keyword with error handling signal should trigger."""
        transcript = (
            "The ticketing system API returns a warning about rate limiting "
            "but the error is benign and can be safely ignored."
        )
        result = detect_vendor_discoveries(transcript)
        assert len(result) == 1
        assert result[0].target_file == "docs/domain-patterns/service-management.md"

    def test_auth_with_knowledge_signal(self):
        """Authentication keyword + knowledge signal should trigger."""
        transcript = (
            "The OIDC flow endpoint should be set to 'authorization_code' "
            "for the redirect-based login."
        )
        result = detect_vendor_discoveries(transcript)
        assert len(result) == 1
        assert result[0].target_file == "docs/domain-patterns/authentication.md"

    def test_code_introspection_no_trigger(self):
        """Code introspection patterns like Get-Module should not trigger."""
        transcript = (
            "$cloudInScope = $components.LocalPlugins -contains 'cloud'\n"
            "if ($cloudInScope) {\n"
            "    $mod = Get-Module -ListAvailable Some.Cloud.Module\n"
            "    if ($mod) {\n"
            "        Write-Host '[SKIP] Some.Cloud.Module already installed'\n"
            "    }\n"
            "}\n"
        )
        result = detect_vendor_discoveries(transcript)
        assert result == [], f"Expected no learnings from code introspection, got {len(result)}"

    def test_deploy_script_snippet_no_trigger(self):
        """Real deploy.ps1-style snippet should not trigger."""
        transcript = (
            "& gh auth login --hostname github.com\n"
            "}\n}\n}\n\n"
            "# --- Step 9: Check Some.Cloud.Module (informational) ---\n"
            "$cloudInScope = $components.LocalPlugins -contains 'cloud'\n"
            "if ($cloudInScope) {\n"
            "    $mod = Get-Module -ListAvailable Some.Cloud.Module\n"
        )
        result = detect_vendor_discoveries(transcript)
        assert result == [], f"Expected no learnings from deploy script, got {len(result)}"

    def test_multiple_vendors_detected(self):
        """Multiple categories in one transcript should each produce a learning."""
        transcript = (
            "The AWS API endpoint should be set to the regional URL. "
            "Then we set up the ticketing system integration and you must never "
            "call the batch API without pagination."
        )
        result = detect_vendor_discoveries(transcript)
        categories = {l.metadata['category'] for l in result}
        assert 'cloud' in categories
        assert 'service-management' in categories

    def test_path_fragment_not_counted(self):
        """Filename fragments with keywords must not produce a learning even if a
        knowledge-signal token appears nearby (the path-listing false positive).
        """
        transcript = (
            "Found files:\n"
            "  docs/skills/cloud-cost-viewer/SKILL.md\n"
            "  docs/skills/cloud-cost-viewer/README.md\n"
            "  docs/skills/cloud-cost-viewer/api\n"
            "api endpoint is the right place for these.\n"
        )
        result = detect_vendor_discoveries(transcript)
        cloud_learnings = [l for l in result if l.metadata.get('category') == 'cloud']
        assert cloud_learnings == [], (
            f"File-path fragments must not produce learnings, got {len(cloud_learnings)}"
        )

    def test_integration_through_detect_learnings(self):
        """Vendor discoveries should surface through detect_learnings with no corrections."""
        transcript = (
            "The hypervisor API endpoint should be set to 'latest' "
            "and you must always validate the version first."
        )
        result = detect_learnings(transcript, {'corrections': 0}, [])
        tech_learnings = [l for l in result if l.type == LearningType.TECHNOLOGY]
        assert len(tech_learnings) >= 1
        assert tech_learnings[0].target_file == "docs/domain-patterns/virtualization.md"

    def test_empty_transcript(self):
        """Empty transcript should return no learnings."""
        result = detect_vendor_discoveries("")
        assert result == []


class TestDetectReferenceImplementations:
    """Tests for detect_reference_implementations function."""

    def test_oauth_pkce_with_success_signal(self):
        """OAuth PKCE implementation with success signal should trigger."""
        transcript = (
            "We successfully implemented the OAuth2 PKCE flow for the static site. "
            "The authorization code exchange works with the identity provider. "
            "This is a working implementation that can be reused."
        )
        result = detect_reference_implementations(transcript)
        assert len(result) >= 1
        assert result[0].type == LearningType.PATTERN
        assert result[0].confidence == 0.8
        assert 'auth-oauth' in result[0].metadata.get('category', '')

    def test_no_success_signal_no_trigger(self):
        """Integration keyword without success signal should not trigger."""
        transcript = (
            "We discussed the OAuth2 PKCE flow and how it might work. "
            "The authorization code exchange is complex."
        )
        result = detect_reference_implementations(transcript)
        assert result == []

    def test_mcp_server_reference(self):
        """MCP server implementation with success signal should trigger."""
        transcript = (
            "The MCP server is now tested and verified and works correctly. "
            "It provides read-only access to the data via model context protocol tools."
        )
        result = detect_reference_implementations(transcript)
        assert len(result) >= 1
        categories = {l.metadata['category'] for l in result}
        assert 'mcp-integration' in categories

    def test_docker_deployment_reference(self):
        """Docker deployment with success signal should trigger."""
        transcript = (
            "Successfully implemented the Docker compose deployment for the service. "
            "The container build and deploy pipeline is working."
        )
        result = detect_reference_implementations(transcript)
        assert len(result) >= 1
        categories = {l.metadata['category'] for l in result}
        assert 'containerization' in categories

    def test_empty_transcript(self):
        """Empty transcript should return no learnings."""
        result = detect_reference_implementations("")
        assert result == []

    def test_integration_through_detect_learnings(self):
        """Reference implementations should surface through detect_learnings with no corrections."""
        transcript = (
            "We built a working implementation of the OAuth2 PKCE flow. "
            "The authorization code exchange is tested and verified and works."
        )
        result = detect_learnings(transcript, {'corrections': 0}, [])
        pattern_learnings = [l for l in result if l.type == LearningType.PATTERN]
        assert len(pattern_learnings) >= 1
        assert pattern_learnings[0].confidence >= 0.75


class TestSlashCommandBodyFalsePositives:
    """Slash-command bodies must not produce false-positive learnings.

    The /learning, /obi-auto, and similar command files contain example phrases
    like "OAuth PKCE flow for static sites" or "Docker compose deployment that
    works" that previously triggered reference_impl detectors.
    """

    def test_learning_command_body_oauth_phrase_stripped(self):
        """/learning body's "OAuth PKCE flow for static sites" must not trigger auth-oauth."""
        transcript = (
            '<command-message>learning</command-message>\n'
            '<command-name>/learning</command-name>\n'
            '<command-args></command-args>\n'
            '# Capture Session Learnings\n'
            '\n'
            'Examples of cross-project knowledge:\n'
            '- OAuth PKCE flow for static sites (reusable across projects)\n'
            '- We successfully implemented this and it is a working example.\n'
            '"role":"assistant","content":"Done."\n'
        )
        result = detect_reference_implementations(transcript)
        auth_learnings = [l for l in result if l.metadata.get('category') == 'auth-oauth']
        assert auth_learnings == [], (
            f"Slash-command body must be stripped before scanning, got {len(auth_learnings)} false positives"
        )

    def test_obi_auto_body_containerization_stripped(self):
        """/obi-auto body with k8s/docker mentions must not trigger containerization."""
        transcript = (
            '<command-message>obi-auto</command-message>\n'
            '<command-name>/obi-auto</command-name>\n'
            '<command-args>ingest invoice PDF</command-args>\n'
            '# Obi Wag Autonomous Mode\n'
            '\n'
            'Available skills include containerization patterns:\n'
            '- k8s container build and deploy pipeline\n'
            '- Successfully tested and works in production.\n'
            '"role":"assistant","content":"OK."\n'
        )
        result = detect_reference_implementations(transcript)
        container_learnings = [l for l in result if l.metadata.get('category') == 'containerization']
        assert container_learnings == [], (
            f"Slash-command body must be stripped, got {len(container_learnings)} false positives"
        )

    def test_vendor_in_command_body_stripped(self):
        """Vendor mentions inside slash-command bodies must not trigger."""
        transcript = (
            '<command-message>doc</command-message>\n'
            '<command-name>/doc</command-name>\n'
            '<command-args></command-args>\n'
            '# Document Authoring\n'
            '\n'
            'Examples of vendor docs we maintain:\n'
            '- The ticketing system API endpoint should be set to v2 for new code.\n'
            '- The endpoint uses bearer auth.\n'
            '"role":"assistant","content":"Done."\n'
        )
        result = detect_vendor_discoveries(transcript)
        sm_learnings = [l for l in result if l.metadata.get('category') == 'service-management']
        assert sm_learnings == [], (
            f"Slash-command body vendor mentions must be stripped, got {len(sm_learnings)} false positives"
        )

    def test_real_user_content_survives_strip(self):
        """User-authored content AFTER a slash-command body must remain detectable.

        The slash-command body strip uses the next JSON role boundary as the end
        anchor. After the boundary, the actual assistant/user content (which may
        contain real vendor knowledge) is preserved for the detector.
        """
        transcript = (
            '<command-message>obi-auto</command-message>\n'
            '<command-name>/obi-auto</command-name>\n'
            '<command-args></command-args>\n'
            '# Obi Wag body with OAuth PKCE example text...\n'
            '"role":"user","content":"After the command body, the user said:\\n'
            'The OIDC api endpoint should be set to the regional URL.\\n'
            'You must always validate the issuer claim first."'
        )
        # The real user content (OIDC api endpoint should be set to...) is a
        # legitimate authentication learning and should still surface.
        result = detect_vendor_discoveries(transcript)
        auth_learnings = [l for l in result if l.metadata.get('category') == 'authentication']
        assert len(auth_learnings) >= 1, (
            "User content after the command body must still be scanned"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
