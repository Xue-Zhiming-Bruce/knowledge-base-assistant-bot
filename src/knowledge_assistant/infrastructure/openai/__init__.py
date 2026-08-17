"""OpenAI model-provider adapters."""

from knowledge_assistant.infrastructure.openai.answers import OpenAIAnswerGenerator
from knowledge_assistant.infrastructure.openai.embeddings import OpenAIEmbeddingProvider

__all__ = ["OpenAIAnswerGenerator", "OpenAIEmbeddingProvider"]
