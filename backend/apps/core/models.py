"""No models. This module exists so Django sends post_migrate for apps.core,
which grants table privileges to the restricted runtime role (see signals.py)."""
