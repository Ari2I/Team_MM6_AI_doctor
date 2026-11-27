"""Runtime эмбеддингов не импортирует локальные ML-зависимости и не требует весов."""

import builtins


def test_runtime_never_imports_local_model_dependencies(monkeypatch):
    original = builtins.__import__

    def checked(name, *args, **kwargs):
        assert name.split(".")[0] not in {"torch", "transformers", "huggingface_hub"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked)
    from llm_rag.embeddings import GigaChatEmbedder

    assert GigaChatEmbedder().embed_documents([]) == []
