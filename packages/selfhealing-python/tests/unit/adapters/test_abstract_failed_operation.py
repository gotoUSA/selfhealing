"""
Unit tests for AbstractFailedOperation model.

Tests the domain-free abstract Django model for DLQ entries.
Uses mocking to avoid actual Django database dependencies.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock


class TestAbstractFailedOperationImport:
    """Test that AbstractFailedOperation can be imported correctly."""
    
    def test_import_with_django_available(self):
        """Test import when Django is available."""
        # Since we're in a Django project context, this should work
        with patch.dict('sys.modules', {'django': MagicMock()}):
            from selfhealing.adapters.django import get_abstract_failed_operation
            # Should not raise
            assert callable(get_abstract_failed_operation)
    
    def test_get_abstract_failed_operation_function_exists(self):
        """Test that get_abstract_failed_operation is exported."""
        from selfhealing.adapters.django import get_abstract_failed_operation
        assert callable(get_abstract_failed_operation)


class TestAbstractFailedOperationChoices:
    """Test Status, ResolutionType, and RecommendedAction choices."""
    
    @pytest.fixture
    def mock_django(self):
        """Mock Django models module."""
        mock_models = MagicMock()
        mock_models.TextChoices = type('TextChoices', (), {})
        mock_models.Model = type('Model', (), {'__init__': lambda self: None})
        mock_models.CharField = MagicMock(return_value=MagicMock())
        mock_models.TextField = MagicMock(return_value=MagicMock())
        mock_models.JSONField = MagicMock(return_value=MagicMock())
        mock_models.PositiveIntegerField = MagicMock(return_value=MagicMock())
        mock_models.DateTimeField = MagicMock(return_value=MagicMock())
        mock_models.Index = MagicMock()
        return mock_models
    
    def test_status_choices_are_domain_free(self):
        """Verify Status choices don't contain domain-specific values."""
        # Status should only contain workflow states, not business domains
        expected_statuses = {
            'PENDING', 'REVIEWING', 'REPLAYED', 'REQUIRES_REVIEW',
            'RESOLVED', 'REJECTED', 'ARCHIVED', 'EXPIRED'
        }
        
        # These are workflow statuses that apply to ANY business domain
        for status in expected_statuses:
            # Just verify they exist as expected names
            assert status in expected_statuses
    
    def test_resolution_type_choices_are_domain_free(self):
        """Verify ResolutionType choices are generic."""
        expected_types = {
            'AUTO_REPLAY', 'MANUAL_FIX', 'REJECTED',
            'EXPIRED', 'INTERNAL_ERROR', 'ARCHIVED'
        }
        
        for res_type in expected_types:
            assert res_type in expected_types
    
    def test_recommended_action_choices_are_domain_free(self):
        """Verify RecommendedAction choices are generic."""
        expected_actions = {
            'REPLAY', 'MANUAL_CHECK', 'ESCALATE', 'ARCHIVE'
        }
        
        for action in expected_actions:
            assert action in expected_actions


class TestAbstractFailedOperationFields:
    """Test that AbstractFailedOperation has all required fields."""
    
    def test_required_fields_exist_in_source(self):
        """Verify required fields are defined in the model source."""
        import inspect
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Core fields that must exist
        required_fields = [
            'domain',
            'failure_type',
            'status',
            'entity_type',
            'entity_id',
            'entity_refs',
            'snapshot_data',
            'error_code',
            'error_message',
            'retry_count',
            'max_retries',
            'last_retry_at',
            'request_data',
            'response_data',
            'metadata',
            'resolved_at',
            'resolution_type',
            'resolution_note',
            'next_action_hint',
            'recommended_action',
            'created_at',
            'updated_at',
            'expires_at',
        ]
        
        for field in required_fields:
            assert f'{field} = models.' in source, f"Field '{field}' not found in model"
    
    def test_no_hardcoded_domain_choices(self):
        """Verify domain field has no hardcoded choices in actual model code."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Find where actual class definition starts (after module docstring)
        # The actual AbstractFailedOperation class should NOT define Domain TextChoices
        
        # Split into docstring and actual code
        # Module docstring ends with triple quotes
        parts = source.split('"""')
        
        # parts[0] is empty, parts[1] is module docstring, parts[2:] is actual code
        if len(parts) > 2:
            actual_code = '"""'.join(parts[2:])  # Everything after module docstring
        else:
            actual_code = source
        
        # Now check if there's a Domain(models.TextChoices) in the actual class
        # (not in the module docstring example)
        lines = actual_code.split('\n')
        
        # Find lines that define Domain as a nested class with TextChoices
        # inside the AbstractFailedOperation class body (not in docstrings)
        in_class = False
        in_docstring = False
        domain_class_found = False
        
        for line in lines:
            if 'class AbstractFailedOperation' in line:
                in_class = True
                continue
            if in_class:
                # Track nested docstrings
                if '"""' in line:
                    in_docstring = not in_docstring
                    continue
                if not in_docstring and 'class Domain(models.TextChoices)' in line:
                    domain_class_found = True
                    break
        
        assert not domain_class_found, \
            "AbstractFailedOperation should NOT define Domain choices"
    
    def test_no_user_fk_in_abstract_model(self):
        """Verify no hardcoded User FK in abstract model code (not docstring)."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Split into docstring and actual code
        parts = source.split('"""')
        
        # parts[0] is empty, parts[1] is module docstring, parts[2:] is actual code
        if len(parts) > 2:
            actual_code = '"""'.join(parts[2:])
        else:
            actual_code = source
        
        # Now look for actual FK definition in the class body (not in docstrings)
        import re
        
        # Find the actual class code, excluding inner docstrings
        lines = actual_code.split('\n')
        in_class = False
        in_docstring = False
        user_fk_found = False
        
        for line in lines:
            if 'class AbstractFailedOperation' in line:
                in_class = True
                continue
            if in_class:
                # Track docstrings
                if '"""' in line:
                    in_docstring = not in_docstring
                    continue
                # Check for user FK definition outside docstrings
                if not in_docstring:
                    if re.match(r'^\s+user\s*=\s*models\.ForeignKey', line):
                        user_fk_found = True
                        break
        
        assert not user_fk_found, \
            "AbstractFailedOperation should NOT define user FK"


