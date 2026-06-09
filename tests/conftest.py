"""Shared pytest fixtures for gdrives tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_service():
    """Return a MagicMock Drive API service."""
    return MagicMock()
