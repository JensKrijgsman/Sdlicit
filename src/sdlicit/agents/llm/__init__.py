"""LLM gateway — model-aware DSPy module selection with compiled state."""

from .gateway import LLMGateway, StubGateway, create_gateway

__all__ = ["LLMGateway", "StubGateway", "create_gateway"]