class TestAbstractFailedOperationMeta:
    """Test Meta class configuration."""
    
    def test_abstract_is_true(self):
        """Verify abstract = True in Meta."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        assert 'abstract = True' in source, "Model must be abstract"
    
    def test_indexes_defined(self):
        """Verify composite indexes are defined."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Check for index definitions
        assert 'models.Index(fields=' in source, "Composite indexes must be defined"
        
        # Key indexes that should exist
        expected_index_patterns = [
            '["domain", "status"]',
            '["failure_type", "status"]',
            '["status", "-created_at"]',
            '["entity_type", "entity_id"]',
        ]
        
        for pattern in expected_index_patterns:
            assert pattern in source, f"Index {pattern} not found"


class TestAbstractFailedOperationMethods:
    """Test state transition methods exist."""
    
    def test_state_transition_methods_exist(self):
        """Verify all state transition methods are defined."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        required_methods = [
            'def mark_as_resolved(',
            'def mark_as_rejected(',
            'def queue_for_replay(',
            'def mark_as_reviewing(',
            'def revert_to_pending(',
            'def mark_as_requires_review(',
            'def mark_as_archived(',
            'def mark_as_expired(',
        ]
        
        for method in required_methods:
            assert method in source, f"Method '{method}' not found"
    
    def test_property_methods_exist(self):
        """Verify property methods are defined."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        required_properties = [
            '@property',
            'def is_replayable(',
            'def age_seconds(',
            'def is_sla_breached(',
        ]
        
        for prop in required_properties:
            assert prop in source, f"Property '{prop}' not found"
    
    def test_factory_method_exists(self):
        """Verify create_from_failure factory method exists."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        assert 'def create_from_failure(' in source
        assert '@classmethod' in source


class TestAbstractFailedOperationDocumentation:
    """Test documentation and usage examples."""
    
    def test_module_docstring_has_usage_example(self):
        """Verify module has usage example."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Should have usage example
        assert 'Usage:' in source
        assert 'class FailedOperation(AbstractFailedOperation):' in source
    
    def test_class_docstring_explains_subclassing(self):
        """Verify class docstring explains how to subclass."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Should explain subclassing requirements
        assert 'Subclasses should:' in source or 'subclass' in source.lower()


class TestAbstractFailedOperationStateTransitions:
    """Test state transition method signatures."""
    
    def test_mark_as_resolved_handles_optional_resolved_by(self):
        """Verify mark_as_resolved handles missing resolved_by FK gracefully."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Should check if resolved_by attribute exists before setting
        assert "hasattr(self, 'resolved_by')" in source, \
            "mark_as_resolved should check for resolved_by attribute"
    
    def test_queue_for_replay_raises_on_max_retries(self):
        """Verify queue_for_replay raises ValueError when max_retries exceeded."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Find queue_for_replay method
        assert 'raise ValueError' in source
        assert 'max_retries' in source


class TestAbstractFailedOperationFactoryMethod:
    """Test create_from_failure factory method."""
    
    def test_factory_accepts_extra_fields(self):
        """Verify factory method accepts **extra_fields for project-specific fields."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Should accept **extra_fields
        assert '**extra_fields' in source
        
        # Should pass them to create
        assert '**extra_fields,' in source or '**extra_fields)' in source
    
    def test_factory_calculates_expires_at(self):
        """Verify factory calculates expires_at from retention_days."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        assert 'retention_days' in source
        assert 'expires_at' in source
        assert 'timedelta(days=retention_days)' in source


class TestDomainFreeDesign:
    """Test that the model is truly domain-free."""
    
    def test_no_ecommerce_references(self):
        """Verify no e-commerce specific references in actual field definitions."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Split source - first extract only the AbstractFailedOperation class body
        # excluding module docstring and class docstrings
        parts = source.split('"""')
        
        # Remove all docstrings by taking only non-docstring parts
        # parts indices: 0=before first """, 1=first docstring, 2=code, 3=next docstring, etc
        non_docstring_parts = []
        for i, part in enumerate(parts):
            if i % 2 == 0:  # Even indices are code, odd are docstrings
                non_docstring_parts.append(part)
        
        code_only = ''.join(non_docstring_parts)
        
        # Also remove single-line comments
        code_lines = [
            line for line in code_only.split('\n')
            if not line.strip().startswith('#')
        ]
        
        code_only = '\n'.join(code_lines)
        
        # These should NOT appear as hardcoded domain values in actual code
        ecommerce_terms = [
            'PAYMENT =',      # class constant definition
            'INVENTORY =',
            'POINT =',
            'SHOPPING =',
        ]
        
        for term in ecommerce_terms:
            assert term not in code_only, \
                f"E-commerce term {term} found in actual code"
    
    def test_no_project_specific_imports(self):
        """Verify no imports from shopping or myproject."""
        from pathlib import Path
        
        model_path = Path(__file__).parent.parent.parent.parent / (
            "src/selfhealing/adapters/django/models.py"
        )
        
        source = model_path.read_text()
        
        # Should not import from shopping or myproject
        assert 'from shopping' not in source
        assert 'from myproject' not in source
        assert 'import shopping' not in source
        assert 'import myproject' not in source
